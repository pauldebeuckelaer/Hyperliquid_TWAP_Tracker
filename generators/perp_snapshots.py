#!/usr/bin/env python3
"""
Perp Snapshots Daily Summary Generator
======================================
Daily overview of whale perpetual positions.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def generate_perp_snapshots_summary(
        storage,
        date: datetime,
        output_dir: Path = Path('reports')
) -> Optional[Dict]:
    """
    Generate daily summary for perp_snapshots table.
    """
    date_str = date.strftime('%Y-%m-%d')
    start_time = f"{date_str}T00:00:00"
    end_time = f"{date_str}T23:59:59"

    logger.info(f"Generating perp_snapshots summary for {date_str}")

    summary = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "overview": _get_overview(storage, start_time, end_time),
        "top_coins": _get_top_coins(storage, start_time, end_time),
        "sentiment": _get_sentiment(storage, start_time, end_time),
        "risk_overview": _get_risk_overview(storage, start_time, end_time),
    }

    # Save to file
    output_path = output_dir / date_str
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'perp_snapshots.json'
    with open(file_path, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved perp_snapshots summary to {file_path}")

    return summary


def _get_overview(storage, start_time: str, end_time: str) -> Dict:
    """General stats for the day."""
    storage.cursor.execute("""
        SELECT 
            COUNT(DISTINCT address) as unique_whales,
            COUNT(DISTINCT coin) as unique_coins,
            COUNT(*) as total_snapshots,
            AVG(leverage) as avg_leverage,
            SUM(margin_used) as total_margin,
            SUM(unrealized_pnl) as total_pnl
        FROM perp_snapshots
        WHERE snapshot_time BETWEEN ? AND ?
    """, (start_time, end_time))

    row = storage.cursor.fetchone()

    if not row or not row[0]:
        return {"unique_whales": 0}

    return {
        "unique_whales": row[0],
        "unique_coins": row[1],
        "total_snapshots": row[2],
        "avg_leverage": round(row[3], 2) if row[3] else 0,
        "total_margin_used": row[4],
        "total_unrealized_pnl": row[5]
    }


def _get_top_coins(storage, start_time: str, end_time: str) -> Dict:
    """Most traded coins by whales."""
    # Latest snapshot per address/coin combo
    storage.cursor.execute("""
        SELECT 
            coin,
            COUNT(DISTINCT address) as whale_count,
            SUM(CASE WHEN side = 'LONG' THEN 1 ELSE 0 END) as longs,
            SUM(CASE WHEN side = 'SHORT' THEN 1 ELSE 0 END) as shorts,
            SUM(ABS(size) * entry_price) as notional_value,
            AVG(leverage) as avg_leverage
        FROM (
            SELECT address, coin, side, size, entry_price, leverage,
                   ROW_NUMBER() OVER (PARTITION BY address, coin ORDER BY snapshot_time DESC) as rn
            FROM perp_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
        GROUP BY coin
        ORDER BY notional_value DESC
        LIMIT 15
    """, (start_time, end_time))

    coins = []
    for row in storage.cursor.fetchall():
        coins.append({
            "coin": row[0],
            "whale_count": row[1],
            "longs": row[2],
            "shorts": row[3],
            "notional_value": round(row[4], 2) if row[4] else 0,
            "avg_leverage": round(row[5], 2) if row[5] else 0
        })

    return coins


def _get_sentiment(storage, start_time: str, end_time: str) -> Dict:
    """Long/short sentiment across all positions."""
    storage.cursor.execute("""
        SELECT 
            side,
            COUNT(DISTINCT address) as whale_count,
            SUM(ABS(size) * entry_price) as notional_value,
            SUM(unrealized_pnl) as total_pnl
        FROM (
            SELECT address, coin, side, size, entry_price, unrealized_pnl,
                   ROW_NUMBER() OVER (PARTITION BY address, coin ORDER BY snapshot_time DESC) as rn
            FROM perp_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
        GROUP BY side
    """, (start_time, end_time))

    sentiment = {}
    for row in storage.cursor.fetchall():
        sentiment[row[0].lower()] = {
            "whale_count": row[1],
            "notional_value": round(row[2], 2) if row[2] else 0,
            "total_pnl": round(row[3], 2) if row[3] else 0
        }

    # Calculate ratio
    long_val = sentiment.get('long', {}).get('notional_value', 0)
    short_val = sentiment.get('short', {}).get('notional_value', 0)
    total = long_val + short_val

    sentiment['long_ratio'] = round(long_val / total, 3) if total > 0 else 0
    sentiment['short_ratio'] = round(short_val / total, 3) if total > 0 else 0

    return sentiment


def _get_risk_overview(storage, start_time: str, end_time: str) -> Dict:
    """High leverage and risk metrics."""
    # High leverage positions (>10x)
    storage.cursor.execute("""
        SELECT COUNT(DISTINCT address || coin) 
        FROM perp_snapshots
        WHERE snapshot_time BETWEEN ? AND ?
        AND leverage > 10
    """, (start_time, end_time))
    high_leverage_count = storage.cursor.fetchone()[0]

    # Very high leverage (>20x)
    storage.cursor.execute("""
        SELECT COUNT(DISTINCT address || coin) 
        FROM perp_snapshots
        WHERE snapshot_time BETWEEN ? AND ?
        AND leverage > 20
    """, (start_time, end_time))
    very_high_leverage_count = storage.cursor.fetchone()[0]

    # Biggest losers (most negative PnL)
    storage.cursor.execute("""
        SELECT address, coin, side, unrealized_pnl, leverage
        FROM (
            SELECT address, coin, side, unrealized_pnl, leverage,
                   ROW_NUMBER() OVER (PARTITION BY address, coin ORDER BY snapshot_time DESC) as rn
            FROM perp_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
        ORDER BY unrealized_pnl ASC
        LIMIT 5
    """, (start_time, end_time))

    biggest_losers = []
    for row in storage.cursor.fetchall():
        biggest_losers.append({
            "address": row[0],
            "coin": row[1],
            "side": row[2],
            "unrealized_pnl": round(row[3], 2) if row[3] else 0,
            "leverage": round(row[4], 2) if row[4] else 0
        })

    return {
        "high_leverage_positions": high_leverage_count,
        "very_high_leverage_positions": very_high_leverage_count,
        "biggest_losers": biggest_losers
    }