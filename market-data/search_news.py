#!/usr/bin/env python3
"""在 BlockBeats 快讯中搜索关键词"""
import re, json, sys

with open('/tmp/blockbeats2.html') as f:
    html = f.read()

# 提取 NUXT
start = html.index('window.__NUXT__=')
depth = 0; end = start; instr = False; esc = False
for i in range(start, len(html)):
    c = html[i]
    if esc: esc = False; continue
    if c == '\\': esc = True; continue
    if c in "\"'":
        if not instr: instr = c
        elif instr == c: instr = False
        continue
    if instr: continue
    if c in '({[': depth += 1
    if c in ')}]':
        depth -= 1
        if depth == 0: end = i+1; break

expr = html[start+len('window.__NUXT__='):end]
try:
    data = eval(expr)
except:
    data = eval('(' + expr + ')')

def find_news(obj, depth=0, maxd=10):
    items = []
    if not obj or isinstance(obj, str) or depth > maxd: return items
    if isinstance(obj, list):
        for v in obj: items.extend(find_news(v, depth+1))
    elif isinstance(obj, dict):
        if 'title' in obj and 'content' in obj: items.append(obj)
        for v in obj.values(): items.extend(find_news(v, depth+1))
    return items

news = find_news(data)
print(f'共 {len(news)} 条快讯\n')

# 搜索关键词
kw = sys.argv[1] if len(sys.argv) > 1 else 'RATS'
kw_upper = kw.upper()
found = 0
for item in news:
    title = item.get('title','')
    content = item.get('content','')
    text = (title + ' ' + content).upper()
    if kw_upper in text:
        found += 1
        c = re.sub(r'<[^>]+>', '', content)
        c = re.sub(r'https?://\S+', '', c)
        c = re.sub(r'\s+', ' ', c).strip()
        print(f'🔔 [{found}] {title}')
        print(f'   {c[:400]}')
        print()

if found == 0:
    print(f'❌ 未找到 "{kw}" 相关快讯')
    # 列出所有快讯标题供参考
    print('\n最近快讯标题:')
    for i, item in enumerate(news[:20]):
        print(f'  {i+1}. {item["title"][:100]}')
