"""Which way does an operator take, and does it come before price moves?

For one operator and one coin, scans that coin's tape once and builds an hourly
table: last price, market volume, and the operator's taker buys / taker sells
(and maker buys / sells). Then:

  1. daily summary: net taker flow ($M, buys minus sells), net total flow,
     and the coin's return that day
  2. hourly correlations of net taker flow with the same hour's return and
     the next 1 and 4 hours' returns

Discipline: the correlations use only the FIT half (before --split) by default.
The held-out half is opened only with --holdout, once, after the hypothesis
is written down. The daily table always shows the fit half only, too.

Taker side: tape `side` is the taker's side. 'B' = taker bought (the buyer took),
'A' = taker sold (the seller took).

Notes:
  - Same-hour correlation mostly shows the operator's own price impact or
    chasing; only the NEXT-hour columns speak to prediction.
  - Next-4h windows overlap hour to hour, so its t-value is overstated.
  - Scans the coin's whole tape once (a few minutes). The hourly table is saved
    to data/taker_<op>_<coin>.csv; --cached reuses it without scanning.

Usage:
  venv/bin/python scripts/taker_direction.py op_f5d81a
  venv/bin/python scripts/taker_direction.py op_f5d81a --cached --split 09-24
  venv/bin/python scripts/taker_direction.py op_f5d81a --cached --holdout
"""
import argparse
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

DB = "file:data/twap.db?mode=ro"
OPERATORS = "scripts/operators.csv"
YEAR = 2026


def op_ids(con, op):
    ops = pd.read_csv(OPERATORS, header=None, names=["op", "role", "address", "name"])
    addrs = set(ops.loc[ops["op"] == op, "address"].astype(str).str.lower())
    ta = pd.read_sql("SELECT id, lower(address) AS address FROM tape_addresses", con)
    return ta.loc[ta["address"].isin(addrs), "id"].tolist()


def build(con, op, coin):
    ids = op_ids(con, op)
    if not ids:
        sys.exit(f"no tape addresses for {op}")
    idl = ",".join(map(str, ids))
    q = f"""
    SELECT ts/3600000 AS h, px, MAX(tid) AS last_tid, SUM(notional) AS vol,
      SUM(CASE WHEN side='B' AND buyer  IN ({idl}) THEN notional ELSE 0 END) AS tk_buy,
      SUM(CASE WHEN side='A' AND seller IN ({idl}) THEN notional ELSE 0 END) AS tk_sell,
      SUM(CASE WHEN side='A' AND buyer  IN ({idl}) THEN notional ELSE 0 END) AS mk_buy,
      SUM(CASE WHEN side='B' AND seller IN ({idl}) THEN notional ELSE 0 END) AS mk_sell
    FROM tape_prints WHERE coin = ? GROUP BY h
    """
    h = pd.read_sql(q, con, params=(coin,)).drop(columns="last_tid")  # px = last print of the hour
    return h


def corr_line(x, y, label):
    ok = x.notna() & y.notna()
    n = int(ok.sum())
    if n < 10:
        return f"  {label:<36} n={n}  (too few)"
    r = float(np.corrcoef(x[ok], y[ok])[0, 1])
    t = r * math.sqrt(n - 2) / math.sqrt(max(1e-12, 1 - r * r))
    return f"  {label:<36} n={n:<5} r={r:+.3f}  t={t:+.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op")
    ap.add_argument("--coin", default="HYPE")
    ap.add_argument("--split", help="first day of the held-out half, MM-DD (default: midpoint of the data)")
    ap.add_argument("--holdout", action="store_true", help="compute on the held-out half (do this once)")
    ap.add_argument("--cached", action="store_true", help="reuse the saved hourly table instead of scanning")
    a = ap.parse_args()

    path = f"data/taker_{a.op}_{a.coin.replace(':', '_')}.csv"
    if a.cached and os.path.exists(path):
        h = pd.read_csv(path)
    else:
        con = sqlite3.connect(DB, uri=True, timeout=60)
        h = build(con, a.op, a.coin)
        h.to_csv(path, index=False)
        print(f"saved {path}")

    # continuous hourly grid; price carried forward through empty hours
    h = h.set_index("h").reindex(range(int(h["h"].min()), int(h["h"].max()) + 1))
    h["px"] = h["px"].ffill()
    h = h.fillna(0.0)
    h["ret"] = np.log(h["px"]).diff() * 1e4                       # bps
    h["fwd1"] = h["ret"].shift(-1)
    h["fwd4"] = h["ret"].rolling(4).sum().shift(-4)
    h["net_tk"] = h["tk_buy"] - h["tk_sell"]
    h["net_all"] = h["net_tk"] + h["mk_buy"] - h["mk_sell"]
    h["net_tk_frac"] = h["net_tk"] / h["vol"].where(h["vol"] > 0)
    h["day"] = h.index // 24

    days = sorted(h["day"].unique())
    split = (int(pd.Timestamp(f"{YEAR}-{a.split}").value // 86_400_000_000_000)
             if a.split else days[len(days) // 2])
    fit = h[h["day"] < split]
    held = h[h["day"] >= split]
    use = held if a.holdout else fit
    span = lambda df: (f"{pd.to_datetime(df['day'].min() * 86400, unit='s'):%m-%d} to "
                       f"{pd.to_datetime(df['day'].max() * 86400, unit='s'):%m-%d}")
    print(f"{a.op} on {a.coin} | fit half {span(fit)} | held-out half {span(held)}")
    print(f"USING: {'HELD-OUT half' if a.holdout else 'fit half'}\n")

    tk = use["tk_buy"].sum() + use["tk_sell"].sum()
    print(f"taker buys ${use['tk_buy'].sum() / 1e6:,.1f}M | taker sells ${use['tk_sell'].sum() / 1e6:,.1f}M | "
          f"net taker {100 * use['net_tk'].sum() / tk:+.1f}% of its taking | "
          f"net incl. maker ${use['net_all'].sum() / 1e6:+,.1f}M")

    if not a.holdout:
        d = use.groupby("day").agg(net_tk_m=("net_tk", "sum"), net_all_m=("net_all", "sum"),
                                   taking_m=("tk_buy", "sum"), ret_bps=("ret", "sum"))
        d["taking_m"] += use.groupby("day")["tk_sell"].sum()
        d[["net_tk_m", "net_all_m", "taking_m"]] /= 1e6
        d.index = pd.to_datetime(d.index * 86400, unit="s").strftime("%m-%d %a")
        print("\ndaily (fit half): $M, return in bps")
        print(d.round(1).to_string())
        print(f"\nday-level corr, net taker vs same-day return: {d['net_tk_m'].corr(d['ret_bps']):+.2f}")

    print("\nhourly correlations (net taker $ and net taker as share of hour volume):")
    for col in ["net_tk", "net_tk_frac"]:
        for tgt, lab in [("ret", "same hour"), ("fwd1", "next hour"), ("fwd4", "next 4h (overlapping)")]:
            print(corr_line(use[col], use[tgt], f"{col} vs {lab}"))


if __name__ == "__main__":
    main()