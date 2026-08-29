#!/usr/bin/env python3
"""Baseline rows for active tiered wallets with no (or stale) lifecycle history."""
import sqlite3, sys
from pathlib import Path

DB  = Path(__file__).resolve().parent.parent / "data" / "twap.db"
SRC = "backfill_state"

BLIND = """
SELECT w.address, w.tier, w.last_tier_update
FROM whale_addresses w
WHERE w.is_active=1 AND w.tier IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM whale_lifecycle_events e WHERE e.address=w.address)
"""

STALE = """
WITH last_ev AS (
  SELECT address, tier AS ev_tier,
         ROW_NUMBER() OVER (PARTITION BY address ORDER BY event_time DESC, id DESC) AS rn
  FROM whale_lifecycle_events
)
SELECT w.address, w.tier, w.last_tier_update, e.ev_tier
FROM whale_addresses w
JOIN last_ev e ON e.address=w.address AND e.rn=1
WHERE w.is_active=1 AND w.tier IS NOT NULL
  AND w.tier IS NOT e.ev_tier
"""

def main():
    con = sqlite3.connect(DB); cur = con.cursor()
    blind = cur.execute(BLIND).fetchall()
    stale = cur.execute(STALE).fetchall()
    print(f"blind: {len(blind)}   stale: {len(stale)}")

    n = cur.execute("SELECT COUNT(*) FROM whale_lifecycle_events "
                    "WHERE event_type='baseline'").fetchone()[0]
    print(f"existing baseline rows: {n} (expect 0)")
    if n:
        print("ABORT: baselines already present"); return

    rows = [(a, t, ts) for a, t, ts in blind]
    if "--stale" in sys.argv:
        rows += [(a, t, ts) for a, t, ts, _ in stale]
    missing_ts = sum(1 for _, _, ts in rows if not ts)
    print(f"to insert: {len(rows)}   (null last_tier_update: {missing_ts})")

    if "--commit" not in sys.argv:
        for r in rows[:5]: print("  would insert:", r)
        print("  dry run, nothing written."); return

    cur.executemany(
        "INSERT INTO whale_lifecycle_events "
        "(address, event_time, event_type, source, tier, prev_tier) "
        "VALUES (?, COALESCE(?, strftime('%Y-%m-%dT%H:%M:%f','now')), "
        "'baseline', ?, ?, NULL)",
        [(a, ts, SRC, t) for a, t, ts in rows])
    con.commit()
    print(f"inserted {len(rows)} rows")

main()