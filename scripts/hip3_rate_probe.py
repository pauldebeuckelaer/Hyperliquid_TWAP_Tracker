#!/usr/bin/env python3
"""
hip3_rate_probe.py — measure what each instrument would actually COST and
what it would actually BUY, before committing anything to config.py.

The subscribe question is settled: everything acks on one socket. The open
question is storage. This probe answers it with numbers instead of guesses.

For every instrument it reports:

    COST    prints/sec  ->  estimated rows/day  ->  estimated MB/day
    BUY     unique addresses seen in the window

That second column is the one that matters. The tape is your only discovery
door for wallets that trade mainnet, never TWAP, and never park capital. A
coin with 5,000 prints between 4 addresses is a closed market-making loop:
maximum cost, zero discovery. A coin with 300 prints between 120 addresses
is a door. Ranking by addresses-per-MB tells you which is which.

Run from the box with the tracker's venv:

    python hip3_rate_probe.py

Read-only. No credentials. Writes nothing to any database.
"""

import asyncio
import json
import sys
import time
from collections import defaultdict

import requests
import websockets

INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"

# How long to listen. 300s is the minimum for a believable rate; 900s is better
# if you can leave it running, because thin instruments are bursty.
LISTEN_SECONDS = 300

# Mainnet coins to measure alongside HIP-3, as a cost baseline.
# BTC and HYPE are already on your tape — they calibrate the estimate against
# real observed growth. The rest are the candidates you named.
MAINNET_COINS = ["BTC", "HYPE", "ETH", "ZEC", "FARTCOIN", "PUMP", "SOL", "XRP"]

# Bytes per row in tape_prints. THIS IS A PLACEHOLDER — replace it with your
# real number before trusting the MB/day column. You can get it from dbstat:
#
#   SELECT name, pgsize, ncell FROM dbstat WHERE name='tape_prints';
#
# or simply: (table bytes + index bytes) / row count.
BYTES_PER_ROW = 100

# Stagger between subscribe frames, to avoid tripping any rate limit.
SUBSCRIBE_DELAY = 0.05


def hr(title):
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)


def post(payload):
    r = requests.post(INFO_URL, json=payload, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {payload}: {r.text[:300]}")
    return r.json()


def collect_universe():
    """Every tradeable instrument: mainnet candidates + all HIP-3 dexes."""
    hr("BUILDING THE SUBSCRIPTION LIST")

    coins = []
    dex_of = {}

    for c in MAINNET_COINS:
        coins.append(c)
        dex_of[c] = "(mainnet)"
    print(f"  mainnet candidates: {len(MAINNET_COINS)}")

    dexs = post({"type": "perpDexs"})
    for entry in dexs:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        try:
            meta = post({"type": "meta", "dex": name})
        except Exception as e:
            print(f"  dex {name!r}: meta FAILED — {e}")
            continue
        names = [a.get("name") for a in meta.get("universe", []) if isinstance(a, dict)]
        for n in names:
            if n:
                coins.append(n)
                dex_of[n] = name
        print(f"  dex {name!r}: {len(names)} assets")

    # Dedupe, preserve order.
    seen = set()
    coins = [c for c in coins if not (c in seen or seen.add(c))]
    print(f"\n  TOTAL SUBSCRIPTIONS TO ATTEMPT: {len(coins)}")
    return coins, dex_of


async def measure(coins):
    """Subscribe to everything, listen, tally per coin."""
    hr(f"LISTENING {LISTEN_SECONDS}s")

    prints = defaultdict(int)
    notional = defaultdict(float)
    addrs = defaultdict(set)
    seconds = defaultdict(set)
    first_ts = {}
    last_ts = {}

    acked = 0
    errors = []

    async with websockets.connect(WS_URL, ping_interval=20, max_size=None) as ws:
        for c in coins:
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": c},
            }))
            await asyncio.sleep(SUBSCRIBE_DELAY)

        print(f"  sent {len(coins)} subscribe frames")

        loop = asyncio.get_event_loop()
        started = loop.time()
        deadline = started + LISTEN_SECONDS
        next_tick = started + 30

        while True:
            now = loop.time()
            if now >= deadline:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - now)
            except asyncio.TimeoutError:
                break

            msg = json.loads(raw)
            ch = msg.get("channel")

            if ch == "subscriptionResponse":
                acked += 1
                continue
            if ch == "error":
                errors.append(msg.get("data"))
                continue
            if ch != "trades":
                continue

            for t in msg.get("data", []):
                coin = t.get("coin")
                if not coin:
                    continue
                prints[coin] += 1
                try:
                    notional[coin] += float(t.get("px", 0)) * float(t.get("sz", 0))
                except (TypeError, ValueError):
                    pass
                for u in (t.get("users") or []):
                    if u:
                        addrs[coin].add(u.lower())
                ts = t.get("time")
                if ts:
                    seconds[coin].add(ts // 1000)
                    first_ts.setdefault(coin, ts)
                    last_ts[coin] = ts

            if loop.time() >= next_tick:
                elapsed = int(loop.time() - started)
                print(f"  {elapsed:>4}s  active instruments: {len(prints):>3}  "
                      f"total prints: {sum(prints.values()):>7}")
                next_tick += 30

    print(f"\n  acks received: {acked} / {len(coins)} sent")
    if acked < len(coins):
        print("  *** FEWER ACKS THAN SUBSCRIPTIONS — the socket may cap them.")
        print("      That bounds how wide one connection can go.")
    if errors:
        print(f"  errors: {len(errors)}")
        for e in errors[:10]:
            print(f"    {e}")

    return prints, notional, addrs, seconds


def report(coins, dex_of, prints, notional, addrs, seconds):
    hr("RESULTS — ranked by unique addresses (the discovery metric)")

    rows = []
    for coin in coins:
        n = prints.get(coin, 0)
        if n == 0:
            continue
        rate = n / LISTEN_SECONDS
        rows_day = rate * 86400
        mb_day = rows_day * BYTES_PER_ROW / 1_000_000
        uniq = len(addrs.get(coin, ()))
        active_sec = len(seconds.get(coin, ()))
        # Prints per ACTIVE second: a fixed-clock quoting venue sits near 1.0
        # and never moves. Organic flow is lumpy and reads higher.
        per_active = n / active_sec if active_sec else 0
        clip = notional.get(coin, 0) / n if n else 0
        addr_per_mb = uniq / mb_day if mb_day > 0 else 0
        rows.append((coin, dex_of.get(coin, "?"), n, rate, uniq, per_active,
                     clip, notional.get(coin, 0), rows_day, mb_day, addr_per_mb))

    rows.sort(key=lambda r: r[4], reverse=True)

    hdr = (f"{'coin':<20}{'dex':<10}{'prints':>8}{'p/s':>8}{'addrs':>7}"
           f"{'p/act_s':>9}{'avg_clip':>12}{'rows/day':>12}{'MB/day':>9}{'addr/MB':>9}")
    print(hdr)
    print("-" * len(hdr))
    for (coin, dex, n, rate, uniq, per_active, clip, notl, rows_day, mb_day, apm) in rows:
        print(f"{coin:<20}{dex:<10}{n:>8}{rate:>8.2f}{uniq:>7}"
              f"{per_active:>9.2f}{clip:>12,.0f}{rows_day:>12,.0f}{mb_day:>9.1f}{apm:>9.1f}")

    silent = [c for c in coins if prints.get(c, 0) == 0]
    print(f"\nSILENT in the window ({len(silent)}): {silent[:40]}")
    if len(silent) > 40:
        print(f"  ... and {len(silent) - 40} more")

    hr("TOTALS")
    total_rows = sum(r[8] for r in rows)
    total_mb = sum(r[9] for r in rows)
    hip3 = [r for r in rows if r[1] != "(mainnet)"]
    main = [r for r in rows if r[1] == "(mainnet)"]
    print(f"  everything:       {total_rows:>14,.0f} rows/day   {total_mb:>9.1f} MB/day")
    print(f"  HIP-3 only:       {sum(r[8] for r in hip3):>14,.0f} rows/day   "
          f"{sum(r[9] for r in hip3):>9.1f} MB/day   ({len(hip3)} instruments)")
    print(f"  mainnet only:     {sum(r[8] for r in main):>14,.0f} rows/day   "
          f"{sum(r[9] for r in main):>9.1f} MB/day   ({len(main)} instruments)")
    print(f"\n  perp_snapshots for comparison: ~2,500,000 rows/day")
    print(f"  BYTES_PER_ROW is currently {BYTES_PER_ROW} — a guess. Replace it.")

    hr("HOW TO READ THIS")
    print("  p/act_s near 1.00 and flat  -> fixed-clock quoting venue, not organic flow")
    print("  many prints, few addrs      -> closed MM loop: pays storage, buys nothing")
    print("  few prints, many addrs      -> a discovery door: cheap and valuable")
    print("  high addr/MB                -> what you want at the top of the list")
    print()
    print("  CAVEAT: a 5-minute window undercounts addresses on slow instruments.")
    print("  A coin touched by 3 wallets here may see 200 across a day. Treat the")
    print("  address column as a floor, and re-run longer before cutting the tail.")


def main():
    coins, dex_of = collect_universe()
    prints, notional, addrs, seconds = asyncio.run(measure(coins))
    report(coins, dex_of, prints, notional, addrs, seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)