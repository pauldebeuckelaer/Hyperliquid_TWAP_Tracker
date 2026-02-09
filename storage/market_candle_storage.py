#!/usr/bin/env python3
"""
Market Candle Storage
=====================
Storage for aggregated market candles (10-minute intervals).

Compresses raw market_snapshots (270K rows/day) into compact candles
(~27K rows/day) for long-term retention and backtesting.

Tables:
- market_candles: 10-min OHLCV + OI + funding per coin
"""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .base import BaseStorage

logger = logging.getLogger(__name__)

# Aggregation interval in minutes
CANDLE_INTERVAL_MINUTES = 10


class MarketCandleStorage(BaseStorage):
    """Storage for aggregated 10-minute market candles."""

    def _create_tables(self):
        """Create market candle tables."""
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candle_time TEXT NOT NULL,
                coin TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                avg_funding REAL,
                max_oi REAL,
                min_oi REAL,
                close_oi REAL,
                max_oi_usd REAL,
                close_oi_usd REAL,
                avg_premium REAL,
                volume REAL,
                num_samples INTEGER,
                UNIQUE(candle_time, coin)
            )
        """)
        self.conn.commit()

    def _create_indexes(self):
        """Create market candle indexes."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_candle_time ON market_candles(candle_time)",
            "CREATE INDEX IF NOT EXISTS idx_candle_coin ON market_candles(coin)",
            "CREATE INDEX IF NOT EXISTS idx_candle_coin_time ON market_candles(coin, candle_time)",
        ]
        self._execute_index_list(indexes)

    # =========================================================================
    # AGGREGATION
    # =========================================================================

    def aggregate_market_snapshots(self, before_time: str = None, batch_size: int = 50000) -> int:
        """
        Aggregate raw market_snapshots into 10-minute candles.

        Groups snapshots by coin and 10-minute window, computing OHLCV
        and OI statistics. Uses INSERT OR IGNORE to skip already-aggregated
        windows (idempotent).

        Args:
            before_time: Only aggregate snapshots before this ISO timestamp.
                         If None, aggregates everything older than 3 days.
            batch_size: Not used directly but kept for interface consistency.

        Returns:
            Number of candles inserted
        """
        if before_time is None:
            before_time = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

        logger.info(f"Aggregating market_snapshots before {before_time} into 10-min candles...")

        # Find the earliest un-aggregated snapshot
        # We check what's already in candles to avoid reprocessing
        self.cursor.execute("""
            SELECT MIN(snapshot_time) FROM market_snapshots
            WHERE snapshot_time < ?
        """, (before_time,))
        row = self.cursor.fetchone()
        earliest = row[0] if row and row[0] else None

        if not earliest:
            logger.info("No market_snapshots to aggregate")
            return 0

        logger.info(f"Aggregating from {earliest} to {before_time}")

        # The core aggregation query:
        # - Truncates snapshot_time to 10-min windows using substr + integer math
        # - Computes OHLC on mark_px (first/last by snapshot_time, min, max)
        # - Computes OI stats (min, max, last)
        # - Averages funding and premium
        # - Takes last volume (day_ntl_vlm is cumulative)
        #
        # The 10-min bucket is computed as:
        #   Take the minute portion, integer-divide by 10, multiply by 10
        #   e.g., minute 37 -> (37/10)*10 = 30 -> :30
        try:
            # Use a temp table approach for speed:
            # 1. Assign each row a bucket + row number within its bucket
            # 2. Aggregate with simple GROUP BY for min/max/avg/count
            # 3. Join first/last rows for open/close values

            # Step 1: Create temp table with bucket assignments
            self.cursor.execute("DROP TABLE IF EXISTS _temp_bucketed")
            self.cursor.execute("""
                CREATE TEMP TABLE _temp_bucketed AS
                SELECT
                    id,
                    snapshot_time,
                    coin,
                    -- 10-min bucket: "2026-02-09T14:30"
                    SUBSTR(snapshot_time, 1, 14) || 
                        PRINTF('%02d', (CAST(SUBSTR(snapshot_time, 15, 2) AS INTEGER) / 10) * 10)
                    AS bucket,
                    mark_px,
                    funding_8h,
                    open_interest,
                    open_interest_usd,
                    day_ntl_vlm,
                    premium
                FROM market_snapshots
                WHERE snapshot_time < ?
            """, (before_time,))

            bucket_count = self.cursor.rowcount
            if bucket_count == 0:
                logger.info("No market_snapshots to aggregate")
                self.cursor.execute("DROP TABLE IF EXISTS _temp_bucketed")
                return 0

            logger.info(f"Bucketed {bucket_count} rows, computing candles...")

            # Step 2: Get first and last snapshot_time per (coin, bucket)
            self.cursor.execute("CREATE INDEX IF NOT EXISTS _temp_idx ON _temp_bucketed(coin, bucket, snapshot_time)")

            # Step 3: Single aggregation query with subqueries only on the temp table
            self.cursor.execute("""
                INSERT OR IGNORE INTO market_candles (
                    candle_time, coin,
                    open, high, low, close,
                    avg_funding,
                    max_oi, min_oi, close_oi,
                    max_oi_usd, close_oi_usd,
                    avg_premium, volume, num_samples
                )
                SELECT
                    agg.bucket,
                    agg.coin,
                    -- Open: price at first snapshot in bucket
                    first_row.mark_px,
                    agg.high_px,
                    agg.low_px,
                    -- Close: price at last snapshot in bucket
                    last_row.mark_px,
                    agg.avg_funding,
                    agg.max_oi,
                    agg.min_oi,
                    -- Close OI: last snapshot's OI
                    last_row.open_interest,
                    agg.max_oi_usd,
                    last_row.open_interest_usd,
                    agg.avg_premium,
                    -- Volume: last snapshot's cumulative volume
                    last_row.day_ntl_vlm,
                    agg.num_samples
                FROM (
                    -- Core aggregation
                    SELECT
                        coin,
                        bucket,
                        MAX(mark_px) AS high_px,
                        MIN(mark_px) AS low_px,
                        AVG(funding_8h) AS avg_funding,
                        MAX(open_interest) AS max_oi,
                        MIN(open_interest) AS min_oi,
                        MAX(open_interest_usd) AS max_oi_usd,
                        AVG(premium) AS avg_premium,
                        COUNT(*) AS num_samples,
                        MIN(snapshot_time) AS first_time,
                        MAX(snapshot_time) AS last_time
                    FROM _temp_bucketed
                    GROUP BY coin, bucket
                ) agg
                -- Join first row for open price
                JOIN _temp_bucketed first_row
                    ON first_row.coin = agg.coin
                    AND first_row.snapshot_time = agg.first_time
                    AND first_row.bucket = agg.bucket
                -- Join last row for close price, close OI, volume
                JOIN _temp_bucketed last_row
                    ON last_row.coin = agg.coin
                    AND last_row.snapshot_time = agg.last_time
                    AND last_row.bucket = agg.bucket
            """)

            candles_inserted = self.cursor.rowcount

            # Cleanup temp table
            self.cursor.execute("DROP TABLE IF EXISTS _temp_bucketed")
            self.conn.commit()

            logger.info(f"Aggregated {candles_inserted} candles from market_snapshots")
            return candles_inserted

        except Exception as e:
            logger.error(f"Error aggregating market snapshots: {e}")
            self.cursor.execute("DROP TABLE IF EXISTS _temp_bucketed")
            self.conn.rollback()
            raise

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    def get_candles(
            self,
            coin: str,
            start_time: str = None,
            end_time: str = None,
            limit: int = 1000
    ) -> List[Dict]:
        """
        Get 10-min candles for a coin.

        Args:
            coin: Coin symbol (e.g., 'BTC', 'ETH')
            start_time: Optional start timestamp
            end_time: Optional end timestamp
            limit: Max results (default 1000)

        Returns:
            List of candle dicts, oldest first (for charting)
        """
        query = "SELECT * FROM market_candles WHERE coin = ?"
        params = [coin]

        if start_time:
            query += " AND candle_time >= ?"
            params.append(start_time)

        if end_time:
            query += " AND candle_time <= ?"
            params.append(end_time)

        query += " ORDER BY candle_time ASC LIMIT ?"
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_price_at_time(self, coin: str, target_time: str) -> Optional[Dict]:
        """
        Get the closest candle to a target time.
        Useful for backtesting: "what was the price when this signal fired?"

        Args:
            coin: Coin symbol
            target_time: ISO timestamp

        Returns:
            Closest candle dict, or None
        """
        # Try to find candle at or just before target time
        self.cursor.execute("""
            SELECT * FROM market_candles
            WHERE coin = ? AND candle_time <= ?
            ORDER BY candle_time DESC
            LIMIT 1
        """, (coin, target_time))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def get_price_after_signal(
            self,
            coin: str,
            signal_time: str,
            hours_after: List[float] = [1, 4, 24, 48]
    ) -> Dict:
        """
        Get prices at various intervals after a signal time.
        Core backtesting method.

        Args:
            coin: Coin symbol
            signal_time: When the signal occurred (ISO timestamp)
            hours_after: List of hours to check (default: 1h, 4h, 24h, 48h)

        Returns:
            Dict with signal price and prices at each interval:
            {
                'signal_price': 84000.0,
                '1h': {'price': 84500.0, 'change_pct': 0.595},
                '4h': {'price': 83000.0, 'change_pct': -1.190},
                ...
            }
        """
        # Get price at signal time
        signal_candle = self.get_price_at_time(coin, signal_time)
        if not signal_candle:
            return {}

        signal_price = signal_candle['close']
        result = {
            'signal_time': signal_time,
            'signal_price': signal_price,
            'signal_candle_time': signal_candle['candle_time'],
        }

        # Get price at each interval
        for hours in hours_after:
            target = (
                    datetime.fromisoformat(signal_time) + timedelta(hours=hours)
            ).isoformat()

            future_candle = self.get_price_at_time(coin, target)
            if future_candle:
                future_price = future_candle['close']
                change_pct = ((future_price - signal_price) / signal_price) * 100
                result[f'{hours}h'] = {
                    'price': future_price,
                    'change_pct': round(change_pct, 4),
                    'candle_time': future_candle['candle_time'],
                }
            else:
                result[f'{hours}h'] = None

        return result

    def get_candle_stats(self) -> Dict:
        """Get statistics about stored candles."""
        stats = {}

        self.cursor.execute("SELECT COUNT(*) FROM market_candles")
        stats['total_candles'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT coin) FROM market_candles")
        stats['unique_coins'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT MIN(candle_time), MAX(candle_time) FROM market_candles")
        row = self.cursor.fetchone()
        stats['earliest'] = row[0]
        stats['latest'] = row[1]

        self.cursor.execute("SELECT AVG(num_samples) FROM market_candles")
        stats['avg_samples_per_candle'] = round(self.cursor.fetchone()[0] or 0, 1)

        return stats