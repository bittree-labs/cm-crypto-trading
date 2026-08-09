# SESSION — sb01 crypto-trade

> 最后提交：2026-08-09 10:45 | Agent: cw-sb01@M4-Pro  
> 恢复：`git pull && 启动 sb01`，读取本文件即可接续

## 会话状态

| 项目 | 值 |
|------|-----|
| Session ID | sb01 (B组 - Crypto & Mining) |
| 主仓库 | cm-crypto-trading |
| 设备 | M4-Pro |
| 工作目录 | ~/workspace/bittree-labs/cm-crypto-trading |
| 运行天数 | 3+ 天（自 08-05 23:40 起） |

## 运行中的任务

### trade_ai3 — AI3 套利（1分钟间隔）

| 属性 | 值 |
|------|-----|
| 脚本 | `~/workspace/scratch/trade_ai3.py`（本地） |
| 间隔 | 60秒 |
| 日志 | `~/workspace/scratch/trade_ai3.log` |
| 数据源 | CoinGecko API (autonomys-network/tickers) |
| 交易对 | XT.COM(USDT) ↔ Kraken(USD) |
| 告警 | Telegram，net5k > $100 时推送 |
| 当前状态 | 🟢 运行中 |

**启动命令**:
```bash
pkill -f trade_ai3.py 2>/dev/null
/usr/local/bin/python3 ~/workspace/scratch/trade_ai3.py &
```

**验证运行**:
```bash
tail -3 ~/workspace/scratch/trade_ai3.log
```

## 已停止

| 系统 | 原因 | 停止时间 |
|------|------|------|
| daemon.py | 改为纯 AI3 套利模式 | 08-05 ~23:40 |

## MCP 服务（codewhale 自动管理）

| 服务 | 命令 |
|------|------|
| meme-data | `node ~/meme-data-mcp/dist/index.js` |
| binance | `node ~/workspace/codewhale/trade/binance-mcp/index.js` |

## 最后已知数据

```
10:45  XT=$0.001149 Kraken=$0.000921  diff=-19.9%  net5k=-$1045
10:36  XT=$0.001199 Kraken=$0.000941  diff=-21.6%  net5k=-$1128
```

> 持续负价差，XT 溢价 ~20%，暂无套利窗口。价差一旦转正即 TG 推送。

## 恢复流程（新 agent）

```bash
cd ~/workspace/bittree-labs/cm-crypto-trading && git pull
cat SESSION.md
pkill -f trade_ai3.py 2>/dev/null
/usr/local/bin/python3 ~/workspace/scratch/trade_ai3.py &
sleep 5 && tail -3 ~/workspace/scratch/trade_ai3.log
```
