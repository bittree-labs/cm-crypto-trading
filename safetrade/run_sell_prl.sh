#!/bin/bash
# 策略1（简单版）：每天 12:00 卖 10 枚 PRL —— 只卖不买，永不一次卖光。
# 由 launchd 任务 com.bittree.safetrade.sellprl 触发（见同目录 launchd/*.plist）。
# 手动跑：
#   ./run_sell_prl.sh                # 真实卖出 10 枚（--live --amount 10 --slices 1）
#   ./run_sell_prl.sh --dry-run      # 只预览，不下单
#   ./run_sell_prl.sh --live --amount 5 --slices 1     # 临时改参数
set -uo pipefail

cd "$(dirname "$0")" || exit 1
mkdir -p logs

# 解释器：优先本模块自带 venv（含 curl_cffi，必须），否则退回 Hermes venv
PY="./venv/bin/python3"
if [ ! -x "${PY}" ]; then
  PY="/Users/bittree/.hermes/hermes-agent/venv/bin/python3"
fi
if ! "${PY}" -c "import curl_cffi" >/dev/null 2>&1; then
  echo "$(date '+%F %T') [FATAL] 解释器缺 curl_cffi，无法绕过 Cloudflare: ${PY}" >> logs/cron.log
  exit 3
fi

# 无参数 = 策略1 默认动作：真实卖出 10 枚
if [ "$#" -eq 0 ]; then
  set -- --live --amount 10 --slices 1
fi

echo "===== $(date '+%F %T') START  py=${PY}  args=$* =====" >> logs/cron.log
"${PY}" sell_prl.py "$@" >> logs/cron.log 2>&1
rc=$?
echo "----- $(date '+%F %T') END    exit=${rc}" >> logs/cron.log
exit "${rc}"
