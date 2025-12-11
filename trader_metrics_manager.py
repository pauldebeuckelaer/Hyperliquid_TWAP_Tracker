#!/usr/bin/env python3
"""
Trader Metrics Manager
======================
Manages collection and storage of trader metrics with two-tier system.

REFACTORED: Now uses SQLite storage instead of JSON files.

Features:
- LIGHT updates: Quick essential metrics (every 6h)
- DEEP updates: Complete metrics (every 24h)
- Tracks spot balances and total portfolio value
- Enhanced filtering with blacklist and price sanity checks
"""
import logging
from datetime import datetime
from typing import Dict, Set, Optional, List
from collections import deque

logger = logging.getLogger(__name__)


class TraderMetricsManager:
    """
    Manages trader metrics collection with two-tier system:
    - LIGHT: Quick essential metrics (every 6h)
    - DEEP: Complete metrics (every 24h)

    Now uses SQLite for storage instead of JSON files.
    """

    def __init__(self, hl_client, storage, config: Optional[Dict] = None):
        """
        Initialize metrics manager.

        Args:
            hl_client: HyperliquidClient instance
            storage: SQLiteBackend instance
            config: Configuration dict
        """
        self.hl_client = hl_client
        self.storage = storage
        self.config = config or {}

        # Update intervals
        self.light_interval_hours = self.config.get('light_interval_hours', 6)
        self.deep_interval_hours = self.config.get('deep_interval_hours', 24)

        # Queue for pending updates
        self.update_queue = deque()

        # Tracking
        self.addresses_seen: Set[str] = set()

        # Stats counters (in-memory, reset on restart)
        self.light_fetches = 0
        self.deep_fetches = 0

        self.dust_threshold = self.config.get('dust_threshold', 5.0)

        # FILTERING CONFIGURATION
        self.blacklisted_tokens = self.config.get('blacklisted_tokens', {
            "NIGGO", "LIQD", "FUND", "STEEL", "HWTR",
            "SWAP", "BERA", "DEPIN", "GENESY",
        })

        self.whitelisted_high_value_tokens = self.config.get('whitelisted_high_value_tokens', {
            "UBTC", "BTC", "WBTC", "tBTC",
        })

        self.whitelisted_sub_dollar_tokens = self.config.get('whitelisted_sub_dollar_tokens', {
            "UFART", "PURR", "UPUMP", "UXPL", "PUMP",
            "kBONK", "BONK", "UBONK3", "PEPE", "SHIB", "FLOKI", "WIF",
        })

        # Price thresholds
        self.max_reasonable_price = self.config.get('max_reasonable_price', 50000)
        self.suspicious_dollar_range = self.config.get('suspicious_dollar_range', (0.995, 1.005))
        self.max_high_value_token_price = self.config.get('max_high_value_token_price', 150000)

        logger.info(
            f"TraderMetricsManager initialized: "
            f"light={self.light_interval_hours}h, deep={self.deep_interval_hours}h"
        )
        logger.info(
            f"Filtering: {len(self.blacklisted_tokens)} blacklisted, "
            f"{len(self.whitelisted_sub_dollar_tokens)} whitelisted sub-$1 tokens"
        )

    def register_addresses(self, addresses: Set[str]):
        """
        Register addresses for tracking.
        Adds new addresses to update queue.

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
        Check which addresses need updates based on time intervals.
        Queries SQLite for last update times and adds to queue.
        """
        now = datetime.now()

        # Get all traders from database
        traders = self.storage.get_all_trader_metrics()

        for trader in traders:
            address = trader['address']
            last_light = trader.get('last_light_update')
            last_deep = trader.get('last_deep_update')

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
        Get next address to update from queue.

        Returns:
            (address, update_type, reason) or None
        """
        if self.update_queue:
            return self.update_queue.popleft()
        return None

    def _should_include_token(self, coin: str, price: float, amount: float) -> tuple:
        """
        Determine if a token should be included based on filtering rules.

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
            return False, "blacklisted"

        # FILTER 1: Price suspiciously close to $1.00 (stale data indicator)
        min_dollar, max_dollar = self.suspicious_dollar_range
        if min_dollar <= price <= max_dollar:
            return False, f"suspicious $1.00 price ({price:.6f})"

        # FILTER 2: High prices - check whitelist first
        if price > self.max_reasonable_price:
            if coin in self.whitelisted_high_value_tokens:
                if price > self.max_high_value_token_price:
                    return False, f"price ${price:,.2f} exceeds high-value max"
                logger.debug(f"✅ {coin}: High-value whitelisted token (${price:,.2f})")
            else:
                return False, f"suspicious high price ${price:,.2f}"

        # FILTER 3: Sub-$1 tokens must be whitelisted
        if price < 1.0 and coin not in self.whitelisted_sub_dollar_tokens:
            return False, f"sub-$1 not whitelisted (${price:.6f})"

        # FILTER 4: Dust threshold
        if token_value <= self.dust_threshold:
            return False, f"dust (${token_value:.2f})"

        return True, "passed all filters"

    def fetch_light_metrics(self, address: str) -> Dict:
        """
        Fetch light metrics (fast, essential data).
        Includes spot balance, vault holdings, and total portfolio value.

        Args:
            address: Trader address

        Returns:
            Dict with metrics
        """
        logger.debug(f"Fetching LIGHT metrics for {address}...")

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
            logger.warning(f"Failed to get role for {address}: {e}")

        # 2. Clearinghouse state (positions, account value)
        try:
            state = self.hl_client.get_user_state(address)
            margin_summary = state.get("marginSummary", {})
            perp_value = float(margin_summary.get("accountValue", 0))

            # Get spot balance with filtering
            spot_value = 0
            spot_balances_detail = []
            dust_value = 0
            dust_count = 0
            filtered_tokens = []

            try:
                spot_state = self.hl_client.get_spot_clearinghouse_state(address)
                if spot_state:
                    balances = spot_state.get("balances", [])

                    for balance in balances:
                        total = float(balance.get("total", 0))
                        coin = balance.get("coin", "")

                        if total == 0:
                            continue

                        # Stablecoins are 1:1 USD
                        if coin in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0", "USDE", "USDH"]:
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
                            price = self.hl_client.get_token_price(coin)

                            if price:
                                should_include, reason = self._should_include_token(coin, price, total)

                                if should_include:
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
                                    logger.debug(f"✅ {coin}: Included - ${token_value:.2f}")
                                else:
                                    token_value = total * price
                                    filtered_tokens.append({
                                        "coin": coin,
                                        "reason": reason,
                                        "price": price,
                                        "amount": total,
                                        "value": token_value
                                    })
                                    if token_value <= self.dust_threshold:
                                        dust_value += token_value
                                    dust_count += 1
                            else:
                                if total > 0.01:
                                    logger.debug(f"🚫 {coin}: No price available")
                                dust_count += 1

                    if spot_value > 1000:
                        logger.debug(f"💰 Spot breakdown for {address}: Total=${spot_value:,.2f}")

            except Exception as e:
                logger.warning(f"Failed to get spot balance for {address}: {e}")

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

            except Exception as e:
                logger.warning(f"Failed to get vaults for {address}: {e}")

            # Calculate total portfolio value
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

            # Leverage ratio
            pos_val = metrics["data"]["account"]["position_value"]
            metrics["data"]["account"]["leverage_ratio"] = (
                round(pos_val / total_portfolio, 2) if total_portfolio > 0 else 0
            )

            # Parse positions
            metrics["data"]["positions"] = []
            for pos_data in state.get("assetPositions", []):
                position = pos_data.get("position", {})
                size = float(position.get("szi", 0))

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
            logger.warning(f"Failed to get state for {address}: {e}")

        # 3. Referral info (cumulative volume)
        try:
            referral = self.hl_client.get_referral_info(address)
            metrics["data"]["cumulative_volume"] = float(referral.get("cumVlm", 0))
        except Exception as e:
            metrics["data"]["cumulative_volume"] = 0
            logger.warning(f"Failed to get referral for {address}: {e}")

        # 4. Open orders count
        try:
            orders = self.hl_client.get_open_orders(address)
            metrics["data"]["open_orders_count"] = len(orders) if orders else 0
        except Exception as e:
            metrics["data"]["open_orders_count"] = 0
            logger.warning(f"Failed to get orders for {address}: {e}")

        return metrics

    def fetch_deep_metrics(self, address: str) -> Dict:
        """
        Fetch deep metrics (complete data).

        Args:
            address: Trader address

        Returns:
            Dict with comprehensive metrics
        """
        logger.debug(f"Fetching DEEP metrics for {address}...")

        # Start with light metrics
        metrics = self.fetch_light_metrics(address)
        metrics["fetch_type"] = "deep"

        # 5. Fills count
        try:
            fills = self.hl_client.get_user_fills(address)
            metrics["data"]["fills_count"] = len(fills) if fills else 0
        except Exception as e:
            metrics["data"]["fills_count"] = 0
            logger.warning(f"Failed to get fills for {address}: {e}")

        # 6. TWAP fills count
        try:
            twap_fills = self.hl_client.get_twap_slice_fills(address)
            metrics["data"]["twap_fills_count"] = len(twap_fills) if twap_fills else 0
        except Exception as e:
            metrics["data"]["twap_fills_count"] = 0
            logger.warning(f"Failed to get TWAP fills for {address}: {e}")

        # 7. Subaccounts
        try:
            subaccounts = self.hl_client.get_sub_accounts(address)
            metrics["data"]["subaccounts_count"] = len(subaccounts) if subaccounts else 0
        except Exception as e:
            metrics["data"]["subaccounts_count"] = 0
            logger.warning(f"Failed to get subaccounts for {address}: {e}")

        return metrics

    def update_trader(self, address: str, update_type: str = 'light'):
        """
        Update metrics for a trader and save to SQLite.

        Args:
            address: Trader address
            update_type: 'light' or 'deep'
        """
        try:
            # Fetch metrics
            if update_type == 'light':
                metrics = self.fetch_light_metrics(address)
                self.light_fetches += 1
            else:
                metrics = self.fetch_deep_metrics(address)
                self.deep_fetches += 1

            # Save to SQLite
            self.storage.save_trader_metrics(address, metrics["data"], update_type)

            # Log summary
            data = metrics["data"]
            acct = data.get("account", {})
            acct_val = acct.get("total_portfolio_value", acct.get("value", 0))
            num_pos = acct.get("num_positions", 0)
            leverage = acct.get("leverage_ratio", 0)
            cum_vol = data.get("cumulative_volume", 0)

            logger.debug(
                f"Updated {address} ({update_type}): "
                f"Value=${float(acct_val):,.0f} "
                f"Positions={num_pos} "
                f"Leverage={leverage:.1f}x "
                f"Vol=${float(cum_vol):,.0f}"
            )

        except Exception as e:
            logger.error(f"Error updating {address}: {e}")

    def process_single_update(self) -> bool:
        """
        Process one update from the queue.

        Returns:
            True if update was processed, False if queue empty
        """
        update = self.get_next_update()
        if not update:
            return False

        address, update_type, reason = update
        logger.debug(f"Processing {update_type} update for {address} (reason: {reason})")

        self.update_trader(address, update_type)
        return True

    def get_summary(self) -> Dict:
        """
        Get summary statistics from SQLite.

        Returns:
            Dict with summary stats
        """
        traders = self.storage.get_all_trader_metrics()
        total = len(traders)

        if total == 0:
            return {"total_addresses": 0}

        active = 0
        with_positions = 0
        total_volume = 0
        total_account_value = 0

        for trader in traders:
            cum_vol = trader.get('cumulative_volume', 0)
            if cum_vol > 0:
                active += 1
                total_volume += cum_vol

            if trader.get('num_positions', 0) > 0:
                with_positions += 1

            total_account_value += trader.get('total_portfolio_value', 0)

        return {
            "total_addresses": total,
            "active_traders": active,
            "traders_with_positions": with_positions,
            "total_volume": round(total_volume, 2),
            "total_account_value": round(total_account_value, 2),
            "avg_volume": round(total_volume / active, 2) if active > 0 else 0,
            "avg_account_value": round(total_account_value / total, 2) if total > 0 else 0,
            "light_fetches": self.light_fetches,
            "deep_fetches": self.deep_fetches,
            "pending_updates": len(self.update_queue)
        }

    def get_trader(self, address: str) -> Optional[Dict]:
        """
        Get metrics for a single trader.

        Args:
            address: Trader address

        Returns:
            Dict with trader metrics or None
        """
        return self.storage.get_trader_metrics(address)

    def get_all_traders(self, limit: int = None, order_by: str = 'total_portfolio_value') -> List[Dict]:
        """
        Get all trader metrics.

        Args:
            limit: Max number of traders
            order_by: Column to sort by

        Returns:
            List of trader dicts
        """
        return self.storage.get_all_trader_metrics(limit=limit, order_by=order_by)

    def get_positions_by_coin(self, coin: str, limit: int = 100) -> List[Dict]:
        """
        Get all positions for a specific coin.

        Args:
            coin: Coin symbol (e.g., 'HYPE')
            limit: Max results

        Returns:
            List of positions
        """
        return self.storage.get_positions_by_coin(coin, limit)

    def get_spot_holders_by_coin(self, coin: str, limit: int = 100) -> List[Dict]:
        """
        Get all spot holders for a specific coin.

        Args:
            coin: Coin symbol
            limit: Max results

        Returns:
            List of spot balances
        """
        return self.storage.get_spot_holders_by_coin(coin, limit)