# CoinGlass BTC 合约数据监控

实时拉取 BTC 合约数据(价格 / 资金费率 / 持仓量 OI / 多空比 / 主动买卖 / 爆仓),
做综合分析与分级预警。

## 快速上手

```bash
cd cm-crypto-trading/coinglass
# 把 key 放进 .env (已 gitignore, 勿提交)
echo 'COINGLASS_API_KEY=你的key' > .env

python3 btc_contract_monitor.py            # 打印分析报告 + 预警
python3 btc_contract_monitor.py --json     # JSON 输出(便于 cron / 二次加工)
python3 btc_contract_monitor.py --demo     # 强制走 Binance 免费源
```

零第三方依赖(仅标准库),Python 3.7+ 可跑,直接用于 cron。

## 数据源

| 优先级 | 源 | 说明 |
|---|---|---|
| 1 | CoinGlass v4 API | 数据最全(含爆仓历史/多空比/CVD/max-pain),需付费套餐 |
| 2 | Binance fapi(免费) | 免费备用:价格/资金费率/OI/多空账户比/taker 买卖比 |

脚本自动检测 CoinGlass 是否可用,不可用(套餐不足/网络异常)时回退 Binance。

## CoinGlass v4 API 规范(实测结论)

- **Base URL**: `https://open-api-v4.coinglass.com`
- **鉴权 header**: `CG-API-KEY: <key>`(不是 `coinglassSecret`,旧 v1 已 deprecated)
- **文档索引**: `https://docs.coinglass.com/llms.txt`(任意文档页 URL 追加 `.md` 可取 markdown 原文)
- **成功响应**: `{"code": "0", "data": [...]}`

### 关键端点(参数易错点)

| 用途 | 路径 | 关键参数 |
|---|---|---|
| 价格 OHLC | `/api/futures/price/history` | `exchange=Binance` + `symbol=BTCUSDT` + `interval` |
| 聚合持仓量 OI | `/api/futures/open-interest/aggregated-history` | `symbol=BTC`(仅币种,无 exchange) |
| 资金费率 | `/api/futures/funding-rate/history` | `exchange` + `symbol=BTCUSDT` |
| 多空账户比 | `/api/futures/global-long-short-account-ratio/history` | `exchange` + `symbol=BTCUSDT` |
| 聚合爆仓 | `/api/futures/liquidation/aggregated-history` | `exchange_list=Binance,OKX,Bybit` + `symbol=BTC` |
| 主动买卖量 | `/api/futures/aggregated-taker-buy-sell-volume/history` | `exchange_list` + `symbol=BTC` |
| 聚合 CVD | `/api/futures/aggregated-cvd/history` | 仅 Startup+ 套餐 |
| 爆仓 max-pain | `/api/futures/liquidation/max-pain` | 仅 Professional+ 套餐 |
| 账户订阅等级 | `/api/user/account/subscription` | 返回 `{level, expire_time, expired}` |

> ⚠️ `symbol` 两套规则:聚合类端点(oi/liquidation/taker/cvd)用**币种** `BTC`;
> 交易所级端点(price/funding/long-short/basis)用**交易对** `BTCUSDT` + `exchange`。

### 套餐与限制(2026-08 价格)

| 套餐 | 月费 | 端点数 | 速率/min | interval 下限 | 备注 |
|---|---|---|---|---|---|
| HOBBYIST | $29 | 80+ | 30 | ≥4h | 无 CVD / whale-index / max-pain |
| STARTUP | $79 | 130+ | 80 | ≥30m | 含 CVD、whale-index |
| STANDARD | $299 | 150+ | 300 | 无限制 | 除 max-pain 外全量 |
| PROFESSIONAL | $699 | 160+ | 1200 | 无限制 | 全量(含 max-pain) |

**当前 key 状态**:套餐不足,所有 futures 端点返回 `{"code":"401","msg":"Upgrade plan"}`。
做 4h 级预警最低需 HOBBYIST;做 1h/15m 级实时预警建议 STARTUP 或 STANDARD。

## 预警逻辑

| 信号 | 触发条件(可改 ALERT 字典) |
|---|---|
| 资金费率过热 | 年化 > 20% 或 < -20%;z-score > 2 |
| OI 异动 | 24h 增 > +15% 或降 < -15% |
| 多空比拥挤 | > 3.0(多头)或 < 0.7(空头) |
| 主动买卖主导 | taker 比 > 1.6 或 < 0.6 |
| 爆仓潮 | 单周期爆仓 > $50M |
| 价-OI 背离 | 价涨 OI 降(逼空)/ 价跌 OI 增(加空) |

分级:HIGH(红)/ MED(黄)/ LOW(绿)。

## Binance 免费源注意

- 数据端点已迁移:`/futures/data/openInterestHist`、`/futures/data/globalLongShortAccountRatio`、`/futures/data/takerlongshortRatio`(旧 `/fapi/v1/...` 路径返回 404 或错误页)。
- `longAccount`/`shortAccount` 是 0-1 小数,需 ×100 转百分比。
- 免费源无爆仓历史(需 CoinGlass 或 WebSocket forceOrders)。
