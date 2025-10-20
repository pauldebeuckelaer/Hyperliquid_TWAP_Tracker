#!/usr/bin/env python3
"""
TWAP State Tracker - Using TWAPOrder Model
Tracks state changes in TWAP orders with structured data
"""
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

from api_client.models import TWAPOrder, TWAPSnapshot

logger = logging.getLogger(__name__)


class TWAPStateTracker:
    """Tracks TWAP order states using the TWAPOrder model"""

    def __init__(self, symbol: str, json_logger=None):
        self.symbol = symbol
        self.update_count = 0

        self.json_logger = json_logger

        # Track snapshots
        self.current_snapshot: Optional[TWAPSnapshot] = None
        self.previous_snapshot: Optional[TWAPSnapshot] = None

        # History tracking
        self.all_addresses_seen = set()
        self.whale_history = []  # Track whale orders over time

        # Create output directory
        Path('twap_snapshots').mkdir(exist_ok=True)

        logger.info(f"TWAP State Tracker initialized for {symbol}")

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

        # Log the update
        self._log_update()

        # Detect and log changes
        if self.previous_snapshot:
            changes = self._detect_and_log_changes()
        else:
            # First update - all orders are "new"
            changes = {
                'new_orders': new_snapshot.orders,
                'completed_orders': [],
                'status_changes': []
            }

        # Save snapshot (OLD - disabled)
        # self._save_snapshot()

        # Save to JSON format
        if self.json_logger:
            self.json_logger.log_snapshot(self.current_snapshot, changes)

    def _log_update(self):
        """Log current state using the model"""
        snapshot = self.current_snapshot
        stats = snapshot.get_stats()

        logger.info(f"[{self.symbol}] UPDATE #{self.update_count}")

        # Log individual orders with detail
        logger.info(f"Orders ({stats['active_orders']} active):")

        # Sort orders: whales first, then by size
        sorted_orders = sorted(
            snapshot.orders,
            key=lambda o: (o.is_whale, o.size),
            reverse=True
        )

        for order in sorted_orders:
            # Determine flags
            flags = []
            if order.is_whale:
                flags.append('W')
            if not order.is_active:
                flags.append('X')

            # Check if new address
            if order.full_address not in self.previous_snapshot.unique_addresses if self.previous_snapshot else True:
                flags.append('N')

            flag_str = ' '.join(flags) if flags else ''
            if flag_str:
                flag_str += ' '

            # Shorten address
            addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"

            logger.info(
                f"{flag_str}{addr} {order.side} {order.size:.0f} "
                f"{order.product_type} {order.duration_hours:.1f}h {order.status}"
            )

        # Log pressure per minute
        logger.info(
            f"Pressure/min: Buy {stats['buy_pressure_per_min']:.1f}, "
            f"Sell {stats['sell_pressure_per_min']:.1f}, "
            f"Net {stats['net_pressure_per_min']:+.1f}"
        )


    def _detect_and_log_changes(self):
        """Detect and log changes between snapshots"""
        changes = self.current_snapshot.compare_with(self.previous_snapshot)

        # Log new orders
        if changes['new_orders']:
            for order in changes['new_orders']:
                whale_flag = 'W ' if order.is_whale else ''
                addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"
                logger.info(
                    f"{whale_flag}New: {addr} {order.side} {order.size:.0f} "
                    f"{order.product_type} {order.duration_hours:.1f}h"
                )

                # Track whale entries
                if order.is_whale:
                    self.whale_history.append({
                        'timestamp': order.timestamp,
                        'address': order.full_address,
                        'side': order.side,
                        'size': order.size,
                        'event': 'NEW_WHALE'
                    })

        # Log completed orders (orders that disappeared from API)
        if changes['completed_orders']:
            for order in changes['completed_orders']:
                whale_flag = 'W ' if order.is_whale else ''
                addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"
                logger.info(
                    f"{whale_flag}Completed: {addr} {order.side} {order.size:.0f} "
                    f"{order.duration_hours:.1f}h (was {order.status})"
                )

                # Track whale completions
                if order.is_whale:
                    self.whale_history.append({
                        'timestamp': self.current_snapshot.timestamp,
                        'address': order.full_address,
                        'side': order.side,
                        'size': order.size,
                        'event': 'WHALE_COMPLETED'
                    })

        # Log status changes (e.g. active -> canceled)
        if changes['status_changes']:
            for change in changes['status_changes']:
                addr = f"{change['address'][:6]}...{change['address'][-4:]}"
                logger.warning(
                    f"Status change: {addr} {change['side']} {change['size']:.0f} "
                    f"{change['old_status']} -> {change['new_status']}"
                )

        # Log significant flow changes
        if abs(changes['volume_change']) > 1000:
            logger.info(
                f"Flow changes: Vol {changes['volume_change']:+.0f}, "
                f"Net {changes['net_flow_change']:+.0f}, "
                f"Active {changes['active_orders_change']:+d}"
            )

        return changes

    def _save_snapshot(self):
        """Save snapshot to JSON file"""
        try:
            filename = Path('twap_snapshots') / f"{self.symbol}_{datetime.now().strftime('%Y%m%d')}.jsonl"

            # Get address summary and convert TWAPOrder objects to dicts
            address_summary = self.current_snapshot.get_address_summary()
            serializable_summary = {}

            for addr, data in address_summary.items():
                serializable_summary[addr] = {
                    'display': data['display'],
                    'total_volume': data['total_volume'],
                    'order_count': data['order_count'],
                    'active_count': data['active_count'],
                    'orders': [
                        {
                            'address': order.full_address,
                            'size': order.size,
                            'side': order.side,
                            'product_type': order.product_type,
                            'status': order.status,
                            'duration_minutes': order.duration_minutes
                        }
                        for order in data['orders']
                    ]
                }

            # Prepare data for JSON
            snapshot_data = {
                'timestamp': self.current_snapshot.timestamp.isoformat(),
                'update_number': self.update_count,
                'symbol': self.symbol,
                'stats': self.current_snapshot.get_stats(),
                'orders': [
                    {
                        'address': order.full_address,
                        'full_address': order.full_address,
                        'size': order.size,
                        'side': order.side,
                        'product_type': order.product_type,
                        'status': order.status,
                        'duration_minutes': order.duration_minutes,
                        'is_whale': order.is_whale
                    }
                    for order in self.current_snapshot.orders
                ],
                'address_summary': serializable_summary
            }

            # Convert datetime objects to strings
            snapshot_data['stats']['timestamp'] = snapshot_data['stats']['timestamp'].isoformat()

            # Append to JSONL file
            with open(filename, 'a', encoding='utf-8') as f:
                f.write(json.dumps(snapshot_data) + '\n')

            logger.debug(f"Snapshot saved to {filename}")

        except Exception as e:
            logger.error(f"Error saving snapshot: {e}")

    def get_current_stats(self) -> Dict:
        """Get current statistics"""
        if not self.current_snapshot:
            return {}

        stats = self.current_snapshot.get_stats()
        stats['all_time_addresses'] = len(self.all_addresses_seen)
        stats['whale_events'] = len(self.whale_history)

        return stats

    def get_whale_activity(self) -> List[Dict]:
        """Get recent whale activity"""
        return self.whale_history[-10:]  # Last 10 whale events