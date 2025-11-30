#!/usr/bin/env python3
"""
All Coins TWAP State Tracker
=============================
Tracks TWAP order states and changes for ALL coins on Hyperliquid.

Single tracker instance managing multiple coins with:
- Per-coin state tracking (current/previous snapshots)
- Consolidated logging (all coins in one log)
- Daily JSON files (all coins, append mode)
- Unified address history (all coins, one file)
- Full depth tracking (new/completed/canceled orders, state changes)
- Progress tracking (elapsed time, progress %, time remaining)

LOGGING STRATEGY:
- DEBUG: Individual order tracking, address discoveries, file operations
- INFO: Snapshot summaries, significant state changes, new/completed orders
- WARNING: Canceled orders, status changes (unusual events)
- ERROR: File operation failures, data errors
"""
import logging
import json
from datetime import datetime, date
from typing import List, Dict, Optional, Set
from pathlib import Path

# Import models
from api_client.models import TWAPOrder, TWAPSnapshot

logger = logging.getLogger(__name__)


def format_size(size: float) -> str:
    """Format size with appropriate precision based on magnitude"""
    if size < 1:
        return f"{size:>10.4f}"
    elif size < 10:
        return f"{size:>10.2f}"
    else:
        return f"{size:>10,.0f}"


def format_usd_pressure(size: float, duration_hours: float, price: float) -> str:
    """Calculate and format USD pressure per minute"""
    if duration_hours <= 0 or price is None:
        return ""

    duration_minutes = duration_hours * 60
    usd_per_min = (size / duration_minutes) * price

    if usd_per_min >= 1000:
        return f"${usd_per_min:>8,.0f}/min"
    else:
        return f"${usd_per_min:>8.2f}/min"


def format_progress(order) -> str:
    """
    Format elapsed/progress/remaining for an order.

    Handles both TWAPOrder objects and dicts.

    Returns formats like:
    - "45m/210m (21%)" - full info
    - "21%" - progress only
    - "45m elapsed" - elapsed only
    - "" - no progress info available
    """
    # Handle both TWAPOrder objects and dicts
    if isinstance(order, dict):
        elapsed = order.get('elapsed_minutes')
        remaining = order.get('time_remaining_minutes')
        progress = order.get('progress_percent')
        duration = order.get('duration_minutes', int(order.get('duration_hours', 0) * 60))
    else:
        elapsed = order.elapsed_minutes
        remaining = order.time_remaining_minutes
        progress = order.progress_percent
        duration = order.duration_minutes

    if elapsed is None:
        return ""

    if remaining is not None and progress is not None:
        return f"{elapsed}m/{duration}m ({progress:.0f}%)"
    elif progress is not None:
        return f"{progress:.0f}%"
    else:
        return f"{elapsed}m elapsed"


class CoinState:
    """State container for a single coin"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.update_count = 0
        self.current_snapshot: Optional[TWAPSnapshot] = None
        self.previous_snapshot: Optional[TWAPSnapshot] = None


class AllCoinsStateTracker:
    """Tracks TWAP order states for ALL coins on Hyperliquid"""

    def __init__(self, json_logger=None, exclude_coins: List[str] = None):
        """
        Initialize the all-coins tracker.

        Args:
            json_logger: Optional JSON logger for detailed output
            exclude_coins: List of coins to exclude (e.g., ['HYPE'] if tracked separately)
        """
        self.json_logger = json_logger
        self.exclude_coins = set(exclude_coins or [])

        # Track state per coin (Option A: Dict of state objects)
        self.coin_states: Dict[str, CoinState] = {}

        # Global update counter
        self.global_update_count = 0

        # History tracking - ALL addresses across ALL coins
        self.all_addresses_seen: Set[str] = set()
        self.address_history_file = Path('address_list.json')
        self._load_address_history()

        # Create output directory
        Path('twap_snapshots').mkdir(exist_ok=True)

        logger.info("=" * 70)
        logger.info("All Coins TWAP State Tracker Initialized")
        if self.exclude_coins:
            logger.info(f"Excluding coins: {', '.join(sorted(self.exclude_coins))}")
        logger.info("=" * 70)

    # U-prefix tokens that are NOT spot derivatives
    U_PREFIX_EXCEPTIONS = {'USDC', 'USDT', 'USUAL', 'UNI', 'UMA', 'USTC', 'UP'}

    def _get_price(self, symbol: str, prices: Dict[str, float]) -> Optional[float]:
        """
        Get price for symbol, resolving U-prefix spot derivatives to underlying.

        UBTC -> looks up BTC price
        USOL -> looks up SOL price
        USDC -> looks up USDC (not a derivative)
        """
        # Direct lookup first
        if symbol in prices:
            return prices[symbol]

        # Try U-prefix resolution
        if symbol.startswith('U') and len(symbol) > 1 and symbol not in self.U_PREFIX_EXCEPTIONS:
            underlying = symbol[1:]  # UBTC -> BTC
            if underlying in prices:
                return prices[underlying]

        return None

    def _load_address_history(self):
        """Load existing address history"""
        if self.address_history_file.exists():
            try:
                with open(self.address_history_file, 'r') as f:
                    data = json.load(f)

                    # Handle different formats
                    if isinstance(data, dict):
                        self.all_addresses_seen = set(data.keys())
                    elif isinstance(data, list):
                        self.all_addresses_seen = set(data)
                    else:
                        self.all_addresses_seen = set(data.get('addresses', []))

                logger.info(f"Loaded {len(self.all_addresses_seen)} addresses from history")
            except Exception as e:
                logger.error(f"Error loading address history: {e}")
                logger.exception(e)
        else:
            logger.debug("No existing address history file found, will create new one")

    def _save_address_history(self):
        """Save address history to file as dictionary"""
        try:
            logger.debug(f"Saving {len(self.all_addresses_seen)} addresses to history")

            # Create dictionary with addresses as keys
            addresses_dict = {address: {} for address in sorted(self.all_addresses_seen)}

            # Write to file
            with open(self.address_history_file, 'w') as f:
                json.dump(addresses_dict, f, indent=2)

            # Verify
            if self.address_history_file.exists():
                file_size = self.address_history_file.stat().st_size
                logger.debug(f"Address history saved: {file_size} bytes")
            else:
                logger.error("File was not created after write attempt!")

        except Exception as e:
            logger.error(f"Error saving address history: {e}")
            logger.exception(e)

    def _detect_changes(self, symbol: str, raw_changes: Dict) -> Dict:
        """
        Process raw changes to properly separate completed/canceled orders.

        API behavior:
        - Completed orders disappear instantly from API
        - Canceled orders linger in API with status='canceled' then eventually disappear

        Detection logic:
        - Order disappeared + was active/running → completed (just finished)
        - Order disappeared + was canceled/error → ignore (already reported, just cleanup)
        - Status changed to canceled/error → canceled (first time seeing cancellation)
        - Status changed FROM canceled/error → ignore (cleanup transition like canceled→removed)

        Returns all orders as TWAPOrder objects (not dicts) for consistency.
        """
        completed_orders = []
        canceled_orders = []

        # Track order hashes to filter from status_changes
        handled_hashes = set()

        # From orders that disappeared from API (these are TWAPOrder objects from PREVIOUS snapshot)
        for order in raw_changes.get('completed_orders', []):
            if order.status in ['canceled', 'error']:
                # Already-canceled order finally cleaned up from API - ignore, we already reported it
                handled_hashes.add(order.order_hash)
                continue
            else:
                # Active order disappeared = completed
                handled_hashes.add(order.order_hash)
                completed_orders.append(order)

        # From status changes (orders still in API but status changed)
        # Status change to canceled/error = first time we see cancellation
        for change in raw_changes.get('status_changes', []):
            if change.get('new_status') in ['canceled', 'error']:
                if change['order_hash'] not in handled_hashes:
                    handled_hashes.add(change['order_hash'])
                    canceled_orders.append({
                        'full_address': change['address'],
                        'order_hash': change['order_hash'],
                        'side': change['side'],
                        'size': change['size'],
                        'product_type': change['product_type'],
                        'duration_hours': change['duration_hours'],
                        'duration_minutes': int(change['duration_hours'] * 60),
                        'status': change['new_status'],
                        'elapsed_minutes': change.get('elapsed_minutes'),
                        'progress_percent': change.get('progress_percent'),
                        'time_remaining_minutes': change.get('time_remaining_minutes')
                    })

        # Filter status_changes to exclude:
        # 1. Orders already handled (moved to completed/canceled)
        # 2. Cleanup transitions (old_status was already canceled/error)
        filtered_status_changes = [
            change for change in raw_changes.get('status_changes', [])
            if change['order_hash'] not in handled_hashes
               and change.get('old_status') not in ['canceled', 'error']
        ]

        return {
            'new_orders': raw_changes.get('new_orders', []),
            'completed_orders': completed_orders,
            'canceled_orders': canceled_orders,
            'status_changes': filtered_status_changes
        }

    def update(self, all_coins_data: Dict[str, List[Dict]], prices: Dict[str, float] = None):
        """
        Update with new TWAP data for all coins.

        Args:
            all_coins_data: Dict mapping coin_symbol -> list of raw TWAP orders
                Example: {
                    'BTC': [{order1}, {order2}],
                    'ETH': [{order3}],
                    'HYPE': [{order4}, {order5}]
                }
            prices: Optional dict mapping coin_symbol -> current USD price
                Example: {'BTC': 97234.5, 'ETH': 3521.2, 'HYPE': 33.8}
        """
        prices = prices or {}
        self.global_update_count += 1

        logger.info("\n" + "=" * 70)
        logger.info(f"ALL COINS UPDATE #{self.global_update_count}")
        logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 70)

        # Track coins with active orders
        coins_with_orders = []
        total_orders = 0
        total_active_orders = 0

        # Update each coin
        for symbol, raw_orders in all_coins_data.items():
            # Skip excluded coins
            if symbol in self.exclude_coins:
                logger.debug(f"Skipping excluded coin: {symbol}")
                continue

            if not raw_orders:
                continue

            # Get or create coin state
            if symbol not in self.coin_states:
                self.coin_states[symbol] = CoinState(symbol)
                logger.debug(f"Created new state for: {symbol}")

            coin_state = self.coin_states[symbol]
            coin_state.update_count += 1

            # Create new snapshot
            new_snapshot = TWAPSnapshot.from_hypurr_data(
                raw_orders,
                symbol,
                coin_state.update_count,
                current_price=self._get_price(symbol, prices)
            )

            # DEBUG: Individual order tracking
            for order in new_snapshot.orders:
                logger.debug(
                    f"[{symbol}] Order: {order.display_address} - "
                    f"Hash: {order.order_hash[:16]}... - Size: {order.size}"
                )

            # Update history
            coin_state.previous_snapshot = coin_state.current_snapshot
            coin_state.current_snapshot = new_snapshot

            # Track addresses
            addresses_before = len(self.all_addresses_seen)
            self.all_addresses_seen.update(new_snapshot.unique_addresses)

            # DEBUG: New addresses discovered
            if len(self.all_addresses_seen) > addresses_before:
                new_count = len(self.all_addresses_seen) - addresses_before
                logger.debug(
                    f"[{symbol}] Discovered {new_count} new address(es). "
                    f"Total: {len(self.all_addresses_seen)}"
                )

            # Track for summary
            if new_snapshot.active_orders:
                coins_with_orders.append(symbol)
                total_orders += new_snapshot.total_orders
                total_active_orders += len(new_snapshot.active_orders)

            # Detect changes
            if coin_state.previous_snapshot:
                raw_changes = new_snapshot.compare_with(coin_state.previous_snapshot)
                changes = self._detect_changes(symbol, raw_changes)
            else:
                changes = {
                    'new_orders': new_snapshot.active_orders,
                    'completed_orders': [],
                    'canceled_orders': [],
                    'status_changes': []
                }

            # Log this coin's snapshot
            self._log_coin_snapshot(symbol, new_snapshot, changes)

        # Always save address history
        self._save_address_history()

        # Log global summary
        logger.info("=" * 70)
        logger.info(f"📊 GLOBAL SUMMARY")
        logger.info(f"Coins with active orders: {len(coins_with_orders)}")
        logger.info(f"Total orders tracked: {total_orders} ({total_active_orders} active)")
        logger.info(f"Total addresses seen: {len(self.all_addresses_seen)}")
        logger.info(f"Active coins: {', '.join(sorted(coins_with_orders)[:20])}")
        if len(coins_with_orders) > 20:
            logger.info(f"... and {len(coins_with_orders) - 20} more")
        logger.info("=" * 70)

        # Save to JSON if logger available
        if self.json_logger:
            self._save_all_to_json()

    def _log_coin_snapshot(self, symbol: str, snapshot: TWAPSnapshot, changes: Dict):
        """Log snapshot and changes for a single coin"""

        stats = snapshot.get_stats()

        # INFO: High-level per-coin summary
        logger.info(f"\n{'─' * 70}")
        logger.info(f"💰 {symbol}")
        logger.info(f"{'─' * 70}")

        # Separate orders by market
        spot_orders = [o for o in snapshot.orders if o.product_type == 'SPOT']
        perp_orders = [o for o in snapshot.orders if o.product_type == 'PERP']

        # Sort by size
        spot_orders.sort(key=lambda o: o.size, reverse=True)
        perp_orders.sort(key=lambda o: o.size, reverse=True)

        # Count active (once, used for both summary and sections)
        active_spot = sum(1 for o in spot_orders if o.is_active)
        active_perp = sum(1 for o in perp_orders if o.is_active)

        if snapshot.current_price:
            # Build market breakdown string - only show markets that have orders
            if active_spot > 0 and active_perp > 0:
                market_breakdown = f"({active_spot} SPOT, {active_perp} PERP)"
            elif active_spot > 0:
                market_breakdown = "SPOT"
            elif active_perp > 0:
                market_breakdown = "PERP"
            else:
                market_breakdown = ""

            logger.info(
                f"Price: ${snapshot.current_price:,.4f} | "
                f"Orders: {stats['total_orders']} total | "
                f"Active: {stats['active_orders']} {market_breakdown}"
            )

            # Calculate USD pressure per minute
            price = snapshot.current_price

            spot_buy_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in spot_orders if o.is_active and o.is_buy_side and o.duration_hours > 0
            )
            spot_sell_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in spot_orders if o.is_active and o.is_sell_side and o.duration_hours > 0
            )
            perp_buy_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in perp_orders if o.is_active and o.is_buy_side and o.duration_hours > 0
            )
            perp_sell_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in perp_orders if o.is_active and o.is_sell_side and o.duration_hours > 0
            )

            total_buy_usd = spot_buy_usd + perp_buy_usd
            total_sell_usd = spot_sell_usd + perp_sell_usd

            # Only show pressure lines for markets with active orders
            if active_spot > 0:
                logger.info(
                    f"💵 SPOT Pressure: Buy ${spot_buy_usd:,.0f}/min | "
                    f"Sell ${spot_sell_usd:,.0f}/min | Net ${spot_buy_usd - spot_sell_usd:+,.0f}/min"
                )
            if active_perp > 0:
                logger.info(
                    f"⚡ PERP Pressure: Buy ${perp_buy_usd:,.0f}/min | "
                    f"Sell ${perp_sell_usd:,.0f}/min | Net ${perp_buy_usd - perp_sell_usd:+,.0f}/min"
                )
            # Only show TOTAL if both markets have activity
            if active_spot > 0 and active_perp > 0:
                logger.info(
                    f"📊 TOTAL Pressure: Buy ${total_buy_usd:,.0f}/min | "
                    f"Sell ${total_sell_usd:,.0f}/min | Net ${total_buy_usd - total_sell_usd:+,.0f}/min"
                )
        else:
            # Build market breakdown string - only show markets that have orders
            if active_spot > 0 and active_perp > 0:
                market_breakdown = f"({active_spot} SPOT, {active_perp} PERP)"
            elif active_spot > 0:
                market_breakdown = "SPOT"
            elif active_perp > 0:
                market_breakdown = "PERP"
            else:
                market_breakdown = ""

            logger.info(
                f"Orders: {stats['total_orders']} total | "
                f"Active: {stats['active_orders']} {market_breakdown}"
            )

        # SPOT MARKET
        if active_spot > 0:
            logger.info(f"  💵 SPOT - {active_spot} Active:")
            for order in spot_orders:
                if not order.is_active:
                    continue

                side_emoji = "🟢" if order.is_buy_side else "🔴"

                # Build progress string
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"

                if snapshot.current_price:
                    usd_pressure = format_usd_pressure(order.size, order.duration_hours, snapshot.current_price)
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str} | {usd_pressure}"
                    )
                else:
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str}"
                    )

        # PERP MARKET
        if active_perp > 0:
            logger.info(f"  ⚡ PERP - {active_perp} Active:")
            for order in perp_orders:
                if not order.is_active:
                    continue

                side_emoji = "🟢" if order.is_buy_side else "🔴"

                # Build progress string
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"

                if snapshot.current_price:
                    usd_pressure = format_usd_pressure(order.size, order.duration_hours, snapshot.current_price)
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str} | {usd_pressure}"
                    )
                else:
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str}"
                    )

        # Log any changes (new/completed/canceled orders)
        self._log_coin_changes(symbol, changes)

    def _log_coin_changes(self, symbol: str, changes: Dict):
        """Log detected changes for a coin"""

        # INFO: New orders
        if changes['new_orders']:
            logger.info(f"  🆕 [{symbol}] New orders: {len(changes['new_orders'])}")
            for order in changes['new_orders']:
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"
                logger.info(
                    f"    NEW: {order.full_address} {order.side:4s} {format_size(order.size)} "
                    f"{order.product_type} {order.duration_hours:.1f}h{progress_str}"
                )

        # INFO: Completed orders
        completed_orders = changes.get('completed_orders', [])
        if completed_orders:
            logger.info(f"  ✅ [{symbol}] Completed: {len(completed_orders)}")
            for order in completed_orders:
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"
                logger.info(
                    f"    COMPLETED: {order.full_address} {order.side:4s} {format_size(order.size)} "
                    f"{order.product_type} {order.duration_hours:.1f}h{progress_str}"
                )

        # WARNING: Canceled orders
        canceled_orders = changes.get('canceled_orders', [])
        if canceled_orders:
            logger.warning(f"  ❌ [{symbol}] Canceled: {len(canceled_orders)}")
            for order in canceled_orders:
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"

                # Handle both TWAPOrder objects and dicts
                if isinstance(order, dict):
                    addr = order['full_address']
                    side = order['side']
                    size = order['size']
                    ptype = order['product_type']
                    dur = order['duration_hours']
                else:
                    addr = order.full_address
                    side = order.side
                    size = order.size
                    ptype = order.product_type
                    dur = order.duration_hours

                logger.warning(
                    f"    CANCELED: {addr} {side:4s} {format_size(size)} "
                    f"{ptype} {dur:.1f}h{progress_str}"
                )

        # WARNING: Status changes
        if changes['status_changes']:
            logger.warning(f"  🔄 [{symbol}] Status changes: {len(changes['status_changes'])}")
            for change in changes['status_changes']:
                elapsed = change.get('elapsed_minutes')
                progress = change.get('progress_percent')
                if elapsed is not None and progress is not None:
                    progress_str = f" | {elapsed}m ({progress:.0f}%)"
                else:
                    progress_str = ""
                logger.warning(
                    f"    STATUS: {change['address']} {change['side']:4s} {format_size(change['size'])} "
                    f"{change['product_type']} {change['old_status']} → {change['new_status']}{progress_str}"
                )

    def _save_all_to_json(self):
        """Save each coin's data to its own daily JSON file"""
        try:
            # Ensure json_logs directory exists
            base_dir = Path('allcoins_json_logs')
            base_dir.mkdir(exist_ok=True)

            for symbol, coin_state in self.coin_states.items():
                if not coin_state.current_snapshot:
                    continue

                snapshot = coin_state.current_snapshot
                # Create per-coin subdirectory
                safe_coin = symbol.replace(":", "_")  # xyz:TSLA -> xyz_TSLA for filesystem
                coin_dir = base_dir / safe_coin
                coin_dir.mkdir(exist_ok=True)

                # Detect changes
                if coin_state.previous_snapshot:
                    raw_changes = snapshot.compare_with(coin_state.previous_snapshot)
                    changes = self._detect_changes(symbol, raw_changes)
                else:
                    changes = {
                        'new_orders': snapshot.active_orders,
                        'completed_orders': [],
                        'canceled_orders': [],
                        'status_changes': []
                    }

                # Convert orders to dicts - consistent field ordering with progress tracking
                def order_to_dict(order):
                    if isinstance(order, dict):
                        return {
                            'address': order.get('full_address', order.get('address', '')),
                            'order_hash': order.get('order_hash', ''),
                            'side': order.get('side', ''),
                            'size': round(order.get('size', 0), 2),
                            'product_type': order.get('product_type', ''),
                            'duration_hours': round(order.get('duration_hours', 0), 2),
                            'duration_minutes': order.get('duration_minutes', int(order.get('duration_hours', 0) * 60)),
                            'status': order.get('status', 'unknown'),
                            'is_active': order.get('is_active', False),
                            'elapsed_minutes': order.get('elapsed_minutes'),
                            'progress_percent': round(order.get('progress_percent'), 1) if order.get(
                                'progress_percent') is not None else None,
                            'time_remaining_minutes': order.get('time_remaining_minutes')
                        }
                    # TWAPOrder object
                    return {
                        'address': order.full_address,
                        'order_hash': order.order_hash,
                        'side': order.side,
                        'size': round(order.size, 2),
                        'product_type': order.product_type,
                        'duration_hours': round(order.duration_hours, 2),
                        'duration_minutes': order.duration_minutes,
                        'status': order.status,
                        'is_active': order.is_active,
                        'elapsed_minutes': order.elapsed_minutes,
                        'progress_percent': round(order.progress_percent,
                                                  1) if order.progress_percent is not None else None,
                        'time_remaining_minutes': order.time_remaining_minutes
                    }

                # Get stats
                stats = snapshot.get_stats()
                active = snapshot.active_orders

                # Build update data - matching HYPE tracker format with progress tracking
                update_data = {
                    'timestamp': datetime.now().isoformat(),
                    'symbol': symbol,
                    'update_number': coin_state.update_count,
                    'current_price': snapshot.current_price,
                    'summary': {
                        'total_orders': stats['total_orders'],
                        'active_orders': stats['active_orders'],
                        'buy_volume': round(stats['buy_volume'], 2),
                        'sell_volume': round(stats['sell_volume'], 2),
                        'net_flow': round(stats['net_flow'], 2),
                        'spot_buy_volume': round(
                            sum(o.size for o in active if o.is_buy_side and o.product_type == 'SPOT'), 2),
                        'spot_sell_volume': round(
                            sum(o.size for o in active if o.is_sell_side and o.product_type == 'SPOT'), 2),
                        'spot_buy_pressure': round(
                            sum(o.get_execution_rate() for o in active if o.is_buy_side and o.product_type == 'SPOT'),
                            2),
                        'spot_sell_pressure': round(
                            sum(o.get_execution_rate() for o in active if o.is_sell_side and o.product_type == 'SPOT'),
                            2),
                        'perp_buy_volume': round(
                            sum(o.size for o in active if o.is_buy_side and o.product_type == 'PERP'), 2),
                        'perp_sell_volume': round(
                            sum(o.size for o in active if o.is_sell_side and o.product_type == 'PERP'), 2),
                        'perp_buy_pressure': round(
                            sum(o.get_execution_rate() for o in active if o.is_buy_side and o.product_type == 'PERP'),
                            2),
                        'perp_sell_pressure': round(
                            sum(o.get_execution_rate() for o in active if o.is_sell_side and o.product_type == 'PERP'),
                            2),
                        'buy_pressure_per_min': round(stats['buy_pressure_per_min'], 2),
                        'sell_pressure_per_min': round(stats['sell_pressure_per_min'], 2),
                        'net_pressure_per_min': round(stats['net_pressure_per_min'], 2),
                        'whale_orders': stats['whale_orders'],
                        'unique_addresses': stats['unique_addresses']
                    },
                    'events': {
                        'new_orders': len(changes.get('new_orders', [])),
                        'completed_orders': len(changes.get('completed_orders', [])),
                        'canceled_orders': len(changes.get('canceled_orders', [])),
                        'status_changes': len(changes.get('status_changes', []))
                    },
                    'active_orders': [order_to_dict(o) for o in snapshot.active_orders],
                    'new_orders': [
                        {
                            'address': o.full_address,
                            'order_hash': o.order_hash,
                            'side': o.side,
                            'size': round(o.size, 2),
                            'product_type': o.product_type,
                            'duration_hours': round(o.duration_hours, 2),
                            'duration_minutes': o.duration_minutes,
                            'elapsed_minutes': o.elapsed_minutes,
                            'progress_percent': round(o.progress_percent,
                                                      1) if o.progress_percent is not None else None,
                            'time_remaining_minutes': o.time_remaining_minutes
                        }
                        for o in changes.get('new_orders', [])
                    ],
                    'completed_orders': [
                        {
                            'address': o.full_address,
                            'order_hash': o.order_hash,
                            'side': o.side,
                            'size': round(o.size, 2),
                            'product_type': o.product_type,
                            'duration_hours': round(o.duration_hours, 2),
                            'duration_minutes': o.duration_minutes,
                            'status': 'completed',
                            'elapsed_minutes': o.elapsed_minutes,
                            'progress_percent': round(o.progress_percent,
                                                      1) if o.progress_percent is not None else None,
                            'time_remaining_minutes': o.time_remaining_minutes
                        }
                        for o in changes.get('completed_orders', [])
                    ],
                    'canceled_orders': [
                        order_to_dict(o) for o in changes.get('canceled_orders', [])
                    ],
                    'status_changes': [
                        {
                            **sc,
                            'size': round(sc['size'], 2),
                            'progress_percent': round(sc['progress_percent'], 1) if sc.get(
                                'progress_percent') is not None else None
                        }
                        for sc in changes.get('status_changes', [])
                    ]
                }

                # Per-coin daily file in coin's folder
                today = datetime.now().strftime('%Y%m%d')
                filename = coin_dir / f"{safe_coin}_{today}.jsonl"

                with open(filename, 'a', encoding='utf-8') as f:
                    json.dump(update_data, f, separators=(',', ':'))
                    f.write('\n')

                logger.debug(f"Saved {symbol} snapshot #{coin_state.update_count} to {filename}")

        except Exception as e:
            logger.error(f"Error saving to JSON: {e}")
            logger.exception(e)

    def get_current_stats(self) -> Dict:
        """Get current statistics across all coins"""
        total_orders = 0
        total_active = 0
        total_volume = 0
        coins_with_activity = 0

        for coin_state in self.coin_states.values():
            if coin_state.current_snapshot:
                snapshot = coin_state.current_snapshot
                total_orders += snapshot.total_orders
                total_active += len(snapshot.active_orders)
                total_volume += snapshot.total_volume

                if snapshot.active_orders:
                    coins_with_activity += 1

        return {
            'global_update_count': self.global_update_count,
            'total_coins_tracked': len(self.coin_states),
            'coins_with_activity': coins_with_activity,
            'total_orders': total_orders,
            'total_active_orders': total_active,
            'total_volume': total_volume,
            'all_time_addresses': len(self.all_addresses_seen),
            'excluded_coins': list(self.exclude_coins)
        }

    def get_coin_stats(self, symbol: str) -> Optional[Dict]:
        """Get stats for a specific coin"""
        coin_state = self.coin_states.get(symbol)
        if not coin_state or not coin_state.current_snapshot:
            return None

        stats = coin_state.current_snapshot.get_stats()
        stats['update_count'] = coin_state.update_count
        return stats

    def get_address_history(self) -> Dict:
        """Get address history summary"""
        return {
            'total_addresses': len(self.all_addresses_seen),
            'addresses': sorted(list(self.all_addresses_seen))
        }

    def get_active_coins(self) -> List[str]:
        """Get list of coins with active orders"""
        active = []
        for symbol, coin_state in self.coin_states.items():
            if coin_state.current_snapshot and coin_state.current_snapshot.active_orders:
                active.append(symbol)
        return sorted(active)