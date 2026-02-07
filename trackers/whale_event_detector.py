#!/usr/bin/env python3
"""
Whale Event Detector
====================
Detects whale position and account changes between snapshots.

Events detected:
- position_opened: New position appeared
- position_closed: Position disappeared
- position_liquidated: Position disappeared while within 0.1% of liquidation price
- position_increased: Position size increased
- position_reduced: Position size decreased
- leverage_changed: Leverage modified
- margin_added: Account margin increased
- margin_removed: Account margin decreased
- account_funded: Deposit detected (unexplained account value increase)
- account_withdrawn: Withdrawal detected (unexplained account value decrease)
- pnl_realized: Account value increased due to position close (not a deposit)

Features:
- Debounce for positions (prevents false close/open from API timeouts)
- Debounce for margin (prevents false margin_removed from API failures)
- Only tracks VIP + Tier1 addresses (consistent minute-by-minute data)
- Blacklist for addresses that generate noise (HFT market makers, etc.)

Usage:
    from whale_event_detector import WhaleEventDetector
    from tier_manager import TierManager

    tier_mgr = TierManager(storage)
    detector = WhaleEventDetector(tier_mgr)

    # Each cycle:
    events = detector.detect(whale_states, prices)
    detector.log_summary(events)
"""
import logging
from datetime import datetime
from typing import Dict, List, Set, Optional

logger = logging.getLogger(__name__)


class WhaleEventDetector:
    """
    Detects whale position/account changes between snapshots.

    Uses debouncing to prevent false events from API timeouts.
    Only tracks addresses in VIP + Tier1 for reliable event detection.
    Blacklisted addresses are excluded entirely from event detection.
    """

    # ===== DETECTION THRESHOLDS =====
    SIZE_CHANGE_PCT = 0.0005  # 1% minimum size change to trigger event
    SIZE_CHANGE_MIN_USD = 1_000  # $10K minimum dollar change
    MARGIN_CHANGE_PCT = 0.05  # 5% minimum margin change
    MARGIN_CHANGE_MIN_USD = 1_000  # $1K minimum dollar change

    # ===== ACCOUNT FUNDING THRESHOLDS =====
    FUNDING_MIN_USD = 50_000  # $50K minimum unexplained delta to fire event

    # ===== LIQUIDATION DETECTION =====
    LIQUIDATION_DISTANCE_PCT = 0.001  # 0.1% — if liq distance was below this when position vanished, it's a liquidation

    # ===== DEBOUNCE SETTINGS =====
    POSITION_DEBOUNCE_THRESHOLD = 3  # Must be missing 3 cycles to count as closed
    MARGIN_DEBOUNCE_THRESHOLD = 3  # Must be zero 3 cycles to count as removed

    # ===== BLACKLISTED ADDRESSES =====
    # Addresses excluded from event detection entirely (e.g. HFT market makers)
    # These generate noise — hundreds of rapid position flips with no directional signal
    BLACKLISTED_ADDRESSES: Set[str] = {
        '0x687feda45b6847763f5bf5c01a2f6c1a3d727f5c',
        '0x2025137a136bea7446deba681cbfc7cf1970840e',# HFT market maker — false event spam
    }

    # ================================

    def __init__(self, tier_manager):
        """
        Initialize event detector.

        Args:
            tier_manager: TierManager instance for getting event tracking addresses
        """
        self.tier_manager = tier_manager

        # State tracking
        self.previous_state: Dict[str, Dict] = {}
        self.snapshot_count = 0

        # Debounce tracking - prevents false events from API timeouts
        self._missing_positions: Dict[tuple, Dict] = {}  # {(address, coin): {'count': int, 'last_seen': position_data}}
        self._zero_margin: Dict[str, int] = {}  # {address: consecutive_zero_count}

        logger.info(f"WhaleEventDetector initialized (blacklisted: {len(self.BLACKLISTED_ADDRESSES)})")

    def _classify_position_close(self, last_seen: Dict, current_price: float) -> str:
        """
        Determine if a closed position was a liquidation or voluntary close.

        Checks the liquidation distance at the last snapshot before the position
        disappeared. If it was below LIQUIDATION_DISTANCE_PCT, classify as liquidated.

        Args:
            last_seen: Last known position data before it disappeared
            current_price: Current price of the coin

        Returns:
            'position_liquidated' or 'position_closed'
        """
        liq_price = last_seen.get('liq_price', 0)
        # Use the last known current price, or entry as fallback
        price = current_price if current_price > 0 else last_seen.get('entry_price', 0)

        if liq_price > 0 and price > 0:
            liq_distance = abs(price - liq_price) / price
            if liq_distance < self.LIQUIDATION_DISTANCE_PCT:
                return 'position_liquidated'

        return 'position_closed'

    def _parse_current_state(self, whale_states: List[Dict]) -> Dict[str, Dict]:
        """
        Parse whale states into comparable format.

        Only includes addresses that are in VIP + Tier1 (event tracking).
        Excludes blacklisted addresses entirely.

        Args:
            whale_states: List of whale state dicts from API

        Returns:
            Dict of address -> {positions: {coin: position_data}, account: account_data}
        """
        event_addresses = self.tier_manager.get_event_tracking_addresses()
        state = {}

        for whale_data in whale_states:
            address = whale_data.get('address', '')
            raw_state = whale_data.get('state', {})

            if not address:
                continue

            # Skip blacklisted addresses (HFT bots, market makers, etc.)
            if address in self.BLACKLISTED_ADDRESSES:
                continue

            # Only track VIP + Tier1 addresses
            if address not in event_addresses:
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
                    'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                    'return_on_equity': float(pos.get('returnOnEquity', 0)),
                    'funding_since_open': float((pos.get('cumFunding') or {}).get('sinceOpen', 0)),
                    'funding_all_time': float((pos.get('cumFunding') or {}).get('allTime', 0)),
                }

            state[address] = {
                'positions': positions,
                'account': account
            }

        return state

    def detect(self, whale_states: List[Dict], prices: Dict) -> List[Dict]:
        """
        Compare current state to previous and detect events.

        Uses debouncing to prevent false events from API timeouts:
        - Positions must be missing for 3 cycles to count as closed
        - Margin must be zero for 3 cycles to count as removed

        Args:
            whale_states: Current whale states from API
            prices: Current prices dict {coin: price}

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
            logger.debug(f"First snapshot - tracking {len(current_state)} addresses")
            return events

        # Get event tracking addresses
        event_addresses = self.tier_manager.get_event_tracking_addresses()

        # Compare each address that's in our tracking list
        for address in event_addresses:
            # Skip blacklisted addresses
            if address in self.BLACKLISTED_ADDRESSES:
                continue

            prev = self.previous_state.get(address, {'positions': {}, 'account': {}})
            curr = current_state.get(address, {'positions': {}, 'account': {}})

            prev_positions = prev.get('positions', {})
            curr_positions = curr.get('positions', {})
            prev_account = prev.get('account', {})
            curr_account = curr.get('account', {})

            # ===== POSITION EVENTS =====
            position_events = self._detect_position_events(
                address, prev_positions, curr_positions, prices, timestamp
            )
            events.extend(position_events)

            # ===== ACCOUNT EVENTS (margin changes) =====
            account_events = self._detect_account_events(
                address, prev_account, curr_account, timestamp
            )
            events.extend(account_events)

            # ===== FUNDING EVENTS (deposits/withdrawals) =====
            funding_events = self._detect_funding_events(
                address, prev_positions, curr_positions,
                prev_account, curr_account, position_events, timestamp
            )
            events.extend(funding_events)

        # Update state for next comparison
        self.previous_state = current_state
        self.snapshot_count += 1

        return events

    def _detect_position_events(
            self,
            address: str,
            prev_positions: Dict,
            curr_positions: Dict,
            prices: Dict,
            timestamp: datetime
    ) -> List[Dict]:
        """
        Detect position-related events for an address.

        Uses smart debouncing:
        - Position must be missing for POSITION_DEBOUNCE_THRESHOLD cycles to count as closed
        - If position reappears during debounce, compare entry price:
          - Same entry price → API flicker, cancel debounce
          - Different entry price → close + reopen, fire both events
        """
        events = []
        all_coins = set(prev_positions.keys()) | set(curr_positions.keys())

        # Also include coins that are in debounce for this address
        # (they may have fallen out of prev_positions after state update)
        for (debounce_addr, debounce_coin) in list(self._missing_positions.keys()):
            if debounce_addr == address:
                all_coins.add(debounce_coin)

        for coin in all_coins:
            in_prev = coin in prev_positions
            in_curr = coin in curr_positions
            key = (address, coin)

            if not in_prev and in_curr:
                # Position appeared
                pos = curr_positions[coin]

                if key in self._missing_positions:
                    # Was missing, now back — is it the SAME position or a NEW one?
                    last_seen = self._missing_positions[key]['last_seen']

                    if last_seen['entry_price'] != pos['entry_price']:
                        # DIFFERENT entry price → close + reopen (not API flicker!)
                        # Fire position_closed/liquidated for the old position
                        close_type = self._classify_position_close(last_seen, prices.get(coin, 0))
                        events.append({
                            'timestamp': timestamp,
                            'address': address,
                            'event_type': close_type,
                            'coin': coin,
                            'side': last_seen['side'],
                            'old_value': last_seen['value'],
                            'new_value': 0,
                            'size': last_seen['abs_size'],
                            'leverage': last_seen['leverage'],
                            'entry_price': last_seen['entry_price'],
                            'current_price': prices.get(coin, 0),
                            'liq_price': last_seen['liq_price'],
                            'margin_used': last_seen['margin_used'],
                            'unrealized_pnl': last_seen['unrealized_pnl'],
                            'return_on_equity': last_seen['return_on_equity'],
                            'funding_since_open': last_seen['funding_since_open'],
                        })
                        logger.debug(
                            f"{close_type} (close+reopen) | {address} | "
                            f"{coin} {last_seen['side']} ${last_seen['value']:,.0f} entry {self._format_price(last_seen['entry_price'])}"
                        )

                        # Fire position_opened for the new position
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
                            'liq_price': pos['liq_price'],
                            'margin_used': pos['margin_used'],
                            'unrealized_pnl': pos['unrealized_pnl'],
                            'return_on_equity': pos['return_on_equity'],
                            'funding_since_open': pos['funding_since_open'],
                        })
                        logger.debug(
                            f"position_opened (close+reopen) | {address} | "
                            f"{coin} {pos['side']} ${pos['value']:,.0f} entry {self._format_price(pos['entry_price'])}"
                        )
                    else:
                        # Same entry price → API flicker, cancel debounce
                        logger.debug(f"Position reappeared (API flicker): {address} {coin}")

                    del self._missing_positions[key]
                else:
                    # Not in debounce — truly new position
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
                        'liq_price': pos['liq_price'],
                        'margin_used': pos['margin_used'],
                        'unrealized_pnl': pos['unrealized_pnl'],
                        'return_on_equity': pos['return_on_equity'],
                        'funding_since_open': pos['funding_since_open'],
                    })
                    logger.debug(
                        f"position_opened | {address} | "
                        f"{coin} {pos['side']} ${pos['value']:,.0f} {pos['leverage']}x"
                    )

            elif in_prev and not in_curr:
                # Position missing — store last seen data and start/increment counter
                pos = prev_positions[coin]

                if key not in self._missing_positions:
                    # First cycle missing — store the position data
                    self._missing_positions[key] = {
                        'count': 1,
                        'last_seen': pos,
                    }
                else:
                    self._missing_positions[key]['count'] += 1

                missing_count = self._missing_positions[key]['count']

                if missing_count >= self.POSITION_DEBOUNCE_THRESHOLD:
                    # Missing for enough cycles — really closed
                    last_seen = self._missing_positions[key]['last_seen']
                    del self._missing_positions[key]

                    close_type = self._classify_position_close(last_seen, prices.get(coin, 0))
                    events.append({
                        'timestamp': timestamp,
                        'address': address,
                        'event_type': close_type,
                        'coin': coin,
                        'side': last_seen['side'],
                        'old_value': last_seen['value'],
                        'new_value': 0,
                        'size': last_seen['abs_size'],
                        'leverage': last_seen['leverage'],
                        'entry_price': last_seen['entry_price'],
                        'current_price': prices.get(coin, 0),
                        'liq_price': last_seen['liq_price'],
                        'margin_used': last_seen['margin_used'],
                        'unrealized_pnl': last_seen['unrealized_pnl'],
                        'return_on_equity': last_seen['return_on_equity'],
                        'funding_since_open': last_seen['funding_since_open'],
                    })
                    logger.debug(
                        f"{close_type} | {address} | "
                        f"{coin} {last_seen['side']} ${last_seen['value']:,.0f}"
                    )
                else:
                    logger.debug(
                        f"Position missing ({missing_count}/{self.POSITION_DEBOUNCE_THRESHOLD}): "
                        f"{address} {coin}"
                    )

            elif in_prev and in_curr:
                # Position exists in both — clear any debounce and check for changes
                if key in self._missing_positions:
                    del self._missing_positions[key]

                prev_pos = prev_positions[coin]
                curr_pos = curr_positions[coin]

                # Check for size changes
                size_events = self._detect_size_change(
                    address, coin, prev_pos, curr_pos, prices, timestamp
                )
                events.extend(size_events)

                # Check for leverage changes
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
                        'liq_price': curr_pos['liq_price'],
                        'margin_used': curr_pos['margin_used'],
                        'unrealized_pnl': curr_pos['unrealized_pnl'],
                        'return_on_equity': curr_pos['return_on_equity'],
                        'funding_since_open': curr_pos['funding_since_open'],
                        'entry_price': curr_pos['entry_price'],
                    })
                    logger.debug(
                        f"leverage_changed | {address} | "
                        f"{coin} {prev_pos['leverage']}x → {curr_pos['leverage']}x"
                    )

            elif not in_prev and not in_curr and key in self._missing_positions:
                # Not in prev or curr, but still in debounce — keep counting
                self._missing_positions[key]['count'] += 1
                missing_count = self._missing_positions[key]['count']

                if missing_count >= self.POSITION_DEBOUNCE_THRESHOLD:
                    # Missing for enough cycles — really closed
                    last_seen = self._missing_positions[key]['last_seen']
                    del self._missing_positions[key]

                    close_type = self._classify_position_close(last_seen, prices.get(coin, 0))
                    events.append({
                        'timestamp': timestamp,
                        'address': address,
                        'event_type': close_type,
                        'coin': coin,
                        'side': last_seen['side'],
                        'old_value': last_seen['value'],
                        'new_value': 0,
                        'size': last_seen['abs_size'],
                        'leverage': last_seen['leverage'],
                        'entry_price': last_seen['entry_price'],
                        'current_price': prices.get(coin, 0),
                        'liq_price': last_seen['liq_price'],
                        'margin_used': last_seen['margin_used'],
                        'unrealized_pnl': last_seen['unrealized_pnl'],
                        'return_on_equity': last_seen['return_on_equity'],
                        'funding_since_open': last_seen['funding_since_open'],
                    })
                    logger.debug(
                        f"{close_type} | {address} | "
                        f"{coin} {last_seen['side']} ${last_seen['value']:,.0f}"
                    )
                else:
                    logger.debug(
                        f"Position missing ({missing_count}/{self.POSITION_DEBOUNCE_THRESHOLD}): "
                        f"{address} {coin}"
                    )

        return events

    def _detect_size_change(
            self,
            address: str,
            coin: str,
            prev_pos: Dict,
            curr_pos: Dict,
            prices: Dict,
            timestamp: datetime
    ) -> List[Dict]:
        """Detect position size increase/decrease."""
        events = []

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
                'entry_price': curr_pos['entry_price'],
                'liq_price': curr_pos['liq_price'],
                'margin_used': curr_pos['margin_used'],
                'unrealized_pnl': curr_pos['unrealized_pnl'],
                'return_on_equity': curr_pos['return_on_equity'],
                'funding_since_open': curr_pos['funding_since_open'],
            })
            logger.debug(
                f"{event_type} | {address} | "
                f"{coin} {curr_pos['side']} ${prev_pos['value']:,.0f} → ${curr_pos['value']:,.0f}"
            )

        return events

    def _detect_account_events(
            self,
            address: str,
            prev_account: Dict,
            curr_account: Dict,
            timestamp: datetime
    ) -> List[Dict]:
        """
        Detect account-level events (margin changes).

        Uses debouncing: margin must be zero for MARGIN_DEBOUNCE_THRESHOLD
        consecutive cycles before triggering margin_removed.
        """
        events = []

        prev_margin = prev_account.get('margin_used', 0)
        curr_margin = curr_account.get('margin_used', 0)

        # ===== MARGIN DEBOUNCE LOGIC =====
        # If current margin is 0 (or very small), it might be API failure
        if curr_margin < 100:  # Essentially zero
            self._zero_margin[address] = self._zero_margin.get(address, 0) + 1
            zero_count = self._zero_margin[address]

            if zero_count < self.MARGIN_DEBOUNCE_THRESHOLD:
                # Not enough consecutive zeros - skip this cycle
                logger.debug(
                    f"Margin zero ({zero_count}/{self.MARGIN_DEBOUNCE_THRESHOLD}): "
                    f"{address} - likely API failure"
                )
                return events
            # else: enough consecutive zeros, this is real - continue to detection
        else:
            # Margin is not zero - clear the counter
            if address in self._zero_margin:
                del self._zero_margin[address]

        # ===== MARGIN CHANGE DETECTION =====
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
                f"{event_type} | {address} | "
                f"${prev_margin:,.0f} → ${curr_margin:,.0f}"
            )

        return events

    def _detect_funding_events(
            self,
            address: str,
            prev_positions: Dict,
            curr_positions: Dict,
            prev_account: Dict,
            curr_account: Dict,
            position_events: List[Dict],
            timestamp: datetime
    ) -> List[Dict]:
        """
        Detect account deposits and withdrawals (approach 2b).

        Compares account value between snapshots and subtracts PnL changes
        to isolate unexplained deltas that indicate real fund transfers.

        Layer 1: Raw delta → always logged at DEBUG
        Layer 2: PnL-adjusted delta → fires events when above threshold
        Layer 3: Context from position events → classifies as deposit vs realized PnL

        Args:
            address: Whale address
            prev_positions: Previous cycle positions
            curr_positions: Current cycle positions
            prev_account: Previous cycle account data
            curr_account: Current cycle account data
            position_events: Position events detected this cycle for this address
            timestamp: Current timestamp

        Returns:
            List of funding event dicts
        """
        events = []

        prev_value = prev_account.get('account_value', 0)
        curr_value = curr_account.get('account_value', 0)

        # Skip if we don't have both values
        if prev_value == 0 and curr_value == 0:
            return events

        # ===== LAYER 1: Raw delta (DEBUG) =====
        raw_delta = curr_value - prev_value

        if abs(raw_delta) >= 10_000:  # Log anything above $10K
            if abs(raw_delta) >= 1_000_000:
                delta_str = f"+${raw_delta / 1_000_000:.2f}M" if raw_delta > 0 else f"-${abs(raw_delta) / 1_000_000:.2f}M"
            else:
                delta_str = f"+${raw_delta / 1_000:.0f}K" if raw_delta > 0 else f"-${abs(raw_delta) / 1_000:.0f}K"
            logger.debug(
                f"account_delta | {address} | "
                f"${prev_value:,.0f} → ${curr_value:,.0f} ({delta_str})"
            )

        # ===== LAYER 2: PnL-adjusted delta =====
        # Calculate how much of the account change is explained by position PnL movement
        pnl_delta = 0.0

        # Get all coins that exist in either snapshot
        all_coins = set(prev_positions.keys()) | set(curr_positions.keys())

        for coin in all_coins:
            prev_pnl = prev_positions.get(coin, {}).get('unrealized_pnl', 0)
            curr_pnl = curr_positions.get(coin, {}).get('unrealized_pnl', 0)
            pnl_delta += (curr_pnl - prev_pnl)

        # The unexplained portion — what PnL can't account for
        unexplained_delta = raw_delta - pnl_delta

        # ===== LAYER 3: Context — check for position closes/opens =====
        # If positions were closed this cycle, realized PnL returns to account
        # This looks like a deposit but isn't
        has_position_closed = any(
            e['event_type'] in ('position_closed', 'position_liquidated') and e['address'] == address
            for e in position_events
        )
        has_position_reduced = any(
            e['event_type'] == 'position_reduced' and e['address'] == address
            for e in position_events
        )
        has_position_opened = any(
            e['event_type'] == 'position_opened' and e['address'] == address
            for e in position_events
        )

        # ===== FIRE EVENTS =====
        if abs(unexplained_delta) >= self.FUNDING_MIN_USD:
            if unexplained_delta > 0:
                # Positive unexplained delta
                if has_position_closed or has_position_reduced:
                    # Likely realized PnL, not a deposit
                    event_type = 'pnl_realized'
                else:
                    # No position activity explains it — real deposit
                    event_type = 'account_funded'
            else:
                # Negative unexplained delta
                if has_position_opened:
                    # Could be margin allocated to new position (not a withdrawal)
                    # Skip — this is handled by position_opened events
                    return events
                else:
                    event_type = 'account_withdrawn'

            events.append({
                'timestamp': timestamp,
                'address': address,
                'event_type': event_type,
                'coin': None,
                'side': None,
                'old_value': prev_value,
                'new_value': curr_value,
                'raw_delta': raw_delta,
                'pnl_delta': pnl_delta,
                'unexplained_delta': unexplained_delta,
                'account_value': curr_value,
                'withdrawable': curr_account.get('withdrawable', 0),
            })

            if abs(unexplained_delta) >= 1_000_000:
                delta_str = f"+${unexplained_delta / 1_000_000:.2f}M" if unexplained_delta > 0 else f"-${abs(unexplained_delta) / 1_000_000:.2f}M"
            else:
                delta_str = f"+${unexplained_delta / 1_000:.0f}K" if unexplained_delta > 0 else f"-${abs(unexplained_delta) / 1_000:.0f}K"

            logger.debug(
                f"{event_type} | {address} | "
                f"unexplained: {delta_str} (raw: ${raw_delta:,.0f}, pnl: ${pnl_delta:,.0f})"
            )

        return events

    @staticmethod
    def _format_price(price: float) -> str:
        """Format price with appropriate decimal places for any token price range."""
        if price == 0:
            return '$0'
        elif price >= 1000:
            return f'${price:,.0f}'
        elif price >= 1:
            return f'${price:,.2f}'
        elif price >= 0.01:
            return f'${price:.4f}'
        else:
            return f'${price:.6f}'

    def _format_pnl_context(self, event: Dict) -> str:
        """Format PnL/intent context string for log output."""
        parts = []

        # Entry price
        entry = event.get('entry_price', 0)
        current = event.get('current_price', 0)
        if entry > 0:
            parts.append(f"entry {self._format_price(entry)}")

        # Unrealized PnL
        pnl = event.get('unrealized_pnl', 0)
        if abs(pnl) >= 1000:
            if abs(pnl) >= 1_000_000:
                pnl_str = f"+${pnl / 1_000_000:.1f}M" if pnl > 0 else f"-${abs(pnl) / 1_000_000:.1f}M"
            else:
                pnl_str = f"+${pnl / 1_000:.0f}K" if pnl > 0 else f"-${abs(pnl) / 1_000:.0f}K"
            parts.append(f"PnL {pnl_str}")

        # ROE
        roe = event.get('return_on_equity', 0)
        if abs(roe) > 0.01:
            roe_pct = roe * 100
            parts.append(f"ROE {roe_pct:+.0f}%")

        # Liquidation distance
        liq = event.get('liq_price', 0)
        if liq > 0 and current > 0:
            liq_dist_pct = ((current - liq) / current) * 100
            if abs(liq_dist_pct) < 20:
                parts.append(f"⚠️ liq {abs(liq_dist_pct):.0f}% away")

        return " | ".join(parts) if parts else ""

    def log_summary(self, events: List[Dict]):
        """
        Log INFO level aggregate summary of events with whale addresses.

        Args:
            events: List of event dicts from detect()
        """
        if not events:
            return

        # Count events by type and coin
        by_coin = {}
        account_events = []

        for event in events:
            event_type = event['event_type']
            coin = event.get('coin')
            address = event.get('address', '')

            if coin:
                if coin not in by_coin:
                    by_coin[coin] = {
                        'opened': [],
                        'closed': [],
                        'liquidated': [],
                        'increased': [],
                        'reduced': [],
                        'leverage': [],
                        'net_value': 0,
                    }

                side_mult = 1 if event.get('side') == 'LONG' else -1
                value_str = f"${event.get('new_value', 0) / 1000:.0f}K"
                ctx = self._format_pnl_context(event)
                ctx_str = f" ({ctx})" if ctx else ""

                if event_type == 'position_opened':
                    lev = event.get('leverage', 0)
                    by_coin[coin]['opened'].append(
                        f"{address} {event.get('side')} {value_str} {lev}x{ctx_str}"
                    )
                    by_coin[coin]['net_value'] += event['new_value'] * side_mult

                elif event_type == 'position_closed':
                    by_coin[coin]['closed'].append(
                        f"{address} {event.get('side')} {value_str}{ctx_str}"
                    )
                    by_coin[coin]['net_value'] -= event['old_value'] * side_mult

                elif event_type == 'position_liquidated':
                    by_coin[coin]['liquidated'].append(
                        f"{address} {event.get('side')} {value_str}{ctx_str}"
                    )
                    by_coin[coin]['net_value'] -= event['old_value'] * side_mult

                elif event_type == 'position_increased':
                    change = event['new_value'] - event['old_value']
                    by_coin[coin]['increased'].append(
                        f"{address} +${change / 1000:.0f}K → {value_str}{ctx_str}"
                    )
                    by_coin[coin]['net_value'] += change * side_mult

                elif event_type == 'position_reduced':
                    change = event['old_value'] - event['new_value']
                    by_coin[coin]['reduced'].append(
                        f"{address} -${change / 1000:.0f}K → {value_str}{ctx_str}"
                    )
                    by_coin[coin]['net_value'] -= change * side_mult

                elif event_type == 'leverage_changed':
                    old_lev = event.get('old_value', 0)
                    new_lev = event.get('new_value', 0)
                    by_coin[coin]['leverage'].append(
                        f"{address} {old_lev}x→{new_lev}x{ctx_str}"
                    )
            else:
                event['address_short'] = address
                account_events.append(event)

        # Log summary
        logger.info("")
        logger.info("─" * 90)
        logger.info(f"📈 WHALE ACTIVITY ({len(events)} events)")
        logger.info("─" * 90)

        # Sort by total activity
        sorted_coins = sorted(
            by_coin.items(),
            key=lambda x: len(x[1]['opened']) + len(x[1]['closed']) + len(x[1]['liquidated']) + len(x[1]['increased']) + len(x[1]['reduced']),
            reverse=True
        )

        for coin, data in sorted_coins[:10]:
            # Net flow
            net = data['net_value']
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

            logger.info(f"  {coin:<8} │ Net: {net_str}")

            if data['opened']:
                logger.info(f"           │   +OPENED: {', '.join(data['opened'])}")
            if data['closed']:
                logger.info(f"           │   -CLOSED: {', '.join(data['closed'])}")
            if data['liquidated']:
                logger.info(f"           │   💀 LIQUIDATED: {', '.join(data['liquidated'])}")
            if data['increased']:
                logger.info(f"           │   ↑ADDED:  {', '.join(data['increased'])}")
            if data['reduced']:
                logger.info(f"           │   ↓REDUCED: {', '.join(data['reduced'])}")
            if data['leverage']:
                logger.info(f"           │   ⚡LEV:   {', '.join(data['leverage'])}")

        # Account events summary
        if account_events:
            logger.info(f"  {'MARGIN':<8} │")
            for event in account_events:
                addr = event['address_short']
                if event['event_type'] == 'margin_added':
                    change = event['new_value'] - event['old_value']
                    logger.info(f"           │   +ADDED:   {addr} +${change / 1000:.0f}K")
                elif event['event_type'] == 'margin_removed':
                    change = event['old_value'] - event['new_value']
                    logger.info(f"           │   -REMOVED: {addr} -${change / 1000:.0f}K")
                elif event['event_type'] == 'account_funded':
                    delta = event.get('unexplained_delta', 0)
                    acct = event.get('account_value', 0)
                    if abs(delta) >= 1_000_000:
                        delta_str = f"+${delta / 1_000_000:.2f}M"
                    else:
                        delta_str = f"+${delta / 1_000:.0f}K"
                    if acct >= 1_000_000:
                        acct_str = f"${acct / 1_000_000:.2f}M"
                    else:
                        acct_str = f"${acct / 1_000:.0f}K"
                    logger.info(f"           │   🚨 DEPOSIT:  {addr} {delta_str} → acct now {acct_str}")
                elif event['event_type'] == 'account_withdrawn':
                    delta = event.get('unexplained_delta', 0)
                    acct = event.get('account_value', 0)
                    if abs(delta) >= 1_000_000:
                        delta_str = f"-${abs(delta) / 1_000_000:.2f}M"
                    else:
                        delta_str = f"-${abs(delta) / 1_000:.0f}K"
                    if acct >= 1_000_000:
                        acct_str = f"${acct / 1_000_000:.2f}M"
                    else:
                        acct_str = f"${acct / 1_000:.0f}K"
                    logger.info(f"           │   💸 WITHDRAW: {addr} {delta_str} → acct now {acct_str}")
                elif event['event_type'] == 'pnl_realized':
                    delta = event.get('unexplained_delta', 0)
                    if abs(delta) >= 1_000_000:
                        delta_str = f"+${delta / 1_000_000:.2f}M"
                    else:
                        delta_str = f"+${delta / 1_000:.0f}K"
                    logger.info(f"           │   💰 REALIZED: {addr} {delta_str} (profit taken)")

        logger.info("─" * 90)

    def get_stats(self) -> Dict:
        """
        Get detector statistics.

        Returns:
            Dict with detector state info
        """
        return {
            'snapshot_count': self.snapshot_count,
            'tracked_addresses': len(self.previous_state),
            'pending_position_debounce': len(self._missing_positions),
            'pending_margin_debounce': len(self._zero_margin),
            'event_tracking_addresses': len(self.tier_manager.get_event_tracking_addresses()),
            'blacklisted_addresses': len(self.BLACKLISTED_ADDRESSES),
        }

    def reset(self):
        """Reset detector state (useful for testing or after long pause)."""
        self.previous_state = {}
        self.snapshot_count = 0
        self._missing_positions = {}
        self._zero_margin = {}
        logger.info("WhaleEventDetector state reset")