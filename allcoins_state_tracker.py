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

        # Daily JSON file path
        self.current_json_file: Optional[Path] = None
        self._update_json_file_path()

        logger.info("=" * 70)
        logger.info("All Coins TWAP State Tracker Initialized")
        if self.exclude_coins:
            logger.info(f"Excluding coins: {', '.join(sorted(self.exclude_coins))}")
        logger.info("=" * 70)

    def _update_json_file_path(self):
        """Update the JSON file path for the current date"""
        today = date.today()
        self.current_json_file = Path(f'twap_snapshots/all_coins_{today.strftime("%Y-%m-%d")}.jsonl')

        # Create empty file if it doesn't exist (no initial content needed for NDJSON)
        if not self.current_json_file.exists():
            self.current_json_file.touch()  # Just create empty file
            logger.info(f"Created new daily JSON file: {self.current_json_file}")

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

    def update(self, all_coins_data: Dict[str, List[Dict]]):
        """
        Update with new TWAP data for all coins.

        Args:
            all_coins_data: Dict mapping coin_symbol -> list of raw TWAP orders
                Example: {
                    'BTC': [{order1}, {order2}],
                    'ETH': [{order3}],
                    'HYPE': [{order4}, {order5}]
                }
        """
        self.global_update_count += 1

        # Check if we need to rotate to a new daily file
        self._update_json_file_path()

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
                coin_state.update_count
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
                changes = new_snapshot.compare_with(coin_state.previous_snapshot)
            else:
                changes = {
                    'new_orders': new_snapshot.orders,
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
        logger.info(
            f"Orders: {stats['total_orders']} total | "
            f"Active: {stats['active_orders']} ({stats['spot_orders']} SPOT, {stats['perp_orders']} PERP)"
        )

        # Separate orders by market
        spot_orders = [o for o in snapshot.orders if o.product_type == 'SPOT']
        perp_orders = [o for o in snapshot.orders if o.product_type == 'PERP']

        # Sort by size
        spot_orders.sort(key=lambda o: o.size, reverse=True)
        perp_orders.sort(key=lambda o: o.size, reverse=True)

        # Count active
        active_spot = sum(1 for o in spot_orders if o.is_active)
        active_perp = sum(1 for o in perp_orders if o.is_active)

        # SPOT MARKET
        if active_spot > 0:
            logger.info(f"  💵 SPOT - {active_spot} Active:")
            for order in spot_orders:
                if not order.is_active:
                    continue

                side_emoji = "🟢" if order.is_buy_side else "🔴"
                logger.info(
                    f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                    f"{order.size:>10,.0f} | {order.duration_hours:>5.1f}h"
                )

        # PERP MARKET
        if active_perp > 0:
            logger.info(f"  ⚡ PERP - {active_perp} Active:")
            for order in perp_orders:
                if not order.is_active:
                    continue

                side_emoji = "🟢" if order.is_buy_side else "🔴"
                logger.info(
                    f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                    f"{order.size:>10,.0f} | {order.duration_hours:>5.1f}h"
                )

        # Log changes
        self._log_coin_changes(symbol, changes)

    def _log_coin_changes(self, symbol: str, changes: Dict):
        """Log detected changes for a coin"""

        # INFO: New orders
        if changes['new_orders']:
            logger.info(f"  🆕 [{symbol}] New orders: {len(changes['new_orders'])}")
            for order in changes['new_orders']:
                logger.info(
                    f"    NEW: {order.full_address} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.product_type} {order.duration_hours:.1f}h"
                )

        # Separate completed and canceled
        completed_orders = []
        canceled_orders = []

        for order in changes.get('completed_orders', []):
            if order.status == 'canceled':
                canceled_orders.append(order)
            else:
                completed_orders.append(order)

        # INFO: Completed orders
        if completed_orders:
            logger.info(f"  ✅ [{symbol}] Completed: {len(completed_orders)}")
            for order in completed_orders:
                logger.info(
                    f"    COMPLETED: {order.full_address} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.duration_hours:.1f}h (status: {order.status})"
                )

        # WARNING: Canceled orders
        if canceled_orders:
            logger.warning(f"  ❌ [{symbol}] Canceled: {len(canceled_orders)}")
            for order in canceled_orders:
                logger.warning(
                    f"    CANCELED: {order.full_address} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.duration_hours:.1f}h"
                )

        # WARNING: Status changes
        if changes['status_changes']:
            logger.warning(f"  🔄 [{symbol}] Status changes: {len(changes['status_changes'])}")
            for change in changes['status_changes']:
                logger.warning(
                    f"    STATUS: {change['full_address']} {change['side']:4s} {change['size']:>10,.0f} "
                    f"{change['old_status']} → {change['new_status']}"
                )

    def _save_all_to_json(self):
        """Save all coin data to daily JSON file (append mode, one line per update)"""
        try:
            # Build data structure for this update
            for symbol, coin_state in self.coin_states.items():
                if not coin_state.current_snapshot:
                    continue

                snapshot = coin_state.current_snapshot

                # Convert orders to dicts
                def order_to_dict(order):
                    return {
                        'address': order.full_address,
                        'side': order.side,
                        'size': order.size,
                        'duration_hours': order.duration_hours,
                        'status': order.status,
                        'product_type': order.product_type,
                        'is_active': order.is_active
                    }

                orders_dict = [order_to_dict(o) for o in snapshot.orders]

                # Detect changes
                if coin_state.previous_snapshot:
                    changes = snapshot.compare_with(coin_state.previous_snapshot)
                else:
                    changes = {
                        'new_orders': snapshot.orders,
                        'completed_orders': [],
                        'canceled_orders': [],
                        'status_changes': []
                    }

                # Convert change orders to dicts
                new_orders_dict = [order_to_dict(o) for o in changes.get('new_orders', [])]
                completed_orders_dict = [order_to_dict(o) for o in changes.get('completed_orders', [])]

                # Separate canceled orders
                canceled_orders = [
                    o for o in changes.get('completed_orders', [])
                    if o.status == 'canceled'
                ]
                canceled_orders_dict = [order_to_dict(o) for o in canceled_orders]

                # Get stats
                stats = snapshot.get_stats()

                # Build update data in the new format (matching document 2)
                update_data = {
                    'timestamp': datetime.now().isoformat(),
                    'symbol': symbol,
                    'update_number': coin_state.update_count,
                    'summary': {
                        'total_orders': stats['total_orders'],
                        'active_orders': stats['active_orders'],
                        'buy_volume': stats['buy_volume'],
                        'sell_volume': stats['sell_volume'],
                        'net_flow': stats['net_flow'],
                        'buy_pressure_per_min': stats['buy_pressure_per_min'],
                        'sell_pressure_per_min': stats['sell_pressure_per_min'],
                        'net_pressure_per_min': stats['net_pressure_per_min'],
                        'whale_orders': stats['whale_orders'],
                        'unique_addresses': stats['unique_addresses']
                    },
                    'events': {
                        'new_orders': len(new_orders_dict),
                        'completed_orders': len(completed_orders_dict),
                        'canceled_orders': len(canceled_orders_dict),
                        'status_changes': len(changes.get('status_changes', []))
                    },
                    'active_orders': [order_to_dict(o) for o in snapshot.orders if o.is_active],
                    'new_orders': new_orders_dict,
                    'completed_orders': completed_orders_dict,
                    'canceled_orders': canceled_orders_dict,
                    'status_changes': changes.get('status_changes', [])
                }

                # Append as single line (NDJSON format)
                with open(self.current_json_file, 'a') as f:
                    json.dump(update_data, f, separators=(',', ':'))
                    f.write('\n')

            logger.debug(f"Appended update #{self.global_update_count} to {self.current_json_file}")

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