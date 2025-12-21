#!/usr/bin/env python3
"""
Whale Event Storage
===================
Storage for whale position and account change events.

Tables:
- whale_events: Position opens/closes/liquidations, size changes, leverage changes, margin changes
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from .base import BaseStorage

logger = logging.getLogger(__name__)


class WhaleEventStorage(BaseStorage):
    """Storage for whale position and account events."""

    def _create_tables(self):
        """Create whale event tables."""

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS whale_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                address TEXT NOT NULL,
                event_type TEXT NOT NULL,
                coin TEXT,
                side TEXT,
                old_value REAL,
                new_value REAL,
                old_size REAL,
                new_size REAL,
                size REAL,
                leverage REAL,
                entry_price REAL,
                current_price REAL,
                position_value REAL,
                account_value REAL,
                withdrawable REAL
            )
        """)

        self.conn.commit()

    def _create_indexes(self):
        """Create whale event indexes."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_whale_events_time ON whale_events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_whale_events_address ON whale_events(address)",
            "CREATE INDEX IF NOT EXISTS idx_whale_events_type ON whale_events(event_type)",
            "CREATE INDEX IF NOT EXISTS idx_whale_events_coin ON whale_events(coin)",
            "CREATE INDEX IF NOT EXISTS idx_whale_events_address_time ON whale_events(address, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_whale_events_coin_time ON whale_events(coin, timestamp)",
        ]
        self._execute_index_list(indexes)

    # =========================================================================
    # SAVE METHODS
    # =========================================================================

    def save_whale_events(self, events: List[Dict]):
        """
        Save whale events from WhaleEventDetector.

        Args:
            events: List of event dicts from WhaleEventDetector.detect()
        """
        if not events:
            return

        try:
            for event in events:
                timestamp = event.get('timestamp')
                if hasattr(timestamp, 'isoformat'):
                    timestamp = timestamp.isoformat()

                self.cursor.execute("""
                    INSERT INTO whale_events (
                        timestamp, address, event_type, coin, side,
                        old_value, new_value, old_size, new_size, size,
                        leverage, entry_price, current_price, position_value,
                        account_value, withdrawable
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    event.get('address', ''),
                    event.get('event_type', ''),
                    event.get('coin'),
                    event.get('side'),
                    event.get('old_value'),
                    event.get('new_value'),
                    event.get('old_size'),
                    event.get('new_size'),
                    event.get('size'),
                    event.get('leverage'),
                    event.get('entry_price'),
                    event.get('current_price'),
                    event.get('position_value'),
                    event.get('account_value'),
                    event.get('withdrawable'),
                ))

            self.conn.commit()
            logger.debug(f"Saved {len(events)} whale events")

        except Exception as e:
            logger.error(f"Error saving whale events: {e}")
            self.conn.rollback()
            raise

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    def get_recent_events(
            self,
            event_type: str = None,
            coin: str = None,
            address: str = None,
            limit: int = 100
    ) -> List[Dict]:
        """
        Get recent whale events with optional filters.

        Args:
            event_type: Filter by event type (e.g., 'position_liquidated')
            coin: Filter by coin
            address: Filter by address
            limit: Max results

        Returns:
            List of event dicts, newest first
        """
        query = "SELECT * FROM whale_events WHERE 1=1"
        params = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        if coin:
            query += " AND coin = ?"
            params.append(coin)

        if address:
            query += " AND address = ?"
            params.append(address)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_liquidations(self, start_time: str = None, limit: int = 100) -> List[Dict]:
        """
        Get liquidation events.

        Args:
            start_time: Optional start timestamp
            limit: Max results

        Returns:
            List of liquidation events
        """
        query = "SELECT * FROM whale_events WHERE event_type = 'position_liquidated'"
        params = []

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_events_by_coin(self, coin: str, limit: int = 100) -> List[Dict]:
        """Get all events for a specific coin."""
        self.cursor.execute("""
            SELECT * FROM whale_events
            WHERE coin = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (coin, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_events_by_address(self, address: str, limit: int = 100) -> List[Dict]:
        """Get all events for a specific address."""
        self.cursor.execute("""
            SELECT * FROM whale_events
            WHERE address = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (address, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_event_summary(self, start_time: str = None) -> Dict:
        """
        Get summary of events by type.

        Args:
            start_time: Optional start timestamp

        Returns:
            Dict with event counts by type
        """
        query = "SELECT event_type, COUNT(*) as count FROM whale_events"
        params = []

        if start_time:
            query += " WHERE timestamp >= ?"
            params.append(start_time)

        query += " GROUP BY event_type ORDER BY count DESC"

        self.cursor.execute(query, params)
        return {row['event_type']: row['count'] for row in self.cursor.fetchall()}

    # =========================================================================
    # CLEANUP
    # =========================================================================

    def cleanup_old_events(self, days_to_keep: int = 30) -> int:
        """
        Remove events older than specified days.

        Args:
            days_to_keep: Number of days of history to keep

        Returns:
            Number of events deleted
        """
        cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()

        self.cursor.execute("SELECT COUNT(*) FROM whale_events WHERE timestamp < ?", (cutoff,))
        count = self.cursor.fetchone()[0]

        if count > 0:
            self.cursor.execute("DELETE FROM whale_events WHERE timestamp < ?", (cutoff,))
            self.conn.commit()
            logger.info(f"Cleaned up {count} old whale events (older than {days_to_keep} days)")

        return count

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get whale event storage statistics."""
        stats = {}

        self.cursor.execute("SELECT COUNT(*) FROM whale_events")
        stats['total_events'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT address) FROM whale_events")
        stats['unique_addresses'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT coin) FROM whale_events")
        stats['unique_coins'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM whale_events")
        row = self.cursor.fetchone()
        stats['earliest_event'] = row[0]
        stats['latest_event'] = row[1]

        # Liquidation count
        self.cursor.execute("SELECT COUNT(*) FROM whale_events WHERE event_type = 'position_liquidated'")
        stats['total_liquidations'] = self.cursor.fetchone()[0]

        stats['db_size_mb'] = self.get_db_size_mb()

        return stats