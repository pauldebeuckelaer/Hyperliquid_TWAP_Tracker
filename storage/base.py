#!/usr/bin/env python3
"""
Base Storage Class
==================
Shared database connection and utilities for all storage modules.

All storage classes inherit from this to share:
- Database connection
- WAL mode for concurrent access
- Common utilities (close, context manager)
- Index creation helper
- WAL checkpoint management
"""
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path('data/twap.db')


class BaseStorage:
    """
    Base class for SQLite storage modules.

    Handles connection management and shared utilities.
    Subclasses implement their own tables and methods.
    """

    def __init__(self, db_path: Path = None, create_tables: bool = True):
        """
        Initialize database connection.

        Args:
            db_path: Path to database file (default: data/twap.db)
            create_tables: Whether to create tables on init (default: True)
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create connection
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access
        self.cursor = self.conn.cursor()

        # Enable WAL mode for better concurrent performance
        self.cursor.execute("PRAGMA journal_mode=WAL")

        # Checkpoint tracking
        self._write_count = 0
        self._last_checkpoint = datetime.now()
        self._checkpoint_interval_seconds = 3600  # 1 hour
        self._checkpoint_write_threshold = 10000  # every 10k writes

        # Subclasses create their own tables
        if create_tables:
            self._create_tables()
            self._create_indexes()

        logger.debug(f"{self.__class__.__name__} initialized: {self.db_path}")

    def _create_tables(self):
        """
        Create database tables. Override in subclasses.
        """
        pass

    def _create_indexes(self):
        """
        Create database indexes. Override in subclasses.
        """
        pass

    def _execute_index_list(self, indexes: List[str]):
        """
        Helper to execute a list of CREATE INDEX statements.

        Args:
            indexes: List of SQL CREATE INDEX statements
        """
        for idx_sql in indexes:
            try:
                self.cursor.execute(idx_sql)
            except sqlite3.Error as e:
                logger.warning(f"Index creation warning: {e}")
        self.conn.commit()

    def commit(self):
        """Commit current transaction."""
        self.conn.commit()
        self.maybe_checkpoint()

    def rollback(self):
        """Rollback current transaction."""
        self.conn.rollback()

    def checkpoint(self, mode: str = 'PASSIVE') -> dict:
        """
        Force a WAL checkpoint.

        Args:
            mode: PASSIVE (non-blocking), FULL (waits), or TRUNCATE (reclaims space)

        Returns:
            Dict with busy, log, checkpointed page counts
        """
        try:
            self.cursor.execute(f"PRAGMA wal_checkpoint({mode})")
            result = self.cursor.fetchone()

            stats = {
                'busy': result[0],       # 0 = success, 1 = blocked
                'log': result[1],        # total pages in WAL
                'checkpointed': result[2] # pages written to main DB
            }

            logger.info(f"WAL checkpoint ({mode}): {stats['checkpointed']}/{stats['log']} pages, busy={stats['busy']}")
            self._last_checkpoint = datetime.now()
            self._write_count = 0

            return stats
        except Exception as e:
            logger.error(f"Checkpoint failed: {e}")
            return {'error': str(e)}

    def maybe_checkpoint(self):
        """
        Check if checkpoint is needed and do it if so.
        Call this after writes.
        """
        self._write_count += 1

        elapsed = (datetime.now() - self._last_checkpoint).total_seconds()

        if elapsed > self._checkpoint_interval_seconds or self._write_count > self._checkpoint_write_threshold:
            logger.info(f"Auto-checkpoint triggered (elapsed={elapsed:.0f}s, writes={self._write_count})")
            return self.checkpoint('PASSIVE')

        return None

    def get_db_size_mb(self) -> float:
        """Get database file size in MB."""
        if self.db_path.exists():
            return round(self.db_path.stat().st_size / (1024 * 1024), 2)
        return 0.0

    def get_wal_size_mb(self) -> float:
        """Get WAL file size in MB."""
        wal_path = Path(str(self.db_path) + '-wal')
        if wal_path.exists():
            return round(wal_path.stat().st_size / (1024 * 1024), 2)
        return 0.0

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.debug(f"{self.__class__.__name__} connection closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.close()