#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT-M 永续合约交易 CLI (Binance fapi, 需 API key)
=================================================
安全默认: 所有下单命令默认 --dry-run (只打印将要发送的参数, 不真的下单), 必须显式加 --live 才发单。

环境变量 / .env:  BINANCE_API_KEY / BINANCE_API_SECRET  (合约权限需在币安后台单独开通)

命令:
  # --- 只读 ---
  python3 perp_trade.py account                      # 资产+持仓+挂单 全景
  python3 perp_trade.py balance                      # 合约钱包余额
  python3 perp_trade.py positions [SYMBOL]           # 持仓(含未实现盈亏/强平价)
  python3 perp_trade.py orders [SYMBOL]              # 当前挂单
  python3 perp_trade.py fees BTCUSDT                 # 手续费率
  python3 perp_trade.py income [--days 7]            # 近期资金流水(含资金费/盈亏)
  python3 perp_trade.py mode                         # 单向/双向持仓 + 全仓/逐仓 + 多资产模式

  # --- 设置(会真的生效, 不必 --live) ---
  python3 perp_trade.py set-leverage BTCUSDT 5
  python3 perp_trade.py set-margin BTCUSDT ISOLATED   # ISOLATED|CROSSED

  # --- 仓位计算(不下单) ---
  python3 perp_trade.py size --side long --risk-pct 1 --stop-pct 2 [--symbol BTCUSDT] [--leverage 5]

  # --- 下单(默认 dry-run) ---
  python3 perp_trade.py market BTCUSDT BUY 0.001          [--live] [--reduce-only]
  python3 perp_trade.py limit  BTCUSDT SELL 0.001 84000   [--live] [--tif GTC]
  python3 perp_trade.py stop   BTCUSDT SELL 0.001 82500   [--live] [--reduce-only]   # 止损市价
  python3 perp_trade.py tp     BTCUSDT SELL 0.001 86000   [--live] [--reduce-only]   # 止盈市价
  python3 perp_trade.py close  BTCUSDT                    [--live]                    # 市价平掉该标的持仓
  python3 perp_trade.py cancel BTCUSDT <orderId>          [--live]
  python3 perp_trade.py cancel-all BTCUSDT                [--live]
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fapi_client import FapiClient, FapiError  # noqa: E402

CST = timezone(timedelta(hours=8))


def die(msg, code=1):
    print("✗ %s" % msg)
    sys.exit(code)


def ok(msg):
    print("✓ %s" % msg)


def jp(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def parse(argv):
    """极简参数解析: 位置参数 + --flag [value]。"""
    pos, flags, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            k = a[2:]
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not nxt.startswith("--"):
                flags[k] = nxt
                i += 2
            else:
                flags[k] = True
                i += 1
        else:
            pos.append(a)
            i += 1
    return pos, flags


def client(need_key=True):
    c = FapiClient()
    if need_key and not (c.api_key and c.api_secret):
        die("缺 API key: export BINANCE_API_KEY / BINANCE_API_SECRET, 或写入 binance/.env")
    return c


# ------------------------------------------------------------------ 只读
def cmd_balance(c, pos, flags):
    rows = [b for b in c.balance() if float(b.get("balance", 0)) != 0 or float(b.get("availableBalance", 0)) != 0]
    if not rows:
        rows = c.balance()
    print("合约钱包余额")
    print("%-8s %14s %14s %14s" % ("资产", "总额", "可用", "占用(委托)"))
    for b in rows:
        print("%-8s %14s %14s %14s" % (b["asset"], b["balance"], b["availableBalance"], b["crossUnPnl"]))
    usdt = [b for b in c.balance() if b["asset"] == "USDT"]
    if usdt:
        print("\n可用保证金(USDT): %s | 未实现盈亏(全仓): %s" % (usdt[0]["availableBalance"], usdt[0]["crossUnPnl"]))


def cmd_positions(c, pos, flags):
    sym = pos[1].upper() if len(pos) > 1 else None
    rows = c.position_risk(sym)
    rows = [p for p in rows if float(p.get("positionAmt", 0)) != 0]
    if not rows:
        ok("无持仓")
        return
    print("%-10s %-6s %12s %12s %12s %12s %10s" % ("标的", "方向", "数量", "开仓均价", "标记价", "未实现盈亏", "杠杆"))
    for p in rows:
        amt = float(p["positionAmt"])
        print("%-10s %-6s %12s %12s %12s %12s %10s" % (
            p["symbol"], "LONG" if amt > 0 else "SHORT", p["positionAmt"], p["entryPrice"],
            p["markPrice"], p["unRealizedProfit"], p.get("leverage", "")))
    print("\n合计未实现盈亏: %.4f USDT" % sum(float(p["unRealizedProfit"]) for p in rows))


def cmd_orders(c, pos, flags):
    sym = pos[1].upper() if len(pos) > 1 else None
    rows = c.open_orders(sym)
    if not rows:
        ok("无挂单")
        return
    print("%-10s %-4s %-14s %12s %12s %-8s %s" % ("标的", "方向", "类型", "数量", "价格", "状态", "订单号"))
    for o in rows:
        print("%-10s %-4s %-14s %12s %12s %-8s %s" % (
            o["symbol"], o["side"], o["type"], o["origQty"], o.get("price", ""),
            o.get("status", ""), o["orderId"]))


def cmd_income(c, pos, flags):
    days = int(flags.get("days", 7))
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    rows = c.income(symbol=(pos[1].upper() if len(pos) > 1 else None), limit=200, start_time=start)
    agg = {}
    for r in rows:
        agg[r["incomeType"]] = agg.get(r["incomeType"], 0.0) + float(r["income"])
    print("近 %d 天资金流水 (共 %d 条)" % (days, len(rows)))
    for k, v in sorted(agg.items(), key=lambda x: -abs(x[1])):
        print("  %-16s %+.4f USDT" % (k, v))
    print("  %-16s %+.4f USDT" % ("合计", sum(agg.values())))


def cmd_mode(c, pos, flags):
    dual = c.position_mode()
    try:
        ma = c.multi_assets_margin()
    except FapiError:
        ma = {}
    print("持仓模式  : %s" % ("双向(Hedge)" if dual.get("dualSidePosition") else "单向(One-way)"))
    print("保证金模式: %s" % ("多资产(Multi-Assets)" if ma.get("multiAssetsMargin") else "单资产(默认)"))
    rows = [p for p in c.position_risk() if float(p.get("positionAmt", 0)) != 0]
    mm = set(p.get("marginType", "cross") for p in rows)
    print("当前持仓保证金类型: %s" % (", ".join(mm) if mm else "(无持仓)"))


def cmd_fees(c, pos, flags):
    sym = pos[1].upper() if len(pos) > 1 else "BTCUSDT"
    r = c.commission_rate(sym)
    maker = float(r["makerCommissionRate"]) * 100
    taker = float(r["takerCommissionRate"]) * 100
    print("%s 手续费: maker %.4f%% / taker %.4f%%" % (sym, maker, taker))
    print("往返 taker %.4f%% — 做单周期需覆盖该成本(含滑点)" % (taker * 2))


def cmd_account(c, pos, flags):
    a = c.account()
    print("=== 资产 ===")
    for x in a.get("assets", []):
        if float(x.get("walletBalance", 0)) != 0:
            print("  %-6s 钱包 %12s | 可用 %12s | 未实现 %10s" % (
                x["asset"], x["walletBalance"], x["availableBalance"], x.get("unrealizedProfit", "0")))
    print("总钱包 %s USDT | 未实现盈亏 %s | 维持保证金 %s" % (
        a.get("totalWalletBalance"), a.get("totalUnrealizedProfit"), a.get("totalMaintMargin")))
    print("=== 持仓 ===")
    for p in a.get("positions", []):
        if float(p.get("positionAmt", 0)) != 0:
            print("  %-10s %-5s 数量 %10s 开仓价 %10s 未实现 %10s 杠杆 %s" % (
                p["symbol"], p.get("positionSide", ""), p["positionAmt"], p["entryPrice"],
                p.get("unrealizedProfit", ""), p.get("leverage", "")))
    print("=== 挂单 %d 条 ===" % len(a.get("orders", [])))


# ------------------------------------------------------------------ 设置
def cmd_set_leverage(c, pos, flags):
    sym, lev = pos[1].upper(), int(pos[2])
    r = c.configure_leverage(sym, lev)
    ok("%s 杠杆 -> %sx (max %sx)" % (r["symbol"], r["leverage"], r.get("maxNotionalValue", "")))


def cmd_set_margin(c, pos, flags):
    sym, mt = pos[1].upper(), pos[2].upper()
    try:
        c.configure_margin_type(sym, mt)
        ok("%s 保证金模式 -> %s" % (sym, mt))
    except FapiError as exc:
        if exc.code == -4046:
            ok("%s 已经是 %s, 无需修改" % (sym, mt))
        else:
            raise


# ------------------------------------------------------------------ 仓位计算
def cmd_size(c, pos, flags):
    sym = (flags.get("symbol") or "BTCUSDT").upper()
    side = (flags.get("side") or "long").lower()
    equity = float(flags.get("equity") or 0)
    if not equity:
        try:
            bal = [b for b in c.balance() if b["asset"] == "USDT"]
            equity = float(bal[0]["availableBalance"]) if bal else 0.0
        except FapiError:
            equity = 0.0
    risk_pct = float(flags.get("risk-pct", 1))
    stop_pct = float(flags.get("stop-pct", 2))
    lev = float(flags.get("leverage", 0))
    price = c.price(sym)
    if not equity:
        print("⚠ 未取到可用权益(无 key), 用 --equity XXXX 指定后再算")
    risk_usd = equity * risk_pct / 100.0
    stop_dist = price * stop_pct / 100.0
    qty = risk_usd / stop_dist
    notional = qty * price
    f = c.filters(sym)
    print("标的 %s | 现价 %.2f | 可用权益 %.2f USDT" % (sym, price, equity))
    print("风险 %.2f%% = %.2f USDT, 止损距离 %.2f%% (%.2f)" % (risk_pct, risk_usd, stop_pct, stop_dist))
    print("仓位数量 %.6f (= %.2f USDT 名义, 价差 1 USDT 盈亏 %.4f USDT)" % (qty, notional, qty))
    print("按 stepSize %s 取整: %s" % (f["stepSize"], c.round_qty(sym, qty)))
    okq, errs = c.check_order(sym, "BUY" if side == "long" else "SELL", qty, price)
    if not okq:
        print("下单自检: %s" % "; ".join(errs))
    else:
        print("下单自检: 通过 (minNotional %s USDT)" % f["minNotional"])
    if lev:
        margin = notional / lev
        print("杠杆 %gx -> 占用保证金 %.2f USDT (权益的 %.1f%%) | 粗略强平距离 ~%.1f%% (1/杠杆, 未计维持保证金)"
              % (lev, margin, margin / equity * 100 if equity else 0, 100.0 / lev))
        if equity and margin > equity:
            print("  ⚠ 保证金 %.2f > 可用权益 %.2f, 该杠杆下开不出这么多仓位" % (margin, equity))
    print("止损价参考: %s %s = %.2f (现价 %+.2f%%)"
          % (sym, "多头" if side == "long" else "空头",
             price * (1 - stop_pct / 100) if side == "long" else price * (1 + stop_pct / 100),
             -stop_pct if side == "long" else stop_pct))


# ------------------------------------------------------------------ 下单
def _send(c, params, flags, desc):
    live = bool(flags.get("live"))
    print("[%s] %s %s" % ("LIVE" if live else "DRY-RUN", desc, json.dumps(params, ensure_ascii=False)))
    okq, errs = None, []
    if params.get("quantity"):
        price = None
        if params.get("type") == "LIMIT":
            price = params.get("price")
        okq, errs = c.check_order(params["symbol"], params["side"], params["quantity"], price)
        print("    自检: %s" % ("通过" if okq else "; ".join(errs)))
    if not live:
        print("    未发送 (加 --live 才真正下单)")
        return None
    if okq is False:
        die("自检未通过, 拒绝下单: %s" % "; ".join(errs))
    r = c.order(**params)
    ok("已下单 orderId=%s status=%s avgPrice=%s qty=%s" % (
        r.get("orderId"), r.get("status"), r.get("avgPrice"), r.get("executedQty")))
    return r


def cmd_market(c, pos, flags):
    sym, side, qty = pos[1].upper(), pos[2].upper(), pos[3]
    p = {"symbol": sym, "side": side, "order_type": "MARKET", "quantity": qty}
    if flags.get("reduce-only"):
        p["reduce_only"] = True
    _send(c, p, flags, "市价单")


def cmd_limit(c, pos, flags):
    sym, side, qty, price = pos[1].upper(), pos[2].upper(), pos[3], pos[4]
    p = {"symbol": sym, "side": side, "order_type": "LIMIT", "quantity": qty,
         "price": price, "time_in_force": flags.get("tif", "GTC")}
    if flags.get("reduce-only"):
        p["reduce_only"] = True
    _send(c, p, flags, "限价单")


def cmd_stop(c, pos, flags):
    sym, side, qty, sp = pos[1].upper(), pos[2].upper(), pos[3], pos[4]
    p = {"symbol": sym, "side": side, "order_type": "STOP_MARKET", "quantity": qty,
         "stop_price": sp, "reduce_only": flags.get("reduce-only", True), "working_type": "MARK_PRICE"}
    _send(c, p, flags, "止损市价单 (触发=标记价)")


def cmd_tp(c, pos, flags):
    sym, side, qty, sp = pos[1].upper(), pos[2].upper(), pos[3], pos[4]
    p = {"symbol": sym, "side": side, "order_type": "TAKE_PROFIT_MARKET", "quantity": qty,
         "stop_price": sp, "reduce_only": flags.get("reduce-only", True), "working_type": "MARK_PRICE"}
    _send(c, p, flags, "止盈市价单 (触发=标记价)")


def cmd_close(c, pos, flags):
    sym = pos[1].upper()
    rows = [p for p in c.position_risk(sym) if float(p.get("positionAmt", 0)) != 0]
    if not rows:
        ok("%s 无持仓" % sym)
        return
    for p in rows:
        amt = float(p["positionAmt"])
        side = "SELL" if amt > 0 else "BUY"
        params = {"symbol": sym, "side": side, "order_type": "MARKET", "quantity": abs(amt),
                  "reduce_only": True}
        _send(c, params, flags, "市价平仓 %s %s" % ("多头" if amt > 0 else "空头", abs(amt)))


def cmd_cancel(c, pos, flags):
    sym, oid = pos[1].upper(), pos[2]
    print("[%s] 撤单 %s orderId=%s" % ("LIVE" if flags.get("live") else "DRY-RUN", sym, oid))
    if flags.get("live"):
        r = c.cancel(sym, order_id=oid)
        ok("已撤单 %s" % r.get("orderId", oid))


def cmd_cancel_all(c, pos, flags):
    sym = pos[1].upper()
    print("[%s] 撤销 %s 全部挂单" % ("LIVE" if flags.get("live") else "DRY-RUN", sym))
    if flags.get("live"):
        c.cancel_all(sym)
        ok("已全部撤销")


COMMANDS = {
    "account": cmd_account, "balance": cmd_balance, "positions": cmd_positions, "orders": cmd_orders,
    "fees": cmd_fees, "income": cmd_income, "mode": cmd_mode,
    "set-leverage": cmd_set_leverage, "set-margin": cmd_set_margin, "size": cmd_size,
    "market": cmd_market, "limit": cmd_limit, "stop": cmd_stop, "tp": cmd_tp,
    "close": cmd_close, "cancel": cmd_cancel, "cancel-all": cmd_cancel_all,
}


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd = argv[1]
    if cmd not in COMMANDS:
        die("未知命令 %s (可用: %s)" % (cmd, ", ".join(sorted(COMMANDS))))
    pos, flags = parse(argv[2:])
    pos = [cmd] + pos
    # 只读/设置类命令必须有 key; 下单类命令只在 --live 时强制要 key(dry-run 可以无 key 预览参数)
    order_cmds = ("market", "limit", "stop", "tp", "close", "cancel", "cancel-all")
    need_key = (cmd not in order_cmds + ("size",)) or bool(flags.get("live"))
    try:
        c = client(need_key)
        COMMANDS[cmd](c, pos, flags)
    except FapiError as exc:
        die(str(exc))


if __name__ == "__main__":
    main(sys.argv)
