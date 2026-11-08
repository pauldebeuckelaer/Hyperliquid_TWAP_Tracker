#!/usr/bin/env python3
"""Frozen bets (Nov 8 2026).
Bet 1: after the same size of fall, does price bounce more when a swing low was
       broken?  Paul and Claude both say no: within 3 pts on both coins.
Bet 2: share of aggressive selling during the fall bought by market makers.
       Paul 75%, Claude 60%, closest wins.  Both: sweeps and dips within 5 pts.

Read-only.  Needs sweep_bet.py in the same folder.
Run from ~/bots/Hyperliquid_TWAP_Analyzer:  python3 scripts/sweep_vs_dip.py
"""
import argparse
import math
import statistics as st
from array import array
from bisect import bisect_left, bisect_right
from collections import defaultdict

from sweep_bet import BAR_MS, SPACING_MS, SWEEP_LIMIT_MS, SWING_SIDE, build_candles, find_sweeps, ro

ENTRY_DELAY_MS = 60_000     # no hindsight: enter 60s after the event
FALL_LOOKBACK_MS = 15 * 60_000
LOW_LOOKBACK_MS = 5 * 60_000
GRID_MS = 60_000
NEAR_LOW = 0.003
TARGET = 0.003
RACE_LIMIT_MS = 4 * 3_600_000
MATCH_TOL = 0.25            # fall and volatility each within +/-25%
MATCHES = 5
VOL_BARS = 12
TOP_N, MIN_PASSIVE, MAX_IMBALANCE = 20, 0.80, 0.10
BET1_PTS, BET2_PTS = 3.0, 5.0
MIN_PAIRS = 30


def say(*a):
    print(*a, flush=True)


def load(db, coin, t0, t1):
    ts, px, usd, tb, buyer, seller = (array("q"), array("d"), array("d"),
                                      array("b"), array("q"), array("q"))
    q = ("SELECT ts, px, notional, side, buyer, seller FROM tape_prints "
         "WHERE coin=? AND ts BETWEEN ? AND ? ORDER BY ts")
    agree = moved = 0
    last_vw = cur_ts = None
    pv = vol = net = 0.0

    def close_ms():
        nonlocal agree, moved, last_vw
        vw = pv / vol if vol else None
        if vw is not None and last_vw is not None and vw != last_vw and net != 0:
            moved += 1
            agree += (vw > last_vw) == (net > 0)
        if vw is not None:
            last_vw = vw

    for n, (t, p, u, s, b, a) in enumerate(db.execute(q, (coin, t0, t1)), 1):
        c = str(s).upper()[:1]
        if c == "B":
            taker_buy = 1
        elif c in ("A", "S"):
            taker_buy = 0
        else:
            raise SystemExit(f"Unknown side value {s!r}: stopping before scoring anything")
        if t != cur_ts:
            close_ms()
            cur_ts, pv, vol, net = t, 0.0, 0.0, 0.0
        pv += p * u
        vol += u
        net += u if taker_buy else -u
        ts.append(t); px.append(p); usd.append(u); tb.append(taker_buy)
        buyer.append(-1 if b is None else b); seller.append(-1 if a is None else a)
        if n % 2_000_000 == 0:
            say(f"  {coin}: {n:,} prints loaded")
    close_ms()
    check = 100.0 * agree / moved if moved else 0.0
    say(f"  {coin}: {len(ts):,} prints; side check {check:.1f}% of millisecond price moves agree")
    if check < 70:
        raise SystemExit("Side column does not look like the taker side: stopping")
    return ts, px, usd, tb, buyer, seller


def market_makers(usd, tb, buyer, seller):
    passive, total = defaultdict(float), defaultdict(float)
    bought, sold = defaultdict(float), defaultdict(float)
    for u, t, b, s in zip(usd, tb, buyer, seller):
        passive[s if t else b] += u
        total[b] += u; total[s] += u
        bought[b] += u; sold[s] += u
    top = sorted(passive, key=passive.get, reverse=True)[:TOP_N]
    mm = set()
    for w in top:
        if w == -1:
            continue
        big = max(bought[w], sold[w])
        if passive[w] / total[w] >= MIN_PASSIVE and abs(bought[w] - sold[w]) / big <= MAX_IMBALANCE:
            mm.add(w)
    all_passive = sum(passive.values())
    return mm, 100.0 * sum(passive[w] for w in mm) / all_passive


def cumulative(usd, tb, buyer, mm):
    sell, mm_buy = array("d", [0.0]), array("d", [0.0])
    a = b = 0.0
    for u, t, w in zip(usd, tb, buyer):
        if not t:
            a += u
            if w in mm:
                b += u
        sell.append(a); mm_buy.append(b)
    return sell, mm_buy


def swing_lows(candles):
    out, side = [], SWING_SIDE
    for c in range(side, len(candles) - side):
        if candles[c + side][0] - candles[c - side][0] != 2 * side:
            continue
        low = candles[c][1]
        if all(low < candles[k][1] for k in range(c - side, c + side + 1) if k != c):
            out.append(((candles[c + side][0] + 1) * BAR_MS, low))
    return out


class Coin:
    def __init__(self, ts, px, candles, sell, mm_buy):
        self.ts, self.px, self.candles = ts, px, candles
        self.sell, self.mm_buy = sell, mm_buy
        self.index = {c[0]: n for n, c in enumerate(candles)}
        self.lows = swing_lows(candles)
        self.low_ts = [x[0] for x in self.lows]

    def fall(self, t):
        ts, px = self.ts, self.px
        i = bisect_right(ts, t) - 1
        j = bisect_left(ts, t - FALL_LOOKBACK_MS)
        if i < 0 or j > i or ts[0] > t - FALL_LOOKBACK_MS:
            return None
        peak = max(px[j:i + 1])
        return i, px.index(peak, j, i + 1), peak, (peak - px[i]) / peak

    def vol(self, t):
        n = self.index.get(t // BAR_MS)
        if n is None or n < VOL_BARS:
            return None
        rows = self.candles[n - VOL_BARS:n]
        if rows[-1][0] - rows[0][0] != VOL_BARS - 1:
            return None
        return sum((c[2] - c[1]) / c[1] for c in rows) / VOL_BARS

    def has_low_nearby(self, t, price, peak):
        a = bisect_left(self.low_ts, t - SWEEP_LIMIT_MS)
        b = bisect_right(self.low_ts, t)
        return any(price * (1 - NEAR_LOW) <= self.lows[k][1] <= peak for k in range(a, b))

    def race(self, t):
        ts, px, candles = self.ts, self.px, self.candles
        start = t + ENTRY_DELAY_MS
        end = start + RACE_LIMIT_MS
        if ts[-1] < end:
            return None
        i = bisect_right(ts, start) - 1
        entry = px[i]
        up, down = entry * (1 + TARGET), entry * (1 - TARGET)
        i += 1
        while i < len(ts) and ts[i] <= end:
            c = candles[self.index[ts[i] // BAR_MS]]
            if i == c[3] and c[1] > down and c[2] < up:
                i = c[4]
                continue
            if px[i] >= up:
                return 1
            if px[i] <= down:
                return 0
            i += 1
        return None

    def event(self, t):
        f = self.fall(t)
        v = self.vol(t)
        if f is None or v is None or v == 0 or f[3] <= 0:
            return None
        return {"t": t, "i": f[0], "peak_i": f[1], "peak": f[2], "fall": f[3], "vol": v}

    def absorption(self, e):
        end = bisect_right(self.ts, e["t"] + ENTRY_DELAY_MS)
        return (self.sell[end] - self.sell[e["peak_i"]],
                self.mm_buy[end] - self.mm_buy[e["peak_i"]])


def dips(coin, sweeps):
    out, last = [], -10**18
    sw = list(sweeps)
    for t in range(coin.ts[0] + FALL_LOOKBACK_MS, coin.ts[-1], GRID_MS):
        if t - last < SPACING_MS:
            continue
        i = bisect_right(coin.ts, t) - 1
        if coin.ts[i] < t - GRID_MS:
            continue
        p = coin.px[i]
        j = bisect_left(coin.ts, t - LOW_LOOKBACK_MS)
        if p > min(coin.px[j:i + 1]):
            continue
        k = bisect_left(sw, t)
        near = any(0 <= q < len(sw) and abs(sw[q] - t) < SPACING_MS for q in (k - 1, k))
        if near:
            continue
        e = coin.event(t)
        if e is None or coin.has_low_nearby(t, p, e["peak"]):
            continue
        out.append(e)
        last = t
    return out


def analyse(coin, name):
    sweeps = find_sweeps(coin.ts, coin.px, coin.candles)
    pool = dips(coin, sweeps)
    say(f"  {name}: {len(sweeps)} sweeps, {len(pool)} candidate ordinary dips")
    cache, diffs, no_match, unresolved = {}, [], 0, 0
    s_win, d_win = [], []
    s_abs = [0.0, 0.0]; d_abs = [0.0, 0.0]
    used = set()

    def race(e):
        if e["t"] not in cache:
            cache[e["t"]] = coin.race(e["t"])
        return cache[e["t"]]

    for t in sweeps:
        e = coin.event(t)
        if e is None:
            no_match += 1
            continue
        near = [d for d in pool
                if abs(d["fall"] / e["fall"] - 1) <= MATCH_TOL and abs(d["vol"] / e["vol"] - 1) <= MATCH_TOL]
        near.sort(key=lambda d: abs(d["t"] - t))
        won = race(e)
        if won is None:
            unresolved += 1
            continue
        matched = [d for d in near if race(d) is not None][:MATCHES]
        if not matched:
            no_match += 1
            continue
        avg = sum(race(d) for d in matched) / len(matched)
        diffs.append(100.0 * (won - avg))
        s_win.append(won); d_win.append(avg)
        a, b = coin.absorption(e)
        s_abs[0] += a; s_abs[1] += b
        for d in matched:
            used.add(d["t"])
    for d in pool:
        if d["t"] in used:
            a, b = coin.absorption(d)
            d_abs[0] += a; d_abs[1] += b
    n = len(diffs)
    gap = st.mean(diffs) if n else float("nan")
    half = 1.96 * st.stdev(diffs) / math.sqrt(n) if n > 1 else float("inf")
    share_s = 100 * s_abs[1] / s_abs[0] if s_abs[0] else float("nan")
    share_d = 100 * d_abs[1] / d_abs[0] if d_abs[0] else float("nan")
    return {"n": n, "no_match": no_match, "unresolved": unresolved, "gap": gap, "half": half,
            "s": 100 * st.mean(s_win) if n else float("nan"),
            "d": 100 * st.mean(d_win) if n else float("nan"),
            "used": len(used), "share_s": share_s, "share_d": share_d}


def bet1(rows):
    if any(r["n"] < MIN_PAIRS for r in rows):
        return f"CAN'T TELL YET (need {MIN_PAIRS}+ matched sweeps on each coin)"
    if all(abs(r["gap"]) <= BET1_PTS for r in rows):
        note = " [provisional: a margin is wider than 3 pts]" if any(r["half"] > BET1_PTS for r in rows) else ""
        return "BOTH RIGHT: the swing low adds nothing" + note
    if any(abs(r["gap"]) > BET1_PTS and abs(r["gap"]) > r["half"] for r in rows):
        return "BOTH WRONG: the swing low changes the bounce on at least one coin"
    return "CAN'T TELL YET (a gap is over 3 pts but inside the noise)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--twap-db", default="data/twap.db")
    ap.add_argument("--triggers-db", default="data/triggers.db")
    ap.add_argument("--coins", nargs="+", default=["BTC", "HYPE"])
    args = ap.parse_args()
    tape, trig = ro(args.twap_db), ro(args.triggers_db)
    rows = []
    for name in args.coins:
        t0, t1 = trig.execute(
            "SELECT MIN(ts_ms), MAX(ts_ms) FROM trigger_tick_coins WHERE coin=?", (name,)).fetchone()
        if t0 is None:
            raise SystemExit(f"{name}: no trigger window")
        say(f"\n{name}: loading prints")
        ts, px, usd, tb, buyer, seller = load(tape, name, t0, t1)
        mm, passive_pct = market_makers(usd, tb, buyer, seller)
        say(f"  {name}: {len(mm)} market makers pass all three rules, {passive_pct:.1f}% of passive volume")
        sell, mm_buy = cumulative(usd, tb, buyer, mm)
        coin = Coin(ts, px, build_candles(ts, px), sell, mm_buy)
        r = analyse(coin, name)
        rows.append(r)
        say(f"{name} RESULT: {r['n']} matched sweeps ({r['no_match']} unmatched, {r['unresolved']} unresolved), "
            f"{r['used']} dips used")
        say(f"  bounce: sweep {r['s']:.1f}%, matched dip {r['d']:.1f}%, gap {r['gap']:+.1f} pts +/-{r['half']:.1f}")
        say(f"  market maker share: sweep {r['share_s']:.1f}%, dip {r['share_d']:.1f}%")

    say("\n=== BET 1 ===")
    say(bet1(rows))
    say("\n=== BET 2 ===")
    avg_s = st.mean(r["share_s"] for r in rows)
    avg_d = st.mean(r["share_d"] for r in rows)
    share = (avg_s + avg_d) / 2
    p, c = abs(share - 75), abs(share - 60)
    winner = "PAUL (75%)" if p < c else "CLAUDE (60%)" if c < p else "TIE"
    say(f"Share {share:.1f}% (sweep {avg_s:.1f}%, dip {avg_d:.1f}%): closest is {winner}")
    say("Sweeps and dips within 5 pts on both coins: "
        + ("YES" if all(abs(r["share_s"] - r["share_d"]) <= BET2_PTS for r in rows) else "NO"))


if __name__ == "__main__":
    main()