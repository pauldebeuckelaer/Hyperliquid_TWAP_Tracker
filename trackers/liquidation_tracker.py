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

        # Stats
        self.snapshot_count = 0
        self.last_snapshot_time: Optional[datetime] = None

        logger.info(f"LiquidationTracker initialized (batch_size={self.batch_size})")

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

    async def _fetch_whale_states_async(self, addresses: List[str]) -> List[Dict]:
        """
        Fetch whale states (perp + spot) in batches.

        Args:
            addresses: List of whale addresses to fetch

        Returns:
            List of state dicts (excluding failures)
        """
        if not addresses:
            return []

        connector = aiohttp.TCPConnector(limit=20)
        all_results = []
        total_batches = (len(addresses) + self.batch_size - 1) // self.batch_size

        async with aiohttp.ClientSession(connector=connector) as session:
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
                        all_results.append(perp)

                # Delay between batches (skip on last batch)
                if i + self.batch_size < len(addresses):
                    await asyncio.sleep(self.batch_delay)

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
    # LIQUIDATION PARSING
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

    def _parse_liquidation_exposure(self, whale_states: List[Dict], prices: Dict) -> Dict:
        """
        Parse whale states and aggregate liquidation exposure by coin.

        Args:
            whale_states: List of whale state dicts from API
            prices: Dict of coin -> price

        Returns:
            Dict with per-coin exposure data including individual positions
        """
        coin_exposure = {}

        for whale_data in whale_states:
            state = whale_data.get('state', {})
            address = whale_data.get('address', '')

            # Account level data
            margin_summary = state.get('marginSummary', {})
            account_value = float(margin_summary.get('accountValue', 0))
            total_margin_used = float(margin_summary.get('totalMarginUsed', 0))
            withdrawable = float(state.get('withdrawable', 0))

            # Account-level risk metrics
            account_margin_ratio = total_margin_used / account_value if account_value > 0 else 0

            positions = state.get('assetPositions', [])

            for pos_data in positions:
                pos = pos_data.get('position', {})
                coin = pos.get('coin', '')
                if not coin:
                    continue

                size = float(pos.get('szi', 0))
                if size == 0:
                    continue

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
                    'spot_usdc': whale_data.get('spot_usdc', 0),
                })

        # Sort positions within each coin by distance to liquidation
        for coin in coin_exposure:
            coin_exposure[coin]['positions'].sort(key=lambda x: x['distance_to_liq'])

        return coin_exposure

    # =========================================================================
    # SNAPSHOT METHODS
    # =========================================================================

    async def take_snapshot_async(self, prices: Dict = None) -> Dict:
        """
        Take a liquidation snapshot for current cycle's addresses.

        Args:
            prices: Optional dict of coin -> price. If not provided, uses client.

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
        logger.info(f"📊 Liquidation snapshot (cycle {cycle}/60) | Addresses: {tier_summary}")

        start_time = datetime.now()

        # Fetch whale states
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

        self.last_snapshot_time = timestamp
        self.snapshot_count += 1

        return {
            'timestamp': timestamp.isoformat(),
            'cycle': cycle,
            'addresses_requested': len(addresses),
            'addresses_fetched': len(whale_states),
            'success_rate': len(whale_states) / len(addresses) * 100 if addresses else 0,
            'fetch_time_seconds': fetch_elapsed,
            'events_detected': len(events),
            'events': events,
        }

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

        for coin, data in coin_exposure.items():
            for pos in data['positions']:
                dist = pos['distance_to_liq']
                if dist < 5:
                    danger_count += 1
                elif dist < 10:
                    warning_count += 1
                elif dist <= 20:
                    watch_count += 1

        total_positions = danger_count + warning_count + watch_count

        if total_positions > 0:
            logger.info(
                f"🔥 Liquidation risk: {danger_count} DANGER (<5%), "
                f"{warning_count} WARNING (5-10%), {watch_count} WATCH (10-20%)"
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

        liq_coins_logger.info("=" * 105)
        liq_coins_logger.info(
            f"LIQUIDATION EXPOSURE — {total_positions} positions within 20% across {len(filtered)} coins"
        )
        liq_coins_logger.info("=" * 105)
        liq_coins_logger.info(
            f"   {'COIN':<8} {'POS':>4}  {'TOTAL':>10}  {'LONG':>10}  {'SHORT':>10}  "
            f"{'CLOSEST':>8}  {'SIDE':<6}  {'ADDRESS'}"
        )
        liq_coins_logger.info(
            f"   {'─' * 8} {'─' * 4}  {'─' * 10}  {'─' * 10}  {'─' * 10}  "
            f"{'─' * 8}  {'─' * 6}  {'─' * 44}"
        )

        for coin, d in sorted_coins:
            liq_coins_logger.info(
                f"   {coin:<8} {d['position_count']:>4}  "
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

        liq_addrs_logger.info("=" * 145)
        liq_addrs_logger.info(
            f"LIQUIDATION ADDRESSES — {len(danger_positions)} positions within 20%"
        )
        liq_addrs_logger.info("=" * 145)
        liq_addrs_logger.info(
            f"   {'DIST':>6}  {'COIN':<8} {'SIDE':<6}  {'VALUE':>10}  "
            f"{'ENTRY':>12} {'CURRENT':>12} {'LIQ':>12}  "
            f"{'PNL%':>8}  {'LEV':>5}  {'FUNDING':>10}  {'ADDRESS'}"
        )
        liq_addrs_logger.info(
            f"   {'─' * 6}  {'─' * 8} {'─' * 6}  {'─' * 10}  "
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
                f"{pos['coin']:<8} {pos['side']:<6}  "
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
        return {
            'snapshot_count': self.snapshot_count,
            'last_snapshot': self.last_snapshot_time.isoformat() if self.last_snapshot_time else None,
            'batch_size': self.batch_size,
            'batch_delay': self.batch_delay,
        }