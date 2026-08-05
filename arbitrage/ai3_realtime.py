#!/usr/bin/env python3
"""AI3 Kraken vs XT 实时价差监控 — 原生 Order Book"""
import urllib.request, json, time, os
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
LOG = os.path.join(os.path.dirname(__file__), "ai3_live_log.jsonl")

def kraken():
    req = urllib.request.Request("https://api.kraken.com/0/public/Ticker?pair=AI3USD",
        headers={"User-Agent": "ArbBot/1.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())["result"]["AI3USD"]
    return {"bid": float(d["b"][0]), "ask": float(d["a"][0]), "last": float(d["c"][0]),
            "vol": float(d["v"][1])}

def xt():
    req = urllib.request.Request("https://sapi.xt.com/v4/public/ticker?symbol=ai3_usdt",
        headers={"User-Agent": "ArbBot/1.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())["result"][0]
    return {"bid": float(d["bp"]), "ask": float(d["ap"]), "last": float(d["c"]),
            "vol": float(d["v"])}

def tg(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "478575303")
    body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    urllib.request.urlopen(urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"}), timeout=10)

last_alert = 0
while True:
    try:
        k = kraken(); x = xt()
        now = datetime.now(TZ).strftime("%H:%M:%S")

        k2x = (x["bid"] - k["ask"]) / k["ask"] * 100
        x2k = (k["bid"] - x["ask"]) / x["ask"] * 100

        best = max(k2x, x2k)
        direction = "Kra→XT" if k2x > x2k else "XT→Kra"

        rec = {"time": now, "k_bid": k["bid"], "k_ask": k["ask"],
               "x_bid": x["bid"], "x_ask": x["ask"],
               "k2x": round(k2x,2), "x2k": round(x2k,2)}
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")

        print(f"[{now}] K {k['bid']:.6f}/{k['ask']:.6f}  X {x['bid']:.6f}/{x['ask']:.6f}  "
              f"K→X:{k2x:+.1f}%  X→K:{x2k:+.1f}%", flush=True)

        if best > 5 and time.time() - last_alert > 1800:
            net = 5000 * best / 100 - 5000 * 0.006 - 20
            tg(f"AI3 {direction} +{best:.1f}%  净利${net:.0f} ($5K)")
            last_alert = time.time()

        time.sleep(60)
    except Exception as e:
        print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] ERR: {e}", flush=True)
        time.sleep(10)
