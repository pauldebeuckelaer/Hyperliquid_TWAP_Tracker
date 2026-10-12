#!/usr/bin/env python3
"""
desk_event_study.py -- does an operator work off TWAP-absorbed inventory,
and does price drift against that inventory afterwards?

Inventory comes from the TAPE, not perp_snapshots (verified Oct 12 2026:
ecb63c ETH Oct 5 17:00-23:00, tape net == snapshot change to the cent).
The starting level is unknown, so only CHANGES are used. All of the
operator's addresses are summed per coin; fills between its own wallets
cancel out, so this is the operator-level inventory.

Per absorbed TWAP (orders the operator absorbed >= --min-ntl in
data/absorption_makers.csv; execution window placed_at_ms -> chain_end_s):
  s          -1 for a BUY TWAP (operator should get shorter), +1 for SELL
  take_usd   s * inventory change over the execution window   (+ = took it on)
  exec_bps   s * price return over the execution window       (- = moved against it)
  unwind_h   -s * inventory change from end to end+h          (+ = worked it off)
  pnl_bps_h  s * price return from end to end+h, in bps       (- = drift against inventory)

Baseline: the same metrics at random times, with durations drawn from the
events and s drawn with the events' BUY share. This is the operator's
background behaviour -- what its inventory and price do anyway.

Events are split by time: first half = FIT, second half = HELD-OUT.
The held-out half is only printed with --held. Iterate on the fit half;
open the held-out half once, when the design is frozen.

Pre-registered primary test (Oct 12 2026): on the held-out half, mean
pnl_bps_60 < 0 and significantly below the baseline (t <= -2).
Secondary: unwind_240 above the baseline.

Usage:
  venv/bin/python scripts/desk_event_study.py --op desk1 --coin BTC
  venv/bin/python scripts/desk_event_study.py --op desk1 --coin BTC --held
"""
import argparse
import sqlite3

import numpy as np
import pandas as pd

H = [30, 60, 240]          # follow-up horizons, minutes
MIN = 60_000               # ms per minute


def clustered_se(x, groups):
    """SE of the mean, clustered (events on the same day are not independent)."""
    x = np.asarray(x, float)
    dev = pd.Series(x - x.mean()).groupby(np.asarray(groups)).sum()
    return float(np.sqrt((dev ** 2).sum()) / len(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", default="desk1")
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--min-ntl", type=float, default=100_000,
                    help="minimum USD the operator absorbed from one order")
    ap.add_argument("--random", type=int, default=2000, help="baseline sample size")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--held", action="store_true", help="also print the held-out half")
    ap.add_argument("--db", default="data/twap.db")
    a = ap.parse_args()

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=30)

    # --- operator addresses -> tape ids
    ops = pd.read_csv("scripts/operators.csv", header=None, names=["op", "role", "address", "name"])
    addrs = ops.loc[ops["op"] == a.op, "address"].str.lower().tolist()
    if not addrs:
        raise SystemExit(f"no addresses for operator {a.op}")
    q = ",".join("?" * len(addrs))
    ids = [r[0] for r in con.execute(
        f"SELECT id FROM tape_addresses WHERE lower(address) IN ({q})", addrs)]
    print(f"{a.op}: {len(addrs)} addresses, {len(ids)} on the tape")

    # --- events: orders this operator absorbed on this coin
    mk = pd.read_csv("data/absorption_makers.csv")
    mk = mk[(mk["coin"] == a.coin) & mk["maker"].str.lower().isin(addrs)]
    ev = (mk.groupby(["order", "side"], as_index=False)
            .agg(op_ntl=("ntl", "sum"), op_pct=("pct", "sum")))
    ev = ev[ev["op_ntl"] >= a.min_ntl].copy()
    t0s, t1s, sts = [], [], []
    for k in ev["order"]:
        w, p = k.split("|")
        r = con.execute("SELECT chain_end_s, chain_status FROM orders "
                        "WHERE lower(address)=? AND placed_at_ms=?", (w.lower(), int(p))).fetchone()
        t0s.append(int(p))
        t1s.append(int(r[0]) * 1000 if r and r[0] else np.nan)
        sts.append(r[1] if r else None)
    ev["t0"], ev["t1"], ev["chain_status"] = t0s, t1s, sts
    n_all = len(ev)
    ev = ev[ev["t1"].notna() & (ev["t1"] > ev["t0"])].copy()
    ev["t1"] = ev["t1"].astype("int64")
    print(f"events: {n_all} absorbed >= ${a.min_ntl:,.0f}, {n_all - len(ev)} dropped (no chain end)")
    if ev.empty:
        raise SystemExit("no events")

    # --- tape: per-minute price, and the operator's own fills
    lo = int(ev["t0"].min()) - 60 * MIN
    hi = int(ev["t1"].max()) + (max(H) + 1) * MIN
    px = pd.read_sql("SELECT MAX(ts) AS ts, px FROM tape_prints "
                     "WHERE coin=? AND ts>=? AND ts<? GROUP BY ts/60000 ORDER BY ts",
                     con, params=(a.coin, lo, hi))
    pt, pp = px["ts"].to_numpy(), px["px"].to_numpy()

    qi = ",".join("?" * len(ids))
    f = pd.read_sql(f"SELECT ts, sz, buyer IN ({qi}) AS b, seller IN ({qi}) AS s FROM tape_prints "
                    f"WHERE coin=? AND ts>=? AND ts<? AND (buyer IN ({qi}) OR seller IN ({qi})) "
                    f"ORDER BY ts", con, params=ids + ids + [a.coin, lo, hi] + ids + ids)
    f["d"] = f["sz"] * (f["b"] - f["s"])
    ft, fc = f["ts"].to_numpy(), f["d"].cumsum().to_numpy()
    print(f"tape: {len(px)} price minutes, {len(f)} operator fills")

    def px_at(t):
        i = np.searchsorted(pt, t, "right") - 1
        return pp[np.clip(i, 0, len(pp) - 1)]

    def inv_at(t):
        i = np.searchsorted(ft, t, "right") - 1
        return np.where(i >= 0, fc[np.clip(i, 0, len(fc) - 1)], 0.0)

    # --- tape gaps: drop windows that touch one
    gcols = [r[1] for r in con.execute("PRAGMA table_info(tape_gaps)")]
    gs = next((c for c in gcols if "start" in c), None)
    ge = next((c for c in gcols if "end" in c), None)
    if gs and ge:
        g = pd.read_sql(f"SELECT {gs} AS gs, {ge} AS ge FROM tape_gaps WHERE {ge}>=? AND {gs}<?",
                        con, params=(lo, hi))
        def clean(t0, t1):
            w_end = t1 + max(H) * MIN
            return np.array([not ((g["gs"] < e) & (g["ge"] > s)).any() for s, e in zip(t0, w_end)])
    else:
        print(f"WARNING: tape_gaps columns not recognised {gcols}; gap filter OFF")
        def clean(t0, t1):
            return np.ones(len(t0), bool)

    def metrics(t0, t1, s):
        t0, t1, s = np.asarray(t0), np.asarray(t1), np.asarray(s, float)
        p0, p1 = px_at(t0), px_at(t1)
        out = {"take_usd": s * (inv_at(t1) - inv_at(t0)) * p1,
               "exec_bps": s * (p1 / p0 - 1) * 1e4}
        for h in H:
            th = t1 + h * MIN
            out[f"unwind_{h}"] = -s * (inv_at(th) - inv_at(t1)) * p1
            out[f"pnl_bps_{h}"] = s * (px_at(th) / p1 - 1) * 1e4
        return pd.DataFrame(out)

    # --- events
    ev["s"] = np.where(ev["side"] == "BUY", -1, 1)
    ok = clean(ev["t0"].to_numpy(), ev["t1"].to_numpy())
    print(f"gap filter: {(~ok).sum()} events dropped")
    ev = ev[ok].sort_values("t0").reset_index(drop=True)
    m = metrics(ev["t0"], ev["t1"], ev["s"])
    ev = pd.concat([ev, m], axis=1)
    ev["day"] = pd.to_datetime(ev["t0"], unit="ms").dt.date
    ev["half"] = np.where(ev.index < len(ev) // 2, "fit", "held")

    # --- baseline at random times
    rng = np.random.default_rng(a.seed)
    dur = (ev["t1"] - ev["t0"]).to_numpy()
    b_t0 = rng.integers(lo + 60 * MIN, hi - (max(H) + 1) * MIN - dur.max(), a.random)
    b_t1 = b_t0 + rng.choice(dur, a.random)
    b_s = np.where(rng.random(a.random) < (ev["s"] == -1).mean(), -1, 1)
    keep = clean(b_t0, b_t1)
    base = metrics(b_t0[keep], b_t1[keep], b_s[keep])

    # --- output
    tag = a.coin.replace(":", "_")
    out_csv = f"data/event_study_{a.op}_{tag}.csv"
    ev.drop(columns=["day"]).to_csv(out_csv, index=False)

    halves = ["fit", "held"] if a.held else ["fit"]
    print(f"\n{a.op} {a.coin}: {len(ev)} events, median duration "
          f"{np.median(dur) / MIN:.0f} min, BUY share {(ev['s'] == -1).mean():.2f}, "
          f"baseline n={len(base)}")
    if not a.held:
        print("(held-out half hidden; rerun with --held once the design is frozen)")
    for half in halves:
        e = ev[ev["half"] == half]
        print(f"\n--- {half.upper()}: {len(e)} events, {e['day'].nunique()} days, "
              f"{pd.to_datetime(e['t0'].min(), unit='ms'):%m-%d} -> "
              f"{pd.to_datetime(e['t0'].max(), unit='ms'):%m-%d}")
        print(f"{'metric':<12}{'events':>12}{'se':>10}{'baseline':>12}{'t vs base':>11}")
        for c in m.columns:
            mu, se, bm = e[c].mean(), clustered_se(e[c], e["day"]), base[c].mean()
            t = (mu - bm) / se if se > 0 else np.nan
            print(f"{c:<12}{mu:>12,.1f}{se:>10,.1f}{bm:>12,.1f}{t:>11.2f}")
    print(f"\nper-event rows: {out_csv}")


if __name__ == "__main__":
    main()