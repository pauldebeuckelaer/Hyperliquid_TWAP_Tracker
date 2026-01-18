#!/usr/bin/env python3
"""
Daily Summary Orchestrator
==========================
Runs all daily summary generators.
"""
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict

from generators.snapshots import generate_snapshots_summary
from generators.events import generate_events_summary
from generators.orders import generate_orders_summary
from generators.portfolio_snapshots import generate_portfolio_snapshots_summary
from generators.perp_snapshots import generate_perp_snapshots_summary
from generators.vault_snapshots import generate_vault_snapshots_summary

# from .orders import generate_orders_summary
# from .whale_events import generate_whale_events_summary
# ... add more as we build them

logger = logging.getLogger(__name__)


def generate_daily_summaries(storage, date: datetime, output_dir: Path = Path('reports')) -> Dict:
    """
    Generate all daily summaries.

    Args:
        storage: SQLiteBackend instance
        date: Date to summarize
        output_dir: Where to save JSON files

    Returns:
        Dict with results per generator
    """
    logger.info(f"Generating daily summaries for {date.date()}")

    results = {}

    # Snapshots
    try:
        results['snapshots'] = generate_snapshots_summary(storage, date, output_dir)
        logger.info("✅ snapshots summary complete")
    except Exception as e:
        logger.error(f"❌ snapshots summary failed: {e}")
        results['snapshots'] = None

    # Events
    try:
        results['events'] = generate_events_summary(storage, date, output_dir)
        logger.info("✅ events summary complete")
    except Exception as e:
        logger.error(f"❌ events summary failed: {e}")
        results['events'] = None


    # Orders
    try:
        results['orders'] = generate_orders_summary(storage, date, output_dir)
        logger.info("✅ orders summary complete")
    except Exception as e:
        logger.error(f"❌ orders summary failed: {e}")
        results['orders'] = None


    # Portfolio Snapshots (pollution detection)
    try:
        results['portfolio_snapshots'] = generate_portfolio_snapshots_summary(storage, date, output_dir)
        logger.info("✅ portfolio_snapshots summary complete")
    except Exception as e:
        logger.error(f"❌ portfolio_snapshots summary failed: {e}")
        results['portfolio_snapshots'] = None

    # Perp Snapshots (position overview)
    try:
        results['perp_snapshots'] = generate_perp_snapshots_summary(storage, date, output_dir)
        logger.info("✅ perp_snapshots summary complete")
    except Exception as e:
        logger.error(f"❌ perp_snapshots summary failed: {e}")
        results['perp_snapshots'] = None

    # Vault Snapshots
    try:
        results['vault_snapshots'] = generate_vault_snapshots_summary(storage, date, output_dir)
        logger.info("✅ vault_snapshots summary complete")
    except Exception as e:
        logger.error(f"❌ vault_snapshots summary failed: {e}")
        results['vault_snapshots'] = None

    logger.info(f"Daily summaries complete: {len([r for r in results.values() if r])} succeeded")

    # Orders (uncomment when ready)
    # try:
    #     results['orders'] = generate_orders_summary(storage, date, output_dir)
    #     logger.info("✅ orders summary complete")
    # except Exception as e:
    #     logger.error(f"❌ orders summary failed: {e}")
    #     results['orders'] = None

    # Add more generators here...


    return results