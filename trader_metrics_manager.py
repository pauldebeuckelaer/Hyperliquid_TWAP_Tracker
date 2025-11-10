#!/usr/bin/env python3
"""
Trader Metrics Manager
Manages collection and storage of trader metrics with two-tier system
UPDATED: Now tracks spot balances and total portfolio value
UPDATED: Enhanced filtering with blacklist and price sanity checks
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

        self.dust_threshold = self.config.get('dust_threshold', 5.0)

        # FILTERING CONFIGURATION
        # Blacklist for known problematic tokens (add more as you discover them)
        self.blacklisted_tokens = self.config.get('blacklisted_tokens', {
            "NIGGO",
            "LIQD",
            "FUND",
            # Add more tokens here as needed
        })

        # Whitelist for trusted sub-$1 tokens
        self.whitelisted_sub_dollar_tokens = self.config.get('whitelisted_sub_dollar_tokens', {
            "UFART", "PURR", "UPUMP", "UXPL", "PUMP",
            "kBONK", "BONK", "PEPE", "SHIB", "FLOKI", "WIF", "UBTC"
        })

        # Price thresholds
        self.max_reasonable_price = self.config.get('max_reasonable_price', 50000)  # $50k
        self.suspicious_dollar_range = self.config.get('suspicious_dollar_range', (0.995, 1.005))

        logger.info(
            f"TraderMetricsManager initialized: "
            f"light={self.light_interval_hours}h, deep={self.deep_interval_hours}h"
        )
        logger.info(f"Filtering: {len(self.blacklisted_tokens)} blacklisted, "
                    f"{len(self.whitelisted_sub_dollar_tokens)} whitelisted sub-$1 tokens")

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

    def _should_include_token(self, coin: str, price: float, amount: float) -> tuple[bool, str]:
        """
        Determine if a token should be included based on filtering rules

        Args:
            coin: Token name
            price: Token price in USD
            amount: Token amount held

        Returns:
            (should_include, reason) tuple
        """
        token_value = amount * price

        # FILTER 0: Blacklist check (highest priority)
        if coin in self.blacklisted_tokens:
            return False, f"blacklisted"

        # FILTER 1: Price suspiciously close to $1.00 (stale data indicator)
        min_dollar, max_dollar = self.suspicious_dollar_range
        if min_dollar <= price <= max_dollar:
            return False, f"suspicious $1.00 price ({price:.6f})"

        # FILTER 2: Absurdly high prices (likely illiquid/manipulated)
        if price > self.max_reasonable_price:
            return False, f"suspicious high price ${price:,.2f} (>${self.max_reasonable_price:,.0f})"

        # FILTER 3: Sub-$1 tokens must be whitelisted
        if price < 1.0 and coin not in self.whitelisted_sub_dollar_tokens:
            return False, f"sub-$1 not whitelisted (${price:.6f})"

        # FILTER 4: Dust threshold
        if token_value <= self.dust_threshold:
            return False, f"dust (${token_value:.2f} < ${self.dust_threshold})"

        # All filters passed!
        return True, "passed all filters"

    def fetch_light_metrics(self, address: str) -> Dict:
        """
        Fetch light metrics (fast, essential data)
        UPDATED: Now includes spot balance, vault holdings, and total portfolio value
        UPDATED: Enhanced filtering with blacklist and price sanity checks

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

        # 2. Clearinghouse state (positions, account value) - UPDATED
        try:
            state = self.hl_client.get_user_state(address)

            # Store simplified account summary
            margin_summary = state.get("marginSummary", {})
            perp_value = float(margin_summary.get("accountValue", 0))

            # Get spot balance with ENHANCED FILTERING
            spot_value = 0
            spot_balances_detail = []
            dust_value = 0
            dust_count = 0
            filtered_tokens = []  # Track what we filtered and why

            try:
                spot_state = self.hl_client.get_spot_clearinghouse_state(address)
                if spot_state:
                    balances = spot_state.get("balances", [])

                    for balance in balances:
                        total = float(balance.get("total", 0))
                        coin = balance.get("coin", "")

                        if total == 0:
                            continue

                        token_value = 0

                        # Stablecoins are 1:1 USD (skip filtering)
                        if coin in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0", "USDE"]:
                            token_value = total
                            if token_value > self.dust_threshold:
                                spot_value += total
                                spot_balances_detail.append({
                                    "coin": coin,
                                    "amount": total,
                                    "value": token_value,
                                    "price_source": "stablecoin_1:1"
                                })
                            else:
                                dust_value += token_value
                                dust_count += 1
                        else:
                            # Get price using optimized lookup
                            price = self.hl_client.get_token_price(coin)

                            if price:
                                # Apply filtering rules
                                should_include, reason = self._should_include_token(coin, price, total)

                                if should_include:
                                    # Token passed all filters - include it
                                    token_value = total * price
                                    spot_value += token_value

                                    price_source = "bridged_asset" if coin.startswith('U') else "all_mids"
                                    spot_balances_detail.append({
                                        "coin": coin,
                                        "amount": total,
                                        "value": token_value,
                                        "price": price,
                                        "price_source": price_source
                                    })
                                    logger.info(f"✅ {coin}: Included - ${token_value:.2f} (price: ${price:.6f})")
                                else:
                                    # Token filtered out
                                    token_value = total * price

                                    # Track for debugging
                                    filtered_tokens.append({
                                        "coin": coin,
                                        "reason": reason,
                                        "price": price,
                                        "amount": total,
                                        "value": token_value
                                    })

                                    if token_value > self.dust_threshold:
                                        # Significant value filtered - log as INFO
                                        logger.info(
                                            f"🚫 {coin}: Filtered - {reason} "
                                            f"(amount: {total:.6f}, value: ${token_value:.2f})"
                                        )
                                    else:
                                        # Dust filtered - log as DEBUG
                                        logger.debug(
                                            f"🚫 {coin}: Filtered - {reason} "
                                            f"(amount: {total:.6f}, value: ${token_value:.2f})"
                                        )
                                        dust_value += token_value

                                    dust_count += 1
                            else:
                                # No price available
                                if total > 0.01:
                                    logger.debug(
                                        f"🚫 {coin}: Filtered out - no price available (amount: {total:.6f}) - "
                                        f"likely illiquid/delisted on {address[:10]}"
                                    )
                                dust_count += 1

                    # Log spot balance breakdown for significant balances
                    if spot_value > 1000:
                        logger.debug(f"💰 Spot breakdown for {address[:10]}: Total=${spot_value:,.2f}")
                        for bal in spot_balances_detail:
                            if bal['value'] > 0:
                                logger.debug(
                                    f"   {bal['coin']}: {bal['amount']:,.6f} = ${bal['value']:,.2f}"
                                )

                    # Log filtering summary if significant tokens were filtered
                    if filtered_tokens:
                        total_filtered_value = sum(t['value'] for t in filtered_tokens)
                        if total_filtered_value > 100:  # Only log if >$100 filtered
                            logger.info(
                                f"🔍 {address[:10]}: Filtered {len(filtered_tokens)} tokens "
                                f"worth ${total_filtered_value:,.2f}"
                            )

            except Exception as e:
                logger.warning(f"Failed to get spot balance for {address[:10]}: {e}")
                spot_value = 0
                spot_balances_detail = []

            # Get vault holdings
            vault_value = 0
            vault_details = []
            try:
                vault_equities = self.hl_client.get_user_vault_equities(address)

                if vault_equities:
                    for vault_eq in vault_equities:
                        vault_addr = vault_eq["vaultAddress"]
                        equity = float(vault_eq["equity"])

                        vault_value += equity
                        vault_details.append({
                            "vault_address": vault_addr,
                            "equity": equity
                        })

                    # Log vault holdings for significant amounts
                    if vault_value > 1000:
                        logger.debug(f"🏦 Vault holdings for {address[:10]}: Total=${vault_value:,.2f}")
                        for vault in vault_details:
                            logger.debug(
                                f"   {vault['vault_address'][:10]}...: ${vault['equity']:,.2f}"
                            )

            except Exception as e:
                logger.warning(f"Failed to get vaults for {address[:10]}: {e}")
                vault_value = 0
                vault_details = []

            # Calculate total portfolio value (perps + spot + vaults)
            total_portfolio = perp_value + spot_value + vault_value

            metrics["data"]["account"] = {
                "value": perp_value,
                "spot_value": spot_value,
                "spot_balances_detail": spot_balances_detail,
                "dust_value": dust_value,
                "dust_count": dust_count,
                "vault_value": vault_value,
                "vault_details": vault_details,
                "total_portfolio_value": total_portfolio,
                "position_value": float(margin_summary.get("totalNtlPos", 0)),
                "margin_used": float(margin_summary.get("totalMarginUsed", 0)),
                "withdrawable": float(state.get("withdrawable", 0))
            }

            # Calculate leverage ratio - uses total portfolio
            pos_val = metrics["data"]["account"]["position_value"]
            metrics["data"]["account"]["leverage_ratio"] = (
                round(pos_val / total_portfolio, 2) if total_portfolio > 0 else 0
            )

            # Parse positions (includes liquidation prices from API!)
            metrics["data"]["positions"] = []
            for pos_data in state.get("assetPositions", []):
                position = pos_data.get("position", {})
                size = float(position.get("szi", 0))

                # Only store active positions
                if size != 0:
                    metrics["data"]["positions"].append({
                        "coin": position.get("coin", ""),
                        "size": size,
                        "side": "LONG" if size > 0 else "SHORT",
                        "entry_price": float(position.get("entryPx", 0)),
                        "liquidation_price": float(position.get("liquidationPx") or 0),
                        "leverage": float(position.get("leverage", {}).get("value", 1)),
                        "margin_used": float(position.get("marginUsed", 0)),
                        "unrealized_pnl": float(position.get("unrealizedPnl", 0))
                    })

            metrics["data"]["account"]["num_positions"] = len(metrics["data"]["positions"])

        except Exception as e:
            metrics["data"]["account"] = {"error": str(e)}
            metrics["data"]["positions"] = []
            logger.warning(f"Failed to get state for {address[:10]}: {e}")

        # 3. Referral info (cumulative volume)
        try:
            referral = self.hl_client.get_referral_info(address)
            metrics["data"]["cumulative_volume"] = float(referral.get("cumVlm", 0))
        except Exception as e:
            metrics["data"]["cumulative_volume"] = 0
            logger.warning(f"Failed to get referral for {address[:10]}: {e}")

        # 4. Open orders (just count)
        try:
            orders = self.hl_client.get_open_orders(address)
            metrics["data"]["open_orders_count"] = len(orders) if orders else 0
        except Exception as e:
            metrics["data"]["open_orders_count"] = 0
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

        # Add deep-only metrics (just counts, no heavy data)

        # 5. Fills count (don't store actual fills)
        try:
            fills = self.hl_client.get_user_fills(address)
            metrics["data"]["fills_count"] = len(fills) if fills else 0
        except Exception as e:
            metrics["data"]["fills_count"] = 0
            logger.warning(f"Failed to get fills for {address[:10]}: {e}")

        # 6. TWAP fills count (don't store details)
        try:
            twap_fills = self.hl_client.get_twap_slice_fills(address)
            metrics["data"]["twap_fills_count"] = len(twap_fills) if twap_fills else 0
        except Exception as e:
            metrics["data"]["twap_fills_count"] = 0
            logger.warning(f"Failed to get TWAP fills for {address[:10]}: {e}")

        # 7. Subaccounts
        try:
            subaccounts = self.hl_client.get_sub_accounts(address)
            metrics["data"]["subaccounts_count"] = len(subaccounts) if subaccounts else 0
        except Exception as e:
            metrics["data"]["subaccounts_count"] = 0
            logger.warning(f"Failed to get subaccounts for {address[:10]}: {e}")

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

            # Log summary (DEBUG level - won't show in console)
            data = metrics["data"]
            acct = data.get("account", {})
            acct_val = acct.get("total_portfolio_value", acct.get("value", 0))
            num_pos = acct.get("num_positions", 0)
            leverage = acct.get("leverage_ratio", 0)
            cum_vol = data.get("cumulative_volume", 0)

            logger.debug(
                f"Updated {address[:10]} ({update_type}): "
                f"Value=${float(acct_val):,.0f} "
                f"Positions={num_pos} "
                f"Leverage={leverage:.1f}x "
                f"Vol=${float(cum_vol):,.0f}"
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
        # Save every 2 minutes
        minutes_since_save = (datetime.now() - self.last_save_time).total_seconds() / 60
        return minutes_since_save >= 2

    def save_if_needed(self):
        """Save metrics if needed"""
        if self.should_save():
            self._save_metrics()

    def get_summary(self) -> Dict:
        """Get summary statistics - uses total portfolio value"""
        total = len(self.metrics_data["traders"])

        if total == 0:
            return {"total_addresses": 0}

        # Count active traders
        active = 0
        with_positions = 0
        total_volume = 0
        total_account_value = 0

        for trader_data in self.metrics_data["traders"].values():
            data = trader_data.get("data", {})

            # Volume
            cum_vol = data.get("cumulative_volume", 0)
            if cum_vol > 0:
                active += 1
                total_volume += cum_vol

            # Positions
            account = data.get("account", {})
            if account.get("num_positions", 0) > 0:
                with_positions += 1

            # Account value - uses total_portfolio_value if available
            total_account_value += account.get("total_portfolio_value", account.get("value", 0))

        return {
            "total_addresses": total,
            "active_traders": active,
            "traders_with_positions": with_positions,
            "total_volume": round(total_volume, 2),
            "total_account_value": round(total_account_value, 2),
            "avg_volume": round(total_volume / active, 2) if active > 0 else 0,
            "avg_account_value": round(total_account_value / total, 2),
            "light_fetches": self.metrics_data["collection_info"]["light_fetches"],
            "deep_fetches": self.metrics_data["collection_info"]["deep_fetches"],
            "pending_updates": len(self.update_queue)
        }