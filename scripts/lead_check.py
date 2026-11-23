#!/usr/bin/env python3
"""Cross-venue lead/lag check around a fast move.

Example (today's Huang headline, also the default):
  python3 scripts/lead_check.py --coin BTC --start "2026-11-23 13:54" \
      --end "2026-11-23 13:58" --ref "2026-11-23 13:55" --thr 0.3
--thr is the move in percent from the last price before --ref; use a negative value for drops.
"""
import argparse, json, os, sqlite3, time, urllib.request
from datetime import datetime, timezone

DB = os.path.expanduser('~/bots/Hyperliquid_TWAP_Analyzer/data/twap.db')

def to_ms(s):
    return int(datetime.strptime(s, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).timestamp() * 1000)

def fmt_ms(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%H:%M:%S.%f')[:-3] if ms else 'never'

def get_json(url):
    time.sleep(0.12)  # gentle on public rate limits
    req = urllib.request.Request(url, headers={'User-Agent': 'lead_check'})
    return json.load(urllib.request.urlopen(req, timeout=15))

# Every fetcher returns rows: (ts_ms, px, size_in_coin, taker_side 'B'/'A', buyer_id_or_None)

def binance(sym, T0, T1):
    out, q = [], f'startTime={T0}&endTime={T1}'
    while True:
        d = [x for x in get_json(f'https://fapi.binance.com/fapi/v1/aggTrades?symbol={sym}&limit=1000&{q}')
             if x['T'] < T1]
        out += [(x['T'], float(x['p']), float(x['q']), 'A' if x['m'] else 'B', None) for x in d]
        if len(d) < 1000: return out
        q = f'fromId={d[-1]["a"] + 1}'

def coinbase(sym, T0, T1):
    base = f'https://api.exchange.coinbase.com/products/{sym}/trades'
    get = lambda after=None, limit=1000: get_json(f'{base}?limit={limit}' + (f'&after={after}' if after else ''))
    def ms_of(x):
        d, _, f = x['time'].rstrip('Z').partition('.')
        sec = datetime.strptime(d, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        return int(sec.timestamp() * 1000) + int((f + '000')[:3])
    at = lambda c: ms_of(get(after=c, limit=1)[0])  # time of newest trade with id < c
    hi = get(limit=1)[0]['trade_id'] + 1
    gap = 1_000_000
    while at(hi - gap) >= T0: gap *= 2
    lo = hi - gap
    while hi - lo > 1:  # binary search: largest cursor whose newest older trade is before T1
        mid = (lo + hi) // 2
        if at(mid) >= T1: hi = mid
        else: lo = mid
    out, cur = [], lo
    while True:
        d = get(after=cur)
        for x in d:
            ts = ms_of(x)
            if ts < T0: return out
            # Coinbase 'side' is the MAKER side: maker buy = taker sold
            out.append((ts, float(x['price']), float(x['size']), 'A' if x['side'] == 'buy' else 'B', None))
        cur = d[-1]['trade_id']

def okx(inst, T0, T1):
    ct = float(get_json(f'https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst}')['data'][0]['ctVal'])
    out, q = [], f'type=2&after={T1}'  # first page positioned by time, then by tradeId
    while True:
        r = get_json(f'https://www.okx.com/api/v5/market/history-trades?instId={inst}&limit=100&{q}')
        if r['code'] != '0': raise RuntimeError(r['msg'])
        d = r['data']  # newest first
        if not d: return out
        for x in d:
            ts = int(x['ts'])
            if ts < T0: return out
            out.append((ts, float(x['px']), float(x['sz']) * ct, 'B' if x['side'] == 'buy' else 'A', None))
        q = f'type=1&after={d[-1]["tradeId"]}'

def deribit(inst, T0, T1):
    out, seen, start = [], set(), T0
    while True:
        r = get_json('https://www.deribit.com/api/v2/public/get_last_trades_by_instrument_and_time'
                     f'?instrument_name={inst}&start_timestamp={start}&end_timestamp={T1 - 1}&count=1000&sorting=asc')['result']
        d = r['trades']
        for x in d:
            if x['trade_id'] in seen: continue
            seen.add(x['trade_id'])
            # BTC-PERPETUAL is inverse: amount is in USD, so divide by price for BTC
            out.append((x['timestamp'], x['price'], x['amount'] / x['price'],
                        'B' if x['direction'] == 'buy' else 'A', None))
        if not d or not r.get('has_more'): return out
        start = d[-1]['timestamp']

def kraken(sym, T0, T1):
    out, since = [], str(T0 * 1_000_000)  # Kraken 'since' is in nanoseconds
    while True:
        r = get_json(f'https://api.kraken.com/0/public/Trades?pair={sym}&since={since}&count=1000')
        if r['error']: raise RuntimeError(r['error'])
        res = r['result']
        d = res[next(k for k in res if k != 'last')]
        for p, v, t, side, *_ in d:
            ts = int(float(t) * 1000)
            if ts >= T1: return out
            out.append((ts, float(p), float(v), 'B' if side == 'b' else 'A', None))
        if len(d) < 1000: return out
        since = res['last']

def hyperliquid(coin, T0, T1):
    con = sqlite3.connect(DB)
    return con.execute("SELECT ts, px, notional/px, side, buyer FROM tape_prints "
                       "WHERE coin=? AND ts>=? AND ts<? ORDER BY ts", (coin, T0, T1)).fetchall()

# name, fetcher, symbol per coin (a missing coin is skipped and reported)
VENUES = [
    ('binance',  binance,     {'BTC': 'BTCUSDT',       'HYPE': 'HYPEUSDT'}),
    ('coinbase', coinbase,    {'BTC': 'BTC-USD',       'HYPE': 'HYPE-USD'}),
    ('okx',      okx,         {'BTC': 'BTC-USDT-SWAP', 'HYPE': 'HYPE-USDT-SWAP'}),
    ('deribit',  deribit,     {'BTC': 'BTC-PERPETUAL'}),
    ('kraken',   kraken,      {'BTC': 'XBTUSD',        'HYPE': 'HYPEUSD'}),
    ('hl',       hyperliquid, {'BTC': 'BTC',           'HYPE': 'HYPE'}),
]

def onset(rows, tref, thr):
    before = [px for ts, px, *_ in rows if ts < tref]
    if not before: return None, None
    ref = before[-1]
    lvl = ref * (1 + thr / 100)
    for ts, px, *_ in rows:
        if ts >= tref and (px >= lvl if thr > 0 else px <= lvl):
            return ref, ts
    return ref, None

def per_sec(rows, watch):
    s = {}
    for ts, px, sz, side, buyer in rows:
        r = s.setdefault(ts // 1000, [px, 0.0, 0.0, 0.0])
        r[0] = px
        r[1 if side == 'B' else 2] += sz
        if watch is not None and buyer == watch and side == 'B': r[3] += sz
    return s

ap = argparse.ArgumentParser()
ap.add_argument('--coin', default='BTC')
ap.add_argument('--start', default='2026-11-23 13:54')
ap.add_argument('--end', default='2026-11-23 13:58')
ap.add_argument('--ref', default='2026-11-23 13:55')
ap.add_argument('--thr', type=float, default=0.3)
ap.add_argument('--watch', type=int, default=292, help='HL tape address id to track as taker buyer')
a = ap.parse_args()
T0, T1, TREF = to_ms(a.start), to_ms(a.end), to_ms(a.ref)

res = {}
for name, fn, syms in VENUES:
    sym = syms.get(a.coin)
    if not sym:
        print(f'{name:9} no market mapped for {a.coin}'); continue
    try:
        rows = sorted(fn(sym, T0, T1))
    except Exception as e:
        print(f'{name:9} ERROR {type(e).__name__}: {e}'); continue
    if not rows:
        print(f'{name:9} no trades in window'); continue
    ref, hit = onset(rows, TREF, a.thr)
    res[name] = (per_sec(rows, a.watch if name == 'hl' else None), hit)
    print(f'{name:9} {len(rows):7} trades  ref {ref}  {a.thr:+}% at {fmt_ms(hit)}')

hits = sorted((h, n) for n, (_, h) in res.items() if h)
if hits:
    print('\nOrder (lag vs first):')
    for h, n in hits:
        print(f'  {n:9} {fmt_ms(h)}  +{h - hits[0][0]} ms')
    start = hits[0][0] // 1000 - 6
    names = list(res)
    print('\ntime      ' + ''.join(f'{n[:9]:>10}' for n in names) + '   watch_tkr')
    for k in range(start, start + 20):
        line = datetime.fromtimestamp(k, timezone.utc).strftime('%H:%M:%S')
        for n in names:
            v = res[n][0].get(k)
            line += f'{(v[0] if v else ""):>10}'
        hl = res.get('hl', ({}, None))[0].get(k)
        print(line + f'   {(hl[3] if hl else 0):8.1f}')