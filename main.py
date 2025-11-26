#!/usr/bin/env python3
"""
TWAP State Tracker with Order-Size Classification + ALL COINS TRACKING
=======================================================================
Tracks TWAP orders for:
1. Individual coins (HYPE) - using TWAPStateTracker
2. ALL other coins - using AllCoinsStateTracker
"""
import time
import signal
import sys
import json

# Import logging configuration FIRST
from logging_config import setup_logging, get_module_logger

# Import components
from api_client.hypurrscan_client import HypurrScanClient
from api_client.hyperliquid_client import HyperliquidClient
from twap_state_tracker import TWAPStateTracker
from allcoins_state_tracker import AllCoinsStateTracker

from trader_metrics_manager import TraderMetricsManager
from json_logger import SimpleJsonLogger

# Get logger for this module
logger = get_module_logger(__name__)

# Timing constants (all in seconds)
LOOP_CYCLE_TIME = 10  # Base cycle: 10 seconds
HYPE_FETCH_INTERVAL = 500  # Fetch individual symbol data every 60 seconds (1 minute)
ALL_COINS_FETCH_INTERVAL = 120 # Fetch all coins data every 120 seconds (2 minutes)


class SimpleTWAPBot:
    """TWAP tracker with order-size based classification + ALL COINS tracking"""

    def __init__(self, config: dict):
        logger.info("Initializing TWAP Tracker with All Coins Support")

        self.config = config
        self.running = False
        self.symbols = config.get('symbols', ['HYPE'])

        # Core components
        self.hypurr_client = HypurrScanClient(config.get('hypurr_data', {}))
        self.json_logger = SimpleJsonLogger(config.get('json_logging', {}))

        # One TWAP tracker per symbol (for individual tracking like HYPE)
        self.trackers = {}
        for symbol in self.symbols:
            self.trackers[symbol] = TWAPStateTracker(
                symbol,
                json_logger=self.json_logger,
            )

        # NEW: All Coins Tracker
        all_coins_config = config.get('all_coins_tracking', {})
        self.all_coins_enabled = all_coins_config.get('enabled', True)

        if self.all_coins_enabled:
            # Exclude individually tracked symbols to avoid duplication
            # (Though for validation, you might want to include HYPE in both)
            exclude_coins = all_coins_config.get('exclude_coins', [])

            self.all_coins_tracker = AllCoinsStateTracker(
                json_logger=self.json_logger,
                exclude_coins=exclude_coins
            )
            logger.info(f"All Coins Tracker enabled (excluding: {exclude_coins})")
        else:
            self.all_coins_tracker = None
            logger.info("All Coins Tracker disabled")

        # Hyperliquid client
        hyperliquid_config = config.get('hyperliquid', {})
        self.hyperliquid_enabled = hyperliquid_config.get('enabled', False)

        if self.hyperliquid_enabled:
            self.hyperliquid_client = HyperliquidClient(hyperliquid_config)

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

        # Setup shutdown handler
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        logger.info(f"TWAP Tracker initialized")
        logger.info(f"Individual tracking: {', '.join(self.symbols)}")
        logger.info(f"All coins tracking: {'enabled' if self.all_coins_enabled else 'disabled'}")

    def _fetch_individual_symbols(self):
        """Fetch and process TWAP data for individual symbols (e.g., HYPE)"""
        try:
            logger.debug("Fetching individual symbol data...")
            data = self.hypurr_client.fetch_all_data(self.symbols)
            twap_data = data.whale_activity_data.get('twap_data', {})

            for symbol in self.symbols:
                twap_orders = twap_data.get(symbol, [])
                if twap_orders:
                    # Fetch current price if client available
                    current_price = None
                    if self.hyperliquid_client:
                        current_price = self.hyperliquid_client.get_token_price(symbol)
                        if current_price:
                            logger.info(f" {symbol} price: ${current_price:,.4f}")

                    self.trackers[symbol].update(twap_orders, current_price=current_price)
                else:
                    logger.warning(f"No TWAP orders found for {symbol}")

        except Exception as e:
            logger.error(f"Error fetching individual symbol data: {e}")
            logger.exception(e)

    def _fetch_all_coins(self):
        """Fetch and process TWAP data for ALL COINS"""
        try:
            logger.debug("Fetching ALL coins data via wildcard...")

            # Use wildcard to get ALL coins
            all_coins_whale_data = self.hypurr_client.get_whale_activity(['*'])
            all_coins_twap_data = all_coins_whale_data.get('twap_data', {})

            if all_coins_twap_data:
                logger.debug(f"Received data for {len(all_coins_twap_data)} coins")
                self.all_coins_tracker.update(all_coins_twap_data)
            else:
                logger.warning("No ALL COINS data received from wildcard fetch")

        except Exception as e:
            logger.error(f"Error fetching ALL COINS data: {e}")
            logger.exception(e)

    def _register_addresses_with_metrics(self):
        """Register all tracked addresses with metrics manager"""
        if not self.hyperliquid_enabled:
            return

        # Collect addresses from individual trackers
        all_addresses = set()
        for symbol, tracker in self.trackers.items():
            all_addresses.update(tracker.all_addresses_seen)

        # Collect addresses from all coins tracker
        if self.all_coins_enabled and self.all_coins_tracker:
            all_addresses.update(self.all_coins_tracker.all_addresses_seen)

        # Register with metrics manager
        self.metrics_manager.register_addresses(all_addresses)

    def start(self):
        """
        Start the main tracking loop.

        Loop structure (10-second base cycle):
        - Every 60s: Fetch individual symbol TWAP data (HYPE)
        - Every 120s: Fetch ALL COINS TWAP data
        """
        logger.info("Starting TWAP tracking loop")
        logger.info(f"Base cycle: {LOOP_CYCLE_TIME}s")
        logger.info(f"Individual symbols fetch interval: {HYPE_FETCH_INTERVAL}s")
        logger.info(f"All coins fetch interval: {ALL_COINS_FETCH_INTERVAL}s")

        self.running = True
        loop_count = 0

        # Calculate how many base cycles fit into each fetch interval
        hype_fetch_cycles = int(HYPE_FETCH_INTERVAL / LOOP_CYCLE_TIME)  # 60/10 = 6
        all_coins_fetch_cycles = int(ALL_COINS_FETCH_INTERVAL / LOOP_CYCLE_TIME)  # 120/10 = 12
        metrics_check_cycles = int(3600 / LOOP_CYCLE_TIME) if self.hyperliquid_enabled else 0

        while self.running:
            try:
                loop_start = time.time()
                loop_count += 1

                logger.debug(f"Loop cycle #{loop_count}")

                # Fetch individual symbol data (HYPE) every 60 seconds
                if loop_count % hype_fetch_cycles == 0:
                    self._fetch_individual_symbols()

                # Fetch ALL COINS data every 120 seconds
                if self.all_coins_enabled and loop_count % all_coins_fetch_cycles == 0:
                    self._fetch_all_coins()

                # Register addresses after any fetch
                if self.hyperliquid_enabled:
                    if (loop_count % hype_fetch_cycles == 0) or (loop_count % all_coins_fetch_cycles == 0):
                        self._register_addresses_with_metrics()

                if self.hyperliquid_enabled and loop_count % metrics_check_cycles == 0:
                    self.metrics_manager.check_for_updates()
                    pending = len(self.metrics_manager.update_queue)
                    if pending > 0:
                        logger.info(f"Scheduled metrics check - {pending} pending updates")

                if self.hyperliquid_enabled and self.metrics_manager.has_pending_updates():
                    self.metrics_manager.process_single_update()
                    self.metrics_manager.save_if_needed()

                # Maintain consistent base cycle
                elapsed = time.time() - loop_start
                sleep_time = max(0.1, LOOP_CYCLE_TIME - elapsed)

                if elapsed > LOOP_CYCLE_TIME:
                    logger.warning(
                        f"Cycle {loop_count} took {elapsed:.1f}s, "
                        f"exceeded {LOOP_CYCLE_TIME}s target"
                    )

                time.sleep(sleep_time)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                logger.exception(e)
                time.sleep(LOOP_CYCLE_TIME)

        logger.info("Tracking stopped")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown"""
        logger.info("Shutdown signal received")
        self.running = False

        if self.hyperliquid_enabled and self.metrics_manager:
            logger.info("Saving trader metrics...")
            self.metrics_manager._save_metrics()
            self.metrics_manager._archive_to_history()

            summary = self.metrics_manager.get_summary()
            logger.info(f"Metrics summary: {summary}")

        # Log final stats
        if self.all_coins_enabled and self.all_coins_tracker:
            stats = self.all_coins_tracker.get_current_stats()
            logger.info("=" * 70)
            logger.info("ALL COINS TRACKER - FINAL STATS")
            logger.info("=" * 70)
            logger.info(f"Total coins tracked: {stats['total_coins_tracked']}")
            logger.info(f"Coins with activity: {stats['coins_with_activity']}")
            logger.info(f"Total orders: {stats['total_orders']}")
            logger.info(f"Active orders: {stats['total_active_orders']}")
            logger.info(f"Addresses seen: {stats['all_time_addresses']}")
            logger.info("=" * 70)


def load_config(config_file: str = "twap_config.json") -> dict:
    """Load configuration"""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        logger.info(f"Config loaded from {config_file}")
        return config
    except FileNotFoundError:
        logger.warning(f"Config file not found, using defaults")
        return {
            'symbols': ['HYPE'],
            'hypurr_data': {},
            'json_logging': {
                'enabled': True,
                'log_dir': 'json_logs'
            },
            'all_coins_tracking': {
                'enabled': True,
                'exclude_coins': []  # Set to ['HYPE'] if you want to avoid duplication
            }
        }
    except Exception as e:
        logger.error(f"Config error: {e}")
        return {
            'hypurr_data': {},
            'all_coins_tracking': {
                'enabled': True,
                'exclude_coins': []
            }
        }


def main():
    """Main entry point"""
    print("TWAP State Tracker with All Coins Support")
    print("=" * 50)
    print("Tracks TWAP orders for individual coins + ALL coins")
    print("=" * 50)
    print()

    try:
        # Load config first
        config = load_config()

        # Setup logging based on config
        setup_logging(config)

        # Now start the bot
        bot = SimpleTWAPBot(config)
        bot.start()
        return 0
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)