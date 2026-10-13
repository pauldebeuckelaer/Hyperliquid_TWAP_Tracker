"""Line up every profiled desk on one coin: share of market volume per week or day.

Reads the per-operator daily files op_timeline.py saves (/tmp/timeline_<op>.csv,
or another folder with --dir) and the market cache data/market_daily.csv, so it
never scans the tape. Run op_timeline.py for a desk first to include it, and
market_volume.py to refresh the market cache.

Prints:
  1. per desk: total $M, maker %, share of market overall, and split into the
     share it made as maker and as taker; then share per week
  2. with --start/--end: share per day in that window, one column per desk
     (easier to see handovers and pull-backs day by day)

Shares are desk notional / market notional for the coin. Each print counts
once in the market, but a desk counts on every side it was on, so the desks'
shares can add up to more than 100% in total (both sides of a print).

Usage:
  venv/bin/python scripts/desk_crossrun.py                      # HYPE, weekly
  venv/bin/python scripts/desk_crossrun.py --start 09-29 --end 10-08
  venv/bin/python scripts/desk_crossrun.py --coin BTC
"""
import argparse
import glob
import os
import sys

import pandas as pd

MARKET = "data/market_daily.csv"
YEAR = 2026


def week_label(day):
    d = pd.to_datetime(day * 86400, unit="s")
    return (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.strftime("%m-%d")


def to_day(mmdd):
    return int(pd.Timestamp(f"{YEAR}-{mmdd}").value // 86_400_000_000_000)


def load_desks(folder, coin):
    rows = []
    for f in sorted(glob.glob(os.path.join(folder, "timeline_*.csv"))):
        op = os.path.basename(f)[len("timeline_"):-len(".csv")]
        d = pd.read_csv(f, usecols=["coin", "day", "ntl", "mk"])
        d = d[d["coin"] == coin].groupby("day", as_index=False)[["ntl", "mk"]].sum()
        if not d.empty:
            rows.append(d.assign(op=op))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coin", default="HYPE")
    ap.add_argument("--dir", default="/tmp", help="folder with timeline_<op>.csv files (default /tmp)")
    ap.add_argument("--start", help="first day for the daily table, MM-DD")
    ap.add_argument("--end", help="last day for the daily table, MM-DD")
    a = ap.parse_args()

    if not os.path.exists(MARKET):
        sys.exit(f"no {MARKET}: run scripts/market_volume.py first")
    m = pd.read_csv(MARKET)
    m = m[m["coin"] == a.coin][["day", "ntl"]].rename(columns={"ntl": "mkt"})
    d = load_desks(a.dir, a.coin)
    if d.empty:
        sys.exit(f"no {a.coin} rows in {a.dir}/timeline_*.csv: run op_timeline.py per desk first")

    lacking = sorted(set(d["day"]) - set(m["day"]))
    if lacking:
        print(f"(market cache lacks {len(lacking)} day(s); left out of shares. Run scripts/market_volume.py)")
    d = d.merge(m, on="day")
    print(f"{a.coin}: {d['op'].nunique()} desks from {a.dir}, "
          f"{pd.to_datetime(d['day'].min() * 86400, unit='s'):%m-%d} to "
          f"{pd.to_datetime(d['day'].max() * 86400, unit='s'):%m-%d}\n")

    # 1. overall + weekly
    mkt_total = m[m["day"].between(d["day"].min(), d["day"].max())]["mkt"].sum()
    g = d.groupby("op").agg(ntl=("ntl", "sum"), mk=("mk", "sum"))
    g["total_m"] = g["ntl"] / 1e6
    g["maker_pct"] = 100 * g["mk"] / g["ntl"]
    g["share"] = 100 * g["ntl"] / mkt_total
    g["as_maker"] = 100 * g["mk"] / mkt_total
    g["as_taker"] = g["share"] - g["as_maker"]
    d["wk"] = week_label(d["day"])
    mw = m.assign(wk=week_label(m["day"])).groupby("wk")["mkt"].sum()
    w = d.groupby(["op", "wk"])["ntl"].sum().unstack(fill_value=0)
    w = 100 * w.div(mw.reindex(w.columns), axis=1)
    g = g[["total_m", "maker_pct", "share", "as_maker", "as_taker"]].join(w).sort_values("share", ascending=False)
    tot = g.drop(columns=["total_m", "maker_pct"]).sum().rename("ALL DESKS")
    pd.set_option("display.width", 250)
    print("share of market volume, % (overall, then per week):")
    print(pd.concat([g, tot.to_frame().T]).round(1).to_string())

    # 2. daily window
    if a.start or a.end:
        lo = to_day(a.start) if a.start else d["day"].min()
        hi = to_day(a.end) if a.end else d["day"].max()
        dd = d[d["day"].between(lo, hi)].copy()
        dd["share"] = 100 * dd["ntl"] / dd["mkt"]
        t = dd.pivot_table(index="day", columns="op", values="share", aggfunc="sum", fill_value=0)
        t = t[g.index.intersection(t.columns)]  # same desk order as above
        t.index = pd.to_datetime(t.index * 86400, unit="s").strftime("%m-%d %a")
        print(f"\nshare per day, % ({a.start or 'first'} to {a.end or 'last'}):")
        print(t.round(1).to_string())


if __name__ == "__main__":
    main()