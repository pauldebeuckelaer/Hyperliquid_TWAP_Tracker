#!/usr/bin/env python3
"""
Spot-axis T1 Board — spot_snapshots validation dump.

Sibling of trackers/cash_board.py, but for spot_snapshots (the spot axis)
instead of perp_account_snapshots (the cash / account-value axis).

Two entry points share one validated query (_QUERY):
  - dump_spot_board(storage, path): in-loop sibling of dump_cash_board.
    Runs on the live storage.cursor, file-only, never raises into caller.
  - main(): standalone read-only run (own mode=ro connection), prints to
    stdout and writes a file. For on-demand checks against Hypurrscan.

Design choices, deliberate and where this DIFFERS from cash_board:
  - spot_snapshots is one row per COIN per whale per snapshot (fan-out),
    unlike perp_account_snapshots which is one row per whale. So we cannot
    pick a single latest row per whale; we pick the latest snapshot_time
    per whale, then SUM(value) across that snapshot's coin rows. The
    GROUP BY does the fan-in -> back to one line per whale.
  - num_coins is carried because for spot, "how many tokens" is a useful
    at-a-glance signal and it's free from the same aggregate.
  - Same as cash_board: NO time filter on the latest-snapshot pick. Always
    show each T1 whale's most recent snapshot regardless of age, and flag
    staleness instead of dropping stale whales.
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone

# Seconds since last snapshot beyond which a whale is flagged stale.
# Spot-axis T1 == polled every cycle (~60s), so a healthy whale should be
# well under this. Tune as needed.
FRESH_MAX_AGE = 120

# Whale-driven: filter to the small tier_spot=1 set first, then for each
# whale pick its latest snapshot_time and SUM(value) across the coin rows
# at that timestamp. The correlated MAX subquery uses the
# (address, snapshot_time) index; the outer GROUP BY fans the per-coin rows
# back in to one line per whale.
_QUERY = """
SELECT w.address,
       SUM(s.value)        AS spot_value,
       COUNT(*)            AS num_coins,
       s.snapshot_time
FROM whale_addresses w
JOIN spot_snapshots s
  ON s.address = w.address
 AND s.snapshot_time = (
       SELECT MAX(s2.snapshot_time)
       FROM spot_snapshots s2
       WHERE s2.address = w.address
     )
WHERE w.tier_spot = 1
  AND w.is_active = 1
GROUP BY w.address, s.snapshot_time
ORDER BY spot_value DESC
"""


def _format_board(rows, now):
    """Shared formatter: rows -> list of text lines."""
    lines = [
        f"# Spot-axis T1 board (tier_spot=1) | {now.isoformat()} | {len(rows)} rows"
    ]
    for addr, spot_value, num_coins, snap in rows:
        spot_value = spot_value or 0.0
        num_coins = num_coins or 0

        try:
            snap_dt = datetime.fromisoformat(snap).replace(tzinfo=timezone.utc)
            age = int((now - snap_dt).total_seconds())
        except Exception:
            age = -1

        flag = "" if 0 <= age <= FRESH_MAX_AGE else " \u26a0STALE"
        snap_hms = snap[11:19] if snap and len(snap) >= 19 else str(snap)

        lines.append(
            f"{addr}  "
            f"spot ${spot_value:>16,.0f}  "
            f"coins {num_coins:>3}  "
            f"snap {snap_hms}  age {age}s{flag}"
        )
    return lines


def dump_spot_board(storage, path="logs/spot_board.txt"):
    """In-loop sibling of dump_cash_board. Overwrite path with the current
    spot-axis T1 board (tier_spot=1) from spot_snapshots.
    Runs on the live storage.cursor. Never raises into the caller."""
    storage.cursor.execute(_QUERY)
    rows = storage.cursor.fetchall()
    now = datetime.now(timezone.utc)

    lines = _format_board(rows, now)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# =========================================================================
# STANDALONE READ-ONLY ENTRY POINT
# =========================================================================

def build_board(db_path):
    """Standalone: read-only board build via own mode=ro connection."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(_QUERY).fetchall()
    finally:
        conn.close()
    return _format_board(rows, datetime.now(timezone.utc))


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "twap.db"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "spot_board.txt"

    try:
        lines = build_board(db_path)
    except Exception as e:
        print(f"spot_board: failed to build board: {e}", file=sys.stderr)
        sys.exit(1)

    text = "\n".join(lines) + "\n"
    print(text, end="")

    try:
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(text)
    except Exception as e:
        print(f"spot_board: wrote stdout but file save failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()