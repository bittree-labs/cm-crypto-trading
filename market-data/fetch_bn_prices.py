#!/usr/bin/env python3
"""拉取 BN 所有 USDT 永续合约的当前价格，按字母序多列展示并保存"""
import json, urllib.request, os, sys

WORKSPACE = '/Users/bittree'

# ── 1. 获取所有价格 ──
print('🔍 正在从 BN 获取所有 USDT 永续合约价格...')
req = urllib.request.Request(
    'https://fapi.binance.com/fapi/v1/ticker/price',
    headers={'User-Agent': 'Mozilla/5.0'}
)
with urllib.request.urlopen(req, timeout=30) as resp:
    all_tickers = json.loads(resp.read())

# 过滤 USDT 永续合约
usdt_prices = {}
for t in all_tickers:
    sym = t['symbol']
    if sym.endswith('USDT'):
        # 排除非永续的（如 USDCUSDT 现货对在合约接口中可能存在，不过合约接口只返回合约）
        usdt_prices[sym] = t['price']

# 按字母序排序
sorted_symbols = sorted(usdt_prices.keys())
total = len(sorted_symbols)
print(f'✅ 获取到 {total} 个交易对价格\n')

# ── 2. 多列展示 ──
# 格式: SYMBOL       PRICE
# 先用紧凑多列
cols = 4
col_width = 26

print(f'{"═" * (cols * col_width)}')
print(f'  BN USDT 永续合约实时价格 — 共 {total} 个')
print(f'{"═" * (cols * col_width)}\n')

for i, sym in enumerate(sorted_symbols):
    price = usdt_prices[sym]
    try:
        pf = float(price)
        if pf >= 1000:
            price_str = f'{pf:,.2f}'
        elif pf >= 1:
            price_str = f'{pf:,.4f}'
        elif pf >= 0.01:
            price_str = f'{pf:.6f}'
        else:
            price_str = f'{pf:.8f}'
    except:
        price_str = price

    cell = f'{sym:<14} ${price_str}'
    end = '\n' if (i + 1) % cols == 0 else '  '
    print(f'  {cell:<{col_width-2}}', end=end)

if total % cols != 0:
    print()
print()

# ── 3. 保存文件 ──

# 纯文本（格式化列表）
txt_path = os.path.join(WORKSPACE, 'bn_usdt_perpetual_prices.txt')
with open(txt_path, 'w') as f:
    f.write(f'BN (Binance) USDT 永续合约实时价格 — {total} 个交易对\n')
    f.write(f'更新时间: 2026-08-01\n')
    f.write('=' * 70 + '\n\n')
    for sym in sorted_symbols:
        price = usdt_prices[sym]
        try:
            pf = float(price)
            if pf >= 1000:
                price_str = f'${pf:,.2f}'
            elif pf >= 1:
                price_str = f'${pf:,.4f}'
            elif pf >= 0.01:
                price_str = f'${pf:.6f}'
            else:
                price_str = f'${pf:.8f}'
        except:
            price_str = f'${price}'
        f.write(f'{sym:<18} {price_str}\n')
print(f'✅ 格式化价格列表已保存: {txt_path}')

# JSON（含价格）
json_path = os.path.join(WORKSPACE, 'bn_usdt_perpetual_prices.json')
with open(json_path, 'w') as f:
    json.dump({
        'exchange': 'Binance (BN)',
        'type': 'USDT Perpetual',
        'total': total,
        'updated': '2026-08-01',
        'prices': {sym: usdt_prices[sym] for sym in sorted_symbols},
    }, f, ensure_ascii=False, indent=2)
print(f'✅ JSON 已保存: {json_path}')
