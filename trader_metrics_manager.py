#!/usr/bin/env python3
"""
Whale Metrics Manager
=====================
Manages hourly snapshots of whale portfolios (>$50K).

Features:
- Hourly snapshots of portfolio composition
- Tracks perp positions, spot balances, vault holdings
- HIP-3 position tracking (tokenized stocks, commodities)
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
    - HIP-3 perp positions (tokenized stocks, etc.)
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

        # =====================================================================
        # HIP-3 CONFIG
        # =====================================================================
        self.hip3_tracking_enabled = self.config.get('hip3_tracking_enabled', False)
        # =====================================================================

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

        hip3_status = "ON" if self.hip3_tracking_enabled else "OFF"
        logger.info(
            f"WhaleMetricsManager initialized: "
            f"min_portfolio=${self.min_portfolio_value:,}, "
            f"blacklist={len(self.blacklisted_tokens)} tokens, "
            f"HIP-3={hip3_status}"
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
                logger.info(f"Added whale: {address} (${portfolio_value:,.0f})")
                return True
            else:
                logger.debug(f"Below threshold: {address} (${portfolio_value:,.0f})")
                return False

        except Exception as e:
            logger.warning(f"Error checking address {address}: {e}")
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
                    logger.info(f"Added whale: {addr} (${portfolio_value:,.0f})")
                    added += 1
            except Exception as e:
                logger.warning(f"Error checking {addr}: {e}")

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
                logger.warning(f"Failed to get perp state for {address}: {e}")

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
                logger.warning(f"Failed to get spot state for {address}: {e}")

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
                logger.warning(f"Failed to get vaults for {address}: {e}")

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
            logger.error(f"Error fetching data for {address}: {e}")
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
                logger.warning(f"Skipping deactivation for {address} - API returned all zeros")
                return False

            self.storage.update_whale_status(address, is_active=False)
            logger.info(f"Whale dropped below threshold: {address} (${portfolio_value:,.0f})")
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
                logger.error(f"Error snapshotting {address}: {e}")
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
            logger.info(f"Reactivated whale: {address} (${portfolio_value:,.0f})")
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
        Fetches perp, spot, vault data in PARALLEL.
        Includes HIP-3 margin values if enabled.
        """
        total = 0.0

        # Fetch all 3 data sources in parallel
        tasks = [
            self.hl_client.get_user_state_async(address, session),
            self.hl_client.get_spot_clearinghouse_state_async(address, session),
            self.hl_client.get_user_vault_equities_async(address, session),
        ]

        # =====================================================================
        # HIP-3: Also fetch HIP-3 states in parallel for portfolio value
        # =====================================================================
        hip3_dexes = []
        if self.hip3_tracking_enabled:
            hip3_dexes = self.hl_client.get_active_hip3_dexes()
            for dex in hip3_dexes:
                tasks.append(
                    self.hl_client.get_user_state_hip3_async(address, dex, session)
                )
        # =====================================================================

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Unpack fixed results
        state_result = results[0]
        spot_result = results[1]
        vault_result = results[2]

        # Log API failures
        if isinstance(state_result, Exception):
            logger.debug(f"Perp API failed for {address}: {state_result}")
        if isinstance(spot_result, Exception):
            logger.debug(f"Spot API failed for {address}: {spot_result}")
        if isinstance(vault_result, Exception):
            logger.debug(f"Vault API failed for {address}: {vault_result}")

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

        # =====================================================================
        # HIP-3: Add margin values from HIP-3 dexes
        # =====================================================================
        if self.hip3_tracking_enabled and hip3_dexes:
            for i, dex in enumerate(hip3_dexes):
                hip3_result = results[3 + i]  # Offset past the 3 fixed tasks

                if isinstance(hip3_result, Exception) or not hip3_result:
                    continue

                try:
                    hip3_state = cast(Dict, hip3_result)
                    margin_summary = hip3_state.get("marginSummary", {})
                    hip3_value = float(margin_summary.get("accountValue", 0))
                    if hip3_value > 0:
                        total += hip3_value
                        logger.debug(f"HIP-3 '{dex}' value for {address[:10]}...: ${hip3_value:,.0f}")
                except Exception:
                    pass
        # =====================================================================

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
                logger.info(f"Added whale: {address} (${portfolio_value:,.0f})")
                return True
            else:
                logger.debug(f"Below threshold: {address} (${portfolio_value:,.0f})")
                return False

        except Exception as e:
            logger.warning(f"Error checking address {address}: {e}")
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
                logger.warning(f"Error checking {addr}: {result}")
                continue

            portfolio_value = cast(float, result)

            if portfolio_value >= self.min_portfolio_value:
                self.storage.add_whale_address(addr)
                logger.info(f"Added whale: {addr} (${portfolio_value:,.0f})")
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
        Includes HIP-3 positions if enabled.
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

            # Build parallel task list
            tasks = [
                self.hl_client.get_user_state_async(address, session),
                self.hl_client.get_spot_clearinghouse_state_async(address, session),
                self.hl_client.get_user_vault_equities_async(address, session),
            ]

            # =================================================================
            # HIP-3: Add parallel tasks for each active dex
            # =================================================================
            hip3_dexes = []
            if self.hip3_tracking_enabled:
                hip3_dexes = self.hl_client.get_active_hip3_dexes()
                for dex in hip3_dexes:
                    tasks.append(
                        self.hl_client.get_user_state_hip3_async(address, dex, session)
                    )
            # =================================================================

            # Fire ALL tasks in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Unpack fixed results
            state_result = results[0]
            spot_result = results[1]
            vault_result = results[2]

            # DEBUG: Log what we got back
            if isinstance(state_result, Exception):
                logger.warning(f"Perp API EXCEPTION for {address}: {state_result}")
            elif not state_result:
                logger.warning(f"Perp API EMPTY for {address}: {state_result}")
            else:
                margin_summary = state_result.get("marginSummary", {})
                account_value = margin_summary.get("accountValue", 0)
                logger.debug(f"Perp API OK for {address}: accountValue={account_value}")

            if isinstance(spot_result, Exception):
                logger.warning(f"Spot API EXCEPTION for {address}: {spot_result}")

            if isinstance(vault_result, Exception):
                logger.warning(f"Vault API EXCEPTION for {address}: {vault_result}")

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
                    logger.warning(f"Failed to parse perp state for {address}: {e}")

            # =================================================================
            # 1b. Process HIP-3 positions (NEW)
            # =================================================================
            if self.hip3_tracking_enabled and hip3_dexes:
                hip3_pos_count = 0

                for i, dex in enumerate(hip3_dexes):
                    hip3_result = results[3 + i]  # Offset past 3 fixed tasks

                    if isinstance(hip3_result, Exception) or not hip3_result:
                        continue

                    try:
                        hip3_state = cast(Dict, hip3_result)

                        # Add HIP-3 margin value to perp_value
                        hip3_margin = hip3_state.get("marginSummary", {})
                        hip3_account_value = float(hip3_margin.get("accountValue", 0))
                        if hip3_account_value > 0:
                            portfolio_data["perp_value"] += hip3_account_value

                        # Parse HIP-3 positions
                        for pos_data in hip3_state.get("assetPositions", []):
                            position = pos_data.get("position", {})
                            size = float(position.get("szi", 0))

                            if size == 0:
                                continue

                            coin = position.get("coin", "")
                            # Ensure dex prefix
                            if ":" not in coin:
                                coin = f"{dex}:{coin}"

                            positions.append({
                                "coin": coin,
                                "size": size,
                                "side": "LONG" if size > 0 else "SHORT",
                                "entry_price": float(position.get("entryPx", 0)),
                                "liquidation_price": float(position.get("liquidationPx") or 0),
                                "leverage": float(position.get("leverage", {}).get("value", 1)),
                                "margin_used": float(position.get("marginUsed", 0)),
                                "unrealized_pnl": float(position.get("unrealizedPnl", 0))
                            })
                            hip3_pos_count += 1

                    except Exception as e:
                        logger.warning(f"Failed to parse HIP-3 '{dex}' state for {address}: {e}")

                if hip3_pos_count > 0:
                    # Update position count to include HIP-3
                    portfolio_data["num_positions"] = len(positions)
                    logger.debug(
                        f"🏗️  {address[:10]}...: {hip3_pos_count} HIP-3 positions added to snapshot"
                    )
            # =================================================================

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
                    logger.warning(f"Failed to parse spot state for {address}: {e}")

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
                    logger.warning(f"Failed to parse vaults for {address}: {e}")

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
            logger.error(f"Error fetching data for {address}: {e}")
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
        # Skip if snapshotted within last 45 minutes
        self.storage.cursor.execute("""
            SELECT snapshot_time FROM portfolio_snapshots 
            WHERE address = ? 
            ORDER BY snapshot_time DESC LIMIT 1
        """, (address,))
        last_snap = self.storage.cursor.fetchone()

        if last_snap:
            last_time = datetime.fromisoformat(last_snap[0])
            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < 3600:  # 60 minutes
                logger.debug(f"Skipping {address} - snapshotted {elapsed / 60:.0f}m ago")
                return True  # Return True so it's not counted as failed

        data = await self.fetch_whale_data_async(address, session)
        if not data:
            logger.warning(f"fetch_whale_data_async returned None for {address}")
            return False

        portfolio_value = data["portfolio_data"]["total_portfolio_value"]
        perp_value = data["portfolio_data"]["perp_value"]
        spot_value = data["portfolio_data"]["spot_value"]
        vault_value = data["portfolio_data"]["vault_value"]

        # Sanity check: detect spot API failure (spot vanishes but perp unchanged)
        self.storage.cursor.execute("""
                SELECT spot_value, vault_value, perp_value
                FROM portfolio_snapshots 
                WHERE address = ? 
                ORDER BY snapshot_time DESC LIMIT 1
            """, (address,))
        prev_snap = self.storage.cursor.fetchone()

        if prev_snap:
            prev_spot, prev_vault, prev_perp = prev_snap
            if prev_perp > 0:
                perp_change_pct = abs(perp_value - prev_perp) / prev_perp
                if perp_change_pct < 0.05:  # Perp unchanged = likely API failure
                    # Spot vanished
                    if prev_spot > 100000 and spot_value < 1000:
                        logger.warning(
                            f"Skipping snapshot for {address} - likely spot API failure (${prev_spot:,.0f} → ${spot_value:,.0f})")
                        return True
                    # Vault vanished
                    if prev_vault > 100000 and vault_value < 1000:
                        logger.warning(
                            f"Skipping snapshot for {address} - likely vault API failure (${prev_vault:,.0f} → ${vault_value:,.0f})")
                        return True

        # Check if still above threshold
        if portfolio_value < self.min_portfolio_value:
            # SANITY CHECK: If ALL values are 0, likely API failure - don't deactivate
            if perp_value == 0 and spot_value == 0 and vault_value == 0:
                logger.warning(f"Skipping deactivation for {address} - likely API failure (all zeros)")
                return False

            self.storage.update_whale_status(address, is_active=False)
            logger.info(f"Whale dropped below threshold: {address} (${portfolio_value:,.0f})")
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

    async def run_hourly_snapshot_async(self, batch_size: int = 1, batch_delay: float = 10.0) -> Dict:
        """
        Async version of run_hourly_snapshot.
        Processes whales in batches to balance speed vs rate limiting.

        Args:
            batch_size: Number of whales to process in parallel (default 10)
            batch_delay: Seconds to wait between batches (default 3.0)
        """
        import time
        start_time = time.time()
        logger.info(f"Starting hourly whale snapshot (async, batch_size={batch_size})...")

        active_whales = self.storage.get_active_whale_addresses()
        total = len(active_whales)

        if total == 0:
            logger.warning("No active whales to snapshot")
            return {"total": 0, "success": 0, "failed": 0, "dropped": 0}

        logger.info(f"Snapshotting {total} active whales in batches of {batch_size}...")

        success = 0
        failed = 0
        dropped = 0

        async with aiohttp.ClientSession() as session:
            for i in range(0, total, batch_size):
                batch = active_whales[i:i + batch_size]

                # Process batch in parallel
                tasks = [self.take_snapshot_async(addr, session) for addr in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                for addr, result in zip(batch, results):
                    if isinstance(result, Exception):
                        logger.error(f"Error snapshotting {addr}: {result}")
                        failed += 1
                        self.errors_count += 1
                    elif result:
                        success += 1
                    else:
                        # Check if whale was dropped (deactivated)
                        if addr not in self.storage.get_active_whale_addresses():
                            dropped += 1
                        else:
                            failed += 1

                self.addresses_processed += len(batch)

                # Progress logging every 100 addresses
                processed = i + len(batch)
                if processed % 100 < batch_size:
                    elapsed_so_far = time.time() - start_time
                    rate = processed / elapsed_so_far if elapsed_so_far > 0 else 0
                    eta = (total - processed) / rate if rate > 0 else 0
                    logger.info(f"Progress: {processed}/{total} ({success} success, {failed} failed) - ETA: {eta:.0f}s")

                # Delay between batches (skip after last batch)
                if i + batch_size < total:
                    await asyncio.sleep(batch_delay)

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
                logger.info(f"Reactivated whale: {whale['address']} (${portfolio_value:,.0f})")
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
            "blacklisted_tokens": len(self.blacklisted_tokens),
            "hip3_tracking_enabled": self.hip3_tracking_enabled,
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