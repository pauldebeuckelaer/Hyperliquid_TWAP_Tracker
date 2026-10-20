"""
Base rate: how often does a coin stay inside a +/-X% band for N days?

Read-only. Uses market_candles (10-minute candles) in twap.db.
Each window starts at the same clock time as the real question
(default 17:00 UTC = 19:00 Belgian summer time) and runs N days.
A window "holds" a band if neither the highest high nor the lowest low
inside the window moved more than X% away from the starting close.

Windows start once per day, so consecutive windows overlap heavily:
the number of truly independent windows is roughly total_days / N.

Usage (on the box):
    python3 scripts/btc_range_base_rate.py
    python3 scripts/btc_range_base_rate.py --coin HYPE --days 8
"""
import argparse
import bisect
import os
import sqlite3
from datetime import datetime, timedelta, timezone

DEFAULT_DB = os.path.expanduser("~/bots/Hyperliquid_TWAP_Analyzer/data/twap.db")
BANDS = [3, 4, 5, 6, 8, 10]
CANDLE_MIN = 10


def parse_ts(s):
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pct(values, q):
    if not values:
        return float("nan")
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--days", type=float, default=8.0)
    ap.add_argument("--start-hour-utc", type=int, default=17)
    ap.add_argument("--min-coverage", type=float, default=0.90,
                    help="skip windows with fewer than this share of expected candles")
    a = ap.parse_args()

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT candle_time, high, low, close FROM market_candles "
        "WHERE coin = ? AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL",
        (a.coin,),
    ).fetchall()
    con.close()
    if not rows:
        print(f"No candles for coin={a.coin}")
        return

    data = sorted((parse_ts(t), h, l, c) for t, h, l, c in rows)
    times = [d[0] for d in data]
    span = timedelta(days=a.days)
    expected = a.days * 24 * 60 / CANDLE_MIN

    windows = []
    skipped_cov = 0
    for i, (t0, _, _, p0) in enumerate(data):
        if t0.hour != a.start_hour_utc or t0.minute != 0:
            continue
        end = t0 + span
        if end > times[-1]:
            break
        j = bisect.bisect_right(times, end)
        seg = data[i + 1:j]
        if len(seg) < a.min_coverage * expected:
            skipped_cov += 1
            continue
        hi = max(d[1] for d in seg)
        lo = min(d[2] for d in seg)
        up = (hi / p0 - 1) * 100
        dn = (1 - lo / p0) * 100
        windows.append((t0, up, dn))

    total_days = (times[-1] - times[0]).total_seconds() / 86400
    print(f"coin={a.coin}  window={a.days:g} days  start={a.start_hour_utc:02d}:00 UTC")
    print(f"candles: {len(data):,}  from {times[0]:%Y-%m-%d %H:%M}  to {times[-1]:%Y-%m-%d %H:%M} UTC"
          f"  ({total_days:.0f} days)")
    print(f"windows used: {len(windows)}  (skipped for gaps: {skipped_cov})"
          f"  ~independent: {total_days / a.days:.0f}")
    if not windows:
        return

    exc = [max(u, d) for _, u, d in windows]
    print(f"\nlargest move away from start inside the window (either side):")
    print(f"  median {pct(exc, 0.5):.1f}%   25th {pct(exc, 0.25):.1f}%   75th {pct(exc, 0.75):.1f}%")

    print(f"\n{'band':>6}  {'held':>6}  {'share':>6}  {'broke up':>9}  {'broke down':>11}")
    for b in BANDS:
        held = sum(1 for _, u, d in windows if u <= b and d <= b)
        up_only = sum(1 for _, u, d in windows if u > b and d <= b)
        dn_only = sum(1 for _, u, d in windows if d > b and u <= b)
        print(f"  ±{b:<3}  {held:>6}  {held / len(windows):>6.0%}  {up_only:>9}  {dn_only:>11}")
    print("\n(broke up / broke down count windows that broke only that side;"
          " windows that broke both sides are in neither column)")


if __name__ == "__main__":
    main()