#!/usr/bin/env python3
"""
Whale State Collector
=====================
Single producer of whale state snapshots.

Three paths:

1. Cold-start (persist-only, Step 2):
    - WhaleDiscovery hands a WhaleState (already fetched) to persist()
    - Collector writes to all five tables, no API calls

2. Cycle-driven fast ladder (collect_async, Step 3):
    - Called every minute by main loop
    - Asks tier_manager which addresses to fetch this cycle
    - Fetches perp + spot-USDC + HIP-3 (VIP+T1 only) in parallel batches
    - Persists to perp_snapshots + perp_account_snapshots
    - Returns whale_states for downstream analyzers (LiquidationTracker,
      EventDetector)

3. Order-end (fetch_and_persist, Step 4a):
    - Called from main._process_order_events when an active whale's TWAP ends
    - Fetches one address, persists all five tables, no threshold check

4. Cycle-driven slow ladder (collect_slow_async, Step 4b):
    - Called every minute, fires only at cycle 30 when a tier is due
    - Fires at per-tier hourly cadences (VIP+T1 1h, T2 2h, T3 4h, T4/T5 8h)
    - Writes portfolio_snapshots, spot_snapshots, vault_snapshots
      (plus perp/account refreshes, since fetch_and_persist writes them all)
    - Returns nothing — slow data is archival, not analytical input

After Step 4b, this class is the ONLY writer to all five whale state tables.
LiquidationTracker is a pure analyzer.

Sanity checks (preserved from WhaleMetricsManager.take_snapshot_async):
    - All-zeros guard: if perp+spot+vault are all 0, treat as API failure
    - Vanish guard: if perp unchanged but spot/vault vanished, partial failure
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp

from .whale_discovery import WhaleState

logger = logging.getLogger(__name__)


class WhaleStateCollector:
    """
    Whale state producer + fast-ladder persister.

    Cold-start path (persist): write in-hand state from Discovery.
    Cycle-driven path (collect_async): fetch + parse + persist for current cycle.
    """

    # Slow-ladder cadence per tier, in hours (Step 4b).
    # T4 and T5 collapsed onto 8h — bottom-tier whales don't move fast
    # enough to justify a separate cadence.
    SLOW_LADDER_HOURS = {
        'vip': 1,
        1: 1,
        2: 2,
        3: 4,
        4: 8,
        5: 8,
    }

    # Cycle position within the hour at which slow ladder fires for any
    # due tier (Step 4b). Cycle 7 is chosen because no fast-ladder tier
    # divides 7 — every other cycle (5, 10, 15, 20, 25, 30, ...) stacks
    # with T2/T3/T4 fast-ladder fires. Cycle 7 keeps slow-ladder load
    # isolated from fast-ladder spikes.
    SLOW_LADDER_ANCHOR_CYCLE = 7

    def __init__(
            self,
            hl_client,
            storage,
            tier_manager,
            token_filter,
            config: Optional[dict] = None,
    ):
        self.hl_client = hl_client
        self.storage = storage
        self.tier_manager = tier_manager
        self.token_filter = token_filter
        self.config = config or {}

        self.min_portfolio_value = self.config.get("min_portfolio_value", 50_000)
        self.hip3_tracking_enabled = self.config.get("hip3_tracking_enabled", False)

        # Batch/concurrency config — lifted from LiquidationTracker defaults
        self.batch_size = self.config.get("batch_size", 50)
        self.batch_delay = self.config.get("batch_delay", 0.5)
        self.hip3_batch_size = self.config.get("hip3_batch_size", 50)
        self.hip3_batch_delay = self.config.get("hip3_batch_delay", 0.5)

        # API endpoint for direct posts (matches LiquidationTracker pattern)
        self.api_url = "https://api.hyperliquid.xyz/info"

        # Stats — separated by code path
        self.snapshots_taken = 0           # cold-start persists
        self.errors_count = 0
        self.skipped_api_failure = 0
        self.skipped_partial_failure = 0
        self.cycle_count = 0               # cycle-driven sweeps
        self.cycle_addresses_fetched = 0
        self.hip3_positions_found = 0
        self.hip3_fetch_errors = 0
        # Slow-ladder stats (Step 4b)
        self.slow_ladder_runs = 0
        self.slow_ladder_addresses = 0
        self.slow_ladder_errors = 0

        hip3_status = "ON" if self.hip3_tracking_enabled else "OFF"
        logger.info(
            f"WhaleStateCollector initialized: "
            f"min_portfolio=${self.min_portfolio_value:,}, "
            f"batch_size={self.batch_size}, HIP-3={hip3_status}"
        )

    # =========================================================================
    # PUBLIC API — COLD-START PATH (Step 2)
    # =========================================================================

    async def persist(self, address: str, state: WhaleState) -> bool:
        """
        Persist an in-hand WhaleState. No API calls.

        Called by main._process_order_events after Discovery.evaluate
        returns a qualifying state. Writes all five tables.
        """
        if not self._passes_sanity_checks(address, state):
            return False

        # Force is_active=True in case tier_manager deactivated between
        # Discovery.evaluate and Collector.persist.
        self.storage.update_whale_status(address, is_active=True)

        snapshot_time = datetime.now().isoformat()

        try:
            self.storage.save_whale_snapshot(
                address=address,
                snapshot_time=snapshot_time,
                portfolio_data=state.portfolio_data,
                positions=state.positions,
                spot_balances=state.spot_balances,
                vaults=state.vaults,
            )
            self.storage.save_perp_account_snapshot(
                address=address,
                snapshot_time=snapshot_time,
                account_data=state.account_data,
            )
        except Exception as e:
            logger.error(f"Failed to persist whale state for {address}: {e}")
            self.errors_count += 1
            return False

        self.snapshots_taken += 1
        logger.debug(
            f"Persisted whale state for {address[:10]}... "
            f"(${state.total_value():,.0f}) at {snapshot_time}"
        )
        return True

    # =========================================================================
    # PUBLIC API — CYCLE-DRIVEN PATH (Step 3)
    # =========================================================================

    async def collect_async(self, prices: Optional[Dict] = None) -> List[Dict]:
        """
        Cycle-driven fetch + persist + return whale_states.

        Flow:
        1. Ask tier_manager which addresses to fetch this cycle.
        2. Fetch perp state + spot USDC for all of them in parallel batches.
        3. For VIP+T1 only, fetch HIP-3 state across all active dexes.
        4. Parse positions + account_data.
        5. Persist to perp_snapshots and perp_account_snapshots.
        6. Return whale_states for downstream analyzers.

        Returns the same whale_states shape that LiquidationTracker
        previously produced — so the analyzers consuming this data
        don't need to change.

        Args:
            prices: Optional price dict. Not used by collect itself; kept
                in the signature so the call site can stay symmetric with
                older take_snapshot_async signatures. Pass it if you have
                it; ignore otherwise.
        """
        timestamp = datetime.now()
        cycle = self.tier_manager.get_current_cycle()

        addresses = self.tier_manager.get_all_addresses_for_current_cycle()
        addresses_by_tier = self.tier_manager.get_addresses_for_current_cycle()

        if not addresses:
            logger.warning("No addresses to fetch for this cycle")
            self.cycle_count += 1
            return []

        tier_summary = {k: len(v) for k, v in addresses_by_tier.items() if v}
        hip3_tag = " +HIP3" if self.hip3_tracking_enabled else ""
        logger.info(
            f"📊 Collector cycle {cycle}/60{hip3_tag} | Addresses: {tier_summary}"
        )

        start_time = datetime.now()
        whale_states = await self._fetch_whale_states_async(addresses)
        fetch_elapsed = (datetime.now() - start_time).total_seconds()

        logger.info(
            f"Fetched {len(whale_states)}/{len(addresses)} states in {fetch_elapsed:.1f}s"
        )

        if not whale_states:
            self.cycle_count += 1
            return []

        # =====================================================================
        # PERSIST — perp_snapshots + perp_account_snapshots
        # =====================================================================
        snapshot_time = timestamp.isoformat()

        # Flatten all positions into rows for save_perp_snapshots_batch.
        # Matches the shape LiquidationTracker's dual-write produced today.
        all_positions = self._extract_positions(whale_states)
        if all_positions:
            try:
                self.storage.save_perp_snapshots_batch(snapshot_time, all_positions)
                logger.debug(
                    f"Persisted {len(all_positions)} perp positions to perp_snapshots"
                )
            except Exception as e:
                logger.error(f"Failed to persist perp_snapshots batch: {e}")
                self.errors_count += 1

        # Account data — one row per address, HIP-3 consolidated across dexes
        account_data_list = self._parse_account_data(whale_states)
        if account_data_list:
            try:
                self.storage.save_perp_account_snapshots_batch(
                    snapshot_time, account_data_list
                )
                logger.debug(
                    f"Persisted {len(account_data_list)} account snapshots"
                )
            except Exception as e:
                logger.error(f"Failed to persist perp_account_snapshots batch: {e}")
                self.errors_count += 1

        self.cycle_count += 1
        self.cycle_addresses_fetched += len(whale_states)

        return whale_states

    # =========================================================================
    # PUBLIC API — FETCH-AND-PERSIST PATH (Step 4a, for order-end)
    # =========================================================================

    async def fetch_and_persist(
            self,
            address: str,
            session,
    ) -> bool:
        """
        Fetch a single address's full state, then persist. No threshold check.

        Used by main.py's order-end path. An active whale's TWAP order just
        ended; we want to capture their state now regardless of portfolio
        size (a whale exiting a position might drop below $50K mid-close,
        and we still want the final snapshot).

        This duplicates some parsing logic from Discovery._fetch_full_state_async
        intentionally: Discovery threshold-checks (returns None for < $50K),
        which is wrong for order-end. Keeping the paths separate avoids a
        silent behavior change.

        Args:
            address: Wallet address
            session: aiohttp.ClientSession (passed from caller's `async with`)

        Returns:
            True if persisted, False if API failure or sanity check rejected.
        """
        state = await self._fetch_one_full_state(address, session)
        if state is None:
            return False

        return await self.persist(address, state)

    async def _fetch_one_full_state(self, address: str, session) -> Optional[WhaleState]:
        """
        Fetch perp + spot + vault (+ HIP-3 if enabled) for a single address,
        parse into a WhaleState. Returns None if all core APIs failed.

        No threshold check — caller decides what to do with the result.

        Parsing logic mirrors Discovery._fetch_full_state_async to keep the
        per-address shape consistent across cold-start and order-end paths.
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

        if all(isinstance(r, Exception) or not r
               for r in (state_result, spot_result, vault_result)):
            logger.warning(
                f"All core APIs failed for {address[:10]}..., skipping order-end snapshot"
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

        # Mainnet perp
        if state_result and not isinstance(state_result, Exception):
            try:
                self._parse_perp_for_persist(
                    state_result, portfolio_data, positions, account_data
                )
            except Exception as e:
                logger.warning(f"Failed to parse perp state for {address}: {e}")

        # HIP-3 (additive)
        if self.hip3_tracking_enabled and hip3_dexes:
            self._parse_hip3_for_persist(
                hip3_dexes, hip3_results,
                portfolio_data, positions, account_data, address,
            )

        # Spot
        if spot_result and not isinstance(spot_result, Exception):
            try:
                self._parse_spot_for_persist(spot_result, portfolio_data, spot_balances)
            except Exception as e:
                logger.warning(f"Failed to parse spot state for {address}: {e}")

        # Vault
        if vault_result and not isinstance(vault_result, Exception):
            try:
                self._parse_vault_for_persist(vault_result, portfolio_data, vaults)
            except Exception as e:
                logger.warning(f"Failed to parse vaults for {address}: {e}")

        # Totals
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

    @staticmethod
    def _parse_perp_for_persist(state, portfolio_data, positions, account_data):
        """Parse mainnet perp into WhaleState fields. Same shape as Discovery."""
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
            })

        portfolio_data["num_positions"] = len(positions)

    @staticmethod
    def _parse_hip3_for_persist(
            hip3_dexes, hip3_results, portfolio_data, positions, account_data, address
    ):
        """Parse HIP-3 dexes into WhaleState fields. Same shape as Discovery."""
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
                hip3_margin = hip3_result.get("marginSummary", {})
                hip3_account_value = float(hip3_margin.get("accountValue", 0))
                if hip3_account_value > 0:
                    portfolio_data["perp_value"] += hip3_account_value

                hip3_acc_total += hip3_account_value
                hip3_raw_total += float(hip3_margin.get("totalRawUsd", 0))
                hip3_margin_total += float(hip3_margin.get("totalMarginUsed", 0))
                hip3_ntl_total += float(hip3_margin.get("totalNtlPos", 0))

                hip3_w = hip3_result.get("withdrawable")
                if hip3_w is not None:
                    hip3_withdrawable_total += float(hip3_w)

                hip3_dexes_present.append(dex)

                for pos_data in hip3_result.get("assetPositions", []):
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
                    })
                    hip3_pos_count += 1

            except Exception as e:
                logger.warning(f"Failed to parse HIP-3 '{dex}' state for {address}: {e}")

        if hip3_pos_count > 0:
            portfolio_data["num_positions"] = len(positions)

        if hip3_dexes_present:
            account_data["hip3_account_value"] = hip3_acc_total
            account_data["hip3_total_raw_usd"] = hip3_raw_total
            account_data["hip3_total_margin_used"] = hip3_margin_total
            account_data["hip3_total_ntl_pos"] = hip3_ntl_total
            account_data["hip3_withdrawable"] = hip3_withdrawable_total
            account_data["hip3_dexes"] = ",".join(hip3_dexes_present)

    def _parse_spot_for_persist(self, spot_state, portfolio_data, spot_balances):
        """Parse spot balances. Uses TokenFilter for inclusion rules."""
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

    @staticmethod
    def _parse_vault_for_persist(vault_equities, portfolio_data, vaults):
        """Parse vault holdings."""
        for vault_eq in vault_equities:
            equity = float(vault_eq.get("equity", 0))
            if equity <= 0:
                continue
            portfolio_data["vault_value"] += equity
            vaults.append({
                "vault_address": vault_eq.get("vaultAddress", ""),
                "value": equity,
            })

    # =========================================================================
    # PUBLIC API — SLOW LADDER (Step 4b)
    # =========================================================================

    async def collect_slow_async(self) -> int:
        """
        Slow-ladder fetch + write for portfolio/spot/vault snapshots.

        Cadence (per-tier hours):
            VIP+T1: 1h   T2: 2h   T3: 4h   T4+T5: 8h

        Fires only when the current cycle hits SLOW_LADDER_ANCHOR_CYCLE (7)
        AND the current hour is divisible by the tier's slow-ladder hour count.
        Cycle 7 is chosen because no fast-ladder tier divides 7, so slow
        ladder doesn't stack with the cycle-5/15/30/60 fast-ladder spikes.

        Independent fetch from fast ladder — duplicates the perp fetch on
        slow-ladder cycles. Accepted cost for simpler design (no cross-cycle
        state sharing required).

        Writes: portfolio_snapshots, spot_snapshots, vault_snapshots,
                perp_snapshots, perp_account_snapshots
        Returns: number of addresses written. 0 if nothing was due this cycle.
        """
        cycle_in_hour = self.tier_manager.get_current_cycle()

        # Wrong cycle position → nothing to do
        if cycle_in_hour != self.SLOW_LADDER_ANCHOR_CYCLE:
            return 0

        hour_count = self.tier_manager.cycle_count // 60

        # Determine which tiers are due this hour
        due_addresses = self._collect_slow_ladder_addresses(hour_count)
        if not due_addresses:
            logger.debug(
                f"Slow ladder check at cycle {cycle_in_hour}: no tiers due "
                f"(hour {hour_count})"
            )
            return 0

        logger.info(
            f"🐢 Slow ladder firing at cycle {cycle_in_hour} (hour {hour_count}): "
            f"{len(due_addresses)} addresses across due tiers"
        )

        start_time = datetime.now()
        snapshot_time = start_time.isoformat()
        written = 0
        errors = 0

        # Fetch each address fresh — perp + spot + vault + HIP-3 if enabled.
        # Reuses fetch_and_persist via the per-address path, which already
        # writes all five tables and runs sanity checks.
        async with aiohttp.ClientSession() as session:
            for address in due_addresses:
                try:
                    success = await self.fetch_and_persist(address, session)
                    if success:
                        written += 1
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1
                    logger.warning(
                        f"Slow ladder fetch_and_persist failed for {address[:10]}...: {e}"
                    )

        elapsed = (datetime.now() - start_time).total_seconds()

        self.slow_ladder_runs += 1
        self.slow_ladder_addresses += written
        self.slow_ladder_errors += errors

        logger.info(
            f"🐢 Slow ladder complete: {written}/{len(due_addresses)} written, "
            f"{errors} errors ({elapsed:.1f}s)"
        )

        return written

    def _collect_slow_ladder_addresses(self, hour_count: int) -> List[str]:
        """
        Build the deduplicated list of addresses due this hour.

        For each tier, check if hour_count is divisible by the tier's
        slow-ladder hour count. If so, include all addresses in that tier.

        VIP is treated as a separate "tier" using the same cadence as T1.
        """
        all_addresses = []
        seen = set()

        for tier_key, hours in self.SLOW_LADDER_HOURS.items():
            if hour_count % hours != 0:
                continue

            # Fetch addresses for this tier
            if tier_key == 'vip':
                addrs = self.storage.get_vip_address_list()
            else:
                addrs = self.storage.get_addresses_by_tier(tier_key)

            tier_label = "VIP" if tier_key == 'vip' else f"T{tier_key}"
            logger.debug(
                f"Slow ladder including {tier_label} ({len(addrs)} addresses, "
                f"every {hours}h)"
            )

            for a in addrs:
                if a not in seen:
                    all_addresses.append(a)
                    seen.add(a)

        return all_addresses

    # =========================================================================
    # INTERNAL: FETCH (lifted from LiquidationTracker, unchanged in behavior)
    # =========================================================================

    async def _fetch_whale_states_async(self, addresses: List[str]) -> List[Dict]:
        """
        Fetch perp + spot USDC + HIP-3 (for VIP+T1) in batches.

        Returns list of whale_state dicts. Each dict contains:
        - address: str
        - state: clearinghouseState dict (mainnet perp)
        - spot_usdc: float (USDC available, total - hold)
        - hip3_positions: list of raw HIP-3 position dicts (empty if not VIP+T1)
        - hip3_margin_by_dex: dict {dex: marginSummary} (only for VIP+T1)
        """
        if not addresses:
            return []

        connector = aiohttp.TCPConnector(limit=20)
        all_results = []
        total_batches = (len(addresses) + self.batch_size - 1) // self.batch_size

        async with aiohttp.ClientSession(connector=connector) as session:
            # STEP 1: main perp + spot for all addresses
            for i in range(0, len(addresses), self.batch_size):
                batch = addresses[i:i + self.batch_size]
                batch_num = i // self.batch_size + 1

                logger.debug(
                    f"Fetching batch {batch_num}/{total_batches} ({len(batch)} addresses)"
                )

                perp_tasks = [self._get_user_state_async(session, addr) for addr in batch]
                perp_results = await asyncio.gather(*perp_tasks)

                spot_tasks = [self._get_user_spot_state_async(session, addr) for addr in batch]
                spot_results = await asyncio.gather(*spot_tasks)

                spot_by_addr = {r["address"]: r["spot"] for r in spot_results if r}
                for perp in perp_results:
                    if perp:
                        perp["spot_usdc"] = self._parse_spot_usdc(
                            spot_by_addr.get(perp["address"])
                        )
                        perp["hip3_positions"] = []
                        all_results.append(perp)

                if i + self.batch_size < len(addresses):
                    await asyncio.sleep(self.batch_delay)

            # STEP 2: HIP-3 for VIP+T1 only
            if self.hip3_tracking_enabled and all_results:
                hip3_dexes = self.hl_client.get_active_hip3_dexes()
                if hip3_dexes:
                    flagged = self.storage.get_hip3_flagged_addresses()
                    fetched_addresses = [
                        r["address"] for r in all_results
                        if (self.tier_manager
                            and self.tier_manager.is_event_tracking_address(r["address"]))
                        or r["address"] in flagged
                    ]
                    hip3_total = 0

                    for dex in hip3_dexes:
                        try:
                            positions_by_addr, margin_by_addr = await self._fetch_hip3_positions_batch(
                                session, fetched_addresses, dex
                            )

                            results_by_addr = {r["address"]: r for r in all_results}

                            for addr, margin in margin_by_addr.items():
                                if addr in results_by_addr:
                                    results_by_addr[addr].setdefault(
                                        "hip3_margin_by_dex", {}
                                    )[dex] = margin

                            if positions_by_addr:
                                dex_pos_count = sum(len(v) for v in positions_by_addr.values())
                                hip3_total += dex_pos_count
                                for addr, positions in positions_by_addr.items():
                                    if addr in results_by_addr:
                                        results_by_addr[addr]["hip3_positions"].extend(positions)
                                logger.debug(
                                    f"HIP-3 '{dex}': {dex_pos_count} positions "
                                    f"across {len(positions_by_addr)} whales"
                                )

                        except Exception as e:
                            self.hip3_fetch_errors += 1
                            logger.warning(f"HIP-3 '{dex}' batch fetch failed: {e}")
                            continue

                    if hip3_total > 0:
                        self.hip3_positions_found += hip3_total
                        logger.info(
                            f"🏗️  HIP-3: {hip3_total} positions found "
                            f"across {len(hip3_dexes)} dexes"
                        )

        success_rate = len(all_results) / len(addresses) * 100 if addresses else 0
        logger.debug(
            f"Fetched {len(all_results)}/{len(addresses)} whale states "
            f"({success_rate:.1f}% success)"
        )
        return all_results

    async def _get_user_state_async(
            self, session: aiohttp.ClientSession, address: str
    ) -> Optional[Dict]:
        """Fetch user perp state."""
        payload = {"type": "clearinghouseState", "user": address}
        try:
            async with session.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"address": address, "state": data}
                logger.warning(f"Failed to fetch state for {address}: {response.status}")
                return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching state for {address}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching state for {address}: {e}")
            return None

    async def _get_user_spot_state_async(
            self, session: aiohttp.ClientSession, address: str
    ) -> Optional[Dict]:
        """Fetch user spot state."""
        payload = {"type": "spotClearinghouseState", "user": address}
        try:
            async with session.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"address": address, "spot": data}
                logger.warning(f"Failed to fetch spot for {address}: {response.status}")
                return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching spot for {address}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching spot for {address[:10]}: {e}")
            return None

    async def _get_user_hip3_state_async(
            self, session: aiohttp.ClientSession, address: str, dex: str
    ) -> Optional[Dict]:
        """Fetch user state for a specific HIP-3 dex."""
        payload = {"type": "clearinghouseState", "user": address, "dex": dex}
        try:
            async with session.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"address": address, "dex": dex, "state": data}
                logger.debug(
                    f"HIP-3 '{dex}' fetch failed for {address[:10]}...: HTTP {response.status}"
                )
                return None
        except asyncio.TimeoutError:
            logger.debug(f"HIP-3 '{dex}' timeout for {address[:10]}...")
            return None
        except Exception as e:
            logger.debug(f"HIP-3 '{dex}' error for {address[:10]}...: {e}")
            return None

    async def _fetch_hip3_positions_batch(
            self, session: aiohttp.ClientSession, addresses: List[str], dex: str
    ) -> tuple:
        """
        Fetch HIP-3 positions and marginSummary for a batch of addresses on one dex.

        Returns (positions_by_addr, margin_by_addr) tuple.
        """
        positions_by_addr = {}
        margin_by_addr = {}

        for i in range(0, len(addresses), self.hip3_batch_size):
            batch = addresses[i:i + self.hip3_batch_size]
            tasks = [self._get_user_hip3_state_async(session, addr, dex) for addr in batch]
            results = await asyncio.gather(*tasks)

            for result in results:
                if not result:
                    continue

                address = result["address"]
                state = result.get("state", {})

                margin_summary = state.get("marginSummary", {})
                if margin_summary:
                    margin_by_addr[address] = {
                        "accountValue": margin_summary.get("accountValue", "0"),
                        "totalRawUsd": margin_summary.get("totalRawUsd", "0"),
                        "totalMarginUsed": margin_summary.get("totalMarginUsed", "0"),
                        "totalNtlPos": margin_summary.get("totalNtlPos", "0"),
                        "withdrawable": state.get("withdrawable"),
                    }

                for pos_data in state.get("assetPositions", []):
                    position = pos_data.get("position", {})
                    size = float(position.get("szi", 0))
                    if size == 0:
                        continue

                    coin = position.get("coin", "")
                    if ":" not in coin:
                        coin = f"{dex}:{coin}"

                    positions_by_addr.setdefault(address, []).append({
                        "coin": coin,
                        "szi": position.get("szi", "0"),
                        "entryPx": position.get("entryPx", "0"),
                        "liquidationPx": position.get("liquidationPx"),
                        "leverage": position.get("leverage", {}),
                        "marginUsed": position.get("marginUsed", "0"),
                        "unrealizedPnl": position.get("unrealizedPnl", "0"),
                        "positionValue": position.get("positionValue", "0"),
                        "cumFunding": position.get("cumFunding", {}),
                    })

            if i + self.hip3_batch_size < len(addresses):
                await asyncio.sleep(self.hip3_batch_delay)

        return positions_by_addr, margin_by_addr

    # =========================================================================
    # INTERNAL: PARSING (lifted from LiquidationTracker, unchanged in behavior)
    # =========================================================================

    @staticmethod
    def _parse_spot_usdc(spot_state: Optional[Dict]) -> float:
        """Extract available USDC (total - hold) from spot state."""
        if not spot_state:
            return 0.0
        balances = spot_state.get("balances", [])
        usdc = next((b for b in balances if b["coin"] == "USDC"), None)
        if usdc:
            total = float(usdc.get("total", 0))
            hold = float(usdc.get("hold", 0))
            return total - hold
        return 0.0

    def _extract_positions(self, whale_states: List[Dict]) -> List[Dict]:
        """
        Extract all positions (main perp + HIP-3) into the flat list shape
        that save_perp_snapshots_batch expects.
        """
        all_positions = []

        for whale_data in whale_states:
            address = whale_data.get("address", "")
            state = whale_data.get("state", {})

            # Main perp positions
            for pos_data in state.get("assetPositions", []):
                pos = pos_data.get("position", {})
                coin = pos.get("coin", "")
                size = float(pos.get("szi", 0))
                if not coin or size == 0:
                    continue
                all_positions.append(self._build_position_row(address, coin, pos))

            # HIP-3 positions (already have dex-prefixed coin names)
            for hip3_pos in whale_data.get("hip3_positions", []):
                coin = hip3_pos.get("coin", "")
                size = float(hip3_pos.get("szi", 0))
                if not coin or size == 0:
                    continue
                all_positions.append(self._build_position_row(address, coin, hip3_pos))

        return all_positions

    @staticmethod
    def _build_position_row(address: str, coin: str, pos: Dict) -> Dict:
        """Common row builder for both main perp and HIP-3 positions."""
        size = float(pos.get("szi", 0))
        side = "LONG" if size > 0 else "SHORT"
        leverage_data = pos.get("leverage", {})
        leverage = leverage_data.get("value", 0) if isinstance(leverage_data, dict) else 0

        return {
            "address": address,
            "coin": coin,
            "side": side,
            "size": abs(size),
            "entry_price": float(pos.get("entryPx", 0)),
            "liq_price": float(pos.get("liquidationPx") or 0),
            "leverage": leverage,
            "margin_used": float(pos.get("marginUsed", 0)),
            "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
        }

    def _parse_account_data(self, whale_states: List[Dict]) -> List[Dict]:
        """
        Build per-address account_data list for save_perp_account_snapshots_batch.

        Consolidates HIP-3 marginSummary across dexes for whales who have
        hip3_margin_by_dex populated (VIP+T1 only — gated by collect_async).
        Lower-tier whales get NULL HIP-3 columns automatically.
        """
        account_data_list = []

        for whale_data in whale_states:
            address = whale_data.get("address", "")
            if not address:
                continue

            state = whale_data.get("state", {})
            margin_summary = state.get("marginSummary", {})

            account_data = {
                "address": address,
                "account_value": float(margin_summary.get("accountValue", 0)),
                "total_raw_usd": float(margin_summary.get("totalRawUsd", 0)),
                "total_margin_used": float(margin_summary.get("totalMarginUsed", 0)),
                "total_ntl_pos": float(margin_summary.get("totalNtlPos", 0)),
                "withdrawable": None,
                "hip3_account_value": None,
                "hip3_total_raw_usd": None,
                "hip3_total_margin_used": None,
                "hip3_total_ntl_pos": None,
                "hip3_withdrawable": None,
                "hip3_dexes": None,
            }

            withdrawable_raw = state.get("withdrawable")
            if withdrawable_raw is not None:
                account_data["withdrawable"] = float(withdrawable_raw)

            hip3_margin_by_dex = whale_data.get("hip3_margin_by_dex")
            if hip3_margin_by_dex:
                hip3_acc_total = 0.0
                hip3_raw_total = 0.0
                hip3_margin_total = 0.0
                hip3_ntl_total = 0.0
                hip3_withdrawable_total = 0.0
                dexes_present = []

                for dex, margin in hip3_margin_by_dex.items():
                    hip3_acc_total += float(margin.get("accountValue", 0))
                    hip3_raw_total += float(margin.get("totalRawUsd", 0))
                    hip3_margin_total += float(margin.get("totalMarginUsed", 0))
                    hip3_ntl_total += float(margin.get("totalNtlPos", 0))
                    w_raw = margin.get("withdrawable")
                    if w_raw is not None:
                        hip3_withdrawable_total += float(w_raw)
                    dexes_present.append(dex)

                if dexes_present:
                    account_data["hip3_account_value"] = hip3_acc_total
                    account_data["hip3_total_raw_usd"] = hip3_raw_total
                    account_data["hip3_total_margin_used"] = hip3_margin_total
                    account_data["hip3_total_ntl_pos"] = hip3_ntl_total
                    account_data["hip3_withdrawable"] = hip3_withdrawable_total
                    account_data["hip3_dexes"] = ",".join(dexes_present)

            account_data_list.append(account_data)

        return account_data_list

    # =========================================================================
    # INTERNAL: SANITY CHECKS (cold-start path only)
    # =========================================================================

    def _passes_sanity_checks(self, address: str, state: WhaleState) -> bool:
        """All-zeros + vanish guards. See class docstring."""
        perp_value = state.portfolio_data.get("perp_value", 0)
        spot_value = state.portfolio_data.get("spot_value", 0)
        vault_value = state.portfolio_data.get("vault_value", 0)

        if perp_value == 0 and spot_value == 0 and vault_value == 0:
            logger.warning(
                f"Skipping persist for {address[:10]}... — "
                f"all values zero (likely API failure)"
            )
            self.skipped_api_failure += 1
            return False

        self.storage.cursor.execute(
            """
            SELECT spot_value, vault_value, perp_value
            FROM portfolio_snapshots
            WHERE address = ?
            ORDER BY snapshot_time DESC LIMIT 1
            """,
            (address,),
        )
        prev_snap = self.storage.cursor.fetchone()

        if prev_snap is not None:
            prev_spot, prev_vault, prev_perp = prev_snap
            if prev_perp > 0:
                perp_change_pct = abs(perp_value - prev_perp) / prev_perp
                if perp_change_pct < 0.05:
                    if prev_spot > 100_000 and spot_value < 1_000:
                        logger.warning(
                            f"Skipping persist for {address[:10]}... — "
                            f"spot vanished (${prev_spot:,.0f} → ${spot_value:,.0f})"
                        )
                        self.skipped_partial_failure += 1
                        return False
                    if prev_vault > 100_000 and vault_value < 1_000:
                        logger.warning(
                            f"Skipping persist for {address[:10]}... — "
                            f"vault vanished (${prev_vault:,.0f} → ${vault_value:,.0f})"
                        )
                        self.skipped_partial_failure += 1
                        return False

        return True

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> dict:
        return {
            "snapshots_taken": self.snapshots_taken,
            "errors": self.errors_count,
            "skipped_api_failure": self.skipped_api_failure,
            "skipped_partial_failure": self.skipped_partial_failure,
            "cycle_count": self.cycle_count,
            "cycle_addresses_fetched": self.cycle_addresses_fetched,
            "hip3_positions_found": self.hip3_positions_found,
            "hip3_fetch_errors": self.hip3_fetch_errors,
            "slow_ladder_runs": self.slow_ladder_runs,
            "slow_ladder_addresses": self.slow_ladder_addresses,
            "slow_ladder_errors": self.slow_ladder_errors,
            "min_portfolio_value": self.min_portfolio_value,
            "hip3_tracking_enabled": self.hip3_tracking_enabled,
        }