#!/usr/bin/env python3
"""
TWAP State Tracker with Address Ranking - FIXED VERSION
Tracks TWAP orders and classifies traders by holder rank
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
from address_tracker import AddressRankTracker

# Get logger for this module
logger = get_module_logger(__name__)


class SimpleTWAPBot:
    """TWAP tracker with integrated address ranking"""

    def __init__(self, config: dict):
        logger.info("Initializing TWAP Tracker with Address Ranking")

        self.config = config
        self.running = False
        self.symbols = config.get('symbols', ['HYPE'])

        # Core components
        self.hypurr_client = HypurrScanClient(config.get('hypurr_data', {}))
        self.json_logger = SimpleJsonLogger(config.get('json_logging', {}))

        # Address tracker
        address_config = config.get('address_tracking', {})
        self.address_tracker = AddressRankTracker(
            self.hypurr_client,
            address_config
        )

        # Configuration for rank updates
        self.rank_update_interval = address_config.get('rank_update_interval', 10)  # Every N loops
        self.max_rank_updates_per_batch = address_config.get('max_updates_per_batch', 5)  # Rate limiting

        # One TWAP tracker per symbol
        # ⭐ FIXED: Pass rank tracker to TWAPStateTracker for whale detection
        self.trackers = {}
        for symbol in self.symbols:
            self.trackers[symbol] = TWAPStateTracker(
                symbol,
                json_logger=self.json_logger,
                rank_tracker=self.address_tracker  # <-- THIS IS THE KEY CHANGE!
            )

        # Setup shutdown handler
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        logger.info(f"TWAP Tracker initialized for: {', '.join(self.symbols)}")
        logger.info("✅ Rank tracker integration enabled")

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
                        # This will now automatically use rank data for whale detection
                        self.trackers[symbol].update(twap_orders)

                        # Update address tracker (add/update addresses from TWAPs)
                        snapshot = self.trackers[symbol].current_snapshot
                        if snapshot:
                            # Add addresses without fetching ranks (fast)
                            self.address_tracker.update_from_snapshot(snapshot, fetch_ranks=False)

                            # Log any whale/dolphin activity
                            self.address_tracker.log_whale_activity(snapshot)
                    else:
                        logger.warning(f"No TWAP orders found for {symbol}")

                # Periodically fetch ranks for new/unknown addresses
                if loop_count % self.rank_update_interval == 0:
                    logger.info("")
                    logger.info("=" * 80)
                    logger.info(f"Periodic Address Rank Update (every {self.rank_update_interval} loops)")
                    logger.info("=" * 80)

                    self.address_tracker.batch_update_ranks(
                        max_addresses=self.max_rank_updates_per_batch
                    )
                    self.address_tracker.log_summary()

                    # Log top traders
                    self.address_tracker.log_top_traders(limit=5, by='rank')

                # Wait 60 seconds
                logger.info("Waiting 60 seconds...")
                time.sleep(60)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error in loop: {e}")
                logger.exception(e)  # Log full traceback
                time.sleep(60)

        # Final report on shutdown
        logger.info("")
        logger.info("=" * 80)
        logger.info("Final Address Classification Report")
        logger.info("=" * 80)
        self.address_tracker.log_summary()
        self.address_tracker.export_report()

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
            'address_tracking': {
                'enabled': True,
                'data_file': 'address_ranks.json',
                'rank_update_interval': 10,
                'max_updates_per_batch': 5
            }
        }
    except Exception as e:
        logger.error(f"Config error: {e}")
        return {'hypurr_data': {}}


def main():
    """Main entry point"""
    print("TWAP State Tracker with Address Ranking")
    print("=" * 50)
    print("Tracks TWAP orders and classifies traders")
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