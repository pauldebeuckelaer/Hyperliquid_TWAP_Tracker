#!/usr/bin/env python3
import json, os, sqlite3, urllib.request
from datetime import datetime, timezone

DB = os.path.expanduser('~/bots/Hyperliquid_TWAP_Analyzer/data/twap.db')
ms = lambda h, m: int(datetime(2026, 11, 23, h, m, tzinfo=timezone.utc).timestamp() * 1000)
T0, T1, TREF = ms(13, 54), ms(13, 58), ms(13, 55)

def binance():
    out, q = [], f'startTime={T0}&endTime={T1}'
    while True:
        url = f'https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&limit=1000&{q}'
        d = [x for x in json.load(urllib.request.urlopen(url, timeout=15)) if x['T'] < T1]
        out += d
        if len(d) < 1000: break
        q = f'fromId={d[-1]["a"] + 1}'
    # m=True means buyer was maker, so the taker sold
    return [(x['T'], float(x['p']), float(x['q']), 'A' if x['m'] else 'B', None) for x in out]

def hl():
    con = sqlite3.connect(DB)
    return con.execute("SELECT ts, px, notional/px, side, buyer FROM tape_prints "
                       "WHERE coin='BTC' AND ts>=? AND ts<? ORDER BY ts", (T0, T1)).fetchall()

def per_sec(rows):
    s = {}
    for ts, px, sz, side, buyer in rows:
        r = s.setdefault(ts // 1000, [px, 0.0, 0.0, 0.0])
        r[0] = px
        r[1 if side == 'B' else 2] += sz
        if buyer == 292 and side == 'B': r[3] += sz
    return s

def onset(rows, s):
    ref = [px for ts, px, *_ in rows if ts < TREF][-1]
    hit = next((k for k in sorted(s) if k * 1000 >= TREF and s[k][0] >= ref * 1.003), None)
    return ref, hit

b_rows, h_rows = binance(), hl()
B, H = per_sec(b_rows), per_sec(h_rows)
(br, bo), (hr, ho) = onset(b_rows, B), onset(h_rows, H)
t = lambda k: datetime.fromtimestamp(k, timezone.utc).strftime('%H:%M:%S') if k else 'never'
print(f'Binance: {len(b_rows)} trades, ref {br}, +0.3% at {t(bo)}')
print(f'HL:      {len(h_rows)} prints, ref {hr}, +0.3% at {t(ho)}')
if bo and ho: print(f'lead (HL - Binance): {ho - bo} s')
start = min(x for x in (bo, ho) if x) - 10
print('\ntime      bn_px     bn_buy  bn_sell | hl_px     hl_buy  hl_sell  292_tkr_buy')
for k in range(start, start + 30):
    b, h = B.get(k, ['', 0, 0, 0]), H.get(k, ['', 0, 0, 0])
    print(f'{t(k)}  {b[0]!s:9} {b[1]:7.1f} {b[2]:7.1f} | {h[0]!s:9} {h[1]:7.1f} {h[2]:7.1f} {h[3]:8.1f}')