#!/usr/bin/env python3
"""
Hypurrscan Storage
==================
Protocol-wide aggregates sourced from the Hypurrscan API.

Unlike MarketStorage (per-coin, minute-level) or WhaleStorage (per-wallet),
this module stores time-series of protocol-wide metrics: platform fees today,
stablecoin supply / HLP state / holder stats later.

Tables:
    platform_fees - Cumulative platform fees snapshots (micro-USDC)

Retention:
    These tables are NEVER purged. The point of collecting them is the
    long-term historical series - cleanup would defeat the purpose.
    Growth is trivial (<150 rows/day, ~5MB/decade).

Dedup:
    All tables use time-based natural primary keys with INSERT OR IGNORE,
    so the hourly poll can throw the full 998-row /feesRecent payload at
    the table every time and SQLite drops duplicates for free.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import BaseStorage

logger = logging.getLogger(__name__)


class HypurrscanStorage(BaseStorage):
    """Storage for protocol-wide aggregates from Hypurrscan."""

    def _create_tables(self):
        """Create platform_fees and any future Hypurrscan aggregate tables."""
        # ---------------------------------------------------------------
        # platform_fees
        # ---------------------------------------------------------------
        # Source: GET https://api.hypurrscan.io/fees         (full history, ~daily)
        #         GET https://api.hypurrscan.io/feesRecent   (last ~8d, ~10min cadence)
        #
        # Both endpoints return rows of shape:
        #   {"time": <unix_seconds>, "total_fees": <int>, "total_spot_fees": <int>}
        #
        # Values are CUMULATIVE in micro-USDC (1e-6 USDC). Strictly non-decreasing.
        # Compute deltas at query time: fees_24h = total_fees[now] - total_fees[24h_ago].
        # Compute perp fees at query time: total_perp_fees = total_fees - total_spot_fees.
        # ---------------------------------------------------------------
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS platform_fees (
                time            INTEGER PRIMARY KEY,   -- unix seconds, from API
                total_fees      INTEGER NOT NULL,      -- micro-USDC, cumulative
                total_spot_fees INTEGER NOT NULL,      -- micro-USDC, cumulative
                inserted_at     TEXT    NOT NULL       -- ISO timestamp, when WE wrote it
            )
        """)

        self.conn.commit()
        logger.debug("HypurrscanStorage tables ready")

    def _create_indexes(self):
        """No extra indexes needed - PK on `time` covers every planned query."""
        # The PK is already a B-tree index on `time`, which serves both
        # range scans ("fees between X and Y") and latest-row lookups
        # ("most recent fee snapshot"). No additional indexes warranted.
        pass

    # =====================================================================
    # platform_fees - writes
    # =====================================================================

    def insert_platform_fees_batch(self, rows: List[Dict]) -> int:
        """
        Insert a batch of platform fees rows. Duplicates are silently ignored.

        Designed for the "dumb endpoint, smart storage" pattern: caller can
        pass the full /feesRecent payload (998 rows) every hour and only the
        ~6 new rows will actually land. SQLite handles dedup via the PK on
        `time` combined with INSERT OR IGNORE.

        Args:
            rows: List of dicts from /fees or /feesRecent, each with keys
                  'time' (int or float), 'total_fees' (numeric),
                  'total_spot_fees' (numeric).

        Returns:
            Number of rows actually inserted (i.e. new rows, excluding dupes).
            Returns 0 on empty input; does not raise on malformed rows -
            skips them with a warning instead, to keep an outage on one bad
            row from blocking legitimate rows in the same batch.
        """
        if not rows:
            return 0

        inserted_at = datetime.now(timezone.utc).isoformat()
        rows_before = self._count_platform_fees_fast()
        skipped = 0

        for row in rows:
            try:
                t = int(row['time'])
                total = int(row['total_fees'])
                spot = int(row['total_spot_fees'])
            except (KeyError, TypeError, ValueError) as e:
                skipped += 1
                logger.warning(f"Skipping malformed platform_fees row: {row!r} ({e})")
                continue

            self.cursor.execute(
                "INSERT OR IGNORE INTO platform_fees "
                "(time, total_fees, total_spot_fees, inserted_at) "
                "VALUES (?, ?, ?, ?)",
                (t, total, spot, inserted_at),
            )

        self.commit()

        rows_after = self._count_platform_fees_fast()
        inserted = rows_after - rows_before

        if skipped:
            logger.warning(
                f"platform_fees batch: {inserted} new, "
                f"{len(rows) - skipped - inserted} dupes, {skipped} malformed"
            )
        else:
            logger.info(
                f"platform_fees batch: {inserted} new, "
                f"{len(rows) - inserted} dupes (of {len(rows)} candidates)"
            )

        return inserted

    # =====================================================================
    # platform_fees - reads
    # =====================================================================

    def get_platform_fees_count(self) -> int:
        """
        Total rows in platform_fees. Used on startup to decide whether the
        one-shot backfill via /fees needs to run.
        """
        return self._count_platform_fees_fast()

    def get_latest_platform_fees(self) -> Optional[Dict]:
        """
        Most recent platform_fees row, or None if the table is empty.
        Useful for sanity checks, dashboards, and verifying that the
        hourly poll is landing new data.

        Returns:
            Dict with keys 'time', 'total_fees', 'total_spot_fees',
            'inserted_at', or None if table is empty.
        """
        self.cursor.execute(
            "SELECT time, total_fees, total_spot_fees, inserted_at "
            "FROM platform_fees ORDER BY time DESC LIMIT 1"
        )
        row = self.cursor.fetchone()
        if row is None:
            return None
        return {
            'time': row['time'],
            'total_fees': row['total_fees'],
            'total_spot_fees': row['total_spot_fees'],
            'inserted_at': row['inserted_at'],
        }

    # =====================================================================
    # Internal
    # =====================================================================

    def _count_platform_fees_fast(self) -> int:
        """COUNT(*) on platform_fees. Trivial at this table's scale."""
        self.cursor.execute("SELECT COUNT(*) FROM platform_fees")
        return self.cursor.fetchone()[0]