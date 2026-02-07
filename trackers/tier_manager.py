#!/usr/bin/env python3
"""
Tier Manager
============
Manages tiered whale address fetching to respect API limits.

Tiers (by position value):
- VIP: Hand-picked addresses (every 1 min)
- Tier 1: $5M+ positions (every 1 min)
- Tier 2: $1M-5M positions (every 5 min)
- Tier 3: $500K-1M positions (every 15 min)
- Tier 4: $250K-500K positions (every 30 min)
- Tier 5: $100K-250K positions (every 60 min)

Usage:
    from tier_manager import TierManager
    from storage import SQLiteBackend

    storage = SQLiteBackend()
    tier_mgr = TierManager(storage)

    # In main loop:
    tier_mgr.increment_cycle()
    addresses = tier_mgr.get_all_addresses_for_current_cycle()

    # Hourly refresh:
    if tier_mgr.should_refresh_tiers():
        tier_mgr.refresh_tiers_from_snapshots()
"""
import logging
from datetime import datetime
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

# Tier thresholds (position value in USD)
TIER_THRESHOLDS = {
    1: 5_000_000,  # $5M+
    2: 1_000_000,  # $1M-5M
    3: 500_000,  # $500K-1M
    4: 250_000,  # $250K-500K
    5: 100_000,  # $100K-250K
}

# Tier fetch frequencies (in cycles, 1 cycle = 1 minute)
TIER_FREQUENCIES = {
    'vip': 1,  # Every cycle
    1: 1,  # Every cycle
    2: 5,  # Every 5 cycles
    3: 15,  # Every 15 cycles
    4: 30,  # Every 30 cycles
    5: 60,  # Every 60 cycles
}


class TierManager:
    """
    Manages tiered whale address fetching.

    Tracks cycle count and determines which addresses
    to fetch each cycle based on their tier.
    """

    def __init__(self, storage):
        """
        Initialize tier manager.

        Args:
            storage: SQLiteBackend instance
        """
        self.storage = storage
        self.cycle_count = 0
        self.last_tier_refresh = None

        # Cache for event tracking addresses (refreshed with tiers)
        self._event_tracking_addresses: Set[str] = set()
        self._refresh_event_tracking_cache()

        logger.info(f"TierManager initialized (VIP: {self.storage.get_vip_count()}, "
                    f"Event tracking: {len(self._event_tracking_addresses)})")

    # =========================================================================
    # CYCLE MANAGEMENT
    # =========================================================================

    def get_current_cycle(self) -> int:
        """
        Get current cycle number (1-60).

        Returns:
            Cycle number, or 0 if no cycles yet
        """
        if self.cycle_count == 0:
            return 0
        return ((self.cycle_count - 1) % 60) + 1

    def increment_cycle(self) -> int:
        """
        Increment cycle counter and return new cycle number.

        Returns:
            Current cycle number (1-60)
        """
        self.cycle_count += 1
        cycle = self.get_current_cycle()

        logger.debug(f"Cycle {self.cycle_count} (position in hour: {cycle}/60)")

        return cycle

    def reset_cycles(self):
        """Reset cycle counter to 0."""
        self.cycle_count = 0
        logger.info("Cycle counter reset")

    # =========================================================================
    # ADDRESS SELECTION
    # =========================================================================

    def get_addresses_for_current_cycle(self) -> Dict[str, List[str]]:
        """
        Get addresses to fetch for current cycle, organized by tier.

        Returns:
            Dict with keys: 'vip', 'tier1', 'tier2', 'tier3', 'tier4', 'tier5'
            Each value is a list of addresses
        """
        return self.storage.get_addresses_for_cycle(self.get_current_cycle())

    def get_all_addresses_for_current_cycle(self) -> List[str]:
        """
        Get flat deduplicated list of addresses for current cycle.
        VIP addresses come first, then by tier.

        Returns:
            List of addresses to fetch this cycle
        """
        return self.storage.get_all_addresses_for_cycle(self.get_current_cycle())

    def _refresh_event_tracking_cache(self):
        """Refresh the cached set of event tracking addresses."""
        vip = set(self.storage.get_vip_address_list())
        tier1 = set(self.storage.get_addresses_by_tier(1))
        self._event_tracking_addresses = vip | tier1

    def get_event_tracking_addresses(self) -> Set[str]:
        """
        Get addresses that should have event tracking (VIP + Tier1).

        Only these addresses get whale_events detection because
        they're fetched every cycle (consistent data for comparison).

        Returns:
            Set of addresses
        """
        return self._event_tracking_addresses.copy()

    def is_event_tracking_address(self, address: str) -> bool:
        """
        Check if an address should have event tracking.

        Args:
            address: Wallet address

        Returns:
            True if address is VIP or Tier1
        """
        return address in self._event_tracking_addresses

    # =========================================================================
    # TIER REFRESH
    # =========================================================================

    def should_refresh_tiers(self) -> bool:
        """
        Check if tier refresh is needed.

        Refresh happens:
        - On first cycle (initialization)
        - Every 60 cycles (hourly)

        Returns:
            True if refresh needed
        """
        if self.last_tier_refresh is None:
            return True

        cycle = self.get_current_cycle()
        return cycle == 60

    def _get_current_tiers(self) -> Dict[str, int]:
        """
        Get current tier assignments from database.

        Returns:
            Dict of address -> tier number (only active addresses with a tier)
        """
        self.storage.cursor.execute("""
            SELECT address, tier FROM whale_addresses
            WHERE is_active = 1 AND tier IS NOT NULL
        """)
        return {row[0]: row[1] for row in self.storage.cursor.fetchall()}

    @staticmethod
    def _format_value(value: float) -> str:
        """Format USD value for log display."""
        if abs(value) >= 1_000_000:
            return f"${value / 1_000_000:.1f}M"
        elif abs(value) >= 1_000:
            return f"${value / 1_000:.0f}K"
        else:
            return f"${value:.0f}"

    def refresh_tiers_from_snapshots(self) -> Dict[str, dict]:
        """
        Recalculate tiers from latest snapshot data.

        Pulls both position_value (from perp_snapshots) and
        portfolio_value (from portfolio_snapshots) for each address.

        Returns:
            Dict of address -> {'tier': int, 'position_value': float, 'portfolio_value': float}
        """
        logger.info("Refreshing tier assignments from snapshot data...")
        start_time = datetime.now()

        # Snapshot current tiers BEFORE recalculating
        old_tiers = self._get_current_tiers()

        # Get latest position values from perp_snapshots
        position_values = self._get_latest_position_values()
        logger.info(f"Found {len(position_values)} addresses with perp positions")

        # Get latest portfolio values from portfolio_snapshots
        portfolio_values = self._get_latest_portfolio_values()
        logger.info(f"Found {len(portfolio_values)} addresses with portfolio snapshots")

        # Combine: use position_value for tier, but store both
        all_addresses = set(position_values.keys()) | set(portfolio_values.keys())

        results = {}
        new_tiers = {}
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

        for address in all_addresses:
            pos_value = position_values.get(address, 0)
            port_value = portfolio_values.get(address, 0)

            # Calculate tier based on position value
            tier = self._calculate_tier(pos_value)

            if tier is not None:
                results[address] = {
                    'tier': tier,
                    'position_value': pos_value,
                    'portfolio_value': port_value,
                }
                new_tiers[address] = tier
                tier_counts[tier] += 1

                # Update in database
                self._update_address_tier(address, tier, pos_value, port_value)
            else:
                # Below all thresholds - mark inactive
                self._deactivate_address(address)

        self.storage.conn.commit()

        # =====================================================================
        # DETECT AND LOG TIER CHANGES
        # =====================================================================
        promoted = []
        demoted = []
        new_entries = []
        deactivated = []

        # Check for promotions, demotions, new entries
        for address, new_tier in new_tiers.items():
            old_tier = old_tiers.get(address)
            pos_value = position_values.get(address, 0)

            if old_tier is None:
                # New address entering the tier system
                new_entries.append((address, new_tier, pos_value))
            elif new_tier < old_tier:
                # Lower tier number = higher tier = promotion
                promoted.append((address, old_tier, new_tier, pos_value))
            elif new_tier > old_tier:
                # Higher tier number = lower tier = demotion
                demoted.append((address, old_tier, new_tier, pos_value))

        # Check for deactivated (were in old_tiers, not in new_tiers)
        for address, old_tier in old_tiers.items():
            if address not in new_tiers:
                pos_value = position_values.get(address, 0)
                deactivated.append((address, old_tier, pos_value))

        # Log summary at INFO level
        changes_total = len(promoted) + len(demoted) + len(new_entries) + len(deactivated)

        # Refresh event tracking cache
        self._refresh_event_tracking_cache()

        self.last_tier_refresh = datetime.now()
        elapsed = (self.last_tier_refresh - start_time).total_seconds()

        logger.info(f"Tier refresh complete in {elapsed:.1f}s")
        logger.info(f"Tier distribution: T1={tier_counts[1]}, T2={tier_counts[2]}, "
                    f"T3={tier_counts[3]}, T4={tier_counts[4]}, T5={tier_counts[5]}")
        logger.info(f"Event tracking addresses: {len(self._event_tracking_addresses)} "
                    f"(VIP + Tier1)")

        if changes_total > 0:
            logger.info(
                f"Tier changes: {len(promoted)} promoted, {len(demoted)} demoted, "
                f"{len(new_entries)} new, {len(deactivated)} deactivated"
            )

            # Log individual changes at DEBUG level
            for addr, old_t, new_t, pos_val in sorted(promoted, key=lambda x: x[2]):
                logger.debug(
                    f"⬆ {addr} T{old_t}→T{new_t} (pos: {self._format_value(pos_val)})"
                )

            for addr, old_t, new_t, pos_val in sorted(demoted, key=lambda x: x[2]):
                logger.debug(
                    f"⬇ {addr} T{old_t}→T{new_t} (pos: {self._format_value(pos_val)})"
                )

            for addr, tier, pos_val in sorted(new_entries, key=lambda x: x[1]):
                logger.debug(
                    f"🆕 {addr} T{tier} (pos: {self._format_value(pos_val)})"
                )

            for addr, old_t, pos_val in deactivated:
                logger.debug(
                    f"❌ {addr} was T{old_t} (pos: {self._format_value(pos_val)})"
                )
        else:
            logger.info("Tier changes: none")

        return results

    def _get_latest_position_values(self) -> Dict[str, float]:
        """
        Get latest total position value per address from perp_snapshots.

        Returns:
            Dict of address -> total_position_value
        """
        self.storage.cursor.execute("""
            WITH latest_times AS (
                SELECT address, MAX(snapshot_time) as latest_time
                FROM perp_snapshots
                GROUP BY address
            )
            SELECT 
                ps.address,
                SUM(ABS(ps.size * ps.entry_price)) as total_position_value
            FROM perp_snapshots ps
            JOIN latest_times lt 
                ON ps.address = lt.address 
                AND ps.snapshot_time = lt.latest_time
            GROUP BY ps.address
        """)

        return {row[0]: row[1] for row in self.storage.cursor.fetchall()}

    def _get_latest_portfolio_values(self) -> Dict[str, float]:
        """
        Get latest portfolio value per address from portfolio_snapshots.

        Returns:
            Dict of address -> total_portfolio_value
        """
        self.storage.cursor.execute("""
            WITH latest_times AS (
                SELECT address, MAX(snapshot_time) as latest_time
                FROM portfolio_snapshots
                GROUP BY address
            )
            SELECT 
                ps.address,
                ps.total_portfolio_value
            FROM portfolio_snapshots ps
            JOIN latest_times lt 
                ON ps.address = lt.address 
                AND ps.snapshot_time = lt.latest_time
        """)

        return {row[0]: row[1] for row in self.storage.cursor.fetchall()}

    def _calculate_tier(self, position_value: float) -> int:
        """
        Calculate tier based on position value.

        Args:
            position_value: Total position value in USD

        Returns:
            Tier number (1-5) or None if below all thresholds
        """
        for tier in sorted(TIER_THRESHOLDS.keys()):
            if position_value >= TIER_THRESHOLDS[tier]:
                return tier
        return None

    def _update_address_tier(self, address: str, tier: int, position_value: float, portfolio_value: float):
        """
        Update address tier in whale_addresses table.

        Args:
            address: Wallet address
            tier: Tier number (1-5)
            position_value: Total position value
            portfolio_value: Total portfolio value
        """
        timestamp = datetime.now().isoformat()

        self.storage.cursor.execute("""
            INSERT INTO whale_addresses (address, first_seen, last_updated, is_active, tier, position_value, last_tier_update)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                last_updated = ?,
                is_active = 1,
                tier = ?,
                position_value = ?,
                last_tier_update = ?
        """, (address, timestamp, timestamp, tier, position_value, timestamp,
              timestamp, tier, position_value, timestamp))

    def _deactivate_address(self, address: str):
        """
        Mark address as inactive (below all tier thresholds).

        Args:
            address: Wallet address
        """
        timestamp = datetime.now().isoformat()

        self.storage.cursor.execute("""
            UPDATE whale_addresses
            SET is_active = 0, tier = NULL, last_updated = ?
            WHERE address = ?
        """, (timestamp, address))

    # =========================================================================
    # STATISTICS & MONITORING
    # =========================================================================

    def get_cycle_summary(self) -> Dict:
        """
        Get summary of current cycle state.

        Returns:
            Dict with cycle info and address counts
        """
        addresses_by_tier = self.get_addresses_for_current_cycle()

        total_this_cycle = sum(len(addrs) for addrs in addresses_by_tier.values())

        return {
            'cycle_count': self.cycle_count,
            'current_cycle': self.get_current_cycle(),
            'last_tier_refresh': self.last_tier_refresh.isoformat() if self.last_tier_refresh else None,
            'addresses_this_cycle': {
                tier: len(addrs) for tier, addrs in addresses_by_tier.items()
            },
            'total_addresses_this_cycle': total_this_cycle,
            'event_tracking_count': len(self._event_tracking_addresses),
        }

    def estimate_api_calls(self) -> Dict:
        """
        Estimate API calls for current cycle.

        Returns:
            Dict with call estimates
        """
        addresses = self.get_all_addresses_for_current_cycle()
        num_addresses = len(addresses)

        # 2 calls per address (perp state + spot state)
        calls_this_cycle = num_addresses * 2

        return {
            'addresses': num_addresses,
            'api_calls': calls_this_cycle,
            'calls_per_minute': calls_this_cycle,  # Since we do 1 cycle per minute
            'estimated_time_seconds': num_addresses * 0.1,  # ~100ms per address in batches
        }

    def get_tier_stats(self) -> Dict:
        """
        Get current tier distribution statistics.

        Returns:
            Dict with counts and values per tier
        """
        stats = {
            'vip_count': self.storage.get_vip_count(),
            'event_tracking_count': len(self._event_tracking_addresses),
            'tiers': {},
        }

        for tier in range(1, 6):
            addresses = self.storage.get_addresses_by_tier(tier)
            stats['tiers'][tier] = {
                'count': len(addresses),
                'threshold': TIER_THRESHOLDS[tier],
                'frequency': TIER_FREQUENCIES[tier],
            }

        # Total active
        stats['total_active'] = sum(t['count'] for t in stats['tiers'].values())

        return stats

    def log_status(self):
        """Log current tier manager status."""
        summary = self.get_cycle_summary()
        api_est = self.estimate_api_calls()

        logger.info(
            f"TierManager | Cycle {summary['cycle_count']} ({summary['current_cycle']}/60) | "
            f"Addresses: {summary['total_addresses_this_cycle']} | "
            f"API calls: {api_est['api_calls']} | "
            f"Event tracking: {summary['event_tracking_count']}"
        )