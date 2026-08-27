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

REQUIRES (run once each, before the first build that needs them):
    ALTER TABLE liq_episodes ADD COLUMN truncated INTEGER;          -- v6
    ALTER TABLE liq_episodes ADD COLUMN cadence_snapped_s REAL;     -- v7
"""

import argparse
import math
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    # check). Multiple of the LOCAL cadence, floored.
    "vanish_multiple": 3,
    "vanish_floor_minutes": 15,
    # How many perp rows before last_seen to measure the local cadence over.
    # Small on purpose: the point is the sampling rate AT THE MOMENT the rows
    # stopped, not the wallet's average over the surrounding days.
    "cadence_window_rows": 6,
    # How long after a vanish to look for a re-entry with a reset entry_price.
    "reentry_hours": 24,
    # Relative size move that counts as a real size change.
    "size_change_eps": 0.01,
    # A capout deactivation stops collection for the wallet entirely. Perp
    # rows then stop for exactly the same reason they stop when a position
    # closes, so every vanish-based inference below is reading a gap the
    # observer created rather than the wallet. The event is written by the
    # tier refresh, which lags the actual drop-out by up to one refresh
    # interval — hence a lookahead past last_seen rather than a containment
    # test against the approach window. Measured lag on the five known cases
    # was ~2h, so 3h covers it with room.
    "truncation_lookahead_hours": 3,
    # margin_mode ground truth only exists after the mid-Aug-14 migration.
    "start_date": "2026-08-14T00:00:00",
}

METHOD_TAG = "v7"   # v7: vanish window from LOCAL cadence snapped to TIER_FREQUENCIES

# trackers/tier_manager.py TIER_FREQUENCIES, in seconds. Cycles are 1 minute:
#   vip/T1: 1   T2: 5   T3: 15   T4: 30   T5: 60
# These are the ONLY sampling rates the collector produces, so a measured
# median landing between two of them is jitter, not a real rate. Demotions are
# never persisted — tier_manager computes `demoted` and only logger.debug's it
# — so the tier a wallet held at a given moment cannot be read from the DB. It
# has to be inferred from row spacing and snapped back onto this ladder.
TIER_CADENCES_S = (60, 300, 900, 1800, 3600)

DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "data" / "twap.db")


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


def last_non_null(values):
    for v in reversed(values):
        if v is not None:
            return v
    return None


def now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def connect(path):
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def snap_to_tier_cadence(measured_s):
    """Snap a measured median gap onto the TIER_CADENCES_S ladder.

    Ratio distance, not absolute: the ladder is multiplicative, so 600s is
    genuinely ambiguous between 300 and 900 in a way that 1800 vs 1801 is not.
    Anything above the top rung snaps to it — a gap longer than T5 is missed
    polls, not a slower tier.
    """
    if not measured_s or measured_s <= 0:
        return None
    return min(TIER_CADENCES_S, key=lambda c: abs(math.log(measured_s / c)))


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


def deactivations(con):
    """address -> sorted list of deactivate times.

    Loaded in one pass because the table is small and the alternative is a
    query per episode. A deactivate means the wallet left the tiered set and
    the collector stopped polling it entirely — both perp_snapshots and
    liquidation_snapshots go silent, for a reason that has nothing to do with
    what the position did.
    """
    out = {}
    for r in con.execute(
            """SELECT address, event_time FROM whale_lifecycle_events
               WHERE event_type = 'deactivate' AND event_time >= ?
               ORDER BY address, event_time""", (PARAMS["start_date"],)):
        t = parse_ts(r["event_time"])
        if t:
            out.setdefault(r["address"], []).append(t)
    return out


# ---------------------------------------------------------------------------
# stage 2 — cadence + approach splitting
# ---------------------------------------------------------------------------

def local_perp_cadence(con, address, coin, t_ref):
    """Median inter-row gap over the last few perp rows at or before t_ref.

    Replaces the +/-24h median used through v6. Tier tracks position size, so
    a wallet that de-risks gets demoted and sampled more slowly — and a
    two-day median hides that behind the fast rows on either side.
    0x90b5e0c5 sat at 1800s through an entire morning while the wide median
    still read ~300s, which set a 15-minute vanish window on a wallet being
    polled every 30 minutes. Three of its episodes then "vanished" while the
    collector was working perfectly.

    Local, and short: what matters is the sampling rate at the moment the rows
    stopped, not the wallet's average over the surrounding days.
    """
    rows = con.execute(
        """SELECT snapshot_time FROM perp_snapshots
           WHERE address = ? AND coin = ? AND snapshot_time <= ?
           ORDER BY snapshot_time DESC LIMIT ?""",
        (address, coin, iso(t_ref), PARAMS["cadence_window_rows"])).fetchall()
    ts = sorted(t for t in (parse_ts(r["snapshot_time"]) for r in rows) if t)
    if len(ts) < 2:
        return None
    gaps = [(ts[i] - ts[i - 1]).total_seconds() for i in range(1, len(ts))]
    gaps = [g for g in gaps if g > 0]
    return statistics.median(gaps) if gaps else None


def perp_cadence(con, address, coin, t0, t1):
    """Median inter-row gap in perp_snapshots over a wide window.

    Kept only as the FALLBACK for local_perp_cadence() — used when the local
    window holds fewer than two rows, which happens on a single-row episode at
    the very start of a wallet's collection history.

    This is a DIFFERENT CLOCK from the liquidation_snapshots cadence.
    liquidation_snapshots only writes while the position sits within 20% of
    its liquidation price, so during an active ladder the position drops out
    of that window repeatedly and its measured cadence is meaningless as a
    basis for "how long is too long between rows". perp_snapshots writes every
    cycle for any OPEN position, so absence there is the real signal.
    """
    rows = con.execute(
        """SELECT snapshot_time FROM perp_snapshots
           WHERE address = ? AND snapshot_time BETWEEN ? AND ? AND coin = ?
           ORDER BY snapshot_time""",
        (address, iso(t0), iso(t1), coin)).fetchall()
    ts = [parse_ts(r["snapshot_time"]) for r in rows]
    ts = [t for t in ts if t]
    if len(ts) < 2:
        return None
    gaps = [(ts[i] - ts[i - 1]).total_seconds() for i in range(1, len(ts))]
    gaps = [g for g in gaps if g > 0]
    return statistics.median(gaps) if gaps else None


def observed_cadence(times):
    """Median inter-row gap across the liquidation_snapshots series for the
    pair. Used only for the approach split, which reads that table."""
    gaps = [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]
    gaps = [g for g in gaps if g > 0]
    return statistics.median(gaps) if gaps else None


def split_approaches(rows, cadence_s):
    """Hysteresis + relative-gap split. Returns lists of row-index ranges.

    NOTE: still splits on gaps in the CENSORED table (rows above 20% distance
    are never written) and still uses that table's own cadence. Both are known
    problems — one wallet's episodes span genuine position boundaries (side
    flip, size->0) that this cannot see. Left alone deliberately: segmenting on
    position identity is its own rule change and wants its own version.
    """
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
    """Any row for this pair in the window — used for the vanish check."""
    return con.execute(
        """SELECT snapshot_time, size, entry_price FROM perp_snapshots
           WHERE address = ? AND snapshot_time > ? AND snapshot_time <= ?
             AND coin = ? ORDER BY snapshot_time LIMIT 1""",
        (address, iso(after), iso(until), coin)).fetchone()


def reentry_check(con, address, coin, last_seen, last_entry, last_size, last_liq):
    """Classify the position that comes back after a disappearance.

    Returns (entry_price_reset, note) where entry_price_reset is 1 only when
    the returning position PROVES the old one is gone.

    SAME SIDE: an add blends the entry price, so a changed entry is not
    evidence by itself — this is what made NBIS (a laddered accumulation) read
    as a death. Blending always lands the new entry BETWEEN the old entry and
    the add price, so it can never cross the old liquidation level. An entry
    beyond that level therefore cannot be a blend.

    SIDE FLIPPED: the old position is definitively gone, but a flip is also
    exactly what a voluntary capitulate-and-flip looks like (episode #5). So a
    flip asserts nothing about HOW it ended — fall through to the price check.
    """
    back = con.execute(
        """SELECT snapshot_time, size, entry_price FROM perp_snapshots
           WHERE address = ? AND snapshot_time > ? AND snapshot_time <= ?
             AND coin = ? ORDER BY snapshot_time LIMIT 1""",
        (address, iso(last_seen),
         iso(last_seen + timedelta(hours=PARAMS["reentry_hours"])), coin)).fetchone()
    if back is None or back["entry_price"] is None or last_entry is None:
        return 0, None

    old_short = (last_size or 0) < 0
    new_short = (back["size"] or 0) < 0

    if old_short != new_short:
        return 0, "side_flip"

    if last_liq is None:
        return 0, "same_side_no_liq"

    new_entry = back["entry_price"]
    beyond = new_entry > last_liq if old_short else new_entry < last_liq
    return (1, "entry_beyond_liq") if beyond else (0, "blended_add")


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
    """Two independent readings, deliberately not conflated.

    VERDICT (dead/survived) needs only the LAST row plus a price check —
    episode #1 had exactly one row and was still decidable, because the whole
    liquidation ladder ran inside one polling gap and price visibly crossed
    the stored liq level.

    CLASSIFIABLE means DEFENSE was readable — did the wallet cut size, move
    liq, inject margin. That needs a series, hence min_rows. An episode can
    have a solid verdict and an unreadable defense channel; that combination
    is exactly the one to exclude from any defense-vs-outcome statistic.

    TRUNCATION outranks everything except is_open. If the wallet was dropped
    from collection around the time its rows stopped, then "the position
    disappeared" and "we stopped looking" have identical signatures, and every
    downstream test is reading a gap we manufactured. That includes
    entry_price_reset: across a blackout of hours the wallet can close and
    open a genuinely new position, which the reset check would score as a
    death. Refuse the verdict rather than guess it. 0x30afce2f/xyz:SP500 was
    the case — passive and frozen at 0.74%, rows stop, deactivate two hours
    later, and the explorer showed a voluntary close a day afterwards. The old
    rules called it survived and happened to be right by luck.

    PRECEDENCE below that. The returning position outranks the price check. A
    liquidated position comes back as a NEW position, so a same-side return
    with a BLENDED entry proves the old one was never closed — it was cut and
    re-added inside the gap. In that case a price crossing means only that the
    stored liq level was stale, not that anything died. NBIS was the case: a
    75-minute gap in the middle of a ladder, size 1646 -> 845 -> 1953, entry
    walking down the whole way, position still open a week later.
    """
    defense_readable = int(ep["n_rows"] >= PARAMS["min_rows"])

    if ep["is_open"]:
        return "open", defense_readable
    if ep["position_vanished"] == 1 and ep["truncated"] == 1:
        return "truncated", 0
    if ep["entry_price_reset"] == 1:
        return "dead", defense_readable
    if ep["reentry_note"] == "blended_add":
        # Same side, entry re-blended: it survived the gap.
        return "survived", defense_readable
    if ep["position_vanished"] == 1:
        if ep["price_traversed_liq"] == 1:
            return "dead", defense_readable
        if ep["price_traversed_liq"] == 0:
            return "survived", defense_readable
        # no price data and no liq level to test against — genuinely unknown
        return "ambiguous", 0
    return "survived", defense_readable


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build(con, labels_only=False, verbose=False):
    mv = method_version()
    data_tail = parse_ts(con.execute(
        "SELECT MAX(snapshot_time) AS t FROM liquidation_snapshots").fetchone()["t"])
    deacts = deactivations(con)
    out = []

    for pair in candidate_pairs(con, labels_only):
        addr, coin = pair["address"], pair["coin"]
        rows = pair_series(con, addr, coin)
        if not rows:
            continue
        times = [parse_ts(r["snapshot_time"]) for r in rows]
        times = [t for t in times if t]
        # A one-row pair still has a decidable outcome — do not drop it here.
        # cadence comes back None and the vanish window falls back to its floor.
        cadence = observed_cadence(times) if len(times) > 1 else None

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

            # Backward pad only. A single-row episode has a zero-width window,
            # which returns no perp rows at all; padding forward instead would
            # risk pulling in a post-vanish re-entry and faking a size change.
            pad = timedelta(seconds=max(cadence or 60, 60) * 2)
            prows = perp_window(con, addr, coin, t_start - pad, t_end)
            size_changed, size_pct, liq_moved = defense_columns(prows, erows)
            mode = margin_mode_for(con, addr, coin, t_start - pad, t_end)

            rate = baseline_liq_rate(con, addr, coin, t_start)
            span_h = max((t_end - t_start).total_seconds() / 3600.0, 1e-9)
            expected = (rate or 0) * span_h
            liq_informative = int(expected >= PARAMS["min_expected_liq_reprints"])

            last_seen = parse_ts(prows[-1]["snapshot_time"]) if prows else t_end
            last_size = prows[-1]["size"] if prows else erows[-1]["size"]
            last_entry = prows[-1]["entry_price"] if prows else erows[-1]["entry_price"]

            # Take the last NON-NULL liq level from either source. A single
            # null on the final perp row was silently forcing 'ambiguous'.
            last_liq = last_non_null(
                [r["liquidation_price"] for r in prows] +
                [r["liq_price"] for r in erows])

            # size can be null too; fall back to the side label.
            if last_size is None:
                last_size = last_non_null([r["size"] for r in erows])
            if last_size is None:
                side = last_non_null([r["side"] for r in erows])
                is_short = bool(side and str(side).lower().startswith(("s", "short")))
            else:
                is_short = last_size < 0

            # Vanish window, on the PERP clock, measured LOCALLY at last_seen
            # and snapped onto the tier ladder. Falls back to the wide-window
            # median, then to the liquidation-table cadence, then to the floor.
            p_cad = local_perp_cadence(con, addr, coin, last_seen)
            if p_cad is None:
                p_cad = perp_cadence(con, addr, coin,
                                     t_start - timedelta(hours=24),
                                     t_end + timedelta(hours=24))
            snapped = snap_to_tier_cadence(p_cad or cadence)
            vanish_win = timedelta(seconds=max((snapped or p_cad or cadence or 60)
                                               * PARAMS["vanish_multiple"],
                                               PARAMS["vanish_floor_minutes"] * 60))
            nxt = next_perp_row(con, addr, coin, last_seen, last_seen + vanish_win)
            vanished = int(nxt is None)

            is_open = int(data_tail is not None
                          and (data_tail - last_seen) < vanish_win)

            # Was the wallet dropped from collection around the time its rows
            # stopped? Window runs from the start of the approach to a
            # lookahead past last_seen, because the deactivate is written by
            # the tier refresh and lags the actual drop-out.
            trunc_end = last_seen + timedelta(
                hours=PARAMS["truncation_lookahead_hours"])
            truncated = int(any(t_start <= d <= trunc_end
                                for d in deacts.get(addr, ())))

            traversed = None
            entry_reset = 0
            reentry_note = None
            if vanished and not is_open:
                traversed = price_traversed(con, coin, last_seen,
                                            last_seen + vanish_win, last_liq, is_short)
                entry_reset, reentry_note = reentry_check(
                    con, addr, coin, last_seen, last_entry, last_size, last_liq)

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
                # Raw local median, and the tier rung it snapped to. Keep both:
                # a large gap between them means the wallet was migrating tiers
                # inside the measurement window.
                "obs_cadence_s": p_cad or cadence,
                "cadence_snapped_s": snapped,
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
                "truncated": truncated,
                "is_open": is_open,
                "reentry_note": reentry_note,
                "built_at": now_iso(),
            }
            ep["verdict"], ep["classifiable"] = decide(ep)
            ep.pop("is_open")
            note = ep.pop("reentry_note")
            out.append(ep)
            if verbose:
                tail = f"  [{note}]" if note else ""
                if ep["truncated"]:
                    tail += "  [blackout]"
                cad = f"{int(snapped)}s" if snapped else "?"
                print(f"  {ep['key'][:44]} {coin:12} "
                      f"min={min_dist:.3f} n={len(erows):4} cad={cad:>5} "
                      f"-> {ep['verdict']}{tail}")
    return mv, out


COLS = ["key", "method_version", "address", "coin", "approach_start",
        "min_dist_time", "approach_end", "min_dist", "peak_notional",
        "margin_mode", "obs_cadence_s", "cadence_snapped_s", "n_rows",
        "max_gap_s", "size_changed", "size_change_pct", "liq_moved",
        "liq_reprints_per_hr", "liq_informative", "last_seen",
        "position_vanished", "price_traversed_liq", "entry_price_reset",
        "truncated", "verdict", "classifiable", "built_at"]


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

    n_trunc = sum(1 for e in eps if e["truncated"])
    if n_trunc:
        print(f"  ({n_trunc} episode(s) overlap a collection blackout)")
    cad_tally = {}
    for e in eps:
        cad_tally[e["cadence_snapped_s"]] = cad_tally.get(e["cadence_snapped_s"], 0) + 1
    print("  cadence: " + ", ".join(
        f"{int(k) if k else '?'}s={v}"
        for k, v in sorted(cad_tally.items(), key=lambda kv: (kv[0] is None, kv[0]))))

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