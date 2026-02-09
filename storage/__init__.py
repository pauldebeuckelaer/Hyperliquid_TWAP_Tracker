"""
CHANGES TO storage/__init__.py
==============================

1. Add import for MarketCandleStorage
2. Add MarketCandleStorage to SQLiteBackend inheritance
3. Update cleanup_old_data() to aggregate before purging
4. Remove liquidation_snapshots and whale_events from purge

Below is the complete updated file.
"""

#!/usr/bin/env python3
"""
Storage Module
==============
Data persistence backends for TWAP tracking and whale monitoring.

Classes:
- TwapStorage: TWAP orders, snapshots, events, addresses
- WhaleStorage: Whale addresses, portfolio/perp/spot/vault snapshots
- MarketStorage: Per-coin market data (prices, funding, OI, volume)
- MarketCandleStorage: Aggregated 10-min candles for backtesting
- LiquidationStorage: Whale liquidation exposure snapshots
- WhaleEventStorage: Whale position/account change events
- SQLiteBackend: Combined class for backward compatibility

Usage:
    # New way (recommended) - use specific storage classes:
    from storage import TwapStorage, WhaleStorage, MarketStorage, MarketCandleStorage, LiquidationStorage, WhaleEventStorage

    twap_db = TwapStorage()
    whale_db = WhaleStorage()
    market_db = MarketStorage()
    candle_db = MarketCandleStorage()
    liq_db = LiquidationStorage()
    event_db = WhaleEventStorage()

    # Old way (backward compatible) - combined class:
    from storage import SQLiteBackend
    db = SQLiteBackend()  # Has all methods from all storage classes

    # Also works:
    from storage.sqlite_backend import SQLiteBackend
"""

from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
import logging


from .base import BaseStorage, DEFAULT_DB_PATH
from .twap_storage import TwapStorage
from .whale_storage import WhaleStorage
from .market_storage import MarketStorage
from .market_candle_storage import MarketCandleStorage
from .liquidation_storage import LiquidationStorage
from .whale_event_storage import WhaleEventStorage


class SQLiteBackend(TwapStorage, WhaleStorage, MarketStorage, MarketCandleStorage, LiquidationStorage, WhaleEventStorage):
    """
    Combined storage backend for backward compatibility.

    Inherits from all storage classes, providing access to all tables
    and methods through a single connection.

    This is the same interface as the old sqlite_backend.py, so existing
    code continues to work without changes.
    """

    def __init__(self, db_path: Path = None):
        """
        Initialize combined storage backend.

        Args:
            db_path: Path to database file (default: data/twap.db)
        """
        # Initialize base with connection but don't create tables yet
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        import sqlite3
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

        # Enable WAL mode
        self.cursor.execute("PRAGMA journal_mode=WAL")
        # Checkpoint tracking
        self._write_count = 0
        self._last_checkpoint = datetime.now()
        self._checkpoint_interval_seconds = 300
        self._checkpoint_write_threshold = 10000

        # Create all tables from all parent classes
        self._create_all_tables()
        self._create_all_indexes()

        logger = logging.getLogger(__name__)
        logger.info(f"SQLiteBackend initialized: {self.db_path}")

    def _create_all_tables(self):
        """Create tables from all storage classes."""
        TwapStorage._create_tables(self)
        WhaleStorage._create_tables(self)
        MarketStorage._create_tables(self)
        MarketCandleStorage._create_tables(self)
        LiquidationStorage._create_tables(self)
        WhaleEventStorage._create_tables(self)

    def _create_all_indexes(self):
        """Create indexes from all storage classes."""
        TwapStorage._create_indexes(self)
        WhaleStorage._create_indexes(self)
        MarketStorage._create_indexes(self)
        MarketCandleStorage._create_indexes(self)
        LiquidationStorage._create_indexes(self)
        WhaleEventStorage._create_indexes(self)

    def cleanup_old_data(self, days_to_keep: int = 3, batch_size: int = 5000) -> Dict[str, int]:
        """
        Aggregate and clean up old market data.

        Strategy:
        - market_snapshots: Aggregate into 10-min candles, then purge (3 days)
        - All other tables: KEEP FOREVER for backtesting

        Args:
            days_to_keep: Days of raw market_snapshots to retain (default: 3)
            batch_size: Rows to delete per batch (default: 5000)

        Returns:
            Dict with aggregation and deletion counts
        """
        import time
        logger = logging.getLogger(__name__)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()

        result = {}

        # =================================================================
        # STEP 1: Aggregate market_snapshots into 10-min candles
        # =================================================================
        try:
            candles_created = self.aggregate_market_snapshots(before_time=cutoff)
            if candles_created > 0:
                result['candles_created'] = candles_created
                logger.info(f"Created {candles_created} candles from market_snapshots")
        except Exception as e:
            logger.error(f"Aggregation failed: {e} — skipping purge to avoid data loss")
            return result

        # =================================================================
        # STEP 2: Purge raw market_snapshots (only table we purge)
        # =================================================================
        deleted = self._batched_delete(
            'market_snapshots', 'snapshot_time', cutoff, batch_size
        )
        if deleted > 0:
            result['market_snapshots_deleted'] = deleted

        total_deleted = sum(v for k, v in result.items() if 'deleted' in k)

        if total_deleted > 0:
            logger.info(f"Cleanup complete: {result}")
            # Only VACUUM if we deleted a lot
            if total_deleted > 50000:
                logger.info("Running VACUUM to reclaim space...")
                self.cursor.execute("VACUUM")
                logger.info("VACUUM complete")
        else:
            logger.info("Cleanup complete: no old data to remove")

        return result

    def _batched_delete(self, table: str, time_column: str, cutoff: str, batch_size: int) -> int:
        """
        Delete rows in batches to avoid long locks.

        Args:
            table: Table name
            time_column: Column to filter on (snapshot_time or timestamp)
            cutoff: ISO timestamp cutoff
            batch_size: Rows per batch

        Returns:
            Total rows deleted
        """
        import time
        logger = logging.getLogger(__name__)

        total_deleted = 0

        while True:
            # Delete a batch using ROWID for efficiency
            self.cursor.execute(f"""
                DELETE FROM {table} 
                WHERE rowid IN (
                    SELECT rowid FROM {table} 
                    WHERE {time_column} < ? 
                    LIMIT ?
                )
            """, (cutoff, batch_size))

            rows_affected = self.cursor.rowcount
            self.conn.commit()

            if rows_affected == 0:
                break

            total_deleted += rows_affected
            logger.debug(f"Deleted batch: {rows_affected} from {table} (total: {total_deleted})")

            # Small sleep to let main loop get writes through
            time.sleep(0.1)

        if total_deleted > 0:
            logger.info(f"Deleted {total_deleted} rows from {table}")

        return total_deleted

    def get_stats(self) -> Dict:
        """Get combined statistics from all storage modules."""
        stats = {}

        # TWAP stats
        self.cursor.execute("SELECT COUNT(*) FROM orders")
        stats['total_orders'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'running'")
        stats['active_orders'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM snapshots")
        stats['total_twap_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM events")
        stats['total_events'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM addresses")
        stats['total_twap_addresses'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT symbol) FROM snapshots")
        stats['unique_symbols'] = self.cursor.fetchone()[0]

        # Whale stats
        self.cursor.execute("SELECT COUNT(*) FROM whale_addresses")
        stats['total_whales'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM whale_addresses WHERE is_active = 1")
        stats['active_whales'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM portfolio_snapshots")
        stats['total_portfolio_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM perp_snapshots")
        stats['total_perp_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM spot_snapshots")
        stats['total_spot_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM vault_snapshots")
        stats['total_vault_snapshots'] = self.cursor.fetchone()[0]

        # Market stats
        self.cursor.execute("SELECT COUNT(*) FROM market_snapshots")
        stats['total_market_snapshots'] = self.cursor.fetchone()[0]

        # Candle stats
        self.cursor.execute("SELECT COUNT(*) FROM market_candles")
        stats['total_market_candles'] = self.cursor.fetchone()[0]

        # Liquidation stats
        self.cursor.execute("SELECT COUNT(*) FROM liquidation_snapshots")
        stats['total_liquidation_snapshots'] = self.cursor.fetchone()[0]

        # Whale event stats
        self.cursor.execute("SELECT COUNT(*) FROM whale_events")
        stats['total_whale_events'] = self.cursor.fetchone()[0]

        # Database file size
        stats['db_size_mb'] = self.get_db_size_mb()

        return stats


__all__ = [
    'BaseStorage',
    'TwapStorage',
    'WhaleStorage',
    'MarketStorage',
    'MarketCandleStorage',
    'LiquidationStorage',
    'WhaleEventStorage',
    'SQLiteBackend',
    'DEFAULT_DB_PATH',
]