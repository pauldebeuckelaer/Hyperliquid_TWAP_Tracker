#!/usr/bin/env python3
"""
Orders Daily Summary Generator
==============================
Generates daily summary JSON from the orders table.
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def generate_orders_summary(
        storage,
        date: datetime,
        output_dir: Path = Path('reports')
) -> Optional[Dict]:
    """
    Generate daily summary for orders table.

    Args:
        storage: SQLiteBackend instance
        date: Date to summarize
        output_dir: Where to save JSON files

    Returns:
        Summary dict, or None if no data
    """
    date_str = date.strftime('%Y-%m-%d')
    start_time = f"{date_str}T00:00:00"
    end_time = f"{date_str}T23:59:59"

    logger.info(f"Generating orders summary for {date_str}")

    summary = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "orders": _get_order_totals(storage),
        "started_today": _get_started_today(storage, start_time, end_time),
        "completed_today": _get_completed_today(storage, start_time, end_time),
        "canceled_today": _get_canceled_today(storage, start_time, end_time),
        "active_orders": _get_active_orders(storage, date),
        "anomalies": _detect_anomalies(storage, start_time, end_time, date),
    }

    # Save to file
    output_path = output_dir / date_str
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'orders.json'
    with open(file_path, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved orders summary to {file_path}")

    return summary


def _get_order_totals(storage) -> Dict:
    """Get overall order statistics."""
    storage.cursor.execute("""
        SELECT status, COUNT(*) as count
        FROM orders
        GROUP BY status
    """)

    by_status = {row[0]: row[1] for row in storage.cursor.fetchall()}
    total = sum(by_status.values())

    return {
        "total": total,
        "by_status": by_status
    }


def _get_started_today(storage, start_time: str, end_time: str) -> Dict:
    """Get orders started today."""
    # Total started today
    storage.cursor.execute("""
        SELECT COUNT(*) FROM orders
        WHERE first_seen_at BETWEEN ? AND ?
    """, (start_time, end_time))
    total = storage.cursor.fetchone()[0]

    if total == 0:
        return {"total": 0}

    # By side
    storage.cursor.execute("""
        SELECT side, COUNT(*) as count
        FROM orders
        WHERE first_seen_at BETWEEN ? AND ?
        GROUP BY side
    """, (start_time, end_time))
    by_side = {row[0]: row[1] for row in storage.cursor.fetchall()}

    # By product
    storage.cursor.execute("""
        SELECT product_type, COUNT(*) as count
        FROM orders
        WHERE first_seen_at BETWEEN ? AND ?
        GROUP BY product_type
    """, (start_time, end_time))
    by_product = {row[0]: row[1] for row in storage.cursor.fetchall()}

    # Top coins - sorted by USD value
    storage.cursor.execute("""
        SELECT 
            o.symbol,
            COUNT(*) as count,
            SUM(CASE WHEN o.side = 'BUY' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN o.side = 'SELL' THEN 1 ELSE 0 END) as sells,
            SUM(o.size) as total_size,
            s.price
        FROM orders o
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON o.symbol = s.symbol
        WHERE o.first_seen_at BETWEEN ? AND ?
        GROUP BY o.symbol
        ORDER BY SUM(o.size * COALESCE(s.price, 0)) DESC
        LIMIT 10
    """, (start_time, end_time, start_time, end_time))

    top_coins = []
    for row in storage.cursor.fetchall():
        symbol = row[0]
        count = row[1]
        buys = row[2]
        sells = row[3]
        total_size = row[4]
        price = row[5] or 0
        size_usd = round(total_size * price, 2) if price else 0

        top_coins.append({
            "symbol": symbol,
            "count": count,
            "buys": buys,
            "sells": sells,
            "size_usd": size_usd
        })

    # Top addresses - already sorted by USD
    storage.cursor.execute("""
        SELECT 
            o.address,
            COUNT(*) as count,
            SUM(CASE WHEN o.side = 'BUY' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN o.side = 'SELL' THEN 1 ELSE 0 END) as sells,
            SUM(o.size * COALESCE(s.price, 0)) as size_usd
        FROM orders o
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON o.symbol = s.symbol
        WHERE o.first_seen_at BETWEEN ? AND ?
        GROUP BY o.address
        ORDER BY size_usd DESC
        LIMIT 10
    """, (start_time, end_time, start_time, end_time))

    top_addresses = [
        {
            "address": row[0][:10] + "...",
            "full_address": row[0],
            "count": row[1],
            "buys": row[2],
            "sells": row[3],
            "size_usd": round(row[4], 2)
        }
        for row in storage.cursor.fetchall()
    ]

    return {
        "total": total,
        "by_side": by_side,
        "by_product": by_product,
        "top_coins": top_coins,
        "top_addresses": top_addresses
    }


def _get_completed_today(storage, start_time: str, end_time: str) -> Dict:
    """Get orders completed today."""
    storage.cursor.execute("""
        SELECT 
            COUNT(*) as count,
            AVG(duration_minutes) as avg_duration
        FROM orders
        WHERE completed_at BETWEEN ? AND ?
    """, (start_time, end_time))

    row = storage.cursor.fetchone()
    total = row[0]
    avg_duration = round(row[1], 1) if row[1] else 0

    if total == 0:
        return {"total": 0}

    # Calculate actual time taken (first_seen to completed)
    storage.cursor.execute("""
        SELECT AVG(
            (julianday(completed_at) - julianday(first_seen_at)) * 24 * 60
        ) as avg_actual
        FROM orders
        WHERE completed_at BETWEEN ? AND ?
    """, (start_time, end_time))

    avg_actual = storage.cursor.fetchone()[0]
    avg_actual = round(avg_actual, 1) if avg_actual else 0

    # Top coins - sorted by USD value
    storage.cursor.execute("""
        SELECT 
            o.symbol,
            COUNT(*) as count,
            AVG(o.duration_minutes) as avg_duration,
            SUM(o.size) as total_size,
            s.price
        FROM orders o
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON o.symbol = s.symbol
        WHERE o.completed_at BETWEEN ? AND ?
        GROUP BY o.symbol
        ORDER BY SUM(o.size * COALESCE(s.price, 0)) DESC
        LIMIT 10
    """, (start_time, end_time, start_time, end_time))

    top_coins = []
    for row in storage.cursor.fetchall():
        symbol = row[0]
        count = row[1]
        avg_dur = round(row[2], 1) if row[2] else 0
        total_size = row[3]
        price = row[4] or 0
        size_usd = round(total_size * price, 2) if price else 0

        top_coins.append({
            "symbol": symbol,
            "count": count,
            "avg_duration_minutes": avg_dur,
            "size_usd": size_usd
        })

    return {
        "total": total,
        "avg_duration_minutes": avg_duration,
        "avg_actual_minutes": avg_actual,
        "top_coins": top_coins
    }


def _get_canceled_today(storage, start_time: str, end_time: str) -> Dict:
    """Get orders canceled today."""
    storage.cursor.execute("""
        SELECT 
            COUNT(*) as count,
            AVG(final_progress_percent) as avg_progress
        FROM orders
        WHERE canceled_at BETWEEN ? AND ?
    """, (start_time, end_time))

    row = storage.cursor.fetchone()
    total = row[0]
    avg_progress = round(row[1], 1) if row[1] else 0

    if total == 0:
        return {"total": 0}

    # Quick cancels (< 10% progress)
    storage.cursor.execute("""
        SELECT COUNT(*) FROM orders
        WHERE canceled_at BETWEEN ? AND ?
        AND final_progress_percent < 10
    """, (start_time, end_time))
    quick_cancels = storage.cursor.fetchone()[0]

    # Top coins - sorted by USD value
    storage.cursor.execute("""
        SELECT 
            o.symbol,
            COUNT(*) as count,
            AVG(o.final_progress_percent) as avg_progress,
            SUM(o.size) as total_size,
            s.price
        FROM orders o
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON o.symbol = s.symbol
        WHERE o.canceled_at BETWEEN ? AND ?
        GROUP BY o.symbol
        ORDER BY SUM(o.size * COALESCE(s.price, 0)) DESC
        LIMIT 10
    """, (start_time, end_time, start_time, end_time))

    top_coins = []
    for row in storage.cursor.fetchall():
        symbol = row[0]
        count = row[1]
        avg_prog = round(row[2], 1) if row[2] else 0
        total_size = row[3]
        price = row[4] or 0
        size_usd = round(total_size * price, 2) if price else 0

        top_coins.append({
            "symbol": symbol,
            "count": count,
            "avg_progress_at_cancel": avg_prog,
            "size_usd": size_usd
        })

    return {
        "total": total,
        "avg_progress_at_cancel": avg_progress,
        "quick_cancels": quick_cancels,
        "quick_cancel_rate": round(quick_cancels / total, 3) if total > 0 else 0,
        "top_coins": top_coins
    }


def _get_active_orders(storage, date: datetime) -> Dict:
    """Get currently active orders snapshot."""
    storage.cursor.execute("""
        SELECT COUNT(*) FROM orders WHERE status = 'active'
    """)
    total = storage.cursor.fetchone()[0]

    if total == 0:
        return {"total": 0}

    # Long running (> 24 hours)
    storage.cursor.execute("""
        SELECT COUNT(*) FROM orders
        WHERE status = 'active'
        AND julianday(?) - julianday(first_seen_at) > 1
    """, (date.isoformat(),))
    long_running = storage.cursor.fetchone()[0]

    # Get date range for price lookup
    date_str = date.strftime('%Y-%m-%d')
    start_time = f"{date_str}T00:00:00"
    end_time = f"{date_str}T23:59:59"

    # Top coins - sorted by USD value
    storage.cursor.execute("""
        SELECT 
            o.symbol,
            COUNT(*) as count,
            SUM(CASE WHEN o.side = 'BUY' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN o.side = 'SELL' THEN 1 ELSE 0 END) as sells,
            SUM(o.size) as total_size,
            s.price
        FROM orders o
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON o.symbol = s.symbol
        WHERE o.status = 'active'
        GROUP BY o.symbol
        ORDER BY SUM(o.size * COALESCE(s.price, 0)) DESC
        LIMIT 10
    """, (start_time, end_time))

    top_coins = []
    for row in storage.cursor.fetchall():
        symbol = row[0]
        count = row[1]
        buys = row[2]
        sells = row[3]
        total_size = row[4]
        price = row[5] or 0
        size_usd = round(total_size * price, 2) if price else 0

        top_coins.append({
            "symbol": symbol,
            "count": count,
            "buys": buys,
            "sells": sells,
            "size_usd": size_usd
        })

    # Top addresses - sorted by USD value
    storage.cursor.execute("""
        SELECT 
            o.address,
            COUNT(*) as count,
            SUM(CASE WHEN o.side = 'BUY' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN o.side = 'SELL' THEN 1 ELSE 0 END) as sells,
            SUM(o.size * COALESCE(s.price, 0)) as size_usd
        FROM orders o
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON o.symbol = s.symbol
        WHERE o.status = 'active'
        GROUP BY o.address
        ORDER BY size_usd DESC
        LIMIT 10
    """, (start_time, end_time))

    top_addresses = [
        {
            "address": row[0][:10] + "...",
            "full_address": row[0],
            "count": row[1],
            "buys": row[2],
            "sells": row[3],
            "size_usd": round(row[4], 2)
        }
        for row in storage.cursor.fetchall()
    ]

    # Oldest active order
    storage.cursor.execute("""
        SELECT 
            order_hash,
            symbol,
            side,
            size,
            first_seen_at,
            julianday(?) - julianday(first_seen_at) as days_running
        FROM orders
        WHERE status = 'active'
        ORDER BY first_seen_at ASC
        LIMIT 1
    """, (date.isoformat(),))

    oldest_row = storage.cursor.fetchone()
    oldest = None
    if oldest_row:
        oldest = {
            "order_hash": oldest_row[0][:16] + "...",
            "symbol": oldest_row[1],
            "side": oldest_row[2],
            "size": oldest_row[3],
            "first_seen_at": oldest_row[4],
            "days_running": round(oldest_row[5], 2)
        }

    return {
        "total": total,
        "long_running": long_running,
        "top_coins": top_coins,
        "top_addresses": top_addresses,
        "oldest": oldest
    }


def _detect_anomalies(storage, start_time: str, end_time: str, date: datetime) -> Dict:
    """Detect anomalies in orders."""
    anomalies = {}

    # Stuck orders (active > 3 days)
    storage.cursor.execute("""
        SELECT 
            order_hash,
            symbol,
            side,
            first_seen_at,
            julianday(?) - julianday(first_seen_at) as days_running
        FROM orders
        WHERE status = 'active'
        AND julianday(?) - julianday(first_seen_at) > 3
        ORDER BY days_running DESC
        LIMIT 5
    """, (date.isoformat(), date.isoformat()))

    stuck_orders = [
        {
            "order_hash": row[0][:16] + "...",
            "symbol": row[1],
            "side": row[2],
            "first_seen_at": row[3],
            "days_running": round(row[4], 2)
        }
        for row in storage.cursor.fetchall()
    ]
    anomalies['stuck_orders'] = stuck_orders

    # High cancel rate addresses (> 50% cancel rate, min 5 orders)
    storage.cursor.execute("""
        SELECT 
            address,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) as canceled,
            ROUND(SUM(CASE WHEN status = 'canceled' THEN 1.0 ELSE 0 END) / COUNT(*), 3) as cancel_rate
        FROM orders
        WHERE first_seen_at BETWEEN ? AND ?
        GROUP BY address
        HAVING total >= 5 AND cancel_rate > 0.5
        ORDER BY cancel_rate DESC
        LIMIT 5
    """, (start_time, end_time))

    high_cancel_addresses = [
        {
            "address": row[0][:10] + "...",
            "full_address": row[0],
            "total_orders": row[1],
            "canceled": row[2],
            "cancel_rate": row[3]
        }
        for row in storage.cursor.fetchall()
    ]
    anomalies['high_cancel_rate_addresses'] = high_cancel_addresses

    return anomalies