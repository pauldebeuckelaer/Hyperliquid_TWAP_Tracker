#!/usr/bin/env python3
"""
Whale Storage
=============
Storage for whale portfolio tracking data.

Tables:
- whale_addresses: Master list of tracked whales (>$50K portfolio)
- portfolio_snapshots: Hourly portfolio summaries
- perp_snapshots: Hourly perp position details
- spot_snapshots: Hourly spot balance details
- vault_snapshots: Hourly vault holding details
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .base import BaseStorage

logger = logging.getLogger(__name__)


class WhaleStorage(BaseStorage):
    """Storage for whale portfolio tracking data."""

    def _create_tables(self):
        """Create whale tracking tables."""

        # Whale addresses - master list of tracked whales
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS whale_addresses (
                address TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_updated TEXT,
                is_active INTEGER DEFAULT 1
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

        self.conn.commit()

    def _create_indexes(self):
        """Create whale tracking indexes."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_whale_addresses_active ON whale_addresses(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_portfolio_time ON portfolio_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_portfolio_address ON portfolio_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_portfolio_address_time ON portfolio_snapshots(address, snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_perp_time ON perp_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_perp_address ON perp_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_perp_coin ON perp_snapshots(coin)",
            "CREATE INDEX IF NOT EXISTS idx_perp_address_time ON perp_snapshots(address, snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_spot_time ON spot_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_spot_address ON spot_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_spot_coin ON spot_snapshots(coin)",
            "CREATE INDEX IF NOT EXISTS idx_spot_address_time ON spot_snapshots(address, snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_vault_time ON vault_snapshots(snapshot_time)",
            "CREATE INDEX IF NOT EXISTS idx_vault_address ON vault_snapshots(address)",
            "CREATE INDEX IF NOT EXISTS idx_vault_address_time ON vault_snapshots(address, snapshot_time)",
        ]
        self._execute_index_list(indexes)

    # =========================================================================
    # WHALE ADDRESS METHODS
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

        self.cursor.execute("SELECT COUNT(*) FROM whale_addresses")
        stats['total_whales'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM whale_addresses WHERE is_active = 1")
        stats['active_whales'] = self.cursor.fetchone()[0]

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