#!/usr/bin/env python3
"""
operator_presence.py

Do the big operators WITHDRAW before volatility?

Every tape print names its maker. scripts/operators.csv maps 853 addresses to
37 operators. Per 5-min bucket we measure the operators' share of passive
(maker) notional. If they thin out their quotes before a move, their share
drops before the range expands.

PRE-REGISTERED (agreed Oct 11 2026, before any result was seen):
  coins     BTC, HYPE (longest tape)
  share_t   maker notional from operators.csv addresses / all maker notional
            in bucket t (a share, so it adjusts for how busy the market is)
  target    log(1 + range_bps) of bucket t+1
  model     target ~ log(1 + range_t) + log(1 + rv_t) + log(1 + rv_hour)
            + log(1 + rv_day) + share_t
            rv = realized vol (sqrt of summed squared print-to-print log
            returns, bps); rv_hour / rv_day average it over the last 12 / 288
            buckets incl. t (HAR-style). Share must add information BEYOND this
            volatility state.
            DESIGN NOTE (changed after the synthetic test, before any real data
            was run): with only range or one-bucket rv as controls, operators who
            merely REACT to current volatility looked predictive - share was a
            better thermometer of current vol than one noisy bucket. The
            hour/day averages remove most of that. Residual caveat: a pass shows
            share improves the volatility forecast; on its own it cannot prove
            operators withdraw in ANTICIPATION rather than reaction.
            The trap vanished at 400 prints/bucket (t -0.7 / +1.9) but not at
            40 (t -6 to -7): the rv control is only sharp when a bucket holds
            many prints. Hence MIN_PRINTS = 200 for both bucket t and t+1.
  SE        clustered by UTC day (neighbouring buckets are not independent)
  split     per coin: first half by date = fit, second half = held out
  claim     coefficient on share_t < 0 (lower presence -> wider next bucket).
            SURVIVES on a coin only if held-out coef < 0 with t < -2.
  scope     predicts HOW MUCH price moves, not which way.

Buckets need >= MIN_PRINTS (200) prints and a directly following bucket with
as many; gaps in the tape therefore drop out on their own.

Output: data/operator_presence_buckets.csv (one row per coin/bucket) + summary.
Read-only (mode=ro). Run on the box:
    venv/bin/python scripts/operator_presence.py
"""
import argparse
import os
import sqlite3
import time

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
OPERATORS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "operators.csv")
OUT_CSV = "data/operator_presence_buckets.csv"
BUCKET_MS = 5 * 60_000
HOUR, DAY = 12, 288          # buckets
MIN_PRINTS = 200             # calibrated on the synthetic trap, see DESIGN NOTE
CHUNK = 2_000_000


def operator_ids(conn, path):
    ops = pd.read_csv(path, dtype=str)
    addrs = sorted(set(ops["address"].str.strip().str.lower()))
    ids = set()
    for i in range(0, len(addrs), 500):
        chunk = addrs[i:i + 500]
        q = ",".join("?" * len(chunk))
        ids.update(r[0] for r in conn.execute(
            f"SELECT id FROM tape_addresses WHERE address IN ({q})", chunk))
    return np.array(sorted(ids), dtype="int64"), len(addrs)


def buckets(conn, coin, op_ids):
    """Per 5-min bucket: open/high/low/close, maker notional total and from operators."""
    parts, last_px = [], None
    for c in pd.read_sql_query(
            "SELECT ts, px, notional, side, buyer, seller FROM tape_prints "
            "WHERE coin = ? ORDER BY ts", conn, params=(coin,), chunksize=CHUNK):
        # realized variance: squared log return from each print to the next,
        # credited to the bucket of the later print (carried across chunks)
        lp = np.log(c["px"].to_numpy("float64"))
        prev = np.concatenate([[np.log(last_px) if last_px else lp[0]], lp[:-1]])
        last_px = float(c["px"].iloc[-1])
        c = c.assign(rv=(lp - prev) ** 2)
        c = c.dropna(subset=["buyer", "seller"])
        maker = np.where(c["side"].to_numpy() == "B", c["seller"].to_numpy("int64"),
                         c["buyer"].to_numpy("int64"))
        c = c.assign(b=c["ts"] // BUCKET_MS,
                     op_ntl=np.where(np.isin(maker, op_ids), c["notional"], 0.0))
        parts.append(c.groupby("b").agg(open=("px", "first"), close=("px", "last"),
                                        high=("px", "max"), low=("px", "min"),
                                        ntl=("notional", "sum"), op_ntl=("op_ntl", "sum"),
                                        rv=("rv", "sum"), prints=("px", "size")))
    p = pd.concat(parts)
    # a bucket split across two chunks appears twice: merge in time order
    return p.groupby(level=0).agg(open=("open", "first"), close=("close", "last"),
                                  high=("high", "max"), low=("low", "min"),
                                  ntl=("ntl", "sum"), op_ntl=("op_ntl", "sum"),
                                  rv=("rv", "sum"), prints=("prints", "sum"))


def ols_cluster(y, X, groups):
    """OLS with an intercept; standard errors clustered by group."""
    A = np.column_stack([np.ones(len(y)), X])
    XtX_inv = np.linalg.inv(A.T @ A)
    coef = XtX_inv @ A.T @ y
    u = y - A @ coef
    meat = np.zeros((A.shape[1], A.shape[1]))
    for g in np.unique(groups):
        s = A[groups == g].T @ u[groups == g]
        meat += np.outer(s, s)
    n, k, G = len(y), A.shape[1], len(np.unique(groups))
    V = XtX_inv @ meat @ XtX_inv * (G / (G - 1)) * ((n - 1) / (n - k))
    se = np.sqrt(np.diag(V))
    return coef, se


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--coins", default="BTC,HYPE")
    ap.add_argument("--operators", default=OPERATORS_CSV)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
    op_ids, n_addr = operator_ids(conn, args.operators)
    print(f"{len(op_ids)} of {n_addr} operator addresses have ever traded on the tape")

    frames, rows = [], []
    for coin in args.coins.split(","):
        b = buckets(conn, coin, op_ids)
        b["range"] = (b["high"] - b["low"]) / b["open"] * 1e4
        b["share"] = b["op_ntl"] / b["ntl"]
        b["lr"] = np.log1p(b["range"])
        b["lrv"] = np.log1p(np.sqrt(b["rv"]) * 1e4)
        # HAR-style volatility state: realized vol averaged over the last hour
        # and the last day (incl. bucket t), on the full time grid so gaps count
        rv_full = b["rv"].reindex(np.arange(b.index.min(), b.index.max() + 1))
        rv_h = rv_full.rolling(HOUR, min_periods=HOUR // 2).mean()
        rv_d = rv_full.rolling(DAY, min_periods=DAY // 2).mean()
        b["lrv_h"] = np.log1p(np.sqrt(rv_h.reindex(b.index)) * 1e4)
        b["lrv_d"] = np.log1p(np.sqrt(rv_d.reindex(b.index)) * 1e4)
        nxt = b["lr"].reindex(b.index + 1)
        b["lr_next"] = nxt.to_numpy()
        nxt_prints = b["prints"].reindex(b.index + 1).to_numpy()
        ok = ((b["prints"] >= MIN_PRINTS) & (nxt_prints >= MIN_PRINTS)
              & b[["lr", "lrv", "lrv_h", "lrv_d", "lr_next", "share"]].notna().all(axis=1))
        d = b[ok].copy()
        d["coin"] = coin
        d["day"] = (d.index * BUCKET_MS) // 86_400_000
        frames.append(d)
        split = d.index.min() + (d.index.max() - d.index.min()) / 2
        print(f"{coin}: median {int(b['prints'].median())} prints/bucket")
        print(f"{coin}: {len(b):,} buckets, {len(d):,} usable, mean operator share "
              f"{100 * d['share'].mean():.1f}% (sd {100 * d['share'].std():.1f}), "
              f"split at {pd.Timestamp(int(split * BUCKET_MS), unit='ms').date()} "
              f"({time.time() - t0:.0f}s)")
        for name, part in (("fit", d[d.index < split]), ("held-out", d[d.index >= split])):
            coef, se = ols_cluster(part["lr_next"].to_numpy(),
                                   part[["lr", "lrv", "lrv_h", "lrv_d", "share"]].to_numpy(),
                                   part["day"].to_numpy())
            rows.append({"coin": coin, "half": name, "buckets": len(part),
                         "days": part["day"].nunique(),
                         "b_range": round(coef[1], 3), "b_rv": round(coef[2], 3),
                         "b_rv_h": round(coef[3], 3), "b_rv_d": round(coef[4], 3),
                         "b_share": round(coef[5], 3), "t_share": round(coef[5] / se[5], 2),
                         "pct_per_10pp": round(100 * (np.exp(coef[5] * 0.1) - 1), 1)})
    conn.close()

    pd.concat(frames).round(5).to_csv(OUT_CSV, index_label="bucket")
    res = pd.DataFrame(rows)
    print(f"\nCSV: {OUT_CSV}   runtime {time.time() - t0:.0f}s\n")
    print("b_share: effect of operator share on log next-bucket range;")
    print("pct_per_10pp: % change in next range per +10 points of operator share")
    print(res.to_string(index=False))
    print()
    for coin, r in res[res["half"] == "held-out"].set_index("coin").iterrows():
        ok = r["b_share"] < 0 and r["t_share"] < -2
        print(f"VERDICT {coin} (held-out coef < 0 and t < -2): "
              f"{'SURVIVES' if ok else 'DROPPED'}")


if __name__ == "__main__":
    main()