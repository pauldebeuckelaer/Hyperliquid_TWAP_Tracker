#!/usr/bin/env python3
"""start_pos.py - rebuild a wallet's perp position before / after a window
from the current API position minus tape flow.

Usage (run from repo root):
    python3 scripts/start_pos.py <address-or-prefix> [coin]

Window: the Oct 29-30 2026 BTC 80k break + slide (edit W0 / W1 for other events).

Reading the output:
    before > 0, selling took it toward 0   -> closing a long
    before ~ 0, ended negative             -> opened a fresh short
    before > 0, ended negative             -> flipped long to short
Assumes the tape caught every fill; odd numbers -> suspect a tape gap first.
"""
import sys
import sqlite3
import json
import urllib.request

W0 = 1793304000000   # 2026-10-29 20:00 UTC  (break)
W1 = 1793329200000   # 2026-10-30 03:00 UTC  (end of slide)
FAR = 10**14

prefix = sys.argv[1].lower()
coin = sys.argv[2] if len(sys.argv) > 2 else 'BTC'
hi = '0x' + format(int(prefix[2:], 16) + 1, '0%dx' % (len(prefix) - 2))

db = sqlite3.connect('file:data/twap.db?mode=ro', uri=True)

NET = """SELECT COALESCE(SUM(d),0), COUNT(*) FROM (
  SELECT  sz AS d FROM tape_prints WHERE buyer=?  AND coin=? AND ts>=? AND ts<?
  UNION ALL
  SELECT -sz AS d FROM tape_prints WHERE seller=? AND coin=? AND ts>=? AND ts<?)"""

HOURLY = """SELECT h, SUM(b), SUM(s) FROM (
  SELECT strftime('%m-%d %H:00', ts/1000, 'unixepoch') h, sz b, 0 s
    FROM tape_prints WHERE buyer=?  AND coin=? AND ts>=? AND ts<?
  UNION ALL
  SELECT strftime('%m-%d %H:00', ts/1000, 'unixepoch') h, 0 b, sz s
    FROM tape_prints WHERE seller=? AND coin=? AND ts>=? AND ts<?)
  GROUP BY h ORDER BY h"""


def net(i, a, b):
    return db.execute(NET, (i, coin, a, b, i, coin, a, b)).fetchone()


rows = db.execute(
    "SELECT id, address FROM tape_addresses WHERE address>=? AND address<?",
    (prefix, hi)).fetchall()
if not rows:
    print('no address in tape_addresses for', prefix)

for i, addr in rows:
    req = urllib.request.Request(
        'https://api.hyperliquid.xyz/info',
        data=json.dumps({'type': 'clearinghouseState', 'user': addr}).encode(),
        headers={'Content-Type': 'application/json'})
    r = json.load(urllib.request.urlopen(req, timeout=15))
    print(addr)
    print('account value:', r['marginSummary']['accountValue'])

    now = 0.0
    for p in r['assetPositions']:
        pos = p['position']
        if pos['coin'] == coin:
            now = float(pos['szi'])
            lev = pos['leverage']
            print(f"{coin} now: szi {pos['szi']} | entry {pos['entryPx']} | "
                  f"{lev['type']} {lev['value']}x | liq {pos.get('liquidationPx')}")

    d_in, n_in = net(i, W0, W1)
    d_after, n_after = net(i, W1, FAR)
    end = now - d_after
    start = end - d_in

    print(f"prints in window: {n_in} | after window: {n_after}")
    print(f"{coin} szi BEFORE window : {start:12.4f}")
    print(f"{coin} change in window  : {d_in:+12.4f}")
    print(f"{coin} szi AT window end : {end:12.4f}")
    print(f"{coin} szi NOW           : {now:12.4f}")
    print('hourly in window (buy / sell):')
    for h, b, s in db.execute(HOURLY, (i, coin, W0, W1, i, coin, W0, W1)):
        print(f"  {h}   {b:10.4f} / {s:10.4f}")