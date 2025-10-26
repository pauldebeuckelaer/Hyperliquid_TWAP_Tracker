#!/usr/bin/env python3
"""
TWAP State Tracker with Order-Size Classification
Tracks TWAP orders and classifies by individual order size
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
from trader_metrics_manager import TraderMetricsManager
from json_logger import SimpleJsonLogger

# Get logger for this module
logger = get_module_logger(__name__)

# Timing constants (all in seconds)
LOOP_CYCLE_TIME = 10  # Base cycle: 10 seconds
TWAP_FETCH_INTERVAL = 60  # Fetch TWAP data every 60 seconds


class SimpleTWAPBot:
    """TWAP tracker with order-size based classification"""

    def __init__(self, config: dict):
        logger.info("Initializing TWAP Tracker with Order-Size Classification")

        self.config = config
        self.running = False
        self.symbols = config.get('symbols', ['HYPE'])

        # Core components
        self.hypurr_client = HypurrScanClient(config.get('hypurr_data', {}))
        self.json_logger = SimpleJsonLogger(config.get('json_logging', {}))

        # One TWAP tracker per symbol
        self.trackers = {}
        for symbol in self.symbols:
            self.trackers[symbol] = TWAPStateTracker(
                symbol,
                json_logger=self.json_logger,
            )

        # NEW: Hyperliquid client
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

        logger.info(f"TWAP Tracker initialized for: {', '.join(self.symbols)}")
        logger.info("Order-size based classification enabled")

    def _fetch_twap_data(self):
        """Fetch and process TWAP data"""
        try:
            data = self.hypurr_client.fetch_all_data(self.symbols)
            twap_data = data.whale_activity_data.get('twap_data', {})

            for symbol in self.symbols:
                twap_orders = twap_data.get(symbol, [])
                if twap_orders:
                    # tracker.update() already logs active orders via _log_snapshot_summary()
                    self.trackers[symbol].update(twap_orders)
                else:
                    logger.warning(f"No TWAP orders found for {symbol}")
        except Exception as e:
            logger.error(f"Error fetching TWAP data: {e}")
            logger.exception(e)

    def _register_addresses_with_metrics(self):
        """Register all tracked addresses with metrics manager"""
        if not self.hyperliquid_enabled:
            return

        # Collect all addresses from all trackers
        all_addresses = set()
        for symbol, tracker in self.trackers.items():
            all_addresses.update(tracker.all_addresses_seen)

        # Register with metrics manager
        self.metrics_manager.register_addresses(all_addresses)

    def start(self):
        """
        Start the main tracking loop.

        Loop structure (10-second base cycle):
        - Every 60s: Fetch TWAP data and update trackers
                     (tracker logs active orders automatically)
        """
        logger.info("Starting TWAP tracking loop")
        logger.info(f"Base cycle: {LOOP_CYCLE_TIME}s")
        logger.info(f"TWAP fetch interval: {TWAP_FETCH_INTERVAL}s")

        self.running = True
        loop_count = 0

        # Calculate how many base cycles fit into fetch interval
        fetch_cycles = int(TWAP_FETCH_INTERVAL / LOOP_CYCLE_TIME)  # 60/10 = 6
        metrics_check_cycles = int(3600 / LOOP_CYCLE_TIME) if self.hyperliquid_enabled else 0

        while self.running:
            try:
                loop_start = time.time()
                loop_count += 1

                logger.debug(f"Loop cycle #{loop_count}")

                # Fetch TWAP data every 60 seconds (every 6 cycles)
                if loop_count % fetch_cycles == 0:
                    self._fetch_twap_data()

                    if self.hyperliquid_enabled:
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
            }
        }
    except Exception as e:
        logger.error(f"Config error: {e}")
        return {'hypurr_data': {}}


def main():
    """Main entry point"""
    print("TWAP State Tracker with Order-Size Classification")
    print("=" * 50)
    print("Tracks TWAP orders and classifies by order size")
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
