#!/usr/bin/env python3
"""Replay the Signal Tracker HYPE strategy ("full") over history, candle by candle.

Rebuilds what the live bot would have seen at every 30-minute candle close from
twap.db, with the bot's own rules and parameters (signal_engine.py and
config.py as they ran from Apr 1 to Sep 13 2026), then simulates its exits.

Reproduced on purpose, quirks included:
  - candles from `snapshots` (30 min, >= 10 ticks), VI, and the bot's
    simple-mean RSI(14); values rounded the way the bot rounded them before
    comparing (VI to 3 decimals, RSI to 1, BTC change to 2)
  - capped whale flow from `orders`: placed TWAP size in HYPE, summed per
    address per 30-min bin, capped at +-5,000 HYPE; if the candle's bin has no
    orders, the bot used the LAST bin that had any, and so does this
  - order-book filter from `orderbook_snapshots`: block if net aggressor
    flow < 0, skipped when the latest snapshot is more than 5 min old
  - BTC 3h gate at -2.0 % (BASELINE only)
  - the SQLite 'T' quirk: the bot compared ISO timestamps written with a 'T'
    against datetime('now', ...), which uses a space. 'T' sorts after ' ', so
    every lookback window really started at 00:00 UTC of the cutoff's day:
    the "last 30 minutes" of order-book flow was everything since midnight.
    On by default (faithful). --no-quirk uses the windows as written.
  - exits: TP +1.5 % / SL -2.0 % / 360 min, checked on every HYPE price tick
    in `snapshots`, max/min tracked like the bot; 30-min cooldown after a TIME
    exit; one HYPE position at a time

Data sources (fixed Oct 15 2026):
  - HYPE price for exits: ALWAYS `snapshots`, the same series the candles and
    the entry price come from, over the whole period. (Before: market_snapshots
    if it had any rows in the range, else snapshots. market_snapshots only keeps
    ~7 days, so a run straddling that edge lost every path before it.)
  - BTC for the 3h gate: `market_candles` 10-min closes, each stamped at its
    candle's CLOSE (candle_time + 10 min) so no candle is seen before it is
    complete, then raw `market_snapshots` from where those begin (last ~7 days).
  - Missing BTC is NaN, never 0. A BASELINE entry with no BTC value is blocked
    with reason "BTC missing" and shows in the funnel. (Before: no BTC rows
    returned 0.0, so the gate silently always passed.) CONTRA has no BTC gate,
    as in the bot.

Flow cap as intended (--flow-cap-usd 5000, added Oct 15 2026):
  The bot's flow_cap=5000 was meant as USD but applied to HYPE size. With this
  flag each address's signed size per bin is converted to USD at the HYPE price
  at evaluation time (the candle close, no look-ahead), capped at +-cap_usd, and
  summed: `flow` is then in USD and drives CONTRA/BASELINE. The as-run flow is
  always kept in `flow_coin`, and the funnel reports how often the sign differs.
  In this mode `flow` no longer matches the real trades' entry_z: expected.

Not reproduced: the 4-position limit shared with other coins, bot downtime,
real fills (entry = candle close, which is what the bot recorded), fees.

No look-ahead in the signal: each candle close only sees rows timestamped at
or before that moment. The exit simulation looks forward, by design.

Outputs (prefix --out, default data/replay_hype):
  _signals.csv     every candle close: inputs, decision, reason, action
  _trades.csv      simulated trades: exit, best/worst point, time to peak
  _paths.csv       tick return path for 420 min after EVERY entry signal,
                   also the ones skipped while in a position (360-min hold
                   plus the tick that triggers the time exit). Read by
                   replay_exits.py; explore on the fit half only.
  _validation.csv  every real HYPE trade next to what the replay saw

Prints: data coverage, validation against the real trades in scanner.db,
the funnel of the signal, the replay's results per month, and how much the
'T' quirk changed decisions.

Run from ~/bots/Hyperliquid_TWAP_Analyzer:
    python3 scripts/replay_hype.py
    python3 scripts/replay_hype.py --start 2026-04-01 --end 2026-09-14
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# config.py, HYPE, as it ran Apr 1 - Sep 13 2026
P = {
    "rsi_period": 14,
    "rsi_threshold": 40,
    "vi_threshold": 0.3,
    "tp_pct": 1.5,
    "sl_pct": 2.0,
    "max_hold_minutes": 12 * 30,
    "flow_cap": 5000,          # as it ran: applied in HYPE units (~$250k+), rarely binds
    "flow_cap_usd": None,      # --flow-cap-usd: the cap as intended, flow in USD
    "flow_lookback_hours": 6,
    "ob_lookback_minutes": 30,
    "ob_stale_minutes": 5,
    "cooldown_minutes": 30,
    "btc_3h_min": -2.0,
    "snapshot_min_ticks": 10,
    "candle_lookback_hours": 30,
}

BTC_CANDLE_MIN = 10   # market_candles interval

EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
BOX_CUTOVER = pd.Timestamp("2026-06-06 14:28", tz="UTC")   # GCP -> box (config.py DB_PATH edit)


def to_utc(s):
    try:
        return pd.to_datetime(s, utc=True, format="ISO8601")
    except (TypeError, ValueError):
        return pd.to_datetime(s, utc=True)


def period_of(ts):
    return "GCP" if ts < BOX_CUTOVER else "box"


class Table:
    """One table's rows, sorted by time, sliced 'as the bot saw them'."""

    def __init__(self, df, tcol):
        df = df.copy()
        df["ts_str"] = df[tcol].astype(str)
        df["dt"] = to_utc(df[tcol])
        df = df.sort_values("dt", kind="stable").reset_index(drop=True)
        self.df = df
        self.sec = ((df["dt"] - EPOCH) / pd.Timedelta(seconds=1)).to_numpy()
        self.ts_str = df["ts_str"].to_numpy(dtype=object)

    def __len__(self):
        return len(self.df)

    def window(self, as_of, lookback, quirk):
        """Rows with ts > as_of - lookback and ts <= as_of.

        quirk=True compares the raw timestamp text with SQLite's
        'YYYY-MM-DD HH:MM:SS' cutoff, exactly as the bot's SQL did."""
        cutoff = as_of - lookback
        i0 = np.searchsorted(self.sec, cutoff.timestamp() - 86400 - 60, "left")
        i1 = np.searchsorted(self.sec, as_of.timestamp(), "right")
        if quirk:
            cut = cutoff.strftime("%Y-%m-%d %H:%M:%S")
            m = np.asarray(self.ts_str[i0:i1] > cut, dtype=bool)
        else:
            m = self.sec[i0:i1] > cutoff.timestamp()
        return self.df.iloc[i0:i1][m]


# ─────────────────────────────────────────────
# Mirrors of signal_engine.py
# ─────────────────────────────────────────────

def build_candles(w):
    if w.empty:
        return pd.DataFrame()
    d = w.set_index("dt")
    c = d.resample("30min").agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        buy_volume=("buy_volume", "sum"),
        sell_volume=("sell_volume", "sum"),
        avg_unique_addr=("unique_addresses", "mean"),
        tick_count=("price", "count"),
    ).dropna(subset=["close"])
    c = c[c["tick_count"] >= P["snapshot_min_ticks"]]
    c = c.reset_index()
    return c.rename(columns={c.columns[0]: "timestamp"})


def add_indicators(candles, rsi_period):
    d = candles.copy()
    if len(d) < rsi_period + 5:
        return d
    d["vol_imbalance"] = (d["buy_volume"] - d["sell_volume"]) / (d["buy_volume"] + d["sell_volume"] + 1)
    delta = d["close"].diff()
    gain = delta.clip(lower=0).rolling(rsi_period).mean()
    loss = (-delta.clip(upper=0)).rolling(rsi_period).mean()
    rs = gain / loss
    d["rsi"] = 100 - (100 / (1 + rs))
    return d


def build_capped_flow(w, cap_usd=None, price=None):
    """cap_usd=None: as the bot ran (size in HYPE, capped at flow_cap HYPE).
    cap_usd set: size * price in USD, capped at +-cap_usd per address per bin."""
    if w.empty:
        return pd.DataFrame(columns=["timestamp", "capped_flow", "unique_whales"])
    df = w.copy()
    df["bin"] = df["dt"].dt.floor("30min")
    df["signed_size"] = df["size"] * df["side"].map({"BUY": 1, "SELL": -1})
    if cap_usd is not None:
        df["signed_size"] = df["signed_size"] * price
        cap = cap_usd
    else:
        cap = P["flow_cap"]
    ab = df.groupby(["bin", "address"])["signed_size"].sum().reset_index()
    ab["capped"] = ab["signed_size"].clip(-cap, cap)
    flow = ab.groupby("bin").agg(
        capped_flow=("capped", "sum"),
        unique_whales=("address", "nunique"),
    ).reset_index()
    flow.columns = ["timestamp", "capped_flow", "unique_whales"]
    return flow


def flow_at(t, as_of, candle_ts, quirk, cap_usd=None, price=None):
    w = t["orders"].window(as_of, pd.Timedelta(hours=P["flow_lookback_hours"]), quirk)
    f = build_capped_flow(w, cap_usd, price)
    if f.empty:
        return 0.0, 0, None
    match = f[f["timestamp"] == candle_ts]
    row = f.iloc[-1] if match.empty else match.iloc[0]
    return float(row["capped_flow"]), int(row["unique_whales"]), row["timestamp"]


def ob_flow(t, as_of, quirk):
    w = t["ob"].window(as_of, pd.Timedelta(minutes=P["ob_lookback_minutes"]), quirk)
    if w.empty:
        return {"flow": 0.0, "count": 0, "stale": True}
    latest = w["dt"].max()
    stale = (as_of - latest).total_seconds() / 60 > P["ob_stale_minutes"]
    return {"flow": float(w["net_aggressor_flow"].sum()), "count": len(w), "stale": bool(stale)}


def btc_change(t, as_of, quirk):
    """BTC % change over ~3h, or NaN when there is not enough BTC data.
    NaN, not 0: a missing value must never pass the gate silently."""
    w = t["btc"].window(as_of, pd.Timedelta(hours=4), quirk)
    if len(w) < 2:
        return np.nan
    cur = w["mark_px"].iloc[-1]
    target = w["dt"].iloc[-1] - pd.Timedelta(hours=3)
    past = w[w["dt"] <= target]
    pp = w["mark_px"].iloc[0] if past.empty else past["mark_px"].iloc[-1]
    if not pp > 0:
        return np.nan
    return (cur - pp) / pp * 100


def evaluate(t, as_of, quirk):
    """What get_signal_for_coin(HYPE) would have returned at `as_of`, plus
    the inputs it never looked at (flow and OB when VI/RSI already failed)."""
    out = {"as_of": as_of, "candle": None, "vi": np.nan, "rsi": np.nan, "price": np.nan,
           "btc_3h": np.nan, "flow": np.nan, "flow_coin": np.nan, "whales": np.nan, "flow_bin": None,
           "ob_flow": np.nan, "ob_count": 0, "ob_stale": None,
           "ob_flow_30m": np.nan, "ob_stale_30m": None,
           "signal_type": "NONE", "entry": False, "reason": ""}

    w = t["snap"].window(as_of, pd.Timedelta(hours=P["candle_lookback_hours"]), quirk)
    candles = build_candles(w)
    if candles.empty or len(candles) < P["rsi_period"] + 5:
        out["reason"] = "Not enough candle data"
        return out
    candles = add_indicators(candles, P["rsi_period"])
    latest = candles.iloc[-1]
    vi, rsi, price = latest.get("vol_imbalance", 0), latest.get("rsi", 50), latest.get("close", 0)
    out["candle"] = latest["timestamp"]
    if pd.isna(rsi) or price <= 0:
        out["reason"] = "Indicators not ready (NaN)"
        return out

    vi_r = round(float(vi), 3) if pd.notna(vi) else 0
    rsi_r = round(float(rsi), 1)
    btc_r = round(float(btc_change(t, as_of, quirk)), 2)   # NaN stays NaN
    out.update(vi=vi_r, rsi=rsi_r, btc_3h=btc_r, price=round(float(price), 4))

    flow_coin, whales, fbin = flow_at(t, as_of, latest["timestamp"], quirk)
    if P["flow_cap_usd"] is not None:
        flow, _, _ = flow_at(t, as_of, latest["timestamp"], quirk, P["flow_cap_usd"], float(price))
    else:
        flow = flow_coin
    ob = ob_flow(t, as_of, quirk)
    ob30 = ob_flow(t, as_of, False)
    out.update(flow=round(flow, 0), flow_coin=round(flow_coin, 0), whales=whales, flow_bin=fbin,
               ob_flow=round(ob["flow"], 0), ob_count=ob["count"], ob_stale=ob["stale"],
               ob_flow_30m=round(ob30["flow"], 0), ob_stale_30m=ob30["stale"])

    if vi_r <= P["vi_threshold"]:
        out["reason"] = "VI too low"
    elif rsi_r >= P["rsi_threshold"]:
        out["reason"] = "RSI too high"
    elif not ob["stale"] and ob["flow"] < 0:
        out["reason"] = "OB flow negative"
    elif flow < 0:
        out.update(signal_type="CONTRA", entry=True, reason="CONTRA")
    elif pd.isna(btc_r):
        out["reason"] = "BTC missing"
    elif btc_r > P["btc_3h_min"]:
        out.update(signal_type="BASELINE", entry=True, reason="BASELINE")
    else:
        out["reason"] = "BTC risk-off"
    return out


# ─────────────────────────────────────────────
# Mirror of check_exit_conditions (main_signaltracker.py)
# ─────────────────────────────────────────────

def price_path(t, as_of, minutes):
    px = t["px"]
    a = as_of.timestamp()
    i0 = np.searchsorted(px.sec, a, "right")
    i1 = np.searchsorted(px.sec, a + minutes * 60, "right")
    return (px.sec[i0:i1] - a) / 60, px.df["mark_px"].to_numpy()[i0:i1]


def simulate(t, as_of, entry_price):
    hold, prices = price_path(t, as_of, P["max_hold_minutes"] + 60)
    if len(prices) == 0:
        return None
    pnl = (prices - entry_price) / entry_price * 100
    tp, sl, th = pnl >= P["tp_pct"], pnl <= -P["sl_pct"], hold >= P["max_hold_minutes"]
    hit = tp | sl | th
    if hit.any():
        k = int(np.argmax(hit))
        kind = "TP" if tp[k] else ("SL" if sl[k] else "TIME")
    else:
        k, kind = len(prices) - 1, "NO_DATA"
    seg = prices[: k + 1]
    mx, mn = max(entry_price, seg.max()), min(entry_price, seg.min())
    gaps = np.diff(np.concatenate([[0.0], hold[: k + 1]]))
    return {
        "exit_time": (as_of + pd.Timedelta(minutes=float(hold[k]))).round("s"),
        "exit_price": float(prices[k]),
        "exit_kind": kind,
        "hold_min": round(float(hold[k]), 1),
        "pnl_pct": round(float(pnl[k]), 3),
        "best_pct": round((mx / entry_price - 1) * 100, 3),
        "worst_pct": round((mn / entry_price - 1) * 100, 3),
        "min_to_peak": round(float(hold[int(np.argmax(seg))]), 1) if seg.max() > entry_price else 0.0,
        "max_gap_min": round(float(gaps.max()), 1),
    }


# ─────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────

def load_tables(db, lo, hi):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    q = pd.read_sql_query
    t0 = time.time()
    print("Loading twap.db tables (market_snapshots can take a few minutes)...", flush=True)
    snap = q("SELECT timestamp, price, buy_volume, sell_volume, unique_addresses FROM snapshots "
             "WHERE symbol='HYPE' AND timestamp >= ? AND timestamp < ?", conn, params=(lo, hi))
    orders = q("SELECT first_seen_at, address, side, size FROM orders "
               "WHERE symbol='HYPE' AND first_seen_at >= ? AND first_seen_at < ?", conn, params=(lo, hi))
    ob = q("SELECT snapshot_time, net_aggressor_flow, num_trades FROM orderbook_snapshots "
           "WHERE coin='HYPE' AND snapshot_time >= ? AND snapshot_time < ?", conn, params=(lo, hi))
    ms = q("SELECT snapshot_time, mark_px FROM market_snapshots "
           "WHERE coin='BTC' AND snapshot_time >= ? AND snapshot_time < ?", conn, params=(lo, hi))
    mc = q("SELECT candle_time, close FROM market_candles "
           "WHERE coin='BTC' AND candle_time >= ? AND candle_time < ?", conn, params=(lo, hi))
    conn.close()
    print(f"  loaded in {time.time() - t0:.0f}s", flush=True)

    # BTC: 10-min candles, stamped at their close so a candle is only visible once
    # complete; then raw market_snapshots from where those start (last ~7 days).
    mc = mc.dropna(subset=["close"])
    mc_close = to_utc(mc["candle_time"]) + pd.Timedelta(minutes=BTC_CANDLE_MIN)
    mc = pd.DataFrame({"snapshot_time": mc_close.dt.strftime("%Y-%m-%dT%H:%M:%S").to_numpy(),
                       "mark_px": mc["close"].to_numpy()})
    ms = ms.dropna(subset=["mark_px"])
    if len(ms) and len(mc):
        first_ms = to_utc(ms["snapshot_time"]).min()
        mc = mc[(to_utc(mc["snapshot_time"]) < first_ms).to_numpy()]
    btc = pd.concat([mc, ms], ignore_index=True)
    src = {"px": "snapshots",
           "btc": f"market_candles {len(mc)} + market_snapshots {len(ms)}"}

    # HYPE price for exits: always snapshots (same series as candles and entry price)
    hype_px = snap[["timestamp", "price"]].rename(columns={"timestamp": "snapshot_time", "price": "mark_px"})
    t = {
        "snap": Table(snap, "timestamp"),
        "orders": Table(orders, "first_seen_at"),
        "ob": Table(ob, "snapshot_time"),
        "btc": Table(btc, "snapshot_time"),
        "px": Table(hype_px, "snapshot_time"),
    }
    return t, src


def load_real(scanner_db, start, end):
    conn = sqlite3.connect(f"file:{scanner_db}?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query("SELECT id, entry_time, entry_price, entry_z, entry_addrs, pnl_pct, exit_reason "
                           "FROM trades WHERE symbol='HYPE'", conn)
    conn.close()
    df["entry_dt"] = to_utc(df["entry_time"])
    df = df[(df["entry_dt"] >= start) & (df["entry_dt"] < end)].copy()
    df["T"] = df["entry_dt"].dt.floor("30min")
    df["real_kind"] = df["exit_reason"].fillna("").str.split(":").str[0]
    return df.sort_values("entry_dt").reset_index(drop=True)


# ─────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────

def show(df):
    with pd.option_context("display.max_rows", 500, "display.width", 250, "display.max_columns", 40):
        print(df.to_string(index=False))


def report_coverage(t, src, n_closes, start, end):
    print("\n== 1. Data coverage ==")
    rows = []
    for name, key in [("snapshots", "snap"), ("orders", "orders"), ("orderbook_snapshots", "ob"),
                      (f"BTC: {src['btc']}", "btc"), (f"HYPE price for exits: {src['px']}", "px")]:
        d = t[key].df
        rows.append({"table": name, "rows": len(d),
                     "first": d["dt"].min() if len(d) else None, "last": d["dt"].max() if len(d) else None})
    show(pd.DataFrame(rows))
    print(f"Candle closes replayed: {n_closes} ({start:%Y-%m-%d} to {end:%Y-%m-%d})")


def report_funnel(sig):
    print("\n== 2. Signal funnel (every candle close, before position/cooldown) ==")
    f = sig["reason"].value_counts().rename_axis("reason").reset_index(name="candles")
    show(f)
    passed = sig[(sig["vi"] > P["vi_threshold"]) & (sig["rsi"] < P["rsi_threshold"])]
    n_nan = int(passed["btc_3h"].isna().sum())
    print(f"BTC value missing on {n_nan} of {len(passed)} candles that passed VI+RSI "
          f"(only BASELINE is gated; those show as 'BTC missing' above).")
    if P["flow_cap_usd"] is not None and len(passed):
        a = passed["flow"].fillna(0) < 0; b = passed["flow_coin"].fillna(0) < 0
        diff = a != b
        to_contra = int((a & ~b).sum()); to_base = int((~a & b).sum())
        print(f"CONTRA/BASELINE side, USD cap ${P['flow_cap_usd']:,.0f} vs as run: differs on {int(diff.sum())} of "
              f"{len(passed)} VI+RSI candles ({100 * diff.mean():.0f}%): "
              f"{to_contra} become CONTRA-side, {to_base} become BASELINE-side.")


def report_validation(sig, trades, real):
    print("\n== 3. Validation: each real HYPE trade vs what the replay saw at that candle ==")
    if real.empty:
        print("No real HYPE trades in range.")
        return pd.DataFrame()
    s = sig.set_index("T")
    tr = trades.set_index("T") if not trades.empty else pd.DataFrame()
    rows = []
    for _, r in real.iterrows():
        x = s.loc[r["T"]] if r["T"] in s.index else None
        y = tr.loc[r["T"]] if (not tr.empty and r["T"] in tr.index) else None
        rows.append({
            "id": r["id"], "T": f"{r['T']:%m-%d %H:%M}", "per": period_of(r["T"]),
            "real_px": r["entry_price"], "rep_px": None if x is None else x["price"],
            "real_flow": r["entry_z"], "rep_flow": None if x is None else x["flow"],
            "real_wh": r["entry_addrs"], "rep_wh": None if x is None else x["whales"],
            "rep_says": None if x is None else (x["signal_type"] if x["entry"] else x["reason"]),
            "rep_action": None if x is None else x["action"],
            "real_pct": r["pnl_pct"], "rep_pct": None if y is None else y["pnl_pct"],
            "real_exit": r["real_kind"], "rep_exit": None if y is None else y["exit_kind"],
        })
    v = pd.DataFrame(rows)
    show(v)

    v["sig_ok"] = v["rep_says"].isin(["CONTRA", "BASELINE"])
    v["px_ok"] = (v["rep_px"].astype(float) / v["real_px"] - 1).abs() < 0.0005
    v["flow_ok"] = (v["rep_flow"].astype(float) - v["real_flow"]).abs() < 1
    v["wh_ok"] = v["rep_wh"].astype(float) == v["real_wh"]
    v["sign_ok"] = np.sign(v["rep_flow"].astype(float)) == np.sign(v["real_flow"])
    both = v.dropna(subset=["rep_pct"])
    print("\nMatch summary (per period):")
    summ = v.groupby("per").agg(real=("id", "count"), replay_signal=("sig_ok", "sum"),
                                price_match=("px_ok", "sum"), flow_exact=("flow_ok", "sum"),
                                flow_sign=("sign_ok", "sum"), whales_match=("wh_ok", "sum")).reset_index()
    show(summ)
    if not both.empty:
        same_kind = (both["rep_exit"] == both["real_exit"].str.split().str[0]).sum()
        print(f"Trades where replay also traded that candle: {len(both)}, "
              f"same exit kind: {same_kind}, same pnl sign: "
              f"{(np.sign(both['rep_pct']) == np.sign(both['real_pct'])).sum()}")
    return v


def report_replay_only(trades, real):
    print("\n== 4. Replay entries with no real trade at that candle (bot down, or position states diverged) ==")
    if trades.empty:
        print("none")
        return
    extra = trades[~trades["T"].isin(set(real["T"]))]
    g = extra.groupby(extra["T"].dt.strftime("%Y-%m")).size().rename("replay_only").reset_index()
    show(g.rename(columns={"T": "month"}))


def summarize(df, by):
    return df.groupby(by).agg(
        n=("pnl_pct", "size"), wins=("pnl_pct", lambda s: int((s > 0).sum())),
        win_pct=("pnl_pct", lambda s: round(100 * (s > 0).mean())),
        avg_pct=("pnl_pct", lambda s: round(s.mean(), 3)), sum_pct=("pnl_pct", lambda s: round(s.sum(), 2)),
        best_ge_1=("best_pct", lambda s: int((s >= 1).sum())),
        med_min_to_peak=("min_to_peak", "median"),
    ).reset_index()


def report_results(trades):
    print("\n== 5. Replay results, strategy as coded (before fees) ==")
    if trades.empty:
        print("no trades")
        return
    trades = trades.assign(month=trades["T"].dt.strftime("%Y-%m"), per=trades["T"].map(period_of))
    show(summarize(trades, "month"))
    print()
    show(summarize(trades, "per"))
    print()
    show(summarize(trades, "signal_type"))
    print()
    show(summarize(trades, "exit_kind"))
    bad = trades["max_gap_min"] > 5
    if bad.any():
        print(f"\nNote: {int(bad.sum())} trades had a price gap > 5 min during the hold (exit timing less exact).")


def report_quirk(sig, quirk):
    print("\n== 6. The 'T' quirk: order-book window as run (since midnight) vs as written (30 min) ==")
    if not quirk:
        print("Run with --no-quirk: windows as written, nothing to compare.")
        return
    c = sig[(sig["vi"] > P["vi_threshold"]) & (sig["rsi"] < P["rsi_threshold"])].copy()
    if c.empty:
        print("No candles passed VI and RSI.")
        return
    blocked_run = (~c["ob_stale"].astype(bool)) & (c["ob_flow"] < 0)
    blocked_30 = (~c["ob_stale_30m"].astype(bool)) & (c["ob_flow_30m"] < 0)
    print(f"Candles passing VI+RSI: {len(c)}")
    print(f"  blocked by OB as run: {int(blocked_run.sum())}   blocked if 30-min window: {int(blocked_30.sum())}")
    print(f"  decision differs: {int((blocked_run != blocked_30).sum())}   "
          f"(entered as run but 30-min would block: {int((~blocked_run & blocked_30).sum())}, "
          f"blocked as run but 30-min would allow: {int((blocked_run & ~blocked_30).sum())})")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/twap.db")
    ap.add_argument("--scanner-db", default=str(Path.home() / "bots/Signal_Tracker/storage/data/scanner.db"))
    ap.add_argument("--start", default="2026-04-01")
    ap.add_argument("--end", default="2026-09-14", help="exclusive")
    ap.add_argument("--offset-sec", type=int, default=20,
                    help="seconds after the candle close the bot evaluated (real entries: ~13-22 s)")
    ap.add_argument("--no-quirk", action="store_true", help="use the lookback windows as written")
    ap.add_argument("--flow-cap-usd", type=float, default=None,
                    help="cap whale flow per address at this many USD, as intended (default: as the bot ran)")
    ap.add_argument("--out", default="data/replay_hype")
    a = ap.parse_args()
    P["flow_cap_usd"] = a.flow_cap_usd

    quirk = not a.no_quirk
    start, end = pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC")
    lo = (start - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    hi = (end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    t, src = load_tables(a.db, lo, hi)
    print("Flow: " + (f"USD cap ${a.flow_cap_usd:,.0f} per address (as intended), flow in USD"
                      if a.flow_cap_usd is not None else "as the bot ran (cap 5,000 HYPE), flow in HYPE"), flush=True)
    for k in ("snap", "px"):
        if len(t[k]) == 0:
            sys.exit(f"No HYPE rows in {k} for {lo}..{hi}; nothing to replay.")

    closes = pd.date_range(start, end, freq="30min", inclusive="left")
    sig_rows, trades, paths = [], [], []
    pos_exit, cooldown_until = None, None
    t0 = time.time()
    for i, T in enumerate(closes):
        as_of = T + pd.Timedelta(seconds=a.offset_sec)
        s = evaluate(t, as_of, quirk)
        s["T"] = T

        if pos_exit is not None and as_of < pos_exit:
            s["action"] = "in_position"
        elif cooldown_until is not None and as_of < cooldown_until:
            s["action"] = "cooldown"
        elif s["entry"]:
            res = simulate(t, as_of, s["price"])
            if res is None:
                s["action"] = "no_price_data"
            else:
                s["action"] = "enter"
                trades.append({"T": T, "entry_time": as_of, "entry_price": s["price"],
                               "signal_type": s["signal_type"], "vi": s["vi"], "rsi": s["rsi"],
                               "flow": s["flow"], "whales": s["whales"], "ob_flow": s["ob_flow"],
                               "btc_3h": s["btc_3h"], **res})
                pos_exit = res["exit_time"]
                cooldown_until = (res["exit_time"] + pd.Timedelta(minutes=P["cooldown_minutes"])
                                  if res["exit_kind"] == "TIME" else None)
        else:
            s["action"] = "none"

        if s["entry"]:
            hold, prices = price_path(t, as_of, P["max_hold_minutes"] + 60)
            for h, p in zip(hold, prices):
                paths.append((T, s["signal_type"], s["action"], round(float(h), 4),
                              round((p / s["price"] - 1) * 100, 4)))
        sig_rows.append(s)
        if (i + 1) % 1000 == 0:
            print(f"  {i + 1}/{len(closes)} candle closes ({time.time() - t0:.0f}s)", flush=True)

    sig = pd.DataFrame(sig_rows)
    trades = pd.DataFrame(trades)
    paths = pd.DataFrame(paths, columns=["T", "signal_type", "action", "minute", "ret_pct"])

    report_coverage(t, src, len(closes), start, end)
    report_funnel(sig)
    real = load_real(a.scanner_db, start, end) if Path(a.scanner_db).exists() else pd.DataFrame()
    if real.empty:
        print(f"\n(no real trades loaded from {a.scanner_db})")
        val = pd.DataFrame()
    else:
        val = report_validation(sig, trades, real)
        report_replay_only(trades, real)
    report_results(trades)
    report_quirk(sig, quirk)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sig.drop(columns=["as_of"]).to_csv(f"{out}_signals.csv", index=False)
    trades.to_csv(f"{out}_trades.csv", index=False)
    paths.to_csv(f"{out}_paths.csv", index=False)
    if not val.empty:
        val.to_csv(f"{out}_validation.csv", index=False)
    print(f"\nSaved {out}_signals.csv, _trades.csv, _paths.csv"
          f"{', _validation.csv' if not val.empty else ''}  (quirk {'on' if quirk else 'off'}, "
          f"flow cap {'$%g USD' % a.flow_cap_usd if a.flow_cap_usd is not None else '5000 HYPE as run'})")


if __name__ == "__main__":
    main()