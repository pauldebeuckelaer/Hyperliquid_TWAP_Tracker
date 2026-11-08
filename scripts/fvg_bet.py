#!/usr/bin/env python3
"""Frozen bet (Nov 8 2026): after price touches a 5-minute fair value gap, does it
continue more often than after a matched pullback with no gap?
Paul and Claude both pick option 2: within 3 pts on both coins, about 50%.

Read-only.  Needs sweep_bet.py in the same folder.
Run from ~/bots/Hyperliquid_TWAP_Analyzer:  python3 scripts/fvg_bet.py
"""
import argparse
import math
import statistics as st
from bisect import bisect_left, bisect_right

from sweep_bet import BAR_MS, SPACING_MS, build_candles, load_prints, ro

MIN_GAP = 0.0005
TOUCH_LIMIT_MS = 4 * 3_600_000
TARGET = 0.003
RACE_LIMIT_MS = 4 * 3_600_000
NEAR_GAP = 0.003
TREND_MS = 3_600_000
PEAK_BARS = 12
VOL_BARS = 12
LOCAL_MS = 5 * 60_000
GRID_MS = 60_000
MATCH_TOL = 0.25
SAME_PTS = 3.0
MIN_PAIRS = 30


def say(*a):
    print(*a, flush=True)


def find_gaps(candles):
    """Each gap: (formed_ms, direction, edge, zone_low, zone_high)."""
    gaps = []
    for n in range(2, len(candles)):
        a, c = candles[n - 2], candles[n]
        if c[0] - a[0] != 2:
            continue
        formed = (c[0] + 1) * BAR_MS
        if a[2] < c[1] and (c[1] - a[2]) / a[2] >= MIN_GAP:
            gaps.append((formed, 1, c[1], a[2], c[1]))
        elif a[1] > c[2] and (a[1] - c[2]) / a[1] >= MIN_GAP:
            gaps.append((formed, -1, c[2], c[2], a[1]))
    return gaps


class Coin:
    def __init__(self, ts, px, candles):
        self.ts, self.px, self.candles = ts, px, candles
        self.index = {c[0]: n for n, c in enumerate(candles)}
        self.gaps = find_gaps(candles)
        self.gap_ts = [g[0] for g in self.gaps]

    def touch(self, gap):
        formed, d, edge = gap[:3]
        limit = formed + TOUCH_LIMIT_MS
        n = self.index.get(formed // BAR_MS)
        if n is None:
            i = bisect_left(self.ts, formed)
            if i >= len(self.ts):
                return None
            n = self.index[self.ts[i] // BAR_MS]
        while n < len(self.candles) and self.candles[n][0] * BAR_MS < limit:
            c = self.candles[n]
            hit = c[1] <= edge if d == 1 else c[2] >= edge
            if hit:
                for i in range(c[3], c[4]):
                    t = self.ts[i]
                    if t < formed or t >= limit:
                        continue
                    if (self.px[i] <= edge) if d == 1 else (self.px[i] >= edge):
                        return i
            n += 1
        return None

    def features(self, i, d):
        t, p = self.ts[i], self.px[i]
        n = self.index[t // BAR_MS]
        if n < max(PEAK_BARS, VOL_BARS):
            return None
        rows = self.candles[n - VOL_BARS:n]
        if rows[-1][0] - rows[0][0] != VOL_BARS - 1:
            return None
        vol = sum((c[2] - c[1]) / c[1] for c in rows) / VOL_BARS
        back = self.candles[n - PEAK_BARS:n + 1]
        if d == 1:
            ext = max(c[2] for c in back)
            pull = (ext - p) / ext
        else:
            ext = min(c[1] for c in back)
            pull = (p - ext) / ext
        j = bisect_right(self.ts, t - TREND_MS) - 1
        if j < 0 or self.ts[j] < t - TREND_MS - 5 * 60_000 or vol == 0 or pull <= 0:
            return None
        return {"i": i, "t": t, "d": d, "vol": vol, "pull": pull, "trend": p >= self.px[j]}

    def near_gap(self, t, p):
        a = bisect_left(self.gap_ts, t - TOUCH_LIMIT_MS)
        b = bisect_right(self.gap_ts, t)
        low, high = p * (1 - NEAR_GAP), p * (1 + NEAR_GAP)
        return any(self.gaps[k][3] <= high and self.gaps[k][4] >= low for k in range(a, b))

    def race(self, i, d):
        ts, px, candles = self.ts, self.px, self.candles
        end = ts[i] + RACE_LIMIT_MS
        if ts[-1] < end:
            return None
        entry = px[i]
        good = entry * (1 + d * TARGET)
        bad = entry * (1 - d * TARGET)
        up, down = max(good, bad), min(good, bad)
        k = i + 1
        while k < len(ts) and ts[k] <= end:
            c = candles[self.index[ts[k] // BAR_MS]]
            if k == c[3] and c[1] > down and c[2] < up:
                k = c[4]
                continue
            if (d == 1 and px[k] >= good) or (d == -1 and px[k] <= good):
                return 1
            if (d == 1 and px[k] <= bad) or (d == -1 and px[k] >= bad):
                return 0
            k += 1
        return None


def touches(coin):
    raw = []
    for g in coin.gaps:
        i = coin.touch(g)
        if i is not None:
            raw.append((coin.ts[i], i, g[1]))
    raw.sort()
    kept, last = [], {1: -10**18, -1: -10**18}
    for t, i, d in raw:
        if t - last[d] >= SPACING_MS:
            kept.append((t, i, d))
            last[d] = t
    return kept


def controls(coin, events):
    out = {1: [], -1: []}
    event_ts = {d: [t for t, _, x in events if x == d] for d in (1, -1)}
    ts, px = coin.ts, coin.px
    for d in (1, -1):
        last = -10**18
        times = event_ts[d]
        for t in range(ts[0] + TREND_MS, ts[-1], GRID_MS):
            if t - last < SPACING_MS:
                continue
            i = bisect_right(ts, t) - 1
            if ts[i] < t - GRID_MS:
                continue
            j = bisect_left(ts, t - LOCAL_MS)
            window = px[j:i + 1]
            if (d == 1 and px[i] > min(window)) or (d == -1 and px[i] < max(window)):
                continue
            k = bisect_left(times, t)
            if any(0 <= q < len(times) and abs(times[q] - t) < SPACING_MS for q in (k - 1, k)):
                continue
            if coin.near_gap(t, px[i]):
                continue
            f = coin.features(i, d)
            if f is not None:
                out[d].append(f)
                last = t
    return out


def analyse(coin, name):
    events = touches(coin)
    pool = controls(coin, events)
    say(f"  {name}: {len(coin.gaps)} gaps, {len(events)} first touches, "
        f"{len(pool[1]) + len(pool[-1])} candidate pullbacks")
    used, diffs, wins, ctrl = set(), [], [], []
    unmatched = unresolved = 0
    split = {1: [], -1: []}
    for t, i, d in events:
        e = coin.features(i, d)
        if e is None:
            unmatched += 1
            continue
        won = coin.race(i, d)
        if won is None:
            unresolved += 1
            continue
        near = [c for c in pool[d] if (d, c["t"]) not in used and c["trend"] == e["trend"]
                and abs(c["pull"] / e["pull"] - 1) <= MATCH_TOL and abs(c["vol"] / e["vol"] - 1) <= MATCH_TOL]
        near.sort(key=lambda c: abs(c["t"] - t))
        partner = None
        for c in near:
            r = coin.race(c["i"], d)
            if r is not None:
                partner = (c, r)
                break
        if partner is None:
            unmatched += 1
            continue
        used.add((d, partner[0]["t"]))
        wins.append(won); ctrl.append(partner[1]); split[d].append(won)
        diffs.append(100.0 * (won - partner[1]))
    n = len(diffs)
    return {"n": n, "unmatched": unmatched, "unresolved": unresolved,
            "gap": st.mean(diffs) if n else float("nan"),
            "half": 1.96 * st.stdev(diffs) / math.sqrt(n) if n > 1 else float("inf"),
            "fvg": 100 * st.mean(wins) if n else float("nan"),
            "ctrl": 100 * st.mean(ctrl) if n else float("nan"),
            "bull": 100 * st.mean(split[1]) if split[1] else float("nan"),
            "bear": 100 * st.mean(split[-1]) if split[-1] else float("nan"),
            "nb": len(split[1]), "ns": len(split[-1])}


def verdict(rows):
    if any(r["n"] < MIN_PAIRS for r in rows):
        return f"CAN'T TELL YET (need {MIN_PAIRS}+ matched pairs on each coin)"
    if all(abs(r["gap"]) <= SAME_PTS for r in rows):
        note = " [provisional: a margin is wider than 3 pts]" if any(r["half"] > SAME_PTS for r in rows) else ""
        return "OPTION 2: BOTH RIGHT, about the same" + note
    clear = [r for r in rows if abs(r["gap"]) > SAME_PTS and abs(r["gap"]) > r["half"]]
    if not clear:
        return "CAN'T TELL YET (a gap is over 3 pts but inside the noise)"
    if all(r["gap"] > 0 for r in clear):
        return "OPTION 1: BOTH WRONG, FVG continues more"
    if all(r["gap"] < 0 for r in clear):
        return "OPTION 3: BOTH WRONG, FVG continues less"
    return "MIXED: BOTH WRONG, coins disagree in direction"


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
        ts, px = load_prints(tape, name, t0, t1)
        say(f"  {name}: {len(ts):,} prints loaded, finding gaps")
        r = analyse(Coin(ts, px, build_candles(ts, px)), name)
        rows.append(r)
        say(f"{name} RESULT: {r['n']} matched pairs ({r['unmatched']} unmatched, {r['unresolved']} unresolved)")
        say(f"  continuation: FVG {r['fvg']:.1f}%, matched pullback {r['ctrl']:.1f}%, "
            f"gap {r['gap']:+.1f} pts +/-{r['half']:.1f}")
        say(f"  FVG by direction: bullish {r['bull']:.1f}% (n={r['nb']}), bearish {r['bear']:.1f}% (n={r['ns']})")
    say("\n=== FVG BET ===")
    say(verdict(rows))


if __name__ == "__main__":
    main()