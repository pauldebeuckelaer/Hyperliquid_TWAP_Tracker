#!/usr/bin/env python3
"""
Market Data Tracker
===================
1-minute snapshots of market data for all perps (main + HIP-3).
Captures prices, funding, OI, volume.

This is a lightweight tracker - whale/liquidation tracking is handled
separately by LiquidationTracker.

HIP-3 Integration:
    When hip3_tracking_enabled=True, this tracker also captures market
    data for builder-deployed perp dexes (xyz, flx, vntl, etc.). HIP-3
    instrument coin names are prefixed with the dex name (e.g., xyz:BRENTOIL)
    and written to the same market_snapshots table.

    HIP-3 fetch failures are isolated — if any HIP-3 dex fails, main perp
    data still lands in the database unaffected.

Usage:
    from market_data_tracker import MarketDataTracker

    tracker = MarketDataTracker(hl_client, storage, config={'hip3_tracking_enabled': True})

    # Each cycle:
    result = tracker.take_snapshot()
"""
import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MarketDataTracker:
    """
    Tracks market data snapshots every minute for main perps and (optionally)
    HIP-3 builder-deployed dexes.

    Captures for each instrument:
    - Mark price
    - Oracle price
    - Previous day price
    - Funding rate (8h)
    - Open interest
    - 24h volume
    - Premium
    """

    def __init__(self, hl_client, storage, config: Optional[Dict] = None):
        """
        Initialize market data tracker.

        Args:
            hl_client: HyperliquidClient instance for API calls
            storage: SQLiteBackend instance
            config: Optional config dict. Supports:
                - hip3_tracking_enabled: bool (default False) — enables HIP-3
                  market data capture per cycle. When True, fetches
                  metaAndAssetCtxs for each active HIP-3 dex in addition
                  to the main perp call.
        """
        self.client = hl_client
        self.storage = storage

        config = config or {}
        self.hip3_tracking_enabled = config.get('hip3_tracking_enabled', False)

        self.last_snapshot_time: Optional[datetime] = None
        self.snapshot_count = 0
        self.hip3_snapshot_count = 0

        logger.info(
            f"MarketDataTracker initialized "
            f"(hip3_tracking_enabled={self.hip3_tracking_enabled})"
        )

    def take_snapshot(self) -> Dict:
        """
        Take a market data snapshot for all perps (main + HIP-3 if enabled).

        Fetches in sequence:
        1. Main perp metaAndAssetCtxs (one API call, ~100+ coins)
        2. HIP-3 metaAndAssetCtxs per active dex (N API calls, one per dex)
           — only if hip3_tracking_enabled=True

        HIP-3 failures are isolated — if any HIP-3 dex fails, main perp
        data still lands in the database.

        Returns:
            Dict with snapshot results including prices and market data
        """
        timestamp = datetime.now()

        logger.debug(f"📊 Market snapshot #{self.snapshot_count + 1}")

        # =====================================================================
        # PHASE 1: Main perp market data
        # =====================================================================
        market_data = self.client.get_meta_and_asset_ctxs()

        if not market_data:
            logger.warning("Failed to get main perp market data")
            return {
                'timestamp': timestamp.isoformat(),
                'success': False,
                'num_coins': 0,
                'num_hip3_coins': 0,
                'hip3_dexes_fetched': [],
                'hip3_dexes_failed': [],
                'prices': {},
                'market_data': {},
            }

        asset_ctxs = market_data.get('asset_ctxs', {})
        active_assets = {k: v for k, v in asset_ctxs.items() if not v.get('is_delisted')}

        logger.debug(f"Captured {len(active_assets)} active main perps")

        # =====================================================================
        # PHASE 2: HIP-3 market data (gated by config flag)
        # =====================================================================
        hip3_active = {}
        hip3_dexes_fetched = []
        hip3_dexes_failed = []

        if self.hip3_tracking_enabled:
            try:
                dex_names = self.client.get_active_hip3_dexes()

                for dex_name in dex_names:
                    try:
                        hip3_data = self.client.get_hip3_meta_and_asset_ctxs(dex_name)

                        if not hip3_data:
                            logger.debug(f"HIP-3 dex '{dex_name}' returned no data")
                            hip3_dexes_failed.append(dex_name)
                            continue

                        dex_ctxs = hip3_data.get('asset_ctxs', {})
                        # Filter delisted — keys are already "dex:COIN" prefixed
                        dex_active = {
                            k: v for k, v in dex_ctxs.items()
                            if not v.get('is_delisted')
                        }

                        hip3_active.update(dex_active)
                        hip3_dexes_fetched.append(dex_name)

                    except Exception as e:
                        logger.warning(
                            f"HIP-3 market data fetch failed for dex '{dex_name}': {e}"
                        )
                        hip3_dexes_failed.append(dex_name)
                        continue

                if hip3_active:
                    self.hip3_snapshot_count += 1
                    msg = (
                        f"🏗️  HIP-3 market data: {len(hip3_active)} instruments "
                        f"across {len(hip3_dexes_fetched)} dexes"
                    )
                    if hip3_dexes_failed:
                        msg += f" | failed: {hip3_dexes_failed}"
                    logger.info(msg)

            except Exception as e:
                logger.error(f"HIP-3 market data branch failed entirely: {e}")
                # Main perp data still gets saved below — isolation guarantee

        # =====================================================================
        # PHASE 3: Merge and persist
        # =====================================================================
        # Combine main perp + HIP-3 into single dict for storage.
        # No key collisions possible because HIP-3 keys contain ":"
        all_active = {**active_assets, **hip3_active}

        # Extract prices for easy access by downstream consumers
        prices = {k: v.get('mark_px', 0) for k, v in all_active.items()}

        # Save to database — single transaction with combined dict
        self.storage.save_market_snapshot(timestamp.isoformat(), all_active)

        self.last_snapshot_time = timestamp
        self.snapshot_count += 1

        return {
            'timestamp': timestamp.isoformat(),
            'success': True,
            'num_coins': len(active_assets),
            'num_hip3_coins': len(hip3_active),
            'hip3_dexes_fetched': hip3_dexes_fetched,
            'hip3_dexes_failed': hip3_dexes_failed,
            'prices': prices,
            'market_data': all_active,
        }

    def get_prices(self) -> Dict[str, float]:
        """
        Get current prices for all perps (main only, via allMids).

        Note: This does NOT include HIP-3 prices. Use take_snapshot() for
        HIP-3 coverage, or call hl_client.get_hip3_mids() directly.

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
            'hip3_snapshot_count': self.hip3_snapshot_count,
            'hip3_tracking_enabled': self.hip3_tracking_enabled,
            'last_snapshot': self.last_snapshot_time.isoformat() if self.last_snapshot_time else None,
        }