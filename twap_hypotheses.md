# TWAP pressure hypotheses (HYPE perp)

Written 2026-10-17, BEFORE any price outcome in Sep 4 - Oct 14 was looked at.
Rule: nothing above "## Results" is edited after this file is committed.
Results are appended below, never rewritten.

## Data facts established before freezing (2026-10-15 / 10-17)
- Tracker upgraded Sep 30: placed_at_ms, chain_status, executed_sz, chain_end_s exist from Oct 1 only.
- status 'completed' != executed. Only chain_status='finished' means fully executed.
- Oct 1-14 HYPE perp: ~44% of TWAPs terminated; ~60% of announced size executed (spot ~84%).
- first_seen_at is UTC; lag vs placed_at_ms: avg 34 s, max 79 s (n=3654).
- final_progress_percent tracks EXECUTION, not time (terminated: 29.5 vs 26.3 executed).
  It reads ~92 for finished TWAPs (last poll lands just before the end).
- Poller end times match chain end within 2 min for 94-98% of orders. Known exceptions:
  - TWAPs longer than 24h (up to 7 days exist) are marked completed by the poller at 24h.
  - Some zero-fill terminated/error TWAPs carry a chain_end_s days after their planned end.
  Neither is used by the definitions below.

## Definitions
- Grid: every full UTC hour t.
- Visible window of a TWAP: from first_seen_at to
  canceled_at if set, else first_seen_at + duration_minutes (fixes the 24h poller cap).
- Pace = sign * size / duration_minutes   (HYPE per minute; BUY +, SELL -).
- ANNOUNCED pressure A(t) = sum of pace over TWAPs visible at t. Only what was live-observable.
- EXECUTED pressure E(t) = same TWAPs, each with pace replaced by
  sign * executed_amount / observed_run_minutes, counted only inside its visible window, where
  executed_amount = size if ended via completed_at, else size * final_progress_percent/100 (Sep);
  executed_sz (Oct). Uses poller times in both halves, never chain_end_s.
- Outcome R(t) = HYPE log return from t to t+60 min, price from `snapshots` (same source as replay_hype.py).
- Orders loaded from first_seen_at >= 2026-08-28 so week-long TWAPs already running on Sep 4 count.

## Periods
- Fit:      t in 2026-09-04 00:00 .. 2026-09-30 23:00 UTC
- Held-out: t in 2026-10-01 00:00 .. 2026-10-14 23:00 UTC  (opened exactly once)

## H-TWAP1 (frozen)
Announced pressure A(t) predicts next-hour return R(t) with a POSITIVE sign.
Pass on held-out only if ALL hold:
1. Pearson t >= 2
2. Spearman t >= 2
3. Pearson t >= 2 after removing the 10 hours with the largest |A(t)|
Tradeable only if the top |A| quintile, traded in the sign of A, clears ~9 bps taker round trip.

## H-TWAP2 (descriptive, no pass rule)
Does E(t) explain R(t) better than A(t)? Report both correlations and a joint regression
R ~ A + E on fit and on held-out.
- E clearly better: the ~40% unexecuted announced flow is noise the market ignores.
- A as good or better: price reacts to the announcement itself.

## Results
