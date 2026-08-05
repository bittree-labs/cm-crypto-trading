# 状态

> 最后更新：2026-08-06 | Agent: cw-sb01@M4-Pro

## Session

- **ID**: sb01 (B组 - Crypto & Mining)
- **主仓库**: cm-crypto-trading
- **设备**: M4-Pro
- **状态**: 🟢 运行中

## 活跃系统

| 系统 | 来源 | 状态 | 备注 |
|------|------|:--:|------|
| trade_ai3 (AI3套利) | ~/workspace/scratch/trade_ai3.py | 🟢 | 1分钟间隔，TG推送 |
| Meme 行情 | MCP meme-data | 🟢 | 自动连接 |
| Binance 行情 | MCP binance | 🟢 | 自动连接 |

## 最近检查 (AI3)

```
23:42  XT=$0.001729 Kraken=$0.001311  diff=-24.2%  net5k=-$1258
23:41  XT=$0.001729 Kraken=$0.001311  diff=-24.2%  net5k=-$1258
```

## 变更记录

- ~23:40 — daemon.py 停用，切换为 trade_ai3 纯套利模式（1分钟间隔）
