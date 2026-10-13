"""Per-operator activity timeline from the raw trade tape.

For one operator label in scripts/operators.csv, finds every one of its
addresses that appears on the tape, sums its prints per day, and prints:

  1. per sub and coin: first/last day, active days, total $M, maker %,
     and $M per week (weeks labelled by their Monday, MM-DD).
     With --share the weekly columns are % of that coin's market volume.
  2. per coin for the whole operator: total $M, maker %, active subs,
     and share of market volume (overall and per week)

Maker side: tape `side` is the taker's side. 'B' = taker bought, so the
seller was the maker; 'A' = taker sold, so the buyer was the maker.

Market share needs data/market_daily.csv, made by scripts/market_volume.py.
Days the cache doesn't cover yet are left out of every share figure, and the
script says so. Refresh the cache with market_volume.py first (incremental,
fast after the first run).

Reading notes:
  - BTC and HYPE tape starts Sep 4 2026, the other coins Sep 13. A `first`
    on those dates means "since we started looking".
  - The first and last weeks are usually partial; low $M numbers there are
    not drops. Shares are not affected by partial weeks.
  - This is ALL tape activity, not only TWAP absorption, so totals are far
    larger than absorption_makers.csv.
  - Raw $M trends follow market volume. Read trends from the share columns.

Read-only on twap.db. Scans the tape twice; about 0.5-2 min per operator.

Usage:
  venv/bin/python scripts/op_timeline.py desk1
  venv/bin/python scripts/op_timeline.py desk1 --share
  venv/bin/python scripts/op_timeline.py op_399965 --coin HYPE
  venv/bin/python scripts/op_timeline.py op_7839 --min-m 2
"""
import argparse
import os
import sqlite3
import sys

import pandas as pd

DB = "file:data/twap.db?mode=ro"
OPERATORS = "scripts/operators.csv"
MARKET = "data/market_daily.csv"


def load_operator(op):
    ops = pd.read_csv(OPERATORS, header=None, names=["op", "role", "address", "name"])
    ops = ops[ops["address"].astype(str).str.startswith("0x")]  # drops a header row if present
    d = ops[ops["op"] == op].copy()
    d["address"] = d["address"].str.lower()
    d["name"] = d["name"].fillna(d["role"] + "_" + d["address"].str[:8])
    return d


def daily_prints(con, ids):
    mx = con.execute("SELECT ts FROM tape_prints ORDER BY tid DESC LIMIT 1").fetchone()[0]
    div = 86400000 if mx > 1e12 else 86400
    idl = ",".join(map(str, ids["id"]))
    q = f"""
    SELECT buyer AS id, coin, ts/{div} AS day, SUM(notional) AS ntl,
           SUM(CASE WHEN substr(lower(side),1,1)='b' THEN 0 ELSE notional END) AS mk
    FROM tape_prints WHERE buyer IN ({idl}) GROUP BY 1,2,3
    UNION ALL
    SELECT seller, coin, ts/{div}, SUM(notional),
           SUM(CASE WHEN substr(lower(side),1,1)='b' THEN notional ELSE 0 END)
    FROM tape_prints WHERE seller IN ({idl}) GROUP BY 1,2,3
    """
    r = pd.read_sql(q, con).merge(ids[["id", "name"]], on="id")
    return add_week(r)


def add_week(df):
    df = df.copy()
    df["date"] = pd.to_datetime(df["day"] * 86400, unit="s")
    df["wk"] = (df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")).dt.strftime("%m-%d")
    return df


def load_market(r):
    """Daily market volume per coin, or None. Warns about days the cache lacks."""
    if not os.path.exists(MARKET):
        print(f"(no {MARKET}: run scripts/market_volume.py for share columns)")
        return None
    m = add_week(pd.read_csv(MARKET))
    missing = sorted(set(r["day"]) - set(m["day"]))
    if missing:
        first = pd.to_datetime(missing[0] * 86400, unit="s")
        print(f"(market cache lacks {len(missing)} day(s) from {first:%m-%d}; "
              f"those days are left out of shares. Run scripts/market_volume.py to refresh)")
    return m


def covered(r, m):
    """Operator rows on days the market cache has."""
    return r[r["day"].isin(set(m["day"]))]


def weekly_share(r, m, index):
    """Operator $ per week / market $ per week for the same coin, in %."""
    rc = covered(r, m)
    op_w = rc.groupby(index + ["wk"])["ntl"].sum()
    mk_w = m.groupby(["coin", "wk"])["ntl"].sum().rename("mkt")
    s = op_w.reset_index().merge(mk_w.reset_index(), on=["coin", "wk"])
    s["pct"] = 100 * s["ntl"] / s["mkt"]
    return s.pivot_table(index=index, columns="wk", values="pct", aggfunc="sum", fill_value=0)


def per_sub_table(r, m=None, share=False):
    g = r.groupby(["name", "coin"]).agg(first=("date", "min"), last=("date", "max"),
                                        days=("day", "nunique"), ntl=("ntl", "sum"), mk=("mk", "sum"))
    g["total_m"] = g["ntl"] / 1e6
    g["maker_pct"] = 100 * g["mk"] / g["ntl"]
    if share and m is not None:
        w = weekly_share(r, m, ["name", "coin"])
    else:
        w = r.pivot_table(index=["name", "coin"], columns="wk", values="ntl", aggfunc="sum", fill_value=0) / 1e6
    g = g[["first", "last", "days", "total_m", "maker_pct"]].join(w)
    g["first"] = g["first"].dt.strftime("%m-%d")
    g["last"] = g["last"].dt.strftime("%m-%d")
    return g.sort_values("total_m", ascending=False)


def per_coin_table(r, m=None):
    g = r.groupby("coin").agg(ntl=("ntl", "sum"), mk=("mk", "sum"), subs=("name", "nunique"))
    g["total_m"] = g["ntl"] / 1e6
    g["maker_pct"] = 100 * g["mk"] / g["ntl"]
    g = g[["total_m", "maker_pct", "subs"]]
    if m is not None:
        rc = covered(r, m)
        days = rc.groupby("coin")["day"].agg(["min", "max"])
        mk = m.merge(days, left_on="coin", right_index=True)
        mk = mk[(mk["day"] >= mk["min"]) & (mk["day"] <= mk["max"])].groupby("coin")["ntl"].sum()
        g["share_pct"] = 100 * rc.groupby("coin")["ntl"].sum() / mk
        g = g.join(weekly_share(r, m, ["coin"]))
    return g.sort_values("total_m", ascending=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", help="operator label from operators.csv, e.g. desk1 or op_399965")
    ap.add_argument("--coin", help="only show this coin (e.g. HYPE, xyz:BRENTOIL)")
    ap.add_argument("--min-m", type=float, default=0.5, help="hide sub/coin rows under this $M (default 0.5)")
    ap.add_argument("--share", action="store_true", help="per-sub weekly columns as %% of market, not $M")
    ap.add_argument("--save", help="CSV for the daily rows (default /tmp/timeline_<op>.csv)")
    a = ap.parse_args()

    d = load_operator(a.op)
    if d.empty:
        sys.exit(f"no addresses for operator '{a.op}' in {OPERATORS}")
    con = sqlite3.connect(DB, uri=True, timeout=60)
    ta = pd.read_sql("SELECT id, lower(address) AS address FROM tape_addresses", con)
    ids = ta.merge(d[["address", "name"]], on="address")
    print(f"{a.op} | {len(d)} addresses, {len(ids)} seen on tape")
    if ids.empty:
        return

    r = daily_prints(con, ids)
    if r.empty:
        sys.exit(f"{a.op} has addresses on the tape but no prints")
    r.drop(columns=["wk"]).to_csv(a.save or f"/tmp/timeline_{a.op}.csv", index=False)
    if a.coin:
        r = r[r["coin"] == a.coin]
        if r.empty:
            sys.exit(f"no {a.coin} prints for {a.op}")
    m = load_market(r)

    g = per_sub_table(r, m, a.share)
    big = g[g["total_m"] >= a.min_m]
    unit = "% of market" if a.share and m is not None else "$M"
    print(f"{len(g) - len(big)} sub/coin rows under ${a.min_m}M hidden; weekly columns in {unit}\n")
    pd.set_option("display.multi_sparse", False)
    pd.set_option("display.width", 250)
    print(big.round(1).to_string())
    print("\nWhole operator, per coin" + ("; share_pct and weekly columns in % of market:" if m is not None else ":"))
    print(per_coin_table(r, m).round(1).to_string())


if __name__ == "__main__":
    main()