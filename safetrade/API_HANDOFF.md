# SafeTrade API 交接文档（给其他会话/Agent 读）

> 本项目内任何会话要动 SafeTrade（safe.trade）的 API，**先读这份**，再读 `README.md`。
> 也可以直接加载技能：`skill_view(name="safetrade-api-trading")`（全局技能，含同样要点）。
> 最后更新：2026-10-08（全部结论均为当日实测，非文档推测）

## 0. 30 秒速览

- 交易所：**SafeTrade**，REST API v2，`https://safe.trade/api/v2`（Swagger: `/trade/public/swagger.json`）
- 能做：查行情/余额、**限价与市价下单、撤单**、查成交 → 可全自动卖 PRL
- 不能/不做：**网页自动化**（官网有 Cloudflare，连真浏览器都被拦）；**提现**（Key 故意不给权限）
- 硬约束：客户端**必须用 curl_cffi 浏览器指纹**（`impersonate="safari17_0"`），否则 403
- 运行位置：**只在 Mac 上跑**（需 VPN 常开）；IDC 集群（ya/ya2）直连不了，且卖币与集群无关
- 凭证：`safetrade/.env`（`SAFETRADE_API_KEY` / `SAFETRADE_API_SECRET`，600，gitignore）
  —— 权限范围：**只授权 PRL/USDT 交易，额度 100 PRL，无提现权限**
- 工具：`safetrade/safetrade_client.py`（客户端）、`sell_prl.py`（分片卖币）、`sell_auto.py`（指标择时）、`quant.py`（指标）、`smoke_test.py`（链路自检）

## 1. 认证与签名（照抄即可）

```python
nonce = str(int(time.time() * 1000))                      # 毫秒
sign  = hmac_sha256_hex(api_secret, nonce + api_key)       # 小写 hex
headers = {
    "X-Auth-Apikey":   api_key,
    "X-Auth-Nonce":    nonce,
    "X-Auth-Signature": sign,
    "Content-Type":    "application/json;charset=utf-8",
}
```

**错误码排查表**（实测）：

| 返回 | 含义 |
|---|---|
| `401 authz.invalid_session` | 没带签名头 |
| `404 record.not_found` | API Key 不存在（Key 错/已删） |
| `401 authz.invalid_signature` | Key 对，**Secret 错了**（或签名串拼错） |
| `403`（Cloudflare 页面） | TLS 指纹不对 **或** 请求太密被限流 → 用 curl_cffi + 退避重试 |

## 2. 端点表（已实测）

| 用途 | 方法与路径 |
|---|---|
| 全部市场 | `GET /trade/public/markets` |
| 单市场参数 | `GET /trade/public/markets/{id}` |
| 行情 | `GET /trade/public/tickers/{market}` |
| 盘口 | `GET /trade/public/markets/{id}/depth?limit=N` → `{asks:[[p,amt]..], bids:[..]}` |
| K线 | `GET /trade/public/markets/{id}/k-line?period=<分钟>&time_from=<unix秒>&time_to=<unix秒>&limit=<≤100>` → `[ts, o, h, l, c, vol]` |
| 市场成交 | `GET /trade/public/markets/{id}/trades` |
| 交易费率 | `GET /trade/public/trading_fees` |
| 币种/提现费 | `GET /trade/public/currencies` → `networks[].withdraw_fee / min_withdraw_amount / withdraw_enabled` |
| 账号信息 | `GET /trade/account/members/me` |
| 现货余额 | `GET /trade/account/balances/spot` |
| **下单** | `POST /trade/market/orders` body `{"market":"prlusdt","side":"sell","amount":10,"price":1.45,"type":"limit"}`（去掉 price = 市价） |
| **撤单** | `POST /trade/market/orders/{id}/cancel` |
| 查单 | `GET /trade/market/orders?market=prlusdt&state=wait,pending&limit=100` |
| 我的成交 | `GET /trade/market/trades?market=prlusdt&limit=50` |
| 提现（**本项目禁用**） | `POST /trade/account/withdraws` |

**下单返回**：`{id, state: pending/wait/done/cancel/rejected, price, avg_price, origin_amount, filled_amount, maker_fee, taker_fee, ...}`

## 3. 市场 / 费率（2026-10-08 实测）

```
prlusdt  QUBIC 无关：PRL/USDT  state=enabled  amount_precision=4  price_precision=2(0.01)  min_amount=2 PRL
PRL/USDC 同状态可用；PRL/BTC 也可用
现货费率：maker == taker == 0.1%（全站一条规则，无 VIP 档差）→ 做 maker 省不了费
提现费：PRL 0.01 PRL（Pearl 网）；USDT 可提网络 BSC/Solana 1u、ERC20 3u（Base/Tron/Avax/Arbitrum/Polygon 暂停）
```

- **maker/taker 差别只有"成交价 vs 确定性"**：taker 贴 best_bid 必成；maker 挂 best_ask 可能多赚 1 tick 但可能不成交。
- 手续费扣在**你收到的那个币**上（卖单扣 USDT，买单扣标的币）。

## 4. 三个必须知道的坑

1. **余额字段没有 `available`**：`/trade/account/balances/spot` 返回 `{currency, balance, locked, type}`，
   其中 **`balance` = 可用(free)**、`locked` = 挂单冻结。当成 `available` 读会得到 None → 误判"没币可卖"。
2. **Cloudflare 限流**：高频轮询（如每 5s 查单）会吃 **403**。客户端已内置 403/429/5xx 指数退避重试
   （`SafeTrade(retries=4, backoff=2.0)`，重试会换新 nonce 重新签名）；轮询间隔 ≥10s。
3. **盘口很薄**：PRL/USDT 一档常只有 3k~4k 枚。一把市价卖 6 万枚会从 1.41 砸到 1.29（−8.5%）。
   **大额必须分片挂限价**（`sell_prl.py` 就是干这个的）。

## 5. 工具与用法

```bash
cd ~/workspace/bittree-labs/cm-crypto-trading/safetrade
./venv/bin/python3 safetrade_client.py ticker prlusdt     # 行情
./venv/bin/python3 safetrade_client.py balance            # 余额（需 Key）
./venv/bin/python3 safetrade_client.py me                 # 连接+签名自检
./venv/bin/python3 safetrade_client.py orders             # 未成交/历史单
./venv/bin/python3 smoke_test.py --live                   # 下单+撤单+成交链路自检（小额真单）
./venv/bin/python3 sell_prl.py --dry-run --amount 10      # 只有计划的预览
./venv/bin/python3 sell_prl.py --live --amount 10 --slices 1        # taker 卖 10 枚
./venv/bin/python3 sell_prl.py --live --mode maker --maker-fallback --amount 10 --timeout 180
./venv/bin/python3 sell_auto.py                            # 指标择时决策（读 bias.json，dry-run）
./run_sell_prl.sh                                          # 每日 12:00 定时任务入口（策略1）
./run_sell_auto.sh                                         # 每 4h 指标引擎入口（影子模式）
```

- 解释器：**用 `safetrade/venv/bin/python3`**（Python 3.14 + curl_cffi 0.16.3，自带）
  或退回 `~/.hermes/hermes-agent/venv/bin/python3`（同样有 curl_cffi）。
  ⚠️ `/usr/local/bin/python3` 是 x86_64 坏二进制，`/usr/bin/python3`(3.8) 装 curl_cffi 会崩，都别用。
- 日志：`logs/cron.log`（定时卖）、`logs/auto.log`（指标引擎）、`logs/decisions.jsonl`（每次决策）、`fills.jsonl`（成交记录）

## 6. 现行策略（用户 2026-10-08 定的）

| 策略 | 内容 | 状态 |
|---|---|---|
| 策略1 | 每天 **12:00 卖 10 枚 PRL**（只卖不买，永不清零） | launchd `com.bittree.safetrade.sellprl` 已装 |
| 策略2 | 参数化卖：`--pct/--max-per-run/--reserve/--floor/--mode maker` | 未挂定时，手动跑 |
| 低频量化 | 指标触发（日线 RSI/布林/EMA/ATR/量能）+ 人定大方向 `bias.json` | 引擎已实现，**launchd 未装（待用户批准）** |

## 7. 不要做的事

- 不要用网页/浏览器自动化操作 safetrade.com（必被 Cloudflare 拦）
- 不要把 Key/Secret 写进代码、日志、聊天、提交
- 不要把卖币任务部署到 IDC 集群（网络不通 + 与集群运维无关）
- 不要调用提现接口；不要在 Key 上加提现权限
- 大额不要市价一把梭（会自己砸盘）
