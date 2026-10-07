#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""低频量化卖币引擎（指标触发，不是定时器触发）。

机制
----
1. 大方向由人定（几天尺度）：`--bias bull|neutral|bear`，或写进 `bias.json`。
2. 指标（日线 + 4H）算出「本轮该卖掉可卖量的百分之几」（ratio）+ 理由清单。
3. 低频约束：
   - `--min-hours` 冷却（默认 6h）：距上次动手不足就不动（真正的低频，一天最多几次）；
   - `--max-per-run` 单次上限（默认 20 枚）：防一次卖光；
   - `--reserve` 底仓不动；
   - `--ratio-floor`：指标算出的比例低于它就不卖（默认 0.05，即 <5% 视为持有）。
4. 执行复用 `sell_prl.py` 的分片限价逻辑（maker 或 taker 可选）。

用法
----
    python3 sell_auto.py --bias bull              # 只出决策（dry-run）
    python3 sell_auto.py --bias bull --live       # 真按决策卖
    python3 sell_auto.py --bias bull --live --mode maker --timeout 120
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

from safetrade_client import SafeTrade, SafeTradeError, load_env
from sell_prl import MARKET, MIN_AMOUNT, log, plan_slices, q_amount, sell_slice
import quant

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "logs", "auto_state.json")
DECISIONS = os.path.join(HERE, "logs", "decisions.jsonl")
BIAS_FILE = os.path.join(HERE, "bias.json")


def read_bias_file() -> str | None:
    if os.path.exists(BIAS_FILE):
        try:
            with open(BIAS_FILE, "r", encoding="utf-8") as fh:
                return (json.load(fh).get("bias") or "").strip() or None
        except Exception:
            return None
    return None


def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            with open(STATE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def save_state(**kw) -> None:
    st = load_state()
    st.update(kw)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser(description="低频量化卖币（指标触发）")
    ap.add_argument("--bias", choices=["bull", "neutral", "bear"], default=None,
                    help="你定的几天级大方向；不给则读 bias.json")
    ap.add_argument("--live", action="store_true", help="真下单（默认只决策）")
    ap.add_argument("--reserve", type=float, default=0.0, help="底仓，不动的 PRL")
    ap.add_argument("--max-per-run", type=float, default=20.0, help="单次卖出上限（枚）")
    ap.add_argument("--ratio-floor", type=float, default=0.05, help="指标比例低于此值视为持有")
    ap.add_argument("--min-hours", type=float, default=6.0, help="冷却：距上次动手的最小间隔小时")
    ap.add_argument("--slices", type=int, default=2, help="分几片挂")
    ap.add_argument("--mode", choices=["limit", "maker"], default="limit")
    ap.add_argument("--timeout", type=int, default=180, help="每片等待成交秒数")
    ap.add_argument("--interval", type=int, default=15, help="片间隔秒")
    ap.add_argument("--floor", type=float, default=None, help="最低可接受价")
    args = ap.parse_args(argv)

    bias = args.bias or read_bias_file() or "neutral"
    client = SafeTrade(timeout=20)

    # ---- 1) 数据 + 指标 ----
    daily = quant.fetch_klines(client, MARKET, 1440, 120)
    h4 = quant.fetch_klines(client, MARKET, 240, 120)
    sig = quant.evaluate(daily, h4, bias)
    if sig.get("error"):
        log(f"[abort] {sig['error']}")
        return 2
    m = sig["metrics"]
    log(f"=== 决策输入 | bias={bias} | 日线{m['bars_daily']}根 ===")
    log(f"  现价={sig['price']}  RSI14={m['rsi14']}  EMA20={m['ema20']}(斜率{m['ema20_slope5_pct']}%/5d)  "
        f"EMA50={m['ema50']}  ATR%={m['atr_pct']}  量比={m['vol_ratio']}")
    log(f"  布林(20,2) 中/上/下={m['boll']}  1日={m['chg_1d_pct']}%  7日={m['chg_7d_pct']}%")
    for why in sig["reasons"]:
        log(f"    · {why}")
    ratio = sig["ratio"]
    log(f"→ 信号：卖出比例 {ratio*100:.1f}%  判定={quant.verdict(ratio)}")

    # ---- 2) 冷却（低频闸） ----
    st = load_state()
    last_ts = st.get("last_action_ts")
    if last_ts and args.live:
        hours = (time.time() - last_ts) / 3600.0
        if hours < args.min_hours:
            log(f"[skip] 距上次动手 {hours:.1f}h < 冷却 {args.min_hours}h，本轮不动")
            return 0

    # ---- 3) 算量 ----
    bal = client.balance_of("prl")
    free = Decimal(str(bal.get("available") or 0))
    locked = Decimal(str(bal.get("locked") or 0))
    sellable = max(free - Decimal(str(args.reserve)), Decimal("0"))
    want = q_amount(min(sellable * Decimal(str(ratio)), Decimal(str(args.max_per_run))))
    log(f"PRL 可用={free} 挂单冻结={locked} 底仓={args.reserve} 可卖={sellable} → 本轮拟卖 {want} PRL")

    if ratio <= args.ratio_floor or want < MIN_AMOUNT:
        log(f"[hold] 信号比例 {ratio*100:.1f}%（阈值 {args.ratio_floor*100:.0f}%）或量不足最小 {MIN_AMOUNT} → 本轮不卖")
        with open(DECISIONS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "bias": bias,
                                 "ratio": ratio, "verdict": quant.verdict(ratio),
                                 "metrics": m, "action": "hold"}, ensure_ascii=False) + "\n")
        return 0

    floor = None if args.floor is None else Decimal(str(args.floor))
    plan = plan_slices(client, want, args.slices, floor)
    for i, p in enumerate(plan, 1):
        log(f"  片#{i} {p['amount']} PRL @ {p['price']} -> ~{p['est_usdt']:.2f} USDT {p['note']}")

    if not args.live:
        log("[DRY-RUN] 未下单。加 --live 执行。")
        with open(DECISIONS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "bias": bias,
                                 "ratio": ratio, "verdict": quant.verdict(ratio),
                                 "metrics": m, "action": "dry-run-would-sell",
                                 "amount": str(want)}, ensure_ascii=False) + "\n")
        return 0

    # ---- 4) 执行 ----
    filled_total = Decimal("0")
    for i, p in enumerate(plan, 1):
        amt = p["amount"]
        price = p["price"]
        if args.mode == "maker":
            from safetrade_client import SafeTrade as _ST  # noqa
            bid, ask, _ = client.best_bid_ask(MARKET, depth_ok=5)
            if ask is None:
                log(f"  #{i} 无卖盘报价，跳过")
                continue
            price = (ask if floor is None else max(ask, floor)).quantize(Decimal("0.01"))
        if price is None:
            log(f"  #{i} 无价，跳过")
            continue
        log(f"  #{i} 执行 {amt} PRL @ {price}（{args.mode}）")
        rec = sell_slice(client, amt, price, args.timeout, True)
        filled_total += Decimal(str(rec.get("filled") or 0))
        with open(DECISIONS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "bias": bias,
                                 "ratio": ratio, "verdict": quant.verdict(ratio),
                                 "metrics": m, "mode": args.mode, **rec}, ensure_ascii=False) + "\n")
        time.sleep(args.interval)

    save_state(last_action_ts=time.time(), last_bias=bias, last_ratio=ratio,
               last_sold=str(filled_total))
    log(f"完成：本轮成交 {filled_total} PRL")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SafeTradeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
