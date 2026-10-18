# TWAP placement event study (HYPE perp)

Written 2026-10-17, BEFORE any price outcome after large TWAP placements was looked at.
Fit half Sep 4-30 was used for twap_hypotheses.md (H-TWAP1, outstanding pressure, null);
that test looked at hourly-grid returns only, never at returns after individual placements.
Held-out Oct 1-14 has not been opened for any price outcome.
Rule: nothing above "## Results" is edited after this file is committed.

## Why
H-TWAP1 (steady outstanding pressure) showed nothing. Remaining question: does the
APPEARANCE of a single large TWAP carry information? Earlier hint (hype_hypotheses.md,
2026-10-15, not frozen): single large sellers carry the information, not headcount.

## Definitions
- Orders: symbol='HYPE', product_type='PERP', duration_minutes <= 1440 (week-long TWAPs are not flow).
- Large: size >= 7000 HYPE. Fixed absolute threshold = 95th percentile of Sep 4-30 sizes
  (dur <= 24h, n=7434), chosen from the size distribution only. Not recomputed on held-out.
- Event: large orders from the same address on the same side are merged into ONE event when they
  start within 60 min of the event's first order. Event time t0 = first_seen_at of that first order.
  Event sign: BUY +1, SELL -1. Event size = sum of merged sizes.
- Outcome S = sign * ln(p(t0+60min) / p(t0)) in bps. Price from `snapshots`, last price at or before
  the time, within 5 min, else the event is dropped.
- Non-overlapping subset: events sorted by t0, keep an event only if t0 >= previous kept t0 + 60 min.

## Periods
- Fit:      t0 in 2026-09-04 00:00 .. 2026-09-30 23:00 UTC
- Held-out: t0 in 2026-10-01 00:00 .. 2026-10-14 23:00 UTC  (opened exactly once)

## H-TWAP3 (frozen)
After a large TWAP appears, HYPE moves in its direction over the next 60 min: mean S > 0.
Pass on held-out only if ALL hold:
1. t(mean S) >= 2 over all events
2. t(mean S) >= 2 after removing the 5 events with the largest |S|
3. mean S > 0 on the non-overlapping subset
Tradeable only if mean S clears ~9 bps taker round trip.

## Descriptive (no pass rule)
- Horizons 15 and 240 min.
- BUY vs SELL events; event size 7k-25k vs >= 25k.
- Held-out only: events whose orders mostly executed (executed_sz / size >= 0.9) vs mostly not
  (< 0.5). This is the real announced-vs-executed question, only possible from Oct 1.

## Results

### 2026-10-17, H-TWAP3 fit half (Sep 4-30, 282 events, 99 addresses)
- 60 min: mean +6.87 bps (se 5.22, t +1.32); without top 5 |S| t +0.66; non-overlap t +0.87.
  Share S>0 0.514. NOT SUPPORTED on fit; carried to held-out only as part of the H-TWAP4 run.
- Descriptive: 15 min +7.04 bps t +2.82; 240 min t +1.28; BUY +16.2 (t +2.25), SELL -5.5 (t -0.74);
  7k-25k t +0.95, >=25k t +0.97. Top addresses: 0x4c3d48cb 26 events, 0x186db447 11.
- 15 min robustness on fit (data/twap_events_fit.csv): all t +2.87; without top 5 t +2.61;
  non-overlap 15m t +2.07; without 0x4c3d48cb t +3.03; one mean per address (n=99) t +2.44.

### H-TWAP4 (frozen 2026-10-17, from the fit result above, BEFORE held-out)
Same events and definitions as H-TWAP3, horizon 15 min instead of 60:
after a large TWAP appears, HYPE moves in its direction over the next 15 min, mean S15 > 0.
Pass on held-out only if ALL hold:
1. t(mean S15) >= 2 over all events
2. t >= 2 after removing the 5 events with the largest |S15|
3. mean S15 > 0 on the non-overlapping subset (15 min spacing)
4. mean of per-address means > 0
Not tradeable at ~9 bps taker round trip even if it passes (fit mean +7 bps).
Power note: held-out has ~half the events of fit; with a true +7 bps the expected t is ~2,
so a real effect fails roughly half the time. A FAIL is weak evidence against, not proof.
Key descriptive split (held-out only): executed >= 90% vs < 50%.
- Mechanical (own slices push price): effect only in the executed group.
- Informational (announcement itself): effect also in the unexecuted group.
H-TWAP3 (60 min) is reported from the same held-out run with its own frozen rule.

### 2026-10-17, held-out (Oct 1-14, 110 events, 53 addresses), opened once
- H-TWAP4 (15 min): mean +6.42 bps (se 3.24, t +1.98); without top 5 t +2.08; non-overlap
  t +1.92 (mean>0); per-address mean +8.21 (t +2.14). VERDICT: FAIL (condition 1, t 1.98 < 2).
  Effect size replicated (fit +7.1). Underpowered as noted before the run.
- H-TWAP3 (60 min): mean +2.17 bps, t +0.34. VERDICT: FAIL.
- Descriptive: executed >= 90% +5.79 (t 1.68, n=66); executed < 50% +13.56 (t 2.09, n=34).
  Unexecuted large TWAPs moved price MORE. Not frozen, not claimed. 240 min -15.0 (t -1.26).
- Carried to Nov re-test (Oct 15 - Nov 14, fresh): H-TWAP4 unchanged, plus a new frozen
  H-TWAP5 written before that data exists: large TWAPs with executed < 50% are followed by
  a 15-min move in their direction (same definitions, same four pass conditions).
