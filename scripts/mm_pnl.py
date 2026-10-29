#!/usr/bin/env python3
"""
mm_pnl.py - true per-coin PnL and hourly inventory path for ONE wallet since a given time.

How the numbers are built:
  - Fills come from tape_prints (buyer/seller are ids into tape_addresses).
  - net  = signed tape quantity since --since (buy +, sell -)
  - cash = -sum(signed qty * px) over the same fills
  - pos_now comes from the API (clearinghouseState).
  - start inventory = pos_now - net        (what the wallet held at --since)
  - true PnL since --since = cash + pos_now * mid_now - start_inv * px_start
      px_start = first tape print of the coin at/after --since
      mid_now  = current API mid

NOT included: fees / maker rebates, funding.
Caveat: if the tape has gaps for this wallet, the error lands in start inventory
(and therefore in PnL). A tape last-fill time is printed so you can see lag vs the API.
Main-dex coins only (allMids / clearinghouseState without a dex param), so no xyz:* coins.

Usage (from the repo root or anywhere):
  python3 scripts/mm_pnl.py 0xca230e81 --since "2026-10-29 10:30"
  python3 scripts/mm_pnl.py 0x5323b922 --since "2026-10-29 10:30" --coin HYPE
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.hyperliquid.xyz/info"
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "twap.db"


def post(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def utc_ms(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def fmt(ms, f="%Y-%m-%d %H:%M"):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime(f)


def resolve(db, prefix):
    """Full address or hex prefix -> list of (id, address) rows in tape_addresses."""
    p = prefix.lower()
    if len(p) == 42:
        return db.execute("SELECT id, address FROM tape_addresses WHERE address = ?",
                          (p,)).fetchall()
    hi = "0x" + format(int(p, 16) + 1, "x").rjust(len(p) - 2, "0")
    # range predicate instead of LIKE so the index is used
    return db.execute("SELECT id, address FROM tape_addresses "
                      "WHERE address >= ? AND address < ?", (p, hi)).fetchall()


def main():
    ap = argparse.ArgumentParser(description="True per-coin PnL + hourly inventory for one wallet.")
    ap.add_argument("prefix", help="wallet address or hex prefix, e.g. 0xca230e81")
    ap.add_argument("--since", required=True, help='UTC start, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--coin", default="HYPE")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()

    db = sqlite3.connect(a.db)
    rows = resolve(db, a.prefix)
    if len(rows) != 1:
        sys.exit(f"{len(rows)} matches for {a.prefix}: {[r[1] for r in rows]}")
    wid, addr = rows[0]
    t0 = utc_ms(a.since)

    fills = db.execute(
        """SELECT ts, sz AS s, px FROM tape_prints WHERE coin = ? AND buyer  = ? AND ts >= ?
           UNION ALL
           SELECT ts, -sz,     px FROM tape_prints WHERE coin = ? AND seller = ? AND ts >= ?
           ORDER BY ts""",
        (a.coin, wid, t0, a.coin, wid, t0)).fetchall()
    first = db.execute("SELECT px FROM tape_prints WHERE coin = ? AND ts >= ? ORDER BY ts LIMIT 1",
                       (a.coin, t0)).fetchone()
    if first is None:
        sys.exit(f"no {a.coin} prints in tape since {a.since}")
    px_start = first[0]

    net = sum(f[1] for f in fills)
    cash = sum(-f[1] * f[2] for f in fills)

    mid = float(post({"type": "allMids"})[a.coin])
    cs = post({"type": "clearinghouseState", "user": addr})
    hp = next((x["position"] for x in cs["assetPositions"]
               if x["position"]["coin"] == a.coin), None)
    pos = float(hp["szi"]) if hp else 0.0
    start = pos - net
    pnl = cash + pos * mid - start * px_start
    equity = float(cs["marginSummary"]["accountValue"])

    print(addr)
    print(f"  equity {equity:,.0f}   {a.coin} now {pos:,.0f}   net since {a.since} {net:,.0f}"
          f"   -> start inventory {start:,.0f}")
    print(f"  px at start {px_start}   mid now {mid}")
    print(f"  TRUE {a.coin} PnL since {a.since} UTC: {pnl:,.0f}   (excl. fees/rebates/funding)")
    if fills:
        print(f"  fills {len(fills):,}   last tape fill {fmt(fills[-1][0])} UTC")
    else:
        print("  no fills since start")
        return

    # hourly path: bought, sold, net, inventory at end of hour, vwap
    hours = {}
    for ts, s, px in fills:
        h = fmt(ts, "%m-%d %H")
        d = hours.setdefault(h, [0.0, 0.0, 0.0, 0.0])
        d[0 if s > 0 else 1] += abs(s)
        d[2] += s
        d[3] += abs(s) * px

    print("\n  hour        bought      sold        net   inventory_end    vwap")
    inv = start
    peak, peak_h = abs(start), "start"
    for h, (b, sd, n, v) in sorted(hours.items()):
        inv += n
        if abs(inv) > peak:
            peak, peak_h = abs(inv), h
        print(f"  {h}  {b:>9,.0f} {sd:>9,.0f} {n:>10,.0f}  {inv:>14,.0f}  {v / (b + sd):>7.2f}")
    print(f"\n  peak |inventory| {peak:,.0f} at {peak_h}")


if __name__ == "__main__":
    main()