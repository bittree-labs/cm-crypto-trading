"""AI3 深度验证"""
import urllib.request, json, locale

def api(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ArbBot/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())

def fmt(n):
    """格式化大数字"""
    if abs(n) >= 1e9:
        return "$%.2fB" % (n/1e9)
    if abs(n) >= 1e6:
        return "$%.2fM" % (n/1e6)
    if abs(n) >= 1e3:
        return "$%.0fK" % (n/1e3)
    return "$%.0f" % n

print("=" * 60)
print("AI3 跨交易所深度验证")
print("=" * 60)

tickers = api("https://api.coingecko.com/api/v3/coins/autonomys-network/tickers")["tickers"]
markets = {}
for t in tickers:
    name = t["market"]["name"]
    if name not in markets:
        markets[name] = {
            "price": t.get("converted_last", {}).get("usd"),
            "vol": t.get("converted_volume", {}).get("usd", 0) or 0,
            "pair": t["base"] + "/" + t["target"],
        }

for name, d in sorted(markets.items(), key=lambda x: x[1]["price"] or 0):
    p = d["price"]
    ps = "$%.8f" % p if p else "?"
    vs = fmt(d["vol"])
    print("%-14s %-12s %12s %12s" % (name, d["pair"], ps, vs))

prices = [(n, d["price"], d["vol"]) for n, d in markets.items() if d["price"] and d["price"] > 0]
prices.sort(key=lambda x: x[1])
if len(prices) >= 2:
    low, high = prices[0], prices[-1]
    diff = (high[1] - low[1]) / low[1] * 100
    print("\n套利路径: %s -> %s" % (low[0], high[0]))
    print("  买入: $%.8f  (24h量: %s)" % (low[1], fmt(low[2])))
    print("  卖出: $%.8f  (24h量: %s)" % (high[1], fmt(high[2])))
    print("  价差: %.2f%%" % diff)
    print()
    for cap in [1000, 5000, 10000]:
        costs = cap * 0.004 + 20 + cap * 0.005
        net = cap * diff / 100 - costs
        print("  投入%s: 毛利%s -> 净利%s (%.1f%%)" % (fmt(cap), fmt(cap*diff/100), "+"+fmt(net) if net>=0 else fmt(net), net/cap*100))
    print()
    print("关键验证项:")
    print("  1. %s上AI3合约地址与%s是否一致？" % (low[0], high[0]))
    print("  2. %s充提状态？" % low[0])
    print("  3. %s的%s深度能否支撑大额成交？" % (low[0], fmt(low[2])))
