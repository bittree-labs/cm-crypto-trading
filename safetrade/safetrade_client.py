#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SafeTrade (safe.trade) REST API v2 客户端 —— BitTree 卖币/做市用

接口文档（官方 Swagger，需要过 Cloudflare）:
    https://safe.trade/api/v2/trade/public/swagger.json
签名方式（与官方 example-client 一致）:
    X-Auth-Apikey    : API Key
    X-Auth-Nonce     : 毫秒时间戳字符串
    X-Auth-Signature : hex( HMAC_SHA256(secret, nonce + key) )

重要工程约束：
    - safe.trade 前置 Cloudflare，纯 requests/urllib 的 TLS 指纹会被 403，
      必须用 curl_cffi 的浏览器指纹（impersonate="safari17_0"）。本客户端
      优先 curl_cffi，缺失时才退回 requests（会警告，可能 403）。
    - 官网 safetrade.com（非 api 子域）连无头浏览器都会被 CF 拦，不要用网页自动化，
      走本 API。

用法：
    python3 safetrade_client.py ticker prlusdt
    python3 safetrade_client.py depth prlusdt 10
    python3 safetrade_client.py fees
    python3 safetrade_client.py markets | grep -i prl
    python3 safetrade_client.py me          # 需 .env 中的 API Key
    python3 safetrade_client.py balance     # 需 API Key，打印各币种可用/冻结
    python3 safetrade_client.py orders
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import hmac
import json
import os
import sys
import time
from decimal import Decimal

BASE_URL = "https://safe.trade/api/v2"
IMPERSONATE = "safari17_0"

try:  # 首选：带浏览器 TLS 指纹
    from curl_cffi import requests as _http  # type: ignore
    _HAS_CURL_CFFI = True
except ImportError:  # 退回
    import requests as _http  # type: ignore
    _HAS_CURL_CFFI = False


def load_env(path: str | None = None) -> None:
    """从 safetrade/.env 读取 SAFETRADE_API_KEY / SAFETRADE_API_SECRET（不存在则跳过）。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class SafeTradeError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body


class SafeTrade:
    """SafeTrade REST API v2 最小可用客户端。"""

    def __init__(self, key: str | None = None, secret: str | None = None,
                 base_url: str = BASE_URL, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.key = key if key is not None else os.environ.get("SAFETRADE_API_KEY", "")
        self.secret = secret if secret is not None else os.environ.get("SAFETRADE_API_SECRET", "")
        self.timeout = timeout
        if not _HAS_CURL_CFFI:
            print("[warn] 未安装 curl_cffi，退回 requests；Cloudflare 可能返回 403。"
                  "请执行: pip install curl_cffi", file=sys.stderr)

    # ---------- 底层 ----------
    def _request(self, method: str, path: str, params=None, body=None, auth: bool = False):
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json;charset=utf-8"
        if auth:
            if not self.key or not self.secret:
                raise SafeTradeError(0, "缺少 API Key/Secret：请在 safetrade/.env 配置 "
                                        "SAFETRADE_API_KEY / SAFETRADE_API_SECRET")
            nonce = str(int(time.time() * 1000))
            mac = hmac.new(self.secret.encode(), digestmod=hashlib.sha256)
            mac.update((nonce + self.key).encode())
            headers.update({
                "X-Auth-Apikey": self.key,
                "X-Auth-Nonce": nonce,
                "X-Auth-Signature": binascii.hexlify(mac.digest()).decode(),
            })
        kwargs = {"headers": headers, "timeout": self.timeout}
        if _HAS_CURL_CFFI:
            kwargs["impersonate"] = IMPERSONATE
        if params:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body
        resp = _http.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise SafeTradeError(resp.status_code, resp.text)
        if not resp.text:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ---------- 公共行情 ----------
    def markets(self):
        return self._request("GET", "/trade/public/markets")

    def market(self, market_id: str):
        return self._request("GET", f"/trade/public/markets/{market_id.lower()}")

    def tickers(self):
        return self._request("GET", "/trade/public/tickers")

    def ticker(self, market_id: str):
        return self._request("GET", f"/trade/public/tickers/{market_id.lower()}")

    def depth(self, market_id: str, limit: int = 20):
        return self._request("GET", f"/trade/public/markets/{market_id.lower()}/depth",
                             params={"limit": limit})

    def market_trades(self, market_id: str, limit: int = 20):
        return self._request("GET", f"/trade/public/markets/{market_id.lower()}/trades",
                             params={"limit": limit})

    def trading_fees(self):
        return self._request("GET", "/trade/public/trading_fees")

    # ---------- 私有（需签名） ----------
    def me(self):
        return self._request("GET", "/trade/account/members/me", auth=True)

    def balances_spot(self):
        """[{"currency":"prl","balance":..,"locked":..,"available":..}, ...]"""
        return self._request("GET", "/trade/account/balances/spot", auth=True)

    def balance_of(self, currency: str) -> dict:
        cur = currency.lower()
        for row in self.balances_spot() or []:
            if str(row.get("currency", "")).lower() == cur:
                return row
        return {"currency": cur, "balance": 0, "locked": 0, "available": 0}

    def orders(self, market: str | None = None, state=None, limit: int = 100):
        params: dict = {"limit": limit, "page": 1}
        if market:
            params["market"] = market.lower()
        if state:
            params["state"] = state if isinstance(state, str) else ",".join(state)
        return self._request("GET", "/trade/market/orders", params=params, auth=True)

    def order(self, order_id):
        return self._request("GET", f"/trade/market/orders/{order_id}", auth=True)

    def create_order(self, market: str, side: str, amount, price=None, order_type: str | None = None):
        """下单。price 省略 => market；给出 => limit。amount 为 base 币数量（PRL 枚数）。"""
        body = {"market": market.lower(), "side": side.lower(),
                "amount": float(amount),
                "type": order_type or ("limit" if price is not None else "market")}
        if price is not None:
            body["price"] = float(price)
        return self._request("POST", "/trade/market/orders", body=body, auth=True)

    def cancel_order(self, order_id):
        return self._request("POST", f"/trade/market/orders/{order_id}/cancel", body={}, auth=True)

    def trades(self, market: str | None = None, limit: int = 50):
        params: dict = {"limit": limit, "page": 1}
        if market:
            params["market"] = market.lower()
        return self._request("GET", "/trade/market/trades", params=params, auth=True)

    # ---------- 便捷 ----------
    def best_bid_ask(self, market_id: str, depth_ok: int = 5):
        d = self.depth(market_id, limit=depth_ok) or {}
        bids = d.get("bids") or []
        asks = d.get("asks") or []
        bid = Decimal(str(bids[0][0])) if bids else None
        ask = Decimal(str(asks[0][0])) if asks else None
        return bid, ask, d

    def depth_walk(self, market_id: str, side: str, limit: int = 100):
        """按档位累加，返回 [(price, cum_amount)]，用于估算吃掉多少量会打到什么价。"""
        d = self.depth(market_id, limit=limit) or {}
        levels = d.get(side) or []
        out, cum = [], Decimal("0")
        for p, a in levels:
            cum += Decimal(str(a))
            out.append((Decimal(str(p)), cum))
        return out


def _cmd(args):
    c = SafeTrade(timeout=20)
    if args.cmd == "ticker":
        print(json.dumps(c.ticker(args.market), indent=1, ensure_ascii=False))
    elif args.cmd == "depth":
        print(json.dumps(c.depth(args.market, args.limit), indent=1, ensure_ascii=False))
    elif args.cmd == "markets":
        for m in c.markets():
            if args.filter.lower() in m["id"]:
                print(f"{m['id']:12} {m['name']:12} state={m['state']:9} "
                      f"amount_prec={m['amount_precision']} price_prec={m['price_precision']} "
                      f"min_amount={m['min_amount']}")
    elif args.cmd == "fees":
        print(json.dumps(c.trading_fees(), indent=1, ensure_ascii=False))
    elif args.cmd == "me":
        print(json.dumps(c.me(), indent=1, ensure_ascii=False))
    elif args.cmd == "balance":
        for row in sorted(c.balances_spot() or [], key=lambda r: str(r.get("currency"))):
            if Decimal(str(row.get("balance") or 0)) > 0:
                print(f"{row.get('currency'):>8}  balance={row.get('balance')} "
                      f"locked={row.get('locked')} available={row.get('available')}")
    elif args.cmd == "orders":
        print(json.dumps(c.orders(market=args.market), indent=1, ensure_ascii=False))


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(description="SafeTrade API v2 客户端")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("ticker"); t.add_argument("market", nargs="?", default="prlusdt")
    d = sub.add_parser("depth"); d.add_argument("market", nargs="?", default="prlusdt")
    d.add_argument("limit", nargs="?", type=int, default=20)
    m = sub.add_parser("markets"); m.add_argument("filter", nargs="?", default="prl")
    sub.add_parser("fees")
    sub.add_parser("me")
    sub.add_parser("balance")
    o = sub.add_parser("orders"); o.add_argument("market", nargs="?", default=None)
    args = p.parse_args(argv)
    try:
        _cmd(args)
    except SafeTradeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
