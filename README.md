# Crypto Trading

BitTree 加密货币交易系统

## 结构

- `arbitrage/` — 跨交易所套利监控（Kraken/XT）
- `meme/` — Meme 币行情监控
- `binance/` — **Binance USDT-M 永续合约**工具集（fapi 客户端 / 数据快照 / 交易 CLI，纯标准库）
- `market-data/` — 行情数据采集（Binance 现货）
- `coinglass/` — CoinGlass BTC 合约监控（资金费/OI/多空比/爆仓 + 分级预警）
- `safetrade/` — **SafeTrade (safe.trade) API 客户端 + PRL 自动卖币**（分片限价、floor 保护、默认 dry-run）
  - 📄 **`safetrade/API_HANDOFF.md` = 给其他会话的交接文档**（端点/签名/费率/坑/策略，先读这个）
  - 技能：`safetrade-api-trading`
- `execution/` — 自动下单/代币销售机器人
- `reports/` — 交易报告

## 配置

```bash
export DEEPSEEK_API_KEY=xxx
export BINANCE_API_KEY=xxx
```
