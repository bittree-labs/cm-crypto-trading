#!/bin/bash
# 加密探长 - 单次采集脚本
DATA_DIR="/Users/bittree/workspace/crypto-watchdog/data"
mkdir -p "$DATA_DIR"
TMPDIR="${DATA_DIR}/.tmp"
mkdir -p "$TMPDIR"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
DATE_HOUR=$(date '+%Y-%m-%d_%H')
REPORT_FILE="$DATA_DIR/report_${DATE_HOUR}.md"
SUMMARY_FILE="$DATA_DIR/hourly_summary.md"

# 1. CoinGecko 价格 — 存入临时文件，避免 pipe 被消费
CG_FILE="${TMPDIR}/coingecko.json"
curl -s --max-time 15 \
  "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,binancecoin,solana&vs_currencies=usd&include_24hr_change=true&include_market_cap=true" \
  > "$CG_FILE" 2>/dev/null

btc_p=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('bitcoin',{}).get('usd','N/A'))" 2>/dev/null)
btc_c=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('bitcoin',{}).get('usd_24h_change',0),2))" 2>/dev/null)
eth_p=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('ethereum',{}).get('usd','N/A'))" 2>/dev/null)
eth_c=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('ethereum',{}).get('usd_24h_change',0),2))" 2>/dev/null)
bnb_p=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('binancecoin',{}).get('usd','N/A'))" 2>/dev/null)
bnb_c=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('binancecoin',{}).get('usd_24h_change',0),2))" 2>/dev/null)
sol_p=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('solana',{}).get('usd','N/A'))" 2>/dev/null)
sol_c=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('solana',{}).get('usd_24h_change',0),2))" 2>/dev/null)

# 2. Trending
TREND=$(curl -s --max-time 15 "https://api.coingecko.com/api/v3/search/trending" 2>/dev/null)
TREND_COINS=$(echo "$TREND" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for c in d.get('coins', [])[:7]:
    i = c.get('item', {})
    print(f\"  - {i.get('name','?')} ({i.get('symbol','?').upper()}) | 市值排名: #{i.get('market_cap_rank','?')}\")
" 2>/dev/null)

# 3. Fear & Greed
FG=$(curl -s --max-time 10 "https://api.alternative.me/fng/?limit=1" 2>/dev/null)
FG_VAL=$(echo "$FG" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',[{}])[0].get('value','N/A'))" 2>/dev/null)
FG_CLS=$(echo "$FG" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',[{}])[0].get('value_classification','N/A'))" 2>/dev/null)

# 4. 生成报告
printf '# 🔍 加密探长 · 小时汇报\n\n**采集时间**: %s (北京时间)\n\n---\n\n## 💰 主流币种行情 (USD)\n\n| 币种 | 价格 | 24h涨跌 |\n|------|------|---------|\n' "$TIMESTAMP" > "$REPORT_FILE"

[ -n "$btc_p" ] && [ "$btc_p" != "N/A" ] && printf "| **BTC**  | \$%'.0f | %s%% |\n" "$btc_p" "$btc_c" >> "$REPORT_FILE" || echo "| **BTC** | N/A | N/A |" >> "$REPORT_FILE"
[ -n "$eth_p" ] && [ "$eth_p" != "N/A" ] && printf "| **ETH**  | \$%'.0f | %s%% |\n" "$eth_p" "$eth_c" >> "$REPORT_FILE" || echo "| **ETH** | N/A | N/A |" >> "$REPORT_FILE"
[ -n "$bnb_p" ] && [ "$bnb_p" != "N/A" ] && printf "| **BNB**  | \$%'.0f | %s%% |\n" "$bnb_p" "$bnb_c" >> "$REPORT_FILE" || echo "| **BNB** | N/A | N/A |" >> "$REPORT_FILE"
[ -n "$sol_p" ] && [ "$sol_p" != "N/A" ] && printf "| **SOL**  | \$%'.0f | %s%% |\n" "$sol_p" "$sol_c" >> "$REPORT_FILE" || echo "| **SOL** | N/A | N/A |" >> "$REPORT_FILE"

printf '\n---\n\n## 😱 恐惧贪婪指数\n\n- **当前值**: %s / 100\n- **状态**: %s\n\n---\n\n## 🔥 CoinGecko 热门趋势币种\n\n%s\n\n---\n\n> ⚠️ 本报告由加密探长自动生成。数据来源: CoinGecko, Alternative.me。\n> 不构成投资建议。下次汇报约在1小时后。\n' "$FG_VAL" "$FG_CLS" "$TREND_COINS" >> "$REPORT_FILE"

# 5. 追加汇总
printf '\n---\n\n## %s\n\n- BTC: $%s (%s%%)\n- ETH: $%s (%s%%)\n- BNB: $%s (%s%%)\n- SOL: $%s (%s%%)\n- 恐惧贪婪: %s (%s)\n' "$TIMESTAMP" "${btc_p:-N/A}" "${btc_c:-N/A}" "${eth_p:-N/A}" "${eth_c:-N/A}" "${bnb_p:-N/A}" "${bnb_c:-N/A}" "${sol_p:-N/A}" "${sol_c:-N/A}" "${FG_VAL:-N/A}" "${FG_CLS:-N/A}" >> "$SUMMARY_FILE"

echo "✅ 报告已保存: $REPORT_FILE"
cat "$REPORT_FILE"
