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
from twap_state_tracker import TWAPStateTracker
from json_logger import SimpleJsonLogger
# from address_tracker import AddressVolumeTracker  # DISABLED

# Get logger for this module
logger = get_module_logger(__name__)


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

        # Address tracker - DISABLED
        # address_config = config.get('address_tracking', {})
        # self.address_tracker = AddressVolumeTracker(address_config)

        # One TWAP tracker per symbol
        self.trackers = {}
        for symbol in self.symbols:
            self.trackers[symbol] = TWAPStateTracker(
                symbol,
                json_logger=self.json_logger,
            )

        # Setup shutdown handler
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        logger.info(f"TWAP Tracker initialized for: {', '.join(self.symbols)}")
        logger.info("Order-size based classification enabled")

    def start(self):
        """Start tracking"""
        logger.info(f"Starting TWAP tracking for {len(self.symbols)} symbols")
        self.running = True

        loop_count = 0

        while self.running:
            try:
                loop_count += 1

                # Fetch TWAP data for all symbols
                logger.info(f"Fetching TWAP data (loop {loop_count})")
                data = self.hypurr_client.fetch_all_data(self.symbols)

                # Process each symbol
                twap_data = data.whale_activity_data.get('twap_data', {})

                for symbol in self.symbols:
                    twap_orders = twap_data.get(symbol, [])

                    if twap_orders:
                        # Update TWAP tracker
                        self.trackers[symbol].update(twap_orders)

                        # Address tracker logging - DISABLED
                        # snapshot = self.trackers[symbol].current_snapshot
                        # if snapshot:
                        #     self.address_tracker.log_whale_activity(snapshot)
                    else:
                        logger.warning(f"No TWAP orders found for {symbol}")

                # Periodic summary - DISABLED
                # if loop_count % 10 == 0:
                #     logger.info("")
                #     logger.info("=" * 80)
                #     logger.info(f"Periodic Summary (loop {loop_count})")
                #     logger.info("=" * 80)
                #     self.address_tracker.log_summary()
                #     self.address_tracker.log_top_traders(limit=10)

                # Wait 60 seconds
                logger.info("Waiting 60 seconds...")
                time.sleep(60)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error in loop: {e}")
                logger.exception(e)
                time.sleep(60)

        # Final report - DISABLED
        # logger.info("")
        # logger.info("=" * 80)
        # logger.info("Final Address Classification Report")
        # logger.info("=" * 80)
        # self.address_tracker.log_summary()
        # self.address_tracker.export_report()

        logger.info("Tracking stopped")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown"""
        logger.info("Shutdown signal received")
        self.running = False


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