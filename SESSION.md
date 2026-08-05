# SESSION — sb01 crypto-trade

> 最后提交：2026-08-06 | Agent: cw-sb01@M4-Pro  
> 恢复：`git pull && 启动 sb01`，读取本文件即可接续

## 会话状态

| 项目 | 值 |
|------|-----|
| Session ID | sb01 (B组 - Crypto & Mining) |
| 主仓库 | cm-crypto-trading |
| 设备 | M4-Pro |
| 工作目录 | ~/workspace/bittree-labs/cm-crypto-trading |

## 运行中的任务

### trade_ai3 — AI3 套利（1分钟间隔）

| 属性 | 值 |
|------|-----|
| 脚本 | `~/workspace/scratch/trade_ai3.py`（本地，不在仓库） |
| 间隔 | 60秒 |
| 日志 | `~/workspace/scratch/trade_ai3.log` |
| 数据源 | CoinGecko API (autonomys-network/tickers) |
| 交易对 | XT.COM(USDT) ↔ Kraken(USD) |
| 告警 | Telegram，net5k > $100 时推送 |

**启动命令**:
```bash
pkill -f trade_ai3.py 2>/dev/null          # 先清理旧实例
/usr/local/bin/python3 ~/workspace/scratch/trade_ai3.py &
```

**验证运行**:
```bash
tail -3 ~/workspace/scratch/trade_ai3.log  # 应有分钟级时间戳
```

**脚本逻辑**（如需要重建）:
- 调用 `https://api.coingecko.com/api/v3/coins/autonomys-network/tickers`
- 提取 Kraken(USD) 和 XT.COM(USDT) 价格
- 计算 diff% = (Kraken - XT) / XT * 100
- 计算 net5k = 5000 * diff/100 - 30(手续费0.6%) - 20(网络费)
- net5k > 100 时 Telegram 推送
- 脚本内容见同目录 `trade_ai3.py`

## 已停止

| 系统 | 原因 | 停止时间 |
|------|------|------|
| daemon.py | 改为纯 AI3 套利模式 | ~23:40 |

## MCP 服务（codewhale 自动管理）

| 服务 | 命令 |
|------|------|
| meme-data | `node ~/meme-data-mcp/dist/index.js` |
| binance | `node ~/workspace/codewhale/trade/binance-mcp/index.js` |

## 最后已知数据

```
23:42  XT=$0.001729 Kraken=$0.001311  diff=-24.2%  net5k=-$1258
```

## 恢复流程（新 agent）

```bash
# 1. 拉最新
cd ~/workspace/bittree-labs/cm-crypto-trading && git pull

# 2. 读状态
cat SESSION.md
cat STATUS.md

# 3. 启动 trade_ai3
pkill -f trade_ai3.py 2>/dev/null
/usr/local/bin/python3 ~/workspace/scratch/trade_ai3.py &

# 4. 验证
sleep 5 && tail -3 ~/workspace/scratch/trade_ai3.log
```
