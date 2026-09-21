#!/usr/bin/env python3
"""
T1 Position Board — read-only validation dump.
Writes logs/t1_board.txt each call: every T1 whale's positions valued at
live mark (from market_snapshots) vs entry, for cross-checking Hypurrscan.
Never raises into the caller — failures are swallowed and logged.
"""
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_QUERY = """
WITH t1 AS (
  SELECT address FROM whale_addresses
  WHERE tier_position = 1 AND is_active = 1
),
lt AS (
  -- Latest snapshot per T1 wallet: one index seek on (address, snapshot_time)
  -- per wallet. The old GROUP BY over perp_snapshots made SQLite walk the
  -- whole address/time index (141.7M rows) every cycle — ~130s, measured
  -- with py-spy on Sep 21.
  SELECT t1.address,
         (SELECT MAX(p.snapshot_time) FROM perp_snapshots p
           WHERE p.address = t1.address
             AND p.snapshot_time >= strftime('%Y-%m-%dT%H:%M:%f','now','-10 minutes')) AS latest
  FROM t1
),
lp AS (
  SELECT ps.address, ps.coin, ps.side, ps.size, ps.entry_price, ps.snapshot_time,
         (SELECT m.mark_px FROM market_snapshots m
           WHERE m.coin = ps.coin
             AND m.snapshot_time >= strftime('%Y-%m-%dT%H:%M:%f','now','-10 minutes')
           ORDER BY m.snapshot_time DESC LIMIT 1) AS mark_px
  FROM lt CROSS JOIN perp_snapshots ps   -- CROSS JOIN: keep lt as the outer loop
  WHERE ps.address = lt.address AND ps.snapshot_time = lt.latest
)
SELECT address, coin, side, size, mark_px,
       ABS(size * mark_px)     AS mark_ntl,
       ABS(size * entry_price) AS entry_ntl,
       snapshot_time
FROM lp
ORDER BY address, mark_ntl DESC
"""


def dump_t1_board(storage, path="logs/t1_board.txt"):
    """Overwrite path with the current T1 mark-valued position board."""
    storage.cursor.execute(_QUERY)
    rows = storage.cursor.fetchall()
    now = datetime.now(timezone.utc)

    lines = [f"# T1 position board | {now.isoformat()} | {len(rows)} rows"]
    cur = None
    whale_mark = 0.0
    whale_entry = 0.0

    def flush_total():
        if cur is not None:
            lines.append(f"    whale total: mark ${whale_mark:,.0f}  entry ${whale_entry:,.0f}")

    for addr, coin, side, size, mark_px, mark_ntl, entry_ntl, snap in rows:
        if addr != cur:
            flush_total()
            try:
                age = int((now - datetime.fromisoformat(snap).replace(tzinfo=timezone.utc)).total_seconds())
            except Exception:
                age = -1
            flag = "" if 0 <= age <= 90 else " ⚠STALE"
            lines.append(f"\n=== {addr} | snap {snap[11:19]} age {age}s{flag} ===")
            cur, whale_mark, whale_entry = addr, 0.0, 0.0

        mark_ntl = mark_ntl or 0.0
        entry_ntl = entry_ntl or 0.0
        whale_mark += mark_ntl
        whale_entry += entry_ntl
        noprice = "" if mark_px is not None else " ⚠NOPRICE"
        mp = mark_px if mark_px is not None else 0.0
        lines.append(
            f"  {coin:<14} {side:<5} sz {size:>16,.4f}  mark {mp:>12,.4f}  "
            f"ntl ${mark_ntl:>15,.0f}  (entry ${entry_ntl:>15,.0f}){noprice}"
        )
    flush_total()

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")