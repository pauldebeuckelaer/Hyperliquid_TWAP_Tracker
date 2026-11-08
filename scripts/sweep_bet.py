#!/usr/bin/env python3
"""Frozen bet (Nov 8 2026): do swing-low sweeps that fire many stops bounce more
than sweeps that fire few?  Paul = same (within 5 pts).  Claude = A beats B.

Read-only on both databases.  Standard library only.
Run from ~/bots/Hyperliquid_TWAP_Analyzer:  python3 sweep_bet.py
"""
import argparse
import math
import sqlite3
from array import array
from bisect import bisect_right

BAR_MS = 5 * 60_000
SWING_SIDE = 3            # candles each side of a swing low
SWEEP_LIMIT_MS = 4 * 3_600_000
SPACING_MS = 30 * 60_000  # sweeps closer than this count once
STOP_WINDOW_MS = 60_000   # stops counted from sweep to sweep + 60s
LOW_WINDOW_MS = 30 * 60_000  # bounce measured from the lowest print in this window
TARGET = 0.003
RACE_LIMIT_MS = 4 * 3_600_000
SAME_PTS = 5.0
MIN_EVENTS = 30


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def load_prints(db, coin, t0, t1):
    ts, px = array("q"), array("d")
    q = "SELECT ts, px FROM tape_prints WHERE coin=? AND ts BETWEEN ? AND ? ORDER BY ts"
    for t, p in db.execute(q, (coin, t0, t1)):
        ts.append(t)
        px.append(p)
    return ts, px


def build_candles(ts, px):
    """Each candle: [bucket, low, high, first_print, last_print_exclusive]."""
    candles = []
    for i, (t, p) in enumerate(zip(ts, px)):
        b = t // BAR_MS
        if not candles or candles[-1][0] != b:
            candles.append([b, p, p, i, i + 1])
        else:
            c = candles[-1]
            c[1] = min(c[1], p)
            c[2] = max(c[2], p)
            c[4] = i + 1
    return candles


def find_sweeps(ts, px, candles):
    sweeps = []
    side = SWING_SIDE
    for c in range(side, len(candles) - side):
        if candles[c + side][0] - candles[c - side][0] != 2 * side:
            continue  # hole in the tape around this candle
        low = candles[c][1]
        if not all(low < candles[k][1] for k in range(c - side, c + side + 1) if k != c):
            continue
        confirmed = (candles[c + side][0] + 1) * BAR_MS
        limit = confirmed + SWEEP_LIMIT_MS
        for j in range(c + side + 1, len(candles)):
            if candles[j][0] * BAR_MS >= limit:
                break
            if candles[j][1] < low:
                for i in range(candles[j][3], candles[j][4]):
                    if px[i] < low:
                        sweeps.append(ts[i])
                        break
                break
    sweeps.sort()
    kept = []
    for t in sweeps:
        if not kept or t - kept[-1] >= SPACING_MS:
            kept.append(t)
    return kept


def race(ts, px, candles, by_bucket, sweep_ts):
    """True = +0.3% first, False = -0.3% first, None = neither inside 4h or no data."""
    first = bisect_right(ts, sweep_ts - 1)
    last = bisect_right(ts, sweep_ts + LOW_WINDOW_MS)
    idx = min(range(first, last), key=px.__getitem__)
    low = px[idx]
    up, down = low * (1 + TARGET), low * (1 - TARGET)
    end = ts[idx] + RACE_LIMIT_MS
    if ts[-1] < end:
        return None  # data stops before the race could finish
    i = idx + 1
    while i < len(ts) and ts[i] <= end:
        candle = candles[by_bucket[ts[i] // BAR_MS]]
        if i == candle[3] and candle[1] > down and candle[2] < up:
            i = candle[4]
            continue
        if px[i] >= up:
            return True
        if px[i] <= down:
            return False
        i += 1
    return None


def rate(group):
    return 100.0 * sum(group) / len(group) if group else float("nan")


def verdict(a, b):
    if len(a) < MIN_EVENTS or len(b) < MIN_EVENTS:
        return f"CAN'T TELL YET (need {MIN_EVENTS}+ events in both A and B)"
    diff = rate(a) - rate(b)
    pa, pb = rate(a) / 100, rate(b) / 100
    half = 196 * math.sqrt(pa * (1 - pa) / len(a) + pb * (1 - pb) / len(b))
    if abs(diff) <= SAME_PTS:
        result = "PAUL WINS (option 2: about the same)"
    elif abs(diff) <= half:
        result = "CAN'T TELL YET (gap is inside the noise)"
    elif diff > 0:
        result = "CLAUDE WINS (option 1: A bounces more)"
    else:
        result = "NEITHER (option 3: B bounces more)"
    note = "  [provisional: margin wider than 5 pts]" if half > SAME_PTS else ""
    return f"{result}; A-B = {diff:+.1f} pts, 95% margin +/-{half:.1f}{note}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--twap-db", default="data/twap.db")
    ap.add_argument("--triggers-db", default="data/triggers.db")
    ap.add_argument("--coins", nargs="+", default=["BTC", "HYPE"])
    args = ap.parse_args()

    tape, trig = ro(args.twap_db), ro(args.triggers_db)
    all_a, all_b = [], []
    for coin in args.coins:
        t0, t1 = trig.execute(
            "SELECT MIN(ts_ms), MAX(ts_ms) FROM trigger_tick_coins WHERE coin=?", (coin,)
        ).fetchone()
        if t0 is None:
            print(f"{coin}: no trigger data")
            continue
        ts, px = load_prints(tape, coin, t0, t1)
        if not ts:
            print(f"{coin}: no tape prints in the trigger window")
            continue
        candles = build_candles(ts, px)
        by_bucket = {c[0]: n for n, c in enumerate(candles)}
        sweeps = find_sweeps(ts, px, candles)

        events, unresolved = [], 0
        for s in sweeps:
            usd = trig.execute(
                "SELECT COALESCE(SUM(sell_usd), 0) FROM trigger_tick_coins "
                "WHERE coin=? AND ts_ms BETWEEN ? AND ?",
                (coin, s, s + STOP_WINDOW_MS),
            ).fetchone()[0]
            won = race(ts, px, candles, by_bucket, s)
            if won is None:
                unresolved += 1
            else:
                events.append((usd, s, won))

        events.sort()
        q = len(events) // 4  # A = top quarter of all scored sweeps
        b = [e[2] for e in events if e[0] == 0]
        a = [e[2] for e in events[-q:]] if q else []
        all_a += a
        all_b += b
        zero = len(b)
        days = (t1 - t0) / 86_400_000
        print(f"\n{coin}: {days:.1f} days, {len(candles)} candles, {len(sweeps)} sweeps, "
              f"{unresolved} unresolved, {len(events)} scored, {zero} with zero stops")
        if q:
            print(f"  B zero stops:     n={len(b)}, bounce {rate(b):.1f}%")
            print(f"  A top quarter:    n={len(a)}, bounce {rate(a):.1f}%, "
                  f"stops from ${events[-q][0]:,.0f}")
            print(f"  {verdict(a, b)}")

    print(f"\nALL COINS: A n={len(all_a)}, bounce {rate(all_a):.1f}% | "
          f"B n={len(all_b)}, bounce {rate(all_b):.1f}%")
    print(f"VERDICT: {verdict(all_a, all_b)}")


if __name__ == "__main__":
    main()