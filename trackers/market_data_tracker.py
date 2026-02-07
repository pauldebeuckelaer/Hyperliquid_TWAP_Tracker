#!/usr/bin/env python3
"""
Market Data Tracker
===================
1-minute snapshots of market data for all perps.
Captures prices, funding, OI, volume.

This is a lightweight tracker - whale/liquidation tracking is handled
separately by LiquidationTracker.

Usage:
    from market_data_tracker import MarketDataTracker

    tracker = MarketDataTracker(hl_client, storage)

    # Each cycle:
    result = tracker.take_snapshot()
"""
import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MarketDataTracker:
    """
    Tracks market data snapshots every minute.

    Captures for all perps:
    - Mark price
    - Oracle price
    - Previous day price
    - Funding rate (8h)
    - Open interest
    - 24h volume
    - Premium
    """

    def __init__(self, hl_client, storage):
        """
        Initialize market data tracker.

        Args:
            hl_client: HyperliquidClient instance for API calls
            storage: SQLiteBackend instance
        """
        self.client = hl_client
        self.storage = storage

        self.last_snapshot_time: Optional[datetime] = None
        self.snapshot_count = 0

        logger.info("MarketDataTracker initialized")

    def take_snapshot(self) -> Dict:
        """
        Take a market data snapshot for all perps.

        Single API call to get prices, funding, OI, volume for all coins.

        Returns:
            Dict with snapshot results including prices and market data
        """
        timestamp = datetime.now()

        logger.debug(f"📊 Market snapshot #{self.snapshot_count + 1}")

        # Get market data (funding, OI, volume, prices) - single API call
        market_data = self.client.get_meta_and_asset_ctxs()

        if not market_data:
            logger.warning("Failed to get market data")
            return {
                'timestamp': timestamp.isoformat(),
                'success': False,
                'num_coins': 0,
                'prices': {},
                'market_data': {},
            }

        asset_ctxs = market_data.get('asset_ctxs', {})

        # Filter out delisted coins
        active_assets = {k: v for k, v in asset_ctxs.items() if not v.get('is_delisted')}

        # Extract prices for easy access
        prices = {k: v.get('mark_px', 0) for k, v in active_assets.items()}

        logger.debug(f"Captured {len(active_assets)} active perps")

        # Save to database
        self.storage.save_market_snapshot(timestamp.isoformat(), active_assets)

        self.last_snapshot_time = timestamp
        self.snapshot_count += 1

        return {
            'timestamp': timestamp.isoformat(),
            'success': True,
            'num_coins': len(active_assets),
            'prices': prices,
            'market_data': active_assets,
        }

    def get_prices(self) -> Dict[str, float]:
        """
        Get current prices for all perps.

        Returns:
            Dict of coin -> price
        """
        all_mids = self.client.get_all_mids()
        if all_mids:
            return {k: float(v) for k, v in all_mids.items() if not k.startswith('@')}
        return {}

    def get_stats(self) -> Dict:
        """Get tracker statistics."""
        return {
            'snapshot_count': self.snapshot_count,
            'last_snapshot': self.last_snapshot_time.isoformat() if self.last_snapshot_time else None,
        }