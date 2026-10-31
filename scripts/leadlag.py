#!/usr/bin/env python3
"""leadlag.py - which venue printed a price drop first: Binance or Hyperliquid.

Rule (frozen for Bet 4 before looking at the data):
  - Each venue gets its own reference: its last price at --w0.
  - For each drop level (default -0.3%, -0.6%, -0.9%), find the first moment
    each venue traded at or below reference * (1 - level).
      Binance: 1-second klines (BTCUSDT spot), first second whose LOW reaches it.
      Hyperliquid: tape_prints, first print at or below it (ms resolution).
  - A venue "leads" a level if it got there more than --lead seconds earlier.
    Otherwise that level is "simultaneous".
  - Majority of the levels decides.

Usage (run from repo root):
    python3 scripts/leadlag.py
    python3 scripts/leadlag.py --w0 2026-10-30T18:45 --w1 2026-10-30T19:30 --levels 0.3,0.6,0.9
"""
import sys
import json
import sqlite3
import argparse
import datetime
import urllib.request
from collections import Counter

BINANCE = 'https://api.binance.com/api/v3/klines'


def to_ms(s):
    if s.isdigit():
        return int(s)
    t = datetime.datetime.fromisoformat(s)
    return int(t.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def hms(ms):
    if ms is None:
        return 'never'
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%H:%M:%S')


def binance_1s(symbol, w0, w1):
    """All 1s klines in [w0, w1) as (open_ms, open, low, close)."""
    out, start = [], w0
    while start < w1:
        url = f'{BINANCE}?symbol={symbol}&interval=1s&startTime={start}&endTime={w1 - 1}&limit=1000'
        rows = json.load(urllib.request.urlopen(url, timeout=15))
        if not rows:
            break
        out += [(r[0], float(r[1]), float(r[3]), float(r[4])) for r in rows]
        start = rows[-1][0] + 1000
    return out


def first_cross(series, thr):
    """series: [(ms, low)], time-ordered. First ms with low <= thr."""
    for ms, low in series:
        if low <= thr:
            return ms
    return None


def verdict(b_ms, h_ms, lead_s):
    if b_ms is None and h_ms is None:
        return 'neither reached'
    if h_ms is None:
        return 'Binance first'
    if b_ms is None:
        return 'Hyperliquid first'
    d = (h_ms - b_ms) / 1000
    if d > lead_s:
        return 'Binance first'
    if d < -lead_s:
        return 'Hyperliquid first'
    return 'Simultaneous'


def main():
    ap = argparse.ArgumentParser(description='Binance vs Hyperliquid: who printed the drop first.')
    ap.add_argument('--w0', default='2026-10-30T18:45', help='window start, UTC time or epoch ms')
    ap.add_argument('--w1', default='2026-10-30T19:30', help='window end, UTC time or epoch ms')
    ap.add_argument('--levels', default='0.3,0.6,0.9', help='drop levels in %%, comma separated')
    ap.add_argument('--lead', type=float, default=30, help='seconds needed to count as leading')
    ap.add_argument('--symbol', default='BTCUSDT', help='Binance spot symbol')
    ap.add_argument('--coin', default='BTC', help='Hyperliquid tape coin')
    a = ap.parse_args()
    w0, w1 = to_ms(a.w0), to_ms(a.w1)
    levels = [float(x) for x in a.levels.split(',')]

    # --- Binance -------------------------------------------------------------
    kl = binance_1s(a.symbol, w0, w1)
    if not kl:
        sys.exit('no Binance klines returned for that window')
    b_ref = kl[0][1]                       # open of the first second = last price at w0
    b_series = [(ms, low) for ms, _, low, _ in kl]

    # --- Hyperliquid ---------------------------------------------------------
    db = sqlite3.connect('file:data/twap.db?mode=ro', uri=True)
    row = db.execute("SELECT px FROM tape_prints WHERE coin=? AND ts<? ORDER BY ts DESC LIMIT 1",
                     (a.coin, w0)).fetchone()
    if not row:
        sys.exit('no Hyperliquid print before w0')
    h_ref = row[0]
    h_series = db.execute("SELECT ts, px FROM tape_prints WHERE coin=? AND ts>=? AND ts<? ORDER BY ts",
                          (a.coin, w0, w1)).fetchall()

    print(f"window {hms(w0)} -> {hms(w1)} UTC | Binance {a.symbol} {len(kl)} seconds | "
          f"Hyperliquid {a.coin} {len(h_series)} prints")
    print(f"reference: Binance {b_ref:,.2f} | Hyperliquid {h_ref:,.1f}\n")

    # --- per level -----------------------------------------------------------
    print(f"{'level':>6} | {'Binance thr':>11} {'first':>8} | {'HL thr':>9} {'first':>8} | "
          f"{'HL - Bin':>8} | verdict")
    votes = []
    for lv in levels:
        bt, ht = b_ref * (1 - lv / 100), h_ref * (1 - lv / 100)
        b_ms, h_ms = first_cross(b_series, bt), first_cross(h_series, ht)
        v = verdict(b_ms, h_ms, a.lead)
        votes.append(v)
        d = f"{(h_ms - b_ms) / 1000:+.0f}s" if (b_ms and h_ms) else '-'
        print(f"{-lv:>5.1f}% | {bt:>11,.1f} {hms(b_ms):>8} | {ht:>9,.1f} {hms(h_ms):>8} | {d:>8} | {v}")

    top, n = Counter(votes).most_common(1)[0]
    result = top if n > len(levels) / 2 else 'no majority'
    print(f"\nRESULT (majority of {len(levels)}): {result}")

    # --- context: per-minute low, % from own reference ------------------------
    print('\nper-minute low, % from own reference (Binance | Hyperliquid):')
    bmin, hmin = {}, {}
    for ms, low in b_series:
        k = ms // 60000
        bmin[k] = min(bmin.get(k, low), low)
    for ms, px in h_series:
        k = ms // 60000
        hmin[k] = min(hmin.get(k, px), px)
    for k in sorted(set(bmin) | set(hmin)):
        b = f"{(bmin[k] / b_ref - 1) * 100:+.2f}%" if k in bmin else '   -  '
        h = f"{(hmin[k] / h_ref - 1) * 100:+.2f}%" if k in hmin else '   -  '
        print(f"  {hms(k * 60000)[:5]}   {b:>7} | {h:>7}")


if __name__ == '__main__':
    main()