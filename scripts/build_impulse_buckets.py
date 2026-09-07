"""
build_impulse_buckets.py

Builds tape_impulse_buckets in twap.db from tape_prints.

  bucket      : 5-minute, per coin
  impulse     : range_bps >= IMPULSE_MULT * trailing median range (prior TRAIL buckets)
  retention   : (p_after - open) / (close - open), measured 30 and 60 min after bucket close
                1.0 = move fully held (permanent), 0.0 = fully reverted (temporary), <0 = overshot back
  class       : continued (ret60 >= 0.5) | partial | reverted (ret60 <= 0)

Run on the box:  python3 build_impulse_buckets.py
Re-runnable; the table is rebuilt from scratch each time.
"""

import sqlite3
import numpy as np
import pandas as pd

DB_PATH      = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"   # adjust
COINS        = ["BTC", "HYPE"]
BUCKET_MS    = 5 * 60 * 1000
TRAIL        = 48          # trailing buckets for the median (48 x 5min = 4h)
IMPULSE_MULT = 3.0
TAKER_BUY    = "B"         # tape_prints.side value meaning the taker bought -- confirm against aggregator
HORIZONS     = {"30": 6, "60": 12}   # buckets after close


def load_buckets(conn, coin):
    q = """
    SELECT ts, px, sz, notional, side
    FROM tape_prints
    WHERE coin = ?
    ORDER BY ts
    """
    df = pd.read_sql_query(q, conn, params=(coin,))
    if df.empty:
        return df
    df["bucket"] = (df["ts"] // BUCKET_MS) * BUCKET_MS
    df["buy_notional"] = np.where(df["side"] == TAKER_BUY, df["notional"], 0.0)

    g = df.groupby("bucket")
    b = pd.DataFrame({
        "open":         g["px"].first(),
        "high":         g["px"].max(),
        "low":          g["px"].min(),
        "close":        g["px"].last(),
        "notional":     g["notional"].sum(),
        "buy_notional": g["buy_notional"].sum(),
        "n_prints":     g["px"].size(),
    })
    # fill empty buckets so the trailing window is time-based, not print-based
    full = pd.RangeIndex(b.index.min(), b.index.max() + BUCKET_MS, BUCKET_MS)
    b = b.reindex(full)
    b["close"] = b["close"].ffill()
    for c in ("open", "high", "low"):
        b[c] = b[c].fillna(b["close"])
    b[["notional", "buy_notional", "n_prints"]] = b[["notional", "buy_notional", "n_prints"]].fillna(0)
    b.index.name = "bucket_ts"
    return b


def classify(b):
    b["range_bps"]    = (b["high"] - b["low"]) / b["close"] * 1e4
    b["taker_buy_pct"] = np.where(b["notional"] > 0, b["buy_notional"] / b["notional"], np.nan)
    # trailing median of the PRIOR TRAIL buckets (shift so the bucket doesn't see itself)
    b["trail_med_bps"] = b["range_bps"].shift(1).rolling(TRAIL, min_periods=TRAIL // 2).median()
    b["range_ratio"]   = b["range_bps"] / b["trail_med_bps"]
    b["is_impulse"]    = (b["range_ratio"] >= IMPULSE_MULT).astype(int)
    move = b["close"] - b["open"]
    b["direction"] = np.sign(move).astype(int)
    b["move_bps"]  = move / b["open"] * 1e4

    for label, k in HORIZONS.items():
        p_after = b["close"].shift(-k)
        b[f"p{label}"]   = p_after
        with np.errstate(divide="ignore", invalid="ignore"):
            b[f"ret{label}"] = np.where(move != 0, (p_after - b["open"]) / move, np.nan)

    def cls(r):
        if r["is_impulse"] != 1 or pd.isna(r["ret60"]):
            return None
        if r["ret60"] >= 0.5:
            return "continued"
        if r["ret60"] <= 0.0:
            return "reverted"
        return "partial"

    b["class"] = b.apply(cls, axis=1)
    return b


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    out = []
    for coin in COINS:
        b = load_buckets(conn, coin)
        if b.empty:
            print(f"{coin}: no prints")
            continue
        b = classify(b)
        b["coin"] = coin
        out.append(b.reset_index())
        imp = b[b["is_impulse"] == 1]
        print(f"{coin}: {len(b)} buckets, {len(imp)} impulses "
              f"({imp['class'].value_counts(dropna=False).to_dict()})")

    if not out:
        return
    res = pd.concat(out, ignore_index=True)
    cols = ["coin", "bucket_ts", "open", "high", "low", "close", "notional", "n_prints",
            "taker_buy_pct", "range_bps", "trail_med_bps", "range_ratio", "is_impulse",
            "direction", "move_bps", "p30", "ret30", "p60", "ret60", "class"]
    res = res[cols]

    conn.execute("DROP TABLE IF EXISTS tape_impulse_buckets")
    res.to_sql("tape_impulse_buckets", conn, index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tib_coin_ts ON tape_impulse_buckets(coin, bucket_ts)")
    conn.commit()
    conn.close()
    print(f"wrote {len(res)} rows to tape_impulse_buckets")


if __name__ == "__main__":
    main()