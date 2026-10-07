#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SafeTrade 下单链路自检（smoke test）：查余额 -> 挂一张不可能成交的限价单 -> 撤单
-> 挂一张可成交的小额限价单（真实成交）-> 对账余额与成交记录。

默认 dry-run，只打印计划；加 --live 才真下单。测试金额取市场最小下单量，
成本约几毛钱 USDT。用法：
    python3 smoke_test.py --live --market qubicusdt --amount 1000000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal

from safetrade_client import SafeTrade, SafeTradeError, load_env


def bal(client: SafeTrade, cur: str) -> Decimal:
    b = client.balance_of(cur)
    return Decimal(str(b.get("balance") or 0)), Decimal(str(b.get("locked") or 0))


def show_bal(client: SafeTrade, curs) -> dict:
    out = {}
    for c in curs:
        b, l = bal(client, c)
        out[c] = (b, l)
        print(f"   {c:<6} balance={b}  locked={l}")
    return out


def main(argv=None) -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--market", default="qubicusdt")
    ap.add_argument("--amount", type=float, default=1_000_000)
    args = ap.parse_args(argv)

    mk = args.market.lower()
    base, quote = mk[:-4], mk[-4:] if mk.endswith("usdt") else (mk.split("usdt")[0], "usdt")
    base = mk.replace("usdt", "")
    client = SafeTrade(timeout=20)

    print(f"### 1) 账号 & 测试参数")
    me = client.me()
    m = client.market(mk)
    print(f"   uid={me['uid']} group={me.get('group')}  market={m['name']} "
          f"min_amount={m['min_amount']} amount_prec={m['amount_precision']} price_prec={m['price_precision']}")
    bid, ask, _ = client.best_bid_ask(mk)
    print(f"   best_bid={bid} best_ask={ask}")
    tick = Decimal(1).scaleb(-int(m["price_precision"]))
    print(f"### 2) 测试前余额（{base}/{quote}）")
    before = show_bal(client, [base, quote])

    amount = Decimal(str(args.amount))

    # ---- A. 不可能成交的单（挂在 best_ask 上方 20%），验证 挂单->查询->撤单 ----
    far = (ask * Decimal("1.2")).quantize(tick)
    print(f"### 3) A 段：挂单 {amount} {base.upper()} @ {far}（远离盘口，不会成交）")
    if not args.live:
        print("   [DRY-RUN] 跳过")
    else:
        o = client.create_order(mk, "sell", float(amount), float(far))
        oid = o.get("id")
        print(f"   下单返回 id={oid} state={o.get('state')} price={o.get('price')} amount={o.get('origin_amount')}")
        time.sleep(3)
        cur = client.order(oid)
        print(f"   查询订单 state={cur.get('state')} filled={cur.get('filled_amount')}")
        client.cancel_order(oid)
        time.sleep(3)
        cur = client.order(oid)
        print(f"   撤单后   state={cur.get('state')} filled={cur.get('filled_amount')}")

    # ---- B. 真实小额成交（挂在 best_bid，立即吃单成交） ----
    print(f"### 4) B 段：真实成交测试 卖出 {amount} {base.upper()} @ {bid}（吃单，taker 0.1%）")
    if not args.live:
        print("   [DRY-RUN] 跳过")
    else:
        o = client.create_order(mk, "sell", float(amount), float(bid))
        oid = o.get("id")
        print(f"   下单返回 id={oid} state={o.get('state')} avg_price={o.get('avg_price')}")
        for _ in range(12):
            time.sleep(5)
            cur = client.order(oid)
            print(f"   轮询 state={cur.get('state')} filled={cur.get('filled_amount')} avg={cur.get('avg_price')}")
            if cur.get("state") in ("done", "cancel", "rejected"):
                break
        if cur.get("state") == "wait":
            client.cancel_order(oid)
            print("   超时未全成，已撤单")

    print(f"### 5) 测试后余额")
    after = show_bal(client, [base, quote])
    if args.live:
        for c in (base, quote):
            d = after[c][0] - before[c][0]
            print(f"   Δ{c:<6} {d:+}")

    print("### 6) 近期成交记录（该市场，前 5 条）")
    for t in (client.trades(mk, limit=5) or [])[:5]:
        print("  ", json.dumps(t, ensure_ascii=False))

    print("### 7) 当前未成交挂单")
    for o in client.orders(mk, state=["wait", "pending"]) or []:
        print(f"   id={o['id']} {o['side']} {o['origin_amount']} @ {o['price']} state={o['state']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SafeTradeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
