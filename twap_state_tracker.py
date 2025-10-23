#!/usr/bin/env python3
"""
TWAP State Tracker with Order-Size Classification
Tracks TWAP order states with simple size-based whale detection
"""
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

from api_client.models import TWAPOrder, TWAPSnapshot

logger = logging.getLogger(__name__)


class TWAPStateTracker:
    """Tracks TWAP order states with order-size classification"""

    # Classification thresholds based on order size
    MEGA_WHALE_THRESHOLD = 50_000
    WHALE_THRESHOLD = 10_000
    DOLPHIN_THRESHOLD = 5_000
    FISH_THRESHOLD = 1_000

    def __init__(self, symbol: str, json_logger=None):
        self.symbol = symbol
        self.update_count = 0
        self.json_logger = json_logger

        # Track snapshots
        self.current_snapshot: Optional[TWAPSnapshot] = None
        self.previous_snapshot: Optional[TWAPSnapshot] = None

        # History tracking
        self.all_addresses_seen = set()
        self.whale_history = []

        # Address classifications
        self.address_classifications = {}  # address -> classification
        self.classifications_file = Path(f'address_classifications_{symbol}.json')
        self._load_classifications()

        # Create output directory
        Path('twap_snapshots').mkdir(exist_ok=True)

        logger.info(f"TWAP State Tracker initialized for {symbol}")
        logger.info("Order-size based classification enabled")

    def _load_classifications(self):
        """Load existing classifications"""
        if self.classifications_file.exists():
            try:
                with open(self.classifications_file, 'r') as f:
                    self.address_classifications = json.load(f)
                logger.info(f"Loaded {len(self.address_classifications)} address classifications")
            except Exception as e:
                logger.error(f"Error loading classifications: {e}")

    def _save_classifications(self):
        """Save classifications to file"""
        try:
            with open(self.classifications_file, 'w') as f:
                json.dump(self.address_classifications, f, indent=2, sort_keys=True)
        except Exception as e:
            logger.error(f"Error saving classifications: {e}")

    def _classify_order(self, order: TWAPOrder) -> str:
        """Classify order based on its size"""
        size = order.size

        if size >= self.MEGA_WHALE_THRESHOLD:
            return 'mega_whale'
        elif size >= self.WHALE_THRESHOLD:
            return 'whale'
        elif size >= self.DOLPHIN_THRESHOLD:
            return 'dolphin'
        elif size >= self.FISH_THRESHOLD:
            return 'fish'
        else:
            return 'shrimp'

    def _update_classifications(self, snapshot: TWAPSnapshot):
        """Update address classifications based on orders"""
        tier_priority = {'shrimp': 0, 'fish': 1, 'dolphin': 2, 'whale': 3, 'mega_whale': 4}
        updated = False

        for order in snapshot.orders:
            address = order.full_address
            classification = self._classify_order(order)

            # Only update if new or higher tier
            existing = self.address_classifications.get(address)
            if not existing or tier_priority[classification] > tier_priority[existing]:
                self.address_classifications[address] = classification
                updated = True

        if updated:
            self._save_classifications()

    def update(self, raw_twap_orders: List[Dict]):
        """Update with new TWAP data using the model"""
        self.update_count += 1

        # Create new snapshot
        new_snapshot = TWAPSnapshot.from_hypurr_data(
            raw_twap_orders,
            self.symbol,
            self.update_count
        )

        # Update history
        self.previous_snapshot = self.current_snapshot
        self.current_snapshot = new_snapshot

        # Track all addresses
        self.all_addresses_seen.update(new_snapshot.unique_addresses)

        # Update address classifications
        self._update_classifications(new_snapshot)

        # Log summary only
        self._log_snapshot_summary()

        # Detect changes
        if self.previous_snapshot:
            changes = self._detect_changes()
        else:
            changes = {
                'new_orders': new_snapshot.orders,
                'completed_orders': [],
                'status_changes': []
            }

        # Save detailed data to JSON with classifications
        if self.json_logger:
            self._save_to_json(new_snapshot, changes)

    def _log_snapshot_summary(self):
        """Log snapshot with order details"""
        snapshot = self.current_snapshot
        stats = snapshot.get_stats()

        logger.info(f"[{self.symbol}] UPDATE #{self.update_count}")
        logger.info(
            f"Orders: {stats['active_orders']} active, {stats['total_orders']} total | "
            f"Pressure/min: Buy {stats['buy_pressure_per_min']:.1f}, "
            f"Sell {stats['sell_pressure_per_min']:.1f}, "
            f"Net {stats['net_pressure_per_min']:+.1f}"
        )

        # Sort orders by size (largest first)
        sorted_orders = sorted(snapshot.orders, key=lambda o: o.size, reverse=True)

        logger.info("Active orders:")
        for order in sorted_orders:
            if not order.is_active:
                continue

            addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"

            logger.info(
                f"  {addr} {order.side} {order.size:.0f} "
                f"{order.product_type} {order.duration_hours:.1f}h {order.status}"
            )

    def _detect_changes(self):
        """Detect and log changes between snapshots"""
        changes = self.current_snapshot.compare_with(self.previous_snapshot)

        # Log new orders with details
        if changes['new_orders']:
            logger.info(f"New orders: {len(changes['new_orders'])}")
            for order in changes['new_orders']:
                addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"
                logger.info(
                    f"  NEW: {addr} {order.side} {order.size:.0f} {order.product_type} {order.duration_hours:.1f}h")

        # Log completed orders with details
        if changes['completed_orders']:
            logger.info(f"Completed orders: {len(changes['completed_orders'])}")
            for order in changes['completed_orders']:
                addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"
                logger.info(
                    f"  ENDED: {addr} {order.side} {order.size:.0f} {order.duration_hours:.1f}h (status: {order.status})")

        # Log status changes with details
        if changes['status_changes']:
            logger.info(f"Status changes: {len(changes['status_changes'])}")
            for change in changes['status_changes']:
                addr = f"{change['address'][:6]}...{change['address'][-4:]}"
                logger.warning(
                    f"  STATUS: {addr} {change['side']} {change['size']:.0f} "
                    f"{change['old_status']} → {change['new_status']}"
                )

        return changes

    def _save_to_json(self, snapshot: TWAPSnapshot, changes: Dict):
        """Save detailed snapshot with classifications to JSON"""

        # Convert orders to dicts with classification
        def order_to_dict(order):
            classification = self._classify_order(order)
            return {
                'full_address': order.full_address,
                'side': order.side,
                'size': order.size,
                'duration_hours': order.duration_hours,
                'status': order.status,
                'product_type': order.product_type,
                'is_whale': (classification in ['mega_whale', 'whale']),  # Use OUR classification
                'classification': classification
            }

        # Create dict versions with classification
        orders_with_classification = [order_to_dict(o) for o in snapshot.orders]
        new_orders_with_classification = [order_to_dict(o) for o in changes.get('new_orders', [])]
        completed_orders_with_classification = [order_to_dict(o) for o in changes.get('completed_orders', [])]

        # Build data structure for JSON logger
        json_data = {
            'timestamp': snapshot.timestamp,
            'symbol': snapshot.symbol,
            'update_number': snapshot.update_number,
            'stats': snapshot.get_stats(),
            'orders': orders_with_classification,
            'new_orders': new_orders_with_classification,
            'completed_orders': completed_orders_with_classification,
            'status_changes': changes.get('status_changes', [])
        }

        # Pass simple dicts to json logger
        self.json_logger.log_data(json_data)

    def _get_classification_summary(self, snapshot: TWAPSnapshot) -> Dict:
        """Get count of orders in each classification"""
        summary = {
            'mega_whale': 0,
            'whale': 0,
            'dolphin': 0,
            'fish': 0,
            'shrimp': 0
        }

        for order in snapshot.orders:
            classification = self._classify_order(order)
            summary[classification] += 1

        return summary

    def get_current_stats(self) -> Dict:
        """Get current statistics"""
        if not self.current_snapshot:
            return {}

        stats = self.current_snapshot.get_stats()
        stats['all_time_addresses'] = len(self.all_addresses_seen)
        stats['classification_summary'] = self._get_classification_summary(self.current_snapshot)

        return stats

    def get_whale_activity(self) -> List[Dict]:
        """Get recent whale activity (mega_whale and whale orders only)"""
        if not self.current_snapshot:
            return []

        whale_orders = []
        for order in self.current_snapshot.orders:
            classification = self._classify_order(order)
            if classification in ['mega_whale', 'whale']:
                whale_orders.append({
                    'address': order.full_address,
                    'side': order.side,
                    'size': order.size,
                    'classification': classification,
                    'status': order.status,
                    'duration_hours': order.duration_hours
                })

        return sorted(whale_orders, key=lambda x: x['size'], reverse=True)