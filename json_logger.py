#!/usr/bin/env python3
"""
JSON Logger - TWAP State Snapshots
Logs complete TWAP state every minute
"""
import json
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


class SimpleJsonLogger:
    """Logs TWAP snapshots to JSON files every minute"""

    def __init__(self, config: dict = None):
        if config is None:
            config = {}

        self.log_dir = Path(config.get('log_dir', 'json_logs'))
        self.enabled = config.get('enabled', True)

        if not self.enabled:
            logger.info("JSON logging disabled")
            return

        # Create log directory
        self.log_dir.mkdir(exist_ok=True)
        logger.info(f"JSON logger initialized: {self.log_dir}")

    def log_data(self, data: Dict):
        """
        Log data dict to JSON file

        Args:
            data: Dict with all snapshot data including classifications
        """
        if not self.enabled:
            return

        try:
            # Build clean JSON structure
            json_output = {
                "timestamp": data['timestamp'].isoformat(),
                "symbol": data['symbol'],
                "update_number": data['update_number'],

                # Summary stats
                "summary": {
                    "total_orders": data['stats']['total_orders'],
                    "active_orders": data['stats']['active_orders'],
                    "buy_volume": round(data['stats']['buy_volume'], 2),
                    "sell_volume": round(data['stats']['sell_volume'], 2),
                    "net_flow": round(data['stats']['net_flow'], 2),
                    "buy_pressure_per_min": round(data['stats']['buy_pressure_per_min'], 2),
                    "sell_pressure_per_min": round(data['stats']['sell_pressure_per_min'], 2),
                    "net_pressure_per_min": round(data['stats']['net_pressure_per_min'], 2),
                    "whale_orders": data['stats']['whale_orders'],
                    "unique_addresses": data['stats']['unique_addresses']
                },

                # Events
                "events": {
                    "new_orders": len(data['new_orders']),
                    "completed_orders": len(data['completed_orders']),
                    "status_changes": len(data['status_changes'])
                },

                # Orders with classification
                "active_orders": [
                    {
                        "address": o['full_address'],
                        "side": o['side'],
                        "size": round(o['size'], 2),
                        "duration_hours": round(o['duration_hours'], 1),
                        "status": o['status'],
                        "product_type": o['product_type'],
                        "classification": o['classification'],
                        "is_whale": o['is_whale']
                    }
                    for o in data['orders']
                ],

                "new_orders": [
                    {
                        "address": o['full_address'],
                        "side": o['side'],
                        "size": round(o['size'], 2),
                        "classification": o['classification']
                    }
                    for o in data['new_orders']
                ],

                "completed_orders": [
                    {
                        "address": o['full_address'],
                        "side": o['side'],
                        "size": round(o['size'], 2),
                        "classification": o['classification']
                    }
                    for o in data['completed_orders']
                ]
            }

            # Save to JSONL file
            filename = self.log_dir / f"{data['symbol']}_{data['timestamp'].strftime('%Y%m%d')}.jsonl"

            with open(filename, 'a', encoding='utf-8') as f:
                json.dump(json_output, f, separators=(',', ':'))
                f.write('\n')

            logger.debug(f"Snapshot saved to {filename}")

        except Exception as e:
            logger.error(f"JSON logging failed: {e}")