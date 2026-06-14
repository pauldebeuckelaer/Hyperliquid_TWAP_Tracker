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
from typing import Dict, List, Set, Tuple, Optional
import heapq

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

AXIS_CAPS = {'position': 200, 'cash': 200, 'spot': 200}

DEACTIVATION_BREAKER_THRESHOLD = 1500

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
        self._verify_candidates: List[str] = []

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
        Recalculate tiers from latest snapshot data using tri-axis logic.

        Three parallel tier axes:
        - tier_position: from perp_snapshots notional exposure
        - tier_perp_amount: from perp_account_snapshots deposited cash
        - tier_spot: from portfolio_snapshots spot value

        Effective tier = min(tier_position, tier_perp_amount, tier_spot), treating
        None as infinity. A whale stays active if ANY axis is non-None.

        Returns:
            Dict of address -> {
                'tier': int (effective),
                'tier_position': int or None,
                'tier_perp_amount': int or None,
                'tier_spot': int or None,
                'position_value': float,
                'raw_usd_value': float,
                'spot_value': float,
                'portfolio_value': float,
            }
        """
        logger.info("Refreshing tier assignments from snapshot data...")
        start_time = datetime.now()

        # Snapshot current effective tiers BEFORE recalculating (for change detection)
        old_tiers = self._get_current_tiers()

        # Pull all three data sources
        position_values = self._get_latest_position_values()
        raw_usd_values = self._get_latest_raw_usd_values()
        spot_values = self._get_latest_spot_values()
        portfolio_values = self._get_latest_portfolio_values()
        logger.info(
            f"Snapshot data: {len(position_values)} with positions, "
            f"{len(raw_usd_values)} with perp cash, "
            f"{len(spot_values)} with spot value, "
            f"{len(portfolio_values)} with portfolio data"
        )

        pos_capped = self._top_n_addresses(position_values, AXIS_CAPS['position'])
        cash_capped = self._top_n_addresses(raw_usd_values, AXIS_CAPS['cash'])
        spot_capped = self._top_n_addresses(spot_values, AXIS_CAPS['spot'])

        # Union of addresses seen in EITHER tier-driving source
        all_addresses = (
                set(position_values.keys())
                | set(raw_usd_values.keys())
                | set(spot_values.keys())
        )

        results = {}
        new_tiers = {}  # address -> effective tier (for change detection)
        deactivation_candidates = []
        capout_candidates = []
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        axis_qualification_counts = {1: 0, 2: 0, 3: 0}
        spot_only_count = 0  # whales that ONLY spot is keeping in the system

        # Canonical active-tiered set — the ONLY population that can be
        # legitimately deactivated. Pulled once, reused by both the main
        # loop and the orphan sweep below.
        self.storage.cursor.execute("""
                    SELECT address FROM whale_addresses
                    WHERE is_active = 1 AND tier IS NOT NULL
                """)
        active_tiered_set = {row[0] for row in self.storage.cursor.fetchall()}
        logger.info(f"Active-tiered candidate pool: {len(active_tiered_set)}")

        for address in all_addresses:
            pos_value = position_values.get(address, 0)
            raw_usd = raw_usd_values.get(address, 0)
            spot_val = spot_values.get(address, 0)
            port_value = portfolio_values.get(address, 0)

            # Compute both tier axes independently
            tier_pos = self._calculate_tier(pos_value) if address in pos_capped else None
            tier_cash = self._calculate_tier(raw_usd) if address in cash_capped else None
            tier_spot = self._calculate_tier(spot_val) if address in spot_capped else None

            # Effective tier = min of non-None axis tiers
            axis_tiers = [t for t in (tier_pos, tier_cash, tier_spot) if t is not None]

            if not axis_tiers:
                if address in active_tiered_set:
                    capout_candidates.append(address)
                continue

            effective_tier = min(axis_tiers)

            results[address] = {
                'tier': effective_tier,
                'tier_position': tier_pos,
                'tier_perp_amount': tier_cash,
                'tier_spot': tier_spot,
                'position_value': pos_value,
                'raw_usd_value': raw_usd,
                'spot_value': spot_val,
                'portfolio_value': port_value,
            }
            new_tiers[address] = effective_tier
            tier_counts[effective_tier] += 1

            # Track which axes qualified the whale
            axis_qualification_counts[len(axis_tiers)] += 1
            if tier_pos is None and tier_cash is None and tier_spot is not None:
                spot_only_count += 1

            self._update_address_tier(
                address, effective_tier,
                tier_pos, tier_cash, tier_spot,
                pos_value, raw_usd, spot_val,
            )

        # Catch orphans: active whales with no fresh data on any axis this refresh.
        # `tier` is the effective tier — set whenever any axis qualifies the whale,
        # cleared on deactivation. So `tier IS NOT NULL` is the canonical
        # "currently axis-qualified" signal.
        seen_addresses = all_addresses
        for address in active_tiered_set - seen_addresses:
            deactivation_candidates.append(address)

        # =====================================================================
        # TWO-STRIKE DEACTIVATION — two independent paths:
        #   capout_candidates    — present in data, ranked out by cap/threshold.
        #                          Two-strike, NO verify fetch, NO breaker
        #                          (a cap-out is a policy decision, not an outage).
        #   deactivation_candidates — disappeared (no data any axis). Two-strike,
        #                          verify fetch + outage breaker (unchanged).
        # =====================================================================
        strike_time = datetime.now().isoformat()
        pending_first_strike = []  # disappeared, strike one (queued for verify)
        executed_deactivations = []  # all deactivations this refresh (both paths)

        # --- CAP-OUT PATH: two-strike, no verify, no breaker ---
        capout_first_strike = 0
        for address in capout_candidates:
            self.storage.cursor.execute(
                "SELECT pending_deactivation FROM whale_addresses "
                "WHERE address = ?", (address,))
            row = self.storage.cursor.fetchone()
            if row and row[0]:
                self._deactivate_address(address)  # strike two
                executed_deactivations.append(address)
            else:
                self.storage.cursor.execute(
                    "UPDATE whale_addresses SET pending_deactivation = ? "
                    "WHERE address = ?", (strike_time, address))
                capout_first_strike += 1  # strike one

        # --- DISAPPEARED PATH: two-strike + verify + outage breaker (unchanged) ---
        if len(deactivation_candidates) > DEACTIVATION_BREAKER_THRESHOLD:
            logger.error(
                f"🚨 DEACTIVATION BREAKER TRIPPED: "
                f"{len(deactivation_candidates)} disappeared candidates "
                f"(threshold {DEACTIVATION_BREAKER_THRESHOLD}). Suspected "
                f"collection outage — no strikes set, no deactivations "
                f"executed on disappeared path this refresh."
            )
        else:
            for address in deactivation_candidates:
                self.storage.cursor.execute(
                    "SELECT pending_deactivation FROM whale_addresses "
                    "WHERE address = ?", (address,))
                row = self.storage.cursor.fetchone()
                if row and row[0]:
                    self._deactivate_address(address)  # strike two
                    executed_deactivations.append(address)
                else:
                    self.storage.cursor.execute(
                        "UPDATE whale_addresses SET pending_deactivation = ? "
                        "WHERE address = ?", (strike_time, address))
                    pending_first_strike.append(address)  # strike one

        if capout_first_strike or pending_first_strike:
            logger.info(
                f"⏳ Strike one: {capout_first_strike} cap-outs (no verify), "
                f"{len(pending_first_strike)} disappeared (verify queued)")

        self._verify_candidates = pending_first_strike  # only disappeared get verified
        self.storage.conn.commit()

        # =====================================================================
        # DETECT AND LOG TIER CHANGES (effective tier transitions)
        # =====================================================================
        promoted = []
        demoted = []
        new_entries = []
        deactivated = []

        for address, new_tier in new_tiers.items():
            old_tier = old_tiers.get(address)
            pos_value = position_values.get(address, 0)
            raw_usd = raw_usd_values.get(address, 0)
            spot_val = spot_values.get(address, 0)

            if old_tier is None:
                new_entries.append((address, new_tier, pos_value, raw_usd, spot_val))
            elif new_tier < old_tier:
                promoted.append((address, old_tier, new_tier, pos_value, raw_usd, spot_val))
            elif new_tier > old_tier:
                demoted.append((address, old_tier, new_tier, pos_value, raw_usd, spot_val))

        for address in executed_deactivations:
            old_tier = old_tiers.get(address)
            pos_value = position_values.get(address, 0)
            raw_usd = raw_usd_values.get(address, 0)
            spot_val = spot_values.get(address, 0)
            deactivated.append((address, old_tier, pos_value, raw_usd, spot_val))

        if pending_first_strike:
            logger.info(
                f"⏳ {len(pending_first_strike)} whales pending verification "
                f"(first strike — verify fetch queued)")

        changes_total = len(promoted) + len(demoted) + len(new_entries) + len(deactivated)

        # Refresh event tracking cache
        self._refresh_event_tracking_cache()

        self.last_tier_refresh = datetime.now()
        elapsed = (self.last_tier_refresh - start_time).total_seconds()

        logger.info(f"Tier refresh complete in {elapsed:.1f}s")
        logger.info(
            f"Tier distribution: T1={tier_counts[1]}, T2={tier_counts[2]}, "
            f"T3={tier_counts[3]}, T4={tier_counts[4]}, T5={tier_counts[5]}"
        )
        logger.info(
            f"Axis qualification: "
            f"{axis_qualification_counts[3]} on all three axes, "
            f"{axis_qualification_counts[2]} on two axes, "
            f"{axis_qualification_counts[1]} on one axis "
            f"({spot_only_count} spot-axis only)"
        )
        logger.info(f"Event tracking addresses: {len(self._event_tracking_addresses)} (VIP + Tier1)"
        )

        if changes_total > 0:
            logger.info(
                f"Tier changes: {len(promoted)} promoted, {len(demoted)} demoted, "
                f"{len(new_entries)} new, {len(deactivated)} deactivated"
            )

            for addr, old_t, new_t, pos_val, raw_val, spot_val in sorted(promoted, key=lambda x: x[2]):
                logger.debug(
                    f"⬆ {addr} T{old_t}→T{new_t} "
                    f"(pos: {self._format_value(pos_val)}, cash: {self._format_value(raw_val)}, spot: {self._format_value(spot_val)})"
                )

            for addr, old_t, new_t, pos_val, raw_val, spot_val in sorted(demoted, key=lambda x: x[2]):
                logger.debug(
                    f"⬇ {addr} T{old_t}→T{new_t} "
                    f"(pos: {self._format_value(pos_val)}, cash: {self._format_value(raw_val)}, spot: {self._format_value(spot_val)})"
                )

            for addr, tier, pos_val, raw_val, spot_val in sorted(new_entries, key=lambda x: x[1]):
                logger.debug(
                    f"🆕 {addr} T{tier} "
                    f"(pos: {self._format_value(pos_val)}, cash: {self._format_value(raw_val)}, spot: {self._format_value(spot_val)})"
                )

            for addr, old_t, pos_val, raw_val, spot_val in deactivated:
                logger.debug(
                    f"❌ {addr} was T{old_t} "
                    f"(pos: {self._format_value(pos_val)}, cash: {self._format_value(raw_val)}, spot: {self._format_value(spot_val)})"
                )
        else:
            logger.info("Tier changes: none")

        return results

    def pop_verify_candidates(self) -> List[str]:
        """First-strike addresses from the last refresh, cleared on read.
        main.py hands these to the collector for a targeted fetch_and_persist
        so the next refresh judges them on fresh data."""
        out = self._verify_candidates
        self._verify_candidates = []
        return out

    def _get_latest_position_values(self) -> Dict[str, float]:
        """
        Get latest total position value per address from perp_snapshots.

        Only considers snapshots from the last 130 minutes - this ensures
        tier assignment runs on FRESH data. Whales whose latest snapshot
        is older than this window are treated as having no positions
        and get deactivated by refresh_tiers_from_snapshots().

        Why 130 minutes? T5 fetches every 60 cycles. 130 gives slack for
        fetch latency, brief API outages, and the gap between the cycle
        when a whale was fetched and the next tier refresh.

        Returns:
            Dict of address -> total_position_value
        """
        self.storage.cursor.execute("""
            WITH latest_times AS (
                SELECT address, MAX(snapshot_time) as latest_time
                FROM perp_snapshots
                WHERE snapshot_time >= strftime('%Y-%m-%dT%H:%M:%f', 'now', '-540 minutes')
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

    def _get_latest_raw_usd_values(self) -> Dict[str, float]:
        """
        Get latest deposited capital per address from perp_account_snapshots.

        Reads total_account_value (mainnet + HIP-3 consolidated account equity).
        This is the "cash" axis for the tri-axis tier system — independent of
        open positions, catches dormant-loaded whales who closed positions but
        kept capital.

        NOTE (fix): previously read total_raw_usd_all, which is equity MINUS
        notional and therefore structurally negative for any leveraged account.
        That made this axis return None for every leveraged whale — i.e. the
        cash axis was effectively dead. total_account_value is the real
        deposited capital (>= 0 in normal cases) and correctly drives tiering.

        Same 130-minute freshness window as _get_latest_position_values to
        avoid letting stale snapshots drive tier assignments.

        Returns:
            Dict of address -> total_account_value
        """
        self.storage.cursor.execute("""
            WITH latest_times AS (
                SELECT address, MAX(snapshot_time) as latest_time
                FROM perp_account_snapshots
                WHERE snapshot_time >= strftime('%Y-%m-%dT%H:%M:%f', 'now', '-540 minutes')
                GROUP BY address
            )
            SELECT 
                pas.address,
                pas.total_account_value
            FROM perp_account_snapshots pas
            JOIN latest_times lt 
                ON pas.address = lt.address 
                AND pas.snapshot_time = lt.latest_time
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

    def _get_latest_spot_values(self) -> Dict[str, float]:
        """
        Get latest spot holdings value per address from portfolio_snapshots.spot_value.

        Reads spot_value (USD total of all qualifying spot holdings, priced and
        aggregated upstream via TokenFilter). This is the "spot" axis for the
        tri-axis tier system — independent of perp positions and perp cash,
        catches whales whose value lives primarily in spot holdings.

        Freshness window is 9h (540 min), wider than the 130min window used by
        position/cash axes. Reason: portfolio_snapshots writes are slow-ladder
        driven, with max cadence of 8h for T4/T5 whales. 9h gives 1h slack for
        slow-ladder execution latency. A tighter window would deactivate the
        spot axis between slow-ladder fires for lower-tier whales.

        Note: spot_value is always >= 0 (no leverage on spot). Sub-threshold
        values are handled by _calculate_tier returning None.

        Returns:
            Dict of address -> spot_value
        """
        self.storage.cursor.execute("""
            WITH latest_times AS (
                SELECT address, MAX(snapshot_time) as latest_time
                FROM portfolio_snapshots
                WHERE snapshot_time >= strftime('%Y-%m-%dT%H:%M:%f', 'now', '-540 minutes')
                GROUP BY address
            )
            SELECT 
                ps.address,
                ps.spot_value
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

    @staticmethod
    def _top_n_addresses(values: Dict[str, float], n: int) -> Set[str]:
        if len(values) <= n:
            return set(values.keys())
        return {a for a, _ in heapq.nlargest(n, values.items(), key=lambda kv: kv[1])}

    def _update_address_tier(
            self,
            address: str,
            effective_tier: int,
            tier_position: Optional[int],
            tier_perp_amount: Optional[int],
            tier_spot: Optional[int],
            position_value: float,
            raw_usd_value: float,
            spot_value: float,
    ):
        """
        Update address tier in whale_addresses table.

        Writes all three axis tiers plus the derived effective tier and the
        three driving dollar values.

        Args:
            address: Wallet address
            effective_tier: min(tier_position, tier_perp_amount, tier_spot)
            tier_position: Tier from notional exposure (None if subthreshold)
            tier_perp_amount: Tier from deposited cash (None if subthreshold)
            tier_spot: Tier from spot holdings (None if subthreshold)
            position_value: Total notional exposure value
            raw_usd_value: Total deposited cash (may be negative)
            spot_value: Total spot holdings value
        """
        timestamp = datetime.now().isoformat()

        self.storage.cursor.execute("""
            INSERT INTO whale_addresses (
                address, first_seen, last_updated, is_active,
                tier, tier_position, position_value,
                tier_perp_amount, raw_usd_value,
                tier_spot, spot_value,
                last_tier_update
            )
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                last_updated = ?,
                is_active = 1,
                tier = ?,
                tier_position = ?,
                position_value = ?,
                tier_perp_amount = ?,
                raw_usd_value = ?,
                tier_spot = ?,
                spot_value = ?,
                last_tier_update = ?
        """, (
            address, timestamp, timestamp,
            effective_tier, tier_position, position_value,
            tier_perp_amount, raw_usd_value,
            tier_spot, spot_value,
            timestamp,
            timestamp,
            effective_tier, tier_position, position_value,
            tier_perp_amount, raw_usd_value,
            tier_spot, spot_value,
            timestamp,
        ))

    def _deactivate_address(self, address: str):
        """
        Mark address as inactive (below all tier thresholds on ALL axes).

        Clears all axis tier columns and value columns to prevent stale data
        from leaking back into queries.
        """
        timestamp = datetime.now().isoformat()

        self.storage.cursor.execute("""
            UPDATE whale_addresses
            SET is_active = 0,
                tier = NULL,
                tier_position = NULL,
                tier_perp_amount = NULL,
                tier_spot = NULL,
                position_value = 0,
                raw_usd_value = 0,
                spot_value = 0,
                last_updated = ?
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