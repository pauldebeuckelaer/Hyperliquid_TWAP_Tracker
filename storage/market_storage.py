#!/usr/bin/env python3
"""
Market Storage
==============
Storage for market data snapshots.

Tables:
- market_snapshots: Per-coin market data (prices, funding, OI, volume)
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .base import BaseStorage

logger = logging.getLogger(__name__)


class MarketStorage(BaseStorage):
    """Storage for market data snapshots."""

    def _create_tables(self):
        """Create market data tables."""

        # Market snapshots - per-coin market data each minute
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_time TEXT NOT NULL,
                coin TEXT NOT NULL,
                mark_px REAL,
                oracle_px REAL,
                prev_day_px REAL,
                funding_8h REAL,
                open_interest REAL,
                open_interest_usd REAL,
                day_ntl_vlm REAL,
                premium REAL,
                UNIQUE(snapshot_time, coin)
            )
        """)

        self.conn.commit()

    def _create_indexes(self):
        """Create market data indexes."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_market_time ON market_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_market_coin ON market_snapshots(coin)",
            "CREATE INDEX IF NOT EXISTS idx_market_coin_time ON market_snapshots(coin, snapshot_time)",
        ]
        self._execute_index_list(indexes)

    # =========================================================================
    # SAVE METHODS
    # =========================================================================

    def save_market_snapshot(self, snapshot_time: str, market_data: Dict):
        """
        Save market data for all coins at a snapshot time.

        Args:
            snapshot_time: ISO timestamp
            market_data: Dict from get_meta_and_asset_ctxs()['asset_ctxs']
                         Keys are coin symbols, values are dicts with market data
        """
        try:
            saved_count = 0
            for coin, data in market_data.items():
                # Skip delisted coins
                if data.get('is_delisted'):
                    continue

                mark_px = data.get('mark_px', 0)
                oi = data.get('open_interest', 0)

                self.cursor.execute("""
                    INSERT OR REPLACE INTO market_snapshots (
                        snapshot_time, coin, mark_px, oracle_px, prev_day_px,
                        funding_8h, open_interest, open_interest_usd,
                        day_ntl_vlm, premium
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot_time,
                    coin,
                    mark_px,
                    data.get('oracle_px', 0),
                    data.get('prev_day_px', 0),
                    data.get('funding', 0),
                    oi,
                    oi * mark_px if mark_px else 0,
                    data.get('day_ntl_vlm', 0),
                    data.get('premium'),
                ))
                saved_count += 1

            self.conn.commit()
            logger.debug(f"Saved market snapshot for {saved_count} coins at {snapshot_time}")

        except Exception as e:
            logger.error(f"Error saving market snapshot: {e}")
            self.conn.rollback()
            raise

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    def get_market_history(
        self,
        coin: str,
        start_time: str = None,
        end_time: str = None,
        limit: int = 1440  # 24 hours of 1-minute snapshots
    ) -> List[Dict]:
        """
        Get market data history for a coin.

        Args:
            coin: Coin symbol (e.g., 'BTC', 'ETH')
            start_time: Optional start timestamp
            end_time: Optional end timestamp
            limit: Max results (default 1440 = 24 hours)

        Returns:
            List of market snapshot dicts, newest first
        """
        query = "SELECT * FROM market_snapshots WHERE coin = ?"
        params = [coin]

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

    def get_latest_market_data(self, coin: str = None) -> Optional[Dict]:
        """
        Get the most recent market data.

        Args:
            coin: Optional coin symbol. If None, returns latest snapshot time's data for all coins.

        Returns:
            Dict with market data, or None if no data
        """
        if coin:
            self.cursor.execute("""
                SELECT * FROM market_snapshots
                WHERE coin = ?
                ORDER BY snapshot_time DESC
                LIMIT 1
            """, (coin,))
            row = self.cursor.fetchone()
            return dict(row) if row else None
        else:
            # Get latest snapshot time
            self.cursor.execute("""
                SELECT MAX(snapshot_time) FROM market_snapshots
            """)
            row = self.cursor.fetchone()
            if not row or not row[0]:
                return None

            snapshot_time = row[0]

            # Get all coins at that time
            self.cursor.execute("""
                SELECT * FROM market_snapshots
                WHERE snapshot_time = ?
                ORDER BY open_interest_usd DESC
            """, (snapshot_time,))

            return {
                'snapshot_time': snapshot_time,
                'coins': [dict(row) for row in self.cursor.fetchall()]
            }

    def get_funding_history(
        self,
        coin: str,
        start_time: str = None,
        end_time: str = None,
        limit: int = 1440
    ) -> List[Dict]:
        """
        Get funding rate history for a coin.

        Args:
            coin: Coin symbol
            start_time: Optional start timestamp
            end_time: Optional end timestamp
            limit: Max results

        Returns:
            List of dicts with snapshot_time and funding_8h
        """
        query = "SELECT snapshot_time, funding_8h FROM market_snapshots WHERE coin = ?"
        params = [coin]

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

    def get_oi_history(
        self,
        coin: str,
        start_time: str = None,
        end_time: str = None,
        limit: int = 1440
    ) -> List[Dict]:
        """
        Get open interest history for a coin.

        Args:
            coin: Coin symbol
            start_time: Optional start timestamp
            end_time: Optional end timestamp
            limit: Max results

        Returns:
            List of dicts with snapshot_time, open_interest, open_interest_usd
        """
        query = """
            SELECT snapshot_time, open_interest, open_interest_usd, mark_px 
            FROM market_snapshots WHERE coin = ?
        """
        params = [coin]

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

    def get_snapshot_times(self, limit: int = 100) -> List[str]:
        """Get list of unique snapshot times."""
        self.cursor.execute("""
            SELECT DISTINCT snapshot_time
            FROM market_snapshots
            ORDER BY snapshot_time DESC
            LIMIT ?
        """, (limit,))
        return [row[0] for row in self.cursor.fetchall()]

    def get_coins_at_time(self, snapshot_time: str) -> List[Dict]:
        """
        Get all coins' market data at a specific time.

        Args:
            snapshot_time: ISO timestamp

        Returns:
            List of market data dicts for all coins
        """
        self.cursor.execute("""
            SELECT * FROM market_snapshots
            WHERE snapshot_time = ?
            ORDER BY open_interest_usd DESC
        """, (snapshot_time,))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_top_coins_by_oi(self, limit: int = 20) -> List[Dict]:
        """
        Get top coins by open interest (latest snapshot).

        Args:
            limit: Number of coins to return

        Returns:
            List of market data dicts sorted by OI
        """
        # Get latest snapshot time
        self.cursor.execute("SELECT MAX(snapshot_time) FROM market_snapshots")
        row = self.cursor.fetchone()
        if not row or not row[0]:
            return []

        self.cursor.execute("""
            SELECT * FROM market_snapshots
            WHERE snapshot_time = ?
            ORDER BY open_interest_usd DESC
            LIMIT ?
        """, (row[0], limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_top_coins_by_volume(self, limit: int = 20) -> List[Dict]:
        """
        Get top coins by 24h volume (latest snapshot).

        Args:
            limit: Number of coins to return

        Returns:
            List of market data dicts sorted by volume
        """
        # Get latest snapshot time
        self.cursor.execute("SELECT MAX(snapshot_time) FROM market_snapshots")
        row = self.cursor.fetchone()
        if not row or not row[0]:
            return []

        self.cursor.execute("""
            SELECT * FROM market_snapshots
            WHERE snapshot_time = ?
            ORDER BY day_ntl_vlm DESC
            LIMIT ?
        """, (row[0], limit))
        return [dict(row) for row in self.cursor.fetchall()]

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
            SELECT COUNT(*) FROM market_snapshots
            WHERE snapshot_time < ?
        """, (cutoff,))
        count = self.cursor.fetchone()[0]

        if count > 0:
            self.cursor.execute("DELETE FROM market_snapshots WHERE snapshot_time < ?", (cutoff,))
            self.conn.commit()
            logger.info(f"Cleaned up {count} old market snapshots (older than {days_to_keep} days)")

        return count

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get market storage statistics."""
        stats = {}

        self.cursor.execute("SELECT COUNT(*) FROM market_snapshots")
        stats['total_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT coin) FROM market_snapshots")
        stats['unique_coins'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT snapshot_time) FROM market_snapshots")
        stats['unique_times'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT MIN(snapshot_time), MAX(snapshot_time) FROM market_snapshots")
        row = self.cursor.fetchone()
        stats['earliest_snapshot'] = row[0]
        stats['latest_snapshot'] = row[1]

        stats['db_size_mb'] = self.get_db_size_mb()

        return stats