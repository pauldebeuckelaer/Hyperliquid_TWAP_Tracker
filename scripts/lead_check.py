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

def coinbase():
    base = 'https://api.exchange.coinbase.com/products/BTC-USD/trades'
    def get(after=None, limit=1000):
        url = f'{base}?limit={limit}' + (f'&after={after}' if after else '')
        time.sleep(0.15)  # stay under the public rate limit
        req = urllib.request.Request(url, headers={'User-Agent': 'lead_check'})
        return json.load(urllib.request.urlopen(req, timeout=15))
    def ms_of(x):
        d, _, f = x['time'].rstrip('Z').partition('.')
        sec = datetime.strptime(d, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        return int(sec.timestamp() * 1000) + int((f + '000')[:3])
    at = lambda c: ms_of(get(after=c, limit=1)[0])  # time of the newest trade with id < c
    hi = get(limit=1)[0]['trade_id'] + 1
    gap = 1_000_000
    while at(hi - gap) >= T0: gap *= 2
    lo = hi - gap
    while hi - lo > 1:  # largest cursor whose newest older trade is before T1
        mid = (lo + hi) // 2
        if at(mid) >= T1: hi = mid
        else: lo = mid
    out, cur = [], lo
    while True:
        d = get(after=cur)
        for x in d:
            ts = ms_of(x)
            if ts < T0: return sorted(out)
            # Coinbase 'side' is the MAKER side: maker buy = taker sold
            out.append((ts, float(x['price']), float(x['size']), 'A' if x['side'] == 'buy' else 'B', None))
        cur = d[-1]['trade_id']

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

b_rows, h_rows, k_rows, c_rows = binance(), hl(), kraken(), coinbase()
B, H, K, C = per_sec(b_rows), per_sec(h_rows), per_sec(k_rows), per_sec(c_rows)
(br, bo), (hr, ho), (kr, ko), (cr, co) = onset(b_rows, B), onset(h_rows, H), onset(k_rows, K), onset(c_rows, C)
t = lambda k: datetime.fromtimestamp(k, timezone.utc).strftime('%H:%M:%S') if k else 'never'
print(f'Binance:  {len(b_rows)} trades, ref {br}, +0.3% at {t(bo)}')
print(f'Coinbase: {len(c_rows)} trades, ref {cr}, +0.3% at {t(co)}')
print(f'Kraken:   {len(k_rows)} trades, ref {kr}, +0.3% at {t(ko)}')
print(f'HL:       {len(h_rows)} prints, ref {hr}, +0.3% at {t(ho)}')
start = min(x for x in (bo, ho, ko, co) if x) - 6
print('\ntime      bn_px     cb_px     cb_buy  cb_sell | kr_px     hl_px     292_tkr')
for k in range(start, start + 20):
    b, c, kk, h = (D.get(k, ['', 0, 0, 0]) for D in (B, C, K, H))
    print(f'{t(k)}  {b[0]!s:9} {c[0]!s:9} {c[1]:7.2f} {c[2]:7.2f} | {kk[0]!s:9} {h[0]!s:9} {h[3]:6.1f}')