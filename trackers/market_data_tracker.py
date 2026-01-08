#!/usr/bin/env python3
"""
Market Data Tracker
===================
1-minute snapshots of market data for all perps.
Captures prices, funding, OI, volume, order book depth.
Also tracks whale liquidation risk with async fetching.
"""
import asyncio
import aiohttp
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from storage import SQLiteBackend

logger = logging.getLogger(__name__)


class MarketDataTracker:
    """Tracks market data snapshots every minute"""

    def __init__(self, hyperliquid_client, db_path: Path = None):
        """
        Initialize market data tracker.

        Args:
            hyperliquid_client: HyperliquidClient instance for API calls
            db_path: Path to SQLite database for whale addresses
        """
        self.client = hyperliquid_client
        self.db_path = db_path or Path('data/twap.db')
        self.last_snapshot_time: Optional[datetime] = None
        self.snapshot_count = 0

        # API endpoint for async calls
        self.api_url = "https://api.hyperliquid.xyz/info"

        self.storage = SQLiteBackend(self.db_path)

        self.event_detector = WhaleEventDetector(hyperliquid_client)

        logger.info("MarketDataTracker initialized")

    def _get_whale_addresses(self) -> List[str]:
        """Get unique whale addresses from all snapshot tables"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT address FROM perp_snapshots 
                UNION
                SELECT DISTINCT address FROM vault_snapshots
                UNION
                SELECT DISTINCT address FROM spot_snapshots
                ORDER BY address
            """)
            addresses = [row[0] for row in cursor.fetchall()]
            conn.close()
            return addresses
        except Exception as e:
            logger.error(f"Error fetching whale addresses: {e}")
            return []

    async def _get_user_state_async(
            self,
            session: aiohttp.ClientSession,
            address: str
    ) -> Optional[Dict]:
        """
        Fetch user state asynchronously.

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
                    logger.warning(f"Failed to fetch state for {address[:10]}...: {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching state for {address[:10]}...")
            return None
        except Exception as e:
            logger.warning(f"Error fetching state for {address[:10]}...: {e}")
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
                    logger.warning(f"Failed to fetch spot state for {address[:10]}...: {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching spot state for {address[:10]}...")
            return None
        except Exception as e:
            logger.warning(f"Error fetching spot state for {address[:10]}...: {e}")
            return None

    async def _fetch_whale_states_async(self, addresses: List[str]) -> List[Dict]:
        """
        Fetch all whale states (perp + spot) concurrently in batches.

        Args:
            addresses: List of whale addresses

        Returns:
            List of state dicts (excluding failures)
        """
        BATCH_SIZE = 250
        BATCH_DELAY = 1  # seconds between batches

        connector = aiohttp.TCPConnector(limit=20)
        all_results = []

        async with aiohttp.ClientSession(connector=connector) as session:
            for i in range(0, len(addresses), BATCH_SIZE):
                batch = addresses[i:i + BATCH_SIZE]
                batch_num = i // BATCH_SIZE + 1
                total_batches = (len(addresses) + BATCH_SIZE - 1) // BATCH_SIZE

                logger.debug(f"   Fetching batch {batch_num}/{total_batches} ({len(batch)} addresses)")

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

                # Wait before next batch (skip on last batch)
                if i + BATCH_SIZE < len(addresses):
                    await asyncio.sleep(BATCH_DELAY)

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

    def _calculate_distance_to_liq(self, current_price: float, liq_price: float, side: str) -> float:
        """
        Calculate percentage distance to liquidation.

        Args:
            current_price: Current mark price
            liq_price: Liquidation price
            side: 'long' or 'short'

        Returns:
            Percentage distance (positive = safe, negative = past liq)
        """
        if current_price == 0 or liq_price == 0:
            return 100.0  # No valid data

        if side == 'long':
            # Long gets liquidated when price drops to liq_price
            distance = (current_price - liq_price) / current_price * 100
        else:
            # Short gets liquidated when price rises to liq_price
            distance = (liq_price - current_price) / current_price * 100

        return distance

    def _parse_liquidation_exposure(self, whale_states: List[Dict], prices: Dict) -> Dict:
        """
        Parse whale states and aggregate liquidation exposure by coin.

        Returns:
            Dict with per-coin exposure data including individual positions
        """
        coin_exposure = {}

        for whale_data in whale_states:
            state = whale_data.get('state', {})
            address = whale_data.get('address', '')

            # === ACCOUNT LEVEL DATA ===
            margin_summary = state.get('marginSummary', {})
            cross_margin_summary = state.get('crossMarginSummary', {})

            account_value = float(margin_summary.get('accountValue', 0))
            total_margin_used = float(margin_summary.get('totalMarginUsed', 0))
            total_ntl_pos = float(margin_summary.get('totalNtlPos', 0))
            total_raw_usd = float(margin_summary.get('totalRawUsd', 0))
            withdrawable = float(state.get('withdrawable', 0))
            cross_maintenance_margin = float(state.get('crossMaintenanceMarginUsed', 0))

            # Calculate account-level risk metrics
            account_margin_ratio = total_margin_used / account_value if account_value > 0 else 0

            positions = state.get('assetPositions', [])

            for pos_data in positions:
                pos = pos_data.get('position', {})

                coin = pos.get('coin', '')
                if not coin:
                    continue

                # === POSITION LEVEL DATA ===
                size = float(pos.get('szi', 0))
                if size == 0:
                    continue

                # Basic position info
                entry_price = float(pos.get('entryPx', 0))
                liq_price_str = pos.get('liquidationPx')
                liq_price = float(liq_price_str) if liq_price_str else 0
                position_value = float(pos.get('positionValue', 0))
                margin_used = float(pos.get('marginUsed', 0))
                max_leverage = int(pos.get('maxLeverage', 0))

                # Leverage info
                leverage_data = pos.get('leverage', {})
                if isinstance(leverage_data, dict):
                    leverage = leverage_data.get('value', 0)
                    leverage_type = leverage_data.get('type', 'cross')  # cross or isolated
                    leverage_raw_usd = float(leverage_data.get('rawUsd', 0))
                else:
                    leverage = 0
                    leverage_type = 'unknown'
                    leverage_raw_usd = 0

                # PnL data
                unrealized_pnl = float(pos.get('unrealizedPnl', 0))
                return_on_equity = float(pos.get('returnOnEquity', 0))

                # Funding data
                cum_funding = pos.get('cumFunding', {})
                funding_all_time = float(cum_funding.get('allTime', 0))
                funding_since_open = float(cum_funding.get('sinceOpen', 0))
                funding_since_change = float(cum_funding.get('sinceChange', 0))

                side = 'LONG' if size > 0 else 'SHORT'

                # Get current price
                current_price = float(prices.get(coin, 0))

                # === DERIVED METRICS ===

                # Distance to liquidation
                if current_price > 0 and liq_price > 0:
                    distance = self._calculate_distance_to_liq(current_price, liq_price, side.lower())
                else:
                    distance = 100.0

                # Price vs entry (% change since entry)
                if entry_price > 0:
                    if side == 'LONG':
                        price_vs_entry_pct = (current_price - entry_price) / entry_price * 100
                    else:
                        price_vs_entry_pct = (entry_price - current_price) / entry_price * 100
                else:
                    price_vs_entry_pct = 0

                # PnL percentage (return on margin)
                pnl_pct = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0

                # Position concentration (% of account in this position)
                position_pct_of_account = (abs(position_value) / account_value * 100) if account_value > 0 else 0

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

                # Add ALL position details
                coin_exposure[coin]['positions'].append({
                    # Identity
                    'address': address,
                    'coin': coin,
                    'side': side,

                    # Position basics
                    'size': abs(size),
                    'size_signed': size,
                    'value': abs(position_value),
                    'entry_price': entry_price,
                    'current_price': current_price,
                    'liq_price': liq_price,
                    'margin_used': margin_used,

                    # Leverage
                    'leverage': leverage,
                    'leverage_type': leverage_type,
                    'leverage_raw_usd': leverage_raw_usd,
                    'max_leverage': max_leverage,

                    # PnL
                    'unrealized_pnl': unrealized_pnl,
                    'return_on_equity': return_on_equity,
                    'pnl_pct': pnl_pct,

                    # Funding
                    'funding_all_time': funding_all_time,
                    'funding_since_open': funding_since_open,
                    'funding_since_change': funding_since_change,

                    # Derived risk metrics
                    'distance_to_liq': distance,
                    'price_vs_entry_pct': price_vs_entry_pct,
                    'position_pct_of_account': position_pct_of_account,

                    # Account level context
                    'account_value': account_value,
                    'account_margin_used': total_margin_used,
                    'account_margin_ratio': account_margin_ratio,
                    'account_withdrawable': withdrawable,
                    'account_total_ntl_pos': total_ntl_pos,
                    'spot_usdc': whale_data.get('spot_usdc', 0),
                })

        # Sort positions within each coin by distance to liquidation
        for coin in coin_exposure:
            coin_exposure[coin]['positions'].sort(key=lambda x: x['distance_to_liq'])

        return coin_exposure

    def _format_value(self, value: float) -> str:
        """Format USD value for display"""
        if value >= 1_000_000:
            return f"${value / 1_000_000:.2f}M"
        elif value >= 1_000:
            return f"${value / 1_000:.1f}K"
        else:
            return f"${value:.0f}"

    def _format_price(self, price: float) -> str:
        """Format price for display"""
        if price >= 10000:
            return f"${price:,.0f}"
        elif price >= 100:
            return f"${price:,.1f}"
        elif price >= 1:
            return f"${price:.2f}"
        elif price >= 0.01:
            return f"${price:.4f}"
        else:
            return f"${price:.6f}"

    def _log_liquidation_exposure(self, coin_exposure: Dict, market_data: Dict = None):
        """Log liquidation exposure with clean per-coin breakdown."""
        if not coin_exposure:
            logger.info("   No positions found")
            return

        market_data = market_data or {}

        # Sort by total value descending - NO LIMIT
        sorted_coins = sorted(
            coin_exposure.items(),
            key=lambda x: x[1]['total_value'],
            reverse=True
        )

        logger.info("")
        logger.info("=" * 165)
        logger.info("🔥 WHALE LIQUIDATION EXPOSURE")
        logger.info("=" * 165)

        for coin, data in sorted_coins:  # No limit - show all coins
            total_val = data['total_value']
            long_val = data['long_value']
            short_val = data['short_value']
            positions = data['positions']

            # Filter positions to only those within 5% of liquidation
            danger_positions = [p for p in positions if p['distance_to_liq'] <= 20]

            # Get market data for this coin
            mkt = market_data.get(coin, {})
            price = mkt.get('mark_px', 0)
            prev_price = mkt.get('prev_day_px', 0)
            funding = mkt.get('funding', 0)
            oi_usd = mkt.get('open_interest', 0) * price if price else 0
            volume = mkt.get('day_ntl_vlm', 0)

            # Format 24h change
            if prev_price > 0 and price > 0:
                change_pct = (price - prev_price) / prev_price * 100
                change_str = f"{change_pct:+.2f}%"
            else:
                change_str = ""

            # Format funding (8h rate, like Hyperliquid UI)
            if funding != 0:
                funding_pct = funding * 100  # Convert to percentage
                funding_str = f"{funding_pct:.4f}%"
            else:
                funding_str = ""

            # Format price
            if price >= 1000:
                price_str = f"${price:,.0f}"
            elif price >= 1:
                price_str = f"${price:.2f}"
            elif price > 0:
                price_str = f"${price:.4f}"
            else:
                price_str = ""

            # Build header with market data
            header_parts = [coin]
            if price_str:
                header_parts.append(price_str)
            if change_str:
                header_parts.append(change_str)
            if funding_str:
                header_parts.append(f"F:{funding_str}")
            if oi_usd > 0:
                header_parts.append(f"OI:{self._format_value(oi_usd)}")
            if volume > 0:
                header_parts.append(f"Vol:{self._format_value(volume)}")

            header_info = " │ ".join(header_parts)
            header_line = f"┌─ {header_info} "
            header_line += "─" * max(0, 175 - len(header_line))

            logger.info("")
            logger.info(header_line)
            logger.info(
                f"│  Total: {self._format_value(total_val):>10} │ Long: {self._format_value(long_val):>10} │ Short: {self._format_value(short_val):>10}")

            # Only show position details if there are danger positions
            if danger_positions:
                logger.info(f"│")
                logger.info(
                    f"│  {'ADDRESS':<44} {'SIDE':<6} {'VALUE':>10} {'LEV':>5} {'ENTRY':>12} {'LIQ':>12} {'DIST':>7} {'PNL%':>8} {'FUNDING':>10} │ {'ACCT VAL':>10} {'MARGIN':>10} {'WDRWBL':>10} {'SPOT':>10}")
                logger.info(
                    f"│  {'-' * 44} {'-' * 6} {'-' * 10} {'-' * 5} {'-' * 12} {'-' * 12} {'-' * 7} {'-' * 8} {'-' * 10} │ {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 10}")

                for pos in danger_positions:
                    dist = pos['distance_to_liq']
                    pnl_pct = pos['pnl_pct']
                    pos_funding = pos['funding_since_open']
                    liq_price = pos['liq_price']
                    entry_price = pos['entry_price']

                    # Account context
                    account_value = pos.get('account_value', 0)
                    margin_used = pos.get('account_margin_used', 0)
                    withdrawable = pos.get('account_withdrawable', 0)

                    # Danger indicator
                    dist_str = f"⚠️{dist:.1f}%"

                    # Entry price formatting
                    if entry_price > 0:
                        entry_str = self._format_price(entry_price)
                    else:
                        entry_str = "-"

                    # Liquidation price formatting
                    if liq_price > 0:
                        liq_str = self._format_price(liq_price)
                    else:
                        liq_str = "-"

                    # PnL
                    if pnl_pct > 0:
                        pnl_str = f"+{pnl_pct:.1f}%"
                    else:
                        pnl_str = f"{pnl_pct:.1f}%"

                    # Funding
                    if abs(pos_funding) >= 1000:
                        funding_pos_str = f"${pos_funding / 1000:.1f}K"
                    else:
                        funding_pos_str = f"${pos_funding:.0f}"

                    leverage_str = f"{pos['leverage']}x" if pos['leverage'] else "-"

                    # Account context formatting
                    acct_val_str = self._format_value(account_value)
                    margin_str = self._format_value(margin_used)
                    wdrwbl_str = self._format_value(withdrawable)

                    spot_usdc = pos.get('spot_usdc', 0)
                    spot_str = self._format_value(spot_usdc)

                    logger.info(
                        f"│  {pos['address']:<44} {pos['side']:<6} {self._format_value(pos['value']):>10} {leverage_str:>5} {entry_str:>12} {liq_str:>12} {dist_str:>7} {pnl_str:>8} {funding_pos_str:>10} │ {acct_val_str:>10} {margin_str:>10} {wdrwbl_str:>10} {spot_str:>10}")

            logger.info(f"└{'─' * 175}")

    def take_snapshot(self) -> Dict:
        """
        Take a market data snapshot for all perps + whale liquidation risk.

        Returns:
            Dict with snapshot results
        """
        timestamp = datetime.now()

        logger.info(f"📊 Market snapshot #{self.snapshot_count + 1}")

        # Get market data (funding, OI, volume, prices) - single API call
        market_data = self.client.get_meta_and_asset_ctxs()

        if market_data:
            asset_ctxs = market_data['asset_ctxs']
            # Filter out delisted
            active_assets = {k: v for k, v in asset_ctxs.items() if not v['is_delisted']}
            perp_prices = {k: v['mark_px'] for k, v in active_assets.items()}
            logger.info(f"   Captured {len(active_assets)} active perps")

            self.storage.save_market_snapshot(timestamp.isoformat(), active_assets)
        else:
            asset_ctxs = {}
            active_assets = {}
            perp_prices = {}
            logger.warning("   Failed to get market data")

        # Get whale liquidation data (async - many API calls)
        whale_addresses = self._get_whale_addresses()
        liquidation_data = []
        coin_exposure = {}
        events = []

        if whale_addresses:
            logger.info(f"   Fetching {len(whale_addresses)} whale states (async)...")

            start_time = datetime.now()

            # Run async fetching
            liquidation_data = asyncio.run(
                self._fetch_whale_states_async(whale_addresses)
            )

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"   Captured {len(liquidation_data)}/{len(whale_addresses)} whale states in {elapsed:.1f}s")

            # Detect events (compare to previous snapshot)
            if liquidation_data and perp_prices:
                events = self.event_detector.detect(liquidation_data, perp_prices)
                self.event_detector.log_summary(events)

                if events:
                    self.storage.save_whale_events(events)

            # Parse and log liquidation exposure
            if liquidation_data and perp_prices:
                coin_exposure = self._parse_liquidation_exposure(liquidation_data, perp_prices)
                self._log_liquidation_exposure(coin_exposure, active_assets)  # Pass market data

                self.storage.save_liquidation_snapshot(timestamp.isoformat(), coin_exposure)

        self.last_snapshot_time = timestamp
        self.snapshot_count += 1

        return {
            'timestamp': timestamp.isoformat(),
            'num_coins': len(perp_prices),
            'prices': perp_prices,
            'market_data': active_assets,  # Include market data in return
            'whale_states': liquidation_data,
            'coin_exposure': coin_exposure,
            'events': events,
        }

    def get_stats(self) -> Dict:
        """Get tracker statistics"""
        return {
            'snapshot_count': self.snapshot_count,
            'last_snapshot': self.last_snapshot_time.isoformat() if self.last_snapshot_time else None
        }


class WhaleEventDetector:
    """
    Detects whale position/account changes between snapshots.
    """

    # ===== DETECTION THRESHOLDS =====
    SIZE_CHANGE_PCT = 0.01  # 1% minimum size change to trigger event
    SIZE_CHANGE_MIN_USD = 10_000  # $10K minimum dollar change
    MARGIN_CHANGE_PCT = 0.05  # 5% minimum margin change
    MARGIN_CHANGE_MIN_USD = 1_000  # $1K minimum dollar change

    # ================================

    def __init__(self, hyperliquid_client=None):
        """
        Initialize event detector.

        Args:
            hyperliquid_client: Client for fetching liquidation data
        """
        self.client = hyperliquid_client
        self.previous_state = {}
        self.snapshot_count = 0

        # Debounce tracking - prevents false close/open events from API timeouts
        self._missing_positions = {}  # {(address, coin): consecutive_missing_count}
        self.DEBOUNCE_THRESHOLD = 3  # Must be missing for 3 loops to count as closed

        logger.info("WhaleEventDetector initialized")

    def _parse_current_state(self, whale_states: List[Dict]) -> Dict:
        """
        Parse whale states into comparable format.

        Returns:
            Dict of address -> {positions: {coin: position_data}, account: account_data}
        """
        state = {}

        for whale_data in whale_states:
            address = whale_data.get('address', '')
            raw_state = whale_data.get('state', {})

            if not address:
                continue

            # Account level data
            margin_summary = raw_state.get('marginSummary', {})
            account = {
                'account_value': float(margin_summary.get('accountValue', 0)),
                'margin_used': float(margin_summary.get('totalMarginUsed', 0)),
                'withdrawable': float(raw_state.get('withdrawable', 0)),
            }

            # Positions
            positions = {}
            for pos_data in raw_state.get('assetPositions', []):
                pos = pos_data.get('position', {})
                coin = pos.get('coin', '')
                size = float(pos.get('szi', 0))

                if not coin or size == 0:
                    continue

                leverage_data = pos.get('leverage', {})
                if isinstance(leverage_data, dict):
                    leverage = leverage_data.get('value', 0)
                else:
                    leverage = 0

                positions[coin] = {
                    'size': size,
                    'abs_size': abs(size),
                    'side': 'LONG' if size > 0 else 'SHORT',
                    'value': abs(float(pos.get('positionValue', 0))),
                    'entry_price': float(pos.get('entryPx', 0)),
                    'leverage': leverage,
                    'liq_price': float(pos.get('liquidationPx') or 0),
                    'margin_used': float(pos.get('marginUsed', 0)),
                }

            state[address] = {
                'positions': positions,
                'account': account
            }

        return state

    def _check_liquidation(self, address: str, coin: str) -> bool:
        """
        Check if a closed position was liquidated.

        Args:
            address: Whale address
            coin: Coin that was closed

        Returns:
            True if liquidation, False if voluntary close
        """
        if not self.client:
            return False

        try:
            fills = self.client.get_user_fills(address)

            if not fills:
                return False

            for fill in fills[:20]:
                if fill.get('coin') == coin:
                    if fill.get('liquidation', False) or 'liq' in fill.get('dir', '').lower():
                        return True

            return False

        except Exception as e:
            logger.debug(f"Error checking liquidation for {address[:10]}...: {e}")
            return False

    def detect(self, whale_states: List[Dict], prices: Dict) -> List[Dict]:
        """
        Compare current state to previous and detect events.
        Uses debouncing to prevent false open/close events from API timeouts.

        Args:
            whale_states: Current whale states from API
            prices: Current prices dict

        Returns:
            List of event dicts
        """
        timestamp = datetime.now()
        current_state = self._parse_current_state(whale_states)
        events = []

        # First snapshot - no comparison possible
        if self.snapshot_count == 0:
            self.previous_state = current_state
            self.snapshot_count += 1
            logger.debug("First snapshot - establishing baseline state")
            return events

        # Compare each address
        all_addresses = set(current_state.keys()) | set(self.previous_state.keys())

        for address in all_addresses:
            prev = self.previous_state.get(address, {'positions': {}, 'account': {}})
            curr = current_state.get(address, {'positions': {}, 'account': {}})

            prev_positions = prev.get('positions', {})
            curr_positions = curr.get('positions', {})
            prev_account = prev.get('account', {})
            curr_account = curr.get('account', {})

            # ===== POSITION EVENTS =====
            all_coins = set(prev_positions.keys()) | set(curr_positions.keys())

            for coin in all_coins:
                in_prev = coin in prev_positions
                in_curr = coin in curr_positions
                key = (address, coin)

                if not in_prev and in_curr:
                    # Position appeared - check if it was missing (API flicker) or truly new
                    if key in self._missing_positions:
                        # Was missing, now back - API flicker, not a real open
                        del self._missing_positions[key]
                        logger.debug(f"Position reappeared (API flicker): {address[:10]}... {coin}")
                    else:
                        # Truly new position
                        pos = curr_positions[coin]
                        events.append({
                            'timestamp': timestamp,
                            'address': address,
                            'event_type': 'position_opened',
                            'coin': coin,
                            'side': pos['side'],
                            'old_value': 0,
                            'new_value': pos['value'],
                            'size': pos['abs_size'],
                            'leverage': pos['leverage'],
                            'entry_price': pos['entry_price'],
                            'current_price': prices.get(coin, 0),
                        })
                        logger.debug(
                            f"position_opened | {address[:10]}... | "
                            f"{coin} {pos['side']} ${pos['value']:,.0f} {pos['leverage']}x"
                        )

                elif in_prev and not in_curr:
                    # Position missing - increment counter, only fire event after threshold
                    self._missing_positions[key] = self._missing_positions.get(key, 0) + 1
                    missing_count = self._missing_positions[key]

                    if missing_count >= self.DEBOUNCE_THRESHOLD:
                        # Missing for enough loops - really closed
                        del self._missing_positions[key]

                        pos = prev_positions[coin]
                        is_liquidation = self._check_liquidation(address, coin)
                        event_type = 'position_liquidated' if is_liquidation else 'position_closed'

                        events.append({
                            'timestamp': timestamp,
                            'address': address,
                            'event_type': event_type,
                            'coin': coin,
                            'side': pos['side'],
                            'old_value': pos['value'],
                            'new_value': 0,
                            'size': pos['abs_size'],
                            'leverage': pos['leverage'],
                            'entry_price': pos['entry_price'],
                            'current_price': prices.get(coin, 0),
                        })
                        logger.debug(
                            f"{event_type} | {address[:10]}... | "
                            f"{coin} {pos['side']} ${pos['value']:,.0f}"
                        )
                    else:
                        logger.debug(
                            f"Position missing ({missing_count}/{self.DEBOUNCE_THRESHOLD}): "
                            f"{address[:10]}... {coin}"
                        )

                elif in_prev and in_curr:
                    # Position exists in both - clear any missing counter and check for changes
                    if key in self._missing_positions:
                        del self._missing_positions[key]

                    prev_pos = prev_positions[coin]
                    curr_pos = curr_positions[coin]

                    prev_size = prev_pos['abs_size']
                    curr_size = curr_pos['abs_size']
                    size_change = curr_size - prev_size
                    size_change_pct = abs(size_change) / prev_size if prev_size > 0 else 0
                    value_change_usd = abs(curr_pos['value'] - prev_pos['value'])

                    # Size changed significantly
                    if size_change_pct > self.SIZE_CHANGE_PCT and value_change_usd > self.SIZE_CHANGE_MIN_USD:
                        if size_change > 0:
                            event_type = 'position_increased'
                        else:
                            event_type = 'position_reduced'

                        events.append({
                            'timestamp': timestamp,
                            'address': address,
                            'event_type': event_type,
                            'coin': coin,
                            'side': curr_pos['side'],
                            'old_value': prev_pos['value'],
                            'new_value': curr_pos['value'],
                            'old_size': prev_size,
                            'new_size': curr_size,
                            'leverage': curr_pos['leverage'],
                            'current_price': prices.get(coin, 0),
                        })
                        logger.debug(
                            f"{event_type} | {address[:10]}... | "
                            f"{coin} {curr_pos['side']} ${prev_pos['value']:,.0f} → ${curr_pos['value']:,.0f}"
                        )

                    # Leverage changed
                    if prev_pos['leverage'] != curr_pos['leverage'] and curr_pos['leverage'] > 0:
                        events.append({
                            'timestamp': timestamp,
                            'address': address,
                            'event_type': 'leverage_changed',
                            'coin': coin,
                            'side': curr_pos['side'],
                            'old_value': prev_pos['leverage'],
                            'new_value': curr_pos['leverage'],
                            'position_value': curr_pos['value'],
                            'current_price': prices.get(coin, 0),
                        })
                        logger.debug(
                            f"leverage_changed | {address[:10]}... | "
                            f"{coin} {prev_pos['leverage']}x → {curr_pos['leverage']}x"
                        )

            # ===== ACCOUNT EVENTS =====
            prev_margin = prev_account.get('margin_used', 0)
            curr_margin = curr_account.get('margin_used', 0)
            margin_change = curr_margin - prev_margin
            margin_change_pct = abs(margin_change) / prev_margin if prev_margin > 0 else 0

            # Margin changed significantly
            if abs(margin_change) > self.MARGIN_CHANGE_MIN_USD and margin_change_pct > self.MARGIN_CHANGE_PCT:
                if margin_change > 0:
                    event_type = 'margin_added'
                else:
                    event_type = 'margin_removed'

                events.append({
                    'timestamp': timestamp,
                    'address': address,
                    'event_type': event_type,
                    'coin': None,
                    'side': None,
                    'old_value': prev_margin,
                    'new_value': curr_margin,
                    'account_value': curr_account.get('account_value', 0),
                    'withdrawable': curr_account.get('withdrawable', 0),
                })
                logger.debug(
                    f"{event_type} | {address[:10]}... | "
                    f"${prev_margin:,.0f} → ${curr_margin:,.0f}"
                )

        # Update state for next comparison
        self.previous_state = current_state
        self.snapshot_count += 1

        return events

    def log_summary(self, events: List[Dict]):
        """
        Log INFO level aggregate summary of events.
        """
        if not events:
            return

        # Count events by type and coin
        by_coin = {}
        account_events = []

        for event in events:
            event_type = event['event_type']
            coin = event.get('coin')

            if coin:
                if coin not in by_coin:
                    by_coin[coin] = {
                        'opened': 0,
                        'closed': 0,
                        'increased': 0,
                        'reduced': 0,
                        'liquidated': 0,
                        'leverage': 0,
                        'net_value': 0,
                    }

                side_mult = 1 if event.get('side') == 'LONG' else -1

                if event_type == 'position_opened':
                    by_coin[coin]['opened'] += 1
                    by_coin[coin]['net_value'] += event['new_value'] * side_mult

                elif event_type == 'position_closed':
                    by_coin[coin]['closed'] += 1
                    by_coin[coin]['net_value'] -= event['old_value'] * side_mult

                elif event_type == 'position_liquidated':
                    by_coin[coin]['liquidated'] += 1
                    by_coin[coin]['net_value'] -= event['old_value'] * side_mult

                elif event_type == 'position_increased':
                    by_coin[coin]['increased'] += 1
                    by_coin[coin]['net_value'] += (event['new_value'] - event['old_value']) * side_mult

                elif event_type == 'position_reduced':
                    by_coin[coin]['reduced'] += 1
                    by_coin[coin]['net_value'] -= (event['old_value'] - event['new_value']) * side_mult

                elif event_type == 'leverage_changed':
                    by_coin[coin]['leverage'] += 1
            else:
                account_events.append(event)

        # Log summary
        logger.info("")
        logger.info("─" * 90)
        logger.info(f"📈 WHALE ACTIVITY ({len(events)} events)")
        logger.info("─" * 90)
        logger.info(f"  {'COIN':<8} │ {'ACTIVITY':<55} │ {'NET FLOW':<20}")
        logger.info(f"  {'-' * 8} │ {'-' * 55} │ {'-' * 20}")

        # Sort by total activity
        sorted_coins = sorted(
            by_coin.items(),
            key=lambda x: sum([
                x[1]['opened'], x[1]['closed'], x[1]['increased'],
                x[1]['reduced'], x[1]['liquidated']
            ]),
            reverse=True
        )

        for coin, counts in sorted_coins[:10]:
            parts = []

            if counts['opened']:
                parts.append(f"+{counts['opened']} opened")
            if counts['closed']:
                parts.append(f"-{counts['closed']} closed")
            if counts['liquidated']:
                parts.append(f"💀{counts['liquidated']} liq")
            if counts['increased']:
                parts.append(f"↑{counts['increased']} added")
            if counts['reduced']:
                parts.append(f"↓{counts['reduced']} reduced")
            if counts['leverage']:
                parts.append(f"⚡{counts['leverage']} lev")

            activity_str = ", ".join(parts) if parts else "no changes"

            # Net flow
            net = counts['net_value']
            if abs(net) >= 1_000_000:
                net_str = f"+${net / 1_000_000:.1f}M" if net > 0 else f"-${abs(net) / 1_000_000:.1f}M"
            elif abs(net) >= 1_000:
                net_str = f"+${net / 1_000:.0f}K" if net > 0 else f"-${abs(net) / 1_000:.0f}K"
            else:
                net_str = "~neutral"

            if net > 0:
                net_str += " long"
            elif net < 0:
                net_str += " short"

            logger.info(f"  {coin:<8} │ {activity_str:<55} │ {net_str:<20}")

        # Account events summary
        if account_events:
            margin_added = [e for e in account_events if e['event_type'] == 'margin_added']
            margin_removed = [e for e in account_events if e['event_type'] == 'margin_removed']

            if margin_added or margin_removed:
                added_total = sum(e['new_value'] - e['old_value'] for e in margin_added)
                removed_total = sum(e['old_value'] - e['new_value'] for e in margin_removed)

                logger.info(
                    f"  {'MARGIN':<8} │ +{len(margin_added)} added (${added_total / 1000:.0f}K), -{len(margin_removed)} removed (${removed_total / 1000:.0f}K)")

        logger.info("─" * 90)