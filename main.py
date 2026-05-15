#!/usr/bin/env python3
"""
TWAP State Tracker - All Coins
==============================
Tracks TWAP orders for ALL coins on Hyperliquid.
Single fetch, single tracker, 1-minute snapshots.

Architecture:
- TWAP tracking: Event-driven portfolio snapshots (WhaleMetricsManager)
- Market data: Per-minute OI, funding, prices (MarketDataTracker)
- Liquidation tracking: Tiered whale fetching (LiquidationTracker)
- Event detection: VIP + Tier1 only (WhaleEventDetector)

Tier System:
- VIP: Hand-picked addresses (every 1 min)
- Tier 1: $5M+ positions (every 1 min)
- Tier 2: $1M-5M positions (every 5 min)
- Tier 3: $500K-1M positions (every 15 min)
- Tier 4: $250K-500K positions (every 30 min)
- Tier 5: $100K-250K positions (every 60 min)
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
from trackers.liquidation_tracker import LiquidationTracker
from trackers.tier_manager import TierManager
from trackers.whale_event_detector import WhaleEventDetector
from trackers.fees_collector import HypurrscanFeesCollector
from coin_registry import init_dynamic_registry
from storage import SQLiteBackend
from trackers.whale_discovery import WhaleDiscovery, TokenFilter
from trackers.whale_state_collector import WhaleStateCollector

from generators.daily_summary import generate_daily_summaries
from telegram.telegram_alerter import TelegramAlerter

logger = get_module_logger(__name__)

# Timing - single interval for everything
FETCH_INTERVAL = 60  # All coins every 60 seconds

# Whale snapshot settings (for event-driven TWAP snapshots)
WHALE_MIN_PORTFOLIO = 50000  # Minimum portfolio value to snapshot


class TWAPBot:
    """TWAP tracker for all coins on Hyperliquid"""

    def __init__(self, config: dict):
        logger.info("Initializing TWAP Tracker")

        self.config = config
        self.running = False
        self.db_path = Path('data/twap.db')

        # Event-driven snapshot queues (for TWAP start/end)
        self._pending_order_starts: list = []
        self._pending_order_ends: list = []

        # Skip first cycle (existing orders appear as "new" on startup)
        self._first_cycle = True

        # Stats for event-driven snapshots
        self._snapshot_stats = {
            "start_snapshots": 0,
            "end_snapshots": 0,
            "below_threshold": 0,
            "skipped_warmup": 0,
            "errors": 0
        }

        # HypurrScan client for TWAP data
        self.hypurr_client = HypurrScanClient(config.get('hypurr_data', {}))

        # All Coins Tracker (TWAP order tracking)
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

            # Wire up event-driven callbacks for TWAP tracking
            self.tracker.on_order_start = self._on_order_start
            self.tracker.on_order_end = self._on_order_end

            # Initialize storage
            self.storage = SQLiteBackend(self.db_path)

            # =================================================================
            # NEW TIERED ARCHITECTURE
            # =================================================================

            # Tier Manager - controls which addresses to fetch each cycle
            self.tier_manager = TierManager(self.storage)

            # Whale Event Detector - detects position/margin changes for VIP + Tier1
            self.event_detector = WhaleEventDetector(self.tier_manager)

            # Market Data Tracker - lightweight, just OI/funding/prices (1 API call)
            self.market_tracker = MarketDataTracker(
                self.hyperliquid_client,
                self.storage,
                config=hyperliquid_config.get('market_data', {})
            )

            # Liquidation Tracker - tiered whale fetching + liquidation snapshots
            self.liquidation_tracker = LiquidationTracker(
                self.storage,
                config=hyperliquid_config.get('liquidation_tracker', {})
            )

            logger.info("Tiered tracking architecture initialized")

            # =================================================================
            # WHALE DISCOVERY — event-driven address evaluation + registration
            # =================================================================
            metrics_config = hyperliquid_config.get('metrics_collection', {})

            self.token_filter = TokenFilter(metrics_config)

            self.whale_discovery = WhaleDiscovery(
                self.hyperliquid_client,
                self.storage,
                self.token_filter,
                config=metrics_config,
            )

            # Collector owns all snapshot persistence. Discovery hands it a
            # WhaleState (already fetched); Collector writes the five tables
            # without re-fetching. Cycle-driven collection lands in Step 3.
            self.collector = WhaleStateCollector(
                self.hyperliquid_client,
                self.storage,
                self.tier_manager,
                self.token_filter,
                config=metrics_config,
            )

            # =================================================================

            # Hypurrscan platform-wide fees collector
            self.fees_collector = HypurrscanFeesCollector(
                self.hypurr_client,
                self.storage,
                config=config.get('hypurr_fees', {})
            )

            logger.info("Tiered tracking architecture initialized")

        else:
            self.hyperliquid_client = None
            self.storage = None
            self.tier_manager = None
            self.event_detector = None
            self.market_tracker = None
            self.liquidation_tracker = None
            self.fees_collector = None
            self.token_filter = None
            self.whale_discovery = None
            self.collector = None
            logger.info("Hyperliquid client disabled")

        # Shutdown handler
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        # Telegram alerter for system notifications
        self.alerter = None
        #try:
            #self.alerter = TelegramAlerter()
            #logger.info("Telegram alerter initialized")
        #except ValueError as e:
            #logger.warning(f"Telegram alerter disabled: {e}")

        logger.info("TWAP Tracker initialized (tiered liquidation tracking)")

    def _on_order_start(self, address: str, symbol: str, order: dict):
        """Queue order start for async processing"""
        self._pending_order_starts.append((address, symbol, order))
        logger.debug(f"Queued order START: {address[:10]}... {symbol} {order.get('side')}")

    def _on_order_end(self, address: str, symbol: str, order: dict, end_type: str):
        """Queue order end for async processing"""
        self._pending_order_ends.append((address, symbol, order, end_type))
        logger.debug(f"Queued order END: {address[:10]}... {symbol} ({end_type})")

    async def _process_order_events(self):
        """
        Process queued order events.

        Flow:
        - Order START: discovery.evaluate (probes portfolio), discovery.register
          if qualifying, then stub-persist for snapshot writes.
        - Order END: stub-persist if address is already a tracked active whale.

        Discovery owns the "should we track this?" decision. The stub-persist
        wraps WhaleMetricsManager.take_snapshot_async until WhaleStateCollector
        lands. Runtime behavior is identical to before this refactor — the
        same writes happen via the same code path, just routed through the
        new class boundary.
        """
        if not self._pending_order_starts and not self._pending_order_ends:
            return

        starts = self._pending_order_starts.copy()
        ends = self._pending_order_ends.copy()
        self._pending_order_starts.clear()
        self._pending_order_ends.clear()

        # Skip first cycle - existing orders appear as "new" on startup
        if self._first_cycle:
            self._first_cycle = False
            self._snapshot_stats["skipped_warmup"] = len(starts)
            logger.info(
                f"⏭️  Warm-up: Skipping {len(starts)} order starts (existing orders). "
                f"Processing {len(ends)} order ends."
            )
            starts = []

        if not starts and not ends:
            return

        # Dedupe addresses BEFORE API calls
        unique_starts = {}
        for address, symbol, order in starts:
            if address not in unique_starts:
                unique_starts[address] = (symbol, order)

        unique_ends = {}
        for address, symbol, order, end_type in ends:
            if address not in unique_ends:
                unique_ends[address] = (symbol, order, end_type)

        if starts:
            logger.info(
                f"Processing {len(unique_starts)} unique addresses "
                f"(from {len(starts)} order starts), {len(unique_ends)} order ends"
            )
        elif ends:
            logger.info(f"Processing {len(unique_ends)} order ends")

        # Track which addresses we've snapshotted this cycle
        snapshotted_this_cycle = set()

        async with aiohttp.ClientSession() as session:
            # =================================================================
            # ORDER STARTS — Discovery evaluate + register, Collector persist
            # =================================================================
            for address, (symbol, order) in unique_starts.items():
                if address in snapshotted_this_cycle:
                    continue

                try:
                    state = await self.whale_discovery.evaluate(address, session)

                    if state is None:
                        # Either API failed or portfolio < threshold
                        self._snapshot_stats["below_threshold"] += 1
                        continue

                    # Qualifies — register (idempotent) and persist from in-hand state.
                    # No re-fetch: Collector writes from the WhaleState that evaluate
                    # already produced.
                    self.whale_discovery.register(address)
                    success = await self.collector.persist(address, state)

                    if success:
                        snapshotted_this_cycle.add(address)
                        self._snapshot_stats["start_snapshots"] += 1
                        logger.info(
                            f"📸 START: {address[:10]}... ${state.total_value():,.0f} | "
                            f"{symbol} {order.get('side')} {order.get('size'):,.0f} "
                            f"({order.get('duration_minutes')}m)"
                        )
                    else:
                        # Sanity check rejected the write (API failure suspected),
                        # or a real DB error occurred. Collector logged the reason.
                        self._snapshot_stats["errors"] += 1

                except Exception as e:
                    self._snapshot_stats["errors"] += 1
                    logger.error(f"Error processing order start {address[:10]}...: {e}")

            # =================================================================
            # ORDER ENDS — snapshot if already tracked. Goes through Collector
            # (no threshold check — an exiting whale gets their final snapshot
            # even if portfolio drops below $50K mid-close).
            # =================================================================
            active_whales = set(self.storage.get_active_whale_addresses())

            for address, (symbol, order, end_type) in unique_ends.items():
                if address in snapshotted_this_cycle:
                    continue

                try:
                    if address in active_whales:
                        success = await self.collector.fetch_and_persist(
                            address, session
                        )

                        if success:
                            snapshotted_this_cycle.add(address)
                            self._snapshot_stats["end_snapshots"] += 1

                            emoji = "✅" if end_type == "completed" else "❌"
                            logger.info(
                                f"📸 END {emoji}: {address[:10]}... | "
                                f"{symbol} {order.get('side')} {end_type}"
                            )
                        else:
                            self._snapshot_stats["errors"] += 1

                except Exception as e:
                    self._snapshot_stats["errors"] += 1
                    logger.error(f"Error processing order end {address[:10]}...: {e}")

        if snapshotted_this_cycle:
            logger.info(
                f"Event snapshots complete: {len(snapshotted_this_cycle)} addresses | "
                f"Stats: {self._snapshot_stats}"
            )

    def _fetch_all_coins(self):
        """Fetch and process TWAP data for ALL coins - single API call"""
        try:
            logger.debug("Fetching all coins data...")

            whale_data = self.hypurr_client.get_whale_activity(['*'])
            twap_data = whale_data.get('twap_data', {})

            if twap_data:
                logger.debug(f"Received data for {len(twap_data)} coins")

                # Fetch prices
                prices = {}
                if self.hyperliquid_client:
                    # Standard perp + spot prices
                    all_mids = self.hyperliquid_client.get_all_mids()
                    if all_mids:
                        prices = {k: float(v) for k, v in all_mids.items() if not k.startswith('@')}

                    # ============================================================
                    # NEW: HIP-3 prices (xyz, flx, vntl — tokenized stocks etc.)
                    # ============================================================
                    hip3_mids = self.hyperliquid_client.get_hip3_mids()
                    if hip3_mids:
                        hip3_prices = {k: float(v) for k, v in hip3_mids.items()}
                        prices.update(hip3_prices)
                        logger.info(f"Merged {len(hip3_prices)} HIP-3 prices into price feed")
                    # ============================================================

                # Update tracker (fires event callbacks)
                self.tracker.update(twap_data, prices=prices)

                # Clean up stale orders
                cleaned = self.tracker.db.cleanup_stale_orders()
                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} stale orders this cycle")
            else:
                logger.warning("No TWAP data received")
                self._send_alert(
                    "API_WARNING",
                    "HypurrScan returned empty TWAP data",
                    {"endpoint": "twap/*"}
                )

        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            logger.exception(e)
            self._send_alert(
                "API_ERROR",
                f"Failed to fetch TWAP data: {str(e)[:100]}",
                {"exception_type": type(e).__name__}
            )

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
        logger.info(f"Event-driven snapshots: portfolio >= ${WHALE_MIN_PORTFOLIO:,}")
        logger.info("Tiered liquidation tracking: VIP+T1 every min, T2-T5 on schedule")

        # Connect Telegram alerter
        if self.alerter:
            try:
                self.alerter.connect()
                self._send_alert(
                    "STARTUP",
                    "🟢 Whale tracker started (tiered architecture)",
                    {"mode": "tiered", "interval": f"{FETCH_INTERVAL}s"}
                )
            except Exception as e:
                logger.error(f"Failed to connect Telegram: {e}")
                self.alerter = None

        self.running = True
        last_cleanup_date = None

        # Initial tier refresh
        if self.tier_manager:
            logger.info("Performing initial tier refresh...")
            self.tier_manager.refresh_tiers_from_snapshots()

        # One-time platform fees backfill (only runs if table is empty and enabled)
        if self.fees_collector and self.fees_collector.needs_backfill():
            try:
                self.fees_collector.backfill()
            except Exception as e:
                logger.error(f"Fees backfill failed at startup: {e}", exc_info=True)
                # Don't abort startup - collector will retry on next poll

        while self.running:
            try:
                loop_start = time.time()

                # =============================================================
                # CYCLE MANAGEMENT
                # =============================================================

                if self.tier_manager:
                    # Increment cycle (1-60)
                    cycle = self.tier_manager.increment_cycle()

                    # Refresh tiers every 60 cycles (hourly)
                    if self.tier_manager.should_refresh_tiers():
                        logger.info("Hourly tier refresh triggered")
                        self.tier_manager.refresh_tiers_from_snapshots()

                    # Log cycle status every 10 cycles
                    if cycle % 10 == 0:
                        self.tier_manager.log_status()

                # =============================================================
                # TWAP TRACKING (existing event-driven system)
                # =============================================================

                # Fetch TWAP data (triggers on_order_start/on_order_end callbacks)
                self._fetch_all_coins()

                # Process queued order events (portfolio snapshots)
                if self.hyperliquid_enabled:
                    asyncio.run(self._process_order_events())

                # =============================================================
                # PLATFORM FEES POLL (hourly, ~1 HTTP call)
                # =============================================================

                if self.fees_collector and self.tier_manager:
                    current_cycle = self.tier_manager.get_current_cycle()
                    if self.fees_collector.should_poll(current_cycle):
                        self.fees_collector.poll()

                # =============================================================
                # MARKET DATA SNAPSHOT (lightweight - 1 API call)
                # =============================================================

                if self.market_tracker:
                    market_result = self.market_tracker.take_snapshot()
                    prices = market_result.get('prices', {})
                else:
                    prices = {}

                # =============================================================
                # CYCLE-DRIVEN WHALE STATE COLLECTION + ANALYSIS
                # Collector produces whale_states; analyzers consume them.
                # This replaces the LiquidationTracker dual-write path.
                # =============================================================

                if self.collector:
                    whale_states = asyncio.run(
                        self.collector.collect_async(prices=prices)
                    )

                    # Liquidation analysis (writes liquidation_snapshots)
                    if self.liquidation_tracker and whale_states:
                        self.liquidation_tracker.analyze(whale_states, prices)

                    # Event detection (writes whale_events)
                    if self.event_detector and whale_states:
                        events = self.event_detector.detect(whale_states, prices)
                        if events:
                            self.event_detector.log_summary(events)
                            self.storage.save_whale_events(events)
                    # =========================================================
                    # SLOW LADDER (Step 4b) — portfolio/spot/vault per-tier cadence
                    # Fires only at cycle 7 when a tier is due (1/2/4/8h ladder).
                    # Returns 0 on most cycles.
                    # =========================================================
                    asyncio.run(self.collector.collect_slow_async())

                # =============================================================
                # DAILY MAINTENANCE
                # =============================================================

                current_date = datetime.now(timezone.utc).date()
                if last_cleanup_date != current_date:
                    logger.info("Running daily maintenance...")

                    # Database cleanup
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

                # =============================================================
                # TIMING
                # =============================================================

                elapsed = time.time() - loop_start
                sleep_time = max(0.1, FETCH_INTERVAL - elapsed)

                if elapsed > FETCH_INTERVAL:
                    logger.warning(
                        f"Cycle took {elapsed:.1f}s, exceeded {FETCH_INTERVAL}s target"
                    )

                time.sleep(sleep_time)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                logger.exception(e)
                self._send_alert(
                    "LOOP_ERROR",
                    f"Main loop error: {str(e)[:100]}",
                    {"exception_type": type(e).__name__}
                )
                time.sleep(FETCH_INTERVAL)

        logger.info("Tracking stopped")

    def _send_alert(self, error_type: str, message: str, details: dict = None):
        """Send alert via Telegram (fire-and-forget from sync context)"""
        if not self.alerter:
            return
        try:
            asyncio.run(self.alerter.send_alert(error_type, message, details))
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown gracefully"""
        logger.info("Shutdown signal received")
        self.running = False

        if self.alerter:
            try:
                asyncio.run(self.alerter.send_alert(
                    "SHUTDOWN",
                    "🔴 Whale tracker stopped",
                    {"signal": signum}
                ))
                asyncio.run(self.alerter.disconnect())
            except Exception:
                pass

        # Close database connections
        logger.info("Closing database connections...")
        self.tracker.close()
        if self.storage:
            self.storage.close()

        # Log final stats
        self._log_final_stats()

    def _log_final_stats(self):
        """Log final statistics on shutdown"""
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

        # Liquidation tracker stats
        if self.liquidation_tracker:
            liq_stats = self.liquidation_tracker.get_stats()
            logger.info(f"Liquidation snapshots taken: {liq_stats['snapshot_count']}")

        # Fees collector stats
        if self.fees_collector:
            fees_stats = self.fees_collector.get_stats()
            logger.info(f"Fees collector: {fees_stats}")

        # Tier manager stats
        if self.tier_manager:
            tier_stats = self.tier_manager.get_tier_stats()
            logger.info(f"Tier distribution: {tier_stats}")

        # Event detector stats
        if self.event_detector:
            event_stats = self.event_detector.get_stats()
            logger.info(f"Event detector: {event_stats}")

        # Collector stats (Step 3 + 4a + 4b)
        if self.collector:
            collector_stats = self.collector.get_stats()
            logger.info(f"Collector: {collector_stats}")

        # Event-driven snapshot stats
        logger.info(f"Event-driven snapshot stats: {self._snapshot_stats}")

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
    print("TWAP State Tracker - All Coins (Tiered Architecture)")
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