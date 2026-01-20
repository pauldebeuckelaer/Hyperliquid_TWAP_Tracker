#!/usr/bin/env python3
"""
Whale Metrics Manager
=====================
Manages hourly snapshots of whale portfolios (>$50K).

Features:
- Hourly snapshots of portfolio composition
- Tracks perp positions, spot balances, vault holdings
- $50K minimum threshold (configurable)
- ~1000 address target capacity
- Liquidation price tracking
- Token filtering with blacklist and price sanity checks
"""
import logging
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, Set, Optional, List, cast

logger = logging.getLogger(__name__)


class WhaleMetricsManager:
    """
    Manages whale portfolio snapshots.

    Takes hourly snapshots of whale portfolios including:
    - Perp positions with liquidation prices
    - Spot balances
    - Vault holdings
    """

    def __init__(self, hl_client, storage, config: Optional[Dict] = None):
        """
        Initialize whale metrics manager.

        Args:
            hl_client: HyperliquidClient instance
            storage: SQLiteBackend instance
            config: Configuration dict
        """
        self.hl_client = hl_client
        self.storage = storage
        self.config = config or {}

        # Portfolio threshold (minimum to track)
        self.min_portfolio_value = self.config.get('min_portfolio_value', 50000)

        # Dust threshold for spot balances
        self.dust_threshold = self.config.get('dust_threshold', 5.0)

        # Stats counters
        self.snapshots_taken = 0
        self.addresses_processed = 0
        self.errors_count = 0

        # FILTERING CONFIGURATION
        self.blacklisted_tokens = self.config.get('blacklisted_tokens', {
            # Original blacklist
            "NIGGO", "LIQD", "FUND", "STEEL", "HWTR",
            "SWAP", "BERA", "DEPIN", "GENESY", "TILT",
            # New additions
            "RANK", "PURRO", "HOLD", "RAT", "MANLET", "WHYPI", "PENIS", "RISK", "HAR"
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
            f"WhaleMetricsManager initialized: "
            f"min_portfolio=${self.min_portfolio_value:,}, "
            f"blacklist={len(self.blacklisted_tokens)} tokens"
        )

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

    def register_address(self, address: str) -> bool:
        """
        Register a new address for potential tracking.
        Address will be tracked if portfolio >= threshold.

        Args:
            address: Wallet address

        Returns:
            True if added to whale tracking, False otherwise
        """
        # Check if already tracked
        existing = self.storage.get_active_whale_addresses()
        if address in existing:
            return False

        # Fetch portfolio value to check threshold
        try:
            portfolio_value = self._get_portfolio_value(address)

            if portfolio_value >= self.min_portfolio_value:
                self.storage.add_whale_address(address)
                logger.info(f"Added whale: {address[:10]}... (${portfolio_value:,.0f})")
                return True
            else:
                logger.debug(f"Below threshold: {address[:10]}... (${portfolio_value:,.0f})")
                return False

        except Exception as e:
            logger.warning(f"Error checking address {address[:10]}...: {e}")
            return False

    def register_addresses(self, addresses: Set[str]):
        """
        Register multiple addresses for tracking.
        Only adds addresses with portfolio >= $50K threshold.
        """
        # Get existing addresses to skip
        existing = set(self.storage.get_active_whale_addresses())
        all_whales = self.storage.get_all_whale_addresses()
        existing.update(w['address'] for w in all_whales)

        new_addresses = addresses - existing

        if not new_addresses:
            return

        logger.info(f"Checking {len(new_addresses)} new addresses for whale status...")

        added = 0
        for addr in new_addresses:
            try:
                portfolio_value = self._get_portfolio_value(addr)

                if portfolio_value >= self.min_portfolio_value:
                    self.storage.add_whale_address(addr)
                    logger.info(f"Added whale: {addr[:10]}... (${portfolio_value:,.0f})")
                    added += 1
            except Exception as e:
                logger.warning(f"Error checking {addr[:10]}...: {e}")

        if added > 0:
            logger.info(f"Added {added} new whales from {len(new_addresses)} candidates")

    def _get_portfolio_value(self, address: str) -> float:
        """
        Get total portfolio value for an address (quick check).

        Args:
            address: Wallet address

        Returns:
            Total portfolio value in USD
        """
        total = 0.0

        # Perp value
        try:
            state = self.hl_client.get_user_state(address)
            margin_summary = state.get("marginSummary", {})
            total += float(margin_summary.get("accountValue", 0))
        except Exception:
            pass

        # Spot value (simplified - just check if significant)
        try:
            spot_state = self.hl_client.get_spot_clearinghouse_state(address)
            if spot_state:
                for balance in spot_state.get("balances", []):
                    bal_total = float(balance.get("total", 0))
                    coin = balance.get("coin", "")

                    if bal_total == 0:
                        continue

                    # Stablecoins
                    if coin in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0", "USDE", "USDH"]:
                        total += bal_total
                    else:
                        price = self.hl_client.get_token_price(coin)
                        if price:
                            should_include, _ = self._should_include_token(coin, price, bal_total)
                            if should_include:
                                total += bal_total * price
        except Exception:
            pass

        # Vault value
        try:
            vault_equities = self.hl_client.get_user_vault_equities(address)
            if vault_equities:
                for vault_eq in vault_equities:
                    total += float(vault_eq.get("equity", 0))
        except Exception:
            pass

        return total

    def fetch_whale_data(self, address: str) -> Optional[Dict]:
        """
        Fetch complete portfolio data for a whale.

        Args:
            address: Wallet address

        Returns:
            Dict with portfolio_data, positions, spot_balances, vaults
            or None if error
        """
        try:
            portfolio_data = {
                "perp_value": 0,
                "spot_value": 0,
                "vault_value": 0,
                "total_portfolio_value": 0,
                "margin_used": 0,
                "leverage_ratio": 0,
                "num_positions": 0,
            }
            positions = []
            spot_balances = []
            vaults = []

            # 1. Perp positions and account state
            try:
                state = self.hl_client.get_user_state(address)
                margin_summary = state.get("marginSummary", {})

                portfolio_data["perp_value"] = float(margin_summary.get("accountValue", 0))
                portfolio_data["margin_used"] = float(margin_summary.get("totalMarginUsed", 0))

                # Parse positions
                for pos_data in state.get("assetPositions", []):
                    position = pos_data.get("position", {})
                    size = float(position.get("szi", 0))

                    if size != 0:
                        positions.append({
                            "coin": position.get("coin", ""),
                            "size": size,
                            "side": "LONG" if size > 0 else "SHORT",
                            "entry_price": float(position.get("entryPx", 0)),
                            "liquidation_price": float(position.get("liquidationPx") or 0),
                            "leverage": float(position.get("leverage", {}).get("value", 1)),
                            "margin_used": float(position.get("marginUsed", 0)),
                            "unrealized_pnl": float(position.get("unrealizedPnl", 0))
                        })

                portfolio_data["num_positions"] = len(positions)

            except Exception as e:
                logger.warning(f"Failed to get perp state for {address[:10]}...: {e}")

            # 2. Spot balances
            try:
                spot_state = self.hl_client.get_spot_clearinghouse_state(address)
                if spot_state:
                    for balance in spot_state.get("balances", []):
                        bal_total = float(balance.get("total", 0))
                        coin = balance.get("coin", "")

                        if bal_total == 0:
                            continue

                        # Stablecoins
                        if coin in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0", "USDE", "USDH"]:
                            if bal_total > self.dust_threshold:
                                portfolio_data["spot_value"] += bal_total
                                spot_balances.append({
                                    "coin": coin,
                                    "amount": bal_total,
                                    "value": bal_total,
                                    "price": 1.0
                                })
                        else:
                            price = self.hl_client.get_token_price(coin)
                            if price:
                                should_include, reason = self._should_include_token(coin, price, bal_total)
                                if should_include:
                                    token_value = bal_total * price
                                    portfolio_data["spot_value"] += token_value
                                    spot_balances.append({
                                        "coin": coin,
                                        "amount": bal_total,
                                        "value": token_value,
                                        "price": price
                                    })

            except Exception as e:
                logger.warning(f"Failed to get spot state for {address[:10]}...: {e}")

            # 3. Vault holdings
            try:
                vault_equities = self.hl_client.get_user_vault_equities(address)
                if vault_equities:
                    for vault_eq in vault_equities:
                        equity = float(vault_eq.get("equity", 0))
                        if equity > 0:
                            portfolio_data["vault_value"] += equity
                            vaults.append({
                                "vault_address": vault_eq.get("vaultAddress", ""),
                                "value": equity
                            })

            except Exception as e:
                logger.warning(f"Failed to get vaults for {address[:10]}...: {e}")

            # Calculate totals
            portfolio_data["total_portfolio_value"] = (
                    portfolio_data["perp_value"] +
                    portfolio_data["spot_value"] +
                    portfolio_data["vault_value"]
            )

            # Leverage ratio
            if portfolio_data["total_portfolio_value"] > 0:
                position_value = sum(abs(p["size"] * p["entry_price"]) for p in positions)
                portfolio_data["leverage_ratio"] = round(
                    position_value / portfolio_data["total_portfolio_value"], 2
                )

            return {
                "portfolio_data": portfolio_data,
                "positions": positions,
                "spot_balances": spot_balances,
                "vaults": vaults
            }

        except Exception as e:
            logger.error(f"Error fetching data for {address[:10]}...: {e}")
            self.errors_count += 1
            return None

    def take_snapshot(self, address: str) -> bool:
        """
        Take a snapshot of a whale's portfolio.

        Args:
            address: Wallet address

        Returns:
            True if snapshot saved, False otherwise
        """
        data = self.fetch_whale_data(address)
        if not data:
            return False

        portfolio_value = data["portfolio_data"]["total_portfolio_value"]
        perp_value = data["portfolio_data"]["perp_value"]
        spot_value = data["portfolio_data"]["spot_value"]
        vault_value = data["portfolio_data"]["vault_value"]

        # Check if still above threshold
        if portfolio_value < self.min_portfolio_value:
            # SAFETY: All zeros means API failed, not empty portfolio
            if perp_value == 0 and spot_value == 0 and vault_value == 0:
                logger.warning(f"Skipping deactivation for {address[:10]}... - API returned all zeros")
                return False

            self.storage.update_whale_status(address, is_active=False)
            logger.info(f"Whale dropped below threshold: {address[:10]}... (${portfolio_value:,.0f})")
            return False

        # Ensure whale is marked active
        self.storage.update_whale_status(address, is_active=True)

        # Save snapshot
        snapshot_time = datetime.now().isoformat()

        self.storage.save_whale_snapshot(
            address=address,
            snapshot_time=snapshot_time,
            portfolio_data=data["portfolio_data"],
            positions=data["positions"],
            spot_balances=data["spot_balances"],
            vaults=data["vaults"]
        )

        self.snapshots_taken += 1
        return True

    def run_hourly_snapshot(self) -> Dict:
        """
        Run hourly snapshot for all active whales.

        Returns:
            Dict with summary stats
        """
        start_time = datetime.now()
        logger.info("Starting hourly whale snapshot...")

        active_whales = self.storage.get_active_whale_addresses()
        total = len(active_whales)

        if total == 0:
            logger.warning("No active whales to snapshot")
            return {"total": 0, "success": 0, "failed": 0, "dropped": 0}

        logger.info(f"Snapshotting {total} active whales...")

        success = 0
        failed = 0
        dropped = 0

        for i, address in enumerate(active_whales):
            try:
                result = self.take_snapshot(address)
                if result:
                    success += 1
                else:
                    # Either error or dropped below threshold
                    # Check if dropped
                    if address not in self.storage.get_active_whale_addresses():
                        dropped += 1
                    else:
                        failed += 1

                self.addresses_processed += 1

                # Progress logging every 100 addresses
                if (i + 1) % 100 == 0:
                    logger.info(f"Progress: {i + 1}/{total} ({success} success, {failed} failed, {dropped} dropped)")

            except Exception as e:
                logger.error(f"Error snapshotting {address[:10]}...: {e}")
                failed += 1
                self.errors_count += 1

        elapsed = (datetime.now() - start_time).total_seconds()

        logger.info(
            f"Hourly snapshot complete: "
            f"{success}/{total} success, {failed} failed, {dropped} dropped "
            f"({elapsed:.1f}s)"
        )

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "dropped": dropped,
            "elapsed_seconds": elapsed
        }

    def reactivate_whale(self, address: str) -> bool:
        """
        Check if an inactive whale should be reactivated.

        Args:
            address: Wallet address

        Returns:
            True if reactivated, False otherwise
        """
        portfolio_value = self._get_portfolio_value(address)

        if portfolio_value >= self.min_portfolio_value:
            self.storage.update_whale_status(address, is_active=True)
            logger.info(f"Reactivated whale: {address[:10]}... (${portfolio_value:,.0f})")
            return True

        return False

    def check_inactive_whales(self) -> int:
        """
        Check all inactive whales and reactivate any above threshold.

        Returns:
            Number of whales reactivated
        """
        all_whales = self.storage.get_all_whale_addresses()
        inactive = [w for w in all_whales if not w['is_active']]

        if not inactive:
            return 0

        logger.info(f"Checking {len(inactive)} inactive whales...")

        reactivated = 0
        for whale in inactive:
            if self.reactivate_whale(whale['address']):
                reactivated += 1

        if reactivated > 0:
            logger.info(f"Reactivated {reactivated} whales")

        return reactivated

    # =============================================================================
    # ASYNC METHODS
    # =============================================================================

    async def _get_portfolio_value_async(
            self,
            address: str,
            session: aiohttp.ClientSession
    ) -> float:
        """
        Async version of _get_portfolio_value.
        Fetches perp, spot, and vault data in PARALLEL.
        """
        total = 0.0

        # Fetch all 3 data sources in parallel
        state_result, spot_result, vault_result = await asyncio.gather(
            self.hl_client.get_user_state_async(address, session),
            self.hl_client.get_spot_clearinghouse_state_async(address, session),
            self.hl_client.get_user_vault_equities_async(address, session),
            return_exceptions=True  # Don't fail if one call fails
        )

        # Process perp value
        if state_result and not isinstance(state_result, Exception):
            try:
                state = cast(Dict, state_result)
                margin_summary = state.get("marginSummary", {})
                total += float(margin_summary.get("accountValue", 0))
            except Exception:
                pass

        # Process spot value
        if spot_result and not isinstance(spot_result, Exception):
            try:
                spot_state = cast(Dict, spot_result)
                for balance in spot_state.get("balances", []):
                    bal_total = float(balance.get("total", 0))
                    coin = balance.get("coin", "")

                    if bal_total == 0:
                        continue

                    # Stablecoins
                    if coin in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0", "USDE", "USDH"]:
                        total += bal_total
                    else:
                        price = self.hl_client.get_token_price(coin)
                        if price:
                            should_include, _ = self._should_include_token(coin, price, bal_total)
                            if should_include:
                                total += bal_total * price
            except Exception:
                pass

        # Process vault value
        if vault_result and not isinstance(vault_result, Exception):
            try:
                vault_equities = cast(List, vault_result)
                for vault_eq in vault_equities:
                    total += float(vault_eq.get("equity", 0))
            except Exception:
                pass

        return total

    async def register_address_async(
            self,
            address: str,
            session: aiohttp.ClientSession
    ) -> bool:
        """
        Async version of register_address.
        """
        # Check if already tracked
        existing = self.storage.get_active_whale_addresses()
        if address in existing:
            return False

        try:
            portfolio_value = await self._get_portfolio_value_async(address, session)

            if portfolio_value >= self.min_portfolio_value:
                self.storage.add_whale_address(address)
                logger.info(f"Added whale: {address[:10]}... (${portfolio_value:,.0f})")
                return True
            else:
                logger.debug(f"Below threshold: {address[:10]}... (${portfolio_value:,.0f})")
                return False

        except Exception as e:
            logger.warning(f"Error checking address {address[:10]}...: {e}")
            return False

    async def register_addresses_async(self, addresses: set) -> int:
        """
        Async version of register_addresses.
        Checks ALL addresses in PARALLEL.

        Returns:
            Number of new whales added
        """
        if not addresses:
            return 0

        # Get existing addresses to skip
        existing = set(self.storage.get_active_whale_addresses())
        all_whales = self.storage.get_all_whale_addresses()
        existing.update(w['address'] for w in all_whales)

        # Only check addresses not yet in whale_addresses
        new_addresses = addresses - existing

        if not new_addresses:
            return 0

        logger.info(f"Checking {len(new_addresses)} addresses for whale status (async)...")

        import time
        start = time.time()

        # Create shared session for all requests
        async with aiohttp.ClientSession() as session:
            # Check all addresses in parallel
            tasks = [
                self._get_portfolio_value_async(addr, session)
                for addr in new_addresses
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        added = 0
        for addr, result in zip(new_addresses, results):
            if isinstance(result, Exception):
                logger.warning(f"Error checking {addr[:10]}...: {result}")
                continue

            portfolio_value = cast(float, result)

            if portfolio_value >= self.min_portfolio_value:
                self.storage.add_whale_address(addr)
                logger.info(f"Added whale: {addr[:10]}... (${portfolio_value:,.0f})")
                added += 1

        elapsed = time.time() - start
        logger.info(f"⏱️ Async whale check: {len(new_addresses)} addresses in {elapsed:.2f}s ({added} whales found)")

        return added

    async def fetch_whale_data_async(
            self,
            address: str,
            session: aiohttp.ClientSession
    ) -> Optional[Dict]:
        """
        Async version of fetch_whale_data.
        Fetches perp, spot, vault data in PARALLEL.
        """
        try:
            portfolio_data = {
                "perp_value": 0,
                "spot_value": 0,
                "vault_value": 0,
                "total_portfolio_value": 0,
                "margin_used": 0,
                "leverage_ratio": 0,
                "num_positions": 0,
            }
            positions = []
            spot_balances = []
            vaults = []

            # Fetch all 3 data sources in parallel
            state_result, spot_result, vault_result = await asyncio.gather(
                self.hl_client.get_user_state_async(address, session),
                self.hl_client.get_spot_clearinghouse_state_async(address, session),
                self.hl_client.get_user_vault_equities_async(address, session),
                return_exceptions=True
            )

            # 1. Process perp positions
            if state_result and not isinstance(state_result, Exception):
                try:
                    state = cast(Dict, state_result)
                    margin_summary = state.get("marginSummary", {})
                    portfolio_data["perp_value"] = float(margin_summary.get("accountValue", 0))
                    portfolio_data["margin_used"] = float(margin_summary.get("totalMarginUsed", 0))

                    for pos_data in state.get("assetPositions", []):
                        position = pos_data.get("position", {})
                        size = float(position.get("szi", 0))

                        if size != 0:
                            positions.append({
                                "coin": position.get("coin", ""),
                                "size": size,
                                "side": "LONG" if size > 0 else "SHORT",
                                "entry_price": float(position.get("entryPx", 0)),
                                "liquidation_price": float(position.get("liquidationPx") or 0),
                                "leverage": float(position.get("leverage", {}).get("value", 1)),
                                "margin_used": float(position.get("marginUsed", 0)),
                                "unrealized_pnl": float(position.get("unrealizedPnl", 0))
                            })

                    portfolio_data["num_positions"] = len(positions)
                except Exception as e:
                    logger.warning(f"Failed to parse perp state for {address[:10]}...: {e}")

            # 2. Process spot balances
            if spot_result and not isinstance(spot_result, Exception):
                try:
                    spot_state = cast(Dict, spot_result)
                    for balance in spot_state.get("balances", []):
                        bal_total = float(balance.get("total", 0))
                        coin = balance.get("coin", "")

                        if bal_total == 0:
                            continue

                        if coin in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0", "USDE", "USDH"]:
                            if bal_total > self.dust_threshold:
                                portfolio_data["spot_value"] += bal_total
                                spot_balances.append({
                                    "coin": coin,
                                    "amount": bal_total,
                                    "value": bal_total,
                                    "price": 1.0
                                })
                        else:
                            price = self.hl_client.get_token_price(coin)
                            if price:
                                should_include, reason = self._should_include_token(coin, price, bal_total)
                                if should_include:
                                    token_value = bal_total * price
                                    portfolio_data["spot_value"] += token_value
                                    spot_balances.append({
                                        "coin": coin,
                                        "amount": bal_total,
                                        "value": token_value,
                                        "price": price
                                    })
                except Exception as e:
                    logger.warning(f"Failed to parse spot state for {address[:10]}...: {e}")

            # 3. Process vault holdings
            if vault_result and not isinstance(vault_result, Exception):
                try:
                    vault_equities = cast(List, vault_result)
                    for vault_eq in vault_equities:
                        equity = float(vault_eq.get("equity", 0))
                        if equity > 0:
                            portfolio_data["vault_value"] += equity
                            vaults.append({
                                "vault_address": vault_eq.get("vaultAddress", ""),
                                "value": equity
                            })
                except Exception as e:
                    logger.warning(f"Failed to parse vaults for {address[:10]}...: {e}")

            # Calculate totals
            portfolio_data["total_portfolio_value"] = (
                    portfolio_data["perp_value"] +
                    portfolio_data["spot_value"] +
                    portfolio_data["vault_value"]
            )

            # Leverage ratio
            if portfolio_data["total_portfolio_value"] > 0:
                position_value = sum(abs(p["size"] * p["entry_price"]) for p in positions)
                portfolio_data["leverage_ratio"] = round(
                    position_value / portfolio_data["total_portfolio_value"], 2
                )

            return {
                "portfolio_data": portfolio_data,
                "positions": positions,
                "spot_balances": spot_balances,
                "vaults": vaults
            }

        except Exception as e:
            logger.error(f"Error fetching data for {address[:10]}...: {e}")
            self.errors_count += 1
            return None

    async def take_snapshot_async(
            self,
            address: str,
            session: aiohttp.ClientSession
    ) -> bool:
        """
        Async version of take_snapshot.
        """
        data = await self.fetch_whale_data_async(address, session)
        if not data:
            return False

        portfolio_value = data["portfolio_data"]["total_portfolio_value"]
        perp_value = data["portfolio_data"]["perp_value"]
        spot_value = data["portfolio_data"]["spot_value"]
        vault_value = data["portfolio_data"]["vault_value"]

        # Check if still above threshold
        if portfolio_value < self.min_portfolio_value:
            # SANITY CHECK: If ALL values are 0, likely API failure - don't deactivate
            if perp_value == 0 and spot_value == 0 and vault_value == 0:
                logger.warning(f"Skipping deactivation for {address[:10]}... - likely API failure (all zeros)")
                return False

            self.storage.update_whale_status(address, is_active=False)
            logger.info(f"Whale dropped below threshold: {address[:10]}... (${portfolio_value:,.0f})")
            return False

    async def run_hourly_snapshot_async(self, batch_size: int = 20) -> Dict:
        """
        Async version of run_hourly_snapshot.
        Processes whales sequentially with delays to avoid rate limiting.
        """
        import time
        start_time = time.time()
        logger.info("Starting hourly whale snapshot (async)...")

        active_whales = self.storage.get_active_whale_addresses()
        total = len(active_whales)

        if total == 0:
            logger.warning("No active whales to snapshot")
            return {"total": 0, "success": 0, "failed": 0, "dropped": 0}

        logger.info(f"Snapshotting {total} active whales sequentially (0.3s delay)...")

        success = 0
        failed = 0
        dropped = 0

        async with aiohttp.ClientSession() as session:
            for i, addr in enumerate(active_whales):
                try:
                    result = await self.take_snapshot_async(addr, session)

                    if isinstance(result, Exception):
                        logger.error(f"Error snapshotting {addr[:10]}...: {result}")
                        failed += 1
                        self.errors_count += 1
                    elif result:
                        success += 1
                    else:
                        # Either error or dropped below threshold
                        if addr not in self.storage.get_active_whale_addresses():
                            dropped += 1
                        else:
                            failed += 1

                    self.addresses_processed += 1

                    # Progress logging every 100 addresses
                    if (i + 1) % 100 == 0:
                        elapsed_so_far = time.time() - start_time
                        rate = (i + 1) / elapsed_so_far
                        eta = (total - i - 1) / rate if rate > 0 else 0
                        logger.info(f"Progress: {i + 1}/{total} ({success} success, {failed} failed) - ETA: {eta:.0f}s")

                    # Rate limit: wait between each call
                    await asyncio.sleep(0.6)

                except Exception as e:
                    logger.error(f"Error snapshotting {addr[:10]}...: {e}")
                    failed += 1
                    self.errors_count += 1

        elapsed = time.time() - start_time

        logger.info(
            f"Hourly snapshot complete: "
            f"{success}/{total} success, {failed} failed, {dropped} dropped "
            f"({elapsed:.1f}s)"
        )

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "dropped": dropped,
            "elapsed_seconds": elapsed
        }

    async def check_inactive_whales_async(self) -> int:
        """
        Async version of check_inactive_whales.
        Checks all inactive whales in parallel.
        """
        all_whales = self.storage.get_all_whale_addresses()
        inactive = [w for w in all_whales if not w['is_active']]

        if not inactive:
            return 0

        logger.info(f"Checking {len(inactive)} inactive whales (async)...")

        async with aiohttp.ClientSession() as session:
            tasks = [
                self._get_portfolio_value_async(w['address'], session)
                for w in inactive
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        reactivated = 0
        for whale, result in zip(inactive, results):
            if isinstance(result, Exception):
                continue

            portfolio_value = cast(float, result)
            if portfolio_value >= self.min_portfolio_value:
                self.storage.update_whale_status(whale['address'], is_active=True)
                logger.info(f"Reactivated whale: {whale['address'][:10]}... (${portfolio_value:,.0f})")
                reactivated += 1

        if reactivated > 0:
            logger.info(f"Reactivated {reactivated} whales")

        return reactivated

    def get_summary(self) -> Dict:
        """
        Get summary statistics.

        Returns:
            Dict with summary stats
        """
        active_count = self.storage.get_whale_count(active_only=True)
        total_count = self.storage.get_whale_count(active_only=False)

        return {
            "active_whales": active_count,
            "total_whales": total_count,
            "inactive_whales": total_count - active_count,
            "snapshots_taken": self.snapshots_taken,
            "addresses_processed": self.addresses_processed,
            "errors": self.errors_count,
            "min_portfolio_threshold": self.min_portfolio_value,
            "blacklisted_tokens": len(self.blacklisted_tokens)
        }

    def get_whale(self, address: str) -> Optional[Dict]:
        """
        Get latest snapshot for a whale.

        Args:
            address: Wallet address

        Returns:
            Dict with latest snapshot or None
        """
        return self.storage.get_latest_snapshot(address)

    def get_whale_history(self, address: str, limit: int = 168) -> List[Dict]:
        """
        Get portfolio history for a whale.

        Args:
            address: Wallet address
            limit: Max snapshots (default 168 = 1 week hourly)

        Returns:
            List of portfolio snapshots
        """
        return self.storage.get_portfolio_history(address, limit=limit)

    def get_top_whales(self, limit: int = 20) -> List[Dict]:
        """
        Get top whales by portfolio value.

        Args:
            limit: Max results

        Returns:
            List of whale snapshots sorted by portfolio value
        """
        active_whales = self.storage.get_active_whale_addresses()

        whales_with_value = []
        for address in active_whales:
            snapshot = self.storage.get_latest_snapshot(address)
            if snapshot:
                whales_with_value.append({
                    "address": address,
                    "total_portfolio_value": snapshot.get("total_portfolio_value", 0),
                    "perp_value": snapshot.get("perp_value", 0),
                    "spot_value": snapshot.get("spot_value", 0),
                    "vault_value": snapshot.get("vault_value", 0),
                    "num_positions": snapshot.get("num_positions", 0),
                    "leverage_ratio": snapshot.get("leverage_ratio", 0),
                })

        # Sort by portfolio value
        whales_with_value.sort(key=lambda x: x["total_portfolio_value"], reverse=True)

        return whales_with_value[:limit]