#!/usr/bin/env python3
"""start_pos.py - full account view for one wallet, plus a rebuild of its perp
position before / after a window from the current API position minus tape flow.

Account view: perp positions on EVERY dex (main + HIP-3 such as xyz),
spot balances valued at USDC mids, and staked HYPE.

Usage (run from repo root):
    python3 scripts/start_pos.py <address-or-prefix> [coin]

coin uses the tape's naming: BTC, HYPE, xyz:BRENTOIL, ...
Window: the Oct 29-30 2026 BTC 80k break + slide (edit W0 / W1 for other events).

Reading the reconstruction:
    before > 0, selling took it toward 0   -> closing a long
    before ~ 0, ended negative             -> opened a fresh short
    before > 0, ended negative             -> flipped long to short
Assumes the tape caught every fill; odd numbers -> suspect a tape gap first.
Only coins in the orderbook tracker's COINS list are on the tape; spot never is.
"""
import sys
import sqlite3
import json
import urllib.request

W0 = 1793304000000   # 2026-10-29 20:00 UTC  (break)
W1 = 1793329200000   # 2026-10-30 03:00 UTC  (end of slide)
FAR = 10**14

API = 'https://api.hyperliquid.xyz/info'


def info(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=15))


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


def full_name(dex, c):
    """HIP-3 coins carry the dex prefix (xyz:BRENTOIL), same as on the tape."""
    return c if (not dex or ':' in c) else f'{dex}:{c}'


# --- market-wide lookups, once per run -------------------------------------
DEXES = [''] + [d['name'] for d in info({'type': 'perpDexs'}) if d]
mids = info({'type': 'allMids'})
spot_meta = info({'type': 'spotMeta'})
tok_name = {t['index']: t['name'] for t in spot_meta['tokens']}
spot_px = {'USDC': 1.0}
for u in spot_meta['universe']:
    base, quote = u['tokens']
    if quote == 0 and u['name'] in mids:          # token 0 = USDC
        spot_px.setdefault(tok_name[base], float(mids[u['name']]))

on_tape = db.execute("SELECT 1 FROM tape_prints WHERE coin=? LIMIT 1",
                     (coin,)).fetchone() is not None

rows = db.execute(
    "SELECT id, address FROM tape_addresses WHERE address>=? AND address<?",
    (prefix, hi)).fetchall()
if not rows:
    print('no address in tape_addresses for', prefix)

for i, addr in rows:
    print(addr)

    # --- perp, every dex ----------------------------------------------------
    now = 0.0
    perp_total = 0.0
    print('perp positions:')
    for dex in DEXES:
        body = {'type': 'clearinghouseState', 'user': addr}
        if dex:
            body['dex'] = dex
        r = info(body)
        av = float(r['marginSummary']['accountValue'])
        if not r['assetPositions'] and av == 0:
            continue
        perp_total += av
        print(f"  [{dex or 'main'}] account value {av:,.0f}")
        for p in r['assetPositions']:
            pos = p['position']
            name = full_name(dex, pos['coin'])
            lev = pos['leverage']
            print(f"    {name:16s} szi {float(pos['szi']):>14,.4f} | "
                  f"ntl {float(pos['positionValue']):>13,.0f} | entry {pos['entryPx']} | "
                  f"{lev['type']} {lev['value']}x | liq {pos.get('liquidationPx')}")
            if name == coin:
                now = float(pos['szi'])

    # --- spot ---------------------------------------------------------------
    spot_total = 0.0
    s = info({'type': 'spotClearinghouseState', 'user': addr})
    bals = [b for b in s.get('balances', []) if float(b['total']) != 0]
    if bals:
        print('spot balances:')
    for b in bals:
        tot = float(b['total'])
        px = spot_px.get(b['coin'])
        if px is None:
            print(f"    {b['coin']:10s} {tot:>16,.4f} | no USDC mid")
        else:
            spot_total += tot * px
            print(f"    {b['coin']:10s} {tot:>16,.4f} | ~${tot * px:,.0f}")

    # --- staked HYPE ----------------------------------------------------------
    staked_usd = 0.0
    try:
        st = info({'type': 'delegatorSummary', 'user': addr})
        deleg = float(st.get('delegated', 0))
        undeleg = float(st.get('undelegated', 0))
        if deleg or undeleg:
            hpx = spot_px.get('HYPE', 0.0)
            staked_usd = (deleg + undeleg) * hpx
            print(f"staked HYPE: delegated {deleg:,.2f} | undelegated {undeleg:,.2f} "
                  f"| ~${staked_usd:,.0f}")
    except Exception as e:                                # endpoint is optional
        print('staking lookup failed:', e)

    print(f"TOTAL (rough): perp {perp_total:,.0f} + spot {spot_total:,.0f} "
          f"+ staked {staked_usd:,.0f} = {perp_total + spot_total + staked_usd:,.0f}")
    print("  (rough: on unified accounts spot USDC can also back perp margin)")

    # --- tape reconstruction ------------------------------------------------
    if not on_tape:
        print(f"\n{coin} is not on the tape - no reconstruction. "
              f"Tape coins use names like BTC or xyz:BRENTOIL.")
        continue
    d_in, n_in = net(i, W0, W1)
    d_after, n_after = net(i, W1, FAR)
    end = now - d_after
    start = end - d_in
    print(f"\nprints in window: {n_in} | after window: {n_after}")
    print(f"{coin} szi BEFORE window : {start:12.4f}")
    print(f"{coin} change in window  : {d_in:+12.4f}")
    print(f"{coin} szi AT window end : {end:12.4f}")
    print(f"{coin} szi NOW           : {now:12.4f}")
    print('hourly in window (buy / sell):')
    for h, b, sl in db.execute(HOURLY, (i, coin, W0, W1, i, coin, W0, W1)):
        print(f"  {h}   {b:10.4f} / {sl:10.4f}")