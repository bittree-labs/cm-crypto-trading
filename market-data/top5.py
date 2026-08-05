#!/usr/bin/env python3
"""BN USDT 永续合约涨幅前5"""
import json, urllib.request

API = 'https://fapi.binance.com/fapi/v1/ticker/24hr'
print('🔍 获取 BN 全量 24hr 数据...')
req = urllib.request.Request(API, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=30) as resp:
    tickers = json.loads(resp.read())

# 过滤 USDT 永续
data = []
for t in tickers:
    sym = t['symbol']
    if not sym.endswith('USDT'):
        continue
    try:
        pct = float(t['priceChangePercent'])
        last = float(t['lastPrice'])
        high = float(t['highPrice'])
        low = float(t['lowPrice'])
        vol = float(t['quoteVolume'])
    except:
        continue
    data.append({'symbol': sym, 'pct': pct, 'last': last, 'high': high, 'low': low, 'vol': vol})

# 按涨幅排序
data.sort(key=lambda x: x['pct'], reverse=True)

# NAD 是异常币（可能是下架或数据异常），过滤掉涨幅/跌幅超过 1000% 的
data = [d for d in data if abs(d['pct']) < 1000]

print(f'\n{"═" * 70}')
print(f'  🔥 BN USDT 永续合约 — 涨幅前 5')
print(f'{"═" * 70}')
print(f'  {"排名":<4} {"合约":<18} {"涨幅":>10} {"最新价":>14} {"24h高":>12} {"24h低":>12} {"成交量(USDT)":>16}')
print(f'  {"─" * 4} {"─" * 18} {"─" * 10} {"─" * 14} {"─" * 12} {"─" * 12} {"─" * 16}')

for i, d in enumerate(data[:5]):
    sym = d['symbol']
    arrow = '📈' if d['pct'] > 0 else '📉'
    price = f"${d['last']:,.4f}" if d['last'] < 1000 else f"${d['last']:,.2f}"
    print(f'  {i+1:<4} {sym:<18} {arrow} {d["pct"]:>+8.2f}% {price:>14} ${d["high"]:>11.4f} ${d["low"]:>11.4f} ${d["vol"]:>14,.0f}')

print(f'\n{"═" * 70}')
print(f'  ❄️ 跌幅前 5')
print(f'{"═" * 70}')
print(f'  {"排名":<4} {"合约":<18} {"涨幅":>10} {"最新价":>14} {"24h高":>12} {"24h低":>12} {"成交量(USDT)":>16}')
print(f'  {"─" * 4} {"─" * 18} {"─" * 10} {"─" * 14} {"─" * 12} {"─" * 12} {"─" * 16}')

for i, d in enumerate(data[-5:]):
    sym = d['symbol']
    arrow = '📈' if d['pct'] > 0 else '📉'
    price = f"${d['last']:,.4f}" if d['last'] < 1000 else f"${d['last']:,.2f}"
    print(f'  {i+1:<4} {sym:<18} {arrow} {d["pct"]:>+8.2f}% {price:>14} ${d["high"]:>11.4f} ${d["low"]:>11.4f} ${d["vol"]:>14,.0f}')

# 保存
out = {
    'updated': '2026-08-01',
    'top5_gainers': [{'rank': i+1, **d} for i, d in enumerate(data[:5])],
    'top5_losers': [{'rank': i+1, **d} for i, d in enumerate(data[-5:])],
}
with open('/Users/bittree/workspace/codewhale/trade/bn_top5_24hr.json', 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f'\n✅ 已保存: /Users/bittree/workspace/codewhale/trade/bn_top5_24hr.json')
