#!/usr/bin/env python3
"""
Events Daily Summary Generator
==============================
Generates daily summary JSON from the events table.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def generate_events_summary(
        storage,
        date: datetime,
        output_dir: Path = Path('reports')
) -> Optional[Dict]:
    """
    Generate daily summary for events table.

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

    logger.info(f"Generating events summary for {date_str}")

    # Check if data exists
    storage.cursor.execute(
        "SELECT COUNT(*) FROM events WHERE timestamp BETWEEN ? AND ?",
        (start_time, end_time)
    )
    total_events = storage.cursor.fetchone()[0]

    if total_events == 0:
        logger.warning(f"No events for {date_str}")
        return None

    summary = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "events": _get_event_totals(storage, start_time, end_time, total_events),
        "new_orders": _get_new_orders_stats(storage, start_time, end_time),
        "completed_orders": _get_completed_orders_stats(storage, start_time, end_time),
        "canceled_orders": _get_canceled_orders_stats(storage, start_time, end_time),
        "anomalies": _detect_anomalies(storage, start_time, end_time),
    }

    # Save to file
    output_path = output_dir / date_str
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'events.json'
    with open(file_path, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved events summary to {file_path}")

    return summary


def _get_event_totals(storage, start_time: str, end_time: str, total: int) -> Dict:
    """Get event type totals and rates."""
    storage.cursor.execute("""
        SELECT event_type, COUNT(*) as count
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY event_type
    """, (start_time, end_time))

    by_type = {row[0]: row[1] for row in storage.cursor.fetchall()}

    new = by_type.get('new', 0)
    completed = by_type.get('completed', 0)
    canceled = by_type.get('canceled', 0)

    # Rates based on completed + canceled (resolved orders)
    resolved = completed + canceled
    completion_rate = round(completed / resolved, 3) if resolved > 0 else 0
    cancellation_rate = round(canceled / resolved, 3) if resolved > 0 else 0

    return {
        "total": total,
        "by_type": by_type,
        "completion_rate": completion_rate,
        "cancellation_rate": cancellation_rate
    }


def _get_new_orders_stats(storage, start_time: str, end_time: str) -> Dict:
    """Get statistics for new orders."""
    # By side
    storage.cursor.execute("""
        SELECT side, COUNT(*) as count
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'new'
        GROUP BY side
    """, (start_time, end_time))

    by_side = {row[0]: row[1] for row in storage.cursor.fetchall()}

    # By product type
    storage.cursor.execute("""
        SELECT product_type, COUNT(*) as count
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'new'
        GROUP BY product_type
    """, (start_time, end_time))

    by_product = {row[0]: row[1] for row in storage.cursor.fetchall()}

    # Total count
    total = sum(by_side.values())

    # Top coins with buy/sell breakdown
    # row[0]=symbol, row[1]=count, row[2]=buys, row[3]=sells, row[4]=total_size, row[5]=price
    storage.cursor.execute("""
        SELECT 
            e.symbol,
            COUNT(*) as count,
            SUM(CASE WHEN e.side = 'BUY' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN e.side = 'SELL' THEN 1 ELSE 0 END) as sells,
            SUM(e.size) as total_size,
            s.price
        FROM events e
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON e.symbol = s.symbol
        WHERE e.timestamp BETWEEN ? AND ?
        AND e.event_type = 'new'
        GROUP BY e.symbol
        ORDER BY count DESC
        LIMIT 10
    """, (start_time, end_time, start_time, end_time))

    top_coins = []
    coins_missing_price = []

    for row in storage.cursor.fetchall():
        symbol = row[0]
        count = row[1]
        buys = row[2]
        sells = row[3]
        total_size = row[4]
        price = row[5] or 0

        size_usd = round(total_size * price, 2) if price else 0

        if not price:
            coins_missing_price.append(symbol)

        top_coins.append({
            "symbol": symbol,
            "count": count,
            "buys": buys,
            "sells": sells,
            "size_usd": size_usd
        })

    # Top addresses with USD values
    storage.cursor.execute("""
        SELECT 
            e.address,
            COUNT(*) as count,
            SUM(CASE WHEN e.side = 'BUY' THEN 1 ELSE 0 END) as buys,
            SUM(CASE WHEN e.side = 'SELL' THEN 1 ELSE 0 END) as sells,
            SUM(e.size * COALESCE(s.price, 0)) as size_usd
        FROM events e
        LEFT JOIN (
            SELECT symbol, AVG(price) as price
            FROM snapshots
            WHERE timestamp BETWEEN ? AND ?
            GROUP BY symbol
        ) s ON e.symbol = s.symbol
        WHERE e.timestamp BETWEEN ? AND ?
        AND e.event_type = 'new'
        GROUP BY e.address
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
        "top_addresses": top_addresses,
        "coins_missing_price": coins_missing_price
    }


def _get_completed_orders_stats(storage, start_time: str, end_time: str) -> Dict:
    """Get statistics for completed orders."""
    storage.cursor.execute("""
        SELECT 
            COUNT(*) as count,
            AVG(duration_minutes) as avg_duration,
            AVG(elapsed_minutes) as avg_elapsed
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'completed'
    """, (start_time, end_time))

    row = storage.cursor.fetchone()
    total = row[0]
    avg_duration = round(row[1], 1) if row[1] else 0
    avg_elapsed = round(row[2], 1) if row[2] else 0

    # Top coins
    storage.cursor.execute("""
        SELECT 
            symbol,
            COUNT(*) as count,
            AVG(elapsed_minutes) as avg_elapsed
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'completed'
        GROUP BY symbol
        ORDER BY count DESC
        LIMIT 10
    """, (start_time, end_time))

    top_coins = [
        {
            "symbol": row[0],
            "count": row[1],
            "avg_elapsed_minutes": round(row[2], 1)
        }
        for row in storage.cursor.fetchall()
    ]

    return {
        "total": total,
        "avg_duration_minutes": avg_duration,
        "avg_elapsed_minutes": avg_elapsed,
        "top_coins": top_coins
    }


def _get_canceled_orders_stats(storage, start_time: str, end_time: str) -> Dict:
    """Get statistics for canceled orders."""
    storage.cursor.execute("""
        SELECT 
            COUNT(*) as count,
            AVG(progress_percent) as avg_progress,
            SUM(CASE WHEN progress_percent < 10 THEN 1 ELSE 0 END) as quick_cancels
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'canceled'
    """, (start_time, end_time))

    row = storage.cursor.fetchone()
    total = row[0]
    avg_progress = round(row[1], 1) if row[1] else 0
    quick_cancels = row[2] or 0

    # Top coins
    storage.cursor.execute("""
        SELECT 
            symbol,
            COUNT(*) as count,
            AVG(progress_percent) as avg_progress
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'canceled'
        GROUP BY symbol
        ORDER BY count DESC
        LIMIT 10
    """, (start_time, end_time))

    top_coins = [
        {
            "symbol": row[0],
            "count": row[1],
            "avg_progress_at_cancel": round(row[2], 1)
        }
        for row in storage.cursor.fetchall()
    ]

    return {
        "total": total,
        "avg_progress_at_cancel": avg_progress,
        "quick_cancels": quick_cancels,
        "quick_cancel_rate": round(quick_cancels / total, 3) if total > 0 else 0,
        "top_coins": top_coins
    }


def _detect_anomalies(storage, start_time: str, end_time: str) -> Dict:
    """Detect anomalies in the events data."""
    anomalies = {}

    # Get totals for calculations
    storage.cursor.execute("""
        SELECT event_type, COUNT(*) 
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY event_type
    """, (start_time, end_time))

    totals = {row[0]: row[1] for row in storage.cursor.fetchall()}
    new = totals.get('new', 0)
    completed = totals.get('completed', 0)
    canceled = totals.get('canceled', 0)
    resolved = completed + canceled

    # High cancellation rate (>50%)
    cancellation_rate = canceled / resolved if resolved > 0 else 0
    anomalies['high_cancellation_rate'] = cancellation_rate > 0.5
    anomalies['cancellation_rate'] = round(cancellation_rate, 3)

    # Buy/sell imbalance (>70% one direction)
    storage.cursor.execute("""
        SELECT side, COUNT(*) 
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'new'
        GROUP BY side
    """, (start_time, end_time))

    sides = {row[0]: row[1] for row in storage.cursor.fetchall()}
    buy = sides.get('BUY', 0)
    sell = sides.get('SELL', 0)
    total_orders = buy + sell

    if total_orders > 0:
        buy_pct = buy / total_orders
        sell_pct = sell / total_orders
        anomalies['buy_sell_imbalance'] = buy_pct > 0.7 or sell_pct > 0.7
        anomalies['buy_pct'] = round(buy_pct, 3)
        anomalies['sell_pct'] = round(sell_pct, 3)
    else:
        anomalies['buy_sell_imbalance'] = False

    # Address concentration (any address >5% of new orders)
    storage.cursor.execute("""
        SELECT 
            address,
            COUNT(*) as count
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'new'
        GROUP BY address
        HAVING count > ?
        ORDER BY count DESC
        LIMIT 5
    """, (start_time, end_time, new * 0.05 if new > 0 else 0))

    concentrated_addresses = [
        {
            "address": row[0][:10] + "...",
            "full_address": row[0],
            "count": row[1],
            "pct_of_total": round(row[1] / new * 100, 2) if new > 0 else 0
        }
        for row in storage.cursor.fetchall()
    ]
    anomalies['address_concentration'] = concentrated_addresses

    # Quick cancel rate (>20% canceled at <10% progress)
    storage.cursor.execute("""
        SELECT 
            COUNT(*) as quick,
            (SELECT COUNT(*) FROM events WHERE timestamp BETWEEN ? AND ? AND event_type = 'canceled') as total
        FROM events 
        WHERE timestamp BETWEEN ? AND ?
        AND event_type = 'canceled'
        AND progress_percent < 10
    """, (start_time, end_time, start_time, end_time))

    row = storage.cursor.fetchone()
    quick = row[0] or 0
    total_canceled = row[1] or 0
    quick_rate = quick / total_canceled if total_canceled > 0 else 0
    anomalies['quick_cancel_rate'] = round(quick_rate, 3)
    anomalies['high_quick_cancel_rate'] = quick_rate > 0.2

    return anomalies