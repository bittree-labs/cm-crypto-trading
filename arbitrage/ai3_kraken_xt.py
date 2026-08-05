"""AI3 Kraken <-> XT 套利监控"""
import urllib.request, json, time

def api(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ArbBot/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())

def send_tg(text):
    token = "8964342406:AAEcWUH8ChXsAPGvS_PuIw9_EtEIKVPO2vI"
    chat = "478575303"
    body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)

tickers = api("https://api.coingecko.com/api/v3/coins/autonomys-network/tickers")["tickers"]

kraken = xt = None
for t in tickers:
    n = t["market"]["name"]
    p = t.get("converted_last", {}).get("usd")
    v = t.get("converted_volume", {}).get("usd", 0) or 0
    s = t.get("bid_ask_spread_percentage")
    if n == "Kraken" and t["target"] == "USD":
        kraken = {"price": float(p), "vol": v, "spread": s}
    if n == "XT.COM" and t["target"] == "USDT":
        xt = {"price": float(p), "vol": v, "spread": s}

print("=" * 55)
print("AI3 Kraken vs XT 套利分析")
print("=" * 55)

if kraken and xt:
    # 双向分析
    xt_buy = (kraken["price"] - xt["price"]) / xt["price"] * 100
    kr_buy = (xt["price"] - kraken["price"]) / kraken["price"] * 100

    print("")
    print("交易所      价格          24h量      价差%(bid-ask)")
    print("-" * 55)
    print("%-12s $%.8f  $%8.0fK     %.1f%%" % ("Kraken", kraken["price"], kraken["vol"]/1e3, kraken["spread"] or 0))
    print("%-12s $%.8f  $%8.0fK     %.1f%%" % ("XT.COM", xt["price"], xt["vol"]/1e3, xt["spread"] or 0))

    # 方向1：XT买 -> Kraken卖
    print("\n--- 方向1: XT买入 -> Kraken卖出 ---")
    print("  价差: %+.2f%%" % xt_buy)
    for cap in [1000, 3000, 5000]:
        fee = cap * 0.003 * 2  # XT 0.3% + Kraken 0.3%
        gas = 20  # 提币/网络费
        net = cap * xt_buy / 100 - fee - gas
        roi = net / cap * 100
        tag = "  <-- 可行!" if net > 30 else ""
        print("  $%d: 毛利$%d - 费用$%d = 净利$%d (%.1f%%)%s" % (cap, cap*xt_buy/100, fee+gas, net, roi, tag))

    # 方向2：Kraken买 -> XT卖
    print("\n--- 方向2: Kraken买入 -> XT卖出 ---")
    print("  价差: %+.2f%%" % kr_buy)
    for cap in [1000, 3000, 5000]:
        fee = cap * 0.003 * 2
        gas = 20
        net = cap * kr_buy / 100 - fee - gas
        roi = net / cap * 100
        tag = "  <-- 可行!" if net > 30 else ""
        print("  $%d: 毛利$%d - 费用$%d = 净利$%d (%.1f%%)%s" % (cap, cap*kr_buy/100, fee+gas, net, roi, tag))

    # 如果有利可图，发Telegram
    best_net = max(
        (5000 * xt_buy / 100 - 5000*0.006 - 20) if xt_buy > 0 else -1,
        (5000 * kr_buy / 100 - 5000*0.006 - 20) if kr_buy > 0 else -1,
    )
    if best_net > 50:
        msg = (
            "AI3 套利机会!\n\n"
            "Kraken: $%.6f\nXT: $%.6f\n"
            "价差: %.1f%%\n"
            "预计净利: $%d ($5K投入)"
        ) % (kraken["price"], xt["price"], max(xt_buy, kr_buy), best_net)
        send_tg(msg)
        print("\nTelegram 已推送!")
