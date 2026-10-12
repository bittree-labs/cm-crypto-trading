#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客户代出 PRL 台账：追加一笔 + 生成汇报版报表（每次卖出后更新累计）。

数据文件：`ledgers/<client>.jsonl`（一行一笔，追加式，不覆盖）
报表文件：`ledgers/<client>-report.md`（客户汇报用，Markdown）

用法
----
# 追加一笔（净到账按 0.1% 平台费自动算，除非 --fee/--net 显式给出）
python3 ledger.py add --client vip6 --qty 2600 --price 1.32 --order-id 704042359 \
        --source api --note "限价即成交"

# 带客户口径的净到账（差额含提现费等）
python3 ledger.py add --client vip6 --qty 2600 --price 0.99 --net 2564 --fee 2.574 \
        --order-id 693957195 --source api --date 2026-09-19T01:06

# 生成/刷新客户汇报报表（含每笔后的累计）
python3 ledger.py report --client vip6 --rate 6.7
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER_DIR = os.path.join(HERE, "ledgers")
FEE_RATE = Decimal("0.001")   # SafeTrade 现货手续费 0.1%（maker=taker）
CST = timezone(timedelta(hours=8))


def _q(x, nd=4) -> Decimal:
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)


def _f(x, nd=4) -> str:
    """千分位 + 固定小数位。"""
    v = Decimal(str(x))
    return f"{v:,.{nd}f}"


def path_of(client: str) -> str:
    return os.path.join(LEDGER_DIR, f"{client}.jsonl")


def load(client: str) -> list[dict]:
    p = path_of(client)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append(rec: dict) -> None:
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(path_of(rec["client"]), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ add

def cmd_add(a) -> int:
    gross = _q(Decimal(str(a.qty)) * Decimal(str(a.price)))
    fee = _q(Decimal(str(a.fee))) if a.fee is not None else _q(gross * FEE_RATE)
    net = _q(Decimal(str(a.net))) if a.net is not None else _q(gross - fee)
    rec = {
        "client": a.client,
        "date": a.date or datetime.now(CST).strftime("%Y-%m-%d %H:%M"),
        "qty": str(_q(a.qty)),
        "price": str(_q(a.price)),
        "gross": str(gross),
        "fee": str(fee),
        "net": str(net),
        "order_id": a.order_id or "",
        "source": a.source,
        "note": a.note or "",
    }
    append(rec)
    rows = load(a.client)
    print(f"已追加第 {len(rows)} 笔：{rec['qty']} PRL @ {rec['price']} → 净到账 {rec['net']} USDT")
    print(f"数据文件：{path_of(a.client)}")
    return cmd_report(argparse.Namespace(client=a.client, rate=a.rate, quiet=False))


# ------------------------------------------------------------------ report

FIELDS = ["#", "成交时间(CST)", "数量(PRL)", "单价(USDT)", "名义成交额(USDT)",
          "手续费(USDT)", "净到账(USDT)", "累计数量", "累计净到账", "累计均价", "委托单号"]


def cmd_report(a) -> int:
    rows = load(a.client)
    if not rows:
        print(f"[{a.client}] 台账为空，先用 add 追加记录")
        return 1

    cq = cd = cg = cf = cnet = Decimal("0")
    lines = []
    lines.append(f"# {a.client.upper()} 代出 PRL —— 结算明细与累计")
    lines.append("")
    lines.append(f"> 生成时间：{datetime.now(CST).strftime('%Y-%m-%d %H:%M')} (CST)　|　"
                 f"平台：SafeTrade（safe.trade）PRL/USDT 现货")
    lines.append("> 口径：净到账 = 名义成交额 − 平台交易手续费；手续费按 SafeTrade 现货费率 "
                 "**0.1%**（maker = taker = 0.1%，全站无档差）；标注「客户账单口径」的净额"
                 "已含提现等其他费用，与平台费存在差额。")
    lines.append("")
    lines.append("| " + " | ".join(FIELDS) + " |")
    lines.append("|" + "---|" * len(FIELDS))

    for i, r in enumerate(rows, 1):
        q, p = Decimal(r["qty"]), Decimal(r["price"])
        g, fee, net = Decimal(r["gross"]), Decimal(r["fee"]), Decimal(r["net"])
        cq += q; cg += g; cf += fee; cnet += net
        avg = (cnet / cq) if cq else Decimal("0")
        lines.append("| " + " | ".join([
            str(i), r["date"], _f(q), _f(p, 4), _f(g), _f(fee), f"**{_f(net)}**",
            _f(cq, 2), f"**{_f(cnet)}**", _f(avg, 4), r.get("order_id") or "—",
        ]) + " |")

    avg_all = (cnet / cq) if cq else Decimal("0")
    lines.append("| " + " | ".join([
        "**合计**", "—", f"**{_f(cq,2)}**", "—", f"**{_f(cg)}**", f"**{_f(cf)}**",
        f"**{_f(cnet)}**", f"**{_f(cq,2)}**", f"**{_f(cnet)}**", f"**{_f(avg_all,4)}**", "—",
    ]) + " |")
    lines.append("")

    if a.rate:
        rate = Decimal(str(a.rate))
        lines.append(f"**折算**：累计净到账 {_f(cnet)} USDT × 汇率 {a.rate} ≈ "
                     f"**{_f(cnet*rate, 2)} 元**（汇率可按客户口径调整）")
        lines.append("")

    lines.append("## 逐笔说明")
    lines.append("")
    open_items: list[str] = []
    for i, r in enumerate(rows, 1):
        bits = []
        if r.get("source") == "api":
            bits.append("交易所 API 成交记录核实")
        else:
            bits.append("来源为人工记录，**待核**")
            open_items.append(f"第 {i} 笔（{r['date']}）：账户 API 未匹配到对应委托单，数量/净额待确认")
        if r.get("order_id"):
            bits.append(f"委托单号 {r['order_id']}")
        diff = Decimal(r["gross"]) - Decimal(r["fee"]) - Decimal(r["net"])
        if diff != 0 and not r.get("note"):
            bits.append(f"净到账含提现等费用，与「名义额−平台费」差 {_f(abs(diff))} USDT")
        if r.get("note"):
            bits.append(r["note"])
        lines.append(f"{i}. **{r['date']}**　" + "；".join(bits))
    if open_items:
        lines.append("")
        lines.append("## 待确认事项")
        lines.append("")
        for x in open_items:
            lines.append(f"- {x}")
    lines.append("")
    lines.append("> 说明：数量与净到账以交易所成交记录为准；「手续费」为平台交易费，"
                 "提现费另计（PRL 提现 0.01 PRL/笔）。")

    md = "\n".join(lines) + "\n"
    out = os.path.join(LEDGER_DIR, f"{a.client}-report.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md)
    if not getattr(a, "quiet", False):
        print(md)
        print(f"[报表已写入] {out}")
    return 0


def cmd_brief(a) -> int:
    """按用户口述风格输出简版（纯文本，方便直接复制到微信）。"""
    rows = load(a.client)
    if not rows:
        print(f"[{a.client}] 台账为空，先用 add 追加记录")
        return 1
    cq = cnet = Decimal("0")
    out = [f"{a.client.upper()} 代出 PRL（PRL/USDT 现货）", ""]
    for i, r in enumerate(rows, 1):
        q, p = Decimal(r["qty"]), Decimal(r["price"])
        g, net = Decimal(r["gross"]), Decimal(r["net"])
        cut = g - net                      # 综合扣费（平台费 + 提现等）
        cq += q
        cnet += net
        price_s = f"{p.normalize():f}"
        out.append(f"卖{i}：{q:,.0f} × {price_s} = {g:,.0f}u − {cut:,.2f} = {net:,.2f}u")
    out.append("")
    out.append(f"到手合计 {cnet:,.2f} USDT（PRL {cq:,.0f} 枚，均价 {cnet/cq:.4f}）")
    if a.rate:
        out.append(f"折算 ≈ {cnet*Decimal(str(a.rate)):,.0f} 元（按 {a.rate}）")
    text = "\n".join(out) + "\n"
    path = os.path.join(LEDGER_DIR, f"{a.client}-brief.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"[简版已写入] {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="客户代出 PRL 台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ad = sub.add_parser("add")
    ad.add_argument("--client", required=True)
    ad.add_argument("--qty", required=True, type=float)
    ad.add_argument("--price", required=True, type=float)
    ad.add_argument("--fee", type=float, default=None, help="平台手续费（缺省按 0.1%% 算）")
    ad.add_argument("--net", type=float, default=None, help="客户净到账（缺省=名义−手续费）")
    ad.add_argument("--date", default=None, help="成交时间 CST，如 2026-09-19 01:06")
    ad.add_argument("--order-id", dest="order_id", default="")
    ad.add_argument("--source", choices=["api", "customer"], default="api")
    ad.add_argument("--note", default="")
    ad.add_argument("--rate", type=float, default=6.7, help="USDT→CNY 折算汇率")
    rp = sub.add_parser("report")
    rp.add_argument("--client", required=True)
    rp.add_argument("--rate", type=float, default=6.7)
    bp = sub.add_parser("brief")
    bp.add_argument("--client", required=True)
    bp.add_argument("--rate", type=float, default=6.7)
    args = ap.parse_args(argv)
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "brief":
        return cmd_brief(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
