#!/usr/bin/env python3
"""
TWAP State Tracker - All Coins
==============================
Tracks TWAP orders for ALL coins on Hyperliquid
Single fetch, single tracker, 1-minute snapshots
NOW WITH SQLITE STORAGE (including trader metrics)
NOW WITH MARKET DATA SNAPSHOTS
REFACTORED: Incremental whale snapshots (no threading)
"""
import time
import signal
import sys
import json
import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime, timezone, timedelta

from logging_config import setup_logging, get_module_logger
from api_client.hypurrscan_client import HypurrScanClient
from api_client.hyperliquid_client import HyperliquidClient
from trackers.state_tracker import AllCoinsStateTracker
from trackers.market_data_tracker import MarketDataTracker
from coin_registry import init_dynamic_registry
from storage import SQLiteBackend
from trader_metrics_manager import WhaleMetricsManager

from generators.daily_summary import generate_daily_summaries

logger = get_module_logger(__name__)

# Timing - single interval for everything
FETCH_INTERVAL = 60  # All coins every 60 seconds

# Whale snapshot settings
WHALES_PER_CYCLE = 20  # How many whales to snapshot each cycle
WHALE_DELAY = 1.0  # Seconds between each whale API call


class TWAPBot:
    """TWAP tracker for all coins on Hyperliquid"""

    def __init__(self, config: dict):
        logger.info("Initializing TWAP Tracker")

        self.config = config
        self.running = False
        self.db_path = Path('data/twap.db')

        self._pending_address_checks: set = set()

        # Whale snapshot state (incremental)
        self._whale_snapshot_index = 0
        self._whale_snapshot_active_list = []
        self._whale_snapshot_stats = {"success": 0, "failed": 0, "dropped": 0}
        self._whale_snapshot_start_time = None

        # HypurrScan client for TWAP data
        self.hypurr_client = HypurrScanClient(config.get('hypurr_data', {}))

        # All Coins Tracker (now with SQLite storage)
        self.tracker = AllCoinsStateTracker(
            exclude_coins=config.get('exclude_coins', [])
        )

        # Hyperliquid client for prices + trader metrics
        hyperliquid_config = config.get('hyperliquid', {})
        self.hyperliquid_enabled = hyperliquid_config.get('enabled', False)

        if self.hyperliquid_enabled:
            self.hyperliquid_client = HyperliquidClient(hyperliquid_config)
            init_dynamic_registry(self.hyperliquid_client)
            logger.info("Hyperliquid client initialized")
            self.tracker.on_new_addresses = self._on_new_addresses

            # Market data tracker
            self.market_tracker = MarketDataTracker(self.hyperliquid_client, self.db_path)
        else:
            self.hyperliquid_client = None
            self.market_tracker = None
            logger.info("Hyperliquid client disabled")

        # Shutdown handler
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        logger.info("TWAP Tracker initialized")

    def _fetch_all_coins(self):
        """Fetch and process TWAP data for ALL coins - single API call"""
        try:
            logger.debug("Fetching all coins data...")

            # Single fetch for everything via twap/*
            whale_data = self.hypurr_client.get_whale_activity(['*'])
            twap_data = whale_data.get('twap_data', {})

            if twap_data:
                logger.debug(f"Received data for {len(twap_data)} coins")

                # Fetch prices (single API call)
                prices = {}
                if self.hyperliquid_client:
                    all_mids = self.hyperliquid_client.get_all_mids()
                    if all_mids:
                        prices = {k: float(v) for k, v in all_mids.items() if not k.startswith('@')}
                        logger.debug(f"Fetched prices for {len(prices)} assets")

                # Update tracker
                self.tracker.update(twap_data, prices=prices)

                # Clean up stale orders
                cleaned = self.tracker.db.cleanup_stale_orders()
                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} stale orders this cycle")
            else:
                logger.warning("No TWAP data received")

        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            logger.exception(e)

    def _start_new_whale_snapshot_cycle(self):
        """Initialize a new whale snapshot cycle"""
        storage = SQLiteBackend(self.db_path)
        self._whale_snapshot_active_list = storage.get_active_whale_addresses()
        storage.close()

        self._whale_snapshot_index = 0
        self._whale_snapshot_stats = {"success": 0, "failed": 0, "dropped": 0}
        self._whale_snapshot_start_time = time.time()

        total = len(self._whale_snapshot_active_list)
        cycles_needed = (total + WHALES_PER_CYCLE - 1) // WHALES_PER_CYCLE
        logger.info(f"Starting whale snapshot cycle: {total} whales, ~{cycles_needed} cycles to complete")

    def _run_whale_snapshot_batch(self):
        """
        Process a batch of whales (called each main loop cycle).
        Returns True if snapshot cycle is complete, False if more work remains.
        """
        if not self._whale_snapshot_active_list:
            self._start_new_whale_snapshot_cycle()
            if not self._whale_snapshot_active_list:
                logger.warning("No active whales to snapshot")
                return True

        total = len(self._whale_snapshot_active_list)
        start_idx = self._whale_snapshot_index
        end_idx = min(start_idx + WHALES_PER_CYCLE, total)

        batch = self._whale_snapshot_active_list[start_idx:end_idx]

        if not batch:
            # Cycle complete
            elapsed = time.time() - self._whale_snapshot_start_time
            stats = self._whale_snapshot_stats
            logger.info(
                f"Whale snapshot cycle complete: "
                f"{stats['success']}/{total} success, {stats['failed']} failed, {stats['dropped']} dropped "
                f"({elapsed:.1f}s total)"
            )
            # Reset for next cycle
            self._whale_snapshot_active_list = []
            self._whale_snapshot_index = 0
            return True

        logger.debug(f"Snapshotting whales {start_idx + 1}-{end_idx}/{total}...")

        # Run async batch
        try:
            batch_stats = asyncio.run(self._snapshot_batch_async(batch))

            self._whale_snapshot_stats["success"] += batch_stats["success"]
            self._whale_snapshot_stats["failed"] += batch_stats["failed"]
            self._whale_snapshot_stats["dropped"] += batch_stats["dropped"]

            self._whale_snapshot_index = end_idx

            # Progress logging every 100 whales
            if end_idx % 100 < WHALES_PER_CYCLE:
                elapsed = time.time() - self._whale_snapshot_start_time
                stats = self._whale_snapshot_stats
                remaining = total - end_idx
                rate = end_idx / elapsed if elapsed > 0 else 0
                eta = remaining / rate if rate > 0 else 0
                logger.info(
                    f"Whale snapshot progress: {end_idx}/{total} "
                    f"({stats['success']} success, {stats['failed']} failed) - ETA: {eta:.0f}s"
                )

        except Exception as e:
            logger.error(f"Error in whale snapshot batch: {e}")
            self._whale_snapshot_index = end_idx  # Skip this batch, continue

        return False

    async def _snapshot_batch_async(self, addresses: list) -> dict:
        """Snapshot a batch of whale addresses with delay between each"""
        stats = {"success": 0, "failed": 0, "dropped": 0}

        storage = SQLiteBackend(self.db_path)
        manager = WhaleMetricsManager(
            self.hyperliquid_client,
            storage,
            config=self.config.get('hyperliquid', {}).get('metrics_collection', {})
        )

        async with aiohttp.ClientSession() as session:
            for i, address in enumerate(addresses):
                try:
                    result = await manager.take_snapshot_async(address, session)

                    if result:
                        stats["success"] += 1
                    else:
                        # Check if dropped (deactivated)
                        if address not in storage.get_active_whale_addresses():
                            stats["dropped"] += 1
                        else:
                            stats["failed"] += 1

                except Exception as e:
                    logger.error(f"Error snapshotting {address}: {e}")
                    stats["failed"] += 1

                # Delay between whales (skip after last one)
                if i < len(addresses) - 1:
                    await asyncio.sleep(WHALE_DELAY)

        storage.close()
        return stats

    def _on_new_addresses(self, addresses: set):
        """Collect new addresses for async whale check after loop completes"""
        if not self.hyperliquid_enabled or not addresses:
            return
        # Just collect them - don't block the loop
        self._pending_address_checks.update(addresses)

    def _process_pending_addresses(self):
        """Process collected addresses asynchronously (all in parallel)"""
        if not self._pending_address_checks:
            return

        addresses = self._pending_address_checks.copy()
        self._pending_address_checks.clear()

        try:
            storage = SQLiteBackend(self.db_path)
            manager = WhaleMetricsManager(
                self.hyperliquid_client,
                storage,
                config=self.config.get('hyperliquid', {}).get('metrics_collection', {})
            )

            # Run async whale check
            added = asyncio.run(manager.register_addresses_async(addresses))

            if added > 0:
                logger.info(f"Discovered {added} new whale(s) from {len(addresses)} addresses")

            storage.close()

        except Exception as e:
            logger.error(f"Error processing addresses: {e}")

    def _run_daily_cleanup(self):
        """Run database cleanup to remove old data"""
        try:
            storage = SQLiteBackend(self.db_path)
            deleted = storage.cleanup_old_data(days_to_keep=7)
            if deleted:
                total = sum(deleted.values())
                logger.info(f"Daily cleanup removed {total} rows: {deleted}")
            else:
                logger.info("Daily cleanup: no old data to remove")
            storage.close()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    def start(self):
        """Start the tracking loop"""
        logger.info(f"Starting TWAP tracking loop (interval: {FETCH_INTERVAL}s)")

        self.running = True
        loop_count = 0
        last_cleanup_date = None  # Track daily cleanup

        while self.running:
            try:
                loop_start = time.time()
                loop_count += 1

                # Fetch all coins (single API call)
                self._fetch_all_coins()

                # Process pending whale checks (async, all in parallel)
                if self.hyperliquid_enabled:
                    self._process_pending_addresses()

                # Market data snapshot every cycle
                if self.market_tracker:
                    self.market_tracker.take_snapshot()

                # Incremental whale snapshots (every cycle, no threading!)
                if self.hyperliquid_enabled:
                    self._run_whale_snapshot_batch()

                # Daily cleanup (runs once when date changes)
                current_date = datetime.now(timezone.utc).date()
                if last_cleanup_date != current_date:
                    logger.info("Running daily database cleanup...")
                    self._run_daily_cleanup()
                    last_cleanup_date = current_date

                    # Generate previous day's summaries
                    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
                    try:
                        storage = SQLiteBackend(self.db_path)
                        generate_daily_summaries(storage, yesterday)
                        storage.close()
                    except Exception as e:
                        logger.error(f"Failed to generate daily summaries: {e}")

                # Maintain consistent interval
                elapsed = time.time() - loop_start
                sleep_time = max(0.1, FETCH_INTERVAL - elapsed)

                if elapsed > FETCH_INTERVAL:
                    logger.warning(
                        f"Cycle {loop_count} took {elapsed:.1f}s, "
                        f"exceeded {FETCH_INTERVAL}s target"
                    )

                time.sleep(sleep_time)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                logger.exception(e)
                time.sleep(FETCH_INTERVAL)

        logger.info("Tracking stopped")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown gracefully"""
        logger.info("Shutdown signal received")
        self.running = False

        # Close database connection
        logger.info("Closing database connection...")
        self.tracker.close()

        # Log final stats
        stats = self.tracker.get_current_stats()
        logger.info("=" * 70)
        logger.info("FINAL STATS")
        logger.info("=" * 70)
        logger.info(f"Total coins tracked: {stats['total_coins_tracked']}")
        logger.info(f"Coins with activity: {stats['coins_with_activity']}")
        logger.info(f"Total orders: {stats['total_orders']}")
        logger.info(f"Active orders: {stats['total_active_orders']}")
        logger.info(f"Addresses seen: {stats['all_time_addresses']}")
        if 'db_stats' in stats:
            db_stats = stats['db_stats']
            logger.info(f"Database size: {db_stats.get('db_size_mb', 0)} MB")

        # Market tracker stats
        if self.market_tracker:
            market_stats = self.market_tracker.get_stats()
            logger.info(f"Market snapshots taken: {market_stats['snapshot_count']}")

        # Whale snapshot stats
        logger.info(f"Whale snapshot stats: {self._whale_snapshot_stats}")

        logger.info("=" * 70)


def load_config(config_file: str = "twap_config.json") -> dict:
    """Load configuration from JSON file"""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        logger.info(f"Config loaded from {config_file}")
        return config
    except FileNotFoundError:
        logger.warning(f"Config file not found, using defaults")
        return {
            'hypurr_data': {},
            'exclude_coins': []
        }
    except Exception as e:
        logger.error(f"Config error: {e}")
        return {'hypurr_data': {}}


def main():
    """Main entry point"""
    print("TWAP State Tracker - All Coins (SQLite)")
    print("=" * 50)

    try:
        config = load_config()
        setup_logging(config)

        bot = TWAPBot(config)
        bot.start()
        return 0
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())