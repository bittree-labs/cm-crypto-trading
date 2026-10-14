#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客户资产台账：充值入账（按小数位识别 VIP）+ 挂单登记 + 持仓汇总。

入账规则（用户 2026-10-14 定）
-----------------------------
**充值金额的小数位数字 = 客户 VIP 编号**：880.7 → vip7、730.5 → vip5、1400.6 → vip6。
（历史上还有按整数量对应客户的情况，见 `sync-deposits` 的二次匹配。）

数据文件（追加式，不覆盖）
--------------------------
- `ledgers/deposits.jsonl`：充值记录（含 client / txid / deposit_id）
- `ledgers/orders.jsonl`  ：挂单登记（含 client / order_id / qty / price）
- `ledgers/<client>.jsonl`：成交（卖出）记录，由 ledger.py 维护

用法
----
    python3 assets.py sync-deposits          # 拉全部充值，按规则入账（幂等，按 txid 去重）
    python3 assets.py order --client vip6 --order-id 705343479 --qty 1373.3117 --price 1.9
    python3 assets.py positions              # 每客户：充值 / 已卖 / 挂单中 / 剩余持仓 / 已到手USDT
    python3 assets.py positions --check      # 再与交易所实时余额对账
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from safetrade_client import SafeTrade, SafeTradeError, load_env

HERE = os.path.dirname(os.path.abspath(__file__))
L = os.path.join(HERE, "ledgers")
DEPOSITS = os.path.join(L, "deposits.jsonl")
ORDERS = os.path.join(L, "orders.jsonl")
CLIENTS = os.path.join(L, "clients.json")
CST = timezone(timedelta(hours=8))
OPEN_STATES = ("wait", "pending")
CLIENT_MIN_AMOUNT = Decimal("100")   # 低于此额度视为粉尘/自营挖矿入金，不进客户账
MATCH_TOL = Decimal("0.03")          # 数量匹配容差（3%）


def _q(x, nd=8) -> Decimal:
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)


def _f(x, nd=2) -> str:
    return f"{Decimal(str(x)):,.{nd}f}"


def load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: str, rec: dict) -> None:
    os.makedirs(L, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def clients() -> dict:
    try:
        with open(CLIENTS, "r", encoding="utf-8") as fh:
            return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    except Exception:
        return {}


def display(client: str) -> str:
    return str(clients().get(client, client.upper()))


def baseline() -> str:
    """客户资产起算日（clients.json 的 _baseline）。"""
    try:
        with open(CLIENTS, "r", encoding="utf-8") as fh:
            return str(json.load(fh).get("_baseline") or "2000-01-01")
    except Exception:
        return "2000-01-01"


def vip_from_amount(amount) -> str | None:
    """小数位=VIP 编号：880.7 → vip7；1400.60 → 不识别（尾零无意义）。"""
    s = f"{Decimal(str(amount)):f}"
    if "." not in s:
        return None
    frac = s.split(".")[1].rstrip("0")
    if len(frac) == 1 and frac.isdigit():
        return f"vip{frac}"
    return None


def client_sales() -> dict[str, list[dict]]:
    out = {}
    for c in clients():
        out[c] = load_jsonl(os.path.join(L, f"{c}.jsonl"))
    return out


# ------------------------------------------------------------------ sync

def cmd_sync_deposits(a) -> int:
    load_env()
    c = SafeTrade(retries=4, backoff=2.0)
    since = a.since or baseline()
    rows, page = [], 1
    while True:
        chunk = c._request("GET", "/trade/account/deposits",
                           params={"limit": 100, "page": page}, auth=True) or []
        rows += chunk
        if len(chunk) < 100:
            break
        page += 1
    print(f"交易所充值记录共 {len(rows)} 条（含所有币种）")
    have = {d.get("txid") for d in load_jsonl(DEPOSITS)}
    sales = client_sales()
    added = 0
    for d in sorted(rows, key=lambda x: x.get("created_at") or ""):
        amt = Decimal(str(d.get("amount") or 0))
        if amt < CLIENT_MIN_AMOUNT:      # 粉尘 / 自营挖矿小额入金，不入客户账
            continue
        if str(d.get("created_at") or "")[:10] < since:   # 本轮代出业务起点之前的入金，不算客户账
            continue
        client = vip_from_amount(amt)
        src = "decimal"
        if client is None:
            # 二次匹配：整数量与某客户某笔卖出数量接近（容差 3%），且时间相近（±5 天）
            best = None
            dt = d.get("created_at") or ""
            try:
                t0 = datetime.strptime(dt[:10], "%Y-%m-%d")
            except Exception:
                t0 = None
            for cid, recs in sales.items():
                for r in recs:
                    rq = Decimal(r["qty"])
                    if t0 is None or rq == 0 or abs(rq - amt) / rq > MATCH_TOL:
                        continue
                    try:
                        t1 = datetime.strptime(r["date"][:10], "%Y-%m-%d")
                    except Exception:
                        continue
                    gap = abs((t1 - t0).days)
                    if gap <= 5 and (best is None or gap < best[0]):
                        best = (gap, cid, r["date"])
            if best:
                client, src = best[1], f"match_sale({best[2]})"
        if d.get("txid") in have or client is None:
            continue     # 无法归属客户（自营/公司入金）→ 不记入客户台账
        append_jsonl(DEPOSITS, {
            "client": client, "amount": f"{amt:f}", "deposit_id": d.get("id"),
            "txid": d.get("txid"), "created_at": d.get("created_at"),
            "completed_at": d.get("completed_at"), "fee": d.get("fee"),
            "source": src,
        })
        have.add(d.get("txid"))
        added += 1
        print(f"  + {amt:>12} PRL → {display(client):<8} ({src})  {str(d.get('txid'))[:12]}…")
    print(f"新增入账 {added} 条 → {DEPOSITS}")
    return 0


# ------------------------------------------------------------------ order

def cmd_order(a) -> int:
    rec = {"client": a.client, "order_id": str(a.order_id), "side": a.side,
           "qty": f"{_q(a.qty):f}", "price": f"{_q(a.price, 4):f}",
           "state": a.state, "filled": f"{_q(a.filled):f}",
           "ts": datetime.now(CST).strftime("%Y-%m-%d %H:%M"), "note": a.note or ""}
    # 同一 order_id 视为同一条记录：覆盖更新（幂等），否则追加
    recs = load_jsonl(ORDERS)
    for i, r in enumerate(recs):
        if str(r.get("order_id")) == rec["order_id"]:
            rec["ts"] = r.get("ts") or rec["ts"]
            recs[i] = rec
            with open(ORDERS, "w", encoding="utf-8") as fh:
                for x in recs:
                    fh.write(json.dumps(x, ensure_ascii=False) + "\n")
            print(f"已更新挂单：{display(a.client)} 卖 {rec['qty']} PRL @ {rec['price']} "
                  f"（id={rec['order_id']}, 已成交 {rec['filled']}, state={rec['state']}）")
            return 0
    append_jsonl(ORDERS, rec)
    print(f"已登记挂单：{display(a.client)} 卖 {rec['qty']} PRL @ {rec['price']} "
          f"（id={rec['order_id']}, state={rec['state']}）")
    return 0


def refresh_orders_from_api(c: SafeTrade) -> None:
    """用交易所实时状态刷新 orders.jsonl 的 state/filled，并提示需要登记为卖出的新成交。"""
    recs = load_jsonl(ORDERS)
    if not recs:
        return
    changed = False
    print("\n--- 刷新挂单状态（交易所实时）---")
    for r in recs:
        try:
            o = c.order(int(r["order_id"]))
        except SafeTradeError as e:
            print(f"  id={r['order_id']} 查询失败: {e}")
            continue
        filled = Decimal(str(o.get("filled_amount") or 0))
        prev = Decimal(str(r.get("filled") or 0))
        newly = filled - prev
        r["state"] = o.get("state") or r.get("state")
        r["qty"] = str(o.get("origin_amount"))
        r["price"] = str(o.get("price"))
        r["filled"] = str(filled)
        changed = True
        tag = display(r["client"])
        note = ""
        if newly > 0:
            note = f"  ⚠️ 新成交 {newly} PRL（需登记为卖出，均价 {o.get('avg_price')}）"
        print(f"  id={r['order_id']} {tag:<8} {r['qty']} @ {r['price']} "
              f"已成交={filled} state={r['state']}{note}")
    if changed:
        with open(ORDERS, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ positions

def cmd_positions(a) -> int:
    base = baseline()
    c = None
    if a.check:
        load_env()
        c = SafeTrade(retries=4, backoff=2.0)
        refresh_orders_from_api(c)
    deps = [d for d in load_jsonl(DEPOSITS) if str(d.get("created_at") or "")[:10] >= base]
    orders = [o for o in load_jsonl(ORDERS) if str(o.get("ts") or "")[:10] >= base]
    all_sales = client_sales()
    sales = {c: [r for r in (all_sales.get(c) or []) if str(r.get("date") or "")[:10] >= base]
             for c in all_sales}
    ids = sorted(set(list(sales.keys()) + [d["client"] for d in deps]
                     + [o["client"] for o in orders]))
    print("客户资产台账（代出 PRL）")
    print(f"生成时间：{datetime.now(CST).strftime('%Y-%m-%d %H:%M')} CST　|　"
          f"起算日：{base}（此前的入金与成交属历史，不计入持仓）")
    print()
    hdr = f"{'客户':<10}{'累计充值':>14}{'已卖出':>14}{'挂单中':>14}{'剩余持仓':>14}{'已到手USDT':>14}"
    print(hdr)
    print("-" * len(hdr))
    tot_dep = tot_sold = tot_rest = tot_hld = tot_usdt = Decimal("0")
    for cid in ids:
        dep = sum((Decimal(d["amount"]) for d in deps if d["client"] == cid), Decimal("0"))
        sold = sum((Decimal(r["qty"]) for r in sales.get(cid, [])), Decimal("0"))
        usdt = sum((Decimal(r["net"]) for r in sales.get(cid, [])), Decimal("0"))
        resting = Decimal("0")
        for o in orders:
            if o["client"] == cid and o["state"] in OPEN_STATES:
                resting += Decimal(o["qty"]) - Decimal(o.get("filled") or 0)
        hld = dep - sold - resting
        tot_dep += dep; tot_sold += sold; tot_rest += resting; tot_hld += hld; tot_usdt += usdt
        print(f"{display(cid):<10}{_f(dep, 4):>14}{_f(sold, 4):>14}{_f(resting, 4):>14}"
              f"{_f(hld, 4):>14}{_f(usdt, 2):>14}")
    print("-" * len(hdr))
    print(f"{'合计':<10}{_f(tot_dep, 4):>14}{_f(tot_sold, 4):>14}{_f(tot_rest, 4):>14}"
          f"{_f(tot_hld, 4):>14}{_f(tot_usdt, 2):>14}")
    print("\n注：剩余持仓 = 累计充值 − 已卖出 − 挂单中；充值按「小数位=VIP编号」入账。")
    print("\n历史全量成交（含起算日之前的，仅供汇报口径）：")
    for cid in ids:
        recs = all_sales.get(cid) or []
        hq = sum((Decimal(r["qty"]) for r in recs), Decimal("0"))
        hu = sum((Decimal(r["net"]) for r in recs), Decimal("0"))
        hg = sum((Decimal(r["gross"]) for r in recs), Decimal("0"))
        if hq:
            print(f"  {display(cid):<10} {_f(hq, 4):>14} PRL，到手 {_f(hu, 2):>12} USDT"
                  f"（均价 {hu / hq:.4f}）")

    if a.check:
        if c is None:
            load_env()
            c = SafeTrade(retries=4, backoff=2.0)
        b = c.balance_of("prl")
        free, locked = Decimal(b["available"]), Decimal(b["locked"])
        live = free + locked
        print("\n--- 与交易所实时对账 ---")
        print(f"  账户 PRL 总量 = {_f(live, 4)}（可用 {_f(free,4)} + 冻结 {_f(locked,4)}）")
        print(f"  客户持仓+挂单 = {_f(tot_hld + tot_rest, 4)}")
        print(f"  账户自有/未分配 = {_f(live - (tot_hld + tot_rest), 4)}")
        opens = c.orders("prlusdt", state=list(OPEN_STATES)) or []
        print(f"  交易所未成交卖单 {len(opens)} 张：")
        for o in opens:
            tag = next((x["client"] for x in orders if x["order_id"] == str(o["id"])), "?")
            print(f"    id={o['id']} {o['origin_amount']} @ {o['price']} → {display(tag)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="客户资产台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sd = sub.add_parser("sync-deposits")
    sd.add_argument("--since", default=None,
                    help="只把该日期之后的充值归入客户账（缺省=clients.json 的 _baseline 起算日）")
    od = sub.add_parser("order")
    od.add_argument("--client", required=True)
    od.add_argument("--order-id", dest="order_id", required=True)
    od.add_argument("--qty", type=float, required=True)
    od.add_argument("--price", type=float, required=True)
    od.add_argument("--side", default="sell")
    od.add_argument("--state", default="wait")
    od.add_argument("--filled", type=float, default=0.0)
    od.add_argument("--note", default="")
    ps = sub.add_parser("positions")
    ps.add_argument("--check", action="store_true", help="与交易所实时余额对账")
    args = ap.parse_args(argv)
    if args.cmd == "sync-deposits":
        return cmd_sync_deposits(args)
    if args.cmd == "order":
        return cmd_order(args)
    return cmd_positions(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SafeTradeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
