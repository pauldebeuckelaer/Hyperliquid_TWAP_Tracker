#!/usr/bin/env python3
"""
Whale Storage
=============
Storage for whale portfolio tracking data.

Tables:
- whale_addresses: Master list of tracked whales with tier assignment
- vip_addresses: Hand-picked whales for priority tracking
- portfolio_snapshots: Hourly portfolio summaries
- perp_snapshots: Hourly perp position details
- spot_snapshots: Hourly spot balance details
- vault_snapshots: Hourly vault holding details

Tier System:
- VIP: Hand-picked addresses (every 1 min)
- Tier 1: Position $5M+ (every 1 min)
- Tier 2: Position $1M-5M (every 5 min)
- Tier 3: Position $500K-1M (every 15 min)
- Tier 4: Position $250K-500K (every 30 min)
- Tier 5: Position $100K-250K (every 60 min)
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from .base import BaseStorage

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


class WhaleStorage(BaseStorage):
    """Storage for whale portfolio tracking data."""

    def _create_tables(self):
        """Create whale tracking tables."""

        # VIP addresses - hand-picked whales for priority tracking
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS vip_addresses (
                address TEXT PRIMARY KEY,
                nickname TEXT,
                notes TEXT,
                added_date TEXT NOT NULL
            )
        """)

        # Whale addresses - master list of tracked whales with tier
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS whale_addresses (
                address TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_updated TEXT,
                is_active INTEGER DEFAULT 1,
                tier INTEGER DEFAULT NULL,
                position_value REAL DEFAULT 0,
                tier_perp_amount INTEGER DEFAULT NULL,
                raw_usd_value REAL DEFAULT 0,
                last_tier_update TEXT
            )
        """)

        # Portfolio snapshots - hourly summary per address
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                snapshot_time TEXT NOT NULL,
                total_portfolio_value REAL,
                perp_value REAL,
                spot_value REAL,
                vault_value REAL,
                margin_used REAL,
                leverage_ratio REAL,
                num_positions INTEGER
            )
        """)

        # Perp snapshots - position details per snapshot
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS perp_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                snapshot_time TEXT NOT NULL,
                coin TEXT NOT NULL,
                size REAL,
                side TEXT,
                entry_price REAL,
                liquidation_price REAL,
                leverage REAL,
                margin_used REAL,
                unrealized_pnl REAL
            )
        """)

        # Spot snapshots - spot balances per snapshot
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS spot_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                snapshot_time TEXT NOT NULL,
                coin TEXT NOT NULL,
                amount REAL,
                value REAL,
                price REAL
            )
        """)

        # Vault snapshots - vault holdings per snapshot
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS vault_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                snapshot_time TEXT NOT NULL,
                vault_address TEXT,
                value REAL
            )
        """)

        # Perp account snapshots - per-address equity for tier_perp_amount
        # (Per-address shape, not per-coin. HIP-3 columns NULL for non-VIP/T1.)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS perp_account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                snapshot_time TEXT NOT NULL,
                account_value REAL,
                total_raw_usd REAL,
                total_margin_used REAL,
                total_ntl_pos REAL,
                withdrawable REAL,
                hip3_account_value REAL,
                hip3_total_raw_usd REAL,
                hip3_total_margin_used REAL,
                hip3_total_ntl_pos REAL,
                hip3_withdrawable REAL,
                hip3_dexes TEXT,
                total_account_value REAL,
                total_raw_usd_all REAL
            )
        """)

        self.conn.commit()

    def _create_indexes(self):
        """Create whale tracking indexes."""
        indexes = [
            # VIP indexes
            "CREATE INDEX IF NOT EXISTS idx_vip_addresses_nickname ON vip_addresses(nickname)",

            # Whale address indexes
            "CREATE INDEX IF NOT EXISTS idx_whale_addresses_active ON whale_addresses(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_whale_addresses_tier ON whale_addresses(tier)",
            "CREATE INDEX IF NOT EXISTS idx_whale_addresses_active_tier ON whale_addresses(is_active, tier)",

            # Portfolio indexes
            "CREATE INDEX IF NOT EXISTS idx_portfolio_time ON portfolio_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_portfolio_address ON portfolio_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_portfolio_address_time ON portfolio_snapshots(address, snapshot_time)",

            # Perp indexes
            "CREATE INDEX IF NOT EXISTS idx_perp_time ON perp_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_perp_address ON perp_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_perp_coin ON perp_snapshots(coin)",
            "CREATE INDEX IF NOT EXISTS idx_perp_address_time ON perp_snapshots(address, snapshot_time)",

            # Spot indexes
            "CREATE INDEX IF NOT EXISTS idx_spot_time ON spot_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_spot_address ON spot_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_spot_coin ON spot_snapshots(coin)",
            "CREATE INDEX IF NOT EXISTS idx_spot_address_time ON spot_snapshots(address, snapshot_time)",

            # Vault indexes
            "CREATE INDEX IF NOT EXISTS idx_vault_time ON vault_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_vault_address ON vault_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_vault_address_time ON vault_snapshots(address, snapshot_time)",

            # Perp account indexes
            "CREATE INDEX IF NOT EXISTS idx_perp_account_time ON perp_account_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_perp_account_address ON perp_account_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_perp_account_address_time ON perp_account_snapshots(address, snapshot_time)",
        ]
        self._execute_index_list(indexes)

    # =========================================================================
    # VIP ADDRESS METHODS
    # =========================================================================

    def add_vip_address(self, address: str, nickname: str = None, notes: str = None) -> bool:
        """
        Add an address to VIP list.

        Args:
            address: Wallet address
            nickname: Optional friendly name (e.g., "ETH Whale", "HYPE Degen")
            notes: Optional notes about this whale

        Returns:
            True if added, False if already exists
        """
        timestamp = datetime.now().isoformat()
        try:
            self.cursor.execute("""
                INSERT INTO vip_addresses (address, nickname, notes, added_date)
                VALUES (?, ?, ?, ?)
            """, (address, nickname, notes, timestamp))
            self.conn.commit()
            logger.info(f"Added VIP: {nickname or address[:10]}... ({address[:10]}...)")
            return True
        except Exception as e:
            logger.debug(f"VIP already exists or error: {e}")
            return False

    def remove_vip_address(self, address: str) -> bool:
        """
        Remove an address from VIP list.

        Args:
            address: Wallet address

        Returns:
            True if removed, False if not found
        """
        self.cursor.execute("DELETE FROM vip_addresses WHERE address = ?", (address,))
        self.conn.commit()
        removed = self.cursor.rowcount > 0
        if removed:
            logger.info(f"Removed VIP: {address[:10]}...")
        return removed

    def update_vip_address(self, address: str, nickname: str = None, notes: str = None) -> bool:
        """
        Update VIP address details.

        Args:
            address: Wallet address
            nickname: New nickname (None to keep existing)
            notes: New notes (None to keep existing)

        Returns:
            True if updated, False if not found
        """
        updates = []
        params = []

        if nickname is not None:
            updates.append("nickname = ?")
            params.append(nickname)

        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        if not updates:
            return False

        params.append(address)
        query = f"UPDATE vip_addresses SET {', '.join(updates)} WHERE address = ?"

        self.cursor.execute(query, params)
        self.conn.commit()
        return self.cursor.rowcount > 0

    def get_vip_addresses(self) -> List[Dict]:
        """
        Get all VIP addresses with metadata.

        Returns:
            List of dicts with address, nickname, notes, added_date
        """
        self.cursor.execute("""
            SELECT address, nickname, notes, added_date
            FROM vip_addresses
            ORDER BY added_date DESC
        """)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_vip_address_list(self) -> List[str]:
        """
        Get just the VIP addresses (no metadata).

        Returns:
            List of address strings
        """
        self.cursor.execute("SELECT address FROM vip_addresses")
        return [row[0] for row in self.cursor.fetchall()]

    def is_vip(self, address: str) -> bool:
        """Check if an address is in the VIP list."""
        self.cursor.execute("SELECT 1 FROM vip_addresses WHERE address = ?", (address,))
        return self.cursor.fetchone() is not None

    def get_vip_count(self) -> int:
        """Get count of VIP addresses."""
        self.cursor.execute("SELECT COUNT(*) FROM vip_addresses")
        return self.cursor.fetchone()[0]

    # =========================================================================
    # TIER MANAGEMENT METHODS
    # =========================================================================

    def calculate_tier(self, position_value: float) -> Optional[int]:
        """
        Calculate tier based on position value.

        Args:
            position_value: Total position value in USD

        Returns:
            Tier number (1-5) or None if below all thresholds
        """
        for tier in sorted(TIER_THRESHOLDS.keys()):
            threshold = TIER_THRESHOLDS[tier]
            if position_value >= threshold:
                return tier
        return None

    def update_address_tier(self, address: str, position_value: float) -> Optional[int]:
        """
        Update an address's tier based on position value.

        Args:
            address: Wallet address
            position_value: Current total position value

        Returns:
            New tier (1-5) or None if below threshold
        """
        tier = self.calculate_tier(position_value)
        timestamp = datetime.now().isoformat()

        # Upsert into whale_addresses
        self.cursor.execute("""
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
        self.conn.commit()

        return tier

    def bulk_update_tiers(self, address_positions: Dict[str, float]) -> Dict[str, int]:
        """
        Bulk update tiers for multiple addresses.

        Args:
            address_positions: Dict of address -> position_value

        Returns:
            Dict of address -> tier for addresses that made the cut
        """
        timestamp = datetime.now().isoformat()
        results = {}

        for address, position_value in address_positions.items():
            tier = self.calculate_tier(position_value)
            if tier is not None:
                results[address] = tier

            self.cursor.execute("""
                INSERT INTO whale_addresses (address, first_seen, last_updated, is_active, tier, position_value, last_tier_update)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    last_updated = ?,
                    is_active = ?,
                    tier = ?,
                    position_value = ?,
                    last_tier_update = ?
            """, (address, timestamp, timestamp, 1 if tier else 0, tier, position_value, timestamp,
                  timestamp, 1 if tier else 0, tier, position_value, timestamp))

        self.conn.commit()
        logger.info(f"Bulk updated tiers for {len(address_positions)} addresses, {len(results)} in tiers")
        return results

    def get_addresses_by_tier(self, tier: int) -> List[str]:
        """
        Get all addresses in a specific tier.

        Args:
            tier: Tier number (1-5)

        Returns:
            List of addresses
        """
        self.cursor.execute("""
            SELECT address FROM whale_addresses
            WHERE tier = ? AND is_active = 1
            ORDER BY position_value DESC
        """, (tier,))
        return [row[0] for row in self.cursor.fetchall()]

    def get_addresses_by_tiers(self, tiers: List[int]) -> List[str]:
        """
        Get all addresses in multiple tiers.

        Args:
            tiers: List of tier numbers

        Returns:
            List of addresses
        """
        placeholders = ','.join('?' * len(tiers))
        self.cursor.execute(f"""
            SELECT address FROM whale_addresses
            WHERE tier IN ({placeholders}) AND is_active = 1
            ORDER BY tier ASC, position_value DESC
        """, tiers)
        return [row[0] for row in self.cursor.fetchall()]

    def get_addresses_for_cycle(self, cycle_number: int) -> Dict[str, List[str]]:
        """
        Get addresses to fetch for a given cycle number.

        Logic:
        - VIP + Tier 1: Every cycle
        - Tier 2: Every 5 cycles (cycle % 5 == 0)
        - Tier 3: Every 15 cycles (cycle % 15 == 0)
        - Tier 4: Every 30 cycles (cycle % 30 == 0)
        - Tier 5: Every 60 cycles (cycle % 60 == 0)

        Args:
            cycle_number: Current cycle (1-based, wraps at 60)

        Returns:
            Dict with 'vip', 'tier1', 'tier2', etc. keys and address lists
        """
        result = {
            'vip': self.get_vip_address_list(),
            'tier1': self.get_addresses_by_tier(1),
            'tier2': [],
            'tier3': [],
            'tier4': [],
            'tier5': [],
        }

        # Normalize cycle to 1-60 range
        cycle = ((cycle_number - 1) % 60) + 1

        if cycle % TIER_FREQUENCIES[2] == 0:
            result['tier2'] = self.get_addresses_by_tier(2)

        if cycle % TIER_FREQUENCIES[3] == 0:
            result['tier3'] = self.get_addresses_by_tier(3)

        if cycle % TIER_FREQUENCIES[4] == 0:
            result['tier4'] = self.get_addresses_by_tier(4)

        if cycle % TIER_FREQUENCIES[5] == 0:
            result['tier5'] = self.get_addresses_by_tier(5)

        return result

    def get_all_addresses_for_cycle(self, cycle_number: int) -> List[str]:
        """
        Get flat list of all unique addresses to fetch for a cycle.

        Args:
            cycle_number: Current cycle number

        Returns:
            Deduplicated list of addresses
        """
        by_tier = self.get_addresses_for_cycle(cycle_number)

        # Combine all, VIP first, then by tier
        all_addresses = []
        seen = set()

        for key in ['vip', 'tier1', 'tier2', 'tier3', 'tier4', 'tier5']:
            for addr in by_tier.get(key, []):
                if addr not in seen:
                    all_addresses.append(addr)
                    seen.add(addr)

        return all_addresses

    def get_tier_stats(self) -> Dict:
        """
        Get statistics about tier distribution.

        Returns:
            Dict with counts per tier
        """
        stats = {'vip': self.get_vip_count()}

        self.cursor.execute("""
            SELECT tier, COUNT(*) as count, SUM(position_value) as total_value
            FROM whale_addresses
            WHERE is_active = 1 AND tier IS NOT NULL
            GROUP BY tier
            ORDER BY tier
        """)

        for row in self.cursor.fetchall():
            stats[f'tier{row["tier"]}'] = {
                'count': row['count'],
                'total_value': row['total_value']
            }

        return stats

    # =========================================================================
    # WHALE ADDRESS METHODS (legacy compatibility + updates)
    # =========================================================================

    def add_whale_address(self, address: str) -> bool:
        """
        Add a new whale address to track.

        Args:
            address: Wallet address

        Returns:
            True if added, False if already exists
        """
        timestamp = datetime.now().isoformat()
        try:
            self.cursor.execute("""
                INSERT INTO whale_addresses (address, first_seen, last_updated, is_active)
                VALUES (?, ?, ?, 1)
            """, (address, timestamp, timestamp))
            self.conn.commit()
            logger.debug(f"Added whale address: {address[:10]}...")
            return True
        except Exception:
            return False

    def update_whale_status(self, address: str, is_active: bool):
        """
        Update whale active status (above/below threshold).

        Args:
            address: Wallet address
            is_active: True if above threshold, False if below
        """
        timestamp = datetime.now().isoformat()
        self.cursor.execute("""
            UPDATE whale_addresses
            SET is_active = ?, last_updated = ?
            WHERE address = ?
        """, (1 if is_active else 0, timestamp, address))
        self.conn.commit()

    def get_active_whale_addresses(self) -> List[str]:
        """Get all active whale addresses (above threshold)."""
        self.cursor.execute("""
            SELECT address FROM whale_addresses
            WHERE is_active = 1
            ORDER BY address
        """)
        return [row[0] for row in self.cursor.fetchall()]

    def get_all_whale_addresses(self) -> List[Dict]:
        """Get all whale addresses with metadata."""
        self.cursor.execute("""
            SELECT * FROM whale_addresses
            ORDER BY last_updated DESC
        """)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_whale_count(self, active_only: bool = True) -> int:
        """Get count of whale addresses."""
        if active_only:
            self.cursor.execute("SELECT COUNT(*) FROM whale_addresses WHERE is_active = 1")
        else:
            self.cursor.execute("SELECT COUNT(*) FROM whale_addresses")
        return self.cursor.fetchone()[0]

    # =========================================================================
    # SNAPSHOT SAVE METHODS
    # =========================================================================

    def save_whale_snapshot(
            self,
            address: str,
            snapshot_time: str,
            portfolio_data: Dict,
            positions: List[Dict],
            spot_balances: List[Dict],
            vaults: List[Dict]
    ):
        """
        Save a complete hourly snapshot for a whale.

        Args:
            address: Wallet address
            snapshot_time: ISO timestamp for this snapshot
            portfolio_data: Dict with total_portfolio_value, perp_value, etc.
            positions: List of perp position dicts
            spot_balances: List of spot balance dicts
            vaults: List of vault holding dicts
        """
        try:
            # 1. Save portfolio summary
            self.cursor.execute("""
                INSERT INTO portfolio_snapshots (
                    address, snapshot_time, total_portfolio_value,
                    perp_value, spot_value, vault_value,
                    margin_used, leverage_ratio, num_positions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                address,
                snapshot_time,
                portfolio_data.get('total_portfolio_value', 0),
                portfolio_data.get('perp_value', 0),
                portfolio_data.get('spot_value', 0),
                portfolio_data.get('vault_value', 0),
                portfolio_data.get('margin_used', 0),
                portfolio_data.get('leverage_ratio', 0),
                portfolio_data.get('num_positions', 0),
            ))

            # 2. Save perp positions
            for pos in positions:
                self.cursor.execute("""
                    INSERT INTO perp_snapshots (
                        address, snapshot_time, coin, size, side,
                        entry_price, liquidation_price, leverage,
                        margin_used, unrealized_pnl
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    address,
                    snapshot_time,
                    pos.get('coin', ''),
                    pos.get('size', 0),
                    pos.get('side', ''),
                    pos.get('entry_price', 0),
                    pos.get('liquidation_price', 0),
                    pos.get('leverage', 0),
                    pos.get('margin_used', 0),
                    pos.get('unrealized_pnl', 0),
                ))

            # 3. Save spot balances
            for bal in spot_balances:
                self.cursor.execute("""
                    INSERT INTO spot_snapshots (
                        address, snapshot_time, coin, amount, value, price
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    address,
                    snapshot_time,
                    bal.get('coin', ''),
                    bal.get('amount', 0),
                    bal.get('value', 0),
                    bal.get('price'),
                ))

            # 4. Save vault holdings
            for vault in vaults:
                self.cursor.execute("""
                    INSERT INTO vault_snapshots (
                        address, snapshot_time, vault_address, value
                    ) VALUES (?, ?, ?, ?)
                """, (
                    address,
                    snapshot_time,
                    vault.get('vault_address', ''),
                    vault.get('value', 0),
                ))

            # Update whale last_updated
            self.cursor.execute("""
                UPDATE whale_addresses
                SET last_updated = ?
                WHERE address = ?
            """, (snapshot_time, address))

            self.conn.commit()
            logger.debug(f"Saved snapshot for {address[:10]}... at {snapshot_time}")

        except Exception as e:
            logger.error(f"Error saving whale snapshot for {address}: {e}")
            self.conn.rollback()
            raise

    def save_perp_snapshots_batch(self, snapshot_time: str, positions: List[Dict]):
        """
        Bulk insert perp positions for a single cycle.

        Used by LiquidationTracker to dual-write position data every cycle,
        so the tier system reads from fresh perp_snapshots instead of stale
        TWAP-event-only data.

        Args:
            snapshot_time: ISO timestamp shared by all rows in this batch
            positions: List of position dicts with keys:
                address, coin, side, size (abs), entry_price, liq_price,
                leverage, margin_used, unrealized_pnl
        """
        if not positions:
            return

        try:
            rows = []
            for p in positions:
                # Position dicts from LiquidationTracker have abs(size).
                # Sign it for consistency with TWAP-path writes (LONG positive,
                # SHORT negative).
                size = p.get('size', 0)
                side = p.get('side', '')
                if side == 'SHORT' and size > 0:
                    size = -size

                rows.append((
                    p.get('address', ''),
                    snapshot_time,
                    p.get('coin', ''),
                    size,
                    side,
                    p.get('entry_price', 0),
                    p.get('liq_price', 0),
                    p.get('leverage', 0),
                    p.get('margin_used', 0),
                    p.get('unrealized_pnl', 0),
                ))

            self.cursor.executemany("""
                INSERT INTO perp_snapshots (
                    address, snapshot_time, coin, size, side,
                    entry_price, liquidation_price, leverage,
                    margin_used, unrealized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            self.conn.commit()
            logger.debug(f"Bulk-saved {len(rows)} perp snapshots at {snapshot_time}")

        except Exception as e:
            logger.error(f"Error in save_perp_snapshots_batch: {e}")
            self.conn.rollback()
            raise

    def save_perp_account_snapshot(
            self,
            address: str,
            snapshot_time: str,
            account_data: Dict
    ):
        """
        Save a single account-equity snapshot for an address.

        Pattern A signature (address as separate param), matching save_whale_snapshot.

        Args:
            address: Wallet address
            snapshot_time: ISO T-format timestamp
            account_data: Dict with keys:
                Mainnet (always present):
                    account_value, total_raw_usd, total_margin_used,
                    total_ntl_pos, withdrawable
                HIP-3 (optional, None for non-VIP/T1):
                    hip3_account_value, hip3_total_raw_usd,
                    hip3_total_margin_used, hip3_total_ntl_pos,
                    hip3_withdrawable, hip3_dexes
        """
        try:
            mainnet_av = account_data.get('account_value') or 0
            hip3_av = account_data.get('hip3_account_value') or 0
            total_av = mainnet_av + hip3_av

            mainnet_raw = account_data.get('total_raw_usd') or 0
            hip3_raw = account_data.get('hip3_total_raw_usd') or 0
            total_raw_all = mainnet_raw + hip3_raw

            self.cursor.execute("""
                INSERT INTO perp_account_snapshots (
                    address, snapshot_time,
                    account_value, total_raw_usd, total_margin_used,
                    total_ntl_pos, withdrawable,
                    hip3_account_value, hip3_total_raw_usd,
                    hip3_total_margin_used, hip3_total_ntl_pos,
                    hip3_withdrawable, hip3_dexes,
                    total_account_value, total_raw_usd_all
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                address, snapshot_time,
                account_data.get('account_value'),
                account_data.get('total_raw_usd'),
                account_data.get('total_margin_used'),
                account_data.get('total_ntl_pos'),
                account_data.get('withdrawable'),
                account_data.get('hip3_account_value'),
                account_data.get('hip3_total_raw_usd'),
                account_data.get('hip3_total_margin_used'),
                account_data.get('hip3_total_ntl_pos'),
                account_data.get('hip3_withdrawable'),
                account_data.get('hip3_dexes'),
                total_av,
                total_raw_all,
            ))
            self.conn.commit()
            logger.debug(f"Saved perp_account snapshot for {address[:10]}... at {snapshot_time}")

        except Exception as e:
            logger.error(f"Error saving perp_account snapshot for {address}: {e}")
            self.conn.rollback()
            raise

    def save_perp_account_snapshots_batch(
            self,
            snapshot_time: str,
            account_data_list: List[Dict]
    ):
        """
        Bulk insert account-equity snapshots for a single cycle.

        Pattern B signature (address inside each dict), matching save_perp_snapshots_batch.
        Used by the tier-stratified poller to write per-address equity in
        parallel with save_perp_snapshots_batch.

        Args:
            snapshot_time: ISO T-format timestamp shared by all rows
            account_data_list: List of dicts. Each dict must contain 'address'
                plus the same fields as save_perp_account_snapshot's account_data.
        """
        if not account_data_list:
            return

        try:
            rows = []
            for d in account_data_list:
                mainnet_av = d.get('account_value') or 0
                hip3_av = d.get('hip3_account_value') or 0
                total_av = mainnet_av + hip3_av

                mainnet_raw = d.get('total_raw_usd') or 0
                hip3_raw = d.get('hip3_total_raw_usd') or 0
                total_raw_all = mainnet_raw + hip3_raw

                rows.append((
                    d.get('address', ''),
                    snapshot_time,
                    d.get('account_value'),
                    d.get('total_raw_usd'),
                    d.get('total_margin_used'),
                    d.get('total_ntl_pos'),
                    d.get('withdrawable'),
                    d.get('hip3_account_value'),
                    d.get('hip3_total_raw_usd'),
                    d.get('hip3_total_margin_used'),
                    d.get('hip3_total_ntl_pos'),
                    d.get('hip3_withdrawable'),
                    d.get('hip3_dexes'),
                    total_av,
                    total_raw_all,
                ))

            self.cursor.executemany("""
                INSERT INTO perp_account_snapshots (
                    address, snapshot_time,
                    account_value, total_raw_usd, total_margin_used,
                    total_ntl_pos, withdrawable,
                    hip3_account_value, hip3_total_raw_usd,
                    hip3_total_margin_used, hip3_total_ntl_pos,
                    hip3_withdrawable, hip3_dexes,
                    total_account_value, total_raw_usd_all
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            self.conn.commit()
            logger.debug(f"Bulk-saved {len(rows)} perp_account snapshots at {snapshot_time}")

        except Exception as e:
            logger.error(f"Error in save_perp_account_snapshots_batch: {e}")
            self.conn.rollback()
            raise

    # =========================================================================
    # SNAPSHOT QUERY METHODS
    # =========================================================================

    def get_portfolio_history(
            self,
            address: str,
            start_time: str = None,
            end_time: str = None,
            limit: int = 168  # 1 week of hourly snapshots
    ) -> List[Dict]:
        """
        Get portfolio snapshot history for an address.

        Args:
            address: Wallet address
            start_time: Optional start timestamp
            end_time: Optional end timestamp
            limit: Max results (default 168 = 1 week hourly)

        Returns:
            List of portfolio snapshots
        """
        query = "SELECT * FROM portfolio_snapshots WHERE address = ?"
        params = [address]

        if start_time:
            query += " AND snapshot_time >= ?"
            params.append(start_time)

        if end_time:
            query += " AND snapshot_time <= ?"
            params.append(end_time)

        query += " ORDER BY snapshot_time DESC LIMIT ?"
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_positions_at_time(self, address: str, snapshot_time: str) -> List[Dict]:
        """Get perp positions for an address at a specific snapshot time."""
        self.cursor.execute("""
            SELECT * FROM perp_snapshots
            WHERE address = ? AND snapshot_time = ?
            ORDER BY ABS(size) DESC
        """, (address, snapshot_time))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_spot_at_time(self, address: str, snapshot_time: str) -> List[Dict]:
        """Get spot balances for an address at a specific snapshot time."""
        self.cursor.execute("""
            SELECT * FROM spot_snapshots
            WHERE address = ? AND snapshot_time = ?
            ORDER BY value DESC
        """, (address, snapshot_time))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_vaults_at_time(self, address: str, snapshot_time: str) -> List[Dict]:
        """Get vault holdings for an address at a specific snapshot time."""
        self.cursor.execute("""
            SELECT * FROM vault_snapshots
            WHERE address = ? AND snapshot_time = ?
            ORDER BY value DESC
        """, (address, snapshot_time))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_latest_snapshot(self, address: str) -> Optional[Dict]:
        """
        Get the most recent snapshot for an address.

        Returns:
            Dict with portfolio data, positions, spot, vaults or None
        """
        self.cursor.execute("""
            SELECT * FROM portfolio_snapshots
            WHERE address = ?
            ORDER BY snapshot_time DESC
            LIMIT 1
        """, (address,))
        row = self.cursor.fetchone()

        if not row:
            return None

        result = dict(row)
        snapshot_time = result['snapshot_time']

        result['positions'] = self.get_positions_at_time(address, snapshot_time)
        result['spot_balances'] = self.get_spot_at_time(address, snapshot_time)
        result['vaults'] = self.get_vaults_at_time(address, snapshot_time)

        return result

    def get_coin_holders_history(
            self,
            coin: str,
            snapshot_time: str = None,
            limit: int = 100
    ) -> List[Dict]:
        """
        Get all holders of a coin at a specific time (or latest).

        Args:
            coin: Coin symbol
            snapshot_time: Optional specific time, defaults to latest
            limit: Max results

        Returns:
            List of position/balance dicts with address
        """
        if not snapshot_time:
            # Get latest snapshot time
            self.cursor.execute("""
                SELECT MAX(snapshot_time) FROM portfolio_snapshots
            """)
            row = self.cursor.fetchone()
            snapshot_time = row[0] if row else None

        if not snapshot_time:
            return []

        # Get perp positions
        self.cursor.execute("""
            SELECT p.*, 'perp' as holding_type
            FROM perp_snapshots p
            WHERE p.coin = ? AND p.snapshot_time = ?
            ORDER BY ABS(p.size) DESC
            LIMIT ?
        """, (coin, snapshot_time, limit))
        perp_results = [dict(row) for row in self.cursor.fetchall()]

        # Get spot balances
        self.cursor.execute("""
            SELECT s.*, 'spot' as holding_type
            FROM spot_snapshots s
            WHERE s.coin = ? AND s.snapshot_time = ?
            ORDER BY s.value DESC
            LIMIT ?
        """, (coin, snapshot_time, limit))
        spot_results = [dict(row) for row in self.cursor.fetchall()]

        return perp_results + spot_results

    def get_snapshot_times(self, limit: int = 100) -> List[str]:
        """Get list of unique snapshot times."""
        self.cursor.execute("""
            SELECT DISTINCT snapshot_time
            FROM portfolio_snapshots
            ORDER BY snapshot_time DESC
            LIMIT ?
        """, (limit,))
        return [row[0] for row in self.cursor.fetchall()]

    # =========================================================================
    # CLEANUP
    # =========================================================================

    def cleanup_old_snapshots(self, days_to_keep: int = 30) -> int:
        """
        Remove snapshots older than specified days.

        Args:
            days_to_keep: Number of days of history to keep

        Returns:
            Number of portfolio snapshots deleted
        """
        cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()

        # Get count before deletion
        self.cursor.execute("""
            SELECT COUNT(*) FROM portfolio_snapshots
            WHERE snapshot_time < ?
        """, (cutoff,))
        count = self.cursor.fetchone()[0]

        if count > 0:
            # Delete from all snapshot tables
            self.cursor.execute("DELETE FROM portfolio_snapshots WHERE snapshot_time < ?", (cutoff,))
            self.cursor.execute("DELETE FROM perp_snapshots WHERE snapshot_time < ?", (cutoff,))
            self.cursor.execute("DELETE FROM spot_snapshots WHERE snapshot_time < ?", (cutoff,))
            self.cursor.execute("DELETE FROM vault_snapshots WHERE snapshot_time < ?", (cutoff,))
            self.conn.commit()
            logger.info(f"Cleaned up {count} old snapshots (older than {days_to_keep} days)")

        return count

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get whale storage statistics."""
        stats = {}

        # VIP stats
        stats['vip_count'] = self.get_vip_count()

        # Whale address stats
        self.cursor.execute("SELECT COUNT(*) FROM whale_addresses")
        stats['total_whales'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM whale_addresses WHERE is_active = 1")
        stats['active_whales'] = self.cursor.fetchone()[0]

        # Tier distribution
        stats['tier_stats'] = self.get_tier_stats()

        # Snapshot stats
        self.cursor.execute("SELECT COUNT(*) FROM portfolio_snapshots")
        stats['total_portfolio_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM perp_snapshots")
        stats['total_perp_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM spot_snapshots")
        stats['total_spot_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM vault_snapshots")
        stats['total_vault_snapshots'] = self.cursor.fetchone()[0]

        stats['db_size_mb'] = self.get_db_size_mb()

        return stats