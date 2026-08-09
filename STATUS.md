# 状态

> 最后更新：2026-08-09 21:37 | Agent: cw-sb01@M4-Pro

## Session

- **ID**: sb01 (B组 - Crypto & Mining)
- **主仓库**: cm-crypto-trading
- **设备**: M4-Pro
- **状态**: 🟢 运行中（第4天）

## 活跃系统

| 系统 | 来源 | 状态 | 备注 |
|------|------|:--:|------|
| trade_ai3 (AI3套利) | ~/workspace/scratch/trade_ai3.py | 🟢 | 1分钟间隔，TG推送 |
| Meme 行情 | MCP meme-data | 🟢 | 自动连接 |
| Binance 行情 | MCP binance | 🟢 | 自动连接 |

## 最近检查 (AI3)

```
10:45  XT=$0.001149 Kraken=$0.000921  diff=-19.9%  net5k=-$1045
10:40  XT=$0.001199 Kraken=$0.000921  diff=-23.2%  net5k=-$1212
10:36  XT=$0.001199 Kraken=$0.000941  diff=-21.6%  net5k=-$1128
```

> 持续负价差，XT 溢价 ~20%，暂无套利窗口

## 变更记录

- 08-05 ~23:40 — daemon.py 停用，切换为 trade_ai3 纯套利模式（1分钟间隔）
- 08-09 — 持续运行3天+，无套利机会触发
