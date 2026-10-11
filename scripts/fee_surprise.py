#!/usr/bin/env python3
"""
fee_surprise.py

Does a FEE SURPRISE predict HYPE's next-day return beyond BTC?

Why: ~99% of Hyperliquid's fees fund the Assistance Fund, which buys HYPE on
the open market every day. Fees are therefore the buyback budget. The market
EXPECTS the buyback (public, continuous), so the variable is not fees but the
surprise: today's fees against their recent average.

PRE-REGISTERED (agreed Oct 11 2026, before any result was seen):
  data      daily UTC, START..END. Cumulative fees (platform_fees, micro-USDC)
            interpolated linearly to each UTC midnight; a midnight whose two
            surrounding samples are more than 48 h apart is unusable, and so
            is every day that touches it.
  surprise  s_d = log( fees_d / mean(fees over the previous 30 days) ),
            needing at least 20 usable days in that window
  target    y_{d+1} = r_HYPE,d+1 - beta * r_BTC,d+1   (daily log returns, bps,
            from 1d candles: close of day vs close of the day before).
            beta is estimated on the FIT half only.
  split     first half by date = fit, second half = held out
  claim     slope of y_{d+1} on s_d is > 0. It SURVIVES only if the held-out
            slope is > 0 with t > 2. Otherwise it is dropped.
  check     one robustness run adds |r_BTC,d| as a control, because fee spikes
            come with volatile days and HYPE may simply rebound after a crash.

Prices come from Hyperliquid's candleSnapshot (two calls, HYPE and BTC), or
from --candles-csv (columns date,coin,close) for offline runs and tests.

Read-only on the database. Run on the box:
    venv/bin/python scripts/fee_surprise.py
"""
import argparse
import json
import sqlite3
import urllib.request

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
API = "https://api.hyperliquid.xyz/info"
START, END = "2025-01-01", "2026-10-10"
MAX_GAP_S = 48 * 3600
WINDOW, MIN_WINDOW = 30, 20
DAY_S = 86_400


def load_fees(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    f = pd.read_sql_query("SELECT time, total_fees FROM platform_fees ORDER BY time", con)
    con.close()
    return f["time"].to_numpy("int64"), f["total_fees"].to_numpy("float64") / 1e6


def cum_at(ts, cum, t):
    """Cumulative fees (USD) at each time t by linear interpolation; NaN where
    t is outside the samples or its two surrounding samples are > MAX_GAP_S apart."""
    t = np.asarray(t, dtype="int64")
    i = np.searchsorted(ts, t, "right")
    lo = np.clip(i - 1, 0, len(ts) - 1)
    hi = np.clip(i, 0, len(ts) - 1)
    gap = ts[hi] - ts[lo]
    exact = (i > 0) & (ts[lo] == t)
    inside = (i > 0) & (i < len(ts)) & (gap <= MAX_GAP_S)
    w = np.where(gap > 0, (t - ts[lo]) / np.where(gap > 0, gap, 1), 0.0)
    val = cum[lo] + w * (cum[hi] - cum[lo])
    return np.where(exact | inside, val, np.nan)


def fetch_closes(coin, start, end):
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000) - 3 * DAY_S * 1000
    t1 = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000) + 3 * DAY_S * 1000
    body = {"type": "candleSnapshot",
            "req": {"coin": coin, "interval": "1d", "startTime": t0, "endTime": t1}}
    r = urllib.request.Request(API, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    rows = json.load(urllib.request.urlopen(r, timeout=30))
    s = pd.Series({pd.Timestamp(int(c["t"]), unit="ms", tz="UTC").normalize(): float(c["c"])
                   for c in rows})
    return s.sort_index()


def closes_from_csv(path, coin):
    c = pd.read_csv(path)
    c = c[c["coin"] == coin]
    return pd.Series(c["close"].to_numpy(float),
                     index=pd.to_datetime(c["date"], utc=True)).sort_index()


def ols(y, X):
    """Slope and t-stat of the FIRST column of X (an intercept is added)."""
    A = np.column_stack([np.ones(len(y)), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    dof = len(y) - A.shape[1]
    s2 = resid @ resid / dof
    se = np.sqrt(s2 * np.linalg.inv(A.T @ A).diagonal())
    return coef[1], coef[1] / se[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--candles-csv", help="offline prices: date,coin,close")
    args = ap.parse_args()

    ts, cum = load_fees(args.db)
    days = pd.date_range(START, END, freq="D", tz="UTC")
    # epoch seconds of each UTC midnight - independent of pandas' datetime
    # resolution (ns in pandas 2, us in pandas 3; .asi8 would differ)
    mid = ((days - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy("int64")
    fees = pd.Series(cum_at(ts, cum, mid + DAY_S) - cum_at(ts, cum, mid), index=days)
    trail = fees.shift(1).rolling(WINDOW, min_periods=MIN_WINDOW).mean()
    surprise = np.log(fees / trail)

    if args.candles_csv:
        h, b = closes_from_csv(args.candles_csv, "HYPE"), closes_from_csv(args.candles_csv, "BTC")
    else:
        h, b = fetch_closes("HYPE", START, END), fetch_closes("BTC", START, END)
    rh = (np.log(h).diff() * 1e4).reindex(days)
    rb = (np.log(b).diff() * 1e4).reindex(days)

    df = pd.DataFrame({"fees": fees, "surprise": surprise, "rh": rh, "rb": rb})
    df["rh_next"], df["rb_next"] = df["rh"].shift(-1), df["rb"].shift(-1)
    df["ctrl"] = df["rb"].abs()
    usable = df.dropna(subset=["surprise", "rh_next", "rb_next", "ctrl"])
    split = usable.index[0] + (usable.index[-1] - usable.index[0]) / 2
    fit_days = df[(df.index < split)].dropna(subset=["rh", "rb"])
    beta, _ = ols(fit_days["rh"].to_numpy(), fit_days[["rb"]].to_numpy())
    usable = usable.assign(y=usable["rh_next"] - beta * usable["rb_next"])

    print(f"fees: {np.isfinite(fees).sum()} of {len(days)} days usable "
          f"({START}..{END}), median ${np.nanmedian(fees) / 1e6:.2f}M/day")
    print(f"usable days with surprise + next-day returns: {len(usable)}; "
          f"split at {split.date()}")
    print(f"beta (HYPE on BTC, fit half): {beta:.2f}\n")

    rows = []
    for name, part in (("fit", usable[usable.index < split]),
                       ("held-out", usable[usable.index >= split])):
        y = part["y"].to_numpy()
        s1, t1 = ols(y, part[["surprise"]].to_numpy())
        s2, t2 = ols(y, part[["surprise", "ctrl"]].to_numpy())
        rows.append({"half": name, "days": len(part),
                     "slope_bps": round(s1, 1), "t": round(t1, 2),
                     "slope_with_ctrl": round(s2, 1), "t_with_ctrl": round(t2, 2)})
    res = pd.DataFrame(rows)
    print("slope = bps of next-day HYPE-beyond-BTC return per unit of log fee surprise")
    print(res.to_string(index=False))

    ho = res.iloc[1]
    ok = ho["slope_bps"] > 0 and ho["t"] > 2
    print(f"\nVERDICT (pre-registered: held-out slope > 0 and t > 2): "
          f"{'SURVIVES' if ok else 'DROPPED'}")


if __name__ == "__main__":
    main()