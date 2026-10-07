#!/bin/bash
# 低频量化卖币引擎（指标触发）—— 每 4h 检查一次，是否动手由指标决定。
# 由 launchd 任务 com.bittree.safetrade.sellauto 触发（StartInterval 14400s）。
#
# 当前为**影子模式**（--dry-run）：只记录决策不下单。观察几天后确认阈值，再切真实执行：
#   把下面 ARGS 里的 --dry-run 删掉即可，然后重启任务。
set -uo pipefail

cd "$(dirname "$0")" || exit 1
mkdir -p logs

PY="./venv/bin/python3"
if [ ! -x "${PY}" ]; then
  PY="/Users/bittree/.hermes/hermes-agent/venv/bin/python3"
fi
if ! "${PY}" -c "import curl_cffi" >/dev/null 2>&1; then
  echo "$(date '+%F %T') [FATAL] 解释器缺 curl_cffi: ${PY}" >> logs/auto.log
  exit 3
fi

# 方向读 bias.json（用户定）；策略参数在此处调
ARGS="--dry-run --reserve 0 --max-per-run 20 --slices 2 --min-hours 4 --mode limit"

if [ "$#" -gt 0 ]; then
  ARGS="$*"
fi

echo "===== $(date '+%F %T') AUTO  args=${ARGS} =====" >> logs/auto.log
"${PY}" sell_auto.py ${ARGS} >> logs/auto.log 2>&1
rc=$?
echo "----- $(date '+%F %T') END exit=${rc}" >> logs/auto.log
exit "${rc}"
