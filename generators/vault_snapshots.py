#!/usr/bin/env python3
"""
Vault Snapshots Daily Summary Generator
=======================================
Daily overview of whale vault deposits.
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def generate_vault_snapshots_summary(
        storage,
        date: datetime,
        output_dir: Path = Path('reports')
) -> Optional[Dict]:
    """
    Generate daily summary for vault_snapshots table.
    """
    date_str = date.strftime('%Y-%m-%d')
    start_time = f"{date_str}T00:00:00"
    end_time = f"{date_str}T23:59:59"

    logger.info(f"Generating vault_snapshots summary for {date_str}")

    summary = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "overview": _get_overview(storage, start_time, end_time),
        "top_vaults": _get_top_vaults(storage, start_time, end_time),
        "top_depositors": _get_top_depositors(storage, start_time, end_time),
    }

    # Save to file
    output_path = output_dir / date_str
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'vault_snapshots.json'
    with open(file_path, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"Saved vault_snapshots summary to {file_path}")

    return summary


def _get_overview(storage, start_time: str, end_time: str) -> Dict:
    """General vault stats for the day."""
    storage.cursor.execute("""
        SELECT 
            COUNT(DISTINCT address) as unique_whales,
            COUNT(DISTINCT vault_address) as unique_vaults,
            SUM(value) as total_value
        FROM (
            SELECT address, vault_address, value,
                   ROW_NUMBER() OVER (PARTITION BY address, vault_address ORDER BY snapshot_time DESC) as rn
            FROM vault_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
    """, (start_time, end_time))

    row = storage.cursor.fetchone()

    if not row or not row[0]:
        return {"unique_whales": 0}

    return {
        "unique_whales": row[0],
        "unique_vaults": row[1],
        "total_value": round(row[2], 2) if row[2] else 0
    }


def _get_top_vaults(storage, start_time: str, end_time: str) -> list:
    """Top vaults by total deposited value."""
    storage.cursor.execute("""
        SELECT 
            vault_address,
            COUNT(DISTINCT address) as whale_count,
            SUM(value) as total_value,
            AVG(value) as avg_deposit
        FROM (
            SELECT address, vault_address, value,
                   ROW_NUMBER() OVER (PARTITION BY address, vault_address ORDER BY snapshot_time DESC) as rn
            FROM vault_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
        GROUP BY vault_address
        ORDER BY total_value DESC
        LIMIT 10
    """, (start_time, end_time))

    vaults = []
    for row in storage.cursor.fetchall():
        vaults.append({
            "vault_address": row[0],
            "whale_count": row[1],
            "total_value": round(row[2], 2) if row[2] else 0,
            "avg_deposit": round(row[3], 2) if row[3] else 0
        })

    return vaults


def _get_top_depositors(storage, start_time: str, end_time: str) -> list:
    """Top whales by total vault deposits."""
    storage.cursor.execute("""
        SELECT 
            address,
            COUNT(DISTINCT vault_address) as vault_count,
            SUM(value) as total_value
        FROM (
            SELECT address, vault_address, value,
                   ROW_NUMBER() OVER (PARTITION BY address, vault_address ORDER BY snapshot_time DESC) as rn
            FROM vault_snapshots
            WHERE snapshot_time BETWEEN ? AND ?
        )
        WHERE rn = 1
        GROUP BY address
        ORDER BY total_value DESC
        LIMIT 10
    """, (start_time, end_time))

    depositors = []
    for row in storage.cursor.fetchall():
        depositors.append({
            "address": row[0],
            "vault_count": row[1],
            "total_value": round(row[2], 2) if row[2] else 0
        })

    return depositors