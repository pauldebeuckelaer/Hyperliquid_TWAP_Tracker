#!/usr/bin/env python3
"""
TWAP pressure test for HYPE perp. Implements twap_hypotheses.md (frozen at commit 6bfc145).

H-TWAP1: announced pressure A(t) predicts next-hour HYPE return R(t), positive sign.
H-TWAP2: descriptive, does executed pressure E(t) explain R(t) better than A(t)?

Default run = FIT half only (Sep 4 - Sep 30). The held-out half (Oct 1 - Oct 14)
is only computed with --holdout, and should be opened exactly once.

Usage (on the box, from the repo root):
    $PY scripts/twap_pressure_test.py              # fit half
    $PY scripts/twap_pressure_test.py --holdout    # held-out half, ONCE
"""
import argparse
import sqlite3
import sys

import numpy as np
import pandas as pd

ORDERS_FROM = "2026-08-28"      # week-long TWAPs running on Sep 4 must be counted
ORDERS_TO = "2026-10-16"
FIT = ("2026-09-04 00:00", "2026-09-30 23:00")
HOLDOUT = ("2026-10-01 00:00", "2026-10-14 23:00")
OCT_START = pd.Timestamp("2026-10-01", tz="UTC")
HORIZON_MIN = 60
PRICE_TOL = pd.Timedelta(minutes=5)   # a price must exist within 5 min before t / t+60
TOP_DROP = 10
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


def load_orders(con):
    q = """SELECT side, size, duration_minutes, first_seen_at, canceled_at, completed_at,
                  final_progress_percent, executed_sz
           FROM orders
           WHERE symbol='HYPE' AND product_type='PERP'
             AND first_seen_at >= ? AND first_seen_at < ?"""
    o = pd.read_sql_query(q, con, params=(ORDERS_FROM, ORDERS_TO))
    o = o[(o["size"] > 0) & (o["duration_minutes"] > 0)].copy()
    o["sign"] = o["side"].map(side_sign)
    o["start"] = ts(o["first_seen_at"])
    o["canceled"] = ts(o["canceled_at"])
    o["planned_end"] = o["start"] + pd.to_timedelta(o["duration_minutes"], unit="min")
    # visible window: canceled_at if set, else first_seen_at + duration (fixes 24h poller cap)
    o["end_vis"] = o["canceled"].where(o["canceled"].notna(), o["planned_end"])
    o = o[o["start"].notna() & o["end_vis"].notna()]
    o["end_vis"] = o[["end_vis", "start"]].max(axis=1)

    o["pace_ann"] = o["sign"] * o["size"] / o["duration_minutes"]

    # executed amount (frozen rule)
    sep_rule = np.where(o["canceled"].isna(), o["size"],
                        o["size"] * o["final_progress_percent"].fillna(0) / 100.0)
    is_oct = o["start"] >= OCT_START
    use_chain = is_oct & o["executed_sz"].notna()
    o["exec_amt"] = np.where(use_chain, o["executed_sz"], sep_rule)
    o["exec_src"] = np.where(use_chain, "executed_sz", np.where(is_oct, "rule_fallback", "rule"))
    run_min = ((o["end_vis"] - o["start"]).dt.total_seconds() / 60.0).clip(lower=1.0)
    o["pace_exec"] = o["sign"] * o["exec_amt"] / run_min
    return o


def load_prices(con):
    q = """SELECT timestamp, price FROM snapshots
           WHERE symbol='HYPE' AND price IS NOT NULL AND price > 0
             AND timestamp >= ? AND timestamp < ?"""
    p = pd.read_sql_query(q, con, params=("2026-09-03", ORDERS_TO))
    p["t"] = ts(p["timestamp"])
    p = p.dropna(subset=["t"]).sort_values("t").drop_duplicates("t")
    return p[["t", "price"]]


def price_at(prices, times):
    left = pd.DataFrame({"t": times})
    m = pd.merge_asof(left, prices, on="t", direction="backward", tolerance=PRICE_TOL)
    return m["price"].to_numpy()


def build_hours(orders, prices, period):
    hours = pd.date_range(period[0], period[1], freq="h", tz="UTC")
    s = orders["start"].to_numpy()
    e = orders["end_vis"].to_numpy()
    pa = orders["pace_ann"].to_numpy()
    pe = orders["pace_exec"].to_numpy()
    A, E, nvis = [], [], []
    for h in hours.to_numpy():
        vis = (s <= h) & (e > h)
        A.append(pa[vis].sum())
        E.append(pe[vis].sum())
        nvis.append(int(vis.sum()))
    p0 = price_at(prices, hours)
    p1 = price_at(prices, hours + pd.Timedelta(minutes=HORIZON_MIN))
    df = pd.DataFrame({"t": hours, "A": A, "E": E, "n_visible": nvis, "p0": p0, "p1": p1})
    df["R"] = np.log(df["p1"] / df["p0"])
    return df


def tstat(r, n):
    if n < 3 or abs(r) >= 1:
        return float("nan")
    return r * np.sqrt((n - 2) / (1 - r * r))


def corr_block(x, y):
    n = len(x)
    pr = float(np.corrcoef(x, y)[0, 1]) if n > 2 else float("nan")
    sr = float(np.corrcoef(pd.Series(x).rank(), pd.Series(y).rank())[0, 1]) if n > 2 else float("nan")
    return n, pr, tstat(pr, n), sr, tstat(sr, n)


def drop_top(df, col, k):
    keep = df[col].abs().rank(method="first", ascending=False) > k
    return df[keep]


def ols(y, X):
    X = np.column_stack([np.ones(len(y))] + X)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = len(y) - X.shape[1]
    s2 = resid @ resid / dof
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    return beta, beta / se


def report(df, label, is_holdout):
    d = df.dropna(subset=["R"])
    print(f"\n=== {label}: {d['t'].min()} .. {d['t'].max()} ===")
    print(f"hours: {len(df)}  with price both ends: {len(d)}  dropped (no price): {len(df) - len(d)}")
    print(f"hours with A != 0: {(d['A'] != 0).sum()}   mean visible TWAPs/hour: {d['n_visible'].mean():.1f}")
    print(f"R: mean {d['R'].mean() * 1e4:+.2f} bps, sd {d['R'].std() * 1e4:.1f} bps")

    n, pr, pt, sr, st = corr_block(d["A"].to_numpy(), d["R"].to_numpy())
    dd = drop_top(d, "A", TOP_DROP)
    n2, pr2, pt2, _, _ = corr_block(dd["A"].to_numpy(), dd["R"].to_numpy())
    print("\nH-TWAP1  A(t) -> R(t)")
    print(f"  Pearson  r={pr:+.3f} t={pt:+.2f}   (n={n})")
    print(f"  Spearman r={sr:+.3f} t={st:+.2f}")
    print(f"  Pearson without top {TOP_DROP} |A| hours: r={pr2:+.3f} t={pt2:+.2f} (n={n2})")

    q = pd.qcut(d["A"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    g = d.groupby(q, observed=True)
    tab = pd.DataFrame({
        "A_mean": g["A"].mean(),
        "R_bps": g["R"].mean() * 1e4,
        "se_bps": g["R"].std() * 1e4 / np.sqrt(g.size()),
        "n": g.size(),
    })
    print("\n  Quintiles of A (1 = most selling, 5 = most buying):")
    print(tab.round(2).to_string())

    qa = pd.qcut(d["A"].abs().rank(method="first"), 5, labels=False)
    top = d[qa == 4]
    trade = np.sign(top["A"]) * top["R"] * 1e4
    print(f"\n  Top |A| quintile traded in sign of A: {trade.mean():+.2f} bps/trade "
          f"(se {trade.std() / np.sqrt(len(trade)):.2f}, n={len(trade)}) vs ~{ROUND_TRIP_BPS} bps round trip")

    nE, prE, ptE, srE, stE = corr_block(d["E"].to_numpy(), d["R"].to_numpy())
    beta, tb = ols(d["R"].to_numpy(), [d["A"].to_numpy(), d["E"].to_numpy()])
    cAE = float(np.corrcoef(d["A"], d["E"])[0, 1])
    print("\nH-TWAP2  (descriptive)")
    print(f"  E(t) -> R(t): Pearson r={prE:+.3f} t={ptE:+.2f}   Spearman r={srE:+.3f} t={stE:+.2f}")
    print(f"  corr(A, E) = {cAE:+.3f}")
    print(f"  R ~ A + E:  t_A={tb[1]:+.2f}  t_E={tb[2]:+.2f}")

    if is_holdout:
        ok = [pt >= 2, st >= 2, pt2 >= 2]
        verdict = "PASS" if all(ok) else "FAIL"
        print(f"\nH-TWAP1 held-out verdict: {verdict}  "
              f"(Pearson t>=2: {ok[0]}, Spearman t>=2: {ok[1]}, without top {TOP_DROP}: {ok[2]})")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/twap.db")
    ap.add_argument("--holdout", action="store_true",
                    help="compute the held-out half (Oct 1-14). Open exactly once.")
    ap.add_argument("--out", default="data/twap_pressure")
    a = ap.parse_args()

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=60)
    orders = load_orders(con)
    prices = load_prices(con)
    con.close()

    print(f"orders loaded: {len(orders)}  (BUY {int((orders['sign'] > 0).sum())}, "
          f"SELL {int((orders['sign'] < 0).sum())})")
    print("exec source:", orders["exec_src"].value_counts().to_dict())
    print(f"price rows: {len(prices)}  {prices['t'].min()} .. {prices['t'].max()}")
    if len(prices) == 0:
        sys.exit("no HYPE prices found in snapshots")

    period, label = (HOLDOUT, "HELD-OUT") if a.holdout else (FIT, "FIT")
    df = build_hours(orders, prices, period)
    report(df, label, a.holdout)
    out = f"{a.out}_{'holdout' if a.holdout else 'fit'}.csv"
    df.to_csv(out, index=False)
    print(f"\nhourly series written to {out}")


if __name__ == "__main__":
    main()