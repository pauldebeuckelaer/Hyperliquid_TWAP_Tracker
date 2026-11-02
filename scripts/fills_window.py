#!/usr/bin/env python3
"""Pull every fill a wallet made in a time window, across all dexes, and summarise.

Source: Hyperliquid info API, userFillsByTime (the exchange's own record, so it
covers coins the tape does not subscribe to and wallets the analyzer never tiers).

Usage (run from repo root):
    python3 scripts/fills_window.py <address> [--w0 2026-10-30T00:00] [--w1 2026-11-02T18:00] [--coin xyz:BRENTOIL]

--w0 / --w1 take a UTC time (YYYY-MM-DDTHH:MM) or epoch ms. Default: Fri Oct 30 00:00 UTC -> now.
--coin limits the hourly breakdown to one coin (the summary always shows all coins).
Raw fills are saved to data/fills_<addr8>_<w0>.json so a run can be re-read without the API.

Control: for 0xde8a6d58 the xyz:BRENTOIL line must show 18,143 sold in Sat 10-31 16:00,
matching the tape. If BRENTOIL is missing, this endpoint is not returning HIP-3 fills
and the other coins' numbers cannot be trusted as complete either.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict

API = "https://api.hyperliquid.xyz/info"
PAGE_LIMIT = 2000  # API returns at most this many fills per call


def to_ms(s):
    if s.isdigit():
        return int(s)
    t = datetime.datetime.fromisoformat(s)
    return int(t.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def fmt_ms(ms, f="%m-%d %H:%M"):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime(f)


def post(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_fills(addr, w0, w1):
    """Page forward through userFillsByTime until the window is covered."""
    fills, seen, start = [], set(), w0
    while start <= w1:
        page = post({"type": "userFillsByTime", "user": addr,
                     "startTime": start, "endTime": w1, "aggregateByTime": False})
        if not page:
            break
        new = [f for f in page if (f["tid"], f["coin"]) not in seen]
        for f in new:
            seen.add((f["tid"], f["coin"]))
        fills.extend(new)
        if len(page) < PAGE_LIMIT or not new:
            break
        start = max(f["time"] for f in page)  # same-ms fills dedup via tid
        time.sleep(0.3)
    fills.sort(key=lambda f: f["time"])
    return fills


def summarise(fills):
    by_coin = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "buy_ntl": 0.0, "sell_ntl": 0.0,
                                   "taker": 0.0, "pnl": 0.0, "fee": 0.0, "n": 0,
                                   "first": None, "last": None, "start_pos": None, "end_pos": None})
    for f in fills:
        c = by_coin[f["coin"]]
        sz, px = float(f["sz"]), float(f["px"])
        signed = sz if f["side"] == "B" else -sz
        if f["side"] == "B":
            c["buy"] += sz; c["buy_ntl"] += sz * px
        else:
            c["sell"] += sz; c["sell_ntl"] += sz * px
        if f.get("crossed"):
            c["taker"] += sz
        c["pnl"] += float(f.get("closedPnl", 0) or 0)
        c["fee"] += float(f.get("fee", 0) or 0)
        c["n"] += 1
        if c["first"] is None:
            c["first"] = f["time"]
            c["start_pos"] = float(f["startPosition"])
        c["last"] = f["time"]
        c["end_pos"] = float(f["startPosition"]) + signed
    return by_coin


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("address")
    ap.add_argument("--w0", default="2026-10-30T00:00")
    ap.add_argument("--w1", default=None, help="default: now")
    ap.add_argument("--coin", default=None)
    a = ap.parse_args()

    addr = a.address.lower()
    if len(addr) != 42:
        sys.exit("need the full 42-char address (the API does not take prefixes)")
    w0 = to_ms(a.w0)
    w1 = to_ms(a.w1) if a.w1 else int(time.time() * 1000)

    fills = fetch_fills(addr, w0, w1)
    os.makedirs("data", exist_ok=True)
    out = f"data/fills_{addr[2:10]}_{fmt_ms(w0, '%Y%m%dT%H%M')}.json"
    with open(out, "w") as fh:
        json.dump(fills, fh)

    print(addr)
    print(f"window {fmt_ms(w0)} -> {fmt_ms(w1)} UTC | {len(fills)} fills | raw saved to {out}")
    if not fills:
        print("no fills in window")
        return

    s = summarise(fills)
    print(f"\n{'coin':<16}{'fills':>6}{'bought':>13}{'sold':>13}{'buy vwap':>11}{'sell vwap':>11}"
          f"{'taker%':>8}{'pos start':>13}{'pos end':>13}{'closedPnl':>12}  first -> last")
    for coin, c in sorted(s.items(), key=lambda kv: -(kv[1]["buy_ntl"] + kv[1]["sell_ntl"])):
        bv = c["buy_ntl"] / c["buy"] if c["buy"] else 0
        sv = c["sell_ntl"] / c["sell"] if c["sell"] else 0
        tk = 100 * c["taker"] / (c["buy"] + c["sell"])
        print(f"{coin:<16}{c['n']:>6}{c['buy']:>13,.2f}{c['sell']:>13,.2f}{bv:>11.4f}{sv:>11.4f}"
              f"{tk:>7.0f}%{c['start_pos']:>13,.2f}{c['end_pos']:>13,.2f}{c['pnl']:>12,.0f}"
              f"  {fmt_ms(c['first'])} -> {fmt_ms(c['last'])}")

    coins = [a.coin] if a.coin else sorted(s)
    print("\nhourly (UTC): coin | hour | bought / sold | taker | position after hour")
    for coin in coins:
        hours, pos = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]), None
        for f in fills:
            if f["coin"] != coin:
                continue
            h = hours[fmt_ms(f["time"], "%a %m-%d %H:00")]
            sz = float(f["sz"])
            h[0 if f["side"] == "B" else 1] += sz
            if f.get("crossed"):
                h[2] += sz
            h[3] = float(f["startPosition"]) + (sz if f["side"] == "B" else -sz)
        for hr, (b, sl, tk, p) in hours.items():
            print(f"  {coin:<14} {hr}  {b:>12,.2f} / {sl:<12,.2f} taker {tk:>10,.2f}  pos {p:>12,.2f}")


if __name__ == "__main__":
    main()