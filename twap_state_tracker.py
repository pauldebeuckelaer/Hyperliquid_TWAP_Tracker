#!/usr/bin/env python3
"""
TWAP State Tracker - WITH GHOST DETECTION + ALL STATUS TRACKING
================================================================
OPTION 1: Detects ghost orders from status changes (backfill)
OPTION 2: Tracks orders in ALL statuses (canceled, completed, error)

This ensures 100% order coverage from the moment tracking starts!

LOGGING STRATEGY:
- DEBUG: Individual order tracking, address discoveries, file operations
- INFO: Snapshot summaries, significant state changes, new/completed orders
- WARNING: Canceled orders, status changes, GHOST ORDERS (unusual events)
- ERROR: File operation failures, data errors
"""
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional, Set
from pathlib import Path

from api_client.models import TWAPOrder, TWAPSnapshot

logger = logging.getLogger(__name__)


class TWAPStateTracker:
    """Tracks TWAP order states and detects changes, with full coverage"""

    def __init__(self, symbol: str, json_logger=None):
        self.symbol = symbol
        self.update_count = 0
        # ========== FIX: Create json_logger if not provided ==========
        if json_logger is None:
            from json_logger import SimpleJsonLogger
            self.json_logger = SimpleJsonLogger({
                'log_dir': 'json_logs',
                'enabled': True
            })
            logger.info(f"✅ JSON logger auto-created for {symbol}")
        else:
            self.json_logger = json_logger
            logger.info(f"✅ JSON logger provided for {symbol}")
        # ==============================================================

        # Track snapshots
        self.current_snapshot: Optional[TWAPSnapshot] = None
        self.previous_snapshot: Optional[TWAPSnapshot] = None

        # ========== OPTION 1: Ghost Order Tracking ==========
        # Track all order hashes we've EVER seen (any status)
        self.ever_tracked_orders: Set[str] = set()

        # Store ghost orders we've discovered
        self.ghost_orders: List[Dict] = []

        # Ghost order stats
        self.ghost_stats = {
            'total_found': 0,
            'by_status_transition': {},
            'total_volume': 0.0
        }
        # ====================================================

        # ========== OPTION 2: All Status Tracking ==========
        # Track orders by status when first seen
        self.first_seen_status: Dict[str, str] = {}  # order_hash -> first status

        # Count orders by first status
        self.orders_by_first_status = {
            'active': 0,
            'canceled': 0,
            'completed': 0,
            'error': 0,
            'unknown': 0
        }
        # ====================================================

        # History tracking
        self.all_addresses_seen: Set[str] = set()
        self.address_history_file = Path(f'address_list.json')
        self._load_address_history()

        # Create output directory
        Path('twap_snapshots').mkdir(exist_ok=True)

        logger.info(f"TWAP State Tracker initialized for {symbol}")
        logger.info("  ✓ Option 1: Ghost order detection enabled")
        logger.info("  ✓ Option 2: All status tracking enabled")

    def _load_address_history(self):
        """Load existing address history"""
        if self.address_history_file.exists():
            try:
                with open(self.address_history_file, 'r') as f:
                    data = json.load(f)
                    # If it's a dict, get the keys as addresses
                    if isinstance(data, dict):
                        self.all_addresses_seen = set(data.keys())
                    # If it's a list (old format), convert it
                    elif isinstance(data, list):
                        self.all_addresses_seen = set(data)
                    # If it's the old format with 'addresses' key
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

            # Create dictionary with addresses as keys and empty dicts as values
            addresses_dict = {address: {} for address in sorted(self.all_addresses_seen)}

            # Write to file
            with open(self.address_history_file, 'w') as f:
                json.dump(addresses_dict, f, indent=2)

            # Verify file was written
            if self.address_history_file.exists():
                file_size = self.address_history_file.stat().st_size
                logger.debug(f"Address history saved: {file_size} bytes")
            else:
                logger.error("File was not created after write attempt!")

        except Exception as e:
            logger.error(f"Error saving address history: {e}")
            logger.exception(e)

    def update(self, raw_twap_orders: List[Dict], current_price: Optional[float] = None):
        """Update with new TWAP data"""
        self.update_count += 1

        # Create new snapshot
        new_snapshot = TWAPSnapshot.from_hypurr_data(
            raw_twap_orders,
            self.symbol,
            self.update_count,
            current_price=current_price
        )

        # ========== OPTION 2: Track ALL orders regardless of status ==========
        for order in new_snapshot.orders:
            order_hash = order.order_hash

            if order_hash and order_hash not in self.ever_tracked_orders:
                # First time seeing this order - track it!
                self.ever_tracked_orders.add(order_hash)
                self.first_seen_status[order_hash] = order.status

                # Count by first status
                if order.status in self.orders_by_first_status:
                    self.orders_by_first_status[order.status] += 1
                else:
                    self.orders_by_first_status['unknown'] += 1

                # INFO: Log when we track non-active orders for the first time
                if order.status != 'active':
                    logger.info(
                        f"📝 Tracking order with status '{order.status}': "
                        f"{order.display_address} {order.side} {order.size:,.0f} "
                        f"{order.product_type} (Duration: {order.duration_hours:.1f}h)"
                    )

            # DEBUG: Individual order tracking (verbose, routine)
            logger.debug(
                f"Order tracking: {order.display_address} - "
                f"Hash: {order.order_hash[:16]}... - Size: {order.size} - Status: {order.status}"
            )
        # =====================================================================

        # Update history
        self.previous_snapshot = self.current_snapshot
        self.current_snapshot = new_snapshot

        # Track all addresses
        addresses_before = len(self.all_addresses_seen)
        self.all_addresses_seen.update(new_snapshot.unique_addresses)

        # DEBUG: Log if new addresses discovered (frequent during early runtime)
        if len(self.all_addresses_seen) > addresses_before:
            new_count = len(self.all_addresses_seen) - addresses_before
            logger.debug(
                f"Discovered {new_count} new address(es). "
                f"Total: {len(self.all_addresses_seen)}"
            )

        # Always save address history
        self._save_address_history()

        # INFO: Log snapshot summary (important state overview)
        self._log_snapshot_summary()

        # Detect changes
        if self.previous_snapshot:
            changes = self._detect_changes()
        else:
            changes = {
                'new_orders': new_snapshot.active_orders,
                'completed_orders': [],
                'canceled_orders': [],
                'status_changes': [],
                'ghost_orders': []
            }

        # Save detailed data to JSON
        if self.json_logger:
            self._save_to_json(new_snapshot, changes)

    def _log_snapshot_summary(self):
        """
        Log snapshot with order details - separated by SPOT and PERP markets.

        IMPORTANT: Only shows truly active orders (status='active').
        Canceled/completed orders that linger in API response are logged at DEBUG level.
        """
        snapshot = self.current_snapshot
        stats = snapshot.get_stats()

        # ========== SEPARATE TRULY ACTIVE FROM LINGERING ORDERS ==========
        # Truly active = status is 'active'
        truly_active = [o for o in snapshot.orders if o.status == 'active']

        # Lingering = status is 'canceled', 'completed', or 'error' but still in API response
        lingering = [o for o in snapshot.orders if o.status in ['canceled', 'completed', 'error']]

        # ========== HIGH-LEVEL SUMMARY ==========
        logger.info(f"[{self.symbol}] UPDATE #{self.update_count}")
        logger.info(f"=" * 70)
        # Show price if available
        if snapshot.current_price:
            logger.info(f"💰 Current Price: ${snapshot.current_price:,.4f}")
        logger.info(
            f"Total Orders in API: {stats['total_orders']} | "
            f"Truly Active: {len(truly_active)} | "
            f"Lingering (ignore): {len(lingering)}"
        )

        # If there are lingering orders, log them at DEBUG level for visibility
        if lingering:
            logger.debug(f"🗑️  Lingering orders (already canceled/completed, waiting to disappear):")
            for order in lingering:
                logger.debug(
                    f"    {order.full_address} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.product_type} status={order.status}"
                )

        # ========== MARKET PRESSURE (based on ACTIVE orders only) ==========
        # Recalculate pressure using only truly active orders
        active_spot_buy = sum(
            o.get_execution_rate()
            for o in truly_active
            if o.is_buy_side and o.product_type == 'SPOT'
        )
        active_spot_sell = sum(
            o.get_execution_rate()
            for o in truly_active
            if o.is_sell_side and o.product_type == 'SPOT'
        )
        active_perp_buy = sum(
            o.get_execution_rate()
            for o in truly_active
            if o.is_buy_side and o.product_type == 'PERP'
        )
        active_perp_sell = sum(
            o.get_execution_rate()
            for o in truly_active
            if o.is_sell_side and o.product_type == 'PERP'
        )

        logger.info(
            f"💵 SPOT Pressure/min: "
            f"Buy {active_spot_buy:.1f} | "
            f"Sell {active_spot_sell:.1f} | "
            f"Net {active_spot_buy - active_spot_sell:+.1f}"
        )
        logger.info(
            f"⚡ PERP Pressure/min: "
            f"Buy {active_perp_buy:.1f} | "
            f"Sell {active_perp_sell:.1f} | "
            f"Net {active_perp_buy - active_perp_sell:+.1f}"
        )
        logger.info(
            f"📊 TOTAL Pressure/min: "
            f"Buy {active_spot_buy + active_perp_buy:.1f} | "
            f"Sell {active_spot_sell + active_perp_sell:.1f} | "
            f"Net {(active_spot_buy + active_perp_buy) - (active_spot_sell + active_perp_sell):+.1f}"
        )

        # ========== SEPARATE ORDERS BY MARKET TYPE ==========
        spot_orders = [o for o in truly_active if o.product_type == 'SPOT']
        perp_orders = [o for o in truly_active if o.product_type == 'PERP']

        # Sort each by size (largest first)
        spot_orders.sort(key=lambda o: o.size, reverse=True)
        perp_orders.sort(key=lambda o: o.size, reverse=True)

        # ========== SPOT MARKET ORDERS ==========
        logger.info(f"=" * 70)
        if spot_orders:
            logger.info(f"💵 SPOT MARKET - {len(spot_orders)} Active Orders")
            logger.info(f"-" * 70)

            for order in spot_orders:
                addr = order.full_address
                side_emoji = "🟢" if order.is_buy_side else "🔴"
                progress = f"{order.progress_percent:>5.1f}%" if order.progress_percent is not None else "  N/A"

                logger.info(
                    f"  {side_emoji} {addr} | {order.side:4s} | "
                    f"{order.size:>10,.0f} | {order.duration_hours:>5.1f}h | {progress}"
                )
        else:
            logger.info(f"💵 SPOT MARKET - No Active Orders")

        # ========== PERP MARKET ORDERS ==========
        logger.info(f"=" * 70)
        if perp_orders:
            logger.info(f"⚡ PERP MARKET - {len(perp_orders)} Active Orders")
            logger.info(f"-" * 70)

            for order in perp_orders:
                addr = order.full_address
                side_emoji = "🟢" if order.is_buy_side else "🔴"
                progress = f"{order.progress_percent:>5.1f}%" if order.progress_percent is not None else "  N/A"

                logger.info(
                    f"  {side_emoji} {addr} | {order.side:4s} | "
                    f"{order.size:>10,.0f} | {order.duration_hours:>5.1f}h | {progress}"
                )
        else:
            logger.info(f"⚡ PERP MARKET - No Active Orders")

        logger.info(f"=" * 70)

    def _reconstruct_order_from_status_change(self, change: Dict) -> Dict:
        """
        Reconstruct a ghost order from status change data.

        Args:
            change: Status change dict with order details

        Returns:
            Reconstructed order dict
        """
        return {
            'order_hash': change['order_hash'],
            'address': change['address'],
            'side': change['side'],
            'size': change['size'],
            'product_type': change['product_type'],
            'duration_hours': change['duration_hours'],
            'old_status': change['old_status'],
            'new_status': change['new_status'],
            'discovered_at_update': self.update_count,
            'timestamp': datetime.now().isoformat()
        }

    def _detect_changes(self):
        """Detect and log changes between snapshots, including ghost orders"""
        changes = self.current_snapshot.compare_with(self.previous_snapshot)

        # ========== OPTION 1: GHOST ORDER DETECTION ==========
        ghost_orders_found = []

        for change in changes.get('status_changes', []):
            order_hash = change.get('order_hash')

            # Check if this order was NEVER tracked (shouldn't happen with Option 2!)
            if order_hash and order_hash not in self.ever_tracked_orders:
                # This is a GHOST ORDER!
                ghost_order = self._reconstruct_order_from_status_change(change)
                ghost_orders_found.append(ghost_order)
                self.ghost_orders.append(ghost_order)

                # Update ghost stats
                self.ghost_stats['total_found'] += 1
                self.ghost_stats['total_volume'] += change['size']

                transition = f"{change['old_status']} → {change['new_status']}"
                self.ghost_stats['by_status_transition'][transition] = \
                    self.ghost_stats['by_status_transition'].get(transition, 0) + 1

                # WARNING: Log ghost order detection
                logger.warning(
                    f"👻 GHOST ORDER DETECTED (shouldn't happen with Option 2!): "
                    f"{change['address']} {change['side']} {change['size']:,.0f} "
                    f"{change['product_type']} ({transition}) - Duration: {change['duration_hours']:.1f}h"
                )

        # Add ghost orders to changes dict
        changes['ghost_orders'] = ghost_orders_found
        # =====================================================

        # INFO: New orders (important event)
        if changes['new_orders']:
            logger.info(f"🆕 New orders detected: {len(changes['new_orders'])}")
            for order in changes['new_orders']:
                addr = f"{order.full_address}"
                logger.info(
                    f"  NEW: {addr} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.product_type} {order.duration_hours:.1f}h"
                )

        # ========== SEPARATE COMPLETED AND CANCELED ==========
        completed_orders = []
        canceled_orders = []

        # From orders that disappeared from API
        for order in changes.get('completed_orders', []):
            if order.status in ['canceled', 'error']:
                canceled_orders.append(order)
            else:
                completed_orders.append(order)

        # From status changes (orders still in API but status changed)
        for change in changes.get('status_changes', []):
            if change.get('new_status') in ['canceled', 'error']:
                # Create a dict matching order_to_dict format
                canceled_orders.append({
                    'full_address': change['address'],
                    'side': change['side'],
                    'size': change['size'],
                    'product_type': change['product_type'],
                    'duration_hours': change['duration_hours'],
                    'order_hash': change['order_hash'],
                    'status': change['new_status'],  # ← Use actual status, not hardcoded
                    'elapsed_minutes': change.get('elapsed_minutes'),
                    'progress_percent': change.get('progress_percent'),
                    'time_remaining_minutes': change.get('time_remaining_minutes')
                })
        # =====================================================

        # INFO: Completed orders (important event - finished successfully)
        if completed_orders:
            logger.info(f"✅ Completed orders: {len(completed_orders)}")
            for order in completed_orders:
                # Handle both TWAPOrder objects and dicts
                if hasattr(order, 'full_address'):
                    addr = order.full_address
                    logger.info(
                        f"  COMPLETED: {addr} {order.side:4s} {order.size:>10,.0f} "
                        f"{order.duration_hours:.1f}h (status: {order.status})"
                    )
                else:
                    addr = order['full_address']
                    logger.info(
                        f"  COMPLETED: {addr} {order['side']:4s} {order['size']:>10,.0f} "
                        f"{order['duration_hours']:.1f}h (status: {order['status']})"
                    )

        if canceled_orders:
            logger.warning(f"❌ Canceled orders: {len(canceled_orders)}")
            for order in canceled_orders:
                # Handle both TWAPOrder objects and dicts
                if hasattr(order, 'full_address'):
                    addr = order.full_address
                    logger.warning(
                        f"  CANCELED: {addr} {order.side:4s} {order.size:>10,.0f} "
                        f"{order.product_type} {order.duration_hours:.1f}h"
                    )
                else:
                    addr = order['full_address']
                    logger.warning(
                        f"  CANCELED: {addr} {order['side']:4s} {order['size']:>10,.0f} "
                        f"{order['product_type']} {order['duration_hours']:.1f}h"
                    )

        # WARNING: Status changes (unusual, potentially important)
        if changes['status_changes']:
            logger.warning(f"🔄 Status changes detected: {len(changes['status_changes'])}")
            for change in changes['status_changes']:
                addr = f"{change['address']}"
                logger.warning(
                    f"  STATUS: {addr} {change['side']:4s} {change['size']:>10,.0f} "
                    f"{change['old_status']} → {change['new_status']}"
                )

        # Store for return
        changes['completed_orders'] = completed_orders
        changes['canceled_orders'] = canceled_orders

        return changes

    def _save_to_json(self, snapshot: TWAPSnapshot, changes: Dict):
        """Save detailed snapshot to JSON, including ghost orders and tracking stats"""

        # Convert orders to dicts - handle both TWAPOrder objects and dicts
        def order_to_dict(order):
            if isinstance(order, dict):
                return {
                    'full_address': order.get('full_address', order.get('address', '')),
                    'side': order['side'],
                    'size': order['size'],
                    'duration_hours': order['duration_hours'],
                    'status': order.get('status', 'unknown'),
                    'product_type': order['product_type'],
                    'is_active': order.get('is_active', False),
                    'order_hash': order['order_hash'],
                    'elapsed_minutes': order.get('elapsed_minutes'),
                    'progress_percent': order.get('progress_percent'),
                    'time_remaining_minutes': order.get('time_remaining_minutes')
                }
            return {
                'full_address': order.full_address,
                'side': order.side,
                'size': order.size,
                'duration_hours': order.duration_hours,
                'status': order.status,
                'product_type': order.product_type,
                'is_active': order.is_active,
                'order_hash': order.order_hash,
                'elapsed_minutes': order.elapsed_minutes,
                'progress_percent': order.progress_percent,
                'time_remaining_minutes': order.time_remaining_minutes
            }

        # Create dict versions
        active_orders_dict = [order_to_dict(o) for o in snapshot.active_orders]
        new_orders_dict = [order_to_dict(o) for o in changes.get('new_orders', [])]
        completed_orders_dict = [order_to_dict(o) for o in changes.get('completed_orders', [])]
        canceled_orders_dict = [order_to_dict(o) for o in changes.get('canceled_orders', [])]
        ghost_orders_dict = changes.get('ghost_orders', [])

        # Use snapshot.active_orders for volume/pressure (already filtered)
        active = snapshot.active_orders

        # Get summary stats with SPOT/PERP breakdown
        summary = {
            'total_orders': len(snapshot.orders),
            'active_orders': len(active),

            # Total volume
            'buy_volume': snapshot.buy_volume,
            'sell_volume': snapshot.sell_volume,
            'net_flow': snapshot.net_flow,

            # SPOT breakdown
            'spot_buy_volume': sum(o.size for o in active if o.is_buy_side and o.product_type == 'SPOT'),
            'spot_sell_volume': sum(o.size for o in active if o.is_sell_side and o.product_type == 'SPOT'),
            'spot_buy_pressure': sum(
                o.get_execution_rate() for o in active if o.is_buy_side and o.product_type == 'SPOT'),
            'spot_sell_pressure': sum(
                o.get_execution_rate() for o in active if o.is_sell_side and o.product_type == 'SPOT'),

            # PERP breakdown
            'perp_buy_volume': sum(o.size for o in active if o.is_buy_side and o.product_type == 'PERP'),
            'perp_sell_volume': sum(o.size for o in active if o.is_sell_side and o.product_type == 'PERP'),
            'perp_buy_pressure': sum(
                o.get_execution_rate() for o in active if o.is_buy_side and o.product_type == 'PERP'),
            'perp_sell_pressure': sum(
                o.get_execution_rate() for o in active if o.is_sell_side and o.product_type == 'PERP'),

            # Total pressure
            'buy_pressure_per_min': snapshot.buy_pressure_per_min,
            'sell_pressure_per_min': snapshot.sell_pressure_per_min,
            'net_pressure_per_min': snapshot.net_pressure_per_min,

            'whale_orders': len(snapshot.whale_orders),
            'unique_addresses': len(snapshot.unique_addresses)
        }

        # Get event counts
        events = {
            'new_orders': len(new_orders_dict),
            'completed_orders': len(completed_orders_dict),
            'canceled_orders': len(canceled_orders_dict),
            'status_changes': len(changes.get('status_changes', [])),
            'ghost_orders': len(ghost_orders_dict)
        }

        # Tracking coverage stats
        tracking_stats = {
            'ever_tracked_orders': len(self.ever_tracked_orders),
            'orders_by_first_status': self.orders_by_first_status.copy()
        }

        # Build data structure for JSON logger
        json_data = {
            'timestamp': snapshot.timestamp,
            'symbol': snapshot.symbol,
            'update_number': snapshot.update_number,
            'current_price': snapshot.current_price,
            'stats': summary,
            'events': events,
            'tracking_stats': tracking_stats,
            'orders': active_orders_dict,
            'new_orders': new_orders_dict,
            'completed_orders': completed_orders_dict,
            'canceled_orders': canceled_orders_dict,
            'status_changes': changes.get('status_changes', []),
            'ghost_orders': ghost_orders_dict
        }

        # Pass to json logger
        self.json_logger.log_data(json_data)
        logger.debug(f"Saved snapshot #{snapshot.update_number} to JSON")

    def get_current_stats(self) -> Dict:
        """Get current statistics, including ghost order stats and tracking coverage"""
        if not self.current_snapshot:
            return {}

        stats = self.current_snapshot.get_stats()
        stats['all_time_addresses'] = len(self.all_addresses_seen)

        # Ghost order stats
        stats['ghost_orders'] = self.ghost_stats
        stats['ever_tracked_orders'] = len(self.ever_tracked_orders)

        # ========== OPTION 2: Add tracking coverage stats ==========
        stats['orders_by_first_status'] = self.orders_by_first_status.copy()
        stats['tracking_coverage'] = {
            'total_orders_tracked': len(self.ever_tracked_orders),
            'started_active': self.orders_by_first_status['active'],
            'started_canceled': self.orders_by_first_status['canceled'],
            'started_completed': self.orders_by_first_status['completed'],
            'started_error': self.orders_by_first_status['error']
        }
        # ===========================================================

        return stats

    def get_address_history(self) -> Dict:
        """Get address history summary"""
        return {
            'total_addresses': len(self.all_addresses_seen),
            'addresses': sorted(list(self.all_addresses_seen))
        }

    def get_ghost_order_summary(self) -> Dict:
        """
        Get summary of all ghost orders detected.

        Returns:
            Dict with ghost order statistics and details
        """
        return {
            'total_found': self.ghost_stats['total_found'],
            'total_volume': self.ghost_stats['total_volume'],
            'by_status_transition': self.ghost_stats['by_status_transition'],
            'ghost_orders': self.ghost_orders
        }

    def get_tracking_coverage_report(self) -> Dict:
        """
        Get detailed report on tracking coverage (Option 2 feature).

        Shows how many orders were first seen in each status.
        With Option 2, we should see non-zero counts for canceled/completed.

        Returns:
            Dict with coverage statistics
        """
        total = len(self.ever_tracked_orders)

        return {
            'total_orders_tracked': total,
            'by_first_status': self.orders_by_first_status.copy(),
            'coverage_percentages': {
                status: (count / total * 100) if total > 0 else 0
                for status, count in self.orders_by_first_status.items()
            },
            'option2_working': self.orders_by_first_status['canceled'] > 0 or
                               self.orders_by_first_status['completed'] > 0
        }