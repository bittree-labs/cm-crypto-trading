#!/usr/bin/env python3
"""
AI3 搬砖套利 24小时自动监控
每分钟记录一次价差，累积数据后分析套利窗口
"""
import urllib.request, json, time, os, sys
from datetime import datetime

URL = "https://api.coingecko.com/api/v3/coins/autonomys-network/tickers"
OUTFILE = os.path.join(os.path.dirname(__file__), "ai3_arbitrage_log.jsonl")
TG_CONFIG = os.path.expanduser("~/.telegram_config")

def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "ArbBot/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())

def load_token():
    if os.path.exists(TG_CONFIG):
        with open(TG_CONFIG) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v

def send_telegram(text):
    load_token()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)

def find_arbitrage(tickers):
    pairs = []
    for t in tickers:
        price = t.get("converted_last", {}).get("usd")
        vol = t.get("converted_volume", {}).get("usd", 0) or 0
        if price and price > 0 and vol > 10:
            pairs.append({
                "exchange": t["market"]["name"],
                "price": float(price),
                "volume_24h": vol,
                "spread": t.get("bid_ask_spread_percentage")
            })
    if len(pairs) < 2:
        return None
    lowest = min(pairs, key=lambda x: x["price"])
    highest = max(pairs, key=lambda x: x["price"])
    diff_pct = (highest["price"] - lowest["price"]) / lowest["price"] * 100
    # 成本: 0.2%×2 手续费 + 0.5% 滑点 + $20 提币费
    capital = 5000
    costs = capital * 0.004 + 20 + capital * 0.005
    net = capital * diff_pct / 100 - costs
    return {
        "time": datetime.now().isoformat(),
        "low_ex": lowest["exchange"], "low_price": round(lowest["price"], 8), "low_vol": lowest["volume_24h"],
        "high_ex": highest["exchange"], "high_price": round(highest["price"], 8), "high_vol": highest["volume_24h"],
        "diff_pct": round(diff_pct, 2),
        "net_profit": round(net, 2),
        "exchanges": len(pairs)
    }

print("🔄 AI3 搬砖套利监控启动 — 每分钟记录一次")
print(f"   日志: {OUTFILE}")
print(f"   停止: Ctrl+C\n")

count = 0
best_ever = None

while True:
    try:
        tickers = fetch().get("tickers", [])
        r = find_arbitrage(tickers)
        count += 1

        if r:
            with open(OUTFILE, "a") as f:
                f.write(json.dumps(r) + "\n")

            profit = r["net_profit"]
            icon = "✅" if profit > 100 else "⚠️" if profit > 0 else "❌"
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] #{count} {icon} {r['low_ex']}→{r['high_ex']} "
                  f"价差{r['diff_pct']:.1f}% 净利${profit:+,.0f}")

            # 如果利润超过历史最佳，发Telegram
            if best_ever is None or profit > best_ever["net_profit"]:
                best_ever = r
                if profit > 200:
                    send_telegram(
                        f"🚀 <b>AI3 套利机会!</b>\n\n"
                        f"买入: {r['low_ex']} @ ${r['low_price']:.6f}\n"
                        f"卖出: {r['high_ex']} @ ${r['high_price']:.6f}\n"
                        f"价差: {r['diff_pct']:.1f}%\n"
                        f"预计净利: <b>${profit:+,.0f}</b>"
                    )
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] #{count} ⏳ 数据不足")

        time.sleep(60)
    except KeyboardInterrupt:
        print(f"\n已停止。共记录 {count} 次。")
        break
    except Exception as e:
        print(f"[!] 错误: {e}，30秒后重试...")
        time.sleep(30)
