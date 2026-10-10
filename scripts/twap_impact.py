#!/usr/bin/env python3
"""
twap_impact.py

What does absorbing a TWAP cost? Price moves around every JOINED order from
absorption_roster.py (status 'ok' in data/absorption_orders.csv - run that
first), all signed so that + = AGAINST the TWAP (up for a buy, down for a sell):

    cost_bps     TWAP vwap vs p0 (last trade before placement): what it paid
    move_end     p0 -> last trade at chain_end
    move_30      p0 -> last trade 30 min after the end (did the move stay?)
    pre_30       30 min BEFORE placement -> p0. Control: if this is positive
                 too, TWAPs are placed after the move already started (chasing)
    particip     TWAP notional / ALL notional printed on the coin while it ran

One order cannot tell impact from market drift. Averaged over many buys and
sells, drift cancels and the signed mean is what is left. The summary also
gives the DRIFT-FREE end move: (raw buy move - raw sell move) / 2, which
removes any common market trend even if buys and sells are unbalanced.
se = standard error of the mean; |mean| < 2*se = not distinguishable from 0.

Slices use the same rule as absorption_roster.py: the wallet's taker prints
0-5 s after each 30 s mark counted from placed_at_ms.

Output: data/twap_impact.csv (one row per order) + printed summary.
Read-only (mode=ro). Run on the box:
    venv/bin/python scripts/twap_impact.py
"""
import sqlite3
import time

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
ORDERS_CSV = "data/absorption_orders.csv"
OUT_CSV = "data/twap_impact.csv"
SLICE_MS = 30_000
GRID_LO_MS, GRID_HI_MS = 0, 5_000
H_MS = 30 * 60_000
PART_BINS = [0, 0.01, 0.03, 0.10, 1.0]
PART_LABELS = ["<1%", "1-3%", "3-10%", ">10%"]


def load_tape(conn, coin, lo, hi):
    t = pd.read_sql_query(
        "SELECT ts, px, notional, side, buyer, seller FROM tape_prints "
        "WHERE coin = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        conn, params=(coin, lo, hi))
    return {"ts": t["ts"].to_numpy("int64"), "px": t["px"].to_numpy("float64"),
            "ntl": t["notional"].to_numpy("float64"),
            "tb": (t["side"] == "B").to_numpy(),
            "buyer": t["buyer"].fillna(-1).to_numpy("int64"),
            "seller": t["seller"].fillna(-1).to_numpy("int64")}


def last_px(tp, t, strict=False):
    """Last trade price before (strict) or at t. NaN if t is past the tape."""
    if t > tp["ts"][-1]:
        return np.nan
    i = np.searchsorted(tp["ts"], t, "left" if strict else "right") - 1
    return tp["px"][i] if i >= 0 else np.nan


def one_order(tp, wid, side, placed, end_ms):
    sign = 1.0 if side == "BUY" else -1.0
    i0 = np.searchsorted(tp["ts"], placed, "left")
    i1 = np.searchsorted(tp["ts"], end_ms + SLICE_MS, "right")
    tb, buyer, seller = tp["tb"][i0:i1], tp["buyer"][i0:i1], tp["seller"][i0:i1]
    ts = tp["ts"][i0:i1]
    taker = (tb & (buyer == wid)) if side == "BUY" else (~tb & (seller == wid))
    off = (ts - placed) % SLICE_MS
    tw = taker & (off >= GRID_LO_MS) & (off <= GRID_HI_MS)
    ntl, px = tp["ntl"][i0:i1], tp["px"][i0:i1]
    if not tw.any():
        return None
    vwap = ntl[tw].sum() / (ntl[tw] / px[tw]).sum()
    in_run = ts <= end_ms
    mkt = ntl[in_run].sum()
    p0 = last_px(tp, placed, strict=True)
    pre = last_px(tp, placed - H_MS, strict=True)
    pe = last_px(tp, end_ms)
    p30 = last_px(tp, end_ms + H_MS)
    bps = lambda a, b: sign * (a - b) / b * 1e4
    return {"p0": p0, "vwap": vwap, "p_end": pe, "p_30": p30,
            "cost_bps": bps(vwap, p0), "move_end": bps(pe, p0),
            "move_30": bps(p30, p0), "pre_30": bps(p0, pre),
            "raw_end": (pe - p0) / p0 * 1e4,
            "twap_ntl": ntl[tw].sum(), "mkt_ntl": mkt,
            "particip": ntl[tw].sum() / mkt if mkt else np.nan}


def mean_se(x):
    x = x.dropna()
    if len(x) < 2:
        return np.nan, np.nan
    return x.mean(), x.std(ddof=1) / np.sqrt(len(x))


def summarise(df, label):
    n = len(df)
    print(f"== {label}: {n} orders "
          f"({(df.side == 'BUY').sum()} buy / {(df.side == 'SELL').sum()} sell), "
          f"median participation {100 * df.particip.median():.1f}%")
    rows = []
    for col in ["pre_30", "cost_bps", "move_end", "move_30"]:
        m, se = mean_se(df[col])
        rows.append({"metric": col, "mean": round(m, 2), "se": round(se, 2),
                     "median": round(df[col].median(), 2)})
    b, s = df[df.side == "BUY"].raw_end, df[df.side == "SELL"].raw_end
    if len(b) > 1 and len(s) > 1:
        m = (b.mean() - s.mean()) / 2
        se = np.sqrt(b.var(ddof=1) / len(b) + s.var(ddof=1) / len(s)) / 2
        rows.append({"metric": "end_driftfree", "mean": round(m, 2),
                     "se": round(se, 2), "median": np.nan})
    print(pd.DataFrame(rows).to_string(index=False, na_rep=""))
    print()


def main():
    t_start = time.time()
    od = pd.read_csv(ORDERS_CSV)
    od = od[od["status"] == "ok"]
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    out = []
    for coin, oc in od.groupby("coin", sort=False):
        keys = list(zip(oc["address"], oc["placed_ms"].astype("int64")))
        ends = {}
        for a, p in keys:
            r = conn.execute("SELECT chain_end_s FROM orders WHERE address = ? "
                             "AND placed_at_ms = ? AND symbol = ?", (a, int(p), coin)).fetchone()
            ends[(a, p)] = r[0] * 1000 if r and r[0] else None
        q = ",".join("?" * len(set(oc["address"])))
        ids = dict(conn.execute(f"SELECT address, id FROM tape_addresses WHERE address IN ({q})",
                                sorted(set(oc["address"]))).fetchall())
        valid = [v for v in ends.values() if v]
        tp = load_tape(conn, coin, int(oc["placed_ms"].min()) - H_MS - 60_000,
                       max(valid) + H_MS + 60_000)
        for o in oc.itertuples(index=False):
            key = (o.address, int(o.placed_ms))
            if not ends.get(key) or o.address not in ids:
                continue
            r = one_order(tp, ids[o.address], o.side, int(o.placed_ms), ends[key])
            if r:
                out.append({"coin": coin, "address": o.address, "side": o.side,
                            "placed_ms": int(o.placed_ms), "minutes": o.minutes, **r})
        print(f"{coin}: {len(oc)} orders ({time.time() - t_start:.0f}s)")
        del tp
    conn.close()

    df = pd.DataFrame(out)
    df.round(4).to_csv(OUT_CSV, index=False)
    print(f"\nCSV: {OUT_CSV}   runtime {time.time() - t_start:.0f}s")
    print("all moves in bps, signed + = AGAINST the TWAP\n")
    summarise(df, "ALL COINS")
    for coin, g in df.groupby("coin", sort=False):
        if len(g) >= 20:
            summarise(g, coin)
    df["pbin"] = pd.cut(df["particip"], PART_BINS, labels=PART_LABELS)
    print("== by participation (all coins)")
    rows = []
    for lab, g in df.groupby("pbin", observed=True):
        mc, sc = mean_se(g["cost_bps"])
        me, se = mean_se(g["move_end"])
        m3, s3 = mean_se(g["move_30"])
        mp, sp = mean_se(g["pre_30"])
        rows.append({"particip": lab, "n": len(g), "pre_30": f"{mp:.1f}±{sp:.1f}",
                     "cost": f"{mc:.1f}±{sc:.1f}", "move_end": f"{me:.1f}±{se:.1f}",
                     "move_30": f"{m3:.1f}±{s3:.1f}"})
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()