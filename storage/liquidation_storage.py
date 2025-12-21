#!/usr/bin/env python3
"""
Liquidation Storage
===================
Storage for whale liquidation exposure snapshots.

Tables:
- liquidation_snapshots: Per-position liquidation risk data at each snapshot
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .base import BaseStorage

logger = logging.getLogger(__name__)


class LiquidationStorage(BaseStorage):
    """Storage for whale liquidation exposure data."""

    def _create_tables(self):
        """Create liquidation tracking tables."""

        # Liquidation snapshots - per-position risk data
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS liquidation_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_time TEXT NOT NULL,
                address TEXT NOT NULL,
                coin TEXT NOT NULL,
                side TEXT NOT NULL,

                -- Position data
                size REAL,
                position_value REAL,
                entry_price REAL,
                mark_price REAL,
                liq_price REAL,
                leverage REAL,
                margin_used REAL,

                -- Risk metrics
                distance_to_liq REAL,
                pnl_pct REAL,
                unrealized_pnl REAL,

                -- Funding
                funding_since_open REAL,

                -- Account context
                account_value REAL,
                account_margin_used REAL,
                account_withdrawable REAL,
                spot_usdc REAL,

                UNIQUE(snapshot_time, address, coin)
            )
        """)

        self.conn.commit()

    def _create_indexes(self):
        """Create liquidation tracking indexes."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_liq_time ON liquidation_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_liq_address ON liquidation_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_liq_coin ON liquidation_snapshots(coin)",
            "CREATE INDEX IF NOT EXISTS idx_liq_distance ON liquidation_snapshots(distance_to_liq)",
            "CREATE INDEX IF NOT EXISTS idx_liq_coin_time ON liquidation_snapshots(coin, snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_liq_address_time ON liquidation_snapshots(address, snapshot_time)",
        ]
        self._execute_index_list(indexes)

    # =========================================================================
    # SAVE METHODS
    # =========================================================================

    def save_liquidation_snapshot(self, snapshot_time: str, coin_exposure: Dict):
        """
        Save liquidation exposure data for all positions.

        Args:
            snapshot_time: ISO timestamp
            coin_exposure: Dict from _parse_liquidation_exposure()
                           Keys are coin symbols, values have 'positions' list
        """
        try:
            saved_count = 0
            for coin, data in coin_exposure.items():
                positions = data.get('positions', [])

                for pos in positions:
                    self.cursor.execute("""
                        INSERT OR REPLACE INTO liquidation_snapshots (
                            snapshot_time, address, coin, side,
                            size, position_value, entry_price, mark_price, liq_price,
                            leverage, margin_used,
                            distance_to_liq, pnl_pct, unrealized_pnl,
                            funding_since_open,
                            account_value, account_margin_used, account_withdrawable, spot_usdc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        snapshot_time,
                        pos.get('address', ''),
                        coin,
                        pos.get('side', ''),
                        pos.get('size', 0),
                        pos.get('value', 0),
                        pos.get('entry_price', 0),
                        pos.get('current_price', 0),
                        pos.get('liq_price', 0),
                        pos.get('leverage', 0),
                        pos.get('margin_used', 0),
                        pos.get('distance_to_liq', 100),
                        pos.get('pnl_pct', 0),
                        pos.get('unrealized_pnl', 0),
                        pos.get('funding_since_open', 0),
                        pos.get('account_value', 0),
                        pos.get('account_margin_used', 0),
                        pos.get('account_withdrawable', 0),
                        pos.get('spot_usdc', 0),
                    ))
                    saved_count += 1

            self.conn.commit()
            logger.debug(f"Saved liquidation snapshot for {saved_count} positions at {snapshot_time}")

        except Exception as e:
            logger.error(f"Error saving liquidation snapshot: {e}")
            self.conn.rollback()
            raise

    # =========================================================================
    # QUERY METHODS - BY RISK
    # =========================================================================

    def get_danger_positions(
            self,
            max_distance: float = 5.0,
            snapshot_time: str = None,
            limit: int = 100
    ) -> List[Dict]:
        """
        Get positions within X% of liquidation.

        Args:
            max_distance: Maximum distance to liquidation (default 5%)
            snapshot_time: Optional specific time, defaults to latest
            limit: Max results

        Returns:
            List of position dicts sorted by distance (closest first)
        """
        if not snapshot_time:
            self.cursor.execute("SELECT MAX(snapshot_time) FROM liquidation_snapshots")
            row = self.cursor.fetchone()
            snapshot_time = row[0] if row and row[0] else None

        if not snapshot_time:
            return []

        self.cursor.execute("""
            SELECT * FROM liquidation_snapshots
            WHERE snapshot_time = ? AND distance_to_liq <= ?
            ORDER BY distance_to_liq ASC
            LIMIT ?
        """, (snapshot_time, max_distance, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_positions_by_coin(
            self,
            coin: str,
            snapshot_time: str = None,
            limit: int = 100
    ) -> List[Dict]:
        """
        Get all positions for a coin at a snapshot time.

        Args:
            coin: Coin symbol
            snapshot_time: Optional specific time, defaults to latest
            limit: Max results

        Returns:
            List of position dicts sorted by distance
        """
        if not snapshot_time:
            self.cursor.execute("""
                SELECT MAX(snapshot_time) FROM liquidation_snapshots WHERE coin = ?
            """, (coin,))
            row = self.cursor.fetchone()
            snapshot_time = row[0] if row and row[0] else None

        if not snapshot_time:
            return []

        self.cursor.execute("""
            SELECT * FROM liquidation_snapshots
            WHERE snapshot_time = ? AND coin = ?
            ORDER BY distance_to_liq ASC
            LIMIT ?
        """, (snapshot_time, coin, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_positions_by_address(
            self,
            address: str,
            snapshot_time: str = None
    ) -> List[Dict]:
        """
        Get all positions for an address at a snapshot time.

        Args:
            address: Wallet address
            snapshot_time: Optional specific time, defaults to latest

        Returns:
            List of position dicts
        """
        if not snapshot_time:
            self.cursor.execute("""
                SELECT MAX(snapshot_time) FROM liquidation_snapshots WHERE address = ?
            """, (address,))
            row = self.cursor.fetchone()
            snapshot_time = row[0] if row and row[0] else None

        if not snapshot_time:
            return []

        self.cursor.execute("""
            SELECT * FROM liquidation_snapshots
            WHERE snapshot_time = ? AND address = ?
            ORDER BY position_value DESC
        """, (snapshot_time, address))
        return [dict(row) for row in self.cursor.fetchall()]

    # =========================================================================
    # QUERY METHODS - HISTORY
    # =========================================================================

    def get_position_history(
            self,
            address: str,
            coin: str,
            start_time: str = None,
            end_time: str = None,
            limit: int = 1440
    ) -> List[Dict]:
        """
        Get history of a specific position.

        Args:
            address: Wallet address
            coin: Coin symbol
            start_time: Optional start timestamp
            end_time: Optional end timestamp
            limit: Max results

        Returns:
            List of position snapshots over time
        """
        query = "SELECT * FROM liquidation_snapshots WHERE address = ? AND coin = ?"
        params = [address, coin]

        if start_time:
            query += " AND snapshot_time >= ?"
            params.append(start_time)

        if end_time:
            query += " AND snapshot_time <= ?"
            params.append(end_time)

        query += " ORDER BY snapshot_time DESC LIMIT ?"
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_distance_history(
            self,
            address: str,
            coin: str,
            limit: int = 1440
    ) -> List[Dict]:
        """
        Get distance to liquidation history for a position.

        Args:
            address: Wallet address
            coin: Coin symbol
            limit: Max results

        Returns:
            List of dicts with snapshot_time and distance_to_liq
        """
        self.cursor.execute("""
            SELECT snapshot_time, distance_to_liq, mark_price, liq_price
            FROM liquidation_snapshots
            WHERE address = ? AND coin = ?
            ORDER BY snapshot_time DESC
            LIMIT ?
        """, (address, coin, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    # =========================================================================
    # QUERY METHODS - AGGREGATES
    # =========================================================================

    def get_coin_exposure_summary(self, snapshot_time: str = None) -> List[Dict]:
        """
        Get aggregated exposure by coin.

        Args:
            snapshot_time: Optional specific time, defaults to latest

        Returns:
            List of dicts with coin, total_value, long_value, short_value, num_positions
        """
        if not snapshot_time:
            self.cursor.execute("SELECT MAX(snapshot_time) FROM liquidation_snapshots")
            row = self.cursor.fetchone()
            snapshot_time = row[0] if row and row[0] else None

        if not snapshot_time:
            return []

        self.cursor.execute("""
            SELECT 
                coin,
                SUM(position_value) as total_value,
                SUM(CASE WHEN side = 'LONG' THEN position_value ELSE 0 END) as long_value,
                SUM(CASE WHEN side = 'SHORT' THEN position_value ELSE 0 END) as short_value,
                COUNT(*) as num_positions,
                MIN(distance_to_liq) as min_distance,
                AVG(distance_to_liq) as avg_distance
            FROM liquidation_snapshots
            WHERE snapshot_time = ?
            GROUP BY coin
            ORDER BY total_value DESC
        """, (snapshot_time,))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_address_exposure_summary(self, snapshot_time: str = None, limit: int = 50) -> List[Dict]:
        """
        Get aggregated exposure by address.

        Args:
            snapshot_time: Optional specific time, defaults to latest
            limit: Max addresses to return

        Returns:
            List of dicts with address, total_value, num_positions, min_distance
        """
        if not snapshot_time:
            self.cursor.execute("SELECT MAX(snapshot_time) FROM liquidation_snapshots")
            row = self.cursor.fetchone()
            snapshot_time = row[0] if row and row[0] else None

        if not snapshot_time:
            return []

        self.cursor.execute("""
            SELECT 
                address,
                SUM(position_value) as total_value,
                COUNT(*) as num_positions,
                MIN(distance_to_liq) as min_distance,
                MAX(account_value) as account_value
            FROM liquidation_snapshots
            WHERE snapshot_time = ?
            GROUP BY address
            ORDER BY total_value DESC
            LIMIT ?
        """, (snapshot_time, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_snapshot_times(self, limit: int = 100) -> List[str]:
        """Get list of unique snapshot times."""
        self.cursor.execute("""
            SELECT DISTINCT snapshot_time
            FROM liquidation_snapshots
            ORDER BY snapshot_time DESC
            LIMIT ?
        """, (limit,))
        return [row[0] for row in self.cursor.fetchall()]

    # =========================================================================
    # CLEANUP
    # =========================================================================

    def cleanup_old_snapshots(self, days_to_keep: int = 7) -> int:
        """
        Remove snapshots older than specified days.

        Args:
            days_to_keep: Number of days of history to keep (default 7)

        Returns:
            Number of snapshots deleted
        """
        cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()

        # Get count before deletion
        self.cursor.execute("""
            SELECT COUNT(*) FROM liquidation_snapshots
            WHERE snapshot_time < ?
        """, (cutoff,))
        count = self.cursor.fetchone()[0]

        if count > 0:
            self.cursor.execute("DELETE FROM liquidation_snapshots WHERE snapshot_time < ?", (cutoff,))
            self.conn.commit()
            logger.info(f"Cleaned up {count} old liquidation snapshots (older than {days_to_keep} days)")

        return count

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get liquidation storage statistics."""
        stats = {}

        self.cursor.execute("SELECT COUNT(*) FROM liquidation_snapshots")
        stats['total_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT address) FROM liquidation_snapshots")
        stats['unique_addresses'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT coin) FROM liquidation_snapshots")
        stats['unique_coins'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT snapshot_time) FROM liquidation_snapshots")
        stats['unique_times'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT MIN(snapshot_time), MAX(snapshot_time) FROM liquidation_snapshots")
        row = self.cursor.fetchone()
        stats['earliest_snapshot'] = row[0]
        stats['latest_snapshot'] = row[1]

        # Current danger positions
        self.cursor.execute("""
            SELECT COUNT(*) FROM liquidation_snapshots
            WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM liquidation_snapshots)
            AND distance_to_liq <= 5
        """)
        stats['current_danger_positions'] = self.cursor.fetchone()[0]

        stats['db_size_mb'] = self.get_db_size_mb()

        return stats