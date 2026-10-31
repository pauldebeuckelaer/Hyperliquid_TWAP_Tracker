#!/usr/bin/env python3
"""start_pos.py - full account view for one wallet, plus a rebuild of its perp
position before / after a window from the current API position minus tape flow.

Account view: perp positions on EVERY dex (main + HIP-3 such as xyz),
spot balances valued at USDC mids, and staked HYPE.

Usage (run from repo root):
    python3 scripts/start_pos.py <address-or-prefix> [coin] [--w0 START] [--w1 END]

coin uses the tape's naming: BTC, HYPE, xyz:BRENTOIL, ...
--w0 / --w1 take a UTC time (2026-10-30T18:00) or epoch ms.
Default window: the Oct 29-30 2026 BTC 80k break + slide.

Reading the reconstruction:
    before > 0, selling took it toward 0   -> closing a long
    before ~ 0, ended negative             -> opened a fresh short
    before > 0, ended negative             -> flipped long to short
Assumes the tape caught every fill; odd numbers -> suspect a tape gap first.
Only coins in the orderbook tracker's COINS list are on the tape; spot never is.
"""
import sys
import argparse
import datetime
import sqlite3
import json
import urllib.request

FAR = 10**14

API = 'https://api.hyperliquid.xyz/info'


def info(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=15))


def to_ms(s):
    """Accept epoch ms (1793304000000) or UTC time (2026-10-29T20:00)."""
    if s.isdigit():
        return int(s)
    t = datetime.datetime.fromisoformat(s)
    return int(t.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def fmt(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


ap = argparse.ArgumentParser(description='Account view + tape reconstruction for one wallet.')
ap.add_argument('address', help='full address or prefix, e.g. 0x2a8a01a6')
ap.add_argument('coin', nargs='?', default='BTC', help='tape name: BTC, HYPE, xyz:BRENTOIL ...')
ap.add_argument('--w0', default='2026-10-29T20:00', help='window start, UTC time or epoch ms')
ap.add_argument('--w1', default='2026-10-30T03:00', help='window end, UTC time or epoch ms')
args = ap.parse_args()

prefix = args.address.lower()
coin = args.coin
W0, W1 = to_ms(args.w0), to_ms(args.w1)
if W1 <= W0:
    sys.exit('--w1 must be after --w0')
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
    print('perp positions (dexes checked: ' + ', '.join(d or 'main' for d in DEXES) + '):')
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

    # Unified accounts: perp account value IS the spot USDC, so don't add it twice.
    spot_usdc = sum(float(b['total']) for b in bals if b['coin'] == 'USDC')
    unified = spot_usdc > 0 and abs(spot_usdc - perp_total) < 1.0
    spot_counted = spot_total - spot_usdc if unified else spot_total
    total = perp_total + spot_counted + staked_usd
    print(f"TOTAL: perp {perp_total:,.0f} + spot {spot_counted:,.0f} "
          f"+ staked {staked_usd:,.0f} = {total:,.0f}")
    if unified:
        print("  (unified account: spot USDC equals perp account value, counted once)")

    # --- tape reconstruction ------------------------------------------------
    if not on_tape:
        print(f"\n{coin} is not on the tape - no reconstruction. "
              f"Tape coins use names like BTC or xyz:BRENTOIL.")
        continue
    d_in, n_in = net(i, W0, W1)
    d_after, n_after = net(i, W1, FAR)
    end = now - d_after
    start = end - d_in
    print(f"\nwindow: {fmt(W0)} -> {fmt(W1)}")
    print(f"prints in window: {n_in} | after window: {n_after}")
    print(f"{coin} szi BEFORE window : {start:12.4f}")
    print(f"{coin} change in window  : {d_in:+12.4f}")
    print(f"{coin} szi AT window end : {end:12.4f}")
    print(f"{coin} szi NOW           : {now:12.4f}")
    print('hourly in window (buy / sell):')
    for h, b, sl in db.execute(HOURLY, (i, coin, W0, W1, i, coin, W0, W1)):
        print(f"  {h}   {b:10.4f} / {sl:10.4f}")