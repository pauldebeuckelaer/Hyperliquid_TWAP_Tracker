#!/usr/bin/env python3
"""
absorption_roster.py

Who absorbs TWAP flow, measured over EVERY Era-2 order on the tape coins.

Same per-order logic as absorption_by_order.py, run in a loop:
  - orders: Era 2 (placed_at_ms set), chain-verified executed_ntl >= --min-ntl,
    placed after that coin's tape started
  - slices: the TWAP wallet's TAKER prints in [placed - 1 min, chain_end + 1 min]
    that sit on the order's 30 s grid (off-grid prints - a market order placed
    mid-TWAP - are excluded and their notional recorded)
  - JOIN CHECK: tape size vs chain executed_sz. Orders off by more than 1 % are
    counted and set aside; only exact-joining orders feed the roster.

Each coin's tape is loaded ONCE for the whole Era-2 span and sliced in memory.

Outputs (full addresses):
  data/absorption_orders.csv   one row per order, with join status
  data/absorption_makers.csv   one row per (order, maker): fills, slices,
                               size, ntl, pct of the order, rank in the order
Printed summary per coin - makers ranked by their share of ALL absorbed TWAP
notional on that coin:
  share      maker's absorbed notional / all joined TWAP notional on the coin
  orders     number of joined orders it filled at least once (of N)
  top15      number of those orders where it ranked in the top 15
  from_buy   share of its absorption that came from BUY TWAPs (= it sold).
             Compare with the coin's own buy share (printed in the header):
             close to it = takes both sides; far above = mostly sells into
             buyers; far below = mostly buys from sellers.

Read-only (mode=ro). Run on the box:
    venv/bin/python scripts/absorption_roster.py
    venv/bin/python scripts/absorption_roster.py --coins BTC,HYPE --min-ntl 250000
"""
import argparse
import sqlite3
import time

import numpy as np
import pandas as pd

DB_PATH = "/home/paul/bots/Hyperliquid_TWAP_Analyzer/data/twap.db"
PAD_MS = 60_000
SLICE_MS = 30_000
GRID_TOL_MS = 5_000
JOIN_TOL = 0.01
TOP = 15
DEFAULT_COINS = "BTC,ETH,FARTCOIN,HYPE,PUMP,SOL,XRP,ZEC,xyz:BRENTOIL"


def tape_span(conn, coin):
    return conn.execute("SELECT MIN(ts), MAX(ts) FROM tape_prints WHERE coin = ?",
                        (coin,)).fetchone()


def load_orders(conn, coin, t_first, t_last, min_ntl):
    return pd.read_sql_query(
        "SELECT address, side, placed_at_ms, chain_end_s, executed_sz, executed_ntl "
        "FROM orders WHERE symbol = ? AND placed_at_ms IS NOT NULL "
        "AND chain_end_s IS NOT NULL AND executed_ntl >= ? "
        "AND placed_at_ms >= ? AND chain_end_s * 1000 <= ? ORDER BY placed_at_ms",
        conn, params=(coin, min_ntl, t_first + PAD_MS, t_last - PAD_MS))


def address_ids(conn, addresses):
    out, addrs = {}, sorted(set(addresses))
    for i in range(0, len(addrs), 500):
        chunk = addrs[i:i + 500]
        q = ",".join("?" * len(chunk))
        out.update(conn.execute(
            f"SELECT address, id FROM tape_addresses WHERE address IN ({q})",
            chunk).fetchall())
    return out


def load_tape(conn, coin, lo, hi):
    t = pd.read_sql_query(
        "SELECT ts, px, notional, side, buyer, seller FROM tape_prints "
        "WHERE coin = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        conn, params=(coin, lo, hi))
    t = t.dropna(subset=["buyer", "seller"])
    return {"ts": t["ts"].to_numpy("int64"), "ntl": t["notional"].to_numpy("float64"),
            "size": (t["notional"] / t["px"]).to_numpy("float64"),
            "tb": (t["side"] == "B").to_numpy(),
            "buyer": t["buyer"].to_numpy("int64"), "seller": t["seller"].to_numpy("int64")}


def one_order(tp, o, wid):
    """Grid-filtered TWAP slices of one order. Returns (summary, maker rows)."""
    placed = int(o.placed_at_ms)
    lo, hi = placed - PAD_MS, int(o.chain_end_s) * 1000 + PAD_MS
    i0, i1 = np.searchsorted(tp["ts"], lo, "left"), np.searchsorted(tp["ts"], hi, "right")
    tb, buyer, seller = tp["tb"][i0:i1], tp["buyer"][i0:i1], tp["seller"][i0:i1]
    hit = np.flatnonzero((tb & (buyer == wid)) | (~tb & (seller == wid)))
    s = {"tape_sz": 0.0, "tape_ntl": 0.0, "slices": 0, "makers": 0, "excl_ntl": 0.0}
    if hit.size == 0:
        return s, None
    ts = tp["ts"][i0:i1][hit]
    off = (ts - placed) % SLICE_MS
    _, first = np.unique(ts, return_index=True)
    med = np.median(off[first])
    dist = np.abs(off - med)
    keep = np.minimum(dist, SLICE_MS - dist) <= GRID_TOL_MS
    ntl, size = tp["ntl"][i0:i1][hit], tp["size"][i0:i1][hit]
    maker = np.where(tb[hit], seller[hit], buyer[hit])
    s["excl_ntl"] = float(ntl[~keep].sum())
    ts, ntl, size, maker = ts[keep], ntl[keep], size[keep], maker[keep]
    if ts.size == 0:
        return s, None
    s.update(tape_sz=float(size.sum()), tape_ntl=float(ntl.sum()),
             slices=int(np.unique(ts).size), makers=int(np.unique(maker).size))
    m = pd.DataFrame({"maker": maker, "ts": ts, "size": size, "ntl": ntl})
    m = m.groupby("maker").agg(fills=("ts", "size"), slices=("ts", "nunique"),
                               size=("size", "sum"), ntl=("ntl", "sum"))
    m["pct"] = 100 * m["ntl"] / s["tape_ntl"]
    m = m.sort_values("ntl", ascending=False)
    m["rank"] = np.arange(1, len(m) + 1)
    return s, m.reset_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default=DEFAULT_COINS)
    ap.add_argument("--min-ntl", type=float, default=100_000,
                    help="minimum chain-verified executed notional per order ($)")
    ap.add_argument("--show", type=int, default=12, help="makers printed per coin")
    args = ap.parse_args()
    t_start = time.time()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)

    order_rows, maker_parts = [], []
    for coin in args.coins.split(","):
        t_first, t_last = tape_span(conn, coin)
        if t_first is None:
            print(f"{coin}: not on the tape, skipped")
            continue
        orders = load_orders(conn, coin, t_first, t_last, args.min_ntl)
        if orders.empty:
            print(f"{coin}: no qualifying orders")
            continue
        ids = address_ids(conn, orders["address"])
        tp = load_tape(conn, coin, int(orders["placed_at_ms"].min()) - PAD_MS,
                       int(orders["chain_end_s"].max()) * 1000 + PAD_MS)
        for o in orders.itertuples(index=False):
            wid = ids.get(o.address)
            if wid is None:
                s, m = {"tape_sz": 0.0, "tape_ntl": 0.0, "slices": 0, "makers": 0,
                        "excl_ntl": 0.0}, None
            else:
                s, m = one_order(tp, o, wid)
            ratio = s["tape_sz"] / o.executed_sz if o.executed_sz else np.nan
            status = ("no_tape_id" if wid is None else "no_prints" if s["slices"] == 0
                      else "ok" if abs(ratio - 1) <= JOIN_TOL else "mismatch")
            key = f"{o.address}|{o.placed_at_ms}"
            order_rows.append({"coin": coin, "order": key, "address": o.address,
                               "side": o.side, "placed_ms": o.placed_at_ms,
                               "minutes": round((o.chain_end_s - o.placed_at_ms / 1000) / 60),
                               "exec_sz": o.executed_sz, "exec_ntl": o.executed_ntl,
                               "tape_sz": s["tape_sz"], "tape_ntl": s["tape_ntl"],
                               "ratio": ratio, "slices": s["slices"], "makers": s["makers"],
                               "excl_ntl": s["excl_ntl"], "status": status})
            if status == "ok":
                m.insert(0, "order", key)
                m.insert(0, "side", o.side)
                m.insert(0, "coin", coin)
                maker_parts.append(m)
        print(f"{coin}: {len(orders)} orders done ({time.time() - t_start:.0f}s)")
        del tp

    od = pd.DataFrame(order_rows)
    mk = pd.concat(maker_parts, ignore_index=True) if maker_parts else pd.DataFrame()
    if mk.empty:
        raise SystemExit("no orders joined - nothing to summarise")
    all_ids = ",".join(str(int(i)) for i in mk["maker"].unique())
    addr = dict(conn.execute(
        f"SELECT id, address FROM tape_addresses WHERE id IN ({all_ids})").fetchall())
    conn.close()
    mk["maker"] = mk["maker"].map(lambda i: addr.get(int(i), "?"))
    od.to_csv("data/absorption_orders.csv", index=False)
    mk.round(4).to_csv("data/absorption_makers.csv", index=False)

    print(f"\nruntime {time.time() - t_start:.0f}s   CSVs: data/absorption_orders.csv, "
          f"data/absorption_makers.csv\n")
    for coin, oc in od.groupby("coin", sort=False):
        ok = oc[oc["status"] == "ok"]
        counts = oc["status"].value_counts().to_dict()
        tot = ok["exec_ntl"].sum()
        buy_share = ok.loc[ok["side"] == "BUY", "exec_ntl"].sum() / tot if tot else np.nan
        print(f"== {coin}: {len(oc)} orders, joined {counts.get('ok', 0)} "
              f"(mismatch {counts.get('mismatch', 0)}, no prints {counts.get('no_prints', 0)}, "
              f"no tape id {counts.get('no_tape_id', 0)})  "
              f"${tot / 1e6:,.1f}M joined, {100 * buy_share:.0f}% of it BUY")
        if ok.empty:
            continue
        m = mk[mk["coin"] == coin]
        g = m.groupby("maker").agg(ntl=("ntl", "sum"), orders=("order", "nunique"),
                                   top15=("rank", lambda r: int((r <= TOP).sum())))
        g["from_buy"] = (m[m["side"] == "BUY"].groupby("maker")["ntl"].sum()
                         .reindex(g.index).fillna(0) / g["ntl"])
        g["share"] = 100 * g["ntl"] / tot
        g = g.sort_values("ntl", ascending=False).head(args.show)
        out = pd.DataFrame({"maker": g.index, "share": g["share"].round(1),
                            "orders": [f"{n}/{len(ok)}" for n in g["orders"]],
                            "top15": g["top15"],
                            "from_buy": (100 * g["from_buy"]).round(0).astype(int)})
        print(out.to_string(index=False))
        print()


if __name__ == "__main__":
    main()