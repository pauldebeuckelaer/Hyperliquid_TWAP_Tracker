# HYPE unlock event study — frozen hypotheses

Frozen: 2026-10-18, before any price data around the unlock dates was pulled or viewed.
Script: `scripts/unlock_event_study.py`

## Background

Hyperliquid Labs distributes team (core contributor) tokens monthly on the 6th,
starting 2026-01-06 (announced on Discord by the cofounder, Dec 28 2025).
The headline/projected unlock (~9.9M HYPE/month) is far larger than the amounts
actually announced for distribution (e.g. ~330K HYPE in April 2026).
The date is fully public, so the prior expectation is a NULL result.

## Data and definitions (fixed)

- Daily UTC candles for HYPE and BTC perps, Hyperliquid `candleSnapshot`, interval `1d`.
- Daily log return of day d: ln(close_d / close_{d-1}).
- Beta: OLS slope of HYPE daily log return on BTC daily log return,
  over all days 2026-01-01 .. 2026-10-17 (inclusive). No intercept used in abnormal returns.
- Abnormal return: AR_d = r_HYPE,d − beta · r_BTC,d.
- Event days: the 6th of each month, 2026-01-06 .. 2026-10-06 (10 events).
- Window: days D−3 .. D+3 inclusive (7 daily returns). CAR = sum of AR over the window.
- Test statistic: mean CAR across the 10 events.

## Placebo (randomization test)

- For each of the same 10 months, draw ONE placebo anchor uniformly from days 13..25
  (windows 10..28 never overlap any unlock window), compute the same mean CAR.
- 20,000 draws, seed 20261018.
- p = share of placebo means ≤ observed mean CAR (one-sided, lower tail).

## H-UNLOCK1 (tested)

HYPE's BTC-adjusted CAR over D−3..D+3 around the 6th is negative on average,
and unusual versus month-matched placebo anchors.

**SUPPORTED iff** mean CAR < 0 **and** p ≤ 0.10. Otherwise NOT SUPPORTED.
Tested once on the 10 events above. Not to be re-tested with a changed window,
threshold, beta period or placebo design.

## H-UNLOCK2 (descriptive only, not tested)

Split of the CAR into pre (D−3..D−1) and post (D..D+3): does any move happen
before the unlock (positioning) or after it (actual selling)?

## Forward round

Nov 6 and Dec 6 2026 are reported as held-out events, same definitions,
beta frozen at the value from the first run.

## Results

(append below; never edit anything above this line)


### Run 1 — 2026-10-18 (events Jan–Oct 2026, n=10)
- beta = 0.935 (n=290)
- mean CAR(-3..+3) = +0.23%, negative in 3/10
- placebo mean CAR: median +2.47%, 10th pct -0.59%, 90th pct +6.37%
- randomization p = 0.1772
- **H-UNLOCK1: NOT SUPPORTED** (mean CAR >= 0; p > 0.10)
- Note: HYPE drifted above beta*BTC through 2026 (placebo median +2.47%/window).
  Relative to placebo, unlock windows were ~2.2% lower, not significant.
  The "mean CAR < 0" condition is stricter than intended under this drift;
  rule stays as frozen.
- H-UNLOCK2 (descriptive): pre +2.70% (positive 8/10), post -2.47% (negative 8/10).
  Run-up into the 6th, fade after. Seen post hoc — not a finding.
