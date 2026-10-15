# HYPE signal — frozen hypotheses

Frozen 2026-10-14, before any look at data from Sep 14 2026 on.
Do not edit the hypotheses below after the first held-out run; add results underneath.

## Setup

- Strategy: Signal Tracker HYPE "full" (VI > 0.3, RSI < 40, capped flow / BTC gate,
  order-book filter), exactly as it ran Apr 1 - Sep 13 2026, including the SQLite 'T'
  quirk (order-book window effectively since 00:00 UTC). Replayed by
  `scripts/replay_hype.py`, variants by `scripts/replay_exits.py`.
- Fit half: Jun 7 - Sep 13 2026 (box era; replay reproduced 42 of 44 real trades).
- Held-out runs: Sep 14 - Oct 14 2026 first, then each later month added, starting with
  the mid-November re-test round. Results are reported per run and cumulatively.
- All numbers are % per trade on the candle-close entry, before fees unless stated
  (fee assumption 0.06 % round trip).

## Fit-half results these came from

Bot settings (TP 1.5 / SL 2.0), all entries: 48 trades, 42 % wins, -0.143 % per trade.

| TP / SL | all entries (as run) | all entries (30-min OB window) | strong CONTRA |
|---|---|---|---|
| 1.5 / 1.0 | +0.048 (51) | +0.066 (128) | +0.175 (20) |
| 1.5 / 2.0 | -0.143 (48) | -0.026 (109) | +0.237 (19) |

Every t-value on the fit half is below 1. These are directions, not findings.

## H1 — a tighter stop

All entries, TP 1.5: **SL 1.0 earns more per trade than SL 2.0.**

- Pass: avg_pct(SL 1.0) > avg_pct(SL 2.0) in the held-out run.
- Why: SL 1.0 beat SL 2.0 in all 4 fit-half comparisons (both OB windows, TP 1.0 and 1.5).
  The smaller-TP effect did NOT repeat across the two windows and is not part of H1.

## H2 — strong CONTRA entries

CONTRA entries with capped flow < -2000, bot exits (TP 1.5 / SL 2.0):
**avg_pct > 0, and higher than all other entries (flow >= -2000) with the same exits.**

- Pass: both conditions hold in the held-out run.
- Not tested if fewer than 5 strong CONTRA trades in the run (report n, carry forward).
- Why: the only profitable bucket in the 79 real trades (flow < -2000: 55 % wins,
  +0.19 %), and positive in every exit cell on the fit half.

## Dropped, not frozen

- H3, order-book filter as written (30 min) instead of as run: doubles entries, no better
  per trade. Not a hypothesis.
- TP 1.0 instead of 1.5: did not hold across both OB windows.

## Held-out commands (run once per period)

```bash
cd ~/bots/Hyperliquid_TWAP_Analyzer
PY=~/bots/Signal_Tracker/.venv/bin/python3
$PY scripts/replay_hype.py --start 2026-09-14 --end 2026-10-14 --out data/replay_hype_ho > /dev/null
# H1
$PY scripts/replay_exits.py --prefix data/replay_hype_ho --start 2026-09-14 --end 2026-10-14 --holdout --tp 1.5 --sl 1.0,2.0
# H2
$PY scripts/replay_exits.py --prefix data/replay_hype_ho --start 2026-09-14 --end 2026-10-14 --holdout --type CONTRA --flow-max -2000
$PY scripts/replay_exits.py --prefix data/replay_hype_ho --start 2026-09-14 --end 2026-10-14 --holdout --flow-min -2000
```

## Results

(append each held-out run here: date run, period, n, avg_pct per arm, pass/fail)
