#!/usr/bin/env python3
"""
markout_population.py

Population version of markout_by_regime.py: maker markouts by regime for
EVERY wallet on one coin, from a single pass over the tape.

Definitions are identical to markout_by_regime.py (see its docstring):
same horizons, same gap / past-end / staleness exclusions, same regime
labels (impulse > trend > quiet, 'unlabeled' = bucket not built yet),
self-trades dropped, GROSS (no fees/rebates).

What is different: the markout is computed ONCE PER PRINT, from the
buyer's point of view, then signed per wallet:
    side 'B' print -> maker is the seller -> maker markout = -buyer_mo
    side 'A' print -> maker is the buyer  -> maker markout = +buyer_mo
So one price lookup serves all ~30k makers, instead of one load per wallet.

Columns (maker side only, per wallet):
    fills, ntl_m      maker fills and maker notional ($M)
    tkr_pct           taker share of the wallet's total notional on this
                      coin -> hybrids like 0xcf3f419d stand out
    all_5/30/60       notional-weighted maker markout, all regimes, bps
    se_30             standard error of all_30, CLUSTERED BY 5-MIN BUCKET.
                      Fills in one bucket are marked by the same move, so a
                      per-fill SE would be far too small. Rough rule:
                      |all_30| < 2*se_30 = not distinguishable from zero.
    imp_30/trd_30/qui_30   30m maker markout inside each regime
    imp_pct           impulse share of the wallet's maker notional
    imp_lean/trd_lean regime's share of maker notional divided by the
                      regime's share of the clock over the wallet's own
                      active range. >1 = leans into the regime. Biased up
                      for everyone: the whole market trades more in
                      impulses, so read the vlean columns instead.
    imp_vlean/trd_vlean regime's share of the wallet's (labeled) maker
                      notional divided by the regime's share of ALL makers'
                      notional over the wallet's own active range.
                      1 = trades like the market; this is the column
                      that separates wallets.

Read-only (mode=ro). Writes one CSV (full addresses) and prints the top rows.
Run on the box, AFTER rebuilding tape_impulse_buckets:
    venv/bin/python scripts/markout_population.py --coin BTC
"""
import argparse
import sqlite3
import time

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
HORIZONS_MIN = [5, 30, 60]
BUCKET_MS = 5 * 60 * 1000
MAX_STALE_S = 120
CHUNK = 2_000_000
REG_NAMES = {0: "impulse", 1: "trend", 2: "quiet", 3: "unlabeled"}


def load_tape(conn, coin):
    """All prints on the coin, compact dtypes, loaded in chunks to cap memory."""
    q = ("SELECT ts, px, notional, side = 'B' AS taker_buy, buyer, seller "
         "FROM tape_prints WHERE coin = ? ORDER BY ts")
    parts, n_null = [], 0
    for c in pd.read_sql_query(q, conn, params=(coin,), chunksize=CHUNK):
        bad = c["buyer"].isna() | c["seller"].isna()
        n_null += int(bad.sum())
        c = c[~bad]
        parts.append(c.astype({"ts": "int64", "px": "float64",
                               "notional": "float64", "taker_buy": "int8",
                               "buyer": "int64", "seller": "int64"}))
    return pd.concat(parts, ignore_index=True), n_null


def price_series(ts, px):
    """1-second last-price series: (second, price), sorted."""
    s = ts // 1000
    last_of_second = np.r_[s[1:] != s[:-1], True]
    return s[last_of_second], px[last_of_second]


def load_gaps(conn, coin):
    g = pd.read_sql_query(
        "SELECT disconnected, reconnected, coins FROM tape_gaps", conn)
    g = g[g["coins"].fillna("").str.split(",").apply(lambda c: coin in c)]
    g = g.sort_values("disconnected")
    d = g["disconnected"].values.astype("int64")
    r = g["reconnected"].fillna(g["disconnected"]).values.astype("int64")
    # Running max of reconnect: a window [ts, end] overlaps some gap iff the
    # latest reconnect among gaps that started by `end` is >= ts.
    rmax = np.maximum.accumulate(r) if len(r) else r
    return d, rmax


def gap_mask(d, rmax, ts, h_ms):
    if len(d) == 0:
        return np.zeros(len(ts), dtype=bool)
    j = np.searchsorted(d, ts + h_ms, side="right") - 1
    jj = np.clip(j, 0, None)
    return (j >= 0) & (rmax[jj] >= ts)


def buyer_markouts(ts, px, ps, pp, gd, grmax):
    """Per-print markout in bps from the BUYER's point of view, per horizon."""
    end_s = ps[-1]
    out, dropped = {}, {}
    for h in HORIZONS_MIN:
        h_ms = h * 60_000
        target = (ts + h_ms) // 1000
        i = np.searchsorted(ps, target, side="right") - 1
        i = np.clip(i, 0, None)
        stale = (target - ps[i]) > MAX_STALE_S
        past_end = target > end_s
        in_gap = gap_mask(gd, grmax, ts, h_ms)
        mo = (pp[i] - px) / px * 1e4
        mo[stale | past_end | in_gap] = np.nan
        out[h] = mo
        dropped[h] = {"gap": int(in_gap.sum()),
                      "past_end": int(past_end.sum()),
                      "stale": int((stale & ~past_end & ~in_gap).sum())}
    return out, dropped


def load_buckets(conn, coin):
    b = pd.read_sql_query(
        "SELECT bucket_ts, is_impulse, is_trend FROM tape_impulse_buckets "
        "WHERE coin = ? ORDER BY bucket_ts", conn, params=(coin,))
    code = np.select([b["is_impulse"] == 1, b["is_trend"] == 1], [0, 1], default=2)
    return b["bucket_ts"].values.astype("int64"), code.astype("int8")


def label(bts, bcode, pbts):
    i = np.searchsorted(bts, pbts)
    ic = np.clip(i, 0, len(bts) - 1)
    hit = (i < len(bts)) & (bts[ic] == pbts)
    return np.where(hit, bcode[ic], 3).astype("int8")


def grid_shares(bts, bcode, first, last):
    """Per wallet: share of buckets in impulse / trend over [first, last]."""
    cum = np.zeros((3, len(bts) + 1), dtype=np.int64)
    for k in range(3):
        cum[k, 1:] = np.cumsum(bcode == k)
    lo = np.searchsorted(bts, first, side="left")
    hi = np.searchsorted(bts, last, side="right")
    total = (hi - lo).astype(float)
    total[total == 0] = np.nan
    return {k: (cum[k, hi] - cum[k, lo]) / total for k in (0, 1)}


def volume_shares(m_bucket, m_reg, m_w, first, last):
    """Per wallet: share of ALL makers' notional that traded in impulse /
    trend buckets over [first, last]. Unlabeled buckets are left out."""
    b = pd.DataFrame({"bucket": m_bucket, "reg": m_reg, "w": m_w})
    b = b[b["reg"] < 3].groupby(["bucket", "reg"])["w"].sum().unstack("reg")
    b = b.reindex(columns=range(3)).fillna(0.0).sort_index()
    bk = b.index.values.astype("int64")
    cum = np.zeros((4, len(bk) + 1))
    for k in range(3):
        cum[k, 1:] = np.cumsum(b[k].values)
    cum[3, 1:] = cum[0, 1:] + cum[1, 1:] + cum[2, 1:]
    lo = np.searchsorted(bk, first, side="left")
    hi = np.searchsorted(bk, last, side="right")
    total = cum[3, hi] - cum[3, lo]
    total[total == 0] = np.nan
    return {k: (cum[k, hi] - cum[k, lo]) / total for k in (0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--min-fills", type=int, default=1000)
    ap.add_argument("--min-ntl", type=float, default=100.0,
                    help="minimum maker notional in $M")
    ap.add_argument("--show", type=int, default=40)
    ap.add_argument("--out", default=None,
                    help="CSV path (default data/markout_population_<COIN>.csv)")
    args = ap.parse_args()
    out_path = args.out or f"data/markout_population_{args.coin}.csv"
    t_start = time.time()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    t, n_null = load_tape(conn, args.coin)
    gd, grmax = load_gaps(conn, args.coin)
    bts, bcode = load_buckets(conn, args.coin)
    print(f"loaded {len(t):,} prints in {time.time() - t_start:.0f}s "
          f"(null address rows skipped: {n_null})")

    ts = t["ts"].values
    px = t["px"].values
    w = t["notional"].values
    taker_buy = t["taker_buy"].values.astype(bool)
    buyer = t["buyer"].values
    seller = t["seller"].values
    del t

    ps, pp = price_series(ts, px)
    bmo, dropped = buyer_markouts(ts, px, ps, pp, gd, grmax)
    pbucket = (ts // BUCKET_MS) * BUCKET_MS
    reg = label(bts, bcode, pbucket)

    keep = buyer != seller
    n_self = int((~keep).sum())
    maker = np.where(taker_buy, seller, buyer)[keep]
    taker = np.where(taker_buy, buyer, seller)[keep]
    msign = np.where(taker_buy, -1.0, 1.0)[keep]

    m = pd.DataFrame({"wid": maker, "w": w[keep],
                      "bucket": pbucket[keep], "reg": reg[keep]})
    for h in HORIZONS_MIN:
        mo = msign * bmo[h][keep]
        ok = ~np.isnan(mo)
        m[f"wm{h}"] = np.where(ok, mo * m["w"].values, 0.0)
        m[f"wv{h}"] = np.where(ok, m["w"].values, 0.0)
    taker_ntl = pd.Series(w[keep]).groupby(taker).sum()
    del bmo

    # ---- per-wallet totals (all makers) ----
    agg_spec = {"fills": ("w", "size"), "ntl": ("w", "sum"),
                "first": ("bucket", "min"), "last": ("bucket", "max")}
    for h in HORIZONS_MIN:
        agg_spec[f"wm{h}"] = (f"wm{h}", "sum")
        agg_spec[f"wv{h}"] = (f"wv{h}", "sum")
    a = m.groupby("wid").agg(**agg_spec)
    n_makers = len(a)
    a = a[(a["fills"] >= args.min_fills) & (a["ntl"] >= args.min_ntl * 1e6)].copy()
    # market-wide volume shares, from ALL makers (before the filter below)
    vol = volume_shares(m["bucket"].values, m["reg"].values, m["w"].values,
                        a["first"].values, a["last"].values)
    m = m[m["wid"].isin(a.index)]

    res = pd.DataFrame(index=a.index)
    res["fills"] = a["fills"]
    res["ntl_m"] = a["ntl"] / 1e6
    tk = taker_ntl.reindex(a.index).fillna(0.0)
    res["tkr_pct"] = 100 * tk / (tk + a["ntl"])
    for h in HORIZONS_MIN:
        res[f"all_{h}"] = a[f"wm{h}"] / a[f"wv{h}"]

    # ---- clustered SE of the 30m markout (cluster = 5-min bucket) ----
    c = m.groupby(["wid", "bucket"])[["wm30", "wv30"]].sum()
    c = c[c["wv30"] > 0]
    lvl = c.index.get_level_values(0)
    W = c["wv30"].groupby(level=0).sum()
    mu = c["wm30"].groupby(level=0).sum() / W
    e = c["wm30"].values - mu.reindex(lvl).values * c["wv30"].values
    e2 = pd.Series(e ** 2, index=lvl).groupby(level=0).sum()
    n = c.groupby(level=0).size()
    res["se_30"] = np.sqrt(n / (n - 1) * e2) / W

    # ---- per regime ----
    r = m.groupby(["wid", "reg"]).agg(ntl=("w", "sum"),
                                       wm=("wm30", "sum"), wv=("wv30", "sum"))
    r_ntl = r["ntl"].unstack("reg").reindex(columns=range(4)).fillna(0.0)
    r_wm = r["wm"].unstack("reg").reindex(columns=range(4))
    r_wv = r["wv"].unstack("reg").reindex(columns=range(4))
    for k, name in [(0, "imp"), (1, "trd"), (2, "qui")]:
        res[f"{name}_30"] = (r_wm[k] / r_wv[k].replace(0, np.nan)).reindex(a.index)
    share = r_ntl.div(a["ntl"], axis=0).reindex(a.index)
    res["imp_pct"] = 100 * share[0]
    grid = grid_shares(bts, bcode, a["first"].values, a["last"].values)
    res["imp_lean"] = share[0].values / grid[0]
    res["trd_lean"] = share[1].values / grid[1]
    lab = r_ntl[[0, 1, 2]].sum(axis=1).replace(0, np.nan)  # same basis as vol
    res["imp_vlean"] = (r_ntl[0] / lab).reindex(a.index).values / vol[0]
    res["trd_vlean"] = (r_ntl[1] / lab).reindex(a.index).values / vol[1]
    unl_pct = 100 * r_ntl[3].sum() / r_ntl.values.sum()

    # ---- addresses, output ----
    ids = ",".join(str(int(i)) for i in res.index)
    addr = dict(conn.execute(
        f"SELECT id, address FROM tape_addresses WHERE id IN ({ids})").fetchall())
    conn.close()
    res.insert(0, "address", [addr.get(int(i), "?") for i in res.index])
    res = res.sort_values("ntl_m", ascending=False).reset_index(drop=True)
    res.round(3).to_csv(out_path, index=False)

    first = pd.to_datetime(ts[0], unit="ms").strftime("%Y-%m-%d %H:%M")
    last = pd.to_datetime(ts[-1], unit="ms").strftime("%Y-%m-%d %H:%M")
    print(f"{args.coin}  {first} -> {last} UTC   self-trades dropped: {n_self:,}")
    for h, d in dropped.items():
        print(f"  {h:>2}m excluded (prints): gap {d['gap']:,}, "
              f"past end {d['past_end']:,}, stale {d['stale']:,}")
    print(f"makers: {n_makers:,} total, {len(res)} with >= {args.min_fills} fills "
          f"and >= ${args.min_ntl:.0f}M  |  unlabeled notional in these: {unl_pct:.2f}%")
    print("GROSS maker markouts in bps, notional-weighted (+ = favourable to the maker)")
    print(f"CSV: {out_path}   runtime {time.time() - t_start:.0f}s\n")

    show = res.head(args.show).copy()
    show["address"] = show["address"].str[:10]
    num = show.columns.drop("address")
    show[num] = show[num].round(2)
    show["ntl_m"] = show["ntl_m"].round(0)
    show["tkr_pct"] = show["tkr_pct"].round(1)
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(show.to_string(index=False))


if __name__ == "__main__":
    main()