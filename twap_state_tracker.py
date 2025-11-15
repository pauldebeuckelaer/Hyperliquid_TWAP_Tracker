#!/usr/bin/env python3
"""
TWAP State Tracker - REFACTORED WITH PROPER LOGGING LEVELS
Tracks TWAP order states and changes

LOGGING STRATEGY:
- DEBUG: Individual order tracking, address discoveries, file operations
- INFO: Snapshot summaries, significant state changes, new/completed orders
- WARNING: Canceled orders, status changes (unusual events)
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
    """Tracks TWAP order states and detects changes"""

    def __init__(self, symbol: str, json_logger=None):
        self.symbol = symbol
        self.update_count = 0
        self.json_logger = json_logger

        # Track snapshots
        self.current_snapshot: Optional[TWAPSnapshot] = None
        self.previous_snapshot: Optional[TWAPSnapshot] = None

        # History tracking
        self.all_addresses_seen: Set[str] = set()
        self.address_history_file = Path(f'address_list.json')
        self._load_address_history()

        # Create output directory
        Path('twap_snapshots').mkdir(exist_ok=True)

        logger.info(f"TWAP State Tracker initialized for {symbol}")

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

    def update(self, raw_twap_orders: List[Dict]):
        """Update with new TWAP data"""
        self.update_count += 1

        # Create new snapshot
        new_snapshot = TWAPSnapshot.from_hypurr_data(
            raw_twap_orders,
            self.symbol,
            self.update_count
        )

        # DEBUG: Individual order tracking (verbose, routine)
        for order in new_snapshot.orders:
            logger.debug(
                f"Order tracking: {order.display_address} - "
                f"Hash: {order.order_hash[:16]}... - Size: {order.size}"
            )

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
                'new_orders': new_snapshot.orders,
                'completed_orders': [],
                'canceled_orders': [],
                'status_changes': []
            }

        # Save detailed data to JSON
        if self.json_logger:
            self._save_to_json(new_snapshot, changes)

    def _log_snapshot_summary(self):
        """Log snapshot with order details - separated by SPOT and PERP markets"""
        snapshot = self.current_snapshot
        stats = snapshot.get_stats()

        # INFO: High-level summary
        logger.info(f"[{self.symbol}] UPDATE #{self.update_count}")
        logger.info(f"=" * 70)
        logger.info(
            f"Total Orders: {stats['total_orders']} | "
            f"Active: {stats['active_orders']} ({stats['spot_orders']} SPOT, {stats['perp_orders']} PERP)"
        )

        # SHOW PRESSURE FOR EACH MARKET SEPARATELY
        logger.info(
            f"💵 SPOT Pressure/min: "
            f"Buy {stats['spot_buy_pressure_per_min']:.1f} | "
            f"Sell {stats['spot_sell_pressure_per_min']:.1f} | "
            f"Net {stats['spot_net_pressure_per_min']:+.1f}"
        )
        logger.info(
            f"⚡ PERP Pressure/min: "
            f"Buy {stats['perp_buy_pressure_per_min']:.1f} | "
            f"Sell {stats['perp_sell_pressure_per_min']:.1f} | "
            f"Net {stats['perp_net_pressure_per_min']:+.1f}"
        )
        logger.info(
            f"📊 TOTAL Pressure/min: "
            f"Buy {stats['buy_pressure_per_min']:.1f} | "
            f"Sell {stats['sell_pressure_per_min']:.1f} | "
            f"Net {stats['net_pressure_per_min']:+.1f}"
        )

        # Separate orders by market
        spot_orders = [o for o in snapshot.orders if o.product_type == 'SPOT']
        perp_orders = [o for o in snapshot.orders if o.product_type == 'PERP']

        # Sort each by size (largest first)
        spot_orders.sort(key=lambda o: o.size, reverse=True)
        perp_orders.sort(key=lambda o: o.size, reverse=True)

        # Count active orders
        active_spot = sum(1 for o in spot_orders if o.is_active)
        active_perp = sum(1 for o in perp_orders if o.is_active)

        # ========== SPOT MARKET ==========
        logger.info(f"=" * 70)
        if active_spot > 0:
            logger.info(f"💵 SPOT MARKET - {active_spot} Active Orders")
            logger.info(f"-" * 70)
            for order in spot_orders:
                if not order.is_active:
                    continue

                addr = f"{order.full_address}"
                side_emoji = "🟢" if order.is_buy_side else "🔴"

                logger.info(
                    f"  {side_emoji} {addr} | {order.side:4s} | "
                    f"{order.size:>10,.0f} | {order.duration_hours:>5.1f}h"
                )
        else:
            logger.info(f"💵 SPOT MARKET - No Active Orders")

        # ========== PERP MARKET ==========
        logger.info(f"=" * 70)
        if active_perp > 0:
            logger.info(f"⚡ PERP MARKET - {active_perp} Active Orders")
            logger.info(f"-" * 70)
            for order in perp_orders:
                if not order.is_active:
                    continue

                addr = f"{order.full_address}"
                side_emoji = "🟢" if order.is_buy_side else "🔴"

                logger.info(
                    f"  {side_emoji} {addr} | {order.side:4s} | "
                    f"{order.size:>10,.0f} | {order.duration_hours:>5.1f}h"
                )
        else:
            logger.info(f"⚡ PERP MARKET - No Active Orders")

        logger.info(f"=" * 70)

    def _detect_changes(self):
        """Detect and log changes between snapshots"""
        changes = self.current_snapshot.compare_with(self.previous_snapshot)

        # INFO: New orders (important event)
        if changes['new_orders']:
            logger.info(f"🆕 New orders detected: {len(changes['new_orders'])}")
            for order in changes['new_orders']:
                addr = f"{order.full_address}"
                logger.info(
                    f"  NEW: {addr} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.product_type} {order.duration_hours:.1f}h"
                )

        # Separate completed and canceled orders
        completed_orders = []
        canceled_orders = []

        for order in changes.get('completed_orders', []):
            if order.status == 'canceled':
                canceled_orders.append(order)
            else:
                completed_orders.append(order)

        # INFO: Completed orders (important event - finished successfully)
        if completed_orders:
            logger.info(f"✅ Completed orders: {len(completed_orders)}")
            for order in completed_orders:
                addr = f"{order.full_address}"
                logger.info(
                    f"  COMPLETED: {addr} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.duration_hours:.1f}h (status: {order.status})"
                )

        # WARNING: Canceled orders (unusual, potentially important)
        if canceled_orders:
            logger.warning(f"❌ Canceled orders: {len(canceled_orders)}")
            for order in canceled_orders:
                addr = f"{order.full_address}"
                logger.warning(
                    f"  CANCELED: {addr} {order.side:4s} {order.size:>10,.0f} "
                    f"{order.duration_hours:.1f}h"
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

        # Store canceled orders for return
        changes['canceled_orders'] = canceled_orders

        return changes

    def _save_to_json(self, snapshot: TWAPSnapshot, changes: Dict):
        """Save detailed snapshot to JSON"""

        # Convert orders to dicts
        def order_to_dict(order):
            return {
                'full_address': order.full_address,
                'side': order.side,
                'size': order.size,
                'duration_hours': order.duration_hours,
                'status': order.status,
                'product_type': order.product_type,
                'is_active': order.is_active
            }

        # Create dict versions
        orders_dict = [order_to_dict(o) for o in snapshot.orders]
        new_orders_dict = [order_to_dict(o) for o in changes.get('new_orders', [])]
        completed_orders_dict = [order_to_dict(o) for o in changes.get('completed_orders', [])]
        canceled_orders_dict = [order_to_dict(o) for o in changes.get('canceled_orders', [])]

        # Build data structure for JSON logger
        json_data = {
            'timestamp': snapshot.timestamp,
            'symbol': snapshot.symbol,
            'update_number': snapshot.update_number,
            'stats': snapshot.get_stats(),
            'orders': orders_dict,
            'new_orders': new_orders_dict,
            'completed_orders': completed_orders_dict,
            'canceled_orders': canceled_orders_dict,
            'status_changes': changes.get('status_changes', [])
        }

        # Pass simple dicts to json logger
        self.json_logger.log_data(json_data)
        logger.debug(f"Saved snapshot #{snapshot.update_number} to JSON")

    def get_current_stats(self) -> Dict:
        """Get current statistics"""
        if not self.current_snapshot:
            return {}

        stats = self.current_snapshot.get_stats()
        stats['all_time_addresses'] = len(self.all_addresses_seen)

        return stats

    def get_address_history(self) -> Dict:
        """Get address history summary"""
        return {
            'total_addresses': len(self.all_addresses_seen),
            'addresses': sorted(list(self.all_addresses_seen))
        }