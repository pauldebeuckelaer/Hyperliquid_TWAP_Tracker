#!/usr/bin/env python3
"""
TWAP Storage
============
Storage for TWAP order tracking data.

Tables:
- orders: Individual order tracking with lifecycle
- snapshots: Aggregated summaries per coin per timestamp
- events: New/completed/canceled events for pattern detection
- addresses: All addresses seen from TWAP orders
"""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .base import BaseStorage

logger = logging.getLogger(__name__)


class TwapStorage(BaseStorage):
    """Storage for TWAP order tracking data."""

    def _create_tables(self):
        """Create TWAP tables."""

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

        self.conn.commit()

    def _create_indexes(self):
        """Create TWAP indexes."""
        indexes = [
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
        ]
        self._execute_index_list(indexes)

    # =========================================================================
    # SAVE METHODS
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


        except Exception as e:
            logger.error(f"Error saving snapshot for {symbol}: {e}")
            self.conn.rollback()
            raise

    def _save_snapshot_summary(self, timestamp: str, symbol: str, price: Optional[float], summary: Dict):
        """Insert or replace snapshot summary."""
        self.cursor.execute("""
            INSERT OR REPLACE INTO snapshots (
                timestamp, symbol, price, active_orders, total_orders,
                buy_volume, sell_volume,
                spot_buy_pressure, spot_sell_pressure,
                perp_buy_pressure, perp_sell_pressure,
                net_pressure, unique_addresses, asset_id
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
            summary.get('unique_addresses', 0),
            summary.get('asset_id')
        ))

    def _upsert_order(self, symbol: str, order: Dict, timestamp: str):
        """Insert or update an order from active_orders (dict format)."""
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
        asset_id = order.get('asset_id')

        if existing:
            self.cursor.execute("""
                UPDATE orders SET
                    last_seen_at = ?,
                    status = ?,
                    size = ?,
                    asset_id = COALESCE(asset_id, ?)
                WHERE order_hash = ?
            """, (timestamp, status, size, order_hash))
        else:
            self.cursor.execute("""
                INSERT INTO orders (
                    order_hash, address, symbol, side, size,
                    product_type, duration_minutes, status,
                    first_seen_at, last_seen_at, asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_hash, address, symbol, side, size,
                product_type, duration, status,
                timestamp, timestamp, asset_id
            ))

    def _upsert_order_from_change(self, symbol: str, order, timestamp: str):
        """Insert/update order from changes (handles TWAPOrder objects and dicts)."""
        if hasattr(order, 'order_hash'):
            order_hash = order.order_hash
            address = order.full_address
            side = order.side
            size = order.size
            product_type = order.product_type
            duration = order.duration_minutes
            status = order.status
            asset_id = getattr(order, 'asset_id', None)
        else:
            order_hash = order.get('order_hash', '')
            address = order.get('address', order.get('full_address', ''))
            side = order.get('side', '')
            size = order.get('size', 0)
            product_type = order.get('product_type', '')
            duration = order.get('duration_minutes', 0)
            status = order.get('status', 'active')
            asset_id = order.get('asset_id')
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
                    status = ?,
                    asset_id = COALESCE(asset_id, ?)
                WHERE order_hash = ?
            """, (timestamp, status, asset_id, order_hash))
        else:
            self.cursor.execute("""
                INSERT INTO orders (
                    order_hash, address, symbol, side, size,
                    product_type, duration_minutes, status,
                    first_seen_at, last_seen_at, asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_hash, address, symbol, side, size,
                product_type, duration, status,
                timestamp, timestamp, asset_id
            ))

    def _mark_order_completed(self, order, timestamp: str):
        """Mark an order as completed."""
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
        """Mark an order as canceled."""
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
        """Record an event (new/completed/canceled)."""
        if hasattr(order, 'order_hash'):
            order_hash = order.order_hash
            address = order.full_address
            side = order.side
            size = order.size
            product_type = order.product_type
            duration = order.duration_minutes
            elapsed = order.elapsed_minutes
            progress = order.progress_percent
            asset_id = getattr(order, 'asset_id', None)
        else:
            order_hash = order.get('order_hash', '')
            address = order.get('address', order.get('full_address', ''))
            side = order.get('side', '')
            size = order.get('size', 0)
            product_type = order.get('product_type', '')
            duration = order.get('duration_minutes', 0)
            elapsed = order.get('elapsed_minutes')
            progress = order.get('progress_percent')
            asset_id = order.get('asset_id')

        if not order_hash:
            logger.debug(f"Skipping {event_type} event for {symbol}: missing order_hash")
            return

        self.cursor.execute("""
            INSERT INTO events (
                timestamp, event_type, order_hash, address, symbol,
                side, size, product_type, duration_minutes,
                elapsed_minutes, progress_percent, asset_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp, event_type, order_hash, address, symbol,
            side, size, product_type, duration,
            elapsed, progress, asset_id
        ))

    def _upsert_address(self, address: str, timestamp: str):
        """Insert or update an address."""
        self.cursor.execute("""
            INSERT INTO addresses (address, first_seen_at, last_seen_at, total_orders)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(address) DO UPDATE SET
                last_seen_at = ?,
                total_orders = total_orders + 1
        """, (address, timestamp, timestamp, timestamp))

    def cleanup_stale_orders(self, grace_period_minutes: int = 2) -> int:
        """Mark orders as completed if they're past their expected completion time."""
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
    # QUERY METHODS
    # =========================================================================

    def get_orders_by_address(self, address: str, limit: int = 100) -> List[Dict]:
        """Get orders for a specific address."""
        self.cursor.execute("""
            SELECT * FROM orders 
            WHERE address = ?
            ORDER BY last_seen_at DESC
            LIMIT ?
        """, (address, limit))
        return [dict(row) for row in self.cursor.fetchall()]

    def get_orders_by_symbol(self, symbol: str, status: str = None, limit: int = 100) -> List[Dict]:
        """Get orders for a specific symbol."""
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
        """Get snapshots for a symbol within time range."""
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
        """Get events with flexible filtering."""
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
        """Get all tracked addresses from TWAP orders."""
        self.cursor.execute("SELECT address FROM addresses ORDER BY address")
        return [row[0] for row in self.cursor.fetchall()]

    def get_address_stats(self, address: str) -> Optional[Dict]:
        """Get stats for an address."""
        self.cursor.execute("SELECT * FROM addresses WHERE address = ?", (address,))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def get_all_symbols(self) -> List[str]:
        """Get all symbols with data."""
        self.cursor.execute("SELECT DISTINCT symbol FROM snapshots ORDER BY symbol")
        return [row[0] for row in self.cursor.fetchall()]

    def get_whale_orders(self, min_size_usd: float = 10000, limit: int = 100) -> List[Dict]:
        """Get large orders."""
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
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get TWAP storage statistics."""
        stats = {}

        self.cursor.execute("SELECT COUNT(*) FROM orders")
        stats['total_orders'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'running'")
        stats['active_orders'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM snapshots")
        stats['total_snapshots'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM events")
        stats['total_events'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(*) FROM addresses")
        stats['total_addresses'] = self.cursor.fetchone()[0]

        self.cursor.execute("SELECT COUNT(DISTINCT symbol) FROM snapshots")
        stats['unique_symbols'] = self.cursor.fetchone()[0]

        stats['db_size_mb'] = self.get_db_size_mb()

        return stats