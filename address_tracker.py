#!/usr/bin/env python3
"""
Address Rank Tracker
Classifies addresses by their holder rank using /rank/{address} endpoint
"""
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class AddressRankTracker:
    """Track and classify addresses by holder rank"""

    def __init__(self, hypurr_client, config: dict = None):
        self.client = hypurr_client
        self.config = config or {}

        self.addresses = {}  # address -> data
        self.data_file = Path(self.config.get('data_file', 'address_ranks.json'))

        # Classification by HYPE holder rank
        self.rank_tiers = {
            'mega_whale': (1, 100),  # Top 100 holders
            'whale': (101, 500),  # Top 500 holders
            'dolphin': (501, 2000),  # Top 2000 holders
            'fish': (2001, 10000),  # Top 10k holders
            'shrimp': (10001, 999999)  # Everyone else
        }

        # Load existing data
        self._load_data()

        logger.info(f"Address Rank Tracker initialized ({len(self.addresses)} addresses loaded)")

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
                'last_rank_check': None,

                # Rank data (from /rank endpoint)
                'hype_rank': None,
                'usdc_rank': None,
                'all_ranks': {},
                'token_count': 0,

                # TWAP statistics
                'twap_count': 0,
                'total_twap_volume': 0,
                'buy_volume': 0,
                'sell_volume': 0,
                'net_volume': 0,

                # Classification
                'classification': 'unknown',
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
        """Update address with TWAP data"""
        data = self.addresses[address]

        data['twap_count'] += 1
        data['total_twap_volume'] += order.size

        if order.is_buy_side:
            data['buy_volume'] += order.size
        else:
            data['sell_volume'] += order.size

        data['net_volume'] = data['buy_volume'] - data['sell_volume']

    def fetch_and_update_rank(self, address: str) -> bool:
        """
        Fetch holder rank for an address and update classification

        Returns:
            True if successfully fetched rank
        """
        logger.debug(f"📡 Fetching rank for {address[:10]}...")

        endpoint = f'rank/{address}'
        result = self.client._get(endpoint)

        if result and isinstance(result, dict) and len(result) > 0:
            data = self.addresses[address]

            # Store all ranks
            data['all_ranks'] = result
            data['token_count'] = len(result)
            data['last_rank_check'] = datetime.now().isoformat()

            # Extract key ranks
            data['hype_rank'] = result.get('HYPE')
            data['usdc_rank'] = result.get('USDC')

            # Classify by HYPE rank
            self._classify_by_rank(address)

            rank_str = f"#{data['hype_rank']}" if data['hype_rank'] else "N/A"
            logger.info(f"✅ {address[:10]}: HYPE rank {rank_str} ({data['classification']})")
            return True
        else:
            logger.warning(f"⚠️ No rank data for {address[:10]}")
            return False

    def _classify_by_rank(self, address: str):
        """Classify address based on HYPE holder rank"""
        data = self.addresses[address]
        hype_rank = data.get('hype_rank')

        if hype_rank is None:
            data['classification'] = 'unknown'
            data['is_whale'] = False
            return

        # Find tier based on rank
        for tier, (min_rank, max_rank) in self.rank_tiers.items():
            if min_rank <= hype_rank <= max_rank:
                data['classification'] = tier
                data['is_whale'] = (tier in ['mega_whale', 'whale'])
                return

        # Default to shrimp
        data['classification'] = 'shrimp'
        data['is_whale'] = False

    def update_from_snapshot(self, snapshot, fetch_ranks: bool = False):
        """
        Update all addresses from a TWAPSnapshot

        Args:
            snapshot: TWAPSnapshot object
            fetch_ranks: If True, fetch ranks for all addresses (rate-limited)
        """
        for order in snapshot.orders:
            self.add_address(order.full_address, order)

        # Optionally fetch ranks (expensive API calls)
        if fetch_ranks:
            for address in snapshot.unique_addresses:
                self.fetch_and_update_rank(address)

        self._save_data()

    def batch_update_ranks(self, addresses: list = None, max_addresses: int = 20):
        """
        Batch update ranks for multiple addresses

        Args:
            addresses: List of addresses (if None, updates all unknown)
            max_addresses: Maximum addresses to update in one batch
        """
        if addresses is None:
            # Get addresses that need rank updates
            addresses = [
                addr for addr, data in self.addresses.items()
                if data.get('classification') == 'unknown' or data.get('last_rank_check') is None
            ]

        # Limit batch size
        addresses = addresses[:max_addresses]

        if not addresses:
            logger.info("All addresses have up-to-date ranks")
            return

        logger.info(f"Updating ranks for {len(addresses)} addresses...")

        success_count = 0
        for address in addresses:
            if self.fetch_and_update_rank(address):
                success_count += 1

        self._save_data()
        logger.info(f"Updated {success_count}/{len(addresses)} address ranks")

    def get_classification_summary(self) -> Dict[str, int]:
        """Get count of addresses in each tier"""
        summary = {tier: 0 for tier in self.rank_tiers.keys()}
        summary['unknown'] = 0

        for data in self.addresses.values():
            tier = data.get('classification', 'unknown')
            summary[tier] = summary.get(tier, 0) + 1

        return summary

    def get_top_addresses(self, limit: int = 10, by: str = 'rank') -> list:
        """
        Get top addresses by rank or TWAP volume

        Args:
            limit: Number of addresses to return
            by: Sort by 'rank' or 'volume'
        """
        if by == 'rank':
            # Sort by HYPE rank (lower = better)
            sorted_addresses = sorted(
                self.addresses.items(),
                key=lambda x: x[1].get('hype_rank') or 999999
            )
        else:  # volume
            sorted_addresses = sorted(
                self.addresses.items(),
                key=lambda x: x[1].get('total_twap_volume', 0),
                reverse=True
            )

        return [
            {
                'address': addr,
                'hype_rank': data.get('hype_rank'),
                'classification': data.get('classification'),
                'twap_volume': data.get('total_twap_volume', 0),
                'twap_count': data.get('twap_count', 0)
            }
            for addr, data in sorted_addresses[:limit]
        ]

    def export_report(self, filename: str = 'address_classification_report.json'):
        """Export detailed classification report"""
        report = {
            'generated': datetime.now().isoformat(),
            'total_addresses': len(self.addresses),
            'classifications': self.get_classification_summary(),
            'top_by_rank': self.get_top_addresses(20, by='rank'),
            'top_by_volume': self.get_top_addresses(20, by='volume'),
            'all_addresses': sorted(
                self.addresses.values(),
                key=lambda x: x.get('hype_rank') or 999999
            )
        }

        report_file = Path(filename)
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"📊 Report exported to {report_file}")
        return report

    def log_summary(self):
        """Log classification summary"""
        summary = self.get_classification_summary()

        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 ADDRESS CLASSIFICATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total Addresses: {len(self.addresses)}")
        logger.info("")
        logger.info("📊 By Classification:")
        for tier in ['mega_whale', 'whale', 'dolphin', 'fish', 'shrimp', 'unknown']:
            count = summary.get(tier, 0)
            if count > 0:
                emoji = {'mega_whale': '🐋🐋', 'whale': '🐋', 'dolphin': '🐬',
                         'fish': '🐟', 'shrimp': '🦐', 'unknown': '❓'}
                logger.info(f"   {emoji.get(tier, '  ')} {tier:12}: {count:3}")
        logger.info("=" * 60)

    def log_whale_activity(self, snapshot):
        """
        Log whale/dolphin TWAP activity from a snapshot

        Args:
            snapshot: TWAPSnapshot object with orders
        """
        for order in snapshot.orders:
            # Check if we know this address's classification
            addr_data = self.addresses.get(order.full_address)

            if addr_data:
                classification = addr_data.get('classification', 'unknown')
                hype_rank = addr_data.get('hype_rank')

                # Only log if whale/dolphin and active
                if classification in ['mega_whale', 'whale', 'dolphin'] and order.is_active:
                    emoji = {
                        'mega_whale': '🐋🐋',
                        'whale': '🐋',
                        'dolphin': '🐬'
                    }.get(classification, '')

                    logger.info("")
                    logger.info(f"{emoji} {classification.upper()} ACTIVITY:")
                    logger.info(f"   Address: {order.display_address}")
                    logger.info(f"   Rank: #{hype_rank}")
                    logger.info(
                        f"   Order: {order.side} {order.size:,.0f} {order.symbol} over {order.duration_hours:.1f}h")
                    logger.info(f"   Status: {order.status}")

    def log_top_traders(self, limit: int = 5, by: str = 'rank'):
        """
        Log top traders

        Args:
            limit: Number of traders to show
            by: Sort by 'rank' or 'volume'
        """
        logger.info("")
        logger.info("🏆 Top Traders by HYPE Rank:")
        logger.info("-" * 60)

        top_addresses = self.get_top_addresses(limit, by=by)

        if not top_addresses:
            logger.info("   No ranked addresses yet")
            return

        for i, addr_data in enumerate(top_addresses, 1):
            rank = addr_data['hype_rank']
            classification = addr_data['classification']
            addr = addr_data['address']

            emoji = {
                'mega_whale': '🐋🐋',
                'whale': '🐋',
                'dolphin': '🐬',
                'fish': '🐟',
                'shrimp': '🦐'
            }.get(classification, '❓')

            if rank:
                logger.info(f"{i}. {emoji} {addr[:10]}... | Rank #{rank:6} | {classification}")
            else:
                logger.info(f"{i}. ❓ {addr[:10]}... | No rank data")