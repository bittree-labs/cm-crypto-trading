#!/bin/bash
# 加密探长 - 每小时加密货币监控脚本
# 默认间隔3600秒，可通过参数覆盖

INTERVAL=${1:-3600}
DATA_DIR="/Users/bittree/workspace/crypto-watchdog/data"
TMPDIR="${DATA_DIR}/.tmp"
mkdir -p "$DATA_DIR" "$TMPDIR"

echo "[加密探长] 启动于 $(date '+%Y-%m-%d %H:%M:%S')，间隔=${INTERVAL}秒"

while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    DATE_HOUR=$(date '+%Y-%m-%d_%H')
    REPORT_FILE="$DATA_DIR/report_${DATE_HOUR}.md"
    SUMMARY_FILE="$DATA_DIR/hourly_summary.md"
    CG_FILE="${TMPDIR}/coingecko.json"
    TREND_FILE="${TMPDIR}/trending.json"
    FG_FILE="${TMPDIR}/feargreed.json"

    echo "========================================"
    echo "[加密探长] 开始采集 @ $TIMESTAMP"
    echo "========================================"

    # ========== 1. 主流币种价格 (CoinGecko 免费 API) ==========
    curl -s --max-time 15 \
      "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,binancecoin,solana&vs_currencies=usd&include_24hr_change=true&include_market_cap=true" \
      > "$CG_FILE" 2>/dev/null

    BTC_PRICE=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('bitcoin',{}).get('usd','N/A'))" 2>/dev/null)
    BTC_CHG=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('bitcoin',{}).get('usd_24h_change',0),2))" 2>/dev/null)
    ETH_PRICE=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('ethereum',{}).get('usd','N/A'))" 2>/dev/null)
    ETH_CHG=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('ethereum',{}).get('usd_24h_change',0),2))" 2>/dev/null)
    BNB_PRICE=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('binancecoin',{}).get('usd','N/A'))" 2>/dev/null)
    BNB_CHG=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('binancecoin',{}).get('usd_24h_change',0),2))" 2>/dev/null)
    SOL_PRICE=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('solana',{}).get('usd','N/A'))" 2>/dev/null)
    SOL_CHG=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(round(d.get('solana',{}).get('usd_24h_change',0),2))" 2>/dev/null)

    BTC_MCAP=$(python3 -c "import json; d=json.load(open('$CG_FILE')); print(d.get('bitcoin',{}).get('usd_market_cap','N/A'))" 2>/dev/null)

    # ========== 2. CoinGecko Trending (save to file) ==========
    curl -s --max-time 15 "https://api.coingecko.com/api/v3/search/trending" > "$TREND_FILE" 2>/dev/null
    TRENDING_COINS=$(python3 -c "
import json
d = json.load(open('${TREND_FILE}'))
coins = d.get('coins', [])[:7]
for c in coins:
    item = c.get('item', {})
    name = item.get('name', '?')
    symbol = item.get('symbol', '?')
    rank = item.get('market_cap_rank', '?')
    print(f'  - {name} ({symbol.upper()}) | 市值排名: #{rank}')
" 2>/dev/null)

    # ========== 3. Fear & Greed (save to file) ==========
    curl -s --max-time 10 "https://api.alternative.me/fng/?limit=1" > "$FG_FILE" 2>/dev/null
    FG_VALUE=$(python3 -c "import json; d=json.load(open('${FG_FILE}')); print(d.get('data',[{}])[0].get('value','N/A'))" 2>/dev/null)
    FG_CLASS=$(python3 -c "import json; d=json.load(open('${FG_FILE}')); print(d.get('data',[{}])[0].get('value_classification','N/A'))" 2>/dev/null)

    # ========== 4. 生成报告 ==========
    {
        echo "# 🔍 加密探长 · 小时汇报"
        echo ""
        echo "**采集时间**: $TIMESTAMP (北京时间)"
        echo ""
        echo "---"
        echo ""
        echo "## 💰 主流币种行情 (USD)"
        echo ""
        echo "| 币种 | 价格 | 24h涨跌 |"
        echo "|------|------|---------|"
        if [ "$BTC_PRICE" != "N/A" ] && [ -n "$BTC_PRICE" ]; then
            printf "| **BTC**  | \$%'.0f | %.1f%% |\n" "$BTC_PRICE" "$BTC_CHG"
        else
            echo "| **BTC** | N/A | N/A |"
        fi
        if [ "$ETH_PRICE" != "N/A" ] && [ -n "$ETH_PRICE" ]; then
            printf "| **ETH**  | \$%'.0f | %.1f%% |\n" "$ETH_PRICE" "$ETH_CHG"
        else
            echo "| **ETH** | N/A | N/A |"
        fi
        if [ "$BNB_PRICE" != "N/A" ] && [ -n "$BNB_PRICE" ]; then
            printf "| **BNB**  | \$%'.0f | %.1f%% |\n" "$BNB_PRICE" "$BNB_CHG"
        else
            echo "| **BNB** | N/A | N/A |"
        fi
        if [ "$SOL_PRICE" != "N/A" ] && [ -n "$SOL_PRICE" ]; then
            printf "| **SOL**  | \$%'.0f | %.1f%% |\n" "$SOL_PRICE" "$SOL_CHG"
        else
            echo "| **SOL** | N/A | N/A |"
        fi
        echo ""
        echo "---"
        echo ""
        echo "## 😱 恐惧贪婪指数"
        echo ""
        echo "- **当前值**: $FG_VALUE / 100"
        echo "- **状态**: $FG_CLASS"
        echo ""
        echo "---"
        echo ""
        echo "## 🔥 CoinGecko 热门趋势币种"
        echo ""
        if [ -n "$TRENDING_COINS" ]; then
            echo "$TRENDING_COINS"
        else
            echo "> 数据获取失败，将在下次重试"
        fi
        echo ""
        echo "---"
        echo ""
        echo "> ⚠️ 本报告由加密探长自动生成。数据来源: CoinGecko, Alternative.me。"
        echo "> 不构成投资建议。下次汇报约在1小时后。"

    } > "$REPORT_FILE"

    # ========== 5. 追加到汇总日志 ==========
    {
        echo ""
        echo "---"
        echo ""
        echo "## $TIMESTAMP"
        echo ""
        echo "- BTC: \$${BTC_PRICE:-N/A} (${BTC_CHG:-N/A}%)"
        echo "- ETH: \$${ETH_PRICE:-N/A} (${ETH_CHG:-N/A}%)"
        echo "- BNB: \$${BNB_PRICE:-N/A} (${BNB_CHG:-N/A}%)"
        echo "- SOL: \$${SOL_PRICE:-N/A} (${SOL_CHG:-N/A}%)"
        echo "- 恐惧贪婪: ${FG_VALUE:-N/A} (${FG_CLASS:-N/A})"
        echo "- 热门: $(echo "$TRENDING_COINS" | head -3 | tr '\n' ' ')"
        echo ""
    } >> "$SUMMARY_FILE"

    echo "[加密探长] 报告已保存: $REPORT_FILE"
    echo "[加密探长] 下次采集: $(date -v+${INTERVAL}S '+%Y-%m-%d %H:%M:%S')"
    echo ""

    sleep "$INTERVAL"
done
