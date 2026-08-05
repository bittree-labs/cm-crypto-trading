#!/usr/bin/env python3
"""
AI3 跨交易所套利监控 — UTC+8 整点汇报
每整点拉一次 CoinGecko tickers，记录价差并输出汇报。
"""
import urllib.request, json, time, os
from datetime import datetime, timezone, timedelta

URL = "https://api.coingecko.com/api/v3/coins/autonomys-network/tickers"
LOGFILE = os.path.join(os.path.dirname(__file__), "ai3_arbitrage_log.jsonl")
TZ = timezone(timedelta(hours=8))  # UTC+8

def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "ArbBot/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())

def run():
    tickers = fetch().get("tickers", [])
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*70}")
    print(f"📊 AI3 套利监控汇报 — {now} (UTC+8)")
    print(f"{'='*70}")

    # 交易所对比
    exchanges = {}
    for t in tickers:
        name = t["market"]["name"]
        price = t.get("converted_last", {}).get("usd")
        vol = t.get("converted_volume", {}).get("usd", 0) or 0
        spread = t.get("bid_ask_spread_percentage")
        if price and price > 0:
            exchanges[name] = {"price": float(price), "vol": vol, "spread": spread}

    print(f"交易所        价格          24h成交量     价差%")
    print("-" * 50)
    for name, d in sorted(exchanges.items(), key=lambda x: x[1]["price"]):
        ss = f"{d['spread']:.2f}%" if isinstance(d['spread'], (int, float)) else "?"
        print(f"{name:<14s} ${d['price']:.8f}  ${d['vol']:>12,.0f}  {ss:>8s}")

    # 套利计算
    if len(exchanges) >= 2:
        sorted_ex = sorted(exchanges.items(), key=lambda x: x[1]["price"])
        low_name, low_d = sorted_ex[0]
        high_name, high_d = sorted_ex[-1]
        diff_pct = (high_d["price"] - low_d["price"]) / low_d["price"] * 100

        capital = 5000
        costs = capital * 0.004 + 20 + capital * 0.005  # 手续费+提币+滑点
        net = capital * diff_pct / 100 - costs

        print(f"\n📈 套利路径: {low_name} → {high_name}")
        print(f"   买入价: ${low_d['price']:.8f}  卖出价: ${high_d['price']:.8f}")
        print(f"   价差:   {diff_pct:.2f}%")
        print(f"   投入$5,000 → 毛利${capital*diff_pct/100:,.2f} → 净利${net:+,.2f}")

        if net > 200:
            print(f"   🔥 利润可观！")
        elif net > 0:
            print(f"   ⚠️ 微利空间")
        else:
            print(f"   ❌ 无套利空间")

        # 记录
        record = {
            "time": now,
            "low_ex": low_name, "low_price": low_d["price"], "low_vol": low_d["vol"],
            "high_ex": high_name, "high_price": high_d["price"], "high_vol": high_d["vol"],
            "diff_pct": round(diff_pct, 2),
            "net_profit": round(net, 2)
        }
        with open(LOGFILE, "a") as f:
            f.write(json.dumps(record) + "\n")

    else:
        print("\n⚠️ 有效交易所不足2个")

# ── 定时调度 ──────────────────────────────────

def next_hour():
    """计算到下一个整点的秒数"""
    now = datetime.now(TZ)
    next_h = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (next_h - now).total_seconds()

if __name__ == "__main__":
    # 立即执行一次
    run()

    # 然后每小时整点执行
    print(f"\n⏱ 下次汇报: 下一个整点 (UTC+8)")
    while True:
        wait = next_hour()
        time.sleep(wait)
        run()
