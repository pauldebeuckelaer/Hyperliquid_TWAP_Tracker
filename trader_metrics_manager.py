#!/usr/bin/env python3
"""
Trader Metrics Manager
Manages collection and storage of trader metrics with two-tier system
"""
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Set, Optional, List
from collections import deque

logger = logging.getLogger(__name__)


class TraderMetricsManager:
    """
    Manages trader metrics collection with two-tier system:
    - LIGHT: Quick essential metrics (every 6h)
    - DEEP: Complete metrics (every 24h)
    """

    def __init__(self, hl_client, config: Optional[Dict] = None):
        """
        Initialize metrics manager

        Args:
            hl_client: HyperliquidClient instance
            config: Configuration dict
        """
        self.hl_client = hl_client
        self.config = config or {}

        # File paths
        self.metrics_file = Path('trader_metrics.json')
        self.history_dir = Path('trader_metrics_history')
        self.history_dir.mkdir(exist_ok=True)

        # Update intervals
        self.light_interval_hours = self.config.get('light_interval_hours', 6)
        self.deep_interval_hours = self.config.get('deep_interval_hours', 24)

        # Storage
        self.metrics_data = self._load_metrics()
        self.update_queue = deque()  # Addresses needing updates

        # Tracking
        self.addresses_seen: Set[str] = set()
        self.last_save_time = datetime.now()

        logger.info(
            f"TraderMetricsManager initialized: "
            f"light={self.light_interval_hours}h, deep={self.deep_interval_hours}h"
        )

    def _load_metrics(self) -> Dict:
        """Load existing metrics from file"""
        if self.metrics_file.exists():
            try:
                with open(self.metrics_file, 'r') as f:
                    data = json.load(f)
                    logger.info(f"Loaded metrics for {len(data.get('traders', {}))} traders")
                    return data
            except Exception as e:
                logger.error(f"Error loading metrics: {e}")

        # Initialize empty structure
        return {
            "collection_info": {
                "created": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "total_addresses": 0,
                "light_fetches": 0,
                "deep_fetches": 0
            },
            "traders": {}
        }

    def _save_metrics(self):
        """Save metrics to file"""
        try:
            # Update metadata
            self.metrics_data["collection_info"]["last_updated"] = datetime.now().isoformat()
            self.metrics_data["collection_info"]["total_addresses"] = len(self.metrics_data["traders"])

            # Save to main file
            with open(self.metrics_file, 'w') as f:
                json.dump(self.metrics_data, f, indent=2)

            self.last_save_time = datetime.now()
            logger.debug(f"Saved metrics for {len(self.metrics_data['traders'])} traders")

        except Exception as e:
            logger.error(f"Error saving metrics: {e}")

    def _archive_to_history(self):
        """Archive current metrics to history folder"""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
            archive_file = self.history_dir / f"trader_metrics_{timestamp}.json"

            with open(archive_file, 'w') as f:
                json.dump(self.metrics_data, f, indent=2)

            logger.info(f"Archived metrics to {archive_file}")

        except Exception as e:
            logger.error(f"Error archiving metrics: {e}")

    def register_addresses(self, addresses: Set[str]):
        """
        Register addresses for tracking
        Adds new addresses to update queue

        Args:
            addresses: Set of addresses to track
        """
        new_addresses = addresses - self.addresses_seen

        if new_addresses:
            logger.info(f"Registered {len(new_addresses)} new addresses")

            # Add to queue for immediate light fetch
            for addr in new_addresses:
                self.update_queue.append((addr, 'light', 'new'))

            self.addresses_seen.update(new_addresses)

    def check_for_updates(self):
        """
        Check which addresses need updates based on time intervals
        Adds addresses to update queue
        """
        now = datetime.now()

        for address, trader_data in self.metrics_data["traders"].items():
            # Parse last update times
            last_light = trader_data.get("last_light_update")
            last_deep = trader_data.get("last_deep_update")

            # Check if deep update needed
            if last_deep:
                last_deep_dt = datetime.fromisoformat(last_deep)
                hours_since_deep = (now - last_deep_dt).total_seconds() / 3600

                if hours_since_deep >= self.deep_interval_hours:
                    self.update_queue.append((address, 'deep', 'scheduled'))
                    continue
            else:
                # Never had deep update
                self.update_queue.append((address, 'deep', 'first'))
                continue

            # Check if light update needed
            if last_light:
                last_light_dt = datetime.fromisoformat(last_light)
                hours_since_light = (now - last_light_dt).total_seconds() / 3600

                if hours_since_light >= self.light_interval_hours:
                    self.update_queue.append((address, 'light', 'scheduled'))

    def has_pending_updates(self) -> bool:
        """Check if there are pending updates"""
        return len(self.update_queue) > 0

    def get_next_update(self) -> Optional[tuple]:
        """
        Get next address to update from queue

        Returns:
            (address, update_type, reason) or None
        """
        if self.update_queue:
            return self.update_queue.popleft()
        return None

    def fetch_light_metrics(self, address: str) -> Dict:
        """
        Fetch light metrics (fast, essential data)

        Args:
            address: Trader address

        Returns:
            Dict with metrics
        """
        logger.debug(f"Fetching LIGHT metrics for {address[:10]}...")

        metrics = {
            "address": address,
            "fetch_type": "light",
            "timestamp": datetime.now().isoformat(),
            "data": {}
        }

        # 1. User role
        try:
            role = self.hl_client.get_user_role(address)
            metrics["data"]["user_role"] = role
        except Exception as e:
            metrics["data"]["user_role"] = {"error": str(e)}
            logger.warning(f"Failed to get role for {address[:10]}: {e}")

        # 2. Clearinghouse state (positions, account value)
        try:
            state = self.hl_client.get_user_state(address)
            metrics["data"]["clearinghouse_state"] = state
        except Exception as e:
            metrics["data"]["clearinghouse_state"] = {"error": str(e)}
            logger.warning(f"Failed to get state for {address[:10]}: {e}")

        # 3. Referral info (volume)
        try:
            referral = self.hl_client.get_referral_info(address)
            metrics["data"]["referral"] = referral
        except Exception as e:
            metrics["data"]["referral"] = {"error": str(e)}
            logger.warning(f"Failed to get referral for {address[:10]}: {e}")

        # 4. Open orders (just presence)
        try:
            orders = self.hl_client.get_open_orders(address)
            metrics["data"]["open_orders_count"] = len(orders) if orders else 0
        except Exception as e:
            metrics["data"]["open_orders_count"] = {"error": str(e)}
            logger.warning(f"Failed to get orders for {address[:10]}: {e}")

        return metrics

    def fetch_deep_metrics(self, address: str) -> Dict:
        """
        Fetch deep metrics (complete data)

        Args:
            address: Trader address

        Returns:
            Dict with comprehensive metrics
        """
        logger.debug(f"Fetching DEEP metrics for {address[:10]}...")

        # Start with light metrics
        metrics = self.fetch_light_metrics(address)
        metrics["fetch_type"] = "deep"

        # 5. Portfolio (PnL history)
        try:
            portfolio = self.hl_client.get_user_portfolio(address)
            metrics["data"]["portfolio"] = portfolio
        except Exception as e:
            metrics["data"]["portfolio"] = {"error": str(e)}
            logger.warning(f"Failed to get portfolio for {address[:10]}: {e}")

        # 6. Fees
        try:
            fees = self.hl_client.get_user_fees(address)
            metrics["data"]["fees"] = fees
        except Exception as e:
            metrics["data"]["fees"] = {"error": str(e)}
            logger.warning(f"Failed to get fees for {address[:10]}: {e}")

        # 7. Fills count (don't store actual fills)
        try:
            fills = self.hl_client.get_user_fills(address)
            metrics["data"]["fills_count"] = len(fills) if fills else 0
        except Exception as e:
            metrics["data"]["fills_count"] = {"error": str(e)}
            logger.warning(f"Failed to get fills for {address[:10]}: {e}")

        # 8. TWAP fills count (don't store details)
        try:
            twap_fills = self.hl_client.get_twap_slice_fills(address)
            metrics["data"]["twap_fills_count"] = len(twap_fills) if twap_fills else 0
        except Exception as e:
            metrics["data"]["twap_fills_count"] = {"error": str(e)}
            logger.warning(f"Failed to get TWAP fills for {address[:10]}: {e}")

        # 9. Subaccounts
        try:
            subaccounts = self.hl_client.get_sub_accounts(address)
            metrics["data"]["subaccounts_count"] = len(subaccounts) if subaccounts else 0
        except Exception as e:
            metrics["data"]["subaccounts_count"] = {"error": str(e)}
            logger.warning(f"Failed to get subaccounts for {address[:10]}: {e}")

        # 10. Vault equities
        try:
            vaults = self.hl_client.get_vault_equities(address)
            metrics["data"]["vault_equities"] = vaults
        except Exception as e:
            metrics["data"]["vault_equities"] = {"error": str(e)}
            logger.warning(f"Failed to get vaults for {address[:10]}: {e}")

        return metrics

    def update_trader(self, address: str, update_type: str = 'light'):
        """
        Update metrics for a trader

        Args:
            address: Trader address
            update_type: 'light' or 'deep'
        """
        try:
            # Fetch metrics
            if update_type == 'light':
                metrics = self.fetch_light_metrics(address)
                self.metrics_data["collection_info"]["light_fetches"] += 1
            else:
                metrics = self.fetch_deep_metrics(address)
                self.metrics_data["collection_info"]["deep_fetches"] += 1

            # Store in main data structure
            if address not in self.metrics_data["traders"]:
                self.metrics_data["traders"][address] = {
                    "first_seen": datetime.now().isoformat(),
                    "last_light_update": None,
                    "last_deep_update": None,
                    "data": {}
                }

            # Update timestamps
            if update_type == 'light':
                self.metrics_data["traders"][address]["last_light_update"] = metrics["timestamp"]
            else:
                self.metrics_data["traders"][address]["last_deep_update"] = metrics["timestamp"]

            # Merge data (deep updates overwrite light data)
            self.metrics_data["traders"][address]["data"].update(metrics["data"])

            # Log summary
            data = metrics["data"]
            acct_val = data.get("clearinghouse_state", {}).get("marginSummary", {}).get("accountValue", 0)
            cum_vol = data.get("referral", {}).get("cumVlm", 0)

            logger.info(
                f"Updated {address[:10]} ({update_type}): "
                f"Value=${float(acct_val):,.0f} Vol=${float(cum_vol):,.0f}"
            )

        except Exception as e:
            logger.error(f"Error updating {address[:10]}: {e}")

    def process_single_update(self) -> bool:
        """
        Process one update from the queue

        Returns:
            True if update was processed, False if queue empty
        """
        update = self.get_next_update()
        if not update:
            return False

        address, update_type, reason = update
        logger.debug(f"Processing {update_type} update for {address[:10]} (reason: {reason})")

        self.update_trader(address, update_type)
        return True

    def should_save(self) -> bool:
        """Check if it's time to save"""
        # Save every 10 updates or every 5 minutes
        minutes_since_save = (datetime.now() - self.last_save_time).total_seconds() / 60
        return minutes_since_save >= 5

    def save_if_needed(self):
        """Save metrics if needed"""
        if self.should_save():
            self._save_metrics()

    def get_summary(self) -> Dict:
        """Get summary statistics"""
        total = len(self.metrics_data["traders"])

        if total == 0:
            return {"total_addresses": 0}

        # Count active traders
        active = 0
        total_volume = 0

        for trader_data in self.metrics_data["traders"].values():
            data = trader_data.get("data", {})
            referral = data.get("referral", {})

            if isinstance(referral, dict):
                cum_vol = float(referral.get("cumVlm", 0))
                if cum_vol > 0:
                    active += 1
                    total_volume += cum_vol

        return {
            "total_addresses": total,
            "active_traders": active,
            "total_volume": total_volume,
            "avg_volume": total_volume / active if active > 0 else 0,
            "light_fetches": self.metrics_data["collection_info"]["light_fetches"],
            "deep_fetches": self.metrics_data["collection_info"]["deep_fetches"],
            "pending_updates": len(self.update_queue)
        }