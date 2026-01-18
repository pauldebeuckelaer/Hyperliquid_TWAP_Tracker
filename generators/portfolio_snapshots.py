#!/usr/bin/env python3
"""
Portfolio Snapshots Daily Summary Generator
============================================
Focuses on data quality checks to detect shitcoin pollution.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def generate_portfolio_snapshots_summary(
        storage,
        date: datetime,
        output_dir: Path = Path('reports')
) -> Optional[Dict]:
    """
    Generate daily summary for portfolio_snapshots table.
    Primary focus: detect pollution from shitcoins with fake prices.
    """
    date_str = date.strftime('%Y-%m-%d')
    start_time = f"{date_str}T00:00:00"
    end_time = f"{date_str}T23:59:59"

    logger.info(f"Generating portfolio_snapshots summary for {date_str}")

    summary = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "data_quality": _detect_pollution(storage, start_time, end_time),
        "whale_summary": _get_whale_summary(storage, start_time, end_time),
    }

    # Save to file
    output_path = output_dir / date_str
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'portfolio_snapshots.json'
    with open(file_path, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved portfolio_snapshots summary to {file_path}")

    return summary


def _detect_pollution(storage, start_time: str, end_time: str) -> Dict:
    """
    Detect shitcoins polluting spot values.
    """

    # Coins we trust (won't flag as pollution)
    major_coins = {
        'HYPE', 'BTC', 'ETH', 'SOL', 'USDC', 'USDT', 'USDE',
        'PURR', 'TRUMP', 'FARTCOIN', 'AI16Z', 'ANIME', 'MELANIA',
        'UBTC', 'UETH', 'USOL'
    }

    # Stablecoins - expected to have fixed $1 price
    stablecoins = {'USDC', 'USDT', 'USDE', 'USDH', 'DAI', 'USDT0'}

    major_coins_sql = ','.join(f"'{c}'" for c in major_coins)
    stablecoins_sql = ','.join(f"'{c}'" for c in stablecoins)

    # 1. Find coins with suspiciously stable prices (exclude stablecoins)
    storage.cursor.execute(f"""
        SELECT 
            coin,
            COUNT(*) as occurrences,
            MIN(price) as min_price,
            MAX(price) as max_price,
            AVG(price) as avg_price,
            MAX(value) as max_value
        FROM spot_snapshots
        WHERE snapshot_time BETWEEN ? AND ?
        AND value > 100000
        AND coin NOT IN ({stablecoins_sql})
        GROUP BY coin
        HAVING COUNT(*) >= 5
        AND (MAX(price) - MIN(price)) / NULLIF(AVG(price), 0) < 0.001
        ORDER BY max_value DESC
    """, (start_time, end_time))

    fixed_price_coins = []
    for row in storage.cursor.fetchall():
        fixed_price_coins.append({
            "coin": row[0],
            "occurrences": row[1],
            "price": row[4],
            "max_value": row[5]
        })

    # 2. Find high-value holdings in non-major coins
    storage.cursor.execute(f"""
        SELECT 
            coin,
            COUNT(DISTINCT address) as holders,
            SUM(value) as total_value,
            AVG(price) as avg_price,
            MAX(value) as max_single_holding
        FROM spot_snapshots
        WHERE snapshot_time BETWEEN ? AND ?
        AND coin NOT IN ({major_coins_sql})
        AND value > 1000000
        GROUP BY coin
        ORDER BY total_value DESC
        LIMIT 20
    """, (start_time, end_time))

    suspicious_coins = []
    for row in storage.cursor.fetchall():
        suspicious_coins.append({
            "coin": row[0],
            "holders": row[1],
            "total_value": row[2],
            "avg_price": row[3],
            "max_holding": row[4]
        })

    # 3. Find addresses most affected by pollution
    storage.cursor.execute(f"""
        SELECT 
            ps.address,
            MAX(ps.spot_value) as spot_value,
            MAX(ps.total_portfolio_value) as total_portfolio,
            GROUP_CONCAT(DISTINCT ss.coin) as spot_coins
        FROM portfolio_snapshots ps
        JOIN spot_snapshots ss ON ps.address = ss.address 
            AND DATE(ps.snapshot_time) = DATE(ss.snapshot_time)
        WHERE ps.snapshot_time BETWEEN ? AND ?
        AND ps.spot_value > 5000000
        AND ss.coin NOT IN ({major_coins_sql})
        AND ss.value > 1000000
        GROUP BY ps.address
        ORDER BY spot_value DESC
        LIMIT 10
    """, (start_time, end_time))

    affected_addresses = []
    for row in storage.cursor.fetchall():
        affected_addresses.append({
            "address": row[0],
            "spot_value": row[1],
            "total_portfolio": row[2],
            "suspicious_coins": row[3].split(',') if row[3] else []
        })

    # 4. Generate blacklist candidates (exclude stablecoins)
    blacklist_candidates = set()

    for coin in fixed_price_coins:
        if coin['max_value'] > 500000 and coin['coin'] not in stablecoins:
            blacklist_candidates.add(coin['coin'])

    for coin in suspicious_coins:
        if coin['holders'] <= 2 and coin['total_value'] > 5000000 and coin['coin'] not in stablecoins:
            blacklist_candidates.add(coin['coin'])

    return {
        "fixed_price_coins": fixed_price_coins,
        "suspicious_high_value_coins": suspicious_coins,
        "affected_addresses": affected_addresses,
        "blacklist_candidates": list(blacklist_candidates)
    }


def _get_whale_summary(storage, start_time: str, end_time: str) -> Dict:
    """Aggregate whale stats for the day."""

    # Get latest snapshot per whale for the day
    storage.cursor.execute("""
        SELECT 
            COUNT(DISTINCT address) as whale_count,
            AVG(total_portfolio_value) as avg_portfolio,
            SUM(total_portfolio_value) as total_value,
            AVG(leverage_ratio) as avg_leverage,
            SUM(margin_used) as total_margin,
            SUM(num_positions) as total_positions
        FROM (
            SELECT address, total_portfolio_value, leverage_ratio, margin_used, num_positions,
                   ROW_NUMBER() OVER (PARTITION BY address ORDER BY snapshot_time DESC) as rn
            FROM portfolio_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
    """, (start_time, end_time))

    row = storage.cursor.fetchone()

    if not row or not row[0]:
        return {"whale_count": 0}

    # Top whales by portfolio value
    storage.cursor.execute("""
        SELECT address, total_portfolio_value, perp_value, spot_value, leverage_ratio
        FROM (
            SELECT address, total_portfolio_value, perp_value, spot_value, leverage_ratio,
                   ROW_NUMBER() OVER (PARTITION BY address ORDER BY snapshot_time DESC) as rn
            FROM portfolio_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
        ORDER BY total_portfolio_value DESC
        LIMIT 10
    """, (start_time, end_time))

    top_whales = []
    for whale in storage.cursor.fetchall():
        top_whales.append({
            "address": whale[0],
            "total_portfolio": whale[1],
            "perp_value": whale[2],
            "spot_value": whale[3],
            "leverage": whale[4]
        })

    return {
        "whale_count": row[0],
        "avg_portfolio_value": row[1],
        "total_portfolio_value": row[2],
        "avg_leverage": row[3],
        "total_margin_used": row[4],
        "total_positions": int(row[5]) if row[5] else 0,
        "top_whales": top_whales
    }