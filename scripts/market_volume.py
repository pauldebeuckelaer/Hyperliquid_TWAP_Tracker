"""Daily market tape volume per coin, cached to data/market_daily.csv.

Sums notional of every print in tape_prints per coin per UTC day, so other
scripts (op_timeline.py) can show an operator's share of the market without
scanning the whole tape again.

The first run scans everything (about 4 minutes). Later runs are incremental:
they keep the cached days and only recompute from the last cached day onward,
because that day was probably still incomplete when it was cached.

Each print counts once in the market total. An operator's volume counts every
side it was on, so in theory a share can pass 100% if it trades with itself.

Usage:
  venv/bin/python scripts/market_volume.py          # update the cache
  venv/bin/python scripts/market_volume.py --full   # rebuild from scratch
"""
import argparse
import os
import sqlite3

import pandas as pd

DB = "file:data/twap.db?mode=ro"
OUT = "data/market_daily.csv"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="ignore the cache and rescan the whole tape")
    a = ap.parse_args()

    con = sqlite3.connect(DB, uri=True, timeout=60)
    mx = con.execute("SELECT ts FROM tape_prints ORDER BY tid DESC LIMIT 1").fetchone()[0]
    div = 86400000 if mx > 1e12 else 86400

    old = None
    if os.path.exists(OUT) and not a.full:
        old = pd.read_csv(OUT)
    if old is not None and not old.empty:
        start = int(old["day"].max())
        old = old[old["day"] < start]
        q = f"SELECT coin, ts/{div} AS day, SUM(notional) AS ntl FROM tape_prints WHERE ts >= ? GROUP BY 1,2"
        new = pd.read_sql(q, con, params=(start * div,))
        r = pd.concat([old, new], ignore_index=True)
        print(f"kept {len(old)} cached rows, recomputed from {pd.to_datetime(start * 86400, unit='s'):%Y-%m-%d}")
    else:
        r = pd.read_sql(f"SELECT coin, ts/{div} AS day, SUM(notional) AS ntl FROM tape_prints GROUP BY 1,2", con)
        print("full scan")

    r = r.sort_values(["coin", "day"])
    r.to_csv(OUT, index=False)
    last = pd.to_datetime(r["day"].max() * 86400, unit="s")
    print(f"wrote {OUT}: {len(r)} rows, {r['coin'].nunique()} coins, through {last:%Y-%m-%d}")


if __name__ == "__main__":
    main()