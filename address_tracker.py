#!/usr/bin/env python3
"""
Address Volume Tracker
Classifies addresses by their TWAP volume in HYPE tokens
"""
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class AddressVolumeTracker:
    """Track and classify addresses by TWAP volume"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.addresses = {}  # address -> data
        self.data_file = Path(self.config.get('data_file', 'address_volumes.json'))

        # Classification by TWAP volume in HYPE tokens
        self.volume_tiers = {
            'mega_whale': 50_000,      # 50K+ HYPE (~$2M+)
            'whale': 10_000,           # 10K+ HYPE (~$400K+)
            'dolphin': 5_000,          # 5K+ HYPE (~$200K+)
            'fish': 1_000,             # 1K+ HYPE (~$40K+)
            'shrimp': 0                # < 1K HYPE
        }

        # Load existing data
        self._load_data()

        logger.info(f"Address Volume Tracker initialized ({len(self.addresses)} addresses loaded)")

    def _load_data(self):
        """Load existing address data"""
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r') as f:
                    self.addresses = json.load(f)
                logger.info(f"Loaded {len(self.addresses)} addresses")
            except Exception as e:
                logger.error(f"Error loading data: {e}")
                self.addresses = {}

    def _save_data(self):
        """Save address data"""
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.addresses, f, indent=2)
            logger.debug(f"Saved {len(self.addresses)} addresses")
        except Exception as e:
            logger.error(f"Error saving data: {e}")

    def add_address(self, address: str, twap_order=None):
        """Add or update address"""
        if address not in self.addresses:
            self.addresses[address] = {
                'address': address,
                'first_seen': datetime.now().isoformat(),
                'last_seen': datetime.now().isoformat(),

                # TWAP statistics
                'twap_count': 0,
                'total_twap_volume': 0,
                'buy_volume': 0,
                'sell_volume': 0,
                'net_volume': 0,

                # Classification
                'classification': 'shrimp',
                'is_whale': False
            }
            logger.debug(f"New address: {address[:10]}...")
        else:
            self.addresses[address]['last_seen'] = datetime.now().isoformat()

        # Update with TWAP order if provided
        if twap_order:
            self._update_with_twap(address, twap_order)

        return self.addresses[address]

    def _update_with_twap(self, address: str, order):
        """Update address with TWAP data and reclassify"""
        data = self.addresses[address]

        data['twap_count'] += 1
        data['total_twap_volume'] += order.size

        if order.is_buy_side:
            data['buy_volume'] += order.size
        else:
            data['sell_volume'] += order.size

        data['net_volume'] = data['buy_volume'] - data['sell_volume']

        # Reclassify based on new volume
        self._classify_by_volume(address)

    def _classify_by_volume(self, address: str):
        """Classify address based on total TWAP volume in HYPE"""
        data = self.addresses[address]
        total_volume = data.get('total_twap_volume', 0)

        if total_volume >= self.volume_tiers['mega_whale']:
            tier = 'mega_whale'
        elif total_volume >= self.volume_tiers['whale']:
            tier = 'whale'
        elif total_volume >= self.volume_tiers['dolphin']:
            tier = 'dolphin'
        elif total_volume >= self.volume_tiers['fish']:
            tier = 'fish'
        else:
            tier = 'shrimp'

        data['classification'] = tier
        data['is_whale'] = (tier in ['mega_whale', 'whale'])

    def update_from_snapshot(self, snapshot):
        """
        Update all addresses from a TWAPSnapshot

        Args:
            snapshot: TWAPSnapshot object
        """
        for order in snapshot.orders:
            self.add_address(order.full_address, order)

        self._save_data()

    def get_classification_summary(self) -> Dict[str, int]:
        """Get count of addresses in each tier"""
        summary = {tier: 0 for tier in ['mega_whale', 'whale', 'dolphin', 'fish', 'shrimp']}

        for data in self.addresses.values():
            tier = data.get('classification', 'shrimp')
            summary[tier] = summary.get(tier, 0) + 1

        return summary

    def get_top_addresses(self, limit: int = 10) -> list:
        """
        Get top addresses by TWAP volume

        Args:
            limit: Number of addresses to return
        """
        sorted_addresses = sorted(
            self.addresses.items(),
            key=lambda x: x[1].get('total_twap_volume', 0),
            reverse=True
        )

        return [
            {
                'address': addr,
                'classification': data.get('classification'),
                'twap_volume': data.get('total_twap_volume', 0),
                'twap_count': data.get('twap_count', 0),
                'net_volume': data.get('net_volume', 0)
            }
            for addr, data in sorted_addresses[:limit]
        ]

    def export_report(self, filename: str = 'address_volume_report.json'):
        """Export detailed classification report"""
        report = {
            'generated': datetime.now().isoformat(),
            'total_addresses': len(self.addresses),
            'classifications': self.get_classification_summary(),
            'top_by_volume': self.get_top_addresses(20),
            'all_addresses': sorted(
                self.addresses.values(),
                key=lambda x: x.get('total_twap_volume', 0),
                reverse=True
            )
        }

        report_file = Path(filename)
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Report exported to {report_file}")
        return report

    def log_summary(self):
        """Log classification summary"""
        summary = self.get_classification_summary()

        logger.info("")
        logger.info("=" * 60)
        logger.info("ADDRESS CLASSIFICATION SUMMARY (BY HYPE VOLUME)")
        logger.info("=" * 60)
        logger.info(f"Total Addresses: {len(self.addresses)}")
        logger.info("")
        logger.info("By Classification:")
        for tier in ['mega_whale', 'whale', 'dolphin', 'fish', 'shrimp']:
            count = summary.get(tier, 0)
            if count > 0:
                threshold = f"({self.volume_tiers[tier]:,}+ HYPE)" if tier != 'shrimp' else "(<1K HYPE)"
                logger.info(f"   {tier:12}: {count:3} {threshold}")
        logger.info("=" * 60)

    def log_whale_activity(self, snapshot):
        """
        Log whale/dolphin TWAP activity from a snapshot

        Args:
            snapshot: TWAPSnapshot object with orders
        """
        for order in snapshot.orders:
            addr_data = self.addresses.get(order.full_address)

            if addr_data:
                classification = addr_data.get('classification', 'shrimp')
                total_volume = addr_data.get('total_twap_volume', 0)

                # Only log if whale/dolphin and active
                if classification in ['mega_whale', 'whale', 'dolphin'] and order.is_active:
                    logger.info("")
                    logger.info(f"{classification.upper()} ACTIVITY:")
                    logger.info(f"   Address: {order.display_address}")
                    logger.info(f"   Total Volume: {total_volume:,.0f} HYPE")
                    logger.info(
                        f"   Order: {order.side} {order.size:,.0f} {order.symbol} over {order.duration_hours:.1f}h")
                    logger.info(f"   Status: {order.status}")

    def log_top_traders(self, limit: int = 5):
        """
        Log top traders by volume

        Args:
            limit: Number of traders to show
        """
        logger.info("")
        logger.info("Top Traders by TWAP Volume (HYPE):")
        logger.info("-" * 60)

        top_addresses = self.get_top_addresses(limit)

        if not top_addresses:
            logger.info("   No addresses tracked yet")
            return

        for i, addr_data in enumerate(top_addresses, 1):
            volume = addr_data['twap_volume']
            classification = addr_data['classification']
            addr = addr_data['address']

            logger.info(f"{i}. {addr[:10]}... | {volume:,.0f} HYPE | {classification}")