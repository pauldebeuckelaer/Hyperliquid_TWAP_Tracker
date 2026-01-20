#!/usr/bin/env python3
"""
Snapshots Daily Summary Generator
=================================
Generates daily summary JSON from the snapshots table.
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def generate_snapshots_summary(
        storage,
        date: datetime,
        output_dir: Path = Path('reports')
) -> Optional[Dict]:
    """
    Generate daily summary for snapshots table.

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

    logger.info(f"Generating snapshots summary for {date_str}")

    # Check if data exists
    storage.cursor.execute(
        "SELECT COUNT(*) FROM snapshots WHERE timestamp BETWEEN ? AND ?",
        (start_time, end_time)
    )
    total_snapshots = storage.cursor.fetchone()[0]

    if total_snapshots == 0:
        logger.warning(f"No snapshots for {date_str}")
        return None

    summary = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "snapshots": _get_snapshot_stats(storage, start_time, end_time, total_snapshots),
        "cycle_timing": _get_cycle_timing(storage, start_time, end_time),
        "pressure": _get_pressure_stats(storage, start_time, end_time),
        "activity": _get_activity_stats(storage, start_time, end_time),
        "addresses": _get_address_stats(storage, start_time, end_time),
    }

    # Save to file
    output_path = output_dir / date_str
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'snapshots.json'
    with open(file_path, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved summary to {file_path}")

    return summary


def _get_snapshot_stats(storage, start_time: str, end_time: str, total: int) -> Dict:
    """Basic snapshot statistics."""
    storage.cursor.execute(
        "SELECT COUNT(DISTINCT symbol) FROM snapshots WHERE timestamp BETWEEN ? AND ?",
        (start_time, end_time)
    )
    unique_symbols = storage.cursor.fetchone()[0]

    storage.cursor.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM snapshots WHERE timestamp BETWEEN ? AND ?",
        (start_time, end_time)
    )
    row = storage.cursor.fetchone()

    return {
        "total_snapshots": total,
        "unique_symbols": unique_symbols,
        "time_range": {
            "first": row[0],
            "last": row[1]
        }
    }


def _get_cycle_timing(storage, start_time: str, end_time: str) -> Dict:
    """Calculate cycle timing statistics."""
    # Get first snapshot of each cycle (grouped by minute)
    storage.cursor.execute("""
        SELECT MIN(timestamp) as cycle_start
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY substr(timestamp, 1, 16)
        ORDER BY cycle_start
    """, (start_time, end_time))

    cycle_starts = [row[0] for row in storage.cursor.fetchall()]

    if len(cycle_starts) < 2:
        return {"cycles": len(cycle_starts), "error": "not enough data"}

    # Calculate gaps between cycles
    gaps = []
    for i in range(1, len(cycle_starts)):
        t1 = datetime.fromisoformat(cycle_starts[i - 1])
        t2 = datetime.fromisoformat(cycle_starts[i])
        gap = (t2 - t1).total_seconds()
        gaps.append((gap, cycle_starts[i]))

    gap_values = [g[0] for g in gaps]
    max_gap = max(gaps, key=lambda x: x[0])

    return {
        "cycles": len(cycle_starts),
        "avg_seconds": round(sum(gap_values) / len(gap_values), 1),
        "min_seconds": round(min(gap_values), 1),
        "max_seconds": round(max_gap[0], 1),
        "max_gap_at": max_gap[1],
        "cycles_over_65s": sum(1 for g in gap_values if g > 65)
    }


def _get_pressure_stats(storage, start_time: str, end_time: str) -> Dict:
    """Calculate pressure statistics with perp/spot breakdown in USD."""
    # Total perp/spot pressure in USD
    storage.cursor.execute("""
        SELECT 
            SUM(perp_buy_pressure * price) as perp_buy,
            SUM(perp_sell_pressure * price) as perp_sell,
            SUM(spot_buy_pressure * price) as spot_buy,
            SUM(spot_sell_pressure * price) as spot_sell
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ?
    """, (start_time, end_time))

    row = storage.cursor.fetchone()
    perp_buy = row[0] or 0
    perp_sell = row[1] or 0
    spot_buy = row[2] or 0
    spot_sell = row[3] or 0

    # Top buy pressure coins (USD)
    storage.cursor.execute("""
        SELECT 
            symbol,
            SUM(net_pressure * price) as net_usd,
            SUM((perp_buy_pressure - perp_sell_pressure) * price) as perp_net_usd,
            SUM((spot_buy_pressure - spot_sell_pressure) * price) as spot_net_usd
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY symbol
        HAVING SUM(net_pressure * price) > 0
        ORDER BY net_usd DESC
        LIMIT 10
    """, (start_time, end_time))

    top_buy = [
        {
            "symbol": row[0],
            "net_usd": round(row[1], 2),
            "perp_usd": round(row[2], 2),
            "spot_usd": round(row[3], 2)
        }
        for row in storage.cursor.fetchall()
    ]

    # Top sell pressure coins (USD)
    storage.cursor.execute("""
        SELECT 
            symbol,
            SUM(net_pressure * price) as net_usd,
            SUM((perp_buy_pressure - perp_sell_pressure) * price) as perp_net_usd,
            SUM((spot_buy_pressure - spot_sell_pressure) * price) as spot_net_usd
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY symbol
        HAVING SUM(net_pressure * price) < 0
        ORDER BY net_usd ASC
        LIMIT 10
    """, (start_time, end_time))

    top_sell = [
        {
            "symbol": row[0],
            "net_usd": round(row[1], 2),
            "perp_usd": round(row[2], 2),
            "spot_usd": round(row[3], 2)
        }
        for row in storage.cursor.fetchall()
    ]

    return {
        "perp": {
            "total_buy_usd": round(perp_buy, 2),
            "total_sell_usd": round(perp_sell, 2),
            "net_usd": round(perp_buy - perp_sell, 2)
        },
        "spot": {
            "total_buy_usd": round(spot_buy, 2),
            "total_sell_usd": round(spot_sell, 2),
            "net_usd": round(spot_buy - spot_sell, 2)
        },
        "top_buy_pressure": top_buy,
        "top_sell_pressure": top_sell
    }


def _get_activity_stats(storage, start_time: str, end_time: str) -> Dict:
    """Calculate activity statistics."""
    # Overall active orders stats
    storage.cursor.execute("""
        SELECT 
            AVG(active_orders),
            MAX(active_orders)
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ?
    """, (start_time, end_time))

    row = storage.cursor.fetchone()
    avg_active = row[0] or 0
    max_active = row[1] or 0

    # Find peak time
    storage.cursor.execute("""
        SELECT timestamp 
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ? 
        AND active_orders = ?
        LIMIT 1
    """, (start_time, end_time, max_active))

    peak_row = storage.cursor.fetchone()
    peak_time = peak_row[0] if peak_row else None

    # Most active coins
    storage.cursor.execute("""
        SELECT 
            symbol,
            ROUND(AVG(active_orders), 1) as avg_orders,
            MAX(active_orders) as max_orders
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ?
        GROUP BY symbol
        ORDER BY avg_orders DESC
        LIMIT 10
    """, (start_time, end_time))

    most_active = [
        {
            "symbol": row[0],
            "avg_orders": row[1],
            "max_orders": row[2]
        }
        for row in storage.cursor.fetchall()
    ]

    return {
        "avg_active_orders": round(avg_active, 1),
        "peak_active_orders": max_active,
        "peak_time": peak_time,
        "most_active_coins": most_active
    }


def _get_address_stats(storage, start_time: str, end_time: str) -> Dict:
    """Calculate address statistics."""
    storage.cursor.execute("""
        SELECT AVG(unique_addresses)
        FROM snapshots 
        WHERE timestamp BETWEEN ? AND ?
    """, (start_time, end_time))

    row = storage.cursor.fetchone()

    return {
        "avg_per_snapshot": round(row[0] or 0, 1)
    }