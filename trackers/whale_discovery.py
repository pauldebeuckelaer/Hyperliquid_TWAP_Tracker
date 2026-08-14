#!/usr/bin/env python3
"""
Whale Discovery
===============
Event-driven whale discovery: probe portfolio value, threshold check, register.

Lifecycle of a newly-discovered whale:
1. TWAP order fires → main._process_order_events calls discovery.evaluate(address)
2. evaluate() fetches full state from Hyperliquid (perp + spot + vault + HIP-3)
3. If portfolio >= min_portfolio_value, returns a WhaleState; else None
4. main calls discovery.register(address) → INSERT into whale_addresses
   (is_active=1, tier=NULL, tier_perp_amount=NULL — UNTIERED until next refresh)
5. main hands the WhaleState to the snapshot manager for persist
6. Next hourly tier refresh (tier_manager.refresh_tiers_from_snapshots) reads
   the persisted perp_snapshots + perp_account_snapshots and assigns tier
7. From the following cycle, the address is picked up by the tier-driven loop

Discovery does NOT:
- Write any snapshot tables (that's the snapshot manager's job)
- Know about tiers (Discovery only cares about the $50K floor)
- Handle reactivation of dormant whales (parked design question)
- Touch HIP-3 unless configured to (same gating as today)
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, cast

import aiohttp
from trackers.tier_manager import TIER_THRESHOLDS

logger = logging.getLogger(__name__)

def _cum_funding(position: Dict) -> Optional[float]:
    """cumFunding.allTime as a float, or None if the field is absent.

    NEGATIVE = funding RECEIVED. Odometer since the wallet first opened
    this coin, so only deltas between consecutive snapshots are
    attributable to an observation window.
    """
    cf = position.get("cumFunding") or {}
    val = cf.get("allTime")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

def _margin_mode(position: Dict) -> Optional[str]:
    """leverage.type — 'cross' or 'isolated' — or None if absent.

    Ground truth for margin mode. The liquidation-distance heuristic
    (dist > 2 => cross) was the pre-column proxy for this.
    """
    lev = position.get("leverage")
    if not isinstance(lev, dict):
        return None
    return lev.get("type")


# =============================================================================
# VALUE OBJECTS
# =============================================================================


@dataclass
class WhaleState:
    """
    Snapshot-able state for a single whale at a single point in time.

    This is the contract between Discovery (producer) and the snapshot
    manager (consumer): Discovery returns this on a successful evaluate(),
    and the snapshot manager's persist() takes this same shape.

    Field shapes match what storage.save_whale_snapshot and
    storage.save_perp_account_snapshot expect today, so the wrapping is
    purely for type safety — no schema change.
    """
    portfolio_data: Dict
    positions: List[Dict]
    spot_balances: List[Dict]
    vaults: List[Dict]
    account_data: Dict

    def total_value(self) -> float:
        """Convenience accessor for the threshold check."""
        return self.portfolio_data.get("total_portfolio_value", 0)


class TokenFilter:
    """
    Token inclusion rules: blacklist, whitelist, dust threshold, price sanity.

    Stateless, no DB or API dependencies. Used by Discovery (for portfolio
    valuation) and later by the snapshot manager (for spot inclusion). One
    instance shared at the TWAPBot level.
    """

    # Stablecoins treated as $1 without a price lookup
    STABLECOINS = frozenset({
        "USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0", "USDE", "USDH",
    })

    def __init__(self, config: Optional[Dict] = None):
        config = config or {}

        self.dust_threshold = config.get("dust_threshold", 5.0)

        self.blacklisted_tokens = config.get("blacklisted_tokens", {
            "NIGGO", "LIQD", "FUND", "STEEL", "HWTR",
            "SWAP", "BERA", "DEPIN", "GENESY", "TILT",
            "RANK", "PURRO", "HOLD", "RAT", "MANLET",
            "WHYPI", "PENIS", "RISK", "HAR",
        })

        self.whitelisted_high_value_tokens = config.get(
            "whitelisted_high_value_tokens",
            {"UBTC", "BTC", "WBTC", "tBTC"},
        )

        self.whitelisted_sub_dollar_tokens = config.get(
            "whitelisted_sub_dollar_tokens",
            {"UFART", "PURR", "UPUMP", "UXPL", "PUMP",
             "kBONK", "BONK", "UBONK3", "PEPE", "SHIB", "FLOKI", "WIF"},
        )

        self.max_reasonable_price = config.get("max_reasonable_price", 50_000)
        self.suspicious_dollar_range = config.get(
            "suspicious_dollar_range", (0.995, 1.005)
        )
        self.max_high_value_token_price = config.get(
            "max_high_value_token_price", 150_000
        )

    def is_stablecoin(self, coin: str) -> bool:
        return coin in self.STABLECOINS

    def should_include(self, coin: str, price: float, amount: float) -> Tuple[bool, str]:
        """
        Returns (include, reason) where reason is the rule the token tripped
        (or 'passed all filters' on success).
        """
        token_value = amount * price

        if coin in self.blacklisted_tokens:
            return False, "blacklisted"

        min_dollar, max_dollar = self.suspicious_dollar_range
        if min_dollar <= price <= max_dollar:
            return False, f"suspicious $1.00 price ({price:.6f})"

        if price > self.max_reasonable_price:
            if coin in self.whitelisted_high_value_tokens:
                if price > self.max_high_value_token_price:
                    return False, f"price ${price:,.2f} exceeds high-value max"
            else:
                return False, f"suspicious high price ${price:,.2f}"

        if price < 1.0 and coin not in self.whitelisted_sub_dollar_tokens:
            return False, f"sub-$1 not whitelisted (${price:.6f})"

        if token_value <= self.dust_threshold:
            return False, f"dust (${token_value:.2f})"

        return True, "passed all filters"


# =============================================================================
# WHALE DISCOVERY
# =============================================================================


class WhaleDiscovery:
    """
    Event-driven discovery: fetch state, threshold-check, register.

    Stateless across calls (no caches, no internal counters that matter).
    Safe to instantiate once at startup and reuse for the life of the bot.
    """

    def __init__(
            self,
            hl_client,
            storage,
            token_filter: TokenFilter,
            config: Optional[Dict] = None,
    ):
        self.hl_client = hl_client
        self.storage = storage
        self.token_filter = token_filter
        self.config = config or {}

        self.min_portfolio_value = self.config.get("min_portfolio_value", 50_000)
        self.hip3_tracking_enabled = self.config.get("hip3_tracking_enabled", False)

        hip3_status = "ON" if self.hip3_tracking_enabled else "OFF"
        logger.info(
            f"WhaleDiscovery initialized: "
            f"min_portfolio=${self.min_portfolio_value:,}, "
            f"HIP-3={hip3_status}"
        )

    # -------------------------------------------------------------------------
    # PUBLIC API
    # -------------------------------------------------------------------------

    async def evaluate(
            self,
            address: str,
            session: aiohttp.ClientSession,
    ) -> Optional[WhaleState]:
        """
        Fetch full state for an address and return WhaleState if it qualifies.

        Returns None if:
        - All three core APIs failed (no data to evaluate)
        - Total portfolio value is below min_portfolio_value

        Returns a WhaleState if the address qualifies as a whale. The caller
        is then expected to call register() and hand the state to the
        snapshot manager for persist.
        """
        state = await self._fetch_full_state_async(address, session)
        if state is None:
            return None

        # Gate on the SAME per-axis floors tiering uses (T5 of each axis),
        # not a summed portfolio total. A whale that can't clear at least one
        # axis floor can't be tiered — registering it would create a
        # permanent untiered-active orphan. Mirrors tier_manager's cash
        # derivation: total_account_value = account_value + hip3_account_value.
        pos_val = sum(abs(p["size"] * p["entry_price"]) for p in state.positions)
        cash_val = (state.account_data.get("account_value") or 0) + \
                   (state.account_data.get("hip3_account_value") or 0)
        spot_val = state.portfolio_data.get("spot_value", 0)

        if not (
                pos_val >= TIER_THRESHOLDS['position'][5] or
                cash_val >= TIER_THRESHOLDS['cash'][5] or
                spot_val >= TIER_THRESHOLDS['spot'][5]
        ):
            logger.debug(
                f"Below all axis floors: {address[:10]}... "
                f"(pos ${pos_val:,.0f}, cash ${cash_val:,.0f}, spot ${spot_val:,.0f})"
            )
            return None

        total = state.total_value()
        logger.info(
            f"Whale qualifies: {address[:10]}... (${total:,.0f})"
        )
        return state

    def register(self, address: str) -> bool:
        """
        Register address in whale_addresses table.

        Three paths:
        - New address: INSERT a fresh row (is_active=1, tier=NULL). Returns True.
        - Existing but inactive (dropped below threshold previously, then
          re-qualified now): flip is_active back to 1. Returns True.
        - Existing and active: no-op. Returns False.

        The inactive-flip case (option b) handles whales that previously
        deactivated and now fire a TWAP order above the $50K floor again.
        Without this, they would stay is_active=0 forever and never be
        picked up by the tier-driven loop.

        Note: the inactive-flip does NOT clear tier columns. tier_manager's
        next hourly refresh will reassign them from fresh snapshot data.
        """
        added = self.storage.add_whale_address(address)
        if added:
            logger.info(f"Registered new whale: {address}")
            return True

        # Already exists — check if it's an inactive whale we need to reactivate
        existing_active = set(self.storage.get_active_whale_addresses())
        if address not in existing_active:
            self.storage.update_whale_status(address, is_active=True)
            logger.info(f"Reactivated previously-inactive whale: {address}")
            return True

        return False

    # -------------------------------------------------------------------------
    # INTERNAL: FULL STATE FETCH
    # -------------------------------------------------------------------------

    async def _fetch_full_state_async(
            self,
            address: str,
            session: aiohttp.ClientSession,
    ) -> Optional[WhaleState]:
        """
        Fetch perp + spot + vault (+ HIP-3 if enabled) in parallel, parse
        into a WhaleState. Returns None if no usable data came back.

        This mirrors what WhaleMetricsManager.fetch_whale_data_async does
        today — when the snapshot manager exists, this logic will move
        there and Discovery will receive state from the manager instead
        of fetching directly. For this refactor round, Discovery fetches
        its own state.
        """
        tasks = [
            self.hl_client.get_user_state_async(address, session),
            self.hl_client.get_spot_clearinghouse_state_async(address, session),
            self.hl_client.get_user_vault_equities_async(address, session),
        ]

        hip3_dexes: List[str] = []
        if self.hip3_tracking_enabled:
            hip3_dexes = self.hl_client.get_active_hip3_dexes()
            for dex in hip3_dexes:
                tasks.append(
                    self.hl_client.get_user_state_hip3_async(address, dex, session)
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        state_result, spot_result, vault_result = results[0], results[1], results[2]
        hip3_results = results[3:] if hip3_dexes else []

        # If all three core fetches failed, there's nothing to evaluate
        if all(isinstance(r, Exception) or not r
               for r in (state_result, spot_result, vault_result)):
            logger.warning(
                f"All core APIs failed for {address[:10]}..., skipping"
            )
            return None

        portfolio_data = {
            "perp_value": 0.0,
            "spot_value": 0.0,
            "vault_value": 0.0,
            "total_portfolio_value": 0.0,
            "margin_used": 0.0,
            "leverage_ratio": 0.0,
            "num_positions": 0,
        }
        positions: List[Dict] = []
        spot_balances: List[Dict] = []
        vaults: List[Dict] = []
        account_data = {
            "account_value": None,
            "total_raw_usd": None,
            "total_margin_used": None,
            "total_ntl_pos": None,
            "withdrawable": None,
            "hip3_account_value": None,
            "hip3_total_raw_usd": None,
            "hip3_total_margin_used": None,
            "hip3_total_ntl_pos": None,
            "hip3_withdrawable": None,
            "hip3_dexes": None,
        }

        # --- 1. Mainnet perp positions + account_data ---
        if state_result and not isinstance(state_result, Exception):
            try:
                self._parse_perp_state(
                    cast(Dict, state_result),
                    portfolio_data, positions, account_data,
                )
            except Exception as e:
                logger.warning(f"Failed to parse perp state for {address}: {e}")

        # --- 2. HIP-3 positions + account_data (additive) ---
        if self.hip3_tracking_enabled and hip3_dexes:
            self._parse_hip3_states(
                hip3_dexes, hip3_results,
                portfolio_data, positions, account_data,
                address,
            )

        # --- 3. Spot balances ---
        if spot_result and not isinstance(spot_result, Exception):
            try:
                self._parse_spot_state(
                    cast(Dict, spot_result),
                    portfolio_data, spot_balances,
                )
            except Exception as e:
                logger.warning(f"Failed to parse spot state for {address}: {e}")

        # --- 4. Vault holdings ---
        if vault_result and not isinstance(vault_result, Exception):
            try:
                self._parse_vault_state(
                    cast(List, vault_result),
                    portfolio_data, vaults,
                )
            except Exception as e:
                logger.warning(f"Failed to parse vaults for {address}: {e}")

        # --- Totals ---
        portfolio_data["total_portfolio_value"] = (
            portfolio_data["perp_value"]
            + portfolio_data["spot_value"]
            + portfolio_data["vault_value"]
        )

        if portfolio_data["total_portfolio_value"] > 0:
            position_value = sum(
                abs(p["size"] * p["entry_price"]) for p in positions
            )
            portfolio_data["leverage_ratio"] = round(
                position_value / portfolio_data["total_portfolio_value"], 2
            )

        return WhaleState(
            portfolio_data=portfolio_data,
            positions=positions,
            spot_balances=spot_balances,
            vaults=vaults,
            account_data=account_data,
        )

    # -------------------------------------------------------------------------
    # INTERNAL: PARSERS (one per API response)
    # -------------------------------------------------------------------------

    def _parse_perp_state(
            self,
            state: Dict,
            portfolio_data: Dict,
            positions: List[Dict],
            account_data: Dict,
    ):
        """Mainnet perp positions + marginSummary into portfolio_data + account_data."""
        margin_summary = state.get("marginSummary", {})
        portfolio_data["perp_value"] = float(margin_summary.get("accountValue", 0))
        portfolio_data["margin_used"] = float(margin_summary.get("totalMarginUsed", 0))

        account_data["account_value"] = float(margin_summary.get("accountValue", 0))
        account_data["total_raw_usd"] = float(margin_summary.get("totalRawUsd", 0))
        account_data["total_margin_used"] = float(margin_summary.get("totalMarginUsed", 0))
        account_data["total_ntl_pos"] = float(margin_summary.get("totalNtlPos", 0))

        withdrawable_raw = state.get("withdrawable")
        if withdrawable_raw is not None:
            account_data["withdrawable"] = float(withdrawable_raw)

        for pos_data in state.get("assetPositions", []):
            position = pos_data.get("position", {})
            size = float(position.get("szi", 0))
            if size == 0:
                continue

            positions.append({
                "coin": position.get("coin", ""),
                "size": size,
                "side": "LONG" if size > 0 else "SHORT",
                "entry_price": float(position.get("entryPx", 0)),
                "liquidation_price": float(position.get("liquidationPx") or 0),
                "leverage": float(position.get("leverage", {}).get("value", 1)),
                "margin_used": float(position.get("marginUsed", 0)),
                "unrealized_pnl": float(position.get("unrealizedPnl", 0)),
                "cum_funding_all_time": _cum_funding(position),
                "margin_mode": _margin_mode(position),
            })

        portfolio_data["num_positions"] = len(positions)

    def _parse_hip3_states(
            self,
            hip3_dexes: List[str],
            hip3_results: List,
            portfolio_data: Dict,
            positions: List[Dict],
            account_data: Dict,
            address: str,
    ):
        """HIP-3 positions + consolidated marginSummary across dexes."""
        hip3_acc_total = 0.0
        hip3_raw_total = 0.0
        hip3_margin_total = 0.0
        hip3_ntl_total = 0.0
        hip3_withdrawable_total = 0.0
        hip3_dexes_present: List[str] = []
        hip3_pos_count = 0

        for dex, hip3_result in zip(hip3_dexes, hip3_results):
            if isinstance(hip3_result, Exception) or not hip3_result:
                continue

            try:
                hip3_state = cast(Dict, hip3_result)
                hip3_margin = hip3_state.get("marginSummary", {})

                hip3_account_value = float(hip3_margin.get("accountValue", 0))
                if hip3_account_value > 0:
                    portfolio_data["perp_value"] += hip3_account_value

                hip3_acc_total += hip3_account_value
                hip3_raw_total += float(hip3_margin.get("totalRawUsd", 0))
                hip3_margin_total += float(hip3_margin.get("totalMarginUsed", 0))
                hip3_ntl_total += float(hip3_margin.get("totalNtlPos", 0))

                hip3_withdrawable_raw = hip3_state.get("withdrawable")
                if hip3_withdrawable_raw is not None:
                    hip3_withdrawable_total += float(hip3_withdrawable_raw)

                hip3_dexes_present.append(dex)

                for pos_data in hip3_state.get("assetPositions", []):
                    position = pos_data.get("position", {})
                    size = float(position.get("szi", 0))
                    if size == 0:
                        continue

                    coin = position.get("coin", "")
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
                        "unrealized_pnl": float(position.get("unrealizedPnl", 0)),
                        "cum_funding_all_time": _cum_funding(position),
                        "margin_mode": _margin_mode(position)
                    })
                    hip3_pos_count += 1

            except Exception as e:
                logger.warning(f"Failed to parse HIP-3 '{dex}' state for {address}: {e}")

        if hip3_pos_count > 0:
            portfolio_data["num_positions"] = len(positions)
            logger.debug(
                f"🏗️  {address[:10]}...: {hip3_pos_count} HIP-3 positions"
            )

        if hip3_dexes_present:
            account_data["hip3_account_value"] = hip3_acc_total
            account_data["hip3_total_raw_usd"] = hip3_raw_total
            account_data["hip3_total_margin_used"] = hip3_margin_total
            account_data["hip3_total_ntl_pos"] = hip3_ntl_total
            account_data["hip3_withdrawable"] = hip3_withdrawable_total
            account_data["hip3_dexes"] = ",".join(hip3_dexes_present)

    def _parse_spot_state(
            self,
            spot_state: Dict,
            portfolio_data: Dict,
            spot_balances: List[Dict],
    ):
        """Spot balances into portfolio_data['spot_value'] + spot_balances list."""
        for balance in spot_state.get("balances", []):
            bal_total = float(balance.get("total", 0))
            coin = balance.get("coin", "")
            if bal_total == 0:
                continue

            if self.token_filter.is_stablecoin(coin):
                if bal_total > self.token_filter.dust_threshold:
                    portfolio_data["spot_value"] += bal_total
                    spot_balances.append({
                        "coin": coin,
                        "amount": bal_total,
                        "value": bal_total,
                        "price": 1.0,
                    })
                continue

            price = self.hl_client.get_token_price(coin)
            if not price:
                continue

            include, _ = self.token_filter.should_include(coin, price, bal_total)
            if not include:
                continue

            token_value = bal_total * price
            portfolio_data["spot_value"] += token_value
            spot_balances.append({
                "coin": coin,
                "amount": bal_total,
                "value": token_value,
                "price": price,
            })

    def _parse_vault_state(
            self,
            vault_equities: List,
            portfolio_data: Dict,
            vaults: List[Dict],
    ):
        """Vault holdings into portfolio_data['vault_value'] + vaults list."""
        for vault_eq in vault_equities:
            equity = float(vault_eq.get("equity", 0))
            if equity <= 0:
                continue
            portfolio_data["vault_value"] += equity
            vaults.append({
                "vault_address": vault_eq.get("vaultAddress", ""),
                "value": equity,
            })