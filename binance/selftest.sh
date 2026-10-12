#!/usr/bin/env bash
# Binance 永续工具集自检 — 一条命令验证全部脚本可用 (公共接口, 不需要 API key)
# 用法: bash selftest.sh [PYTHON]   默认 python3
set -u
cd "$(dirname "$0")"
PY="${1:-python3}"
PASS=0; FAIL=0

chk() { # chk "名称" "命令" "期望包含的字符串"
  local name="$1" cmd="$2" want="${3:-}"
  local out rc
  out="$(eval "$cmd" 2>&1)"; rc=$?
  if [ $rc -ne 0 ]; then printf '✗ %-28s exit=%s\n%s\n' "$name" "$rc" "$(printf '%s' "$out" | tail -5)"; FAIL=$((FAIL+1)); return; fi
  if [ -n "$want" ] && ! printf '%s' "$out" | grep -q "$want"; then
    printf '✗ %-28s 输出缺少 "%s"\n%s\n' "$name" "$want" "$(printf '%s' "$out" | tail -5)"; FAIL=$((FAIL+1)); return
  fi
  printf '✓ %-28s %s\n' "$name" "$(printf '%s' "$out" | head -1 | cut -c1-70)"; PASS=$((PASS+1))
}

chkfail() { # chkfail "名称" "命令" "期望包含的字符串"  (期望非 0 退出)
  local name="$1" cmd="$2" want="${3:-}"
  local out rc
  out="$(eval "$cmd" 2>&1)"; rc=$?
  if [ $rc -eq 0 ]; then printf '✗ %-28s 本应失败却成功\n' "$name"; FAIL=$((FAIL+1)); return; fi
  if [ -n "$want" ] && ! printf '%s' "$out" | grep -q "$want"; then
    printf '✗ %-28s 错误信息不含 "%s": %s\n' "$name" "$want" "$(printf '%s' "$out" | tail -1)"; FAIL=$((FAIL+1)); return
  fi
  printf '✓ %-28s %s\n' "$name" "$(printf '%s' "$out" | head -1 | cut -c1-70)"; PASS=$((PASS+1))
}

echo "=== Binance USDT-M 永续工具集自检 (python=$PY) ==="
chk "fapi_client ping"     "$PY fapi_client.py ping" "ping ok"
chk "fapi_client pairs"    "$PY fapi_client.py pairs" "永续合约"
chk "fapi_client filters"  "$PY fapi_client.py filters BTCUSDT" "stepSize"
chk "perp_snapshot 文本"   "$PY perp_snapshot.py BTCUSDT" "资金费"
chk "perp_snapshot JSON"   "$PY perp_snapshot.py --json --symbol BTCUSDT" "funding_rate_annual_pct"
chk "perp_snapshot 多标的" "$PY perp_snapshot.py BTCUSDT ETHUSDT SOLUSDT" "ETHUSDT"
chk "perp_trade size"      "$PY perp_trade.py size --side long --risk-pct 1 --stop-pct 2 --equity 10000 --leverage 5" "止损价参考"
chkfail "perp_trade 无key报错" "$PY perp_trade.py balance" "缺 API key"
chk "perp_trade dry-run"   "$PY perp_trade.py market BTCUSDT BUY 0.001" "DRY-RUN"

echo "-----------------------------------------------"
echo "通过 $PASS / 失败 $FAIL"
[ $FAIL -eq 0 ] || exit 1
echo "注: 私有接口(余额/持仓/下单)需配 BINANCE_API_KEY/SECRET 后单独验证 —— 见 README.md"
