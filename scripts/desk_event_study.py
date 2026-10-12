#!/usr/bin/env python3
"""
desk_event_study.py -- when an operator absorbs a TWAP, what happens to its
inventory and to price afterwards?

Inventory comes from the TAPE, not perp_snapshots (verified Oct 12 2026:
ecb63c ETH Oct 5 17:00-23:00, tape net == snapshot change to the cent).
The starting level is unknown, so only CHANGES are used. All of the
operator's addresses are summed per coin; fills between its own wallets
cancel out, so this is the operator-level inventory.

Per absorbed TWAP (orders the operator absorbed >= --min-ntl in
data/absorption_makers.csv; execution window placed_at_ms -> chain_end_s):
  s          -1 for a BUY TWAP (operator should get shorter), +1 for SELL
  take_usd   s * inventory change over the execution window   (+ = took it on)
  exec_bps   s * price return over the execution window       (- = pushed against it)
  unwind_h   -s * inventory change from end to end+h          (+ = worked it off)
  pnl_bps_h  s * price return from end to end+h, in bps       (+ = moved in its favour)

Baseline: the same metrics at random times across the whole roster period,
durations drawn from the events, s drawn with the events' BUY share.
"Excess" = event value minus its own coin's baseline mean.

Filters (frozen Oct 12 2026): --min-ntl 100000, --max-min 120, twins merged
(same side, placed and ended in the same minute), tape-gap and tape-coverage
filters, coins with fewer than 4 usable events skipped.

FIT / HELD-OUT: per coin, events sorted by time; the first half is FIT, the
second half HELD-OUT. The pooled held-out set is the union of the per-coin
held-out halves, so no event already inspected can end up in it.
The held-out half is only printed with --held.

------------------------------------------------------------------------
FROZEN HYPOTHESIS (iteration 3, Oct 12 2026, BEFORE opening the held-out half)

History: the first pre-registration (pnl_bps_60 < 0, price drifts against
absorbed inventory) was contradicted on the fit half of every liquid coin.
It was replaced, before any held-out data was seen, by:

  H1 (primary):   pooled over coins, held-out mean EXCESS pnl_bps_60 > 0,
                  with t >= 2 using the larger of the day- and
                  placing-wallet-clustered standard errors.
                  "After a TWAP ends, price reverts in the absorbing desk's favour."
  H2 (secondary): slope of excess pnl_bps_60 on push (= -exec_bps) > 0,
                  t >= 2 (day-clustered). "Bigger push, bigger reversal."
  Reported, not tested: pnl_bps_240, unwind_*, per-coin rows.

Fit-half reading that motivated it (desk1): exec_bps about -4 on BTC/ETH,
-7 SOL, -20 ZEC, -100 and worse on XRP/PUMP; pnl_bps_60 positive on BTC,
ETH, SOL, HYPE, ZEC (ZEC +1.1 mean); only BTC individually t > 2.
------------------------------------------------------------------------

Usage:
  venv/bin/python scripts/desk_event_study.py --op desk1 --coin BTC
  venv/bin/python scripts/desk_event_study.py --op desk1 --coin ALL
  venv/bin/python scripts/desk_event_study.py --op desk1 --coin ALL --held
"""
import argparse
import sqlite3

import numpy as np
import pandas as pd

H = [30, 60, 240]          # follow-up horizons, minutes
MIN = 60_000               # ms per minute
ALL_COINS = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "XRP", "PUMP", "FARTCOIN", "xyz:BRENTOIL"]


def clustered_se(x, groups):
    """SE of the mean, clustered by group."""
    x = np.asarray(x, float)
    dev = pd.Series(x - x.mean()).groupby(np.asarray(groups)).sum()
    return float(np.sqrt((dev ** 2).sum()) / len(x))


def clustered_slope(x, y, groups):
    """OLS slope of y on x with a cluster-robust SE."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    xd = x - x.mean()
    sxx = (xd ** 2).sum()
    b = (xd * (y - y.mean())).sum() / sxx
    e = (y - y.mean()) - b * xd
    sc = pd.Series(xd * e).groupby(np.asarray(groups)).sum()
    return float(b), float(np.sqrt((sc ** 2).sum()) / sxx)


def run_coin(con, a, coin, addrs, ids, roster_lo, roster_hi):
    """Events + baseline for one coin. Returns (ev, base, metric_cols) or (None, msg, None)."""
    mk = pd.read_csv("data/absorption_makers.csv")
    mk = mk[(mk["coin"] == coin) & mk["maker"].str.lower().isin(addrs)]
    ev = (mk.groupby(["order", "side"], as_index=False)
            .agg(op_ntl=("ntl", "sum"), op_pct=("pct", "sum")))
    ev = ev[ev["op_ntl"] >= a.min_ntl].copy()
    if ev.empty:
        return None, "no events", None
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
    log = [f"events: {n_all} absorbed >= ${a.min_ntl:,.0f}, {n_all - len(ev)} dropped (no chain end)"]
    n0 = len(ev)
    ev = ev[(ev["t1"] - ev["t0"]) <= a.max_min * MIN].copy()
    log.append(f"duration filter: {n0 - len(ev)} dropped (ran > {a.max_min:.0f} min)")
    ev["wallet"] = ev["order"].str.split("|").str[0].str.lower()
    ev["k0"], ev["k1"] = ev["t0"] // MIN, ev["t1"] // MIN
    n0 = len(ev)
    ev = (ev.sort_values("t0")
            .groupby(["side", "k0", "k1"], as_index=False)
            .agg(order=("order", "first"), wallet=("wallet", "first"), op_ntl=("op_ntl", "sum"),
                 op_pct=("op_pct", "sum"), t0=("t0", "first"), t1=("t1", "first"),
                 chain_status=("chain_status", "first"))
            .drop(columns=["k0", "k1"]))
    log.append(f"twin merge: {n0 - len(ev)} merged")
    if ev.empty:
        return None, "; ".join(log + ["no events"]), None

    lo = min(roster_lo, int(ev["t0"].min())) - 60 * MIN
    hi = max(roster_hi, int(ev["t1"].max())) + (max(H) + 1) * MIN
    px = pd.read_sql("SELECT MAX(ts) AS ts, px FROM tape_prints "
                     "WHERE coin=? AND ts>=? AND ts<? GROUP BY ts/60000 ORDER BY ts",
                     con, params=(coin, lo, hi))
    if px.empty:
        return None, "; ".join(log + ["no tape prints"]), None
    pt, pp = px["ts"].to_numpy(), px["px"].to_numpy()

    qi = ",".join("?" * len(ids))
    f = pd.read_sql(f"SELECT ts, sz, buyer IN ({qi}) AS b, seller IN ({qi}) AS s FROM tape_prints "
                    f"WHERE coin=? AND ts>=? AND ts<? AND (buyer IN ({qi}) OR seller IN ({qi})) "
                    f"ORDER BY ts", con, params=ids + ids + [coin, lo, hi] + ids + ids)
    f["d"] = f["sz"] * (f["b"] - f["s"])
    ft, fc = f["ts"].to_numpy(), f["d"].cumsum().to_numpy()
    log.append(f"tape: {len(px)} price minutes, {len(f)} operator fills")

    def px_at(t):
        i = np.searchsorted(pt, t, "right") - 1
        return pp[np.clip(i, 0, len(pp) - 1)]

    def inv_at(t):
        if len(ft) == 0:
            return np.zeros(len(np.atleast_1d(t)))
        i = np.searchsorted(ft, t, "right") - 1
        return np.where(i >= 0, fc[np.clip(i, 0, len(fc) - 1)], 0.0)

    # tape_gaps: disconnected / reconnected in ms, treated as global (conservative)
    g = pd.read_sql("SELECT disconnected AS gs, reconnected AS ge FROM tape_gaps "
                    "WHERE reconnected>=? AND disconnected<?", con, params=(lo, hi))

    def clean(t0, t1):
        w_end = t1 + max(H) * MIN
        return np.array([not ((g["gs"] < e) & (g["ge"] > s)).any() for s, e in zip(t0, w_end)])

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

    ev["s"] = np.where(ev["side"] == "BUY", -1, 1)
    cov = (ev["t0"] >= pt[0]) & (ev["t1"] + max(H) * MIN <= pt[-1])
    log.append(f"coverage filter: {(~cov).sum()} dropped")
    ev = ev[cov].copy()
    ok = clean(ev["t0"].to_numpy(), ev["t1"].to_numpy())
    log.append(f"gap filter: {(~ok).sum()} dropped")
    ev = ev[ok].sort_values("t0").reset_index(drop=True)
    if len(ev) < 4:
        return None, "; ".join(log + [f"only {len(ev)} usable events, skipped"]), None

    m = metrics(ev["t0"], ev["t1"], ev["s"])
    ev = pd.concat([ev, m], axis=1)
    ev["coin"] = coin
    ev["day"] = pd.to_datetime(ev["t0"], unit="ms").dt.date.astype(str)
    ev["half"] = np.where(ev.index < len(ev) // 2, "fit", "held")

    rng = np.random.default_rng(a.seed)
    dur = (ev["t1"] - ev["t0"]).to_numpy()
    b_lo, b_hi = int(pt[0]), int(pt[-1]) - (max(H) + 1) * MIN - int(dur.max())
    if b_hi <= b_lo:
        return None, "; ".join(log + ["tape coverage too short for a baseline"]), None
    b_t0 = rng.integers(b_lo, b_hi, a.random)
    b_t1 = b_t0 + rng.choice(dur, a.random)
    b_s = np.where(rng.random(a.random) < (ev["s"] == -1).mean(), -1, 1)
    keep = clean(b_t0, b_t1)
    base = metrics(b_t0[keep], b_t1[keep], b_s[keep])
    return ev, (base, log), list(m.columns)


def print_table(e, base_means, cols, excess):
    print(f"{'metric':<12}{'mean':>12}{'median':>12}{'se_day':>10}{'se_wallet':>11}"
          f"{'baseline':>12}{'t vs base':>11}")
    for c in cols:
        if excess:
            x = e[c] - e["coin"].map(base_means[c])
            bm = 0.0
        else:
            x = e[c]
            bm = base_means[c]
        mu, md = x.mean(), x.median()
        sd, sw = clustered_se(x, e["day"]), clustered_se(x, e["wallet"])
        se = max(sd, sw)
        t = (mu - bm) / se if se > 0 else np.nan
        print(f"{c:<12}{mu:>12,.1f}{md:>12,.1f}{sd:>10,.1f}{sw:>11,.1f}{bm:>12,.1f}{t:>11.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", default="desk1")
    ap.add_argument("--coin", default="BTC", help="a coin, or ALL to pool the taped coins")
    ap.add_argument("--min-ntl", type=float, default=100_000,
                    help="minimum USD the operator absorbed from one order")
    ap.add_argument("--max-min", type=float, default=120,
                    help="drop TWAPs whose execution ran longer than this (minutes)")
    ap.add_argument("--random", type=int, default=2000, help="baseline sample size per coin")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--held", action="store_true", help="also print the held-out half")
    ap.add_argument("--db", default="data/twap.db")
    a = ap.parse_args()

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=30)

    ops = pd.read_csv("scripts/operators.csv", header=None, names=["op", "role", "address", "name"])
    addrs = ops.loc[ops["op"] == a.op, "address"].str.lower().tolist()
    if not addrs:
        raise SystemExit(f"no addresses for operator {a.op}")
    q = ",".join("?" * len(addrs))
    ids = [r[0] for r in con.execute(
        f"SELECT id FROM tape_addresses WHERE lower(address) IN ({q})", addrs)]
    print(f"{a.op}: {len(addrs)} addresses, {len(ids)} on the tape")

    # whole roster period (all coins' orders), so the baseline covers quiet days too
    all_ms = pd.read_csv("data/absorption_makers.csv", usecols=["order"])["order"] \
               .str.split("|").str[1].astype("int64")
    roster_lo, roster_hi = int(all_ms.min()), int(all_ms.max())

    coins = ALL_COINS if a.coin.upper() == "ALL" else [a.coin]
    evs, bases = [], {}
    cols = None
    for coin in coins:
        ev, info, c = run_coin(con, a, coin, addrs, ids, roster_lo, roster_hi)
        if ev is None:
            print(f"{coin}: {info}")
            continue
        base, log = info
        cols = c
        if len(coins) == 1:
            print("\n".join(log))
        evs.append(ev)
        bases[coin] = base.mean()
        bases[coin]["n"] = len(base)
    if not evs:
        raise SystemExit("no coin had enough events")

    ev = pd.concat(evs, ignore_index=True)
    base_means = pd.DataFrame(bases).T
    tag = "ALL" if len(coins) > 1 else a.coin.replace(":", "_")
    out_csv = f"data/event_study_{a.op}_{tag}.csv"
    ev.to_csv(out_csv, index=False)
    halves = ["fit", "held"] if a.held else ["fit"]

    if len(coins) == 1:
        coin = coins[0]
        dur = (ev["t1"] - ev["t0"]).to_numpy()
        print(f"\n{a.op} {coin}: {len(ev)} events, median duration "
              f"{np.median(dur) / MIN:.0f} min, BUY share {(ev['s'] == -1).mean():.2f}, "
              f"baseline n={int(base_means.loc[coin, 'n'])}")
        if not a.held:
            print("(held-out half hidden; rerun with --held once the design is frozen)")
        for half in halves:
            e = ev[ev["half"] == half]
            print(f"\n--- {half.upper()}: {len(e)} events, {e['day'].nunique()} days, "
                  f"{e['wallet'].nunique()} placing wallets, "
                  f"{pd.to_datetime(e['t0'].min(), unit='ms'):%m-%d} -> "
                  f"{pd.to_datetime(e['t0'].max(), unit='ms'):%m-%d}")
            print_table(e, base_means.loc[coin], cols, excess=False)
        print(f"\nper-event rows: {out_csv}")
        return

    # --- pooled
    print(f"\npooled {a.op}: {len(ev)} events over {ev['coin'].nunique()} coins")
    if not a.held:
        print("(held-out half hidden; rerun with --held once the design is frozen)")
    for half in halves:
        e = ev[ev["half"] == half].copy()
        print(f"\n=== {half.upper()}: {len(e)} events, {e['day'].nunique()} days, "
              f"{e['wallet'].nunique()} placing wallets")
        print(f"{'coin':<14}{'n':>4}{'exec_bps':>10}{'pnl_60':>9}{'pnl_240':>9}{'base_60':>9}")
        for coin, gcoin in e.groupby("coin", sort=False):
            print(f"{coin:<14}{len(gcoin):>4}{gcoin['exec_bps'].mean():>10.1f}"
                  f"{gcoin['pnl_bps_60'].mean():>9.1f}{gcoin['pnl_bps_240'].mean():>9.1f}"
                  f"{base_means.loc[coin, 'pnl_bps_60']:>9.1f}")
        print("\nEXCESS over each coin's own baseline (baseline column is 0 by construction):")
        print_table(e, base_means, cols, excess=True)
        y = e["pnl_bps_60"] - e["coin"].map(base_means["pnl_bps_60"])
        push = -e["exec_bps"]
        b, se = clustered_slope(push, y, e["day"])
        print(f"\nH2 slope: excess pnl_bps_60 per bp of push = {b:.3f}  (se_day {se:.3f}, "
              f"t {b / se if se > 0 else float('nan'):.2f})")
        if half == "held":
            mu = y.mean()
            se1 = max(clustered_se(y, e["day"]), clustered_se(y, e["wallet"]))
            t1 = mu / se1 if se1 > 0 else float("nan")
            print(f"\nH1 VERDICT: excess pnl_bps_60 = {mu:.1f} bps, t = {t1:.2f} -> "
                  f"{'SUPPORTED' if t1 >= 2 else 'NOT SUPPORTED'}")
            print(f"H2 VERDICT: slope t = {b / se if se > 0 else float('nan'):.2f} -> "
                  f"{'SUPPORTED' if se > 0 and b / se >= 2 else 'NOT SUPPORTED'}")
    print(f"\nper-event rows: {out_csv}")


if __name__ == "__main__":
    main()