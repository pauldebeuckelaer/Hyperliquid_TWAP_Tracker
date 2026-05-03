#!/usr/bin/env python3
"""
Liquidation Tracker
===================
Tracks whale liquidation exposure with tiered address fetching.

Features:
- Tiered fetching to respect API limits
- VIP + Tier1 every cycle (1 min)
- Lower tiers on longer intervals
- Event detection for VIP + Tier1 only
- Saves to liquidation_snapshots table
- Dedicated log files for coin-level and address-level liquidation data
- HIP-3 position tracking (tokenized stocks, commodities, etc.)

Usage:
    from liquidation_tracker import LiquidationTracker
    from tier_manager import TierManager
    from whale_event_detector import WhaleEventDetector

    tier_mgr = TierManager(storage)
    event_detector = WhaleEventDetector(tier_mgr)
    liq_tracker = LiquidationTracker(hl_client, storage, tier_mgr, event_detector)

    # Each cycle:
    tier_mgr.increment_cycle()
    result = await liq_tracker.take_snapshot_async()
"""
import asyncio
import aiohttp
import logging
from datetime import datetime
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)
liq_coins_logger = logging.getLogger(f"{__name__}.coins")
liq_addrs_logger = logging.getLogger(f"{__name__}.addresses")


class LiquidationTracker:
    """
    Tracks whale liquidation exposure with tiered fetching.

    Uses TierManager to determine which addresses to fetch each cycle.
    Uses WhaleEventDetector to detect position/margin changes for VIP + Tier1.
    """

    def __init__(
            self,
            hl_client,
            storage,
            tier_manager,
            event_detector,
            config: Optional[Dict] = None
    ):
        """
        Initialize liquidation tracker.

        Args:
            hl_client: HyperliquidClient instance for API calls
            storage: SQLiteBackend instance
            tier_manager: TierManager instance for address selection
            event_detector: WhaleEventDetector instance for event detection
            config: Optional configuration dict
        """
        self.client = hl_client
        self.storage = storage
        self.tier_manager = tier_manager
        self.event_detector = event_detector
        self.config = config or {}

        # API endpoint for async calls
        self.api_url = "https://api.hyperliquid.xyz/info"

        # Batch settings
        self.batch_size = self.config.get('batch_size', 50)
        self.batch_delay = self.config.get('batch_delay', 0.5)

        # =====================================================================
        # HIP-3 CONFIG
        # =====================================================================
        self.hip3_tracking_enabled = self.config.get('hip3_tracking_enabled', False)
        self.hip3_batch_size = self.config.get('hip3_batch_size', 50)
        self.hip3_batch_delay = self.config.get('hip3_batch_delay', 0.5)
        # =====================================================================

        # Stats
        self.snapshot_count = 0
        self.last_snapshot_time: Optional[datetime] = None
        # HIP-3 stats
        self._hip3_positions_found = 0
        self._hip3_fetch_errors = 0

        if self.hip3_tracking_enabled:
            logger.info(f"LiquidationTracker initialized (batch_size={self.batch_size}, HIP-3=ON)")
        else:
            logger.info(f"LiquidationTracker initialized (batch_size={self.batch_size}, HIP-3=OFF)")

    # =========================================================================
    # ASYNC API METHODS
    # =========================================================================

    async def _get_user_state_async(
            self,
            session: aiohttp.ClientSession,
            address: str
    ) -> Optional[Dict]:
        """
        Fetch user perp state asynchronously.

        Args:
            session: aiohttp session
            address: User address

        Returns:
            User state dict or None on error
        """
        payload = {"type": "clearinghouseState", "user": address}

        try:
            async with session.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"address": address, "state": data}
                else:
                    logger.warning(f"Failed to fetch state for {address}: {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching state for {address}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching state for {address}: {e}")
            return None

    async def _get_user_spot_state_async(
            self,
            session: aiohttp.ClientSession,
            address: str
    ) -> Optional[Dict]:
        """
        Fetch user spot state asynchronously.

        Args:
            session: aiohttp session
            address: User address

        Returns:
            Spot state dict or None on error
        """
        payload = {"type": "spotClearinghouseState", "user": address}

        try:
            async with session.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"address": address, "spot": data}
                else:
                    logger.warning(f"Failed to fetch spot for {address}: {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching spot for {address}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching spot for {address[:10]}: {e}")
            return None

    # =========================================================================
    # HIP-3 ASYNC FETCH (NEW)
    # =========================================================================

    async def _get_user_hip3_state_async(
            self,
            session: aiohttp.ClientSession,
            address: str,
            dex: str
    ) -> Optional[Dict]:
        """
        Fetch user's clearinghouseState for a specific HIP-3 dex.

        Args:
            session: aiohttp session
            address: User address
            dex: HIP-3 dex name (e.g., "xyz", "flx")

        Returns:
            {"address": address, "dex": dex, "state": <clearinghouseState>}
            or None on error
        """
        payload = {"type": "clearinghouseState", "user": address, "dex": dex}

        try:
            async with session.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"address": address, "dex": dex, "state": data}
                else:
                    logger.debug(f"HIP-3 '{dex}' fetch failed for {address[:10]}...: HTTP {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.debug(f"HIP-3 '{dex}' timeout for {address[:10]}...")
            return None
        except Exception as e:
            logger.debug(f"HIP-3 '{dex}' error for {address[:10]}...: {e}")
            return None

    async def _fetch_hip3_positions_batch(
            self,
            session: aiohttp.ClientSession,
            addresses: List[str],
            dex: str
    ) -> tuple:
        """
        Fetch HIP-3 positions and marginSummary for a batch of addresses on a single dex.

        Args:
            session: aiohttp session
            addresses: List of whale addresses
            dex: HIP-3 dex name

        Returns:
            Tuple of (positions_by_addr, margin_by_addr):
              - positions_by_addr: Dict[address, List[position_dict]]
                Addresses with no positions are omitted.
              - margin_by_addr: Dict[address, marginSummary_dict]
                Includes top-level 'withdrawable' merged in.
                Addresses where the API failed are omitted.
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

                # Capture marginSummary + withdrawable for account_data
                margin_summary = state.get("marginSummary", {})
                if margin_summary:
                    margin_by_addr[address] = {
                        "accountValue": margin_summary.get("accountValue", "0"),
                        "totalRawUsd": margin_summary.get("totalRawUsd", "0"),
                        "totalMarginUsed": margin_summary.get("totalMarginUsed", "0"),
                        "totalNtlPos": margin_summary.get("totalNtlPos", "0"),
                        "withdrawable": state.get("withdrawable"),
                    }

                # Existing position parsing
                asset_positions = state.get("assetPositions", [])

                for pos_data in asset_positions:
                    position = pos_data.get("position", {})
                    size = float(position.get("szi", 0))

                    if size == 0:
                        continue

                    coin = position.get("coin", "")

                    # Ensure dex prefix — API should return "xyz:TSLA" but
                    # we add it defensively if missing
                    if ":" not in coin:
                        coin = f"{dex}:{coin}"

                    if address not in positions_by_addr:
                        positions_by_addr[address] = []

                    positions_by_addr[address].append({
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

            # Delay between batches
            if i + self.hip3_batch_size < len(addresses):
                await asyncio.sleep(self.hip3_batch_delay)

        return positions_by_addr, margin_by_addr

    # =========================================================================
    # MAIN FETCH METHOD (UPDATED)
    # =========================================================================

    async def _fetch_whale_states_async(self, addresses: List[str]) -> List[Dict]:
        """
        Fetch whale states (perp + spot + HIP-3) in batches.

        Args:
            addresses: List of whale addresses to fetch

        Returns:
            List of state dicts (excluding failures).
            Each dict contains:
            - address: str
            - state: main perp clearinghouseState
            - spot_usdc: float
            - hip3_positions: list of raw position dicts (if HIP-3 enabled)
        """
        if not addresses:
            return []

        connector = aiohttp.TCPConnector(limit=20)
        all_results = []
        total_batches = (len(addresses) + self.batch_size - 1) // self.batch_size

        async with aiohttp.ClientSession(connector=connector) as session:
            # =================================================================
            # STEP 1: Fetch main perp + spot states (existing logic)
            # =================================================================
            for i in range(0, len(addresses), self.batch_size):
                batch = addresses[i:i + self.batch_size]
                batch_num = i // self.batch_size + 1

                logger.debug(f"Fetching batch {batch_num}/{total_batches} ({len(batch)} addresses)")

                # Fetch perp states for batch
                perp_tasks = [self._get_user_state_async(session, addr) for addr in batch]
                perp_results = await asyncio.gather(*perp_tasks)

                # Fetch spot states for batch
                spot_tasks = [self._get_user_spot_state_async(session, addr) for addr in batch]
                spot_results = await asyncio.gather(*spot_tasks)

                # Combine perp + spot
                spot_by_addr = {r['address']: r['spot'] for r in spot_results if r}
                for perp in perp_results:
                    if perp:
                        perp['spot_usdc'] = self._parse_spot_usdc(spot_by_addr.get(perp['address']))
                        perp['hip3_positions'] = []  # Placeholder, filled in step 2
                        all_results.append(perp)

                # Delay between batches (skip on last batch)
                if i + self.batch_size < len(addresses):
                    await asyncio.sleep(self.batch_delay)

            # =================================================================
            # STEP 2: Fetch HIP-3 positions (NEW — additive)
            # =================================================================
            if self.hip3_tracking_enabled and all_results:
                hip3_dexes = self.client.get_active_hip3_dexes()

                if hip3_dexes:
                    # Get addresses that succeeded in step 1
                    # HIP-3 restricted to VIP + Tier1 only to avoid rate limit explosion
                    fetched_addresses = [
                        r['address'] for r in all_results
                        if self.tier_manager and self.tier_manager.is_event_tracking_address(r['address'])
                    ]
                    hip3_total_positions = 0

                    for dex in hip3_dexes:
                        try:
                            positions_by_addr, margin_by_addr = await self._fetch_hip3_positions_batch(
                                session, fetched_addresses, dex
                            )

                            # Merge marginSummary into all_results (for account_data)
                            results_by_addr = {r['address']: r for r in all_results}
                            for addr, margin in margin_by_addr.items():
                                if addr in results_by_addr:
                                    if 'hip3_margin_by_dex' not in results_by_addr[addr]:
                                        results_by_addr[addr]['hip3_margin_by_dex'] = {}
                                    results_by_addr[addr]['hip3_margin_by_dex'][dex] = margin

                            if positions_by_addr:
                                dex_pos_count = sum(len(v) for v in positions_by_addr.values())
                                hip3_total_positions += dex_pos_count

                                # Merge positions into existing results
                                for addr, positions in positions_by_addr.items():
                                    if addr in results_by_addr:
                                        results_by_addr[addr]['hip3_positions'].extend(positions)

                                logger.debug(
                                    f"HIP-3 '{dex}': {dex_pos_count} positions "
                                    f"across {len(positions_by_addr)} whales"
                                )

                        except Exception as e:
                            self._hip3_fetch_errors += 1
                            logger.warning(f"HIP-3 '{dex}' batch fetch failed: {e}")
                            continue

                    if hip3_total_positions > 0:
                        self._hip3_positions_found += hip3_total_positions
                        logger.info(
                            f"🏗️  HIP-3: {hip3_total_positions} positions found "
                            f"across {len(hip3_dexes)} dexes"
                        )

        success_rate = len(all_results) / len(addresses) * 100 if addresses else 0
        logger.debug(f"Fetched {len(all_results)}/{len(addresses)} whale states ({success_rate:.1f}% success)")

        return all_results

    def _parse_spot_usdc(self, spot_state: Dict) -> float:
        """
        Extract available USDC from spot state.

        Args:
            spot_state: Spot clearinghouse state dict

        Returns:
            Available USDC (total - hold)
        """
        if not spot_state:
            return 0.0

        balances = spot_state.get('balances', [])
        usdc = next((b for b in balances if b['coin'] == 'USDC'), None)

        if usdc:
            total = float(usdc.get('total', 0))
            hold = float(usdc.get('hold', 0))
            return total - hold

        return 0.0

    # =========================================================================
    # LIQUIDATION PARSING (REFACTORED)
    # =========================================================================

    def _calculate_distance_to_liq(self, current_price: float, liq_price: float, side: str) -> float:
        """
        Calculate percentage distance to liquidation.

        Args:
            current_price: Current mark price
            liq_price: Liquidation price
            side: 'LONG' or 'SHORT'

        Returns:
            Percentage distance (positive = safe, negative = past liq)
        """
        if current_price == 0 or liq_price == 0:
            return 100.0  # No valid data

        if side == 'LONG':
            # Long gets liquidated when price drops to liq_price
            distance = (current_price - liq_price) / current_price * 100
        else:
            # Short gets liquidated when price rises to liq_price
            distance = (liq_price - current_price) / current_price * 100

        return distance

    def _add_position_to_exposure(
            self,
            coin_exposure: Dict,
            address: str,
            coin: str,
            pos: Dict,
            prices: Dict,
            account_value: float,
            total_margin_used: float,
            withdrawable: float,
            spot_usdc: float
    ):
        """
        Add a single position to the coin_exposure aggregation.

        Extracted to avoid duplicating logic for main perp and HIP-3 positions.

        Args:
            coin_exposure: Mutable dict being built up
            address: Whale address
            coin: Coin name (e.g., "BTC" or "xyz:TSLA")
            pos: Raw position dict from API
            prices: Price lookup dict
            account_value: Whale's main perp account value
            total_margin_used: Whale's total margin used
            withdrawable: Whale's withdrawable amount
            spot_usdc: Whale's spot USDC balance
        """
        size = float(pos.get('szi', 0))
        if size == 0:
            return

        # Position data
        entry_price = float(pos.get('entryPx', 0))
        liq_price_str = pos.get('liquidationPx')
        liq_price = float(liq_price_str) if liq_price_str else 0
        position_value = float(pos.get('positionValue', 0))
        margin_used = float(pos.get('marginUsed', 0))

        # Leverage info
        leverage_data = pos.get('leverage', {})
        if isinstance(leverage_data, dict):
            leverage = leverage_data.get('value', 0)
        else:
            leverage = 0

        # PnL data
        unrealized_pnl = float(pos.get('unrealizedPnl', 0))

        # Funding data
        cum_funding = pos.get('cumFunding', {})
        funding_since_open = float(cum_funding.get('sinceOpen', 0))

        side = 'LONG' if size > 0 else 'SHORT'
        current_price = float(prices.get(coin, 0))

        # Distance to liquidation
        if current_price > 0 and liq_price > 0:
            distance = self._calculate_distance_to_liq(current_price, liq_price, side)
        else:
            distance = 100.0

        # PnL percentage (return on margin)
        pnl_pct = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0

        # Initialize coin entry if needed
        if coin not in coin_exposure:
            coin_exposure[coin] = {
                'total_value': 0,
                'long_value': 0,
                'short_value': 0,
                'positions': []
            }

        # Aggregate totals
        coin_exposure[coin]['total_value'] += abs(position_value)

        if side == 'LONG':
            coin_exposure[coin]['long_value'] += position_value
        else:
            coin_exposure[coin]['short_value'] += abs(position_value)

        # Add position details
        coin_exposure[coin]['positions'].append({
            'address': address,
            'coin': coin,
            'side': side,
            'size': abs(size),
            'value': abs(position_value),
            'entry_price': entry_price,
            'current_price': current_price,
            'liq_price': liq_price,
            'margin_used': margin_used,
            'leverage': leverage,
            'unrealized_pnl': unrealized_pnl,
            'pnl_pct': pnl_pct,
            'funding_since_open': funding_since_open,
            'distance_to_liq': distance,
            'account_value': account_value,
            'account_margin_used': total_margin_used,
            'account_withdrawable': withdrawable,
            'spot_usdc': spot_usdc,
        })

    def _parse_liquidation_exposure(self, whale_states: List[Dict], prices: Dict) -> Dict:
        """
        Parse whale states and aggregate liquidation exposure by coin.

        Handles both main perp positions AND HIP-3 positions.

        Args:
            whale_states: List of whale state dicts from API
            prices: Dict of coin -> price (must include HIP-3 prices like "xyz:TSLA")

        Returns:
            Dict with per-coin exposure data including individual positions
        """
        coin_exposure = {}

        for whale_data in whale_states:
            state = whale_data.get('state', {})
            address = whale_data.get('address', '')

            # Account level data (main perp dex)
            margin_summary = state.get('marginSummary', {})
            account_value = float(margin_summary.get('accountValue', 0))
            total_margin_used = float(margin_summary.get('totalMarginUsed', 0))
            withdrawable = float(state.get('withdrawable', 0))

            spot_usdc = whale_data.get('spot_usdc', 0)

            # =================================================================
            # MAIN PERP POSITIONS (existing logic, now uses extracted method)
            # =================================================================
            positions = state.get('assetPositions', [])

            for pos_data in positions:
                pos = pos_data.get('position', {})
                coin = pos.get('coin', '')
                if not coin:
                    continue

                size = float(pos.get('szi', 0))
                if size == 0:
                    continue

                self._add_position_to_exposure(
                    coin_exposure, address, coin, pos, prices,
                    account_value, total_margin_used, withdrawable,
                    spot_usdc
                )

            # =================================================================
            # HIP-3 POSITIONS (NEW — additive)
            # =================================================================
            hip3_positions = whale_data.get('hip3_positions', [])

            for hip3_pos in hip3_positions:
                coin = hip3_pos.get('coin', '')
                if not coin:
                    continue

                size = float(hip3_pos.get('szi', 0))
                if size == 0:
                    continue

                self._add_position_to_exposure(
                    coin_exposure, address, coin, hip3_pos, prices,
                    account_value, total_margin_used, withdrawable,
                    spot_usdc
                )

        # Sort positions within each coin by distance to liquidation
        for coin in coin_exposure:
            coin_exposure[coin]['positions'].sort(key=lambda x: x['distance_to_liq'])

        return coin_exposure

    # =========================================================================
    # ACCOUNT DATA PARSING (separate from liquidation logic)
    # =========================================================================

    def _parse_account_data(self, whale_states: List[Dict]) -> List[Dict]:
        """
        Build per-address account_data list for save_perp_account_snapshots_batch.

        Consolidates HIP-3 marginSummary across dexes for VIP+T1 whales who
        have hip3_margin_by_dex populated. Lower-tier whales get NULL HIP-3
        columns automatically (no hip3_margin_by_dex present).

        Args:
            whale_states: List of whale state dicts from _fetch_whale_states_async

        Returns:
            List of dicts, one per address, ready for save_perp_account_snapshots_batch
        """
        account_data_list = []

        for whale_data in whale_states:
            address = whale_data.get('address', '')
            if not address:
                continue

            state = whale_data.get('state', {})
            margin_summary = state.get('marginSummary', {})

            # Mainnet fields (always populated for any whale we successfully fetched)
            account_data = {
                'address': address,
                'account_value': float(margin_summary.get('accountValue', 0)),
                'total_raw_usd': float(margin_summary.get('totalRawUsd', 0)),
                'total_margin_used': float(margin_summary.get('totalMarginUsed', 0)),
                'total_ntl_pos': float(margin_summary.get('totalNtlPos', 0)),
                'withdrawable': None,
                'hip3_account_value': None,
                'hip3_total_raw_usd': None,
                'hip3_total_margin_used': None,
                'hip3_total_ntl_pos': None,
                'hip3_withdrawable': None,
                'hip3_dexes': None,
            }

            withdrawable_raw = state.get('withdrawable')
            if withdrawable_raw is not None:
                account_data['withdrawable'] = float(withdrawable_raw)

            # HIP-3 consolidation (only present for VIP+T1 via gating in _fetch_whale_states_async)
            hip3_margin_by_dex = whale_data.get('hip3_margin_by_dex')
            if hip3_margin_by_dex:
                hip3_acc_total = 0.0
                hip3_raw_total = 0.0
                hip3_margin_total = 0.0
                hip3_ntl_total = 0.0
                hip3_withdrawable_total = 0.0
                dexes_present = []

                for dex, margin in hip3_margin_by_dex.items():
                    hip3_acc_total += float(margin.get('accountValue', 0))
                    hip3_raw_total += float(margin.get('totalRawUsd', 0))
                    hip3_margin_total += float(margin.get('totalMarginUsed', 0))
                    hip3_ntl_total += float(margin.get('totalNtlPos', 0))
                    hip3_withdrawable_raw = margin.get('withdrawable')
                    if hip3_withdrawable_raw is not None:
                        hip3_withdrawable_total += float(hip3_withdrawable_raw)
                    dexes_present.append(dex)

                if dexes_present:
                    account_data['hip3_account_value'] = hip3_acc_total
                    account_data['hip3_total_raw_usd'] = hip3_raw_total
                    account_data['hip3_total_margin_used'] = hip3_margin_total
                    account_data['hip3_total_ntl_pos'] = hip3_ntl_total
                    account_data['hip3_withdrawable'] = hip3_withdrawable_total
                    account_data['hip3_dexes'] = ",".join(dexes_present)

            account_data_list.append(account_data)

        return account_data_list

    # =========================================================================
    # SNAPSHOT METHODS (UPDATED)
    # =========================================================================

    async def take_snapshot_async(self, prices: Dict = None) -> Dict:
        """
        Take a liquidation snapshot for current cycle's addresses.

        Args:
            prices: Optional dict of coin -> price. If not provided, uses client.
                    Should include HIP-3 prices (e.g., "xyz:TSLA": 399.71) if
                    hip3_tracking_enabled is True.

        Returns:
            Dict with snapshot results
        """
        timestamp = datetime.now()
        cycle = self.tier_manager.get_current_cycle()

        # Get addresses for this cycle
        addresses = self.tier_manager.get_all_addresses_for_current_cycle()
        addresses_by_tier = self.tier_manager.get_addresses_for_current_cycle()

        if not addresses:
            logger.warning("No addresses to fetch for this cycle")
            return {
                'timestamp': timestamp.isoformat(),
                'cycle': cycle,
                'addresses_fetched': 0,
                'events': [],
            }

        # Log what we're fetching
        tier_summary = {k: len(v) for k, v in addresses_by_tier.items() if v}
        hip3_tag = " +HIP3" if self.hip3_tracking_enabled else ""
        logger.info(f"📊 Liquidation snapshot (cycle {cycle}/60){hip3_tag} | Addresses: {tier_summary}")

        start_time = datetime.now()

        # Fetch whale states (now includes HIP-3 if enabled)
        whale_states = await self._fetch_whale_states_async(addresses)

        fetch_elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Fetched {len(whale_states)}/{len(addresses)} states in {fetch_elapsed:.1f}s")

        # Get prices if not provided
        if prices is None:
            all_mids = self.client.get_all_mids()
            if all_mids:
                prices = {k: float(v) for k, v in all_mids.items() if not k.startswith('@')}
            else:
                prices = {}
                logger.warning("Failed to get prices")

        # =================================================================
        # MERGE HIP-3 PRICES (NEW)
        # =================================================================
        if self.hip3_tracking_enabled:
            try:
                hip3_mids = self.client.get_hip3_mids()
                if hip3_mids:
                    hip3_prices = {k: float(v) for k, v in hip3_mids.items()}
                    prices.update(hip3_prices)
                    logger.debug(f"Merged {len(hip3_prices)} HIP-3 prices into liquidation price feed")
            except Exception as e:
                logger.warning(f"Failed to fetch HIP-3 prices for liquidation: {e}")
        # =================================================================

        # Detect events (VIP + Tier1 only - handled inside detector)
        events = []
        if whale_states and prices:
            events = self.event_detector.detect(whale_states, prices)
            if events:
                self.event_detector.log_summary(events)
                self.storage.save_whale_events(events)

        # Parse and save liquidation exposure
        if whale_states and prices:
            coin_exposure = self._parse_liquidation_exposure(whale_states, prices)
            self._log_liquidation_summary(coin_exposure)
            self._log_liquidation_coins(coin_exposure)
            self._log_liquidation_addresses(coin_exposure)
            self.storage.save_liquidation_snapshot(timestamp.isoformat(), coin_exposure)

            # Dual-write ALL positions to perp_snapshots (not 20%-filtered).
            # This is what gives the tier system fresh position data every cycle.
            all_positions = []
            for coin_data in coin_exposure.values():
                all_positions.extend(coin_data['positions'])

            if all_positions:
                self.storage.save_perp_snapshots_batch(
                    timestamp.isoformat(),
                    all_positions)
                logger.debug(f"Dual-wrote {len(all_positions)} positions to perp_snapshots")

            # Also write per-address account equity to perp_account_snapshots
            account_data_list = self._parse_account_data(whale_states)
            if account_data_list:
                self.storage.save_perp_account_snapshots_batch(
                    timestamp.isoformat(),
                    account_data_list
                )
                logger.debug(f"Dual-wrote {len(account_data_list)} account snapshots to perp_account_snapshots")

        self.last_snapshot_time = timestamp
        self.snapshot_count += 1

        result = {
            'timestamp': timestamp.isoformat(),
            'cycle': cycle,
            'addresses_requested': len(addresses),
            'addresses_fetched': len(whale_states),
            'success_rate': len(whale_states) / len(addresses) * 100 if addresses else 0,
            'fetch_time_seconds': fetch_elapsed,
            'events_detected': len(events),
            'events': events,
        }

        # Add HIP-3 stats if enabled
        if self.hip3_tracking_enabled:
            hip3_pos_this_cycle = sum(
                len(w.get('hip3_positions', []))
                for w in whale_states
            )
            result['hip3_positions'] = hip3_pos_this_cycle

        return result

    def take_snapshot(self, prices: Dict = None) -> Dict:
        """
        Synchronous wrapper for take_snapshot_async.

        Args:
            prices: Optional dict of coin -> price

        Returns:
            Dict with snapshot results
        """
        return asyncio.run(self.take_snapshot_async(prices))

    # =========================================================================
    # FORMATTING HELPERS
    # =========================================================================

    @staticmethod
    def _format_value(value: float) -> str:
        """Format USD value for display."""
        if abs(value) >= 1_000_000:
            return f"${value / 1_000_000:.2f}M"
        elif abs(value) >= 1_000:
            return f"${value / 1_000:.1f}K"
        else:
            return f"${value:.0f}"

    @staticmethod
    def _format_price(price: float) -> str:
        """Format price with appropriate decimal places for any token price range."""
        if price == 0:
            return "$0"
        elif price >= 10000:
            return f"${price:,.0f}"
        elif price >= 100:
            return f"${price:,.1f}"
        elif price >= 1:
            return f"${price:.2f}"
        elif price >= 0.01:
            return f"${price:.4f}"
        else:
            return f"${price:.6f}"

    # =========================================================================
    # LOGGING
    # =========================================================================

    def _log_liquidation_summary(self, coin_exposure: Dict):
        """Log summary of liquidation exposure to console/main log."""
        if not coin_exposure:
            return

        # Count positions by risk bucket
        danger_count = 0  # < 5%
        warning_count = 0  # 5-10%
        watch_count = 0  # 10-20%

        # Separate HIP-3 counts for visibility
        hip3_count = 0

        for coin, data in coin_exposure.items():
            is_hip3 = ':' in coin
            for pos in data['positions']:
                dist = pos['distance_to_liq']
                if dist < 5:
                    danger_count += 1
                elif dist < 10:
                    warning_count += 1
                elif dist <= 20:
                    watch_count += 1

                if is_hip3:
                    hip3_count += 1

        total_positions = danger_count + warning_count + watch_count

        if total_positions > 0:
            hip3_tag = f" | 🏗️ {hip3_count} HIP-3" if hip3_count > 0 else ""
            logger.info(
                f"🔥 Liquidation risk: {danger_count} DANGER (<5%), "
                f"{warning_count} WARNING (5-10%), {watch_count} WATCH (10-20%)"
                f"{hip3_tag}"
            )

    def _log_liquidation_coins(self, coin_exposure: Dict):
        """
        Log per-coin liquidation summary to dedicated coins log file.
        Only includes coins with at least one position within 20% of liquidation.
        """
        if not coin_exposure:
            return

        # Filter: only coins with positions within 20%
        filtered = {}
        for coin, data in coin_exposure.items():
            danger_positions = [p for p in data['positions'] if p['distance_to_liq'] <= 20]
            if danger_positions:
                closest = danger_positions[0]  # already sorted by distance
                filtered[coin] = {
                    'total_value': sum(abs(p['value']) for p in danger_positions),
                    'long_value': sum(p['value'] for p in danger_positions if p['side'] == 'LONG'),
                    'short_value': sum(abs(p['value']) for p in danger_positions if p['side'] == 'SHORT'),
                    'position_count': len(danger_positions),
                    'closest_dist': closest['distance_to_liq'],
                    'closest_side': closest['side'],
                    'closest_address': closest['address'],
                }

        if not filtered:
            liq_coins_logger.info("No positions within 20% of liquidation")
            return

        # Sort by closest liquidation distance
        sorted_coins = sorted(filtered.items(), key=lambda x: x[1]['closest_dist'])

        total_positions = sum(d['position_count'] for d in filtered.values())

        # Widened COIN column from 8 to 12 for HIP-3 names like "xyz:TSLA"
        liq_coins_logger.info("=" * 109)
        liq_coins_logger.info(
            f"LIQUIDATION EXPOSURE — {total_positions} positions within 20% across {len(filtered)} coins"
        )
        liq_coins_logger.info("=" * 109)
        liq_coins_logger.info(
            f"   {'COIN':<12} {'POS':>4}  {'TOTAL':>10}  {'LONG':>10}  {'SHORT':>10}  "
            f"{'CLOSEST':>8}  {'SIDE':<6}  {'ADDRESS'}"
        )
        liq_coins_logger.info(
            f"   {'─' * 12} {'─' * 4}  {'─' * 10}  {'─' * 10}  {'─' * 10}  "
            f"{'─' * 8}  {'─' * 6}  {'─' * 44}"
        )

        for coin, d in sorted_coins:
            liq_coins_logger.info(
                f"   {coin:<12} {d['position_count']:>4}  "
                f"{self._format_value(d['total_value']):>10}  "
                f"{self._format_value(d['long_value']):>10}  "
                f"{self._format_value(d['short_value']):>10}  "
                f"{d['closest_dist']:>7.1f}%  "
                f"{d['closest_side']:<6}  "
                f"{d['closest_address']}"
            )

        liq_coins_logger.info("")

    def _log_liquidation_addresses(self, coin_exposure: Dict):
        """
        Log per-address liquidation detail to dedicated addresses log file.
        Only includes positions within 20% of liquidation, sorted by distance.
        """
        if not coin_exposure:
            return

        # Collect all positions within 20%
        danger_positions = []
        for coin, data in coin_exposure.items():
            for pos in data['positions']:
                if pos['distance_to_liq'] <= 20:
                    danger_positions.append(pos)

        if not danger_positions:
            liq_addrs_logger.info("No positions within 20% of liquidation")
            return

        # Sort by distance (closest first)
        danger_positions.sort(key=lambda x: x['distance_to_liq'])

        # Widened COIN column from 8 to 12 for HIP-3 names
        liq_addrs_logger.info("=" * 149)
        liq_addrs_logger.info(
            f"LIQUIDATION ADDRESSES — {len(danger_positions)} positions within 20%"
        )
        liq_addrs_logger.info("=" * 149)
        liq_addrs_logger.info(
            f"   {'DIST':>6}  {'COIN':<12} {'SIDE':<6}  {'VALUE':>10}  "
            f"{'ENTRY':>12} {'CURRENT':>12} {'LIQ':>12}  "
            f"{'PNL%':>8}  {'LEV':>5}  {'FUNDING':>10}  {'ADDRESS'}"
        )
        liq_addrs_logger.info(
            f"   {'─' * 6}  {'─' * 12} {'─' * 6}  {'─' * 10}  "
            f"{'─' * 12} {'─' * 12} {'─' * 12}  "
            f"{'─' * 8}  {'─' * 5}  {'─' * 10}  {'─' * 44}"
        )

        for pos in danger_positions:
            # PnL formatting
            pnl = pos['pnl_pct']
            pnl_str = f"+{pnl:.1f}%" if pnl > 0 else f"{pnl:.1f}%"

            # Funding formatting
            funding = pos['funding_since_open']
            if abs(funding) >= 1000:
                fund_str = f"${funding / 1000:.1f}K"
            else:
                fund_str = f"${funding:.0f}"

            # Leverage
            lev = pos.get('leverage', 0)
            lev_str = f"{lev}x" if lev else "-"

            liq_addrs_logger.info(
                f"   {pos['distance_to_liq']:>5.1f}%  "
                f"{pos['coin']:<12} {pos['side']:<6}  "
                f"{self._format_value(pos['value']):>10}  "
                f"{self._format_price(pos['entry_price']):>12} "
                f"{self._format_price(pos['current_price']):>12} "
                f"{self._format_price(pos['liq_price']):>12}  "
                f"{pnl_str:>8}  "
                f"{lev_str:>5}  "
                f"{fund_str:>10}  "
                f"{pos['address']}"
            )

        liq_addrs_logger.info("")

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get tracker statistics."""
        stats = {
            'snapshot_count': self.snapshot_count,
            'last_snapshot': self.last_snapshot_time.isoformat() if self.last_snapshot_time else None,
            'batch_size': self.batch_size,
            'batch_delay': self.batch_delay,
        }

        # HIP-3 stats
        if self.hip3_tracking_enabled:
            stats['hip3_enabled'] = True
            stats['hip3_positions_found_total'] = self._hip3_positions_found
            stats['hip3_fetch_errors'] = self._hip3_fetch_errors
        else:
            stats['hip3_enabled'] = False

        return stats