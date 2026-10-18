"""HYPE unlock event study — see unlock_hypotheses.md (frozen 2026-10-18).

Usage (from repo root on the box):
    python scripts/unlock_event_study.py            # fetch candles (cached) + run test
    python scripts/unlock_event_study.py --refetch  # force a fresh download
    python scripts/unlock_event_study.py --candles-dir /tmp/synth   # use given CSVs

Candles are cached to data/unlock_candles_<COIN>.csv (ignored by git).
Standard library only.
"""
import argparse
import csv
import json
import math
import os
import random
import statistics
import urllib.request
from datetime import date, datetime, timedelta, timezone

API = "https://api.hyperliquid.xyz/info"
COINS = ("HYPE", "BTC")
FETCH_START = date(2025, 12, 1)       # enough history before the Jan 3 window start
BETA_START = date(2026, 1, 1)         # frozen
BETA_END = date(2026, 10, 17)         # frozen
EVENTS = [date(2026, m, 6) for m in range(1, 11)]   # Jan..Oct 6, 2026 (frozen)
FORWARD = [date(2026, 11, 6), date(2026, 12, 6)]
PRE, POST = 3, 3                      # window D-3 .. D+3
PLACEBO_DAYS = range(13, 26)          # 13..25
N_DRAWS = 20_000
SEED = 20261018
P_THRESHOLD = 0.10


# ---------- data ----------
def fetch_daily(coin):
    start_ms = int(datetime(FETCH_START.year, FETCH_START.month, FETCH_START.day,
                            tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    body = json.dumps({"type": "candleSnapshot",
                       "req": {"coin": coin, "interval": "1d",
                               "startTime": start_ms, "endTime": end_ms}}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)
    out = {}
    for c in rows:
        d = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc).date()
        out[d] = float(c["c"])
    return out


def load_csv(path):
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out[date.fromisoformat(row["date"])] = float(row["close"])
    return out


def save_csv(path, closes):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        for d in sorted(closes):
            w.writerow([d.isoformat(), closes[d]])


def get_closes(coin, candles_dir, refetch):
    path = os.path.join(candles_dir, f"unlock_candles_{coin}.csv")
    if os.path.exists(path) and not refetch:
        return load_csv(path)
    closes = fetch_daily(coin)
    os.makedirs(candles_dir, exist_ok=True)
    save_csv(path, closes)
    return closes


def log_returns(closes):
    days = sorted(closes)
    r = {}
    for prev, cur in zip(days, days[1:]):
        if (cur - prev).days != 1:
            raise SystemExit(f"gap in candles between {prev} and {cur}")
        r[cur] = math.log(closes[cur] / closes[prev])
    return r


# ---------- stats ----------
def ols_beta(y, x):
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / sxx


def car(ar, anchor, lo=-PRE, hi=POST):
    total = 0.0
    for k in range(lo, hi + 1):
        d = anchor + timedelta(days=k)
        if d not in ar:
            return None
        total += ar[d]
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles-dir", default="data")
    ap.add_argument("--refetch", action="store_true")
    a = ap.parse_args()

    closes = {c: get_closes(c, a.candles_dir, a.refetch) for c in COINS}
    rets = {c: log_returns(closes[c]) for c in COINS}
    common = sorted(set(rets["HYPE"]) & set(rets["BTC"]))
    print(f"candles: {common[0]} .. {common[-1]}  ({len(common)} daily returns)")

    beta_days = [d for d in common if BETA_START <= d <= BETA_END]
    beta = ols_beta([rets["HYPE"][d] for d in beta_days], [rets["BTC"][d] for d in beta_days])
    print(f"beta (HYPE on BTC, {BETA_START}..{BETA_END}, n={len(beta_days)}): {beta:.3f}")

    ar = {d: rets["HYPE"][d] - beta * rets["BTC"][d] for d in common}

    # ---- events ----
    print("\nevent        CAR(-3..+3)   pre(-3..-1)   post(0..+3)   raw HYPE   raw BTC")
    cars, pres, posts = [], [], []
    for e in EVENTS:
        c = car(ar, e)
        if c is None:
            raise SystemExit(f"missing data around {e}")
        pre, post = car(ar, e, -PRE, -1), car(ar, e, 0, POST)
        raw_h = sum(rets["HYPE"][e + timedelta(days=k)] for k in range(-PRE, POST + 1))
        raw_b = sum(rets["BTC"][e + timedelta(days=k)] for k in range(-PRE, POST + 1))
        cars.append(c); pres.append(pre); posts.append(post)
        print(f"{e}   {c*100:+8.2f}%    {pre*100:+8.2f}%    {post*100:+8.2f}%   "
              f"{raw_h*100:+7.2f}%  {raw_b*100:+7.2f}%")
    mean_car = statistics.fmean(cars)
    print(f"\nmean CAR: {mean_car*100:+.2f}%   (pre {statistics.fmean(pres)*100:+.2f}%, "
          f"post {statistics.fmean(posts)*100:+.2f}%)   negative in {sum(c < 0 for c in cars)}/10")

    # ---- placebo randomization ----
    per_month = []
    for e in EVENTS:
        opts = [c for c in (car(ar, date(e.year, e.month, d)) for d in PLACEBO_DAYS) if c is not None]
        if not opts:
            raise SystemExit(f"no placebo anchors for {e:%Y-%m}")
        per_month.append(opts)
    rng = random.Random(SEED)
    draws = [statistics.fmean(rng.choice(o) for o in per_month) for _ in range(N_DRAWS)]
    p = sum(dm <= mean_car for dm in draws) / N_DRAWS
    draws.sort()
    print(f"placebo mean CAR: median {draws[N_DRAWS//2]*100:+.2f}%, "
          f"10th pct {draws[N_DRAWS//10]*100:+.2f}%, 90th pct {draws[9*N_DRAWS//10]*100:+.2f}%")
    print(f"randomization p (lower tail): {p:.4f}")

    verdict = "SUPPORTED" if (mean_car < 0 and p <= P_THRESHOLD) else "NOT SUPPORTED"
    print(f"\nH-UNLOCK1: {verdict}  (rule: mean CAR < 0 and p <= {P_THRESHOLD})")

    # ---- forward events (reported only if data exists) ----
    fwd = [(e, car(ar, e)) for e in FORWARD]
    fwd = [(e, c) for e, c in fwd if c is not None]
    if fwd:
        print("\nforward (held-out) events, beta frozen at first-run value:")
        for e, c in fwd:
            print(f"  {e}  CAR {c*100:+.2f}%")


if __name__ == "__main__":
    main()