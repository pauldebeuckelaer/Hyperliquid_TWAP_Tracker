#!/usr/bin/env python3
"""
Liquidation Tracker (Analyzer)
==============================
Pure consumer of whale_states. Computes liquidation exposure, emits logs,
persists liquidation_snapshots.

After Step 3 of the refactor, this class no longer:
- Fetches whale states from the API (moved to WhaleStateCollector)
- Writes to perp_snapshots or perp_account_snapshots (moved to Collector)
- Calls WhaleEventDetector (called directly from main.py now)
- Depends on tier_manager (Collector handles tier-driven fetching)

It only:
- Takes whale_states + prices that Collector produced
- Parses positions into per-coin exposure buckets
- Computes distance-to-liquidation per position
- Writes liquidation_snapshots (aggregated)
- Emits the danger/warning/watch summary log
- Emits the per-coin and per-address detail logs

Class name kept as LiquidationTracker for stable imports. The role shifted
from "tracker (fetches and writes)" to "analyzer (consumes and writes)".

Usage:
    liq_tracker = LiquidationTracker(storage, config=...)

    # Each cycle (called from main.py after Collector produces whale_states):
    liq_tracker.analyze(whale_states, prices)
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)
liq_coins_logger = logging.getLogger(f"{__name__}.coins")
liq_addrs_logger = logging.getLogger(f"{__name__}.addresses")


class LiquidationTracker:
    """
    Liquidation exposure analyzer. Consumes whale_states; writes only
    to liquidation_snapshots.
    """

    def __init__(self, storage, config: Optional[Dict] = None):
        """
        Args:
            storage: SQLiteBackend instance (for save_liquidation_snapshot)
            config: Optional configuration dict
        """
        self.storage = storage
        self.config = config or {}

        # Stats
        self.snapshot_count = 0
        self.last_snapshot_time: Optional[datetime] = None
        self._hip3_positions_seen = 0

        logger.info("LiquidationTracker initialized (analyzer mode)")

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def analyze(self, whale_states: List[Dict], prices: Dict) -> Dict:
        """
        Analyze pre-fetched whale_states for liquidation exposure.

        Args:
            whale_states: List of whale state dicts (produced by Collector)
            prices: Dict of coin -> price (includes HIP-3 prices if enabled)

        Returns:
            Dict with analysis stats:
                {
                    'timestamp': str,
                    'whales_analyzed': int,
                    'positions_analyzed': int,
                    'coins_with_exposure': int,
                }
        """
        timestamp = datetime.now()

        if not whale_states:
            logger.debug("No whale_states to analyze")
            return {
                'timestamp': timestamp.isoformat(),
                'whales_analyzed': 0,
                'positions_analyzed': 0,
                'coins_with_exposure': 0,
            }

        if not prices:
            logger.warning("No prices provided — cannot compute distance-to-liq")
            return {
                'timestamp': timestamp.isoformat(),
                'whales_analyzed': len(whale_states),
                'positions_analyzed': 0,
                'coins_with_exposure': 0,
            }

        coin_exposure = self._parse_liquidation_exposure(whale_states, prices)

        # Emit logs (three streams)
        self._log_liquidation_summary(coin_exposure)
        self._log_liquidation_coins(coin_exposure)
        self._log_liquidation_addresses(coin_exposure)

        # Persist (the only table this class writes to)
        self.storage.save_liquidation_snapshot(timestamp.isoformat(), coin_exposure)

        # Count positions for stats
        total_positions = sum(len(d['positions']) for d in coin_exposure.values())

        self.snapshot_count += 1
        self.last_snapshot_time = timestamp

        return {
            'timestamp': timestamp.isoformat(),
            'whales_analyzed': len(whale_states),
            'positions_analyzed': total_positions,
            'coins_with_exposure': len(coin_exposure),
        }

    # =========================================================================
    # INTERNAL: EXPOSURE PARSING
    # =========================================================================

    def _calculate_distance_to_liq(
            self, current_price: float, liq_price: float, side: str
    ) -> float:
        """
        Percentage distance from current price to liquidation.
        Positive = safe, negative = past liq.
        """
        if current_price == 0 or liq_price == 0:
            return 100.0  # No valid data — treat as safe

        if side == 'LONG':
            return (current_price - liq_price) / current_price * 100
        else:
            return (liq_price - current_price) / current_price * 100

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
            spot_usdc: float,
    ):
        """Add a single position to the coin_exposure aggregation."""
        size = float(pos.get('szi', 0))
        if size == 0:
            return

        entry_price = float(pos.get('entryPx', 0))
        liq_price_str = pos.get('liquidationPx')
        liq_price = float(liq_price_str) if liq_price_str else 0
        position_value = float(pos.get('positionValue', 0))
        margin_used = float(pos.get('marginUsed', 0))

        leverage_data = pos.get('leverage', {})
        leverage = (
            leverage_data.get('value', 0)
            if isinstance(leverage_data, dict) else 0
        )

        unrealized_pnl = float(pos.get('unrealizedPnl', 0))
        cum_funding = pos.get('cumFunding', {})
        funding_since_open = float(cum_funding.get('sinceOpen', 0))

        side = 'LONG' if size > 0 else 'SHORT'
        current_price = float(prices.get(coin, 0))

        if current_price > 0 and liq_price > 0:
            distance = self._calculate_distance_to_liq(current_price, liq_price, side)
        else:
            distance = 100.0

        pnl_pct = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0

        if coin not in coin_exposure:
            coin_exposure[coin] = {
                'total_value': 0,
                'long_value': 0,
                'short_value': 0,
                'positions': [],
            }

        coin_exposure[coin]['total_value'] += abs(position_value)

        if side == 'LONG':
            coin_exposure[coin]['long_value'] += position_value
        else:
            coin_exposure[coin]['short_value'] += abs(position_value)

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

    def _parse_liquidation_exposure(
            self, whale_states: List[Dict], prices: Dict
    ) -> Dict:
        """
        Aggregate whale positions into per-coin exposure buckets.

        Handles both main perp and HIP-3 positions. HIP-3 positions have
        dex-prefixed coin names (e.g., "xyz:TSLA") matching the prices dict.
        """
        coin_exposure = {}

        for whale_data in whale_states:
            state = whale_data.get('state', {})
            address = whale_data.get('address', '')

            margin_summary = state.get('marginSummary', {})
            account_value = float(margin_summary.get('accountValue', 0))
            total_margin_used = float(margin_summary.get('totalMarginUsed', 0))
            withdrawable = float(state.get('withdrawable', 0))
            spot_usdc = whale_data.get('spot_usdc', 0)

            # Main perp positions
            for pos_data in state.get('assetPositions', []):
                pos = pos_data.get('position', {})
                coin = pos.get('coin', '')
                if not coin:
                    continue

                size = float(pos.get('szi', 0))
                if size == 0:
                    continue

                self._add_position_to_exposure(
                    coin_exposure, address, coin, pos, prices,
                    account_value, total_margin_used, withdrawable, spot_usdc,
                )

            # HIP-3 positions (coin already prefixed with "dex:")
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
                    account_value, total_margin_used, withdrawable, spot_usdc,
                )
                self._hip3_positions_seen += 1

        # Sort positions within each coin by distance to liquidation
        for coin in coin_exposure:
            coin_exposure[coin]['positions'].sort(key=lambda x: x['distance_to_liq'])

        return coin_exposure

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
    # LOGGING (three streams: summary, coins, addresses)
    # =========================================================================

    def _log_liquidation_summary(self, coin_exposure: Dict):
        """High-level danger/warning/watch counts to the main log."""
        if not coin_exposure:
            return

        danger_count = 0     # < 5%
        warning_count = 0    # 5-10%
        watch_count = 0      # 10-20%
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

                if is_hip3 and dist <= 20:
                    hip3_count += 1

        risky_positions = danger_count + warning_count + watch_count

        if risky_positions > 0:
            hip3_tag = f" | 🏗️ {hip3_count} HIP-3" if hip3_count > 0 else ""
            logger.info(
                f"🔥 Liquidation risk: {danger_count} DANGER (<5%), "
                f"{warning_count} WARNING (5-10%), {watch_count} WATCH (10-20%)"
                f"{hip3_tag}"
            )

    def _log_liquidation_coins(self, coin_exposure: Dict):
        """Per-coin summary to dedicated coins log file."""
        if not coin_exposure:
            return

        # Filter: only coins with positions within 20%
        filtered = {}
        for coin, data in coin_exposure.items():
            danger_positions = [p for p in data['positions'] if p['distance_to_liq'] <= 20]
            if danger_positions:
                closest = danger_positions[0]
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

        sorted_coins = sorted(filtered.items(), key=lambda x: x[1]['closest_dist'])
        total_positions = sum(d['position_count'] for d in filtered.values())

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
        """Per-address detail to dedicated addresses log file."""
        if not coin_exposure:
            return

        danger_positions = []
        for coin, data in coin_exposure.items():
            for pos in data['positions']:
                if pos['distance_to_liq'] <= 20:
                    danger_positions.append(pos)

        if not danger_positions:
            liq_addrs_logger.info("No positions within 20% of liquidation")
            return

        danger_positions.sort(key=lambda x: x['distance_to_liq'])

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
            pnl = pos['pnl_pct']
            pnl_str = f"+{pnl:.1f}%" if pnl > 0 else f"{pnl:.1f}%"

            funding = pos['funding_since_open']
            if abs(funding) >= 1000:
                fund_str = f"${funding / 1000:.1f}K"
            else:
                fund_str = f"${funding:.0f}"

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
        """Get analyzer statistics."""
        return {
            'snapshot_count': self.snapshot_count,
            'last_snapshot': self.last_snapshot_time.isoformat() if self.last_snapshot_time else None,
            'hip3_positions_seen': self._hip3_positions_seen,
        }