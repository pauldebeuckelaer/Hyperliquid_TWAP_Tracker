#!/usr/bin/env python3
"""
Hypurrscan Fees Collector
=========================
Polls the Hypurrscan /fees and /feesRecent endpoints and persists
platform-wide cumulative fees into twap.db (platform_fees table).

Design:
- Endpoints are "dumb": /fees returns full history every call (~481 rows),
  /feesRecent returns last ~8 days at ~10-min cadence (~998 rows).
  Query params are ignored server-side.
- Storage is "smart": platform_fees.time is a PRIMARY KEY, and inserts
  use INSERT OR IGNORE. We can throw the full payload at the table
  every poll and SQLite silently drops the ~992 duplicates.

Two operations:
- backfill(): one-shot on first run (when table is empty). Pulls /fees
  for the full multi-month history at daily-ish resolution.
- poll(): hourly while running. Pulls /feesRecent (the higher-resolution
  feed) and writes the ~6 rows that are new since last hour.

Cadence:
- Controlled by poll_interval_cycles in config (default: 60 cycles = 1hr).
- The TierManager cycle counter is the clock; this collector doesn't
  maintain its own timing state. should_poll(cycle) is the gate.

Safety:
- Disabled by default via config.collection_enabled. Dark on first deploy.
- All network errors are caught and logged - a failed poll never crashes
  the main loop. /feesRecent's 8-day window gives us 192 retry opportunities
  before data falls out of reach.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Defaults - overridable via config
DEFAULT_POLL_INTERVAL_CYCLES = 60  # 60 cycles of 60s = 1 hour


class HypurrscanFeesCollector:
    """
    Polls Hypurrscan platform fee endpoints and persists to platform_fees table.
    """

    def __init__(self, hypurr_client, storage, config: dict = None):
        """
        Initialize fees collector.

        Args:
            hypurr_client: HypurrscanClient instance (already configured)
            storage: SQLiteBackend instance (provides HypurrscanStorage methods)
            config: Optional dict with keys:
                - collection_enabled (bool): master on/off switch (default False)
                - poll_interval_cycles (int): cycles between polls (default 60)
        """
        self.hypurr_client = hypurr_client
        self.storage = storage
        self.config = config or {}

        self.enabled = self.config.get('collection_enabled', False)
        self.poll_interval_cycles = self.config.get(
            'poll_interval_cycles', DEFAULT_POLL_INTERVAL_CYCLES
        )

        # Stats
        self.poll_count = 0
        self.rows_inserted_total = 0
        self.last_error: Optional[str] = None

        logger.info(
            f"HypurrscanFeesCollector initialized "
            f"(enabled={self.enabled}, interval={self.poll_interval_cycles} cycles)"
        )

    # =========================================================================
    # Backfill - one-time population from /fees
    # =========================================================================

    def needs_backfill(self) -> bool:
        """True if platform_fees table is empty and collection is enabled."""
        if not self.enabled:
            return False
        return self.storage.get_platform_fees_count() == 0

    def backfill(self) -> int:
        """
        One-time backfill from /fees (full history).

        Idempotent: calling on an already-populated table is safe -
        INSERT OR IGNORE will no-op on every row. We still gate on
        needs_backfill() to avoid the redundant HTTP call.

        Returns:
            Rows actually inserted. 0 if already populated, disabled,
            or API call failed.
        """
        if not self.enabled:
            logger.info("Fees collection disabled - skipping backfill")
            return 0

        if not self.needs_backfill():
            existing = self.storage.get_platform_fees_count()
            logger.info(f"platform_fees already has {existing} rows - skipping backfill")
            return 0

        logger.info("Starting one-time backfill of platform_fees from /fees...")

        rows = self.hypurr_client.get_platform_fees()
        if rows is None:
            logger.warning("Backfill failed: /fees returned None")
            self.last_error = "backfill: /fees returned None"
            return 0

        if not rows:
            logger.warning("Backfill got empty response from /fees")
            return 0

        inserted = self.storage.insert_platform_fees_batch(rows)
        self.rows_inserted_total += inserted

        logger.info(
            f"Backfill complete: {inserted} rows inserted "
            f"(from {len(rows)} candidates)"
        )
        return inserted

    # =========================================================================
    # Hourly poll - ongoing collection from /feesRecent
    # =========================================================================

    def should_poll(self, cycle: int) -> bool:
        """
        Decide whether this cycle should trigger a fees poll.

        Args:
            cycle: Current cycle number (1-60) from TierManager

        Returns:
            True if this cycle is a poll trigger (i.e. cycle % interval == 0).
            False if collection is disabled or cycle doesn't match.
        """
        if not self.enabled:
            return False
        if cycle <= 0:
            return False
        # Trigger at cycle boundaries. With default interval=60, fires at cycle 60.
        return (cycle % self.poll_interval_cycles) == 0

    def poll(self) -> int:
        """
        Pull /feesRecent and write any new rows to platform_fees.

        Called by the main loop when should_poll(cycle) returns True.
        All errors are caught and logged; this method never raises, so
        a Hypurrscan outage cannot crash the main tracking loop.

        Returns:
            Rows actually inserted (typically ~6 per hour, 0 on failure).
        """
        if not self.enabled:
            return 0

        try:
            rows = self.hypurr_client.get_platform_fees_recent()
            if rows is None:
                logger.warning("Fees poll failed: /feesRecent returned None")
                self.last_error = "poll: /feesRecent returned None"
                return 0

            if not rows:
                logger.warning("Fees poll got empty response from /feesRecent")
                return 0

            inserted = self.storage.insert_platform_fees_batch(rows)
            self.poll_count += 1
            self.rows_inserted_total += inserted

            # Single-line status log so hourly polls are easy to grep
            latest = self.storage.get_latest_platform_fees()
            latest_time = latest['time'] if latest else 'n/a'
            logger.info(
                f"Fees poll #{self.poll_count}: +{inserted} rows "
                f"(latest time={latest_time})"
            )
            return inserted

        except Exception as e:
            # Defensive: any unexpected error gets logged, not raised
            self.last_error = f"poll: {type(e).__name__}: {e}"
            logger.error(f"Fees poll raised unexpected exception: {e}", exc_info=True)
            return 0

    # =========================================================================
    # Introspection
    # =========================================================================

    def get_stats(self) -> dict:
        """Return collector stats for logging/monitoring."""
        return {
            'enabled': self.enabled,
            'poll_interval_cycles': self.poll_interval_cycles,
            'poll_count': self.poll_count,
            'rows_inserted_total': self.rows_inserted_total,
            'last_error': self.last_error,
            'table_row_count': self.storage.get_platform_fees_count(),
        }