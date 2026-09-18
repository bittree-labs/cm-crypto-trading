#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC 合约数据实时分析 + 预警
============================
数据源优先级:
  1) CoinGlass v4 API  (open-api-v4.coinglass.com, header: CG-API-KEY)  —— 需付费套餐
  2) Binance fapi 公开接口 (免费, 无需 key)                             —— 备用/演示

统一输出: 价格 / 资金费率 / 持仓量(OI) / 多空比 / 主动买卖 / 爆仓 + 综合预警信号。

用法:
  python3 btc_contract_monitor.py           # 拉最新数据, 打印分析报告 + 预警
  python3 btc_contract_monitor.py --json    # 输出 JSON (便于 cron / 二次加工)
  python3 btc_contract_monitor.py --demo    # 强制走 Binance 免费源

环境变量:
  COINGLASS_API_KEY  覆盖下方的 API_KEY
  HTTPS_PROXY        如 http://127.0.0.1:7897 (海外网络走 Clash 时)

依赖: 仅 Python 标准库 (urllib), 无第三方依赖, 可直接用于 cron。
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ============================ 配置 ============================
def _load_key():
    """优先环境变量 COINGLASS_API_KEY, 其次脚本同目录的 .env (已 gitignore)。"""
    k = os.environ.get("COINGLASS_API_KEY")
    if k:
        return k
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("COINGLASS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


COINGLASS_API_KEY = _load_key()
COINGLASS_BASE = "https://open-api-v4.coinglass.com"
BINANCE_FAPI   = "https://fapi.binance.com"

SYMBOL        = "BTC"        # CoinGlass 币种 (聚合端点用)
PAIR          = "BTCUSDT"    # 交易对 (交易所级端点用)
EXCHANGE      = "Binance"
EXCHANGE_LIST = "Binance,OKX,Bybit"
INTERVAL      = "4h"         # 聚合周期 (HOBBYIST 套餐最低 4h)
HIST_LIMIT    = 200          # 历史窗口条数

# ---- 预警阈值 (可按需调) ----
ALERT = {
    "funding_annual_high": 20.0,   # 年化 > 20% 视为多头过热
    "funding_annual_low":  -20.0,  # 年化 < -20% 视为空头过热
    "funding_zscore":      2.0,    # z-score 偏离阈值
    "oi_surge_24h":  15.0,         # 24h OI 增幅 > 15% 预警 (杠杆堆积)
    "oi_drop_24h":  -15.0,         # 24h OI 降幅 < -15% 预警 (平仓/清算潮)
    "ls_ratio_high": 3.0,          # 多空比 > 3 多头拥挤
    "ls_ratio_low":  0.7,          # 多空比 < 0.7 空头拥挤
    "taker_high": 1.6,             # > 1.6 主动买盘主导
    "taker_low":  0.6,             # < 0.6 主动卖盘主导
    "liq_spike_usd": 50_000_000,   # 单周期爆仓额 > $50M 预警
    "divergence_price": 2.0,       # 价格变动阈值 %
    "divergence_oi": 2.0,          # OI 反向变动阈值 %
}

HTTP_TIMEOUT = 20


# ============================ HTTP 工具 ============================
def _http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("User-Agent", "btc-monitor/1.0")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body)


def _q(base, params):
    return base + "?" + urllib.parse.urlencode(params)


# ============================ 统一 schema 说明 ============================
# 各 provider 方法返回统一 schema 数组, analyze_* 只认统一字段:
#   price      : [{time, open, high, low, close, volume_usd}]
#   oi         : [{time, close}]                      (close = OI, 单位 USD)
#   funding    : [{time, close}]                      (close = 资金费率, 小数)
#   long_short : [{time, long_pct, short_pct, ratio}] (pct 为百分数)
#   taker      : [{time, taker_ratio, buy_usd, sell_usd}]
#   liquidation: [{time, long_usd, short_usd}]


# ============================ 数据源: CoinGlass v4 ============================
class CoinGlass:
    def __init__(self, key):
        self.key = key
        self.h = {"accept": "application/json", "CG-API-KEY": key}

    def _get(self, path, params):
        return _http_get(_q(COINGLASS_BASE + path, params), headers=self.h)

    def check_access(self):
        try:
            r = self._get("/api/futures/supported-coins", {})
        except Exception as e:
            return False, f"网络/请求异常: {e}"
        if str(r.get("code", "")) == "0":
            return True, ""
        return False, r.get("msg", "unknown")

    def price(self, interval, limit):
        data = self._get("/api/futures/price/history",
                         {"exchange": EXCHANGE, "symbol": PAIR, "interval": interval, "limit": limit}).get("data", [])
        return [{"time": x.get("time"), "open": float(x["open"]), "high": float(x["high"]),
                 "low": float(x["low"]), "close": float(x["close"]),
                 "volume_usd": float(x.get("volume_usd", 0))} for x in data]

    def oi(self, interval, limit):
        data = self._get("/api/futures/open-interest/aggregated-history",
                         {"symbol": SYMBOL, "interval": interval, "limit": limit, "unit": "usd"}).get("data", [])
        return [{"time": x.get("time"), "close": float(x["close"])} for x in data]

    def funding(self, interval, limit):
        data = self._get("/api/futures/funding-rate/history",
                         {"exchange": EXCHANGE, "symbol": PAIR, "interval": interval, "limit": limit}).get("data", [])
        return [{"time": x.get("time"), "close": float(x["close"])} for x in data]

    def long_short(self, interval, limit):
        data = self._get("/api/futures/global-long-short-account-ratio/history",
                         {"exchange": EXCHANGE, "symbol": PAIR, "interval": interval, "limit": limit}).get("data", [])
        out = []
        for x in data:
            lp = float(x.get("global_account_long_percent", 0))
            sp = float(x.get("global_account_short_percent", 0))
            out.append({"time": x.get("time"), "long_pct": lp, "short_pct": sp,
                        "ratio": lp / sp if sp else 0})
        return out

    def taker(self, interval, limit):
        data = self._get("/api/futures/aggregated-taker-buy-sell-volume/history",
                         {"exchange_list": EXCHANGE_LIST, "symbol": SYMBOL, "interval": interval, "limit": limit, "unit": "usd"}).get("data", [])
        out = []
        for x in data:
            b = float(x.get("aggregated_buy_volume_usd", 0))
            s = float(x.get("aggregated_sell_volume_usd", 0))
            out.append({"time": x.get("time"), "buy_usd": b, "sell_usd": s,
                        "taker_ratio": b / s if s else 0})
        return out

    def liquidation(self, interval, limit):
        data = self._get("/api/futures/liquidation/aggregated-history",
                         {"exchange_list": EXCHANGE_LIST, "symbol": SYMBOL, "interval": interval, "limit": limit}).get("data", [])
        return [{"time": x.get("time"),
                 "long_usd": float(x.get("aggregated_long_liquidation_usd", 0)),
                 "short_usd": float(x.get("aggregated_short_liquidation_usd", 0))} for x in data]

    def cvd(self, interval, limit):
        return self._get("/api/futures/aggregated-cvd/history",
                         {"exchange_list": EXCHANGE_LIST, "symbol": SYMBOL, "interval": interval, "limit": limit, "unit": "usd"}).get("data", [])


# ============================ 数据源: Binance fapi (免费) ============================
class Binance:
    def _get(self, path, params):
        return _http_get(_q(BINANCE_FAPI + path, params))

    def mark_price(self):
        return self._get("/fapi/v1/premiumIndex", {"symbol": PAIR})

    def price(self, interval, limit):
        data = self._get("/fapi/v1/klines", {"symbol": PAIR, "interval": interval, "limit": limit})
        return [{"time": k[0], "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume_usd": float(k[7])} for k in data]

    def oi(self, interval, limit):
        data = self._get("/futures/data/openInterestHist", {"symbol": PAIR, "period": "1h", "limit": limit})
        # sumOpenInterestValue 即 USD 计价的 OI
        return [{"time": x["timestamp"], "close": float(x["sumOpenInterestValue"])} for x in data]

    def funding(self, interval, limit):
        data = self._get("/fapi/v1/fundingRate", {"symbol": PAIR, "limit": limit})
        out = [{"time": x["fundingTime"], "close": float(x["fundingRate"])} for x in data]
        # 追加当前最新 funding (premiumIndex 的 lastFundingRate)
        try:
            cur = float(self.mark_price().get("lastFundingRate"))
            out.append({"time": int(time.time() * 1000), "close": cur})
        except Exception:
            pass
        return out

    def long_short(self, interval, limit):
        data = self._get("/futures/data/globalLongShortAccountRatio", {"symbol": PAIR, "period": "1h", "limit": limit})
        out = []
        for x in data:
            lp = float(x["longAccount"]) * 100   # 0-1 小数 -> 百分数
            sp = float(x["shortAccount"]) * 100
            out.append({"time": x["timestamp"], "long_pct": lp, "short_pct": sp,
                        "ratio": float(x["longShortRatio"])})
        return out

    def taker(self, interval, limit):
        data = self._get("/futures/data/takerlongshortRatio", {"symbol": PAIR, "period": "1h", "limit": limit})
        out = []
        for x in data:
            b = float(x["buyVol"]); s = float(x["sellVol"])
            out.append({"time": x["timestamp"], "buy_usd": b, "sell_usd": s,
                        "taker_ratio": float(x["buySellRatio"])})
        return out

    def ticker_24h(self):
        return self._get("/fapi/v1/ticker/24hr", {"symbol": PAIR})


# ============================ 指标分析 (统一 schema) ============================
def _pct(cur, prev):
    if not prev:
        return 0.0
    return (cur - prev) / abs(prev) * 100.0


def _mean_std(xs):
    if not xs:
        return 0.0, 0.0
    n = len(xs)
    m = sum(xs) / n
    return m, (sum((x - m) ** 2 for x in xs) / n) ** 0.5


def _fmt_time(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def analyze_price(series):
    if not series:
        return {}
    closes = [x["close"] for x in series]
    cur = closes[-1]
    prev24 = closes[-7] if len(closes) >= 7 else closes[0]  # 4h * 6 = 24h
    return {
        "price": cur,
        "chg_24h_pct": _pct(cur, prev24),
        "period_high": max(x["high"] for x in series),
        "period_low": min(x["low"] for x in series),
        "ts": series[-1]["time"],
    }


def analyze_oi(series):
    if not series:
        return {}
    cur = series[-1]["close"]
    def find_offset(hours):
        target = series[-1]["time"] - hours * 3600 * 1000
        best = series[0]["close"]
        for x in series:
            if x["time"] <= target:
                best = x["close"]
            else:
                break
        return best
    return {"oi_usd": cur,
            "chg_24h_pct": _pct(cur, find_offset(24)),
            "chg_4h_pct": _pct(cur, find_offset(4)),
            "ts": series[-1]["time"]}


def analyze_funding(series):
    if not series:
        return {}
    rates = [x["close"] for x in series]
    cur = rates[-1]
    annual = cur * 3 * 365 * 100  # 8h 结算 -> 年化
    m, sd = _mean_std(rates[-50:])
    return {"funding_rate": cur, "annual_pct": annual,
            "zscore": (cur - m) / sd if sd > 0 else 0.0, "mean_rate": m}


def analyze_longshort(series):
    if not series:
        return {}
    last = series[-1]
    return {"ls_ratio": last.get("ratio"),
            "long_pct": last.get("long_pct"),
            "short_pct": last.get("short_pct")}


def analyze_taker(series):
    if not series:
        return {}
    last = series[-1]
    return {"taker_ratio": last.get("taker_ratio"),
            "buy_usd": last.get("buy_usd"), "sell_usd": last.get("sell_usd")}


def analyze_liquidation(series):
    if not series:
        return {}
    last = series[-1]
    return {"long_liq_usd": last.get("long_usd"), "short_liq_usd": last.get("short_usd"),
            "total_liq_usd": (last.get("long_usd") or 0) + (last.get("short_usd") or 0),
            "ts": last.get("time")}


# ============================ 预警引擎 ============================
def build_alerts(m):
    alerts = []
    def add(sev, title, detail):
        alerts.append({"severity": sev, "title": title, "detail": detail})

    f = m.get("funding") or {}
    if f.get("annual_pct") is not None:
        ap = f["annual_pct"]; z = f.get("zscore", 0)
        if ap > ALERT["funding_annual_high"]:
            add("HIGH", "资金费率多头过热", f"年化 {ap:+.2f}% (> {ALERT['funding_annual_high']}%)，多头拥挤、做多成本高")
        if ap < ALERT["funding_annual_low"]:
            add("HIGH", "资金费率空头过热", f"年化 {ap:+.2f}% (< {ALERT['funding_annual_low']}%)，空头拥挤、做空成本高")
        if abs(z) >= ALERT["funding_zscore"]:
            add("MED", f"资金费率 z-score 异常({'偏高' if z>0 else '偏低'})",
                f"z={z:+.2f}，偏离近 50 期均值 {f.get('mean_rate',0)*100:.4f}%")

    oi = m.get("oi") or {}
    if oi.get("chg_24h_pct") is not None:
        c24 = oi["chg_24h_pct"]
        if c24 > ALERT["oi_surge_24h"]:
            add("HIGH", "持仓量 24h 急升", f"OI 24h +{c24:.1f}% ({oi.get('oi_usd',0)/1e9:.2f}B USD)，杠杆快速堆积")
        if c24 < ALERT["oi_drop_24h"]:
            add("HIGH", "持仓量 24h 骤降", f"OI 24h {c24:.1f}% ({oi.get('oi_usd',0)/1e9:.2f}B USD)，大规模平仓/清算")

    ls = m.get("long_short") or {}
    if ls.get("ls_ratio"):
        r = ls["ls_ratio"]
        if r > ALERT["ls_ratio_high"]:
            add("MED", "多空账户比多头拥挤", f"多空比 {r:.2f} (> {ALERT['ls_ratio_high']})，账户多空极度失衡")
        if r < ALERT["ls_ratio_low"]:
            add("MED", "多空账户比空头拥挤", f"多空比 {r:.2f} (< {ALERT['ls_ratio_low']})，空头账户占优")

    tk = m.get("taker") or {}
    if tk.get("taker_ratio"):
        tr = tk["taker_ratio"]
        if tr > ALERT["taker_high"]:
            add("MED", "主动买盘主导", f"taker 买卖比 {tr:.2f} (> {ALERT['taker_high']})，主动买入意愿强")
        if tr < ALERT["taker_low"]:
            add("MED", "主动卖盘主导", f"taker 买卖比 {tr:.2f} (< {ALERT['taker_low']})，主动卖出意愿强")

    liq = m.get("liquidation") or {}
    if liq.get("total_liq_usd"):
        t = liq["total_liq_usd"]
        if t > ALERT["liq_spike_usd"]:
            side = "多" if liq["long_liq_usd"] > liq["short_liq_usd"] else "空"
            add("HIGH", "爆仓潮", f"单周期爆仓 ${t/1e6:.1f}M（{side}头为主），市场波动放大")

    p = m.get("price") or {}
    if p.get("chg_24h_pct") is not None and oi.get("chg_24h_pct") is not None:
        pc = p["chg_24h_pct"]; oc = oi["chg_24h_pct"]
        if pc > ALERT["divergence_price"] and oc < -ALERT["divergence_oi"]:
            add("MED", "价涨 OI 降(背离)", f"价格 +{pc:.1f}% 但 OI {oc:.1f}%，疑似逼空(空头回补)")
        if pc < -ALERT["divergence_price"] and oc > ALERT["divergence_oi"]:
            add("MED", "价跌 OI 增(背离)", f"价格 {pc:.1f}% 但 OI +{oc:.1f}%，空头加仓/多头扛单")
    return alerts


# ============================ 报告 ============================
def render_report(m, alerts, source, note=""):
    p = m.get("price") or {}; f = m.get("funding") or {}; oi = m.get("oi") or {}
    ls = m.get("long_short") or {}; tk = m.get("taker") or {}; liq = m.get("liquidation") or {}

    L = [f"BTC 合约分析报告  (数据源: {source})", "=" * 46]
    if note:
        L.append(f"[!] {note}")
    if p:
        L.append(f"价格        : ${p.get('price',0):,.1f}  24h {p.get('chg_24h_pct',0):+.2f}%")
    if f:
        L.append(f"资金费率    : {f.get('funding_rate',0)*100:+.4f}% (年化 {f.get('annual_pct',0):+.2f}%, z={f.get('zscore',0):+.2f})")
    if oi:
        L.append(f"持仓量 OI   : ${oi.get('oi_usd',0)/1e9:,.2f}B  24h {oi.get('chg_24h_pct',0):+.1f}%  4h {oi.get('chg_4h_pct',0):+.1f}%")
    if ls and ls.get("ls_ratio"):
        lp = ls.get("long_pct"); sp = ls.get("short_pct")
        extra = f"  多 {lp:.1f}% / 空 {sp:.1f}%" if lp is not None else ""
        L.append(f"多空账户比  : {ls['ls_ratio']:.2f}{extra}")
    if tk and tk.get("taker_ratio"):
        L.append(f"主动买卖比  : {tk['taker_ratio']:.2f}")
    if liq and liq.get("total_liq_usd"):
        L.append(f"爆仓        : 多 ${liq.get('long_liq_usd',0)/1e6:,.1f}M / 空 ${liq.get('short_liq_usd',0)/1e6:,.1f}M")
    L.append("-" * 46)

    if alerts:
        L.append(f"⚠ 预警 {len(alerts)} 条:")
        sev_icon = {"HIGH": "🔴", "MED": "🟡", "LOW": "🟢"}
        for a in alerts:
            L.append(f"  {sev_icon.get(a['severity'],'•')} [{a['severity']}] {a['title']}")
            L.append(f"        {a['detail']}")
    else:
        L.append("✅ 无触发预警，市场处于常态区间")
    return "\n".join(L)


# ============================ 数据采集 ============================
def collect(prov, is_coinglass, interval=INTERVAL, limit=HIST_LIMIT):
    m = {}
    m["price"] = analyze_price(prov.price(interval, limit))
    m["oi"] = analyze_oi(prov.oi(interval, limit))
    m["funding"] = analyze_funding(prov.funding(interval, limit))
    m["long_short"] = analyze_longshort(prov.long_short(interval, limit))
    m["taker"] = analyze_taker(prov.taker(interval, limit))
    if is_coinglass:
        m["liquidation"] = analyze_liquidation(prov.liquidation(interval, limit))
        try:
            cvd = prov.cvd(interval, limit)
            if cvd:
                m["cvd_last"] = cvd[-1].get("cum_vol_delta")
        except Exception:
            pass
    # Binance 用 ticker 修正精确的 24h 涨跌幅
    if not is_coinglass:
        try:
            tk = prov.ticker_24h()
            if tk and m.get("price"):
                m["price"]["chg_24h_pct"] = float(tk.get("priceChangePercent", m["price"]["chg_24h_pct"]))
        except Exception:
            pass
    return m


def main():
    want_json = "--json" in sys.argv
    force_binance = "--demo" in sys.argv

    source = "CoinGlass v4"; note = ""; m = None

    if not force_binance:
        cg = CoinGlass(COINGLASS_API_KEY)
        ok, err = cg.check_access()
        if ok:
            try:
                m = collect(cg, is_coinglass=True)
            except Exception as e:
                note = f"CoinGlass 数据拉取失败({e})，回退 Binance"; m = None
        else:
            note = f"CoinGlass 返回「{err}」(套餐权限不足)，已回退 Binance 免费源"; m = None

    if m is None:
        source = "Binance fapi (免费)"
        m = collect(Binance(), is_coinglass=False)

    alerts = build_alerts(m)

    if want_json:
        out = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "source": source, "note": note, "metrics": m, "alerts": alerts}
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_report(m, alerts, source, note))


if __name__ == "__main__":
    main()
