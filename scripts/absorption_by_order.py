#!/usr/bin/env python3
"""
absorption_by_order.py

Who absorbed ONE TWAP order, and what inventory were they carrying?

Takes a single Era-2 order from `orders` (by address + placement time),
finds its slices on the tape (prints where the TWAP wallet is TAKER inside
[placed - 1 min, chain_end + 1 min], kept only if they sit on the order's
30 s slice grid - other taker prints by the same wallet, e.g. a market
order placed mid-TWAP, are listed as EXCLUDED), checks the tape total
against the chain-verified executed_sz / executed_ntl, then ranks the
makers on the other side.

Per maker (top N by absorbed size):
    fills, slices     maker fills against the TWAP / distinct TWAP slices hit
    size, pct         size absorbed (coin units) and share of the order
    net_window        the maker's NET flow on this coin over the whole window,
                      from ALL its prints (not only those against the TWAP);
                      + = bought, - = sold. Covers every wallet.
    pos_start/end     ABSOLUTE position (signed, + long / - short) from the
                      last perp_snapshots row at or before the window start /
                      end. Empty when the wallet is not snapshotted. Snapshot
                      age is shown so a stale reading is visible.

Reading it: filling a BUY TWAP says little (any maker with asks gets
lifted). What a maker REFUSES matters - a maker long at pos_start that
sells hard in net_window during a SELL TWAP is unloading, not absorbing.

Read-only (mode=ro). Run on the box:
    venv/bin/python scripts/absorption_by_order.py \
        --address 0x468fe3f0e20b0bf463083a4054687cddc71a9186 --placed "2026-10-09 11:47"
"""
import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
PAD_MS = 60_000
SLICE_MS = 30_000     # Hyperliquid TWAP slice interval
GRID_TOL_MS = 5_000   # a taker moment further than this from the grid is not the TWAP


def find_order(conn, address, placed):
    """The Era-2 order placed by `address` within 1 minute of `placed` (UTC)."""
    t = datetime.strptime(placed, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    lo = int(t.timestamp() * 1000) - 60_000
    hi = int(t.timestamp() * 1000) + 120_000
    rows = conn.execute(
        "SELECT symbol, side, placed_at_ms, chain_end_s, executed_sz, executed_ntl, "
        "chain_status FROM orders WHERE address = ? AND placed_at_ms BETWEEN ? AND ? "
        "ORDER BY placed_at_ms", (address.lower(), lo, hi)).fetchall()
    if not rows:
        raise SystemExit(f"no order for {address} placed near {placed} UTC")
    if len(rows) > 1:
        print(f"note: {len(rows)} orders in that minute, using the first")
    keys = ["symbol", "side", "placed_ms", "end_s", "exec_sz", "exec_ntl", "status"]
    return dict(zip(keys, rows[0]))


def address_id(conn, address):
    r = conn.execute("SELECT id FROM tape_addresses WHERE address = ?",
                     (address.lower(),)).fetchone()
    if r is None:
        raise SystemExit(f"{address} never appears on the tape")
    return r[0]


def load_window(conn, coin, lo, hi):
    return pd.read_sql_query(
        "SELECT ts, px, notional, side, buyer, seller FROM tape_prints "
        "WHERE coin = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        conn, params=(coin, lo, hi))


def snapshot_position(conn, address, coin, at_ms):
    """Signed size from the last perp_snapshots row at or before at_ms.
    snapshot_time is TEXT in mixed formats ('T' or space, optional suffix), so
    rows are fetched by whole-day bounds and parsed here, not compared as text."""
    at = datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    d0 = (at - timedelta(days=1)).strftime("%Y-%m-%d")
    d1 = (at + timedelta(days=1)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT snapshot_time, size FROM perp_snapshots "
        "WHERE address = ? AND coin = ? AND snapshot_time >= ? AND snapshot_time < ?",
        (address, coin, d0, d1)).fetchall()
    best = None
    for st, size in rows:
        t = datetime.strptime(st.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        if t <= at and (best is None or t > best[0]):
            best = (t, size)
    if best is None:
        return None, None
    return best[1], (at - best[0]).total_seconds() / 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True, help="TWAP wallet")
    ap.add_argument("--placed", required=True, help="placement time, 'YYYY-MM-DD HH:MM' UTC")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    o = find_order(conn, args.address, args.placed)
    coin = o["symbol"]
    lo, hi = o["placed_ms"] - PAD_MS, o["end_s"] * 1000 + PAD_MS
    wid = address_id(conn, args.address)
    t = load_window(conn, coin, lo, hi)

    taker_buy = t["side"] == "B"
    is_twap = (taker_buy & (t["buyer"] == wid)) | (~taker_buy & (t["seller"] == wid))
    tw = t[is_twap].copy()
    tw["maker"] = tw["seller"].where(taker_buy[is_twap], tw["buyer"])
    tw["size"] = tw["notional"] / tw["px"]

    # ---- keep only moments on the TWAP's 30 s grid ----
    # Slices land at a fixed offset from placement (seen: ~1.3-2 s). Any other
    # taker print by the same wallet in the window (a market order, a second
    # order) is off-grid and would otherwise be counted as TWAP flow.
    off = (tw["ts"] - o["placed_ms"]) % SLICE_MS
    med = off.groupby(tw["ts"]).first().median()
    dist = (off - med).abs()
    dist = np.minimum(dist, SLICE_MS - dist)
    on_grid = dist <= GRID_TOL_MS
    excl = tw[~on_grid]
    tw = tw[on_grid]

    start =datetime.fromtimestamp(o["placed_ms"] / 1000, tz=timezone.utc)
    print(f"{coin} {o['side']} TWAP by {args.address}  placed {start:%Y-%m-%d %H:%M:%S} UTC  "
          f"{(o['end_s'] - o['placed_ms'] / 1000) / 60:.0f} min  status {o['status']}")
    print(f"  chain: {o['exec_sz']:.5f} {coin}  ${o['exec_ntl']:,.0f}")
    print(f"  tape : {tw['size'].sum():.5f} {coin}  ${tw['notional'].sum():,.0f}  "
          f"in {len(tw)} prints, {tw['ts'].nunique()} slices, "
          f"{tw['maker'].nunique()} distinct makers")
    if not excl.empty:
        print(f"  EXCLUDED off-grid (not TWAP): {excl['size'].sum():.5f} {coin}  "
              f"${excl['notional'].sum():,.0f}  in {len(excl)} prints at "
              f"{excl['ts'].nunique()} moment(s), slice offset {med / 1000:.1f}s:")
        for ts_, grp in excl.groupby("ts"):
            when = datetime.fromtimestamp(ts_ / 1000, tz=timezone.utc)
            print(f"      {when:%Y-%m-%d %H:%M:%S}  {grp['size'].sum():.4f} {coin}")
    if tw.empty:
        raise SystemExit("no TWAP prints found on the tape in this window")

    # ---- makers against the TWAP ----
    g = tw.groupby("maker").agg(fills=("ts", "size"), slices=("ts", "nunique"),
                                size=("size", "sum"), ntl=("notional", "sum"))
    g["pct"] = 100 * g["ntl"] / tw["notional"].sum()
    g = g.sort_values("size", ascending=False).head(args.top)

    # ---- each top maker's net flow over the whole window, all its prints ----
    t["size"] = t["notional"] / t["px"]
    bought = t.groupby("buyer")["size"].sum()
    sold = t.groupby("seller")["size"].sum()
    g["net_window"] = (bought.reindex(g.index).fillna(0)
                       - sold.reindex(g.index).fillna(0))

    # ---- absolute positions from perp_snapshots ----
    ids = ",".join(str(int(i)) for i in g.index)
    addr = dict(conn.execute(
        f"SELECT id, address FROM tape_addresses WHERE id IN ({ids})").fetchall())
    rows = []
    for mid, r in g.iterrows():
        a = addr.get(int(mid), "?")
        p0, age0 = snapshot_position(conn, a, coin, lo)
        p1, age1 = snapshot_position(conn, a, coin, hi)
        rows.append({"maker": a, "fills": int(r.fills), "slices": int(r.slices),
                     "size": round(r["size"], 4), "pct": round(r.pct, 1),
                     "net_window": round(r.net_window, 2),
                     "pos_start": None if p0 is None else round(p0, 2),
                     "age0_min": None if age0 is None else round(age0, 1),
                     "pos_end": None if p1 is None else round(p1, 2),
                     "age1_min": None if age1 is None else round(age1, 1)})
    conn.close()

    out = pd.DataFrame(rows)
    print(f"\ntop {len(out)} makers absorbing it (size in {coin}; "
          f"net_window + = bought, - = sold; pos from perp_snapshots, blank = not tracked)\n")
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(out.to_string(index=False, na_rep=""))


if __name__ == "__main__":
    main()