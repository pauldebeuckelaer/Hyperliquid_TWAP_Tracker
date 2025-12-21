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
"""
import sqlite3
import logging
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

    def rollback(self):
        """Rollback current transaction."""
        self.conn.rollback()

    def get_db_size_mb(self) -> float:
        """Get database file size in MB."""
        if self.db_path.exists():
            return round(self.db_path.stat().st_size / (1024 * 1024), 2)
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