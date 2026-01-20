#!/usr/bin/env python3
"""
TWAP State Tracker - All Coins
==============================
Tracks TWAP orders for ALL coins on Hyperliquid
Single fetch, single tracker, 1-minute snapshots
NOW WITH SQLITE STORAGE (including trader metrics)
NOW WITH MARKET DATA SNAPSHOTS
"""
import time
import signal
import sys
import json
import threading
import asyncio
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


class TWAPBot:
    """TWAP tracker for all coins on Hyperliquid"""

    def __init__(self, config: dict):
        logger.info("Initializing TWAP Tracker")

        self.config = config
        self.running = False
        self._snapshot_running = False
        self.db_path = Path('data/twap.db')

        self._pending_address_checks: set = set()

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

    def _run_whale_snapshot(self):
        """Run whale snapshot in background thread"""
        try:
            # Create thread-local connection
            thread_storage = SQLiteBackend(self.db_path)
            thread_manager = WhaleMetricsManager(
                self.hyperliquid_client,
                thread_storage,
                config=self.config.get('hyperliquid', {}).get('metrics_collection', {})
            )

            # Check if any inactive whales should be reactivated (async)
            reactivated = asyncio.run(thread_manager.check_inactive_whales_async())
            if reactivated > 0:
                logger.info(f"Reactivated {reactivated} whales")

            # Run hourly snapshot (async)
            result = asyncio.run(thread_manager.run_hourly_snapshot_async())
            logger.info(f"Hourly snapshot complete: {result['success']}/{result['total']} whales")

            thread_storage.close()
        except Exception as e:
            logger.error(f"Snapshot error: {e}")
        finally:
            self._snapshot_running = False

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

        # Whale snapshot every hour
        #metrics_check_cycles = int(3600 / FETCH_INTERVAL) if self.hyperliquid_enabled else 0
        metrics_check_cycles = 5

        while self.running:
            try:
                loop_start = time.time()
                loop_count += 1

                # Fetch all coins (single API call)
                self._fetch_all_coins()

                # NEW: Process pending whale checks (async, all in parallel)
                if self.hyperliquid_enabled:
                    self._process_pending_addresses()

                # Market data snapshot every cycle
                if self.market_tracker:
                    self.market_tracker.take_snapshot()

                # Periodic whale snapshots (hourly, in background thread)
                if self.hyperliquid_enabled and metrics_check_cycles > 0 and loop_count % metrics_check_cycles == 0:
                    if not self._snapshot_running:
                        self._snapshot_running = True
                        logger.info("Starting whale snapshot in background...")
                        threading.Thread(target=self._run_whale_snapshot, daemon=True).start()
                    else:
                        logger.warning("Snapshot still running, skipping this cycle")

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

                    last_cleanup_date = current_date


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