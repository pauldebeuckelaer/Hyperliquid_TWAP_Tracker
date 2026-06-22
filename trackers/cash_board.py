#!/usr/bin/env python3
"""
Cash-axis T1 Board — standalone read-only validation dump.

Sibling of trackers/position_board.py, but for perp_account_snapshots
(the cash / account-value axis) instead of perp_snapshots.

Differences from the position board, both deliberate:
  - ONE line per whale (account table is one row per whale per snapshot,
    no per-coin fan-out), so no group/flush accumulator.
  - NO time filter on the latest-row pick: we always show each T1 whale's
    most recent snapshot regardless of age, and flag staleness instead of
    dropping stale whales. (You can't log staleness for a filtered-out row.)

Read-only: opens its own mode=ro connection. Never touches the live writer.
Run on demand, compare logs/cash_board.txt against Hypurrscan UI.

Usage:
    python3 cash_board.py [db_path] [out_path]
Defaults: db_path=twap.db   out_path=cash_board.txt
Also prints the board to stdout.
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone

# Seconds since last snapshot beyond which a whale is flagged stale.
# Cash-axis T1 == effective tier 1 == polled every cycle (~60s), so a
# healthy whale should be well under this. Tune as needed.
FRESH_MAX_AGE = 120

# Whale-driven: filter to the small tier_perp_amount=1 set first, then pull
# each one's latest snapshot via the (address, snapshot_time) index. Avoids a
# full-table MAX-per-address scan.
_QUERY = """
SELECT w.address,
       pas.total_account_value,
       pas.account_value,
       pas.hip3_account_value,
       pas.snapshot_time
FROM whale_addresses w
JOIN perp_account_snapshots pas
  ON pas.address = w.address
 AND pas.snapshot_time = (
       SELECT MAX(p2.snapshot_time)
       FROM perp_account_snapshots p2
       WHERE p2.address = w.address
     )
WHERE w.tier_perp_amount = 1
  AND w.is_active = 1
ORDER BY pas.total_account_value DESC
"""


def build_board(db_path):
    """Return the board as a list of text lines. Read-only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(_QUERY).fetchall()
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    lines = [
        f"# Cash-axis T1 board (tier_perp_amount=1) | {now.isoformat()} | {len(rows)} rows"
    ]

    for addr, total_av, mainnet_av, hip3_av, snap in rows:
        total_av = total_av or 0.0
        mainnet_av = mainnet_av or 0.0
        hip3_av = hip3_av or 0.0

        try:
            snap_dt = datetime.fromisoformat(snap).replace(tzinfo=timezone.utc)
            age = int((now - snap_dt).total_seconds())
        except Exception:
            age = -1

        flag = "" if 0 <= age <= FRESH_MAX_AGE else " ⚠STALE"
        snap_hms = snap[11:19] if snap and len(snap) >= 19 else str(snap)

        lines.append(
            f"{addr}  "
            f"total ${total_av:>16,.0f}  "
            f"(mainnet ${mainnet_av:>16,.0f}  hip3 ${hip3_av:>16,.0f})  "
            f"snap {snap_hms}  age {age}s{flag}"
        )

    return lines


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "twap.db"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "cash_board.txt"

    try:
        lines = build_board(db_path)
    except Exception as e:
        print(f"cash_board: failed to build board: {e}", file=sys.stderr)
        sys.exit(1)

    text = "\n".join(lines) + "\n"

    # stdout for immediate eyeballing
    print(text, end="")

    # file for record / diffing against Hypurrscan over time
    try:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(text)
    except Exception as e:
        print(f"cash_board: wrote stdout but file save failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()