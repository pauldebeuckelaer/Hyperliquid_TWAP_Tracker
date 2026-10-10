#!/usr/bin/env python3
"""
twap_informed.py

Are TWAP placers INFORMED, or do they just ride momentum?

TWAPs start after price already moved their way (pre_30 > 0 in twap_impact.py)
and price keeps going their way afterwards. That continuation could be plain
momentum - a 30-min move tending to continue, whoever trades - or information.

1. Momentum baseline, no TWAPs: on each coin, a timestamp every CONTROL_STEP
   across the order span. Per horizon H, fit  fwd_H = a + b * pre_30  (raw bps,
   last trade price). b > 0 = momentum, b < 0 = mean reversion.
2. For every JOINED order (status 'ok' in data/absorption_orders.csv), from
   placement: predicted = a + b * its own pre_30;
   excess = sign * (actual fwd_H - predicted),  sign + for BUY, - for SELL.
   Mean excess clearly > 0 (more than 2 se) = informed beyond momentum.

Limitation: the baseline is "all times". If TWAPs cluster in unusual hours
(e.g. high volatility), momentum may differ then; matching by hour would be
the refinement.

Output: data/twap_informed.csv (one row per order) + printed summary.
Read-only (mode=ro). Run on the box:
    venv/bin/python scripts/twap_informed.py              # joined orders only
    venv/bin/python scripts/twap_informed.py --status all # every order (for twap_wallets.py)
"""
import argparse
import sqlite3
import time

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
ORDERS_CSV = "data/absorption_orders.csv"
OUT_CSV = "data/twap_informed.csv"
PRE_MS = 30 * 60_000
HORIZONS = {"60m": 60 * 60_000, "4h": 4 * 3_600_000}
CONTROL_STEP = 5 * 60_000
MIN_ORDERS = 20


def load_prices(conn, coin, lo, hi):
    t = pd.read_sql_query("SELECT ts, px FROM tape_prints WHERE coin = ? AND ts BETWEEN ? AND ? "
                          "ORDER BY ts", conn, params=(coin, lo, hi))
    return t["ts"].to_numpy("int64"), t["px"].to_numpy("float64")


def px_at(ts, px, t):
    """Last trade price at or before each t (NaN before start / after end)."""
    t = np.asarray(t, dtype="int64")
    i = np.searchsorted(ts, t, "right") - 1
    out = px[np.clip(i, 0, None)].astype(float)
    out[(i < 0) | (t > ts[-1])] = np.nan
    return out


def bps(a, b):
    return (a - b) / b * 1e4


def mean_se(x):
    x = pd.Series(x).dropna()
    return (x.mean(), x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else (np.nan, np.nan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", choices=["ok", "all"], default="ok",
                    help="'ok' = joined orders only; 'all' = every order in the roster CSV "
                         "(informedness needs no tape join - only placement, side, price)")
    args = ap.parse_args()
    t_start = time.time()
    od = pd.read_csv(ORDERS_CSV)
    if args.status == "ok":
        od = od[od["status"] == "ok"]
    od = od.copy()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    out, fits = [], []
    hmax = max(HORIZONS.values())
    for coin, oc in od.groupby("coin", sort=False):
        lo = int(oc["placed_ms"].min()) - PRE_MS - 60_000
        hi = int(oc["placed_ms"].max()) + hmax + 60_000
        ts, px = load_prices(conn, coin, lo, hi)
        if len(ts) == 0:
            continue
        # ---- momentum baseline on a regular grid of control times ----
        ct = np.arange(ts[0] + PRE_MS, ts[-1] - hmax, CONTROL_STEP)
        p0c, prec = px_at(ts, px, ct), px_at(ts, px, ct - PRE_MS)
        pre_c = bps(p0c, prec)
        fit = {}
        for h, hms in HORIZONS.items():
            fwd_c = bps(px_at(ts, px, ct + hms), p0c)
            ok = ~np.isnan(pre_c) & ~np.isnan(fwd_c)
            x, y = pre_c[ok], fwd_c[ok]
            b = np.cov(x, y)[0, 1] / np.var(x, ddof=1)
            a = y.mean() - b * x.mean()
            r2 = np.corrcoef(x, y)[0, 1] ** 2
            fit[h] = (a, b)
            fits.append({"coin": coin, "horizon": h, "controls": int(ok.sum()),
                         "a": round(a, 2), "b": round(b, 3), "r2": round(r2, 3)})
        # ---- every joined order ----
        t0 = oc["placed_ms"].to_numpy("int64")
        p0 = px_at(ts, px, t0 - 1)
        pre = bps(p0, px_at(ts, px, t0 - PRE_MS))
        sign = np.where(oc["side"].to_numpy() == "BUY", 1.0, -1.0)
        rec = pd.DataFrame({"coin": coin, "address": oc["address"].to_numpy(),
                            "side": oc["side"].to_numpy(), "placed_ms": t0,
                            "exec_ntl": oc["exec_ntl"].to_numpy(),
                            "pre_30": sign * pre})
        for h, hms in HORIZONS.items():
            a, b = fit[h]
            fwd = bps(px_at(ts, px, t0 + hms), p0)
            rec[f"fwd_{h}"] = sign * fwd
            rec[f"pred_{h}"] = sign * (a + b * pre)
            rec[f"excess_{h}"] = sign * (fwd - (a + b * pre))
        out.append(rec)
        print(f"{coin}: {len(oc)} orders, {len(ct):,} control times ({time.time() - t_start:.0f}s)")
    conn.close()

    df = pd.concat(out, ignore_index=True)
    df.round(4).to_csv(OUT_CSV, index=False)
    print(f"\nCSV: {OUT_CSV}   runtime {time.time() - t_start:.0f}s")

    print("\n== momentum baseline (no TWAPs): fwd = a + b * pre_30, raw bps")
    print(pd.DataFrame(fits).to_string(index=False))

    print("\n== TWAPs, signed + = in the TWAP's direction; mean ± se across orders")
    rows = []
    groups = [("ALL", df)] + [(c, g) for c, g in df.groupby("coin", sort=False)
                              if len(g) >= MIN_ORDERS]
    df["big"] = df.groupby("coin")["exec_ntl"].transform(lambda x: x >= x.median())
    groups += [("ALL big half", df[df["big"]]), ("ALL small half", df[~df["big"]])]
    for name, g in groups:
        if len(g) < 2:
            continue
        r = {"group": name, "n": len(g)}
        m, s = mean_se(g["pre_30"])
        r["pre_30"] = f"{m:.1f}±{s:.1f}"
        for h in HORIZONS:
            for col in ("fwd", "pred", "excess"):
                m, s = mean_se(g[f"{col}_{h}"])
                r[f"{col}_{h}"] = f"{m:.1f}±{s:.1f}"
        rows.append(r)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()