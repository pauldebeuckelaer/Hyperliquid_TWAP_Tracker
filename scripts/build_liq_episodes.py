#!/usr/bin/env python3
"""
build_liq_episodes.py — build the liq_episodes forensic table from twap.db.

Standalone. Does NOT touch twap.service. Run by hand:

    venv/bin/python3 scripts/build_liq_episodes.py
    venv/bin/python3 scripts/build_liq_episodes.py --labels-only
    venv/bin/python3 scripts/build_liq_episodes.py --dry-run --verbose

Contract: full delete-and-reinsert for the current METHOD_TAG on every run.
Nothing incremental, nothing upserted. Rows for other method_versions are
left alone so two rule sets can coexist for diffing.

Column-name trap: liquidation_snapshots.liq_price
                  perp_snapshots.liquidation_price
Same quantity, two names.
"""

import argparse
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# RULES. Most edits belong in this block and nowhere else.
# ---------------------------------------------------------------------------

PARAMS = {
    # An approach opens when distance_to_liq drops under this (stored as a
    # percentage in liquidation_snapshots, not a fraction).
    "entry_threshold": 1.0,
    # ...and does not close until it recovers above this. Hysteresis, so a
    # wallet oscillating around 1% makes one episode instead of a dozen.
    "exit_threshold": 2.0,
    # Peak position_value required to qualify.
    "notional_floor": 100_000,
    # A gap larger than this many times the wallet's OWN observed cadence
    # breaks the episode. Relative, because 30 minutes is one missed poll on
    # a T4 wallet and a vanished position on a VIP.
    "gap_multiple": 3,
    # Fewer rows than this and the approach is not readable.
    "min_rows": 3,
    # liq_price is only an informative defense channel if the wallet's own
    # baseline reprint rate would have produced at least this many reprints
    # inside the episode window. Below it, absence of movement means nothing
    # and the verdict has to rest on size alone. (Episode #1's correction.)
    "min_expected_liq_reprints": 1.0,
    # Baseline reprint rate is measured over this many hours before the
    # approach starts.
    "baseline_hours": 48,
    # After the last seen row, how long to look for the position coming back
    # before calling it vanished (also the window for the price-traversal
    # check). Multiple of cadence, floored.
    "vanish_multiple": 3,
    "vanish_floor_minutes": 15,
    # How long after a vanish to look for a re-entry with a reset entry_price.
    "reentry_hours": 24,
    # Relative size move that counts as a real size change.
    "size_change_eps": 0.01,
    # margin_mode ground truth only exists after the mid-Aug-14 migration.
    "start_date": "2026-08-14T00:00:00",
}

METHOD_TAG = "v1"

DEFAULT_DB = "data/twap.db"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def method_version():
    """Short stable hash of the rules + a manual tag."""
    import hashlib
    blob = repr(sorted(PARAMS.items())).encode()
    return f"{METHOD_TAG}-{hashlib.sha1(blob).hexdigest()[:6]}"


def parse_ts(s):
    if s is None:
        return None
    s = s.strip().replace(" ", "T")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def iso(dt):
    return None if dt is None else dt.isoformat()


def connect(path):
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    return con


# ---------------------------------------------------------------------------
# stage 1 — candidate pairs
# ---------------------------------------------------------------------------

def candidate_pairs(con, labels_only=False):
    sql = """
        SELECT address, coin, MAX(position_value) AS peak_notional
        FROM liquidation_snapshots
        WHERE snapshot_time >= ?
          AND distance_to_liq < ?
          AND position_value > ?
        GROUP BY address, coin
    """
    rows = con.execute(sql, (PARAMS["start_date"],
                             PARAMS["entry_threshold"],
                             PARAMS["notional_floor"])).fetchall()
    if labels_only:
        labeled = {r["key"].split(":")[0]
                   for r in con.execute("SELECT key FROM liq_episode_labels")}
        rows = [r for r in rows if r["address"] in labeled]
    return rows


def pair_series(con, address, coin):
    sql = """
        SELECT snapshot_time, size, side, position_value, entry_price,
               mark_price, liq_price, leverage, margin_used, distance_to_liq,
               unrealized_pnl, account_value
        FROM liquidation_snapshots
        WHERE address = ? AND coin = ? AND snapshot_time >= ?
        ORDER BY snapshot_time
    """
    return con.execute(sql, (address, coin, PARAMS["start_date"])).fetchall()


# ---------------------------------------------------------------------------
# stage 2 — cadence + approach splitting
# ---------------------------------------------------------------------------

def observed_cadence(times):
    """Median inter-row gap in seconds. The wallet's real clock, including
    any mid-window tier migration — which the tier column would lie about."""
    gaps = [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]
    gaps = [g for g in gaps if g > 0]
    return statistics.median(gaps) if gaps else None


def split_approaches(rows, cadence_s):
    """Hysteresis + relative-gap split. Returns lists of row-index ranges."""
    entry = PARAMS["entry_threshold"]
    exit_ = PARAMS["exit_threshold"]
    max_gap = (cadence_s or 60) * PARAMS["gap_multiple"]

    episodes, current, prev_t = [], [], None
    for i, r in enumerate(rows):
        t = parse_ts(r["snapshot_time"])
        if t is None:
            continue
        broke = prev_t is not None and (t - prev_t).total_seconds() > max_gap
        if current and broke:
            episodes.append(current)
            current = []
        d = r["distance_to_liq"]
        if not current:
            if d is not None and d < entry:
                current = [i]
        else:
            if d is not None and d > exit_:
                episodes.append(current)
                current = []
            else:
                current.append(i)
        prev_t = t
    if current:
        episodes.append(current)
    return episodes


# ---------------------------------------------------------------------------
# stage 3 — response columns from perp_snapshots
# ---------------------------------------------------------------------------

def perp_window(con, address, coin, t0, t1):
    sql = """
        SELECT snapshot_time, size, entry_price, liquidation_price,
               margin_used, margin_mode, unrealized_pnl
        FROM perp_snapshots
        WHERE address = ? AND snapshot_time BETWEEN ? AND ? AND coin = ?
        ORDER BY snapshot_time
    """
    return con.execute(sql, (address, iso(t0), iso(t1), coin)).fetchall()


def baseline_liq_rate(con, address, coin, t_start):
    """Distinct liq_price values per hour over the quiet window before the
    approach. On a quiet cross account this is ~1/hr — the funding tick."""
    t0 = t_start - timedelta(hours=PARAMS["baseline_hours"])
    rows = perp_window(con, address, coin, t0, t_start)
    if len(rows) < 2:
        return None
    vals = {r["liquidation_price"] for r in rows if r["liquidation_price"] is not None}
    ts = [parse_ts(r["snapshot_time"]) for r in rows]
    ts = [t for t in ts if t]
    if len(ts) < 2:
        return None
    hours = (max(ts) - min(ts)).total_seconds() / 3600.0
    return (len(vals) / hours) if hours > 0 else None


def defense_columns(prows, lrows):
    """size_changed / liq_moved, preferring perp rows, falling back to the
    liquidation_snapshots series if the perp window is empty."""
    sizes = [abs(r["size"]) for r in prows if r["size"] is not None]
    liqs = [r["liquidation_price"] for r in prows if r["liquidation_price"] is not None]
    if not sizes:
        sizes = [abs(r["size"]) for r in lrows if r["size"] is not None]
        liqs = [r["liq_price"] for r in lrows if r["liq_price"] is not None]

    size_change_pct = None
    size_changed = 0
    if sizes:
        hi, lo = max(sizes), min(sizes)
        if hi > 0:
            size_change_pct = (hi - lo) / hi
            size_changed = int(size_change_pct > PARAMS["size_change_eps"])
    liq_moved = int(len(set(liqs)) > 1) if liqs else 0
    return size_changed, size_change_pct, liq_moved


def margin_mode_for(con, address, coin, t0, t1):
    """Mode cannot be switched while a position is open, so any one row in
    the window labels the whole episode."""
    row = con.execute(
        """SELECT margin_mode FROM perp_snapshots
           WHERE address = ? AND snapshot_time BETWEEN ? AND ? AND coin = ?
             AND margin_mode IS NOT NULL LIMIT 1""",
        (address, iso(t0), iso(t1), coin)).fetchone()
    return row["margin_mode"] if row else None


# ---------------------------------------------------------------------------
# stage 4 — vanish, re-entry, price traversal
# ---------------------------------------------------------------------------

def next_perp_row(con, address, coin, after, until):
    return con.execute(
        """SELECT snapshot_time, size, entry_price FROM perp_snapshots
           WHERE address = ? AND snapshot_time > ? AND snapshot_time <= ?
             AND coin = ? ORDER BY snapshot_time LIMIT 1""",
        (address, iso(after), iso(until), coin)).fetchone()


def price_traversed(con, coin, t0, t1, liq_price, is_short):
    """Did price cross the liquidation level inside the gap? market_snapshots
    (1-min) inside its 7-day retention, market_candles high/low outside it.
    Returns 1 / 0 / None-when-no-price-data."""
    if liq_price is None:
        return None
    row = con.execute(
        """SELECT MAX(mark_px) AS hi, MIN(mark_px) AS lo, COUNT(*) AS n
           FROM market_snapshots
           WHERE coin = ? AND snapshot_time BETWEEN ? AND ?""",
        (coin, iso(t0), iso(t1))).fetchone()
    if not row or not row["n"]:
        row = con.execute(
            """SELECT MAX(high) AS hi, MIN(low) AS lo, COUNT(*) AS n
               FROM market_candles
               WHERE coin = ? AND candle_time BETWEEN ? AND ?""",
            (coin, iso(t0), iso(t1))).fetchone()
    if not row or not row["n"] or row["hi"] is None:
        return None
    return int(row["hi"] >= liq_price) if is_short else int(row["lo"] <= liq_price)


# ---------------------------------------------------------------------------
# stage 5 — verdict
# ---------------------------------------------------------------------------

def decide(ep):
    if ep["n_rows"] < PARAMS["min_rows"]:
        return "unreadable", 0
    if ep["is_open"]:
        return "open", 1
    if ep["entry_price_reset"] == 1:
        return "dead", 1
    if ep["position_vanished"] == 1:
        if ep["price_traversed_liq"] == 1:
            return "dead", 1
        if ep["price_traversed_liq"] == 0:
            return "survived", 1
        return "ambiguous", 0
    return "survived", 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build(con, labels_only=False, verbose=False):
    mv = method_version()
    data_tail = parse_ts(con.execute(
        "SELECT MAX(snapshot_time) AS t FROM liquidation_snapshots").fetchone()["t"])
    out = []

    for pair in candidate_pairs(con, labels_only):
        addr, coin = pair["address"], pair["coin"]
        rows = pair_series(con, addr, coin)
        if len(rows) < 2:
            continue
        times = [parse_ts(r["snapshot_time"]) for r in rows]
        times = [t for t in times if t]
        cadence = observed_cadence(times)

        for idxs in split_approaches(rows, cadence):
            erows = [rows[i] for i in idxs]
            if not erows:
                continue
            etimes = [parse_ts(r["snapshot_time"]) for r in erows]
            dists = [(r["distance_to_liq"], parse_ts(r["snapshot_time"])) for r in erows
                     if r["distance_to_liq"] is not None]
            if not dists:
                continue
            min_dist, min_dist_time = min(dists, key=lambda x: x[0])
            t_start, t_end = etimes[0], etimes[-1]
            peak_notional = max((r["position_value"] or 0) for r in erows)
            if peak_notional <= PARAMS["notional_floor"]:
                continue

            gaps = [(etimes[i] - etimes[i - 1]).total_seconds()
                    for i in range(1, len(etimes))]
            max_gap = max(gaps) if gaps else 0.0

            prows = perp_window(con, addr, coin, t_start, t_end)
            size_changed, size_pct, liq_moved = defense_columns(prows, erows)
            mode = margin_mode_for(con, addr, coin, t_start, t_end)

            rate = baseline_liq_rate(con, addr, coin, t_start)
            span_h = max((t_end - t_start).total_seconds() / 3600.0, 1e-9)
            expected = (rate or 0) * span_h
            liq_informative = int(expected >= PARAMS["min_expected_liq_reprints"])

            last_seen = parse_ts(prows[-1]["snapshot_time"]) if prows else t_end
            last_size = prows[-1]["size"] if prows else erows[-1]["size"]
            last_entry = prows[-1]["entry_price"] if prows else erows[-1]["entry_price"]
            last_liq = (prows[-1]["liquidation_price"] if prows else erows[-1]["liq_price"])
            is_short = (last_size or 0) < 0

            vanish_win = timedelta(seconds=max((cadence or 60) * PARAMS["vanish_multiple"],
                                               PARAMS["vanish_floor_minutes"] * 60))
            nxt = next_perp_row(con, addr, coin, last_seen, last_seen + vanish_win)
            vanished = int(nxt is None)

            is_open = int(data_tail is not None
                          and (data_tail - last_seen) < vanish_win)

            traversed = None
            entry_reset = 0
            if vanished and not is_open:
                traversed = price_traversed(con, coin, last_seen,
                                            last_seen + vanish_win, last_liq, is_short)
                back = next_perp_row(con, addr, coin, last_seen,
                                     last_seen + timedelta(hours=PARAMS["reentry_hours"]))
                if back and back["entry_price"] and last_entry:
                    entry_reset = int(abs(back["entry_price"] - last_entry)
                                      / max(abs(last_entry), 1e-9) > 0.001)

            ep = {
                "key": f"{addr}:{coin}:{iso(min_dist_time)}",
                "method_version": mv,
                "address": addr,
                "coin": coin,
                "approach_start": iso(t_start),
                "min_dist_time": iso(min_dist_time),
                "approach_end": iso(t_end),
                "min_dist": min_dist,
                "peak_notional": peak_notional,
                "margin_mode": mode,
                "obs_cadence_s": cadence,
                "n_rows": len(erows),
                "max_gap_s": max_gap,
                "size_changed": size_changed,
                "size_change_pct": size_pct,
                "liq_moved": liq_moved,
                "liq_reprints_per_hr": rate,
                "liq_informative": liq_informative,
                "last_seen": iso(last_seen),
                "position_vanished": vanished,
                "price_traversed_liq": traversed,
                "entry_price_reset": entry_reset,
                "is_open": is_open,
                "built_at": datetime.utcnow().isoformat(),
            }
            ep["verdict"], ep["classifiable"] = decide(ep)
            ep.pop("is_open")
            out.append(ep)
            if verbose:
                print(f"  {ep['key'][:24]}... {coin:12} "
                      f"min={min_dist:.3f} n={len(erows):4} -> {ep['verdict']}")
    return mv, out


COLS = ["key", "method_version", "address", "coin", "approach_start",
        "min_dist_time", "approach_end", "min_dist", "peak_notional",
        "margin_mode", "obs_cadence_s", "n_rows", "max_gap_s", "size_changed",
        "size_change_pct", "liq_moved", "liq_reprints_per_hr", "liq_informative",
        "last_seen", "position_vanished", "price_traversed_liq",
        "entry_price_reset", "verdict", "classifiable", "built_at"]


def write(con, mv, episodes, chunk=200):
    """Short write bursts. The collector is writing to this DB too."""
    con.execute("DELETE FROM liq_episodes WHERE method_version = ?", (mv,))
    con.commit()
    sql = (f"INSERT OR REPLACE INTO liq_episodes ({','.join(COLS)}) "
           f"VALUES ({','.join('?' * len(COLS))})")
    for i in range(0, len(episodes), chunk):
        con.executemany(sql, [[e[c] for c in COLS] for e in episodes[i:i + chunk]])
        con.commit()


def compare_labels(con, mv):
    rows = con.execute(
        """SELECT e.key, e.verdict AS built, l.verdict AS hand, l.archetype
           FROM liq_episodes e JOIN liq_episode_labels l USING (key)
           WHERE e.method_version = ?""", (mv,)).fetchall()
    if not rows:
        print("\nno label overlap yet — populate liq_episode_labels to enable the check")
        return
    bad = [r for r in rows if r["built"] != r["hand"]]
    print(f"\nregression: {len(rows) - len(bad)}/{len(rows)} agree with hand labels")
    for r in bad:
        print(f"  DISAGREE {r['key']}  built={r['built']}  hand={r['hand']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--labels-only", action="store_true",
                    help="restrict to wallets present in liq_episode_labels")
    ap.add_argument("--dry-run", action="store_true", help="build but do not write")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    con = connect(a.db)
    mv, eps = build(con, a.labels_only, a.verbose)
    print(f"\nmethod_version = {mv}")
    print(f"episodes built = {len(eps)}")
    tally = {}
    for e in eps:
        tally[e["verdict"]] = tally.get(e["verdict"], 0) + 1
    for k in sorted(tally):
        print(f"  {k:12} {tally[k]}")

    if a.dry_run:
        print("\ndry run — nothing written")
        return 0
    write(con, mv, eps)
    print(f"wrote {len(eps)} rows")
    compare_labels(con, mv)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())