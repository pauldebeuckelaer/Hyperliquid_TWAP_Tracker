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
            logger.info("📝 JSON logging disabled")
            return

        # Create log directory
        self.log_dir.mkdir(exist_ok=True)
        logger.info(f"✅ JSON logger initialized: {self.log_dir}")

    def log_snapshot(self, snapshot, changes: Dict):
        """
        Log a complete TWAP snapshot with changes

        Args:
            snapshot: TWAPSnapshot object
            changes: Dict from snapshot.compare_with()
        """
        if not self.enabled:
            return

        try:
            stats = snapshot.get_stats()

            # Build JSON structure
            data = {
                "timestamp": snapshot.timestamp.isoformat(),
                "symbol": snapshot.symbol,
                "update_number": snapshot.update_number,

                # Summary stats
                "summary": {
                    "total_orders": stats['total_orders'],
                    "active_orders": stats['active_orders'],
                    "buy_volume": round(stats['buy_volume'], 2),
                    "sell_volume": round(stats['sell_volume'], 2),
                    "net_flow": round(stats['net_flow'], 2),
                    "buy_pressure_per_min": round(stats['buy_pressure_per_min'], 2),
                    "sell_pressure_per_min": round(stats['sell_pressure_per_min'], 2),
                    "net_pressure_per_min": round(stats['net_pressure_per_min'], 2),
                    "whale_orders": stats['whale_orders'],
                    "unique_addresses": stats['unique_addresses']
                },

                # Events this minute
                "events": {
                    "new_orders": len(changes.get('new_orders', [])),
                    "completed_orders": len(changes.get('completed_orders', [])),
                    "canceled_orders": len([c for c in changes.get('status_changes', [])
                                            if c['new_status'] == 'canceled'])
                },

                # Active orders
                "active_orders": self._format_orders(
                    snapshot.orders,
                    new_addresses=[o.full_address for o in changes.get('new_orders', [])]
                ),

                # Completed orders this minute
                "completed_orders": self._format_orders(changes.get('completed_orders', [])),

                # Canceled orders this minute
                "canceled_orders": self._format_canceled_orders(
                    changes.get('status_changes', [])
                )
            }

            # Save to JSONL file (one per day)
            filename = self.log_dir / f"{snapshot.symbol}_{snapshot.timestamp.strftime('%Y%m%d')}.jsonl"

            with open(filename, 'a', encoding='utf-8') as f:
                json.dump(data, f, separators=(',', ':'))
                f.write('\n')

            logger.debug(f"💾 Snapshot saved to {filename}")

        except Exception as e:
            logger.error(f"❌ JSON logging failed: {e}")

    def _format_orders(self, orders: List, new_addresses: List[str] = None) -> List[Dict]:
        """Format orders for JSON output"""
        if new_addresses is None:
            new_addresses = []

        formatted = []
        for order in orders:
            order_data = {
                "address": order.full_address,
                "side": order.side,
                "size": round(order.size, 2),
                "duration_hours": round(order.duration_hours, 1),
                "status": order.status,
                "product_type": order.product_type,
                "is_whale": order.is_whale
            }

            # Add is_new flag only for active orders
            if new_addresses and order.full_address in new_addresses:
                order_data["is_new"] = True

            formatted.append(order_data)

        return formatted

    def _format_canceled_orders(self, status_changes: List[Dict]) -> List[Dict]:
        """Format canceled orders from status changes"""
        canceled = []
        for change in status_changes:
            if change.get('new_status') == 'canceled':
                canceled.append({
                    "address": change['address'],
                    "side": change['side'],
                    "size": round(change['size'], 2),
                    "duration_hours": round(change.get('duration_hours', 0), 1),
                    "product_type": change.get('product_type', 'SPOT'),
                    "is_whale": change.get('is_whale', False)
                })
        return canceled