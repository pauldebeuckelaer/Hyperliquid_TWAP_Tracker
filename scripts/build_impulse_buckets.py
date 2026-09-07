"""
build_impulse_buckets.py  (V2)

Builds tape_impulse_buckets and tape_impulse_events in twap.db from tape_prints.

Bucket (5 min, per coin)
  impulse   : range_bps >= IMPULSE_MULT * trailing median range (prior TRAIL buckets)
  wick      : body_ratio = |close-open| / (high-low) < BODY_MIN  -> no direction, no retention
  retention : (p_after - open) / (close - open) at 30 / 60 min after bucket close
              1.0 = move held (permanent), 0.0 = fully reverted (temporary), <0 = overshot back
  class     : wick | continued (ret60 >= 0.5) | partial | reverted (ret60 <= 0)

Trend (rolling TREND_WIN buckets = 1h)
  drift_bps : close[last] - open[first], in bps of open[first]
  eff       : |drift| / sum(|close-to-close moves|)   (Kaufman efficiency: 1 = straight line, ~0 = chop)
  window is a trend if |drift_bps| >= TREND_MULT * trailing median range AND eff >= EFF_MIN
  is_trend  : bucket lies inside any qualifying window; trend_dir = sign of that window's drift

Event
  adjacent impulse buckets (same coin) share an event_id; tape_impulse_events summarises each
  as one move: first open -> last close, retention from the last bucket's close.

Run on the box:  venv/bin/python scripts/build_impulse_buckets.py
Re-runnable; both tables are rebuilt from scratch.
"""

import sqlite3
import numpy as np
import pandas as pd

DB_PATH      = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
COINS        = ["BTC", "HYPE"]
BUCKET_MS    = 5 * 60 * 1000
TRAIL        = 48          # trailing buckets for the median range (4h)
IMPULSE_MULT = 3.0
BODY_MIN     = 0.4         # |close-open| / (high-low) below this -> wick
TAKER_BUY    = "B"         # tape_prints.side value meaning the taker bought
HORIZONS     = {"30": 6, "60": 12}   # buckets after close
TREND_WIN    = 12          # buckets per trend window (1h)
TREND_MULT   = 6.0         # |drift| must be >= this x trailing median 5-min range
EFF_MIN      = 0.5


def load_buckets(conn, coin):
    q = "SELECT ts, px, sz, notional, side FROM tape_prints WHERE coin = ? ORDER BY ts"
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
    full = pd.RangeIndex(b.index.min(), b.index.max() + BUCKET_MS, BUCKET_MS)
    b = b.reindex(full)
    b["close"] = b["close"].ffill()
    for c in ("open", "high", "low"):
        b[c] = b[c].fillna(b["close"])
    b[["notional", "buy_notional", "n_prints"]] = b[["notional", "buy_notional", "n_prints"]].fillna(0)
    b.index.name = "bucket_ts"
    return b


def classify_impulses(b):
    rng = b["high"] - b["low"]
    move = b["close"] - b["open"]

    b["range_bps"]     = rng / b["close"] * 1e4
    b["move_bps"]      = move / b["open"] * 1e4
    b["body_ratio"]    = np.where(rng > 0, move.abs() / rng, 0.0)
    b["taker_buy_pct"] = np.where(b["notional"] > 0, b["buy_notional"] / b["notional"], np.nan)

    b["trail_med_bps"] = b["range_bps"].shift(1).rolling(TRAIL, min_periods=TRAIL // 2).median()
    b["range_ratio"]   = b["range_bps"] / b["trail_med_bps"]
    b["is_impulse"]    = (b["range_ratio"] >= IMPULSE_MULT).astype(int)
    b["is_wick"]       = (b["body_ratio"] < BODY_MIN).astype(int)
    b["direction"]     = np.where(b["is_wick"] == 1, 0, np.sign(move)).astype(int)

    for label, k in HORIZONS.items():
        p_after = b["close"].shift(-k)
        b[f"p{label}"] = p_after
        ret = np.where(move != 0, (p_after - b["open"]) / move, np.nan)
        b[f"ret{label}"] = np.where(b["is_wick"] == 1, np.nan, ret)

    def cls(r):
        if r["is_impulse"] != 1:
            return None
        if r["is_wick"] == 1:
            return "wick"
        if pd.isna(r["ret60"]):
            return None
        if r["ret60"] >= 0.5:
            return "continued"
        if r["ret60"] <= 0.0:
            return "reverted"
        return "partial"

    b["class"] = b.apply(cls, axis=1)

    # adjacent impulse buckets -> one event
    imp = b["is_impulse"] == 1
    new_event = imp & ~imp.shift(1, fill_value=False)
    b["event_id"] = np.where(imp, new_event.cumsum(), 0).astype(int)
    return b


def flag_trends(b):
    w = TREND_WIN
    first_open = b["open"].shift(w - 1)
    drift = b["close"] - first_open
    b["trend_drift_bps"] = drift / first_open * 1e4
    step = b["close"].diff().abs()
    path = step.rolling(w, min_periods=w).sum()
    b["trend_eff"] = np.where(path > 0, drift.abs() / path, 0.0)

    qualifies = (b["trend_drift_bps"].abs() >= TREND_MULT * b["trail_med_bps"]) & (b["trend_eff"] >= EFF_MIN)
    end_dir = np.where(qualifies, np.sign(drift), 0).astype(int)

    # a bucket is in a trend if any window ending in the next w-1 buckets (including itself) qualifies
    end_series = pd.Series(end_dir, index=b.index)
    fwd_max = end_series.iloc[::-1].rolling(w, min_periods=1).max().iloc[::-1]
    fwd_min = end_series.iloc[::-1].rolling(w, min_periods=1).min().iloc[::-1]
    trend_dir = np.where(fwd_max > 0, 1, np.where(fwd_min < 0, -1, 0)).astype(int)
    b["trend_dir"] = trend_dir
    b["is_trend"]  = (trend_dir != 0).astype(int)
    return b


def build_events(b, coin):
    imp = b[b["event_id"] > 0]
    if imp.empty:
        return pd.DataFrame()
    rows = []
    for eid, g in imp.groupby("event_id"):
        first, last = g.iloc[0], g.iloc[-1]
        o, c = first["open"], last["close"]
        move = c - o
        hi, lo = g["high"].max(), g["low"].min()
        body = abs(move) / (hi - lo) if hi > lo else 0.0
        tot = g["notional"].sum()
        row = {
            "coin": coin, "event_id": int(eid),
            "start_ts": int(g.index[0]), "end_ts": int(g.index[-1]) + BUCKET_MS,
            "n_buckets": len(g), "open": o, "close": c, "high": hi, "low": lo,
            "move_bps": move / o * 1e4, "range_bps": (hi - lo) / c * 1e4,
            "body_ratio": body, "notional": tot,
            "taker_buy_pct": g["buy_notional"].sum() / tot if tot > 0 else np.nan,
            "in_trend": int(g["is_trend"].max()),
        }
        for label, k in HORIZONS.items():
            p_after = b["close"].shift(-k).get(g.index[-1], np.nan)
            ok = move != 0 and body >= BODY_MIN and not pd.isna(p_after)
            row[f"ret{label}"] = (p_after - o) / move if ok else np.nan
        r60 = row["ret60"]
        row["class"] = ("wick" if body < BODY_MIN else None if pd.isna(r60)
                        else "continued" if r60 >= 0.5 else "reverted" if r60 <= 0 else "partial")
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    buckets, events = [], []
    for coin in COINS:
        b = load_buckets(conn, coin)
        if b.empty:
            print(f"{coin}: no prints")
            continue
        b = classify_impulses(b)
        b = flag_trends(b)
        b["coin"] = coin
        buckets.append(b.reset_index())
        ev = build_events(b, coin)
        events.append(ev)
        evc = ev["class"].value_counts(dropna=False).to_dict() if not ev.empty else {}
        print(f"{coin}: {len(b)} buckets | {int(b['is_impulse'].sum())} impulse buckets in {len(ev)} events "
              f"{evc} | {int(b['is_trend'].sum())} trend buckets")

    if not buckets:
        return
    bcols = ["coin", "bucket_ts", "open", "high", "low", "close", "notional", "n_prints", "taker_buy_pct",
             "range_bps", "move_bps", "body_ratio", "trail_med_bps", "range_ratio",
             "is_impulse", "is_wick", "direction", "event_id", "p30", "ret30", "p60", "ret60", "class",
             "trend_drift_bps", "trend_eff", "is_trend", "trend_dir"]
    res = pd.concat(buckets, ignore_index=True)[bcols]
    conn.execute("DROP TABLE IF EXISTS tape_impulse_buckets")
    res.to_sql("tape_impulse_buckets", conn, index=False)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tib_coin_ts ON tape_impulse_buckets(coin, bucket_ts)")

    nonempty = [e for e in events if not e.empty]
    ev = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()
    conn.execute("DROP TABLE IF EXISTS tape_impulse_events")
    if not ev.empty:
        ev.to_sql("tape_impulse_events", conn, index=False)
    conn.commit()
    conn.close()
    print(f"wrote {len(res)} bucket rows, {len(ev)} event rows")


if __name__ == "__main__":
    main()