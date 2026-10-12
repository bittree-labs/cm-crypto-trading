#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USDT-M 永续合约数据快照 (Binance fapi, 全公共接口, 无需 key)
==========================================================
一次拉齐: 价格 / 标记价基差 / 24h 统计 / 资金费率(当期+历史+年化) / 持仓量 OI(现值+1h/4h/24h 变化)
          / 大户多空账户比·持仓比 / 全市场多空账户比 / 主动买卖量比 / 多周期 K 线摘要 / 衍生信号

用法:
  python3 perp_snapshot.py                      # BTCUSDT 文本报告
  python3 perp_snapshot.py ETHUSDT SOLUSDT      # 多标的
  python3 perp_snapshot.py --json               # JSON (cron/二次加工用)
  python3 perp_snapshot.py --watch 60           # 每 60s 刷新一次
  python3 perp_snapshot.py --symbol BTCUSDT --period 15m
  python3 perp_snapshot.py BTCUSDT ETHUSDT --save ../data/binance_perp   # 追加 JSONL 历史

依赖: 仅标准库 + 同目录 fapi_client.py
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fapi_client import FapiClient, FapiError  # noqa: E402

CST = timezone(timedelta(hours=8))
DEFAULT_SYMBOL = "BTCUSDT"


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _ts(ms):
    return datetime.fromtimestamp(ms / 1000.0, CST).strftime("%m-%d %H:%M")


# ---------------------------------------------------------------- 采集
# 需要拉取的子项: key -> (调用说明, lambda)
def _jobs(c, symbol, period):
    return [
        ("mark", lambda: c.mark_price(symbol)),                            # 标记价/指数价/资金费率/下次结算
        ("ticker24h", lambda: c.ticker24h(symbol)),
        ("book", lambda: c.book_ticker(symbol)),
        ("oi", lambda: c.open_interest(symbol)),
        ("oi_hist", lambda: c.open_interest_hist(symbol, "5m", 288)),      # 24h @5m
        ("funding_hist", lambda: c.funding_rate(symbol, 48)),
        ("ls_global", lambda: c.long_short_ratio(symbol, "global", period, 24)),
        ("ls_top_account", lambda: c.long_short_ratio(symbol, "top_account", period, 24)),
        ("ls_top_position", lambda: c.long_short_ratio(symbol, "top_position", period, 24)),
        ("taker", lambda: c.taker_ratio(symbol, period, 24)),
        ("k1h", lambda: c.klines(symbol, "1h", 168)),
        ("k4h", lambda: c.klines(symbol, "4h", 90)),
        ("k1d", lambda: c.klines(symbol, "1d", 60)),
        ("filters", lambda: c.filters(symbol)),
    ]


def collect(c, symbol, period="1h", workers=8):
    """并发拉一个标的的全部永续数据(约 2s), 失败的子项置 None 而不中断。"""
    out = {"symbol": symbol, "fetched_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST"), "errors": []}
    jobs = _jobs(c, symbol, period)

    def run(job):
        key, fn = job
        try:
            return key, fn(), None
        except FapiError as exc:
            return key, None, "%s: %s" % (key, exc.msg if hasattr(exc, "msg") else exc)
        except Exception as exc:                                   # noqa: BLE001
            return key, None, "%s: %s" % (key, exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, val, err in pool.map(run, jobs):
            out[key] = val
            if err:
                out["errors"].append(err)
    return out


def _oi_change(hist, hours):
    """从 5m OI 历史算 N 小时变化率。"""
    if not hist:
        return None
    n = max(1, int(hours * 60 / 5))
    if len(hist) <= n:
        n = len(hist) - 1
    if n <= 0:
        return None
    now = _f(hist[-1].get("sumOpenInterest"))
    past = _f(hist[-1 - n].get("sumOpenInterest"))
    if past <= 0:
        return None
    return {"abs": now - past, "pct": (now - past) / past * 100.0,
            "from": _ts(hist[-1 - n]["timestamp"]), "to": _ts(hist[-1]["timestamp"])}


def analyze(s):
    """把原始数据算成指标 + 信号。"""
    a = {"symbol": s["symbol"], "errors": s["errors"], "fetched_at": s["fetched_at"]}
    mark = s.get("mark") or {}
    t24 = s.get("ticker24h") or {}
    book = s.get("book") or {}
    oi = s.get("oi") or {}

    price = _f(mark.get("markPrice")) or _f(t24.get("lastPrice"))
    a["price"] = price
    a["index_price"] = _f(mark.get("indexPrice"))
    a["basis"] = price - a["index_price"]
    a["basis_pct"] = (a["basis"] / a["index_price"] * 100.0) if a["index_price"] else 0.0
    a["bid"] = _f(book.get("bidPrice"))
    a["ask"] = _f(book.get("askPrice"))
    a["spread_bp"] = ((a["ask"] - a["bid"]) / a["bid"] * 10000.0) if a["bid"] else 0.0
    a["change_24h_pct"] = _f(t24.get("priceChangePercent"))
    a["high_24h"] = _f(t24.get("highPrice"))
    a["low_24h"] = _f(t24.get("lowPrice"))
    a["quote_vol_24h"] = _f(t24.get("quoteVolume"))
    a["trades_24h"] = int(_f(t24.get("count")))
    a["open_interest"] = _f(oi.get("openInterest"))
    a["open_interest_usd"] = a["open_interest"] * price
    a["oi_chg_1h"] = _oi_change(s.get("oi_hist"), 1)
    a["oi_chg_4h"] = _oi_change(s.get("oi_hist"), 4)
    a["oi_chg_24h"] = _oi_change(s.get("oi_hist"), 24)

    # 资金费率
    fr_now = _f(mark.get("lastFundingRate"))
    a["funding_rate_now"] = fr_now
    a["funding_rate_annual_pct"] = fr_now * 3 * 365 * 100.0        # 8h 一期, 3 期/天
    a["next_funding_time"] = _ts(mark["nextFundingTime"]) if mark.get("nextFundingTime") else None
    fh = s.get("funding_hist") or []
    if fh:
        rates = [_f(x.get("fundingRate")) for x in fh]
        a["funding_avg_16d_pct"] = sum(rates) / len(rates) * 100.0   # 48 期 × 8h = 16 天
        a["funding_sum_16d_pct"] = sum(rates) * 100.0
        a["funding_rate_min_16d_pct"] = min(rates) * 100.0
        a["funding_rate_max_16d_pct"] = max(rates) * 100.0
        a["funding_hist_last"] = [{"t": _ts(x["fundingTime"]), "rate_pct": _f(x["fundingRate"]) * 100.0} for x in fh[-8:]]

    # 多空比 / 主动买卖
    def _last(rows, field):
        return _f(rows[-1].get(field)) if rows else None

    lg, lta, ltp, tk = s.get("ls_global"), s.get("ls_top_account"), s.get("ls_top_position"), s.get("taker")
    a["long_short_global"] = _last(lg, "longShortRatio")
    a["long_short_top_account"] = _last(lta, "longShortRatio")
    a["long_short_top_position"] = _last(ltp, "longShortRatio")
    a["taker_buy_sell_ratio"] = _last(tk, "buySellRatio")
    if lg:
        a["long_account_pct_global"] = _f(lg[-1].get("longAccount")) * 100.0
        a["long_short_global_trend"] = [_f(x.get("longShortRatio")) for x in lg[-12:]]
    if tk:
        a["taker_ratio_trend"] = [_f(x.get("buySellRatio")) for x in tk[-12:]]

    # K 线摘要: 涨跌幅 + ATR(波动) + 位置
    def _kl(k, n):
        if not k or len(k) < n + 1:
            return None
        last = k[-1]
        prev = k[-1 - n]
        hi = max(_f(x[2]) for x in k[-n:])
        lo = min(_f(x[3]) for x in k[-n:])
        chg = (_f(last[4]) - _f(prev[4])) / _f(prev[4]) * 100.0 if _f(prev[4]) else 0.0
        trs = []
        for i in range(len(k) - n, len(k)):
            x = k[i]
            pc = _f(k[i - 1][4])
            trs.append(max(_f(x[2]) - _f(x[3]), abs(_f(x[2]) - pc), abs(_f(x[3]) - pc)))
        atr = sum(trs) / len(trs)
        return {"chg_pct": chg, "hi": hi, "lo": lo, "atr": atr, "atr_pct": atr / price * 100.0 if price else 0.0}

    a["k1h_24"] = _kl(s.get("k1h"), 24)
    a["k4h_30"] = _kl(s.get("k4h"), 30)
    a["k1d_14"] = _kl(s.get("k1d"), 14)
    a["filters"] = s.get("filters")
    a["signals"] = _signals(a)
    return a


def _signals(a):
    """衍生信号: 拥挤度/资金费拥挤/主动流/波动。给的是观察信号, 不是交易建议。"""
    sig = []
    fr_ann = a.get("funding_rate_annual_pct") or 0.0
    if fr_ann > 30:
        sig.append({"level": "warn", "tag": "资金费多头拥挤",
                    "detail": "年化 %.1f%% — 多头付钱, 现货/中性策略有利, 追多有成本" % fr_ann})
    elif fr_ann < -30:
        sig.append({"level": "warn", "tag": "资金费空头拥挤",
                    "detail": "年化 %.1f%% — 空头付钱, 反弹易挤空" % fr_ann})
    else:
        sig.append({"level": "ok", "tag": "资金费中性", "detail": "年化 %.1f%%" % fr_ann})

    ch = a.get("oi_chg_24h") or {}
    pc = a.get("change_24h_pct") or 0.0
    if ch:
        if ch["pct"] > 5 and pc > 0:
            sig.append({"level": "warn", "tag": "价涨仓增", "detail": "OI24h +%.1f%% 且价 +%.1f%% — 多头杠杆在加, 回调易连环爆" % (ch["pct"], pc)})
        elif ch["pct"] > 5 and pc < 0:
            sig.append({"level": "warn", "tag": "价跌仓增", "detail": "OI24h +%.1f%% 且价 %.1f%% — 空头在加仓/承压" % (ch["pct"], pc)})
        elif ch["pct"] < -5 and pc < 0:
            sig.append({"level": "info", "tag": "价跌仓减", "detail": "OI24h %.1f%% — 多头止损离场, 抛压释放中" % ch["pct"]})
        elif ch["pct"] < -5 and pc > 0:
            sig.append({"level": "info", "tag": "价涨仓减", "detail": "OI24h %.1f%% — 空头回补推动, 涨势可持续性偏弱" % ch["pct"]})

    tk = a.get("taker_buy_sell_ratio")
    if tk is not None:
        if tk > 1.15:
            sig.append({"level": "info", "tag": "主动买占优", "detail": "taker 买卖量比 %.2f" % tk})
        elif tk < 0.87:
            sig.append({"level": "info", "tag": "主动卖占优", "detail": "taker 买卖量比 %.2f" % tk})

    lsg, lst = a.get("long_short_global"), a.get("long_short_top_position")
    if lsg and lst and lst > 0:
        if lsg / lst > 1.6:
            sig.append({"level": "info", "tag": "散户相对更偏多",
                        "detail": "散户多空比 %.2f vs 大户持仓比 %.2f — 反向指标可作参考" % (lsg, lst)})
        elif lsg / lst < 0.65:
            sig.append({"level": "info", "tag": "大户相对更偏多",
                        "detail": "大户持仓比 %.2f vs 散户 %.2f" % (lst, lsg)})

    atr = (a.get("k1d_14") or {}).get("atr_pct")
    if atr:
        level = "warn" if atr > 4.5 else ("ok" if atr < 2.0 else "info")
        sig.append({"level": level, "tag": "日波动 %.2f%% (ATR14)" % atr,
                    "detail": "建议止损距离 ≥ %.1f%% (1.5×ATR), 杠杆上限 ~%.0fx (按 3%% 账户风险)" % (1.5 * atr, min(20, 3.0 / max(atr / 100 * 1.5, 0.001)))})
    return sig


# ---------------------------------------------------------------- 输出
def render(a):
    L = []
    p = a["price"]
    L.append("=" * 78)
    L.append("%s 永续  %s" % (a["symbol"], a["fetched_at"]))
    L.append("=" * 78)
    L.append("价格   标记 %.2f | 指数 %.2f | 基差 %+.2f (%+.3f%%) | 买一/卖一 %.2f/%.2f (%.2f bp)"
             % (p, a["index_price"], a["basis"], a["basis_pct"], a["bid"], a["ask"], a["spread_bp"]))
    L.append("24h    涨跌 %+.2f%% | 高 %.2f 低 %.2f | 成交额 $%.2fB | 成交 %d 笔"
             % (a["change_24h_pct"], a["high_24h"], a["low_24h"], a["quote_vol_24h"] / 1e9, a["trades_24h"]))
    L.append("持仓量 OI %.2f %s ($%.2fB)" % (a["open_interest"], a["symbol"].replace("USDT", ""), a["open_interest_usd"] / 1e9))
    for k, label in (("oi_chg_1h", "1h"), ("oi_chg_4h", "4h"), ("oi_chg_24h", "24h")):
        v = a.get(k)
        if v:
            L.append("       OI %-3s %+.2f%% (%+.0f)  [%s → %s]" % (label, v["pct"], v["abs"], v["from"], v["to"]))
    L.append("资金费 当期 %.4f%% | 年化 %+.2f%% | 下次结算 %s"
             % (a["funding_rate_now"] * 100, a["funding_rate_annual_pct"], a["next_funding_time"]))
    if "funding_hist_last" in a:
        L.append("       近8期: %s" % "  ".join("%.4f%%" % x["rate_pct"] for x in a["funding_hist_last"]))
        L.append("       近48期(16天) 均值 %.4f%% | 合计 %.3f%% | 区间 [%.4f%%, %.4f%%]"
                 % (a["funding_avg_16d_pct"], a["funding_sum_16d_pct"], a["funding_rate_min_16d_pct"], a["funding_rate_max_16d_pct"]))
    L.append("多空   全市场账户比 %.3f (多 %.1f%%) | 大户账户比 %.3f | 大户持仓比 %.3f | taker 买卖比 %.3f"
             % (a.get("long_short_global") or 0, a.get("long_account_pct_global") or 0,
                a.get("long_short_top_account") or 0, a.get("long_short_top_position") or 0,
                a.get("taker_buy_sell_ratio") or 0))
    if a.get("long_short_global_trend"):
        L.append("       散户多空比近12期: %s" % " ".join("%.2f" % x for x in a["long_short_global_trend"]))
    if a.get("taker_ratio_trend"):
        L.append("       taker 比近12期:  %s" % " ".join("%.2f" % x for x in a["taker_ratio_trend"]))
    for key, label in (("k1h_24", "1h/近24根"), ("k4h_30", "4h/近30根"), ("k1d_14", "1d/近14根")):
        v = a.get(key)
        if v:
            L.append("K线 %-9s 涨跌 %+.2f%% | 高 %.2f 低 %.2f | ATR %.2f (%.2f%%)"
                     % (label, v["chg_pct"], v["hi"], v["lo"], v["atr"], v["atr_pct"]))
    f = a.get("filters")
    if f:
        L.append("规则   数量步长 %s (min %s) | 价格步长 %s | 最小名义 %s USDT | 状态 %s"
                 % (f["stepSize"], f["minQty"], f["tickSize"], f["minNotional"], f["status"]))
    L.append("-" * 78)
    L.append("信号")
    for s in a["signals"]:
        L.append("  [%s] %s — %s" % (s["level"].upper(), s["tag"], s["detail"]))
    if a["errors"]:
        L.append("取数失败: %s" % "; ".join(a["errors"]))
    return "\n".join(L)


def main(argv):
    args = []
    watch = 0
    period = "1h"
    save_dir = None
    as_json = False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--watch", "--period", "--symbol", "--save"):
            val = argv[i + 1] if i + 1 < len(argv) else ""
            if a == "--watch":
                watch = int(val or 0)
            elif a == "--period":
                period = val or "1h"
            elif a == "--symbol":
                args.append(val)
            else:
                save_dir = val
            i += 2
            continue
        if a == "--json":
            as_json = True
        elif not a.startswith("--"):
            args.append(a)          # 位置参数 = 交易对
        i += 1
    if not args:
        args = [DEFAULT_SYMBOL]
    c = FapiClient()
    while True:
        results = []
        for sym in args:
            sym = sym.upper()
            try:
                results.append(analyze(collect(c, sym, period)))
            except FapiError as exc:
                results.append({"symbol": sym, "errors": [str(exc)], "signals": [], "price": 0.0})
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            stamp = datetime.now(CST).strftime("%Y%m%d")
            with open(os.path.join(save_dir, "perp_%s.jsonl" % stamp), "a", encoding="utf-8") as fh:
                for r in results:
                    fh.write(json.dumps({"ts": int(time.time()), "data": r}, ensure_ascii=False) + "\n")
        if as_json:
            print(json.dumps(results if len(results) > 1 else results[0], indent=2, ensure_ascii=False))
        else:
            print("\n\n".join(render(r) for r in results))
        if not watch:
            break
        time.sleep(watch)


if __name__ == "__main__":
    main(sys.argv)
