#!/usr/bin/env python3
"""
markout_by_regime.py

Fill-precise markouts for one wallet on one coin, split by role (maker/taker)
and by regime from tape_impulse_buckets (impulse / trend / quiet).

Markout per fill, in bps, from the wallet's point of view:
    sign * (p(ts + H) - fill_px) / fill_px * 1e4
    sign      = +1 if the wallet bought, -1 if it sold
    p(ts + H) = last tape print on the coin at or before ts + H
Positive = price moved in the wallet's favour after the fill.
Reported notional-weighted, per horizon H in HORIZONS_MIN.

Role: tape_prints.side is the aggressor's side.
    side 'B' -> buyer is taker, seller is maker
    side 'A' -> seller is taker, buyer is maker

Exclusions (per horizon):
    - fills whose [ts, ts+H] overlaps a tape_gaps row for this coin
    - fills whose ts+H lies past the end of the tape
    - p(ts+H) older than MAX_STALE_S before ts+H (catches unrecorded gaps)
    - self-trades (buyer == seller == wallet) are dropped entirely
    NOTE: tape_gaps only records gaps from Sep 5 2026 17:12 UTC on. Earlier
    holes are caught only by the staleness check.

Regime of a fill = regime of its 5-min bucket:
    impulse if is_impulse, else trend if is_trend, else quiet.
    Impulse takes precedence over trend. is_trend looks ahead by design,
    which is fine here: this labels fills, it does not predict.
    'unlabeled' = bucket not in tape_impulse_buckets -> re-run
    build_impulse_buckets.py first.

grid_pct = share of the coin's 5-min buckets in that regime, over the
wallet's active range. Compare with each regime's share of notional to see
whether the wallet leans into or away from a regime.

GROSS markouts: fees and rebates are NOT included.

Read-only (opens the DB with mode=ro). Run on the box:
    venv/bin/python scripts/markout_by_regime.py 0xecb63c --coin BTC
"""
import argparse
import sqlite3
import sys

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
HORIZONS_MIN = [5, 30, 60]
BUCKET_MS = 5 * 60 * 1000
MAX_STALE_S = 120
REGIMES = ["impulse", "trend", "quiet", "unlabeled"]


def resolve_wallet(conn, prefix):
    rows = conn.execute(
        "SELECT id, address FROM tape_addresses WHERE address LIKE ?",
        (prefix.lower() + "%",),
    ).fetchall()
    if len(rows) != 1:
        shown = ", ".join(a for _, a in rows[:5])
        sys.exit(f"prefix {prefix!r} matches {len(rows)} addresses {shown}")
    return rows[0]


def load_fills(conn, coin, wid):
    q = """SELECT tid, ts, px, notional, side, buyer, seller
           FROM tape_prints
           WHERE coin = ? AND (buyer = ? OR seller = ?)"""
    f = pd.read_sql_query(q, conn, params=(coin, wid, wid))
    n_self = int((f["buyer"] == f["seller"]).sum())
    f = f[f["buyer"] != f["seller"]].copy()
    is_buyer = f["buyer"] == wid
    taker_is_buyer = f["side"] == "B"
    f["sign"] = np.where(is_buyer, 1, -1)
    f["role"] = np.where(is_buyer == taker_is_buyer, "taker", "maker")
    return f.sort_values("ts").reset_index(drop=True), n_self


def load_prices(conn, coin, t0_ms, t1_ms):
    """1-second last-price series for the coin over [t0, t1]."""
    q = """SELECT ts, px FROM tape_prints
           WHERE coin = ? AND ts BETWEEN ? AND ? ORDER BY ts"""
    p = pd.read_sql_query(q, conn, params=(coin, t0_ms, t1_ms))
    p["s"] = p["ts"] // 1000
    p = p.drop_duplicates("s", keep="last")
    return pd.DataFrame({"target_s": p["s"].astype("int64").values,
                         "matched_s": p["s"].astype("int64").values,
                         "p_after": p["px"].values})


def gap_mask(gaps, ts, horizon_ms):
    end = ts + horizon_ms
    bad = np.zeros(len(ts), dtype=bool)
    for d, r in zip(gaps["disconnected"], gaps["reconnected"]):
        bad |= (ts <= r) & (end >= d)
    return bad


def add_markouts(f, prices, gaps):
    ts = f["ts"].values
    px = f["px"].values
    tape_end_s = prices["target_s"].iloc[-1]
    dropped = {}
    for h in HORIZONS_MIN:
        h_ms = h * 60_000
        left = pd.DataFrame({"target_s": ((ts + h_ms) // 1000).astype("int64")})
        m = pd.merge_asof(left, prices, on="target_s", direction="backward")
        stale = ((m["target_s"] - m["matched_s"]) > MAX_STALE_S).values
        past_end = (m["target_s"] > tape_end_s).values
        in_gap = gap_mask(gaps, ts, h_ms)
        p_after = m["p_after"].values.astype(float)
        bad = stale | past_end | in_gap | np.isnan(p_after)
        mo = f["sign"].values * (p_after - px) / px * 1e4
        mo[bad] = np.nan
        f[f"mo{h}"] = mo
        dropped[h] = {"gap": int(in_gap.sum()),
                      "past_end": int(past_end.sum()),
                      "stale": int((stale & ~past_end & ~in_gap).sum())}
    return f, dropped


def add_regimes(conn, f, coin):
    b = pd.read_sql_query(
        "SELECT bucket_ts, is_impulse, is_trend FROM tape_impulse_buckets WHERE coin = ?",
        conn, params=(coin,))
    f["bucket_ts"] = (f["ts"] // BUCKET_MS) * BUCKET_MS
    f = f.merge(b, on="bucket_ts", how="left")
    f["regime"] = np.select(
        [f["is_impulse"] == 1, f["is_trend"] == 1, f["is_impulse"].notna()],
        ["impulse", "trend", "quiet"], default="unlabeled")

    lo, hi = f["bucket_ts"].min(), f["bucket_ts"].max()
    grid = b[(b["bucket_ts"] >= lo) & (b["bucket_ts"] <= hi)]
    grid_reg = np.select([grid["is_impulse"] == 1, grid["is_trend"] == 1],
                         ["impulse", "trend"], default="quiet")
    grid_pct = pd.Series(grid_reg).value_counts(normalize=True) * 100
    return f, grid_pct


def nw(g, col):
    ok = g[col].notna()
    w = g.loc[ok, "notional"]
    return (g.loc[ok, col] * w).sum() / w.sum() if w.sum() > 0 else np.nan


def report(f, grid_pct):
    rows = []
    for role in ["maker", "taker"]:
        fr = f[f["role"] == role]
        if fr.empty:
            continue
        role_ntl = fr["notional"].sum()
        for regime in REGIMES + ["ALL"]:
            g = fr if regime == "ALL" else fr[fr["regime"] == regime]
            if g.empty:
                continue
            row = {"role": role, "regime": regime, "fills": len(g),
                   "ntl_$M": round(g["notional"].sum() / 1e6, 2),
                   "ntl_pct": round(100 * g["notional"].sum() / role_ntl, 1),
                   "grid_pct": round(grid_pct.get(regime, np.nan), 1)
                   if regime in grid_pct.index else np.nan}
            for h in HORIZONS_MIN:
                row[f"bps_{h}m"] = round(nw(g, f"mo{h}"), 2)
            rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet", help="address or unique prefix, e.g. 0xecb63c")
    ap.add_argument("--coin", default="BTC")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    wid, address = resolve_wallet(conn, args.wallet)

    f, n_self = load_fills(conn, args.coin, wid)
    if f.empty:
        sys.exit(f"{address}: no fills on {args.coin}")

    t0 = int(f["ts"].iloc[0])
    t1 = int(f["ts"].iloc[-1]) + max(HORIZONS_MIN) * 60_000
    prices = load_prices(conn, args.coin, t0, t1)

    gaps = pd.read_sql_query("SELECT disconnected, reconnected, coins FROM tape_gaps", conn)
    gaps = gaps[gaps["coins"].str.split(",").apply(lambda c: args.coin in c)]

    f, dropped = add_markouts(f, prices, gaps)
    f, grid_pct = add_regimes(conn, f, args.coin)
    conn.close()

    first = pd.to_datetime(t0, unit="ms").strftime("%Y-%m-%d %H:%M")
    last = pd.to_datetime(int(f["ts"].iloc[-1]), unit="ms").strftime("%Y-%m-%d %H:%M")
    print(f"{address}  {args.coin}  {first} -> {last} UTC")
    print(f"fills: {len(f)} ({(f['role'] == 'maker').sum()} maker / "
          f"{(f['role'] == 'taker').sum()} taker), self-trades dropped: {n_self}")
    for h, d in dropped.items():
        print(f"  {h:>2}m excluded: gap {d['gap']}, past end {d['past_end']}, stale {d['stale']}")
    print("GROSS markouts in bps, notional-weighted, wallet's point of view (+ = favourable)\n")

    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(report(f, grid_pct).to_string(index=False))


if __name__ == "__main__":
    main()