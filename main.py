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

        while self.running:
            try:
                loop_start = time.time()
                loop_count += 1

                logger.debug(f"Loop cycle #{loop_count}")

                # Fetch TWAP data every 60 seconds (every 6 cycles)
                if loop_count % fetch_cycles == 0:
                    self._fetch_twap_data()

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
