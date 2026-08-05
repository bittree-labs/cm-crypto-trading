#!/usr/bin/env python3
import json
with open('/Users/bittree/bn_usdt_perpetual_prices.json') as f:
    data = json.load(f)
with open('/Users/bittree/bn_usdt_perpetual_prices.html') as f:
    html = f.read()
html = html.replace('__PRICES__', json.dumps(data['prices']))
with open('/Users/bittree/bn_usdt_perpetual_prices.html', 'w') as f:
    f.write(html)
print('done')
