#!/usr/bin/env python3
"""
Storage Module
==============
Data persistence backends for TWAP tracking and whale monitoring.

Classes:
- TwapStorage: TWAP orders, snapshots, events, addresses
- WhaleStorage: Whale addresses, portfolio/perp/spot/vault snapshots
- MarketStorage: Per-coin market data (prices, funding, OI, volume)
- LiquidationStorage: Whale liquidation exposure snapshots
- WhaleEventStorage: Whale position/account change events
- SQLiteBackend: Combined class for backward compatibility

Usage:
    # New way (recommended) - use specific storage classes:
    from storage import TwapStorage, WhaleStorage, MarketStorage, LiquidationStorage, WhaleEventStorage

    twap_db = TwapStorage()
    whale_db = WhaleStorage()
    market_db = MarketStorage()
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
from .liquidation_storage import LiquidationStorage
from .whale_event_storage import WhaleEventStorage


class SQLiteBackend(TwapStorage, WhaleStorage, MarketStorage, LiquidationStorage, WhaleEventStorage):
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
        LiquidationStorage._create_tables(self)
        WhaleEventStorage._create_tables(self)

    def _create_all_indexes(self):
        """Create indexes from all storage classes."""
        TwapStorage._create_indexes(self)
        WhaleStorage._create_indexes(self)
        MarketStorage._create_indexes(self)
        LiquidationStorage._create_indexes(self)
        WhaleEventStorage._create_indexes(self)

    def cleanup_old_data(self, days_to_keep: int = 7, batch_size: int = 5000) -> Dict[str, int]:
        """
        Delete data older than specified days to manage database size.
        Uses batched deletes to avoid blocking the main loop.

        Args:
            days_to_keep: Number of days of data to retain (default: 7)
            batch_size: Rows to delete per batch (default: 5000)

        Returns:
            Dict with count of deleted rows per table
        """
        import time
        logger = logging.getLogger(__name__)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()

        deleted = {}

        # Tables with 'snapshot_time' column
        snapshot_time_tables = ['market_snapshots', 'liquidation_snapshots']
        for table in snapshot_time_tables:
            table_deleted = self._batched_delete(
                table, 'snapshot_time', cutoff, batch_size
            )
            if table_deleted > 0:
                deleted[table] = table_deleted

        # Tables with 'timestamp' column
        timestamp_tables = ['whale_events']
        for table in timestamp_tables:
            table_deleted = self._batched_delete(
                table, 'timestamp', cutoff, batch_size
            )
            if table_deleted > 0:
                deleted[table] = table_deleted

        total = sum(deleted.values())

        if total > 0:
            logger.info(f"Cleanup complete: {total} total rows deleted")
            # Only VACUUM if we deleted a lot (it's expensive)
            if total > 50000:
                logger.info("Running VACUUM to reclaim space...")
                self.cursor.execute("VACUUM")
                logger.info("VACUUM complete")
        else:
            logger.info("Cleanup complete: no old data to remove")

        return deleted

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
    'LiquidationStorage',
    'WhaleEventStorage',
    'SQLiteBackend',
    'DEFAULT_DB_PATH',
]