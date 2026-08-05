#!/usr/bin/env python3
"""拉取 BN (Binance) 所有 USDT 永续合约交易对，按字母排序展示并保存"""
import json, urllib.request, os

WORKSPACE = '/Users/bittree'
API = 'https://fapi.binance.com/fapi/v1/exchangeInfo'

print('🔍 正在从 BN (Binance Futures) 获取交易对...')
req = urllib.request.Request(API, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as resp:
    data = json.loads(resp.read())

pairs = []
for s in data['symbols']:
    if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING':
        pairs.append({
            'symbol': s['symbol'],
            'base': s['baseAsset'],
            'price_precision': s['pricePrecision'],
            'qty_precision': s['quantityPrecision'],
        })

pairs.sort(key=lambda x: x['symbol'])
total = len(pairs)

# ── 格式化展示 ──
print(f'\n═══════════════════════════════════════════════════════════')
print(f'  BN (Binance) USDT 永续合约交易对 — 共 {total} 个')
print(f'═══════════════════════════════════════════════════════════\n')

# 计算列宽，自动适配终端
cols = 6
for i, p in enumerate(pairs):
    end = '\n' if (i + 1) % cols == 0 else '  '
    print(f'{p["symbol"]:<14}', end=end)
if total % cols != 0:
    print()

print(f'\n{"─" * 60}')
print(f'合计: {total} 个 USDT 永续合约')
print(f'数据来源: {API}')
print(f'{"─" * 60}')

# ── 保存 JSON（含详细信息）──
json_path = os.path.join(WORKSPACE, 'bn_usdt_perpetual_pairs.json')
with open(json_path, 'w') as f:
    json.dump({
        'exchange': 'Binance (BN)',
        'type': 'USDT Perpetual',
        'total': total,
        'updated': '2026-08-01',
        'pairs': pairs,
    }, f, ensure_ascii=False, indent=2)
print(f'\n✅ 详细信息已保存: {json_path}')

# ── 保存纯符号列表（每行一个）──
txt_path = os.path.join(WORKSPACE, 'bn_usdt_perpetual_symbols.txt')
with open(txt_path, 'w') as f:
    for p in pairs:
        f.write(p['symbol'] + '\n')
print(f'✅ 纯符号列表已保存: {txt_path}')

# ── 按 base 资产统计 ──
bases = {}
for p in pairs:
    bases[p['base']] = bases.get(p['base'], 0) + 1
multi_base = [(b, c) for b, c in bases.items() if c > 1]
if multi_base:
    print(f'\n⚠️  以下 base 资产有多个合约变体:')
    for b, c in sorted(multi_base, key=lambda x: -x[1]):
        variants = [p['symbol'] for p in pairs if p['base'] == b]
        print(f'  {b}: {c} 个 — {", ".join(variants)}')
