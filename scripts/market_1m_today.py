#!/usr/bin/env python3
"""Pull today's 1-minute candles for every live Hyperliquid perp (main dex).

Run on the box:  python3 market_1m_today.py  -> writes market_1m_YYYY-MM-DD.csv
Stdlib only. Paced to stay well under the 1200 weight/min info limit.
"""
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

URL = "https://api.hyperliquid.xyz/info"
SLEEP_S = 1.2  # ~50 calls/min


def post(body):
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main():
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    start_ms = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    universe = post({"type": "meta"})["universe"]
    coins = [u["name"] for u in universe if not u.get("isDelisted")]
    print(f"{len(coins)} live perps, pulling 1m candles since {day} 00:00 UTC", file=sys.stderr)

    out = f"market_1m_{day}.csv"
    failed = []
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["coin", "minute_utc", "open", "high", "low", "close", "volume_coin", "n_trades"])
        for i, coin in enumerate(coins, 1):
            try:
                candles = post({
                    "type": "candleSnapshot",
                    "req": {"coin": coin, "interval": "1m", "startTime": start_ms, "endTime": end_ms},
                })
                for c in candles:
                    minute = datetime.fromtimestamp(c["t"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
                    w.writerow([coin, minute, c["o"], c["h"], c["l"], c["c"], c["v"], c["n"]])
            except Exception as e:  # keep going; report at the end
                failed.append((coin, str(e)[:80]))
            if i % 25 == 0:
                print(f"  {i}/{len(coins)}", file=sys.stderr)
            time.sleep(SLEEP_S)

    print(f"wrote {out}", file=sys.stderr)
    if failed:
        print(f"{len(failed)} coins failed:", file=sys.stderr)
        for coin, err in failed:
            print(f"  {coin}: {err}", file=sys.stderr)


if __name__ == "__main__":
    main()