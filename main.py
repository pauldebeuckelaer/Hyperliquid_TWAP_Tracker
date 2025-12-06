#!/usr/bin/env python3
"""
TWAP State Tracker - All Coins
==============================
Tracks TWAP orders for ALL coins on Hyperliquid
Single fetch, single tracker, 1-minute snapshots
NOW WITH SQLITE STORAGE
"""
import time
import signal
import sys
import json

from logging_config import setup_logging, get_module_logger
from api_client.hypurrscan_client import HypurrScanClient
from api_client.hyperliquid_client import HyperliquidClient
from trackers.state_tracker import AllCoinsStateTracker
from coin_registry import init_dynamic_registry
from trader_metrics_manager import TraderMetricsManager

logger = get_module_logger(__name__)

# Timing - single interval for everything
FETCH_INTERVAL = 60  # All coins every 60 seconds


class TWAPBot:
    """TWAP tracker for all coins on Hyperliquid"""

    def __init__(self, config: dict):
        logger.info("Initializing TWAP Tracker")

        self.config = config
        self.running = False

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

            metrics_config = hyperliquid_config.get('metrics_collection', {})
            self.metrics_manager = TraderMetricsManager(
                self.hyperliquid_client,
                config=metrics_config
            )
            logger.info("Hyperliquid client and metrics manager initialized")
        else:
            self.hyperliquid_client = None
            self.metrics_manager = None
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
            else:
                logger.warning("No TWAP data received")

        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            logger.exception(e)

    def _register_addresses_with_metrics(self):
        """Register tracked addresses with metrics manager"""
        if not self.hyperliquid_enabled or not self.metrics_manager:
            return

        self.metrics_manager.register_addresses(self.tracker.all_addresses_seen)

    def start(self):
        """Start the tracking loop"""
        logger.info(f"Starting TWAP tracking loop (interval: {FETCH_INTERVAL}s)")

        self.running = True
        loop_count = 0

        # Metrics check every hour
        metrics_check_cycles = int(3600 / FETCH_INTERVAL) if self.hyperliquid_enabled else 0

        while self.running:
            try:
                loop_start = time.time()
                loop_count += 1

                # Fetch all coins (single API call)
                self._fetch_all_coins()

                # Register addresses with metrics manager
                if self.hyperliquid_enabled:
                    self._register_addresses_with_metrics()

                # Periodic metrics check (hourly)
                if self.hyperliquid_enabled and metrics_check_cycles > 0:
                    if loop_count % metrics_check_cycles == 0:
                        self.metrics_manager.check_for_updates()
                        pending = len(self.metrics_manager.update_queue)
                        if pending > 0:
                            logger.info(f"Scheduled metrics check - {pending} pending updates")

                # Process pending metrics updates
                if self.hyperliquid_enabled and self.metrics_manager.has_pending_updates():
                    self.metrics_manager.process_single_update()
                    self.metrics_manager.save_if_needed()

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

        # Save metrics if enabled
        if self.hyperliquid_enabled and self.metrics_manager:
            logger.info("Saving trader metrics...")
            self.metrics_manager._save_metrics()
            self.metrics_manager._archive_to_history()
            logger.info(f"Metrics summary: {self.metrics_manager.get_summary()}")

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