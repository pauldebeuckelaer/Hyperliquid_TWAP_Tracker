#!/usr/bin/env python3
"""
SQLite Storage Backend
======================
Replaces JSONL storage with SQLite database for efficient querying.

TWAP Tables (existing):
- orders: Individual order tracking with lifecycle
- snapshots: Aggregated summaries per coin per timestamp
- events: New/completed/canceled events for pattern detection
- addresses: All addresses seen from TWAP orders

Whale Snapshot Tables (NEW):
- whale_addresses: Master list of tracked whales (>$50K portfolio)
- portfolio_snapshots: Hourly portfolio summaries
- perp_snapshots: Hourly perp position details
- spot_snapshots: Hourly spot balance details
- vault_snapshots: Hourly vault holding details

Usage:
    db = SQLiteBackend('data/twap.db')
    db.save_snapshot(symbol, snapshot_data, changes)
    db.save_whale_snapshot(address, portfolio_data, positions, spot_balances, vaults)
    db.close()
"""
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Database path
DEFAULT_DB_PATH = Path('data/twap.db')


class SQLiteBackend:
    """SQLite storage backend for TWAP data and whale snapshots"""

    def __init__(self, db_path: Path = None):
        """
        Initialize SQLite backend.

        Args:
            db_path: Path to database file (default: data/twap.db)
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access
        self.cursor = self.conn.cursor()

        # Enable WAL mode for better concurrent performance
        self.cursor.execute("PRAGMA journal_mode=WAL")

        # Create tables
        self._create_tables()

        logger.info(f"SQLite backend initialized: {self.db_path}")

    def _create_tables(self):
        """Create database tables if they don't exist"""

        # =================================================================
        # TWAP TABLES (existing - unchanged)
        # =================================================================

        # Orders table - tracks individual orders with lifecycle
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_hash TEXT UNIQUE NOT NULL,
                address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                product_type TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                completed_at TEXT,
                canceled_at TEXT,
                final_progress_percent REAL
            )
        """)

        # Snapshots table - aggregated data per coin per timestamp
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL,
                active_orders INTEGER NOT NULL DEFAULT 0,
                total_orders INTEGER NOT NULL DEFAULT 0,
                buy_volume REAL NOT NULL DEFAULT 0,
                sell_volume REAL NOT NULL DEFAULT 0,
                spot_buy_pressure REAL NOT NULL DEFAULT 0,
                spot_sell_pressure REAL NOT NULL DEFAULT 0,
                perp_buy_pressure REAL NOT NULL DEFAULT 0,
                perp_sell_pressure REAL NOT NULL DEFAULT 0,
                net_pressure REAL NOT NULL DEFAULT 0,
                unique_addresses INTEGER NOT NULL DEFAULT 0,
                UNIQUE(timestamp, symbol)
            )
        """)

        # Events table - new/completed/canceled for pattern detection
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                order_hash TEXT NOT NULL,
                address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                product_type TEXT NOT NULL,
                duration_minutes INTEGER,
                elapsed_minutes INTEGER,
                progress_percent REAL
            )
        """)

        # Addresses table - all addresses ever seen from TWAP orders
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS addresses (
                address TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                total_orders INTEGER DEFAULT 0,
                total_volume REAL DEFAULT 0
            )
        """)

        # =================================================================
        # WHALE SNAPSHOT TABLES (NEW)
        # =================================================================

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

        # Create indexes
        self._create_indexes()

        self.conn.commit()
        logger.debug("Database tables created/verified")

    def _create_indexes(self):
        """Create indexes for query performance"""
        indexes = [
            # TWAP indexes (existing)
            "CREATE INDEX IF NOT EXISTS idx_orders_address ON orders(address)",
            "CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
            "CREATE INDEX IF NOT EXISTS idx_orders_last_seen ON orders(last_seen_at)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_symbol ON snapshots(symbol)",
            "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_events_address ON events(address)",
            "CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol)",
            "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)",

            # Whale snapshot indexes (NEW)
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

        for idx_sql in indexes:
            self.cursor.execute(idx_sql)

    # =========================================================================
    # TWAP METHODS (existing - unchanged)
    # =========================================================================

    def save_snapshot(self, symbol: str, snapshot_data: Dict, changes: Dict):
        """
        Save a snapshot and its associated data.

        Args:
            symbol: Coin symbol
            snapshot_data: Dict with timestamp, summary, active_orders, etc.
            changes: Dict with new_orders, completed_orders, canceled_orders
        """
        timestamp = snapshot_data.get('timestamp', datetime.now().isoformat())
        summary = snapshot_data.get('summary', {})
        price = snapshot_data.get('current_price')
        active_orders = snapshot_data.get('active_orders', [])

        try:
            # 1. Save snapshot summary
            self._save_snapshot_summary(timestamp, symbol, price, summary)

            # 2. Update/insert active orders from snapshot
            for order in active_orders:
                self._upsert_order(symbol, order, timestamp)

            # 3. ALSO insert orders from new_orders changes
            for order in changes.get('new_orders', []):
                self._upsert_order_from_change(symbol, order, timestamp)

            # 4. Record events
            for order in changes.get('new_orders', []):
                self._record_event('new', symbol, order, timestamp)

            for order in changes.get('completed_orders', []):
                self._record_event('completed', symbol, order, timestamp)
                self._mark_order_completed(order, timestamp)

            for order in changes.get('canceled_orders', []):
                self._record_event('canceled', symbol, order, timestamp)
                self._mark_order_canceled(order, timestamp)

            # 5. Update addresses
            addresses_in_snapshot = set()
            for order in active_orders:
                addr = order.get('address', '')
                if addr:
                    addresses_in_snapshot.add(addr)

            for addr in addresses_in_snapshot:
                self._upsert_address(addr, timestamp)

            self.conn.commit()

        except Exception as e:
            logger.error(f"Error saving snapshot for {symbol}: {e}")
            self.conn.rollback()
            raise

    def _save_snapshot_summary(self, timestamp: str, symbol: str, price: Optional[float], summary: Dict):
        """Insert or replace snapshot summary"""
        self.cursor.execute("""
            INSERT OR REPLACE INTO snapshots (
                timestamp, symbol, price, active_orders, total_orders,
                buy_volume, sell_volume,
                spot_buy_pressure, spot_sell_pressure,
                perp_buy_pressure, perp_sell_pressure,
                net_pressure, unique_addresses
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            symbol,
            price,
            summary.get('active_orders', 0),
            summary.get('total_orders', 0),
            summary.get('buy_volume', 0),
            summary.get('sell_volume', 0),
            summary.get('spot_buy_pressure', 0),
            summary.get('spot_sell_pressure', 0),
            summary.get('perp_buy_pressure', 0),
            summary.get('perp_sell_pressure', 0),
            summary.get('net_pressure_per_min', 0),
            summary.get('unique_addresses', 0)
        ))

    def _upsert_order(self, symbol: str, order: Dict, timestamp: str):
        """Insert or update an order from active_orders (dict format)"""
        order_hash = order.get('order_hash', '')
        if not order_hash:
            return

        self.cursor.execute(
            "SELECT id, first_seen_at FROM orders WHERE order_hash = ?",
            (order_hash,)
        )
        existing = self.cursor.fetchone()

        address = order.get('address', '')
        side = order.get('side', '')
        size = order.get('size', 0)
        product_type = order.get('product_type', '')
        duration = order.get('duration_minutes', 0)
        status = order.get('status', 'active')

        if existing:
            self.cursor.execute("""
                UPDATE orders SET
                    last_seen_at = ?,
                    status = ?,
                    size = ?
                WHERE order_hash = ?
            """, (timestamp, status, size, order_hash))
        else:
            self.cursor.execute("""
                INSERT INTO orders (
                    order_hash, address, symbol, side, size,
                    product_type, duration_minutes, status,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_hash, address, symbol, side, size,
                product_type, duration, status,
                timestamp, timestamp
            ))

    def _upsert_order_from_change(self, symbol: str, order, timestamp: str):
        """Insert/update order from changes (handles TWAPOrder objects and dicts)"""
        if hasattr(order, 'order_hash'):
            order_hash = order.order_hash
            address = order.full_address
            side = order.side
            size = order.size
            product_type = order.product_type
            duration = order.duration_minutes
            status = order.status
        else:
            order_hash = order.get('order_hash', '')
            address = order.get('address', order.get('full_address', ''))
            side = order.get('side', '')
            size = order.get('size', 0)
            product_type = order.get('product_type', '')
            duration = order.get('duration_minutes', 0)
            status = order.get('status', 'active')

        if not order_hash:
            return

        self.cursor.execute(
            "SELECT id FROM orders WHERE order_hash = ?",
            (order_hash,)
        )
        existing = self.cursor.fetchone()

        if existing:
            self.cursor.execute("""
                UPDATE orders SET
                    last_seen_at = ?,
                    status = ?
                WHERE order_hash = ?
            """, (timestamp, status, order_hash))
        else:
            self.cursor.execute("""
                INSERT INTO orders (
                    order_hash, address, symbol, side, size,
                    product_type, duration_minutes, status,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_hash, address, symbol, side, size,
                product_type, duration, status,
                timestamp, timestamp
            ))

    def _mark_order_completed(self, order, timestamp: str):
        """Mark an order as completed"""
        if hasattr(order, 'order_hash'):
            order_hash = order.order_hash
            progress = order.progress_percent
        else:
            order_hash = order.get('order_hash', '')
            progress = order.get('progress_percent')

        if not order_hash:
            return

        self.cursor.execute("""
            UPDATE orders SET
                status = 'completed',
                completed_at = ?,
                final_progress_percent = ?
            WHERE order_hash = ?
        """, (timestamp, progress, order_hash))

    def _mark_order_canceled(self, order, timestamp: str):
        """Mark an order as canceled"""
        if hasattr(order, 'order_hash'):
            order_hash = order.order_hash
            progress = order.progress_percent
        else:
            order_hash = order.get('order_hash', '')
            progress = order.get('progress_percent')

        if not order_hash:
            return

        self.cursor.execute("""
            UPDATE orders SET
                status = 'canceled',
                canceled_at = ?,
                final_progress_percent = ?
            WHERE order_hash = ?
        """, (timestamp, progress, order_hash))

    def _record_event(self, event_type: str, symbol: str, order, timestamp: str):
        """Record an event (new/completed/canceled)"""
        if hasattr(order, 'order_hash'):
            order_hash = order.order_hash
            address = order.full_address
            side = order.side
            size = order.size
            product_type = order.product_type
            duration = order.duration_minutes
            elapsed = order.elapsed_minutes
            progress = order.progress_percent
        else:
            order_hash = order.get('order_hash', '')
            address = order.get('address', order.get('full_address', ''))
            side = order.get('side', '')
            size = order.get('size', 0)
            product_type = order.get('product_type', '')
            duration = order.get('duration_minutes', 0)
            elapsed = order.get('elapsed_minutes')
            progress = order.get('progress_percent')

        if not order_hash:
            logger.debug(f"Skipping {event_type} event for {symbol}: missing order_hash")
            return

        self.cursor.execute("""
            INSERT INTO events (
                timestamp, event_type, order_hash, address, symbol,
                side, size, product_type, duration_minutes,
                elapsed_minutes, progress_percent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp, event_type, order_hash, address, symbol,
            side, size, product_type, duration,
            elapsed, progress
        ))

    def _upsert_address(self, address: str, timestamp: str):
        """Insert or update an address"""
        self.cursor.execute("""
            INSERT INTO addresses (address, first_seen_at, last_seen_at, total_orders)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(address) DO UPDATE SET
                last_seen_at = ?,
                total_orders = total_orders + 1
        """, (address, timestamp, timestamp, timestamp))

    def cleanup_stale_orders(self, grace_period_minutes: int = 2) -> int:
        """Mark orders as completed if they're past their expected completion time."""
        from datetime import datetime, timedelta, timezone

        current_time = datetime.now(timezone.utc)

        self.cursor.execute("""
            SELECT id, order_hash, symbol, first_seen_at, duration_minutes, last_seen_at
            FROM orders
            WHERE status = 'active'
        """)

        cleaned = 0
        for row in self.cursor.fetchall():
            order_id, order_hash, symbol, first_seen, duration, last_seen = row

            first_seen_dt = datetime.fromisoformat(first_seen).replace(tzinfo=timezone.utc)
            expected_completion = first_seen_dt + timedelta(minutes=duration)
            grace_end = expected_completion + timedelta(minutes=grace_period_minutes)

            if current_time > grace_end:
                self.cursor.execute("""
                    UPDATE orders 
                    SET 
                        status = 'completed',
                        completed_at = ?,
                        last_seen_at = ?,
                        final_progress_percent = 100.0
                    WHERE id = ?
                """, (expected_completion.isoformat(), last_seen, order_id))

                cleaned += 1
                logger.info(f"Cleaned stale order: {symbol} {order_hash[:10]}...")

        if cleaned > 0:
            self.conn.commit()
            logger.info(f"Cleaned {cleaned} stale orders")

        return cleaned

    # =========================================================================
    # TWAP QUERY METHODS (existing - unchanged)
    # =========================================================================

    def get_orders_by_address(self, address: str, limit: int = 100) -> List[Dict]:
        """Get orders for a specific address"""
        self.cursor.execute("""
            SELECT * FROM orders 
            WHERE address = ?
            ORDER BY last_seen_at DESC
            LIMIT ?
        """, (address, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_orders_by_symbol(self, symbol: str, status: str = None, limit: int = 100) -> List[Dict]:
        """Get orders for a specific symbol"""
        if status:
            self.cursor.execute("""
                SELECT * FROM orders 
                WHERE symbol = ? AND status = ?
                ORDER BY last_seen_at DESC
                LIMIT ?
            """, (symbol, status, limit))
        else:
            self.cursor.execute("""
                SELECT * FROM orders 
                WHERE symbol = ?
                ORDER BY last_seen_at DESC
                LIMIT ?
            """, (symbol, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_snapshots(self, symbol: str, start_time: str = None, end_time: str = None, limit: int = 1000) -> List[Dict]:
        """Get snapshots for a symbol within time range"""
        query = "SELECT * FROM snapshots WHERE symbol = ?"
        params = [symbol]

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_events(self, event_type: str = None, symbol: str = None, address: str = None,
                   start_time: str = None, limit: int = 500) -> List[Dict]:
        """Get events with flexible filtering"""
        query = "SELECT * FROM events WHERE 1=1"
        params = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)

        if address:
            query += " AND address = ?"
            params.append(address)

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        self.cursor.execute(query, params)
        return [dict(row) for row in self.cursor.fetchall()]

    def get_all_addresses(self) -> List[str]:
        """Get all tracked addresses from TWAP orders"""
        self.cursor.execute("SELECT address FROM addresses ORDER BY address")
        return [row[0] for row in self.cursor.fetchall()]

    def get_address_stats(self, address: str) -> Optional[Dict]:
        """Get stats for an address"""
        self.cursor.execute("SELECT * FROM addresses WHERE address = ?", (address,))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def get_all_symbols(self) -> List[str]:
        """Get all symbols with data"""
        self.cursor.execute("SELECT DISTINCT symbol FROM snapshots ORDER BY symbol")
        return [row[0] for row in self.cursor.fetchall()]

    def get_whale_orders(self, min_size_usd: float = 10000, limit: int = 100) -> List[Dict]:
        """Get large orders"""
        self.cursor.execute("""
            SELECT o.*, s.price 
            FROM orders o
            LEFT JOIN (
                SELECT symbol, price 
                FROM snapshots 
                GROUP BY symbol 
                HAVING timestamp = MAX(timestamp)
            ) s ON o.symbol = s.symbol
            WHERE o.status = 'running'
            ORDER BY o.size DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in self.cursor.fetchall()]

    # =========================================================================
    # WHALE SNAPSHOT METHODS (NEW)
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
        except sqlite3.IntegrityError:
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

    def cleanup_old_snapshots(self, days_to_keep: int = 30) -> int:
        """
        Remove snapshots older than specified days.

        Args:
            days_to_keep: Number of days of history to keep

        Returns:
            Number of portfolio snapshots deleted
        """
        from datetime import datetime, timedelta

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
    # STATS (updated)
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get database statistics"""
        stats = {}

        # TWAP stats
        self.cursor.execute("SELECT COUNT(*) FROM orders")
        stats['total_orders'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'running'")
        stats['active_orders'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM snapshots")
        stats['total_twap_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM events")
        stats['total_events'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM addresses")
        stats['total_twap_addresses'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT symbol) FROM snapshots")
        stats['unique_symbols'] = self.cursor.fetchone()[0]

        # Whale snapshot stats
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

        # Database file size
        stats['db_size_mb'] = round(self.db_path.stat().st_size / (1024 * 1024), 2)

        return stats

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("SQLite connection closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()