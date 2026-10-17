#!/usr/bin/env python3
"""
TWAP placement event study for HYPE perp. Implements twap_event_hypotheses.md
(H-TWAP3: 60 min, default; H-TWAP4: 15 min, run with --horizon 15).

Default run = FIT half only (Sep 4 - Sep 30). The held-out half (Oct 1 - Oct 14)
is only computed with --holdout, and should be opened exactly once.

Usage (on the box, from the repo root):
    $PY scripts/twap_event_study.py              # fit half
    $PY scripts/twap_event_study.py --holdout    # held-out half, ONCE
    $PY scripts/twap_event_study.py --holdout --horizon 15   # H-TWAP4 verdict
"""
import argparse
import sqlite3
import sys

import numpy as np
import pandas as pd

ORDERS_FROM = "2026-09-03"
ORDERS_TO = "2026-10-16"
FIT = ("2026-09-04 00:00", "2026-09-30 23:00")
HOLDOUT = ("2026-10-01 00:00", "2026-10-14 23:00")
SIZE_MIN = 7000.0
MAX_DUR = 1440
MERGE_MIN = 60
HORIZON = 60
ALL_H = (15, 60, 240)
PRICE_TOL = pd.Timedelta(minutes=5)
TOP_DROP = 5
ROUND_TRIP_BPS = 9.0


def ts(s):
    return pd.to_datetime(s, utc=True, format="mixed", errors="coerce")


def side_sign(v):
    u = str(v).strip().upper()
    if u in ("B", "BUY", "LONG", "BID"):
        return 1.0
    if u in ("A", "S", "SELL", "SHORT", "ASK"):
        return -1.0
    raise ValueError(f"unknown side value: {v!r}")


def load_events(con):
    q = """SELECT address, side, size, duration_minutes, first_seen_at, chain_status, executed_sz
           FROM orders
           WHERE symbol='HYPE' AND product_type='PERP'
             AND first_seen_at >= ? AND first_seen_at < ?
             AND duration_minutes > 0 AND duration_minutes <= ? AND size >= ?"""
    o = pd.read_sql_query(q, con, params=(ORDERS_FROM, ORDERS_TO, MAX_DUR, SIZE_MIN))
    o["sign"] = o["side"].map(side_sign)
    o["start"] = ts(o["first_seen_at"])
    o = o.dropna(subset=["start"]).sort_values(["address", "sign", "start"]).reset_index(drop=True)

    # merge same address + side within 60 min of the event's FIRST order
    ev_id, cur, anchor, prev_key = [], -1, None, None
    for addr, sg, st in zip(o["address"], o["sign"], o["start"]):
        key = (addr, sg)
        if key != prev_key or (st - anchor) > pd.Timedelta(minutes=MERGE_MIN):
            cur += 1
            anchor = st
        ev_id.append(cur)
        prev_key = key
    o["ev"] = ev_id
    o["ex_known"] = o["executed_sz"].notna()
    g = o.groupby("ev")
    ev = pd.DataFrame({
        "address": g["address"].first(),
        "sign": g["sign"].first(),
        "t0": g["start"].min(),
        "size": g["size"].sum(),
        "n_orders": g.size(),
        "ex_sz": g["executed_sz"].sum(min_count=1),
        "all_ex_known": g["ex_known"].all(),
    }).sort_values("t0").reset_index(drop=True)
    ev["exec_share"] = np.where(ev["all_ex_known"], ev["ex_sz"] / ev["size"], np.nan)
    return o, ev


def load_prices(con):
    q = """SELECT timestamp, price FROM snapshots
           WHERE symbol='HYPE' AND price IS NOT NULL AND price > 0
             AND timestamp >= ? AND timestamp < ?"""
    p = pd.read_sql_query(q, con, params=(ORDERS_FROM, "2026-10-17"))
    p["t"] = ts(p["timestamp"])
    p = p.dropna(subset=["t"]).sort_values("t").drop_duplicates("t")
    return p[["t", "price"]]


def price_at(prices, times):
    left = pd.DataFrame({"t": pd.DatetimeIndex(times)}).reset_index()
    left = left.sort_values("t")
    m = pd.merge_asof(left, prices, on="t", direction="backward", tolerance=PRICE_TOL)
    return m.sort_values("index")["price"].to_numpy()


def add_outcomes(ev, prices):
    p0 = price_at(prices, ev["t0"])
    ev["p0"] = p0
    for h in ALL_H:
        p1 = price_at(prices, ev["t0"] + pd.Timedelta(minutes=h))
        ev[f"S{h}"] = ev["sign"] * np.log(p1 / p0) * 1e4
    return ev


def mean_t(x):
    x = pd.Series(x).dropna()
    n = len(x)
    if n < 3:
        return n, float("nan"), float("nan"), float("nan")
    m, se = x.mean(), x.std() / np.sqrt(n)
    return n, m, se, m / se


def non_overlap(ev):
    keep, last = [], None
    for t in ev["t0"]:
        ok = last is None or t >= last + pd.Timedelta(minutes=HORIZON)
        keep.append(ok)
        if ok:
            last = t
    return ev[keep]


def line(label, x):
    n, m, se, t = mean_t(x)
    return f"  {label:<34} n={n:<4} mean={m:+7.2f} bps  se={se:6.2f}  t={t:+.2f}"


def report(ev, label, is_holdout):
    col = f"S{HORIZON}"
    d = ev.dropna(subset=[col])
    print(f"\n=== {label}: events {len(ev)}, with price: {len(d)}, dropped (no price): {len(ev) - len(d)} ===")
    print(f"  BUY {int((d['sign'] > 0).sum())}  SELL {int((d['sign'] < 0).sum())}  "
          f"addresses {d['address'].nunique()}  orders merged: {int(d['n_orders'].sum())}")
    top_addr = d["address"].value_counts().head(5)
    print("  top addresses by events:", ", ".join(f"{a[:10]}:{n}" for a, n in top_addr.items()))

    hname = "H-TWAP3" if HORIZON == 60 else "H-TWAP4" if HORIZON == 15 else f"horizon {HORIZON}"
    print(f"\n{hname}  signed return over {HORIZON} min after placement")
    n, m, se, t = mean_t(d[col])
    print(line("all events", d[col]))
    dd = d[d[col].abs().rank(method="first", ascending=False) > TOP_DROP]
    n2, m2, se2, t2 = mean_t(dd[col])
    print(line(f"without top {TOP_DROP} |S|", dd[col]))
    no = non_overlap(d)
    n3, m3, se3, t3 = mean_t(no[col])
    print(line("non-overlapping subset", no[col]))
    pa = d.groupby("address")[col].mean()
    n4, m4, se4, t4 = mean_t(pa)
    print(line("1 per address (address mean)", pa))
    top = d["address"].value_counts().index[0]
    print(line(f"without top address {top[:10]}", d.loc[d["address"] != top, col]))
    print(f"  share S>0: {(d[col] > 0).mean():.3f}   median: {d[col].median():+.2f} bps")
    print(f"  vs ~{ROUND_TRIP_BPS} bps taker round trip")

    print("\nDescriptive")
    for h in ALL_H:
        if h != HORIZON:
            print(line(f"horizon {h} min", d[f"S{h}"]))
    print(line("BUY events", d.loc[d["sign"] > 0, col]))
    print(line("SELL events", d.loc[d["sign"] < 0, col]))
    print(line("size 7k-25k", d.loc[d["size"] < 25000, col]))
    print(line("size >= 25k", d.loc[d["size"] >= 25000, col]))
    if is_holdout:
        print(line("executed >= 90%", d.loc[d["exec_share"] >= 0.9, col]))
        print(line("executed < 50%", d.loc[d["exec_share"] < 0.5, col]))
        print(f"  events with unknown execution: {int(d['exec_share'].isna().sum())}")
        ok = [t >= 2, t2 >= 2, m3 > 0]
        names = ["t>=2", f"t>=2 without top {TOP_DROP}", "non-overlap mean>0"]
        if HORIZON == 15:
            ok.append(m4 > 0)
            names.append("per-address mean>0")
        verdict = "PASS" if all(ok) else "FAIL"
        print(f"\n{hname} held-out verdict: {verdict}  (" +
              ", ".join(f"{nm}: {o}" for nm, o in zip(names, ok)) + ")")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/twap.db")
    ap.add_argument("--holdout", action="store_true",
                    help="compute the held-out half (Oct 1-14). Open exactly once.")
    ap.add_argument("--out", default="data/twap_events")
    ap.add_argument("--horizon", type=int, default=60, choices=[15, 60],
                    help="primary horizon: 60 = H-TWAP3, 15 = H-TWAP4")
    a = ap.parse_args()
    global HORIZON
    HORIZON = a.horizon

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=60)
    orders, ev = load_events(con)
    prices = load_prices(con)
    con.close()
    if len(prices) == 0:
        sys.exit("no HYPE prices found in snapshots")

    period, label = (HOLDOUT, "HELD-OUT") if a.holdout else (FIT, "FIT")
    lo, hi = pd.Timestamp(period[0], tz="UTC"), pd.Timestamp(period[1], tz="UTC")
    ev = ev[(ev["t0"] >= lo) & (ev["t0"] <= hi)].reset_index(drop=True)
    print(f"large orders loaded (all periods): {len(orders)}  ->  events in {label}: {len(ev)}")
    ev = add_outcomes(ev, prices)
    report(ev, label, a.holdout)
    out = f"{a.out}_{'holdout' if a.holdout else 'fit'}_{HORIZON}.csv"
    ev.to_csv(out, index=False)
    print(f"\nevents written to {out}")


if __name__ == "__main__":
    main()