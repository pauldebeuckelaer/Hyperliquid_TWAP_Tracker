#!/usr/bin/env python3
"""
TWAP State Tracker - FIXED VERSION with AddressRankTracker Integration
Properly classifies whales by fetching rank data
"""
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

from api_client.models import TWAPOrder, TWAPSnapshot

logger = logging.getLogger(__name__)


class TWAPStateTracker:
    """Tracks TWAP order states with whale classification"""

    def __init__(self, symbol: str, json_logger=None, rank_tracker=None):
        self.symbol = symbol
        self.update_count = 0
        self.json_logger = json_logger

        # CRITICAL: Add rank tracker integration
        self.rank_tracker = rank_tracker

        # Track snapshots
        self.current_snapshot: Optional[TWAPSnapshot] = None
        self.previous_snapshot: Optional[TWAPSnapshot] = None

        # History tracking
        self.all_addresses_seen = set()
        self.whale_history = []

        # Create output directory
        Path('twap_snapshots').mkdir(exist_ok=True)

        logger.info(f"TWAP State Tracker initialized for {symbol}")
        if rank_tracker:
            logger.info("✅ Rank tracker integration enabled")
        else:
            logger.warning("⚠️ No rank tracker - whale detection will be basic")

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

        # CRITICAL: Update rank tracker with new addresses
        if self.rank_tracker:
            self._update_address_ranks(new_snapshot)

        # Log the update
        self._log_update()

        # Detect and log changes
        if self.previous_snapshot:
            changes = self._detect_and_log_changes()
        else:
            changes = {
                'new_orders': new_snapshot.orders,
                'completed_orders': [],
                'status_changes': []
            }

        # Save to JSON format
        if self.json_logger:
            self.json_logger.log_snapshot(self.current_snapshot, changes)

    def _update_address_ranks(self, snapshot: TWAPSnapshot):
        """
        Update rank tracker with addresses from snapshot
        Fetches ranks for new addresses
        """
        # Add all addresses from this snapshot
        for order in snapshot.orders:
            self.rank_tracker.add_address(order.full_address, order)

        # Get addresses that need rank updates (new or unknown)
        addresses_needing_ranks = []
        for address in snapshot.unique_addresses:
            addr_data = self.rank_tracker.addresses.get(address, {})

            # Fetch rank if:
            # 1. Never checked before, OR
            # 2. Classification is unknown
            if (addr_data.get('last_rank_check') is None or
                    addr_data.get('classification') == 'unknown'):
                addresses_needing_ranks.append(address)

        # Fetch ranks for addresses that need it
        if addresses_needing_ranks:
            logger.info(f"📡 Fetching ranks for {len(addresses_needing_ranks)} address(es)...")
            for address in addresses_needing_ranks:
                self.rank_tracker.fetch_and_update_rank(address)

            # Save updated data
            self.rank_tracker._save_data()

    def _log_update(self):
        """Log current state with proper whale classification"""
        snapshot = self.current_snapshot
        stats = snapshot.get_stats()

        logger.info(f"[{self.symbol}] UPDATE #{self.update_count}")
        logger.info(f"Orders ({stats['active_orders']} active):")

        # Sort orders: whales first, then by size
        sorted_orders = sorted(
            snapshot.orders,
            key=lambda o: (self._get_whale_priority(o), o.size),
            reverse=True
        )

        for order in sorted_orders:
            # Get classification from rank tracker
            classification = self._get_address_classification(order.full_address)

            # Determine flags
            flags = []

            # Use rank-based whale detection instead of basic size check
            if classification in ['mega_whale', 'whale']:
                flags.append('🐋' if classification == 'whale' else '🐋🐋')
            elif classification == 'dolphin':
                flags.append('🐬')

            if not order.is_active:
                flags.append('X')

            # Check if new address
            if self.previous_snapshot and order.full_address not in self.previous_snapshot.unique_addresses:
                flags.append('N')

            flag_str = ' '.join(flags) if flags else ''
            if flag_str:
                flag_str += ' '

            # Shorten address
            addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"

            # Add rank info if available
            rank_info = self._get_rank_info(order.full_address)

            logger.info(
                f"{flag_str}{addr} {order.side} {order.size:.0f} "
                f"{order.product_type} {order.duration_hours:.1f}h {order.status}"
                f"{rank_info}"
            )

        # Log pressure per minute
        logger.info(
            f"Pressure/min: Buy {stats['buy_pressure_per_min']:.1f}, "
            f"Sell {stats['sell_pressure_per_min']:.1f}, "
            f"Net {stats['net_pressure_per_min']:+.1f}"
        )

    def _get_address_classification(self, address: str) -> str:
        """Get classification from rank tracker"""
        if not self.rank_tracker:
            return 'unknown'

        addr_data = self.rank_tracker.addresses.get(address)
        if not addr_data:
            return 'unknown'

        return addr_data.get('classification', 'unknown')

    def _get_whale_priority(self, order: TWAPOrder) -> int:
        """Get priority for sorting (higher = more important)"""
        classification = self._get_address_classification(order.full_address)

        priority_map = {
            'mega_whale': 5,
            'whale': 4,
            'dolphin': 3,
            'fish': 2,
            'shrimp': 1,
            'unknown': 0
        }

        return priority_map.get(classification, 0)

    def _get_rank_info(self, address: str) -> str:
        """Get rank info string for logging"""
        if not self.rank_tracker:
            return ""

        addr_data = self.rank_tracker.addresses.get(address)
        if not addr_data:
            return ""

        hype_rank = addr_data.get('hype_rank')
        if hype_rank:
            return f" [HYPE #{hype_rank}]"

        return ""

    def _detect_and_log_changes(self):
        """Detect and log changes between snapshots"""
        changes = self.current_snapshot.compare_with(self.previous_snapshot)

        # Log new orders with proper classification
        if changes['new_orders']:
            for order in changes['new_orders']:
                classification = self._get_address_classification(order.full_address)

                # Emoji based on classification
                emoji = {
                    'mega_whale': '🐋🐋',
                    'whale': '🐋',
                    'dolphin': '🐬',
                    'fish': '🐟',
                    'shrimp': '🦐'
                }.get(classification, '')

                flag_str = f"{emoji} " if emoji else ''
                addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"
                rank_info = self._get_rank_info(order.full_address)

                logger.info(
                    f"{flag_str}New: {addr} {order.side} {order.size:.0f} "
                    f"{order.product_type} {order.duration_hours:.1f}h{rank_info}"
                )

                # Track whale entries
                if classification in ['mega_whale', 'whale']:
                    self.whale_history.append({
                        'timestamp': order.timestamp,
                        'address': order.full_address,
                        'side': order.side,
                        'size': order.size,
                        'classification': classification,
                        'event': 'NEW_WHALE'
                    })

        # Log completed orders
        if changes['completed_orders']:
            for order in changes['completed_orders']:
                classification = self._get_address_classification(order.full_address)
                emoji = {
                    'mega_whale': '🐋🐋',
                    'whale': '🐋',
                    'dolphin': '🐬'
                }.get(classification, '')

                flag_str = f"{emoji} " if emoji else ''
                addr = f"{order.full_address[:6]}...{order.full_address[-4:]}"
                rank_info = self._get_rank_info(order.full_address)

                logger.info(
                    f"{flag_str}Completed: {addr} {order.side} {order.size:.0f} "
                    f"{order.duration_hours:.1f}h (was {order.status}){rank_info}"
                )

                # Track whale completions
                if classification in ['mega_whale', 'whale']:
                    self.whale_history.append({
                        'timestamp': self.current_snapshot.timestamp,
                        'address': order.full_address,
                        'side': order.side,
                        'size': order.size,
                        'classification': classification,
                        'event': 'WHALE_COMPLETED'
                    })

        # Log status changes
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
        return self.whale_history[-10:]