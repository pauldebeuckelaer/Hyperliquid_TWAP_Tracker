#!/usr/bin/env python3
"""
twap_wallets.py

Which TWAP wallets are consistently RIGHT? Ranks placers by the excess move
after their orders (beyond what the coin's own momentum predicts), from
data/twap_informed.csv - run  twap_informed.py --status all  first.

DECISIONS, NOT ORDERS: orders by the same wallet, coin and side placed less
than --gap-min apart are one decision (a batch fired together shares one
timing; counting it as five bets would fake certainty). A decision's excess
is the mean over its orders.

Per wallet (>= --min-dec decisions):
    dec, orders   decisions / orders behind them
    coins         distinct coins traded
    buy%          share of BUY decisions
    ntl_m         executed notional ($M)
    ex60 / ex4h   mean excess (bps, + = price went the TWAP's way beyond momentum)
    t60 / t4h     mean / se across decisions. Ranked on t, not on the mean.
    hit60/hit4h   % of decisions with excess > 0 - robust to one giant move

LUCK CHECK: with N wallets and no edge, about 2.5 % land above t = 2 by chance.
The summary prints that expected count next to the real one; a real count not
clearly above it means the "top wallets" are noise. Horizons overlap between a
wallet's decisions, so t is somewhat optimistic for very active wallets.

Output: data/twap_wallets.csv + printed ranking. No database access.
    venv/bin/python scripts/twap_wallets.py
    venv/bin/python scripts/twap_wallets.py --min-dec 20 --show 25
"""
import argparse

import numpy as np
import pandas as pd

IN_CSV = "data/twap_informed.csv"
OUT_CSV = "data/twap_wallets.csv"


def decisions(df, gap_ms):
    df = df.sort_values(["address", "coin", "side", "placed_ms"]).copy()
    same = ((df["address"] == df["address"].shift()) & (df["coin"] == df["coin"].shift())
            & (df["side"] == df["side"].shift())
            & (df["placed_ms"] - df["placed_ms"].shift() < gap_ms))
    df["dec_id"] = (~same).cumsum()
    return df.groupby("dec_id").agg(
        address=("address", "first"), coin=("coin", "first"), side=("side", "first"),
        placed_ms=("placed_ms", "first"), orders=("placed_ms", "size"),
        ntl=("exec_ntl", "sum"), ex60=("excess_60m", "mean"), ex4h=("excess_4h", "mean"))


def tstat(x):
    x = x.dropna()
    if len(x) < 2 or x.std(ddof=1) == 0:
        return np.nan, np.nan, len(x)
    return x.mean(), x.mean() / (x.std(ddof=1) / np.sqrt(len(x))), len(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-dec", type=int, default=10)
    ap.add_argument("--gap-min", type=float, default=10)
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    df = pd.read_csv(IN_CSV)
    dec = decisions(df, args.gap_min * 60_000)
    rows = []
    for addr, g in dec.groupby("address"):
        m60, t60, n60 = tstat(g["ex60"])
        m4h, t4h, n4h = tstat(g["ex4h"])
        rows.append({"address": addr, "dec": len(g), "orders": int(g["orders"].sum()),
                     "coins": g["coin"].nunique(),
                     "buy%": round(100 * (g["side"] == "BUY").mean()),
                     "ntl_m": round(g["ntl"].sum() / 1e6, 1),
                     "ex60": m60, "t60": t60,
                     "hit60": 100 * (g["ex60"].dropna() > 0).mean() if n60 else np.nan,
                     "ex4h": m4h, "t4h": t4h,
                     "hit4h": 100 * (g["ex4h"].dropna() > 0).mean() if n4h else np.nan})
    w = pd.DataFrame(rows)
    w.round(2).to_csv(OUT_CSV, index=False)
    q = w[w["dec"] >= args.min_dec].copy()

    print(f"{len(df):,} orders -> {len(dec):,} decisions from {dec['address'].nunique():,} wallets; "
          f"{len(q)} wallets with >= {args.min_dec} decisions")
    for h in ("t60", "t4h"):
        n = q[h].notna().sum()
        print(f"  {h}: {(q[h] > 2).sum()} wallets above t=2 (chance alone: ~{0.025 * n:.1f}), "
              f"{(q[h] < -2).sum()} below t=-2 (chance alone: ~{0.025 * n:.1f})")
    print(f"CSV: {OUT_CSV}\n")

    cols = ["address", "dec", "orders", "coins", "buy%", "ntl_m",
            "ex60", "t60", "hit60", "ex4h", "t4h", "hit4h"]
    fmt = lambda d: d[cols].round({"ex60": 1, "t60": 2, "hit60": 0,
                                   "ex4h": 1, "t4h": 2, "hit4h": 0})
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(f"== top {args.show} by t60 (consistently right over the next hour)")
        print(fmt(q.sort_values("t60", ascending=False).head(args.show)).to_string(index=False))
        print(f"\n== top {args.show} by t4h (next 4 hours)")
        print(fmt(q.sort_values("t4h", ascending=False).head(args.show)).to_string(index=False))
        print(f"\n== bottom 10 by t60 (consistently WRONG - also a signal)")
        print(fmt(q.sort_values("t60").head(10)).to_string(index=False))


if __name__ == "__main__":
    main()