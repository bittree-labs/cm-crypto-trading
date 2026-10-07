# SafeTrade (safe.trade) API —— PRL 自动卖币

> 结论先说：**SafeTrade 有官方 REST API v2，支持程序化挂单/撤单/查余额，PRL/USDT 现货可全自动卖出。**
> 不需要网页自动化（官网 safetrade.com 有 Cloudflare，无头浏览器直接被拦；API 走 `safe.trade` 子域，指纹伪装后可通）。

## 1. 接口事实（2026-10-08 实测）

| 项 | 值 |
|---|---|
| Base URL | `https://safe.trade/api/v2` |
| 官方 Swagger | `https://safe.trade/api/v2/trade/public/swagger.json`（25 个端点） |
| 认证 | 三个请求头：`X-Auth-Apikey` / `X-Auth-Nonce`（毫秒时间戳）/ `X-Auth-Signature` |
| 签名算法 | `hex( HMAC_SHA256( api_secret, nonce + api_key ) )` （小写 hex，与官方 example-client 一致） |
| 公共行情 | `/trade/public/tickers/{market}`、`/trade/public/markets/{id}/depth`、`/k-line`、`/trades`、`/trade/public/trading_fees` |
| 账户 | `GET /trade/account/members/me`、`GET /trade/account/balances/spot`、`GET /trade/account/deposits`、`POST /trade/account/withdraws` |
| 交易 | `POST /trade/market/orders`（下单）、`POST /trade/market/orders/{id}/cancel`（撤单）、`GET /trade/market/orders`（查单）、`GET /trade/market/trades`（成交） |
| 下单参数 | `{market, side, amount, price?, type:"limit"|"market"}`（`amount` = PRL 枚数；不传 `price` 即市价） |
| WebSocket | `/websocket/public`、`/websocket/private` |

**已验证**：不带签名 `POST /trade/market/orders` → `401 authz.invalid_session`；带（无效）签名 → `404 record.not_found`（说明签名头已被服务端解析、进入 API Key 查找），公共行情端点 200 正常返回。

PRL/USDT 市场参数（实测）：

```
prlusdt  state=enabled  amount_precision=4(PRL)  price_precision=2(0.01)  min_amount=2 PRL
trading_fees: maker 0.001 / taker 0.001   → 名义费率 0.1%
（另有 PRL/USDC、PRL/BTC 同状态可用）
```

> 2026-10-08 01:03 盘口：last 1.41 / best_bid 1.41（3.4k 枚）/ best_ask 1.42（0.3k 枚），24h 量 ≈ 281 万枚。
> **买盘很薄**：一把市价卖 6 万枚，从 1.41 一路吃到 1.29（-8.5%）。这就是本模块存在的理由。

## 2. ⚠️ 三个工程约束（踩过才知道）

1. **必须有浏览器 TLS 指纹**：`safe.trade` 前置 Cloudflare，`curl` / `urllib` / 纯 `requests` 直接 **403**；用 `curl_cffi` 的 `impersonate="safari17_0"` 才通。本模块已默认使用 `curl_cffi`。
   ```bash
   pip install curl_cffi
   ```
2. **不要用网页自动化**：`safetrade.com`（官网，非 api 子域）连真实浏览器都会被 CF 拦（"Sorry, you have been blocked"）。所有操作走 API。
3. **只在 Mac 上跑（需开 VPN）**：卖币脚本的出口 = Mac 的 VPN/代理，实测可通。**不要部署到 ya / ya2 等 IDC 集群**——集群没有 VPN，直连 `safe.trade:443` 会 TLS reset（实测不可达）；且卖币是交易侧的事，与集群运维无关。部署自检：`python3 safetrade_client.py ticker prlusdt`。

## 3. 快速开始

```bash
cd ~/workspace/bittree-labs/cm-crypto-trading/safetrade
pip install curl_cffi            # 首次
cp .env.example .env && chmod 600 .env   # 填 API Key / Secret

python3 safetrade_client.py markets prl      # 市场参数
python3 safetrade_client.py ticker prlusdt   # 行情
python3 safetrade_client.py depth prlusdt 10 # 盘口
python3 safetrade_client.py fees             # 费率
python3 safetrade_client.py me               # 需 Key：账号信息（连通性自检）
python3 safetrade_client.py balance          # 需 Key：余额（含 PRL 可用量）
python3 safetrade_client.py orders           # 需 Key：当前挂单
```

### 卖币（默认 dry-run，不会下单）

```bash
# 预览：卖 10000 枚 PRL，分 5 片，最低可接受价 1.35
python3 sell_prl.py --dry-run --amount 10000 --slices 5 --floor 1.35

# 真实执行：卖光可用 PRL（可留 reserve），分 6 片，floor 兜底
python3 sell_prl.py --live --slices 6 --floor 1.35 --interval 15 --timeout 180
```

`--live` 之外的所有情况都是预览。执行时**每一片都用最新盘口重算价格**（计划价只是预览），
若最新最差价 < `--floor` 就不卖（宁可不成交，也不贱卖）。

参数速查：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--live` | 关 | 真实下单；不加=只预览 |
| `--amount` | 可用余额 − reserve | 本次卖出 PRL 数量 |
| `--reserve` | 0 | 保留不卖的 PRL |
| `--slices` | 5 | 分片数（每片独立限价吃单） |
| `--floor` | 无 | 最低可接受价（USDT/PRL），低于则不卖 |
| `--interval` | 15 | 片间隔秒 |
| `--timeout` | 180 | 单片等待成交秒数，超时撤单 |
| `--mode` | limit | `market` = 市价（需 `--max-slippage` 保护，慎用） |

产物：`logs/sell_prl_YYYYMMDD.log`（人类可读）+ `fills.jsonl`（每片 JSON 记录，用于对账：PRL 数量 / 价 / 成交状态）。

## 3.5 策略（当前 Key 权限：交易对 prl-usdt / 总额度 100 PRL）

原则：**每次只卖一小口，永不清零。**

```bash
python3 sell_prl.py --live --pct 20 --max-per-run 20 --slices 2 --floor 1.30
```

- 每次卖「可用量的 20%」且**单次硬上限 20 枚** → 98 枚可用时每次卖 ~19.6 枚（≈28 USDT），
  5 次左右走完 100 枚额度；矿机新产出的 PRL 到账后自动纳入下一轮，不会清零。
- 想更慢：`--pct 10`（每次 ~10 枚）。想留底仓：`--reserve 50`（可用里先留 50 枚不卖）。
- `--max-per-run` 是防呆闸：万一天可用量突然变大，也不会被一次卖光。
- `--floor` 是价格保护：执行时按最新盘口重算，最差价低于 floor 就这一片不卖。

## 4. 定时任务（每天固定时间卖）

crontab（Mac / Linux）：

```cron
# 每天 10:07 卖币，每片间隔 15s，最低价 1.35
7 10 * * * cd ~/workspace/bittree-labs/cm-crypto-trading/safetrade && \
  /usr/bin/python3 sell_prl.py --live --slices 6 --floor 1.35 >> logs/cron.log 2>&1
```

或用 Hermes 的 cronjob（可带 Telegram 通知）：让 agent 建一个每天定时跑上面的命令、并把 `fills.jsonl` 末尾几行推送出来的任务。

## 5. 安全约定

- API Key 权限：**只勾现货交易（trade），绝不勾提现（withdraw）**；并绑定出口 IP（最多 20 条，支持网段）。即便 Key 泄漏，最多只能帮你交易，不能把钱提走。
- `.env` 已随仓库 `.gitignore` 忽略；文件权限 `600`。Key/Secret 不进聊天、不进日志。
- `sell_prl.py` 默认 dry-run；`--live` 必须显式写。
- 先小量试跑（例如 `--amount 100 --live --slices 1`）确认整条链路，再放全量。

## 6. 费率（2026-10-08 实测）

### 现货交易费（`GET /trade/public/trading_fees`）

```json
[{"group":"any","market_id":"any","maker":"0.001","taker":"0.001"}]
```

**maker = taker = 0.1%，全站统一一条规则，没有 VIP 档差 → 做 maker 省不了手续费。**
实测核对：2 PRL @1.43 = 2.86 → 到手 2.85714（费 0.00286 = 0.1%，taker）；QUBIC 同样 0.1%。
手续费按成交额扣在「你收到的那个币」上（卖单扣 USDT / 买单扣标的币）。

所以 maker 与 taker 的差别只有**价格**和**确定性**，不是费率：
- **taker（本脚本默认）**：贴 best_bid 直接吃 → 一定卖掉，成交价 = 当时买一
- **maker**：挂 best_ask 等买盘来吃 → 可能多卖 1 tick（0.01/枚），但可能等不到（= 没卖掉）

盘口一档通常几千枚，20 枚的量吃单冲击可忽略 → 默认 taker，确定性优先。

### 提现费（`GET /trade/public/currencies` → `networks[].withdraw_fee`）

| 币 | 网络 | 提现费 | 最小提现 | 可提现 |
|---|---|---|---|---|
| PRL | Pearl | **0.01 PRL** | 1 PRL | ✅ |
| USDT | BSC | 1 | 2 | ✅ |
| USDT | Solana | 1 | 2 | ✅ |
| USDT | ERC20 | 3 | 50 | ✅ |
| USDT | Base / Tron / Avalanche / Arbitrum | 1 | 1~10 | ❌ 暂停 |
| USDT | Polygon | 2 | 5 | ❌ 暂停 |
| USDC | Base / ERC20 / Arbitrum | 3~7 | 0~50 | ✅ |

> 交易费和提现费是两笔：卖币扣 0.1% 交易费；把钱提出交易所再扣一笔提现费（PRL 提现仅 0.01 PRL，
> 这就是之前"代出"账单里被扣掉的那几 u 的来源之一）。

## 7. 现状 / 待办

- [x] 确认 API 支持挂单 → 已实测端点存在且签名头被解析
- [x] 运行宿主定为 **Mac（VPN 常开）**；ya/ya2 集群不参与卖币
- [x] 客户端 + 分片卖币脚本（dry-run 跑通，读实时盘口出计划）
- [ ] 拿到 API Key（用户网页端创建、绑 IP、只给交易权限）后：`me` / `balance` 自检 → 小额 `--live` 试跑
- [ ] 定每天定时执行的时间点与 floor 策略（建议先观察一周盘口）
- [ ] 可选：搬砖/波段（PRL/USDC 与 PRL/USDT 价差、跨所价差）后续再谈
