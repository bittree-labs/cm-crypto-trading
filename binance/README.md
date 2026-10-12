# Binance USDT-M 永续合约（fapi）工具集

我们基本只交易 **USDT 本位永续合约**（`fapi.binance.com`），现货（`api.binance.com`）只在需要搬砖/对冲时用。
全部脚本 **纯标准库（Py3.7+ 可跑），无 pip 依赖**；实测日期 2026-10-01，主站直连可用（国内被墙时见文末备用域名/代理）。

## 文件

| 文件 | 作用 |
|------|------|
| `fapi_client.py` | REST 客户端：公共行情 + HMAC 签名私有接口 + 精度取整/自检/重试/时间校准 |
| `perp_snapshot.py` | 一键拉齐 BTCUSDT（可多标的）永续数据 → 文本报告 / JSON / JSONL 历史 |
| `perp_trade.py` | 交易 CLI：账户/持仓/挂单、设杠杆保证金、仓位计算、下单（默认 dry-run） |
| `.env.example` | key 模板（复制成 `.env`，已 gitignore） |

## 快速开始

```bash
cd binance
python3 fapi_client.py ping                      # 连通性 + 时钟偏移
python3 perp_snapshot.py                         # BTCUSDT 永续全景（约 2 秒）
python3 perp_snapshot.py ETHUSDT SOLUSDT --json  # 多标的 JSON
python3 perp_snapshot.py BTCUSDT --save ../data/binance_perp   # 存 JSONL 供回测/复盘

cp .env.example .env && vi .env                  # 填 key（合约权限要单独开通）
python3 perp_trade.py balance                    # 合约钱包资产
python3 perp_trade.py size --side long --risk-pct 1 --stop-pct 2 --leverage 5
python3 perp_trade.py market BTCUSDT BUY 0.001   # 默认 dry-run，加 --live 才真发单
```

## 接口地图（USDT-M 永续，`/fapi/v1`、数据类 `/futures/data/`）

### 公共（无鉴权，直接 GET）
| 用途 | 端点 | 备注 |
|------|------|------|
| 连通/服务器时间 | `/fapi/v1/ping`、`/fapi/v1/time` | 时钟偏移 >1s 会 -1021 |
| 交易对规则 | `/fapi/v1/exchangeInfo` | weight 20，含 `stepSize/tickSize/minNotional/数量精度` |
| 最新价 | `/fapi/v1/ticker/price?symbol=` | 不传 symbol 返回全部 |
| 24h 统计 | `/fapi/v1/ticker/24hr?symbol=` | 涨跌幅/高低/成交额/笔数 |
| 买一卖一 | `/fapi/v1/ticker/bookTicker?symbol=` | 算滑点用 |
| 标记价+资金费 | `/fapi/v1/premiumIndex?symbol=` | `markPrice/indexPrice/lastFundingRate/nextFundingTime/interestRate` |
| 资金费历史 | `/fapi/v1/fundingRate?symbol=&limit=1000` | 8h 一期，做资金费套利/成本核算 |
| 持仓量 | `/fapi/v1/openInterest?symbol=` | 单张数（合约张），→ BTC 要乘 `markPrice` 前注意单位 |
| K 线 | `/fapi/v1/klines?symbol=&interval=&limit=1500` | 单次上限 1500 |
| 连续/指数 K 线 | `/fapi/v1/continuousKlines`、`/fapi/v1/indexPriceKlines` | 拼接历史合约 |
| 深度 / 成交 | `/fapi/v1/depth`、`/fapi/v1/aggTrades` | 深度 5/10/20/50/100/500/1000 档 |
| OI 历史 | `/futures/data/openInterestHist` | period 5m~1d，limit 500 |
| 多空账户比（全市场） | `/futures/data/globalLongShortAccountRatio` | 散户情绪，常作反向指标 |
| 大户账户比 / 持仓比 | `/futures/data/topLongShortAccountRatio`、`topLongShortPositionRatio` | 聪明钱 |
| 主动买卖量比 | `/futures/data/takerlongshortRatio` | >1 主动买占优 |
| 基差 | `/futures/data/basis` | 期货-现货基差历史 |

### 私有（HMAC-SHA256 签名：`query + &timestamp=&recvWindow=60000` → `&signature=hex(hmac_sha256(secret, query))`，头带 `X-MBX-APIKEY`）
| 用途 | 端点 |
|------|------|
| 合约钱包余额 | `GET /fapi/v2/balance`（看 `availableBalance`，不是 `balance`） |
| 账户全景（资产+持仓+挂单+费率档） | `GET /fapi/v3/account` |
| 持仓 + 强平价 + 未实现盈亏 | `GET /fapi/v2/positionRisk?symbol=` |
| 资金流水（REALIZED_PNL/FUNDING_FEE/COMMISSION） | `GET /fapi/v1/income` |
| 手续费率 | `GET /fapi/v1/commissionRate?symbol=` |
| 设杠杆 / 保证金模式 | `POST /fapi/v1/leverage`、`POST /fapi/v1/marginType` |
| 单向/双向持仓、多资产模式 | `GET|POST /fapi/v1/positionSide/dual`、`/fapi/v1/multiAssetsMargin` |
| 下单 / 查单 / 撤单 | `POST|GET|DELETE /fapi/v1/order` |
| 挂单列表 / 批量撤单 / 历史单 / 成交明细 | `/fapi/v1/openOrders`、`/allOpenOrders`、`/allOrders`、`/userTrades` |
| 划转（现货↔合约） | `POST /sapi/v1/futures/transfer`（`sapi` 域名同 host，需现货 key 权限） |
| 杠杆/借贷 | `sapi` 的 `margin`、`futures` 系列（暂未封装） |

### WebSocket（比轮询省 weight，实时首选）
- 公共：`wss://fstream.binance.com/ws/<symbol>@aggTrade`、`@markPrice@1s`、`@kline_1m`、`@depth20@100ms`、`@forceOrder`（爆仓流）
- 私有（需 listenKey）：`POST /fapi/v1/listenKey` → `wss://fstream.binance.com/ws/<listenKey>`，订阅 `ORDER_TRADE_UPDATE`、`ACCOUNT_UPDATE`；每 30 分钟 `PUT /fapi/v1/listenKey` 续期
- 组合流：`/stream?streams=a/b/c`

## 下单必读（踩过的坑）

1. **精度**：数量按 `LOT_SIZE.stepSize` **向下取整**（本工具 `round_qty` 已处理），价格按 `PRICE_FILTER.tickSize` 取整，否则 `-1111/-1013`。BTCUSDT 永续：stepSize `0.001`、tickSize `0.1`、minNotional **50 USDT**（资金费率那栏的 minNotional 是 5，别混）。
2. **两套权限**：现货 key 默认无合约权限，合约要在币安后台单独开 Futures；合约 key 也只有开了 Futures 才可用。
3. **`-1021 Timestamp`**：本机时钟偏移。客户端已内置 `sync_time()` 自动补偿 + `recvWindow=60000`。
4. **`-2019 Margin insufficient`**：可用保证金不足（多资产模式下会先用其他币种）。
5. **`-2027`/`-4164`**：超过单笔最大名义/超杠杆档位，需要降杠杆或分批。
6. **`reduceOnly` 与双向持仓互斥**：单向模式用 `reduceOnly=true` 平仓；双向模式（Hedge）用 `positionSide=LONG/SHORT` + 反向 side。
7. **限流**：429/418 触发后必须退避（客户端内置指数回退，最多 3 次）。重量按端点计，klines=1~2、exchangeInfo=20、下单=1。
8. **`type=MARKET` 没有 `price`**；`STOP_MARKET` 必须有 `stopPrice` 并建议 `workingType=MARK_PRICE`（避免插针以最新价触发）。
9. **资金费结算 8h 一次**（00/08/16 UTC），持仓跨结算点必然付费/收费；`funding_rate_annual_pct = 当期费率 × 3 × 365`。
10. **国内网络**：`api/fapi.binance.com` 被墙时换 `api1~api4.binance.com`、`fapi1~fapi4.binance.com`，或 `export HTTPS_PROXY=http://127.0.0.1:7897`（标准库自动读）。

## 期货测试网（强烈建议先在这跑通下单链路）
```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from fapi_client import FapiClient, FAPI_TESTNET
c = FapiClient(base=FAPI_TESTNET)   # key 用 testnet.binancefuture.com 申请的
print(c.ping(), c.price('BTCUSDT'))"
```
测试网与主网 **key 不通用**，行情/规则基本一致，适合验证下单→止损→平仓全流程。

## 与本仓库其他部分的关系
- `market-data/` — 旧的行情采集（现货价格/pairs）。
- `coinglass/btc_contract_monitor.py` — CoinGlass 付费源 + Binance 备用源的合约监控（预警分级更细）。
- `arbitrage/` — 跨所（Kraken/XT）价差监控。
本目录是 **Binance 永续专用**：数据 + 交易一体化，无第三方依赖。
