#!/usr/bin/env python3
"""
slice_impact.py

Mechanical impact of TWAP slices, measured in the seconds around EACH slice -
short enough that minute-scale trends and TWAP timing (see twap_impact.py:
TWAPs are placed after price already moved their way) barely register.

Uses the JOINED orders (status 'ok') in data/absorption_orders.csv - run
absorption_roster.py first. Slices = the wallet's taker prints 0-5 s after
each 30 s mark from placed_at_ms (same rule as the roster).

Prices use a MID proxy, not the last trade: mid(t) = average of the last
taker-BUY price and the last taker-SELL price before t. The last trade alone
bounces between bid and ask - right after a buy slice it is the slice's own
fill at the ask, which would fake a push. Mid is left empty when either side's
last print is older than STALE_MS (thin markets).

Per slice, signed + = AGAINST the TWAP (up for a buy, down for a sell), bps:
    cost      slice vwap vs mid just before the slice (half spread + book walk)
    move_5    mid 5 s after the slice vs mid before: the immediate push
    move_25   mid 25 s after (just before the next slice): what STAYED
    pre_25    mid 25 s before -> mid before. Control; near 0, or slightly
              negative if the previous slice's push is fading

Summary: per coin, mean +/- se where se is across ORDERS (a TWAP's slices are
not independent), plus medians. Then dose-response: slices split into size
quartiles WITHIN each coin (Q1 smallest), pooled across coins.

Output: data/twap_slices.csv (one row per slice) + printed summary.
Read-only (mode=ro). Run on the box:
    venv/bin/python scripts/slice_impact.py
"""
import sqlite3
import time

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
ORDERS_CSV = "data/absorption_orders.csv"
OUT_CSV = "data/twap_slices.csv"
SLICE_MS = 30_000
GRID_LO_MS, GRID_HI_MS = 0, 5_000
STALE_MS = 60_000
METRICS = ["pre_25", "cost", "move_5", "move_25"]


def load_tape(conn, coin, lo, hi):
    t = pd.read_sql_query(
        "SELECT ts, px, notional, side, buyer, seller FROM tape_prints "
        "WHERE coin = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        conn, params=(coin, lo, hi))
    tb = (t["side"] == "B").to_numpy()
    ts, px = t["ts"].to_numpy("int64"), t["px"].to_numpy("float64")
    return {"ts": ts, "px": px, "ntl": t["notional"].to_numpy("float64"), "tb": tb,
            "buyer": t["buyer"].fillna(-1).to_numpy("int64"),
            "seller": t["seller"].fillna(-1).to_numpy("int64"),
            "b_ts": ts[tb], "b_px": px[tb], "s_ts": ts[~tb], "s_px": px[~tb]}


def mid_before(tp, t):
    """Vectorised mid proxy strictly before each time in t (NaN when stale)."""
    ib = np.searchsorted(tp["b_ts"], t, "left") - 1
    is_ = np.searchsorted(tp["s_ts"], t, "left") - 1
    ok = (ib >= 0) & (is_ >= 0)
    ibc, isc = np.clip(ib, 0, None), np.clip(is_, 0, None)
    fresh = ok & (t - tp["b_ts"][ibc] <= STALE_MS) & (t - tp["s_ts"][isc] <= STALE_MS)
    return np.where(fresh, (tp["b_px"][ibc] + tp["s_px"][isc]) / 2, np.nan)


def order_slices(tp, wid, side, placed, end_ms):
    i0 = np.searchsorted(tp["ts"], placed, "left")
    i1 = np.searchsorted(tp["ts"], end_ms + SLICE_MS, "right")
    ts = tp["ts"][i0:i1]
    if side == "BUY":
        taker = tp["tb"][i0:i1] & (tp["buyer"][i0:i1] == wid)
    else:
        taker = ~tp["tb"][i0:i1] & (tp["seller"][i0:i1] == wid)
    off = (ts - placed) % SLICE_MS
    tw = taker & (off >= GRID_LO_MS) & (off <= GRID_HI_MS)
    if not tw.any():
        return None
    s = pd.DataFrame({"ts": ts[tw], "ntl": tp["ntl"][i0:i1][tw],
                      "sz": tp["ntl"][i0:i1][tw] / tp["px"][i0:i1][tw]})
    s = s.groupby("ts").agg(ntl=("ntl", "sum"), sz=("sz", "sum")).reset_index()
    t = s["ts"].to_numpy("int64")
    sign = 1.0 if side == "BUY" else -1.0
    m0 = mid_before(tp, t)
    m_pre = mid_before(tp, t - 25_000)
    m5 = mid_before(tp, t + 5_000)
    m25 = mid_before(tp, t + 25_000)
    vwap = (s["ntl"] / s["sz"]).to_numpy()
    bps = lambda a, b: sign * (a - b) / b * 1e4
    s["cost"], s["move_5"] = bps(vwap, m0), bps(m5, m0)
    s["move_25"], s["pre_25"] = bps(m25, m0), bps(m0, m_pre)
    # drop slices whose +25 s point runs past the end of the loaded tape
    s.loc[t + 25_000 > tp["ts"][-1], METRICS] = np.nan
    return s


def by_order(df, cols):
    """Per-order means, then mean and se across orders."""
    per = df.groupby("order")[cols].mean()
    n = per.notna().sum()
    return per.mean(), per.std(ddof=1) / np.sqrt(n), n


def main():
    t_start = time.time()
    od = pd.read_csv(ORDERS_CSV)
    od = od[od["status"] == "ok"]
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    parts = []
    for coin, oc in od.groupby("coin", sort=False):
        ends = {}
        for a, p in zip(oc["address"], oc["placed_ms"].astype("int64")):
            r = conn.execute("SELECT chain_end_s FROM orders WHERE address = ? "
                             "AND placed_at_ms = ? AND symbol = ?", (a, int(p), coin)).fetchone()
            if r and r[0]:
                ends[(a, int(p))] = r[0] * 1000
        addrs = sorted(set(oc["address"]))
        q = ",".join("?" * len(addrs))
        ids = dict(conn.execute(f"SELECT address, id FROM tape_addresses WHERE address IN ({q})",
                                addrs).fetchall())
        tp = load_tape(conn, coin, int(oc["placed_ms"].min()) - 120_000,
                       max(ends.values()) + 120_000)
        for o in oc.itertuples(index=False):
            key = (o.address, int(o.placed_ms))
            if key not in ends or o.address not in ids:
                continue
            s = order_slices(tp, ids[o.address], o.side, key[1], ends[key])
            if s is not None:
                s.insert(0, "order", f"{o.address}|{key[1]}")
                s.insert(0, "side", o.side)
                s.insert(0, "coin", coin)
                parts.append(s)
        print(f"{coin}: {len(oc)} orders ({time.time() - t_start:.0f}s)")
        del tp
    conn.close()

    df = pd.concat(parts, ignore_index=True)
    df.round(4).to_csv(OUT_CSV, index=False)
    print(f"\nCSV: {OUT_CSV}  {len(df):,} slices  runtime {time.time() - t_start:.0f}s")
    print("bps, signed + = AGAINST the TWAP; mean +/- se across orders | median over slices\n")

    rows = []
    for coin, g in [("ALL", df)] + list(df.groupby("coin", sort=False)):
        m, se, n = by_order(g, METRICS)
        r = {"coin": coin, "orders": g["order"].nunique(), "slices": len(g),
             "med_slice_$": int(g["ntl"].median())}
        for c in METRICS:
            r[c] = f"{m[c]:.2f}±{se[c]:.2f} | {g[c].median():.2f}"
        rows.append(r)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(pd.DataFrame(rows).to_string(index=False))

    df["q"] = df.groupby("coin")["ntl"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 4, labels=["Q1", "Q2", "Q3", "Q4"]))
    print("\n== dose-response: slice size quartile WITHIN each coin, pooled (Q1 = smallest)")
    rows = []
    for q, g in df.groupby("q", observed=True):
        m, se, n = by_order(g, METRICS)
        r = {"quartile": q, "slices": len(g), "med_slice_$": int(g["ntl"].median())}
        for c in METRICS:
            r[c] = f"{m[c]:.2f}±{se[c]:.2f}"
        rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()