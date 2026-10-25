#!/usr/bin/env python3
"""
score_level_breaks.py - test the round-level trigger hypothesis on every coin.

HYPOTHESIS (refined on BTC, Oct 24-25 2026):
  When a trigger cascade crosses a round level, the first trigger tick through
  the level is the largest tick of the burst that follows it (allowing for a
  tiny "touch" tick just before the big one).

CONTROL:
  Cascades may simply tend to open with their biggest tick, levels or not.
  So the same score is computed for bursts that start WITHOUT a level cross.
  The hypothesis only adds something if the level group scores clearly higher.

FROZEN RULES (fixed on 2026-10-25, before scoring any data outside BTC Oct 24-25):
  tick       one coin's share of one oracle-trigger transaction
             (a row in trigger_tick_coins)
  direction  'up' if triggered buys > triggered sells (shorts stopped), else 'down'
  size       dominant-side notional in USD
  level      any multiple of step = 10^(floor(log10(price)) - 2)
             (BTC ~80k -> $100, ETH ~2.3k -> $10, SOL ~100 -> $1, HYPE ~75 -> $0.1)
  cross      up:   prev tick's px_hi <= L <  this tick's px_hi
             down: this tick's px_lo <  L <= prev tick's px_lo
             prev tick = the coin's previous trigger tick of EITHER direction,
             and it must be within 30 min, else the tick is unclassifiable
             (an exact touch of L is not a cross)
  chain      the tick plus following SAME-direction ticks of the same coin,
             each no more than 10 s after the previous one; the chain stops
             before the next level-crossing tick (that one starts its own chain)
  counted    chains of length >= 2 whose largest tick is >= $50k
  pass       the largest tick is position 1, OR it is position 2 and position 1
             is under 20% of it and at most 4 s earlier ("touch then break")
  groups     LEVEL = chains starting at a level-crossing tick
             CONTROL = chains starting at a burst start (no same-direction tick
             in the previous 10 s) that does not cross a level
  samples    IN  = from 2026-10-24 00:00 UTC (where the rule was refined)
             OUT = everything before that (the actual test)

PLACEBO (added 2026-10-25, after the first run showed +15pp out-of-sample):
  A tick that moves price further crosses ANY grid line more often, and bigger
  ticks move price further. So part of the lift could be mechanical. --placebo
  reruns the same test on the grid shifted by 0.25, 0.5 and 0.75 of a step
  (e.g. BTC 80,025 / 80,050 / 80,075). Round numbers matter only if the real
  grid (offset 0) beats the placebo grids clearly.

Usage
  python3 score_level_breaks.py --placebo       real grid vs shifted grids
  python3 score_level_breaks.py                 all coins
  python3 score_level_breaks.py --coin BTC      one coin
  python3 score_level_breaks.py --min-n 20      hide coins with few chains
"""
import argparse
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = os.path.expanduser("~/bots/Hyperliquid_TWAP_Analyzer/data")
TRIG_DB = os.path.join(DATA_DIR, "triggers.db")

GAP_MS = 10_000
REF_MAX_MS = 30 * 60 * 1000
MIN_CHAIN_USD = 50_000
TOUCH_FRAC = 0.20
TOUCH_GAP_MS = 4_000
IN_SAMPLE_START_MS = int(datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp() * 1000)
EPS = 1e-9


def step_for(px):
    return 10 ** (math.floor(math.log10(px)) - 2)


OFFSET = 0.0   # 0 = real round levels; 0.5 = placebo grid halfway between them


def crosses(direction, prev, cur):
    """True if cur crosses a grid level relative to prev (see FROZEN RULES).
    The grid is (k + OFFSET) * step; OFFSET 0 gives the real round levels."""
    if direction == "up":
        step = step_for(cur["hi"])
        k = math.ceil(prev["hi"] / step - OFFSET - EPS)   # lowest level >= prev high
        return cur["hi"] / step - OFFSET > k + EPS        # ... and below this tick's high
    step = step_for(cur["lo"])
    k = math.floor(prev["lo"] / step - OFFSET + EPS)      # highest level <= prev low
    return cur["lo"] / step - OFFSET < k - EPS            # ... and above this tick's low


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def is_pass(chain):
    sizes = [t["size"] for t in chain]
    m = max(sizes)
    pos = sizes.index(m)
    if pos == 0:
        return True
    if pos == 1 and sizes[0] < TOUCH_FRAC * m and chain[1]["ts"] - chain[0]["ts"] <= TOUCH_GAP_MS:
        return True
    return False


def score_coin(ticks):
    """ticks: list of dicts sorted by ts. Returns list of (group, sample, passed, length)."""
    # classify each tick: direction, size, cross flag (True/False/None)
    for i, t in enumerate(ticks):
        t["dir"] = "up" if t["buy_usd"] > t["sell_usd"] else "down"
        t["size"] = max(t["buy_usd"], t["sell_usd"])
        t["cross"] = None
        if i > 0:
            prev = ticks[i - 1]
            if t["ts"] - prev["ts"] <= REF_MAX_MS:
                t["cross"] = crosses(t["dir"], prev, t)

    by_dir = {"up": [t for t in ticks if t["dir"] == "up"],
              "down": [t for t in ticks if t["dir"] == "down"]}

    results = []
    for d, seq in by_dir.items():
        for i, t in enumerate(seq):
            if t["cross"] is None:
                continue
            burst_start = i == 0 or t["ts"] - seq[i - 1]["ts"] > GAP_MS
            if t["cross"]:
                group = "LEVEL"
            elif burst_start:
                group = "CONTROL"
            else:
                continue
            chain = [t]
            for nxt in seq[i + 1:]:
                if nxt["ts"] - chain[-1]["ts"] > GAP_MS or nxt["cross"]:
                    break
                chain.append(nxt)
            if len(chain) < 2 or max(c["size"] for c in chain) < MIN_CHAIN_USD:
                continue
            sample = "IN" if t["ts"] >= IN_SAMPLE_START_MS else "OUT"
            results.append((group, sample, is_pass(chain), len(chain)))
    return results


def fmt_rate(k, n):
    if n == 0:
        return f"{'-':>5} {'':>13}"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:4.0f}% [{100 * lo:3.0f}-{100 * hi:3.0f}%]"


def summarize(rows):
    out = {}
    for g in ("LEVEL", "CONTROL"):
        sel = [r for r in rows if r[0] == g]
        out[g] = (sum(r[2] for r in sel), len(sel),
                  (sum(r[3] for r in sel) / len(sel)) if sel else 0.0)
    return out


def print_table(title, rows_by_key, min_n):
    print(f"\n{title}")
    print(f"  {'':16} {'LEVEL n':>7}  {'pass':>19}  {'len':>4}   {'CONTROL n':>9}  {'pass':>19}  {'len':>4}   {'lift':>6}")
    for key, rows in rows_by_key:
        s = summarize(rows)
        (kl, nl, ll), (kc, nc, lc) = s["LEVEL"], s["CONTROL"]
        if nl + nc < min_n:
            continue
        lift = (100 * kl / nl - 100 * kc / nc) if nl and nc else float("nan")
        lift_s = f"{lift:+5.0f}pp" if not math.isnan(lift) else f"{'-':>6}"
        print(f"  {key:16} {nl:>7}  {fmt_rate(kl, nl):>19}  {ll:4.1f}   {nc:>9}  {fmt_rate(kc, nc):>19}  {lc:4.1f}   {lift_s}")


def run(per_coin, offset):
    global OFFSET
    OFFSET = offset
    all_rows, coin_rows = [], []
    for coin, ticks in per_coin.items():
        rows = score_coin(ticks)
        coin_rows.append((coin, rows))
        all_rows += rows
    coin_rows.sort(key=lambda kv: -len(kv[1]))
    return all_rows, coin_rows


PLACEBO_OFFSETS = (0.0, 0.25, 0.5, 0.75)


def lift_of(rows):
    s = summarize(rows)
    (kl, nl, _), (kc, nc, _) = s["LEVEL"], s["CONTROL"]
    if not nl or not nc:
        return None, nl
    return 100 * kl / nl - 100 * kc / nc, nl


def run_placebo(per_coin, min_n):
    """Score the real grid (offset 0) and shifted placebo grids, out-of-sample only.
    If round numbers matter, offset 0 must beat the placebo offsets clearly.
    If the lifts are similar, the effect is mechanical (big ticks move price
    further, so they cross ANY grid line more often), not round-number stops."""
    results = {}
    for off in PLACEBO_OFFSETS:
        all_rows, coin_rows = run(per_coin, off)
        oos = [r for r in all_rows if r[1] == "OUT"]
        table = [("ALL COINS", oos)]
        table += [("  len 2", [r for r in oos if r[3] == 2]),
                  ("  len 3-5", [r for r in oos if 3 <= r[3] <= 5]),
                  ("  len 6+", [r for r in oos if r[3] >= 6])]
        table += [(c, [r for r in rows if r[1] == "OUT"]) for c, rows in coin_rows]
        results[off] = {k: lift_of(v) for k, v in table}
        order = [k for k, _ in table]

    print("\nPLACEBO TEST, OUT-OF-SAMPLE: lift (LEVEL pass% minus CONTROL pass%) per grid offset")
    print("offset 0 = real round levels; 0.25 / 0.5 / 0.75 = same grid shifted by that fraction of a step")
    hdr = "".join(f"{('real 0' if o == 0 else f'off {o}'):>12}" for o in PLACEBO_OFFSETS)
    print(f"  {'':14}{hdr}   {'LEVEL n (real)':>15}")
    for key in order:
        cells, n_real = "", results[0.0][key][1]
        if n_real < min_n and not key.startswith(("ALL", "  len")):
            continue
        for o in PLACEBO_OFFSETS:
            lift, _ = results[o][key]
            cells += f"{(f'{lift:+.0f}pp' if lift is not None else '-'):>12}"
        print(f"  {key:14}{cells}   {n_real:>15}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coin", help="score only this coin (exact name, e.g. BTC or xyz:BRENTOIL)")
    ap.add_argument("--min-n", type=int, default=10, help="hide coins with fewer chains (default 10)")
    ap.add_argument("--placebo", action="store_true",
                    help="compare the real round-level grid with shifted placebo grids")
    ap.add_argument("--db", default=TRIG_DB)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    q = ("SELECT coin, ts_ms, sell_usd, buy_usd, px_hi, px_lo FROM trigger_tick_coins "
         "WHERE px_hi > 0 AND px_lo > 0 AND (sell_usd > 0 OR buy_usd > 0)")
    params = ()
    if args.coin:
        q += " AND coin = ?"
        params = (args.coin,)
    q += " ORDER BY coin, ts_ms"

    per_coin = defaultdict(list)
    for coin, ts, s_usd, b_usd, hi, lo in con.execute(q, params):
        per_coin[coin].append({"ts": ts, "sell_usd": s_usd, "buy_usd": b_usd, "hi": hi, "lo": lo})
    if not per_coin:
        sys.exit("No trigger ticks found. Has build_trigger_batches.py run?")

    first = min(t[0]["ts"] for t in per_coin.values())
    last = max(t[-1]["ts"] for t in per_coin.values())
    fmt = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"Trigger ticks {fmt(first)} -> {fmt(last)} UTC, {len(per_coin)} coins")

    if args.placebo:
        run_placebo(per_coin, args.min_n)
        return

    all_rows, coin_rows = run(per_coin, 0.0)
    print("pass = largest tick of the chain is the first (or a touch-then-break 2nd); "
          "len = mean chain length; lift = LEVEL pass% minus CONTROL pass%")

    oos = [r for r in all_rows if r[1] == "OUT"]
    ins = [r for r in all_rows if r[1] == "IN"]
    print_table("ALL COINS", [("out-of-sample", oos), ("in-sample", ins)], 0)

    print_table("PER COIN, OUT-OF-SAMPLE (before Oct 24)",
                [(c, [r for r in rows if r[1] == "OUT"]) for c, rows in coin_rows], args.min_n)

    # chain length confound: short chains pass more often by chance
    strata = [("length 2", lambda n: n == 2), ("length 3-5", lambda n: 3 <= n <= 5), ("length 6+", lambda n: n >= 6)]
    print_table("ALL COINS OUT-OF-SAMPLE, BY CHAIN LENGTH",
                [(name, [r for r in oos if f(r[3])]) for name, f in strata], 0)


if __name__ == "__main__":
    main()