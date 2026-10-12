#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance USDT-M 永续合约 REST 客户端 (stdlib only, Py3.7+)
=========================================================
公共行情零鉴权; 私有接口 HMAC-SHA256 签名。
用法见同目录 README.md。

    from fapi_client import FapiClient
    c = FapiClient()                       # 公共
    c.price("BTCUSDT")
    c = FapiClient(key, secret)            # 私有
    c.balance()

环境变量: BINANCE_API_KEY / BINANCE_API_SECRET (或同目录 .env)
代理    : 标准库自动读 HTTPS_PROXY / https_proxy
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN

FAPI_BASE = "https://fapi.binance.com"          # 主站 (国内被墙备用 fapi1~fapi4.binance.com)
FAPI_TESTNET = "https://testnet.binancefuture.com"
DATA_BASE = "https://fapi.binance.com"          # /futures/data/* 同 host

_ENV_LOADED = False


def load_env(path=None):
    """把脚本同目录 .env 里的 KEY=VALUE 灌进 os.environ (已存在的变量不覆盖)。"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


class FapiError(Exception):
    def __init__(self, code, msg, path="", http=0):
        self.code = code
        self.msg = msg
        self.path = path
        self.http = http
        Exception.__init__(self, "binance error %s (HTTP %s) @ %s: %s" % (code, http, path, msg))

    @property
    def retryable(self):
        """429 / 5xx / 限流类错误可退避重试。"""
        return self.http == 429 or self.http >= 500 or self.code in (-1003, -1008, -1015)

    @property
    def is_auth(self):
        return self.code in (-2015, -2014, -1022, -1021, -2019)


class FapiClient(object):
    def __init__(self, api_key=None, api_secret=None, base=FAPI_BASE,
                 timeout=15, recv_window=60000, retries=3, backoff=0.8, verbose=False):
        load_env()
        self.api_key = api_key if api_key is not None else os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.environ.get("BINANCE_API_SECRET", "")
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.recv_window = recv_window
        self.retries = retries
        self.backoff = backoff
        self.verbose = verbose
        self._filters = {}          # symbol -> {stepSize, tickSize, minQty, minNotional, qtyPrec, pricePrec}
        self._time_offset = 0       # 本地时钟对服务器的补偿(ms)
        self.last_weight = None

    # ---------------------------------------------------------------- HTTP
    def _sign(self, query):
        return hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

    def _http(self, url, method="GET", data=None, headers=None):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", "bittree-fapi/1.0")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                self.last_weight = resp.headers.get("X-MBX-USED-WEIGHT-1M")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(body)
                code, msg = err.get("code", exc.code), err.get("msg", body)
            except ValueError:
                code, msg = exc.code, body[:300]
            raise FapiError(code, msg, path=url.split("/fapi")[-1].split("?")[0], http=exc.code)
        except urllib.error.URLError as exc:
            raise FapiError(0, "network: %s" % exc.reason, path=url.split("/fapi")[-1].split("?")[0], http=0)

    def request(self, method, path, params=None, signed=False, base=None):
        """低层调用: 自动签名 / 重试 / 时间戳补偿。"""
        params = dict(params or {})
        url = (base or self.base) + path
        attempt = 0
        while True:
            p = dict(params)
            if signed:
                if not self.api_key or not self.api_secret:
                    raise FapiError(-2015, "需要 API key: 设置 BINANCE_API_KEY/BINANCE_API_SECRET 或写 .env", path)
                p["timestamp"] = int(time.time() * 1000) + self._time_offset
                p["recvWindow"] = self.recv_window
                query = urllib.parse.urlencode(sorted(p.items()))
                query = query + "&signature=" + self._sign(query)
            else:
                query = urllib.parse.urlencode(sorted(p.items()))
            headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
            if method == "GET":
                try:
                    return self._http(url + ("?" + query if query else ""), "GET", headers=headers)
                except FapiError as exc:
                    err = exc
            else:
                try:
                    return self._http(url, method, data=query.encode("utf-8"), headers=headers)
                except FapiError as exc:
                    err = exc
            # ---- 错误处理 ----
            if err.code == -1021 and attempt < self.retries:   # 时间戳不同步 -> 重新校准
                self.sync_time()
                attempt += 1
                continue
            if err.retryable and attempt < self.retries:
                attempt += 1
                time.sleep(self.backoff * (2 ** (attempt - 1)))
                continue
            raise err

    def sync_time(self):
        """拉服务器时间算 offset, 解决 -1021 Timestamp。"""
        server = self._http(self.base + "/fapi/v1/time", "GET")["serverTime"]
        self._time_offset = server - int(time.time() * 1000)
        return self._time_offset

    # ------------------------------------------------------------ 公共行情
    def ping(self):
        return self.request("GET", "/fapi/v1/ping")

    def server_time(self):
        return self.request("GET", "/fapi/v1/time")["serverTime"]

    def exchange_info(self, cache=True):
        """交易对规则(精度/杠杆/多空双向)。默认缓存, 但交易前建议 exchange_info(False) 刷新。"""
        if cache and getattr(self, "_exinfo", None):
            return self._exinfo
        info = self.request("GET", "/fapi/v1/exchangeInfo")
        self._exinfo = info
        self._filters = {}
        for s in info.get("symbols", []):
            f = dict((x["filterType"], x) for x in s.get("filters", []))
            self._filters[s["symbol"]] = {
                "stepSize": f.get("LOT_SIZE", {}).get("stepSize", "0.001"),
                "minQty": f.get("LOT_SIZE", {}).get("minQty", "0"),
                "maxQty": f.get("LOT_SIZE", {}).get("maxQty", "0"),
                "tickSize": f.get("PRICE_FILTER", {}).get("tickSize", "0.01"),
                "minNotional": f.get("MIN_NOTIONAL", {}).get("notional", "5"),
                "qtyPrec": s.get("quantityPrecision", 3),
                "pricePrec": s.get("pricePrecision", 2),
                "contractType": s.get("contractType", ""),
                "status": s.get("status", ""),
            }
        return info

    def symbols(self, quote="USDT", contract_type="PERPETUAL", status="TRADING"):
        """列出 USDT 永续合约交易对。"""
        info = self.exchange_info()
        out = []
        for s in info.get("symbols", []):
            if quote and s.get("quoteAsset") != quote:
                continue
            if contract_type and s.get("contractType") != contract_type:
                continue
            if status and s.get("status") != status:
                continue
            out.append(s["symbol"])
        return out

    def price(self, symbol=None):
        r = self.request("GET", "/fapi/v1/ticker/price", {"symbol": symbol} if symbol else {})
        if symbol:
            return float(r["price"])
        return dict((x["symbol"], float(x["price"])) for x in r)

    def ticker24h(self, symbol=None):
        return self.request("GET", "/fapi/v1/ticker/24hr", {"symbol": symbol} if symbol else {})

    def book_ticker(self, symbol):
        return self.request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol})

    def mark_price(self, symbol=None):
        """标记价 + 指数价 + 资金费率 + 下次结算时间。"""
        return self.request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol} if symbol else {})

    def funding_rate(self, symbol, limit=100, start_time=None, end_time=None):
        p = {"symbol": symbol, "limit": limit}
        if start_time:
            p["startTime"] = start_time
        if end_time:
            p["endTime"] = end_time
        return self.request("GET", "/fapi/v1/fundingRate", p)

    def funding_info(self):
        """全市场当前资金费率/下次结算。"""
        return self.request("GET", "/fapi/v1/fundingInfo")

    def open_interest(self, symbol):
        return self.request("GET", "/fapi/v1/openInterest", {"symbol": symbol})

    def open_interest_hist(self, symbol, period="5m", limit=30):
        return self.request("GET", "/futures/data/openInterestHist",
                            {"symbol": symbol, "period": period, "limit": limit})

    def klines(self, symbol, interval="1h", limit=100, start_time=None, end_time=None):
        p = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            p["startTime"] = start_time
        if end_time:
            p["endTime"] = end_time
        return self.request("GET", "/fapi/v1/klines", p)

    def continuous_klines(self, pair, contract_type="PERPETUAL", interval="1h", limit=100):
        return self.request("GET", "/fapi/v1/continuousKlines",
                            {"pair": pair, "contractType": contract_type, "interval": interval, "limit": limit})

    def index_price_klines(self, pair, interval="1h", limit=100):
        return self.request("GET", "/fapi/v1/indexPriceKlines", {"pair": pair, "interval": interval, "limit": limit})

    def depth(self, symbol, limit=20):
        return self.request("GET", "/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def agg_trades(self, symbol, limit=100):
        return self.request("GET", "/fapi/v1/aggTrades", {"symbol": symbol, "limit": limit})

    def basis(self, pair, contract_type="PERPETUAL", period="5m", limit=30):
        return self.request("GET", "/futures/data/basis",
                            {"pair": pair, "contractType": contract_type, "period": period, "limit": limit})

    def long_short_ratio(self, symbol, kind="global", period="1h", limit=30):
        """kind: global(全市场账户比) / top_account(大户账户比) / top_position(大户持仓比)"""
        path = {"global": "/futures/data/globalLongShortAccountRatio",
                "top_account": "/futures/data/topLongShortAccountRatio",
                "top_position": "/futures/data/topLongShortPositionRatio"}[kind]
        return self.request("GET", path, {"symbol": symbol, "period": period, "limit": limit})

    def taker_ratio(self, symbol, period="1h", limit=30):
        """主动买/卖量比 (>1 主动买占优)。"""
        return self.request("GET", "/futures/data/takerlongshortRatio",
                            {"symbol": symbol, "period": period, "limit": limit})

    # ------------------------------------------------------------ 私有/账户
    def balance(self, version="v2"):
        """合约钱包余额 (USDT 等资产, availableBalance 才是可用保证金)。"""
        return self.request("GET", "/fapi/%s/balance" % version, {}, signed=True)

    def account(self, version="v3"):
        """合约账户全量: 资产 + 持仓 + 挂单 + 手续费档位。"""
        return self.request("GET", "/fapi/%s/account" % version, {}, signed=True)

    def position_risk(self, symbol=None):
        return self.request("GET", "/fapi/v2/positionRisk",
                            {"symbol": symbol} if symbol else {}, signed=True)

    def income(self, income_type=None, symbol=None, limit=100, start_time=None):
        p = {"limit": limit}
        if income_type:
            p["incomeType"] = income_type
        if symbol:
            p["symbol"] = symbol
        if start_time:
            p["startTime"] = start_time
        return self.request("GET", "/fapi/v1/income", p, signed=True)

    def commission_rate(self, symbol):
        return self.request("GET", "/fapi/v1/commissionRate", {"symbol": symbol}, signed=True)

    def configure_leverage(self, symbol, leverage=5):
        return self.request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, signed=True)

    def configure_margin_type(self, symbol, margin_type="ISOLATED"):
        """ISOLATED / CROSSED。已是该模式会返回 -4046, 可忽略。"""
        return self.request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type}, signed=True)

    def position_mode(self, dual=None):
        if dual is None:
            return self.request("GET", "/fapi/v1/positionSide/dual", {}, signed=True)
        return self.request("POST", "/fapi/v1/positionSide/dual",
                            {"dualSidePosition": "true" if dual else "false"}, signed=True)

    def multi_assets_margin(self, enabled=None):
        if enabled is None:
            return self.request("GET", "/fapi/v1/multiAssetsMargin", {}, signed=True)
        return self.request("POST", "/fapi/v1/multiAssetsMargin",
                            {"multiAssetsMargin": "true" if enabled else "false"}, signed=True)

    def open_orders(self, symbol=None):
        return self.request("GET", "/fapi/v1/openOrders", {"symbol": symbol} if symbol else {}, signed=True)

    def all_orders(self, symbol, limit=50):
        return self.request("GET", "/fapi/v1/allOrders", {"symbol": symbol, "limit": limit}, signed=True)

    def user_trades(self, symbol, limit=50):
        return self.request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "limit": limit}, signed=True)

    # ---------------------------------------------------------------- 下单
    def order(self, symbol, side, order_type="MARKET", quantity=None, price=None,
              time_in_force=None, reduce_only=False, close_position=False,
              stop_price=None, position_side=None, client_id=None, working_type=None,
              price_protect=None, callback_rate=None, activation_price=None):
        """
        下单。quantity/price 会按 exchangeInfo 的 stepSize/tickSize 自动取整。
        side: BUY/SELL ; order_type: MARKET/LIMIT/STOP_MARKET/TAKE_PROFIT_MARKET/TRAILING_STOP_MARKET...
        """
        p = {"symbol": symbol, "side": side.upper(), "type": order_type.upper()}
        if quantity is not None:
            p["quantity"] = self.round_qty(symbol, quantity)
        if price is not None:
            p["price"] = self.round_price(symbol, price)
        if stop_price is not None:
            p["stopPrice"] = self.round_price(symbol, stop_price)
        if time_in_force:
            p["timeInForce"] = time_in_force
        if reduce_only:
            p["reduceOnly"] = "true"
        if close_position:
            p["closePosition"] = "true"
        if position_side:
            p["positionSide"] = position_side
        if client_id:
            p["newClientOrderId"] = client_id
        if working_type:
            p["workingType"] = working_type
        if price_protect is not None:
            p["priceProtect"] = "true" if price_protect else "false"
        if callback_rate is not None:
            p["callbackRate"] = callback_rate
        if activation_price is not None:
            p["activationPrice"] = self.round_price(symbol, activation_price)
        return self.request("POST", "/fapi/v1/order", p, signed=True)

    def cancel(self, symbol, order_id=None, client_id=None):
        p = {"symbol": symbol}
        if order_id:
            p["orderId"] = order_id
        if client_id:
            p["origClientOrderId"] = client_id
        return self.request("DELETE", "/fapi/v1/order", p, signed=True)

    def cancel_all(self, symbol):
        return self.request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    def query_order(self, symbol, order_id=None, client_id=None):
        p = {"symbol": symbol}
        if order_id:
            p["orderId"] = order_id
        if client_id:
            p["origClientOrderId"] = client_id
        return self.request("GET", "/fapi/v1/order", p, signed=True)

    # ------------------------------------------------------------ 精度工具
    def filters(self, symbol):
        if symbol not in self._filters:
            self.exchange_info(cache=False)
        if symbol not in self._filters:
            raise FapiError(-1121, "无效交易对 %s" % symbol, "filters")
        return self._filters[symbol]

    @staticmethod
    def _floor_to(value, step):
        v, s = Decimal(str(value)), Decimal(str(step))
        if s <= 0:
            return v
        return (v / s).to_integral_value(rounding=ROUND_DOWN) * s

    def round_qty(self, symbol, qty):
        """数量向下取整到 stepSize(向下才不会超仓/超余额)。"""
        f = self.filters(symbol)
        q = self._floor_to(qty, f["stepSize"])
        return format(q.normalize(), "f") if q != 0 else "0"

    def round_price(self, symbol, price):
        """价格取整到 tickSize(四舍五入到最近档)。"""
        f = self.filters(symbol)
        t = Decimal(str(f["tickSize"]))
        v = Decimal(str(price))
        q = (v / t).to_integral_value() * t
        return format(q.normalize(), "f")

    def check_order(self, symbol, side, qty, price=None):
        """下单前自检: 返回 (ok, [问题列表])。minNotional 按 minNotional 与当前价估算。"""
        f = self.filters(symbol)
        errs = []
        q = Decimal(str(self.round_qty(symbol, qty)))
        if q <= 0:
            errs.append("数量取整后为 0 (stepSize=%s)" % f["stepSize"])
        if q < Decimal(str(f["minQty"])):
            errs.append("数量 %s < minQty %s" % (q, f["minQty"]))
        if f["maxQty"] and Decimal(f["maxQty"]) > 0 and q > Decimal(f["maxQty"]):
            errs.append("数量 %s > maxQty %s" % (q, f["maxQty"]))
        ref = Decimal(str(price)) if price else Decimal(str(self.price(symbol)))
        notional = q * ref
        if notional < Decimal(str(f["minNotional"])):
            errs.append("名义 %s USDT < minNotional %s" % (notional, f["minNotional"]))
        return (not len(errs), errs)


# ------------------------------------------------------------------ CLI 速用
def _cli(c, cmd, argv):
    if cmd == "ping":
        c.ping()
        print("ping ok  server_time=%d  offset=%dms" % (c.server_time(), c.sync_time()))
    elif cmd == "price":
        sym = argv[2] if len(argv) > 2 else "BTCUSDT"
        print(json.dumps(c.mark_price(sym), indent=2, ensure_ascii=False))
    elif cmd == "pairs":
        syms = c.symbols()
        print("USDT-M 永续合约 %d 个:\n%s" % (len(syms), ", ".join(syms[:60]) + (" ..." if len(syms) > 60 else "")))
    elif cmd == "filters":
        print(json.dumps(c.filters(argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "balance":
        print(json.dumps(c.balance(), indent=2, ensure_ascii=False))
    else:
        print("usage: fapi_client.py [ping|price SYMBOL|pairs|filters SYMBOL|balance]")


def main():
    import sys
    c = FapiClient()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    try:
        _cli(c, cmd, sys.argv)
    except FapiError as exc:
        print("✗ %s" % exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
