"""Descriptive only: what HYPE does day by day around the 6th (Jan–Oct 2026 unlocks).

NOT a test. H-UNLOCK3 (unlock_hypotheses.md) is frozen and evaluated on future
events only, so nothing here can change it.

Reads the candles cached by unlock_event_study.py (run that first).
    python3 scripts/unlock_path.py
"""
import math
import os
import statistics
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from unlock_event_study import EVENTS, get_closes, log_returns  # noqa: E402

BETA = 0.935
LO, HI = -5, 10


def main():
    closes = {c: get_closes(c, "data", False) for c in ("HYPE", "BTC")}
    r = {c: log_returns(closes[c]) for c in closes}
    days = sorted(set(r["HYPE"]) & set(r["BTC"]))
    ar = {d: r["HYPE"][d] - BETA * r["BTC"][d] for d in days}

    sample = [d for d in days if date(2026, 1, 1) <= d <= date(2026, 10, 17)]
    drift = statistics.fmean(ar[d] for d in sample)
    up_share = sum(ar[d] > 0 for d in sample) / len(sample)
    print(f"baseline, all days 2026: mean AR {drift*100:+.2f}%/day, "
          f"AR>0 on {up_share*100:.0f}% of days, raw HYPE up on "
          f"{sum(r['HYPE'][d] > 0 for d in sample)/len(sample)*100:.0f}% of days\n")

    # ---- per day offset, across the 10 events ----
    print("day   mean AR   median AR   AR>0   rawHYPE>0   mean cum AR from D (D..k)")
    cum = [0.0] * len(EVENTS)
    for k in range(LO, HI + 1):
        vals = [ar[e + timedelta(days=k)] for e in EVENTS]
        raw = [r["HYPE"][e + timedelta(days=k)] for e in EVENTS]
        cum_txt = ""
        if k >= 0:
            cum = [c + v for c, v in zip(cum, vals)]
            cum_txt = f"{statistics.fmean(cum)*100:+7.2f}%  (drift-only {drift*(k+1)*100:+.2f}%)"
        print(f"D{k:+3d}  {statistics.fmean(vals)*100:+6.2f}%   {statistics.median(vals)*100:+6.2f}%"
              f"   {sum(v > 0 for v in vals):2d}/10    {sum(x > 0 for x in raw):2d}/10      {cum_txt}")

    # ---- per event: how long down, how deep, how long to recover ----
    print("\nevent        trough day  trough cumAR   days to recover   cumAR at D+3   D+5     D+10")
    for e in EVENTS:
        path, c = [], 0.0
        for k in range(0, HI + 1):
            c += ar[e + timedelta(days=k)]
            path.append(c)
        t = min(range(len(path)), key=lambda i: path[i])
        rec = next((i for i in range(t + 1, len(path)) if path[i] >= 0), None)
        trough = path[t] if path[t] < 0 else 0.0
        t_txt = f"D+{t}" if path[t] < 0 else "never <0"
        rec_txt = "-" if path[t] >= 0 else (f"D+{rec}" if rec is not None else "not by D+10")
        print(f"{e}   {t_txt:>9}   {trough*100:+8.2f}%     {rec_txt:>12}     "
              f"{path[3]*100:+7.2f}%  {path[5]*100:+7.2f}%  {path[10]*100:+7.2f}%")


if __name__ == "__main__":
    main()