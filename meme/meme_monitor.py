#!/usr/bin/env python3
"""
Meme Coin 新池子监控 — Telegram 告警推送
==========================================
独立进程，定时轮询 DEX Screener API，发现新池子后推送 Telegram。

用法:
  方式1 - 环境变量:
    export TELEGRAM_BOT_TOKEN="123:abc"
    export TELEGRAM_CHAT_ID="@mychannel"
    python3 meme_monitor.py

  方式2 - 命令行:
    python3 meme_monitor.py --token "123:abc" --chat "@mychannel"

可选参数:
  --interval  轮询间隔秒数 (默认 60)
  --min-liq   最小流动性过滤 (USD, 默认 0 不过滤)
  --chain     链过滤 (ethereum, solana, bsc, base, 默认全部)
  --once      只运行一次就退出
"""

import os, sys, json, time, argparse, urllib.request
from datetime import datetime, timezone

# ── 配置 ──────────────────────────────────────

TELEGRAM_API = "https://api.telegram.org"
DEX_API = "https://api.dexscreener.com/latest/dex"

class Config:
    def __init__(self):
        parser = argparse.ArgumentParser(description="Meme Coin Pool Monitor")
        parser.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        parser.add_argument("--chat", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--min-liq", type=float, default=0,
                          help="最小流动性(USD), 0=不过滤")
        parser.add_argument("--chain", default="",
                          help="链过滤: ethereum,solana,bsc,base")
        parser.add_argument("--once", action="store_true")
        args = parser.parse_args()

        self.bot_token = args.token
        self.chat_id = args.chat
        self.interval = args.interval
        self.min_liquidity = args.min_liq
        self.chain = args.chain.lower() if args.chain else ""
        self.once = args.once

        if not self.bot_token or not self.chat_id:
            print("ERROR: 需要 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID", file=sys.stderr)
            print("  export TELEGRAM_BOT_TOKEN='123:abc'", file=sys.stderr)
            print("  export TELEGRAM_CHAT_ID='@mychannel'", file=sys.stderr)
            sys.exit(1)

# ── Telegram ──────────────────────────────────

def send_telegram(token: str, chat: str, text: str, silent: bool = False):
    """发送 Telegram 消息"""
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    body = json.dumps({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": silent,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception as e:
        print(f"[!] Telegram send error: {e}", file=sys.stderr)
        return None

def format_alert(pool: dict) -> str:
    """格式化告警消息"""
    base = pool.get("baseToken", {})
    chain = pool.get("chainId", "?")
    dex = pool.get("dexId", "?")
    price = float(pool.get("priceUsd", 0))
    liq = pool.get("liquidity", {}).get("usd", 0) or 0
    vol = pool.get("volume", {}).get("h24", 0) or 0
    created = pool.get("pairCreatedAt", 0)
    url = pool.get("url", "")

    age = "未知"
    if created:
        delta = datetime.now(timezone.utc).timestamp() * 1000 - created
        mins = int(delta / 60000)
        if mins < 60:
            age = f"{mins} 分钟前"
        elif mins < 1440:
            age = f"{mins//60} 小时前"
        else:
            age = f"{mins//1440} 天前"

    price_str = f"{price:.10f}".rstrip("0").rstrip(".")
    liq_str = f"{liq:,.0f}" if liq >= 1 else f"{liq:.2f}"
    vol_str = f"{vol:,.0f}" if vol >= 1 else f"{vol:.2f}"

    return "\n".join([
        f"🆕 <b>新池子</b>",
        f"",
        f"<b>代币:</b> {base.get('name','?')} (${base.get('symbol','?')})",
        f"<b>链:</b> {chain} | <b>DEX:</b> {dex}",
        f"<b>价格:</b> ${price_str}",
        f"<b>流动性:</b> ${liq_str}",
        f"<b>24h量:</b> ${vol_str}",
        f"<b>创建:</b> {age}",
        f"",
        f'<a href="{url}">🔗 DEX Screener</a>',
    ])

# ── DEX 轮询 ──────────────────────────────────

def fetch_new_pools(config: Config):
    """从 DEX Screener 获取最新池子"""
    url = f"{DEX_API}/pairs?rankBy=created"
    req = urllib.request.Request(url, headers={"User-Agent": "MemeMonitor/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
    except Exception as e:
        print(f"[!] API error: {e}", file=sys.stderr)
        return []

    pairs = data.get("pairs", []) or []
    results = []
    for p in pairs:
        liq = p.get("liquidity", {}).get("usd", 0) or 0
        chain = p.get("chainId", "")

        if config.min_liquidity > 0 and liq < config.min_liquidity:
            continue
        if config.chain and chain.lower() != config.chain:
            continue

        results.append(p)
    return results

# ── 主循环 ────────────────────────────────────

def main():
    config = Config()
    seen = set()  # 已推送的池子地址

    print(f"[+] Meme Monitor started")
    print(f"    chat: {config.chat_id}")
    print(f"    interval: {config.interval}s, min_liq: ${config.min_liquidity}")
    if config.chain:
        print(f"    chain: {config.chain}")
    print()

    # 启动消息
    send_telegram(config.bot_token, config.chat_id,
        "🚀 <b>Meme Monitor 已启动</b>\n\n"
        f"轮询间隔: {config.interval}s | 最小流动性: ${config.min_liquidity:,.0f}"
        + (f" | 链: {config.chain}" if config.chain else " | 链: 全部"),
        silent=True
    )

    while True:
        try:
            pools = fetch_new_pools(config)
            new_count = 0
            for p in pools:
                addr = p.get("pairAddress", "")
                if addr and addr not in seen:
                    seen.add(addr)
                    msg = format_alert(p)
                    result = send_telegram(config.bot_token, config.chat_id, msg)
                    if result and result.get("ok"):
                        new_count += 1
                    # 限速：每秒最多发 2 条
                    time.sleep(0.5)

            if new_count > 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 推送 {new_count} 个新池子, "
                      f"已追踪 {len(seen)} 个")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 无新池子, "
                      f"已追踪 {len(seen)} 个", end="\r")

            if config.once:
                break

            time.sleep(config.interval)

        except KeyboardInterrupt:
            print("\n[+] 已停止")
            send_telegram(config.bot_token, config.chat_id,
                "🛑 <b>Meme Monitor 已停止</b>", silent=True)
            break
        except Exception as e:
            print(f"\n[!] Error: {e}", file=sys.stderr)
            time.sleep(config.interval)

if __name__ == "__main__":
    main()
