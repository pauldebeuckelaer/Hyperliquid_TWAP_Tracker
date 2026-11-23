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

def kraken():
    out, since = [], str(T0 * 1_000_000)  # Kraken 'since' is in nanoseconds
    while True:
        url = f'https://api.kraken.com/0/public/Trades?pair=XBTUSD&since={since}&count=1000'
        r = json.load(urllib.request.urlopen(url, timeout=15))
        if r['error']: raise SystemExit(r['error'])
        res = r['result']
        d = res[next(k for k in res if k != 'last')]
        for p, v, t, side, *_ in d:
            ts = int(float(t) * 1000)
            if ts >= T1: return out
            out.append((ts, float(p), float(v), 'B' if side == 'b' else 'A', None))
        if len(d) < 1000: return out
        since = res['last']

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

b_rows, h_rows, k_rows = binance(), hl(), kraken()
B, H, K = per_sec(b_rows), per_sec(h_rows), per_sec(k_rows)
(br, bo), (hr, ho), (kr, ko) = onset(b_rows, B), onset(h_rows, H), onset(k_rows, K)
t = lambda k: datetime.fromtimestamp(k, timezone.utc).strftime('%H:%M:%S') if k else 'never'
print(f'Binance: {len(b_rows)} trades, ref {br}, +0.3% at {t(bo)}')
print(f'Kraken:  {len(k_rows)} trades, ref {kr}, +0.3% at {t(ko)}')
print(f'HL:      {len(h_rows)} prints, ref {hr}, +0.3% at {t(ho)}')
start = min(x for x in (bo, ho, ko) if x) - 6
print('\ntime      bn_px     kr_px     kr_buy  kr_sell | hl_px     292_tkr_buy')
for k in range(start, start + 20):
    b, kk, h = B.get(k, ['', 0, 0, 0]), K.get(k, ['', 0, 0, 0]), H.get(k, ['', 0, 0, 0])
    print(f'{t(k)}  {b[0]!s:9} {kk[0]!s:9} {kk[1]:7.2f} {kk[2]:7.2f} | {h[0]!s:9} {h[3]:8.1f}')