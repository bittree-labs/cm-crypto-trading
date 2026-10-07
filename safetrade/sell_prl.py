#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PRL -> USDT 自动化卖币（SafeTrade，PRL/USDT 现货）

场景：矿场每天固定时间把账户里的 PRL 卖掉换成 USDT。核心不是"能下单"，
而是"别把盘口砸穿" —— PRL/USDT 盘口不深（1 档常常只有几千枚），
一把市价单会把价格打下去几个点，等于自己给自己砸盘。

因此本脚本默认行为：
  1. 读实时盘口（depth），按片（slice）计算"这一片能不能在 >= floor 价成交"；
  2. 每片挂一个限价卖单，价格取"该片量在买盘里能吃完的最差价"，
     能立刻吃单成交，但被 floor 兜底：低于 floor 就不挂（宁可不卖）；
  3. 单片挂单超时未成交 -> 撤单，剩余量并入下一片；
  4. 全程写日志 + JSONL 成交记录，便于对账（PRL 数量 / 到手 USDT / 均价）。

安全默认：
  * 默认 --dry-run，只打印计划，不下单；
  * 真正下单必须显式加 --live；
  * API Key 必须在网页端限制为"仅现货交易、禁提现"，并绑定出口 IP。

用法：
  # 0) 预览（不需要 API Key，只需 --amount）
  python3 sell_prl.py --dry-run --amount 10000 --slices 5 --floor 1.15

  # 1) 真实执行：卖掉可用 PRL 全部（保留 0），分 6 片，最低价 1.15
  python3 sell_prl.py --live --slices 6 --floor 1.15

  # 2) 每天固定时间（crontab 示例：每天 10:07）
  # 7 10 * * * cd ~/workspace/bittree-labs/cm-crypto-trading/safetrade && \
  #            ./venv/bin/python3 sell_prl.py --live --slices 6 --floor 1.15 >> logs/cron.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import ROUND_DOWN, Decimal
from datetime import datetime, timezone

from safetrade_client import SafeTrade, SafeTradeError, load_env

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
FILL_LOG = os.path.join(HERE, "fills.jsonl")
MARKET = "prlusdt"
PRL_AMOUNT_PRECISION = 4     # 市场参数 amount_precision（PRL 枚数小数位）
PRICE_PRECISION = 2         # market price_precision（USDT 价小数位）
MIN_AMOUNT = Decimal("2")   # market min_amount（PRL）


def log(msg: str) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, f"sell_prl_{datetime.now():%Y%m%d}.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def q_amount(x: Decimal) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-PRL_AMOUNT_PRECISION), rounding=ROUND_DOWN)


def q_price(x: Decimal) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-PRICE_PRECISION), rounding=ROUND_DOWN)


def _levels(client: SafeTrade):
    """买盘档位（可变副本，用于模拟吃单）[[price, amount], ...]。"""
    return [[Decimal(str(p)), Decimal(str(a))]
            for p, a in (client.depth(MARKET, limit=100) or {}).get("bids", [])]


def _walk(levels, size: Decimal):
    """在档位表 levels 里吃完 size 枚 PRL（就地扣减），返回 (最差成交价, 预估到手, 已吃量)。"""
    cum = Decimal("0")
    proceeds = Decimal("0")
    worst = None
    for lv in levels:
        if cum >= size:
            break
        p, a = lv
        if a <= 0:
            continue
        take = min(size - cum, a)
        lv[1] = a - take
        cum += take
        proceeds += take * p
        worst = p
    return worst, proceeds, cum


def _floor_allowed(levels, floor: Decimal) -> Decimal:
    """买盘里价格 >= floor 的可吃总量。"""
    return sum(a for p, a in levels if p >= floor)


def plan_slices(client: SafeTrade, total: Decimal, slices: int, floor: Decimal | None):
    """按当前买盘快照模拟逐片吃单，返回计划。真实执行时会用最新盘口重算价格。"""
    levels = _levels(client)
    size = q_amount(total / slices)
    plan = []
    left = total
    for i in range(slices):
        amt = size if i < slices - 1 else q_amount(left)
        left -= amt
        if amt < MIN_AMOUNT:
            continue
        trial = [[p, a] for p, a in levels]
        price, est, _ = _walk(trial, amt)
        note = ""
        if price is None:
            plan.append({"amount": amt, "price": None, "est_usdt": Decimal("0"),
                         "note": "买盘不足，跳过"})
            continue
        if floor is not None and price < floor:
            amt2 = q_amount(min(amt, _floor_allowed(levels, floor)))
            if amt2 < MIN_AMOUNT:
                plan.append({"amount": amt, "price": None, "est_usdt": Decimal("0"),
                             "note": f"低于 floor {floor}，不卖"})
                continue
            amt, note = amt2, f"缩量至 {amt2}（floor {floor} 保护）"
            trial = [[p, a] for p, a in levels]
            price, est, _ = _walk(trial, amt)
        levels = trial
        plan.append({"amount": amt, "price": q_price(price), "est_usdt": est, "note": note})
    return plan


def sell_slice(client: SafeTrade, amount: Decimal, price: Decimal | None,
               timeout_s: int, live: bool) -> dict:
    """下一片：限价卖（price 给定）或市价卖（price=None）。返回成交/剩余情况。"""
    if not live:
        return {"planned_amount": str(amount), "planned_price": None if price is None else str(price),
                "filled": "0", "state": "DRY-RUN"}
    order = client.create_order(MARKET, "sell", float(amount), None if price is None else float(price))
    oid = order.get("id")
    log(f"  挂单 id={oid} {amount} PRL @ {price}")
    deadline = time.time() + timeout_s
    filled = Decimal("0")
    state = None
    while time.time() < deadline:
        time.sleep(5)
        cur = client.order(oid)
        filled = Decimal(str(cur.get("filled_amount") or 0))
        state = cur.get("state")
        if state in ("done", "cancel", "rejected"):
            break
    if state not in ("done", "cancel", "rejected"):
        try:
            client.cancel_order(oid)
            state = "timeout-cancelled"
        except SafeTradeError as e:
            log(f"  撤单失败: {e}")
    return {"order_id": oid, "planned_amount": str(amount),
            "planned_price": None if price is None else str(price),
            "filled": str(filled), "state": state}


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser(description="PRL -> USDT 自动卖币（SafeTrade）")
    ap.add_argument("--live", action="store_true", help="真实下单（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只预览不下单（默认行为；显式写上便于脚本化，且优先于 --live）")
    ap.add_argument("--amount", type=float, default=None, help="要卖的 PRL 数量；缺省=可用余额按策略算")
    ap.add_argument("--reserve", type=float, default=0.0, help="底仓：可用量里保留不卖的 PRL 数量")
    ap.add_argument("--pct", type=float, default=None, help="按可卖量的百分比卖（如 20=卖两成）")
    ap.add_argument("--max-per-run", type=float, default=None, help="单次最大卖出量（防一次卖完）")
    ap.add_argument("--slices", type=int, default=5, help="分片数（降低冲击成本）")
    ap.add_argument("--floor", type=float, default=None, help="最低可接受价（USDT/PRL），低于不卖")
    ap.add_argument("--interval", type=int, default=15, help="片间隔秒数")
    ap.add_argument("--timeout", type=int, default=180, help="单片挂单等待成交秒数，超时撤单")
    ap.add_argument("--mode", choices=["limit", "market"], default="limit",
                    help="limit=按盘口限价吃单（默认）; market=市价（无价格保护，慎用）")
    ap.add_argument("--max-slippage", type=float, default=0.05,
                    help="市价模式下滑点上限（相对 best_bid），超过则中止")
    args = ap.parse_args(argv)
    if args.dry_run:
        args.live = False

    client = SafeTrade(timeout=20)
    t = client.ticker(MARKET)
    bid, ask, _ = client.best_bid_ask(MARKET, depth_ok=5)
    log(f"PRL/USDT last={t.get('last')} best_bid={bid} best_ask={ask} 24h量={t.get('amount')} PRL")

    if args.amount is not None:
        total = q_amount(Decimal(str(args.amount)))
        log(f"本次指定卖出 {total} PRL")
    else:
        if not client.key:
            log("[error] 未指定 --amount 且无 API Key，读不到余额。"
                "示例: --dry-run --amount 20  或  配好 Key 后 --pct 20 --max-per-run 20")
            return 2
        bal = client.balance_of("prl")
        free = Decimal(str(bal.get("available") or 0))
        locked = Decimal(str(bal.get("locked") or 0))
        sellable = max(free - Decimal(str(args.reserve)), Decimal("0"))
        if args.pct is not None:
            sellable = sellable * Decimal(str(args.pct)) / Decimal("100")
        if args.max_per_run is not None:
            sellable = min(sellable, Decimal(str(args.max_per_run)))
        total = q_amount(sellable)
        log(f"PRL 可用={free}  挂单冻结={locked}  底仓={args.reserve}  "
            f"单次上限={args.max_per_run if args.max_per_run is not None else '无'}  "
            f"百分比={args.pct if args.pct is not None else '无'}%  →  本次卖出 {total} PRL")

    if total < MIN_AMOUNT:
        log("可卖数量低于市场最小下单量，退出")
        return 0

    floor = None if args.floor is None else Decimal(str(args.floor))
    plan = plan_slices(client, total, args.slices, floor)
    est_sum = sum(p["est_usdt"] for p in plan if p["price"] is not None)
    est_amt = sum(p["amount"] for p in plan if p["price"] is not None)
    log(f"计划：{len(plan)} 片，合计 {est_amt} PRL，预估到手 {est_sum:.2f} USDT"
        + (f"，均 {est_sum / est_amt:.4f}" if est_amt else ""))
    for i, p in enumerate(plan, 1):
        log(f"  #{i} {p['amount']} PRL @ {p['price']} -> ~{p['est_usdt']:.2f} USDT {p['note']}")

    if not args.live:
        log("[DRY-RUN] 未下单。确认无误后加 --live 执行。")
        return 0

    if args.mode == "market":
        if bid is None:
            log("[error] 无买盘，中止")
            return 3
        for i, p in enumerate(plan, 1):
            wp, est, _ = _walk(_levels(client), p["amount"])
            if wp is None or (bid - wp) / bid > Decimal(str(args.max_slippage)):
                log(f"  #{i} 市价滑点 {(bid - wp) / bid * 100:.2f}% 超过上限，跳过")
                continue
            log(f"  #{i} 市价卖出 {p['amount']} PRL（预估 {est:.2f} USDT）")
            rec = sell_slice(client, p["amount"], None, args.timeout, True)
            with open(FILL_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                     "mode": "market", **rec}, ensure_ascii=False) + "\n")
            time.sleep(args.interval)
        return 0

    for i, p in enumerate(plan, 1):
        amt = p["amount"]
        levels = _levels(client)
        wp, est, _ = _walk([[x, y] for x, y in levels], amt)
        if wp is None:
            log(f"  #{i} 最新盘口无买盘，跳过")
            continue
        if floor is not None and wp < floor:
            allowed = q_amount(min(amt, _floor_allowed(levels, floor)))
            if allowed < MIN_AMOUNT:
                log(f"  #{i} 最新最差价 {wp} 低于 floor {floor}，跳过不卖")
                continue
            log(f"  #{i} 最差价 {wp} < floor {floor}，缩量 {amt} -> {allowed}（floor 保护）")
            amt = allowed
            wp, est, _ = _walk([[x, y] for x, y in levels], amt)
        price = q_price(wp)  # 执行时用最新盘口重算，计划价仅作预览
        log(f"  #{i} 执行：{amt} PRL @ {price}（预估 {est:.2f} USDT）")
        rec = sell_slice(client, amt, price, args.timeout, True)
        with open(FILL_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                 "mode": "limit", "est_usdt": str(est),
                                 **rec}, ensure_ascii=False) + "\n")
        time.sleep(args.interval)

    log("执行结束。核对：python3 safetrade_client.py balance / orders")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SafeTradeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
