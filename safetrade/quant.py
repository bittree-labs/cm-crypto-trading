#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K线拉取 + 低频指标计算（纯标准库，不依赖 numpy/pandas）。

数据源：SafeTrade `/trade/public/markets/{market}/k-line`
  period 单位=分钟（1/5/15/60/240/1440），返回 [ts, open, high, low, close, volume]
  limit 最大 100/页，用 time_from/time_to 翻页。
"""
from __future__ import annotations

import time
from typing import Iterable, Sequence

# ---------------------------------------------------------------- k-line

def fetch_klines(client, market: str, period_min: int, bars: int) -> list[list]:
    """拉最近 bars 根 period_min 分钟 K 线（自动翻页，返回按时间升序）。"""
    out: list[list] = []
    now = int(time.time())
    to = now
    span = period_min * 60
    while len(out) < bars:
        want = min(100, bars - len(out))
        frm = to - span * want
        chunk = client._request("GET", f"/trade/public/markets/{market}/k-line",
                                params={"period": period_min, "time_from": frm,
                                        "time_to": to, "limit": want}) or []
        if not chunk:
            break
        chunk = [list(k) for k in chunk]
        out = chunk + out
        to = int(chunk[0][0]) - 1
        if len(out) >= bars or len(chunk) < want:
            break
    # 去重 + 升序
    dedup = {int(k[0]): k for k in out}
    return [dedup[t] for t in sorted(dedup)][-bars:]


# ---------------------------------------------------------------- 指标

def closes_of(klines: Sequence[Sequence]) -> list[float]:
    return [float(k[4]) for k in klines]


def ema(vals: Sequence[float], n: int) -> list[float]:
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = [float(vals[0])]
    for v in vals[1:]:
        out.append(out[-1] + k * (float(v) - out[-1]))
    return out


def rsi(vals: Sequence[float], n: int = 14) -> float | None:
    if len(vals) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def atr(klines: Sequence[Sequence], n: int = 14) -> float | None:
    if len(klines) < n + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def bollinger(vals: Sequence[float], n: int = 20, k: float = 2.0):
    if len(vals) < n:
        return None
    win = vals[-n:]
    mid = sum(win) / n
    var = sum((v - mid) ** 2 for v in win) / n
    sd = var ** 0.5
    return mid, mid + k * sd, mid - k * sd


def pct_change(vals: Sequence[float], back: int) -> float | None:
    if len(vals) <= back:
        return None
    base = vals[-1 - back]
    if not base:
        return None
    return (vals[-1] / base - 1.0) * 100.0


def slope_pct(series: Sequence[float], back: int) -> float | None:
    """序列末值相对 back 根之前的变化百分比。"""
    return pct_change(series, back)


def vol_ratio(klines: Sequence[Sequence], n: int = 20) -> float | None:
    vols = [float(k[5]) for k in klines]
    if len(vols) < n + 1:
        return None
    avg = sum(vols[-n - 1:-1]) / n
    return (vols[-1] / avg) if avg else None


# ---------------------------------------------------------------- 信号

BIAS_BASE = {"bull": 0.15, "neutral": 0.30, "bear": 0.60}  # 每日卖出「可卖量」的基准比例


def evaluate(klines_daily: Sequence[Sequence], klines_h4: Sequence[Sequence],
             bias: str = "neutral") -> dict:
    """把「用户定的几天级大方向」+ 指标 → 卖出强度比例（0~1）与理由。

    ratio = 基准(bias) + Σ 指标调整，clamp[0,1]。
    ratio 的含义：本轮把「可卖量」卖掉的比例（低频引擎再按单次上限截断）。
    """
    c = closes_of(klines_daily)
    if len(c) < 30:
        return {"error": "日线数据不足（<30 根），无法评估", "ratio": 0.0, "reasons": []}
    ema20 = ema(c, 20)
    ema50 = ema(c, 50) if len(c) >= 50 else ema(c, 20)
    price = c[-1]
    r = rsi(c, 14)
    a = atr(klines_daily, 14)
    bb = bollinger(c, 20, 2.0)
    e20_slope = slope_pct(ema20, 5) or 0.0
    vr = vol_ratio(klines_daily, 20)
    chg1 = pct_change(c, 1) or 0.0
    chg7 = pct_change(c, 7) or 0.0
    atr_pct = (a / price * 100.0) if a else 0.0

    ratio = BIAS_BASE.get(bias, 0.30)
    reasons: list[str] = [f"大方向={bias} → 基准卖出 {ratio*100:.0f}%"]

    def adj(pp: float, why: str):
        nonlocal ratio
        ratio += pp / 100.0
        reasons.append(f"{pp:+.0f}pp  {why}")

    # 1) 日线超买超卖
    if r is not None:
        if r >= 70:
            adj(+15, f"日线RSI={r:.1f} 超买 → 加速兑现")
        elif r >= 60:
            adj(+5, f"日线RSI={r:.1f} 偏强 → 小幅加速")
        elif r <= 35:
            adj(-15, f"日线RSI={r:.1f} 超卖 → 缓卖等反弹")
        elif r <= 45:
            adj(-5, f"日线RSI={r:.1f} 偏弱 → 缓卖")

    # 2) 布林带位置
    if bb:
        mid, up, low = bb
        if price >= up:
            adj(+15, f"价 {price:.4f} ≥ 布林上轨 {up:.4f} → 加速兑现")
        elif price <= low:
            adj(-15, f"价 {price:.4f} ≤ 布林下轨 {low:.4f} → 缓卖等回归")
        elif price > mid:
            adj(+5, f"价在中轨上方 → 略加速")

    # 3) 趋势（EMA20 斜率 / 价格对 EMA50）
    if e20_slope > 2:
        adj(-10, f"EMA20 5日斜率 +{e20_slope:.1f}% 向上 → 持有为主")
    elif e20_slope < -2:
        adj(+10, f"EMA20 5日斜率 {e20_slope:.1f}% 向下 → 加速卖出")
    if price < ema50[-1]:
        adj(+5, f"价在 EMA50({ema50[-1]:.4f}) 下方 → 偏空加速")

    # 4) 波动率（ATR%）：波动大时降低单次强度，避免砸盘
    if atr_pct > 8:
        adj(-10, f"日ATR={atr_pct:.1f}% 波动大 → 减量分片")
    elif atr_pct and atr_pct < 3:
        adj(+5, f"日ATR={atr_pct:.1f}% 波动小 → 可多卖一点")

    # 5) 量能
    if vr is not None and vr > 2:
        adj(+5, f"成交量 {vr:.1f}× 均量 → 流动性好，可多一些")

    ratio = max(0.0, min(1.0, ratio))
    ratio = round(ratio, 4)  # 抹掉浮点尾差，避免 0.049999… vs 0.05 的边界误判
    return {
        "bias": bias,
        "ratio": ratio,
        "price": price,
        "metrics": {
            "rsi14": None if r is None else round(r, 2),
            "ema20": round(ema20[-1], 4),
            "ema50": round(ema50[-1], 4),
            "ema20_slope5_pct": round(e20_slope, 2),
            "boll": None if not bb else [round(x, 4) for x in bb],
            "atr14": None if a is None else round(a, 4),
            "atr_pct": round(atr_pct, 2),
            "vol_ratio": None if vr is None else round(vr, 2),
            "chg_1d_pct": round(chg1, 2),
            "chg_7d_pct": round(chg7, 2),
            "bars_daily": len(c),
        },
        "reasons": reasons,
    }


def verdict(ratio: float) -> str:
    if ratio <= 0.02:
        return "持有（本轮不卖）"
    if ratio < 0.15:
        return "极慢卖（清一点零头）"
    if ratio < 0.35:
        return "慢卖"
    if ratio < 0.65:
        return "正常卖"
    if ratio < 0.9:
        return "加速卖"
    return "清仓式卖出（价高/趋势弱）"
