#!/usr/bin/env python3
"""
TWAP Order Models for HypurrScan Data
======================================

Lightweight data models for handling TWAP orders from HypurrScan API.
Provides standardized structure and helper methods for TWAP analysis.

NOW WITH COIN REGISTRY SUPPORT FOR ALL COINS!

Classes:
    TWAPOrder: Individual TWAP order with parsing and analysis methods
    TWAPSnapshot: Collection of orders at a point in time
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging

# Import coin registry for multi-coin support
from coin_registry import get_market_type, get_coin_name

logger = logging.getLogger(__name__)


@dataclass
class TWAPOrder:
    """
    Standardized TWAP order from HypurrScan.

    Attributes:
        address: Shortened address (first 10 chars)
        full_address: Complete wallet address
        symbol: Trading symbol (e.g., 'HYPE', 'BTC', 'ETH')
        size: Order size in tokens
        side: Direction ('BUY', 'SELL')
        product_type: 'SPOT' or 'PERP'
        status: Current status ('active', 'canceled', 'error', etc.)
        duration_minutes: Total TWAP duration in minutes
        elapsed_minutes: Minutes elapsed since start (if calculable)
        progress_percent: Execution progress (0-100)
        timestamp: When this snapshot was taken
    """
    # Core fields from API
    address: str
    full_address: str
    symbol: str
    size: float
    side: str
    product_type: str
    status: str
    duration_minutes: int
    order_hash: str = ""
    asset_id: Optional[int] = None

    # Calculated/optional fields
    elapsed_minutes: Optional[int] = None
    progress_percent: Optional[float] = None
    timestamp: Optional[datetime] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_hypurr_data(cls, raw_order: Dict[str, Any], symbol: str) -> 'TWAPOrder':
        """
        Parse raw HypurrScan order data into standardized format.

        Args:
            raw_order: Raw order dict from HypurrScan API
            symbol: Trading symbol

        Returns:
            TWAPOrder instance
        """
        action = raw_order.get('action', {})
        twap_info = action.get('twap', {})

        # Get asset ID and determine market type using coin registry
        asset_id = twap_info.get('a', 0)

        # Use coin registry for market type detection (works for ALL coins!)
        product_type = get_market_type(asset_id)

        # Verify symbol matches (optional validation)
        registry_symbol = get_coin_name(asset_id)
        if registry_symbol != symbol and not registry_symbol.startswith('UNKNOWN'):
            logger.debug(f"Symbol mismatch: expected {symbol}, registry says {registry_symbol}")

        # Side detection (b field: true=buy, false=sell)
        is_buy = twap_info.get('b', True)
        side = 'BUY' if is_buy else 'SELL'

        # Status detection
        ended_status = raw_order.get('ended')
        error_field = raw_order.get('error')

        if ended_status == 'canceled':
            status = 'canceled'
        elif ended_status == 'error' or error_field:
            status = 'error'
        elif ended_status is None and not error_field:
            status = 'active'
        elif ended_status:  # Has an 'ended' value that's not canceled/error
            # This is likely a completion - log it for investigation
            status = 'completed'  # Normalize to 'completed'
            logger.debug(f"🎯 COMPLETED ORDER DETECTED - ended field: {ended_status}")
        else:
            status = str(ended_status) if ended_status else 'unknown'

        # Address handling
        full_address = raw_order.get('user', raw_order.get('full_address', 'unknown'))
        short_address = full_address[:10] + '...' if len(full_address) > 10 else full_address

        # Calculate elapsed time and progress
        start_time_ms = raw_order.get('time')
        elapsed_minutes = None
        progress_percent = None

        if start_time_ms:
            start_time = datetime.fromtimestamp(start_time_ms / 1000)
            now = datetime.now()
            elapsed_minutes = int((now - start_time).total_seconds() / 60)

            duration_minutes = twap_info.get('m', 0)
            if duration_minutes > 0:
                progress_percent = round((elapsed_minutes / duration_minutes) * 100, 1)

        return cls(
            address=short_address,
            full_address=full_address,
            symbol=symbol,
            size=float(twap_info.get('s', 0)),
            side=side,
            product_type=product_type,
            status=status,
            duration_minutes=twap_info.get('m', 0),
            elapsed_minutes=elapsed_minutes,
            progress_percent=progress_percent,
            order_hash=raw_order.get('order_hash', raw_order.get('hash', '')),
            asset_id=asset_id,
            timestamp=datetime.now(),
            raw_data=raw_order
        )

    @property
    def time_remaining_minutes(self) -> Optional[int]:
        """Calculate minutes remaining until order completes"""
        if self.elapsed_minutes is not None and self.duration_minutes > 0:
            remaining = self.duration_minutes - self.elapsed_minutes
            return max(0, remaining)  # Don't go negative
        return None

    @property
    def display_address(self) -> str:
        """Format address for display: 0x1234...abcd"""
        if len(self.full_address) > 10:
            return f"{self.full_address[:6]}...{self.full_address[-4:]}"
        return self.full_address

    @property
    def is_active(self) -> bool:
        """Check if order is currently active"""
        return self.status == 'active'

    @property
    def is_canceled(self) -> bool:
        """Check if order was canceled"""
        return self.status == 'canceled'

    @property
    def is_completed(self) -> bool:
        """Check if order completed successfully"""
        return self.status in ['completed', 'filled', 'done']

    @property
    def is_buy_side(self) -> bool:
        """Check if this is a buy-side order (BUY or LONG)"""
        return self.side in ['BUY', 'LONG']

    @property
    def is_sell_side(self) -> bool:
        """Check if this is a sell-side order (SELL or SHORT)"""
        return self.side in ['SELL', 'SHORT']

    @property
    def is_whale(self) -> bool:
        """Simple whale detection (>50k tokens)"""
        return self.size > 50000

    @property
    def is_large_order(self) -> bool:
        """Detect large orders (>10k tokens)"""
        return self.size > 10000

    @property
    def duration_hours(self) -> float:
        """Get duration in hours"""
        return self.duration_minutes / 60.0

    @property
    def expected_end_time(self) -> Optional[datetime]:
        """Calculate expected completion time (if order is active)"""
        if self.timestamp and self.is_active and self.elapsed_minutes is not None:
            remaining_minutes = self.duration_minutes - self.elapsed_minutes
            return self.timestamp + timedelta(minutes=remaining_minutes)
        return None

    def get_execution_rate(self) -> float:
        """
        Calculate tokens per minute execution rate.

        Returns:
            Tokens executed per minute
        """
        if self.duration_minutes > 0:
            return self.size / self.duration_minutes
        return 0.0

    def format_summary(self) -> str:
        """Get formatted one-line summary"""
        status_emoji = {
            'active': '🟢',
            'canceled': '🔴',
            'error': '⚠️',
            'completed': '✅'
        }.get(self.status, '⚪')

        whale_flag = '🐋' if self.is_whale else '  '

        return (f"{status_emoji} {whale_flag} {self.display_address} | "
                f"{self.side:5} {self.size:10,.0f} {self.symbol} | "
                f"{self.product_type:4} | {self.duration_hours:.1f}h")

    def __str__(self):
        return (f"TWAPOrder({self.display_address}: {self.side} {self.size:,.0f} {self.symbol} "
                f"[{self.status}] over {self.duration_hours:.1f}h)")


@dataclass
class TWAPSnapshot:
    """
    Collection of TWAP orders at a specific point in time.

    Provides analysis methods for the entire order set.
    """
    orders: List[TWAPOrder]
    symbol: str
    timestamp: datetime
    update_number: int = 0
    current_price: Optional[float] = None

    @classmethod
    def from_hypurr_data(cls, raw_orders: List[Dict], symbol: str, update_number: int = 0, current_price: Optional[float] = None) -> 'TWAPSnapshot':
        """Create snapshot from raw HypurrScan data"""
        orders = [TWAPOrder.from_hypurr_data(order, symbol) for order in raw_orders]
        return cls(
            orders=orders,
            symbol=symbol,
            timestamp=datetime.now(),
            update_number=update_number,
            current_price=current_price
        )

    @property
    def total_orders(self) -> int:
        """Total number of orders"""
        return len(self.orders)

    @property
    def active_orders(self) -> List['TWAPOrder']:
        """
        Get only TRULY active orders.

        An order is truly active if:
        - status == 'active' (not 'canceled', 'completed', 'error')

        We ignore orders where status is 'canceled' even if they still
        appear in the API response (they linger for ~10-30 mins).
        """
        return [o for o in self.orders if o.status == 'active']

    @property
    def canceled_orders(self) -> List[TWAPOrder]:
        """Get only canceled orders"""
        return [o for o in self.orders if o.is_canceled]

    @property
    def whale_orders(self) -> List[TWAPOrder]:
        """Get whale orders (>50k tokens)"""
        return [o for o in self.orders if o.is_whale]

    @property
    def total_volume(self) -> float:
        """Calculate total volume across all orders"""
        return sum(o.size for o in self.orders)

    @property
    def active_volume(self) -> float:
        """Calculate volume of active orders only"""
        return sum(o.size for o in self.active_orders)

    @property
    def buy_volume(self) -> float:
        """Calculate total buy-side volume (ACTIVE orders only)"""
        return sum(o.size for o in self.active_orders if o.is_buy_side)

    @property
    def sell_volume(self) -> float:
        """Calculate total sell-side volume (ACTIVE orders only)"""
        return sum(o.size for o in self.active_orders if o.is_sell_side)

    @property
    def net_flow(self) -> float:
        """Calculate net flow (buy - sell volume)"""
        return self.buy_volume - self.sell_volume

    # ========== SPOT MARKET PRESSURE ==========
    @property
    def spot_buy_pressure_per_min(self) -> float:
        """Calculate SPOT market buy pressure per minute"""
        return sum(
            o.get_execution_rate()
            for o in self.active_orders
            if o.is_buy_side and o.product_type == 'SPOT'
        )

    @property
    def spot_sell_pressure_per_min(self) -> float:
        """Calculate SPOT market sell pressure per minute"""
        return sum(
            o.get_execution_rate()
            for o in self.active_orders
            if o.is_sell_side and o.product_type == 'SPOT'
        )

    @property
    def spot_net_pressure_per_min(self) -> float:
        """Calculate SPOT market net pressure per minute (buy - sell)"""
        return self.spot_buy_pressure_per_min - self.spot_sell_pressure_per_min

    # ========== PERP MARKET PRESSURE ==========
    @property
    def perp_buy_pressure_per_min(self) -> float:
        """Calculate PERP market buy pressure per minute"""
        return sum(
            o.get_execution_rate()
            for o in self.active_orders
            if o.is_buy_side and o.product_type == 'PERP'
        )

    @property
    def perp_sell_pressure_per_min(self) -> float:
        """Calculate PERP market sell pressure per minute"""
        return sum(
            o.get_execution_rate()
            for o in self.active_orders
            if o.is_sell_side and o.product_type == 'PERP'
        )

    @property
    def perp_net_pressure_per_min(self) -> float:
        """Calculate PERP market net pressure per minute (buy - sell)"""
        return self.perp_buy_pressure_per_min - self.perp_sell_pressure_per_min

    @property
    def buy_pressure_per_min(self) -> float:
        """Calculate active buy pressure per minute"""
        return sum(o.get_execution_rate() for o in self.active_orders if o.is_buy_side)

    @property
    def sell_pressure_per_min(self) -> float:
        """Calculate active sell pressure per minute"""
        return sum(o.get_execution_rate() for o in self.active_orders if o.is_sell_side)

    @property
    def net_pressure_per_min(self) -> float:
        """Calculate net pressure per minute (buy - sell)"""
        return self.buy_pressure_per_min - self.sell_pressure_per_min

    @property
    def unique_addresses(self) -> set:
        """Get unique addresses"""
        return {o.full_address for o in self.orders}

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        return {
            'timestamp': self.timestamp,
            'symbol': self.symbol,
            'total_orders': self.total_orders,
            'active_orders': len(self.active_orders),
            'canceled_orders': len(self.canceled_orders),
            'unique_addresses': len(self.unique_addresses),
            'total_volume': self.total_volume,
            'active_volume': self.active_volume,
            'buy_volume': self.buy_volume,
            'sell_volume': self.sell_volume,
            'net_flow': self.net_flow,

            # TOTAL PRESSURE (SPOT + PERP combined)
            'buy_pressure_per_min': self.buy_pressure_per_min,
            'sell_pressure_per_min': self.sell_pressure_per_min,
            'net_pressure_per_min': self.net_pressure_per_min,

            # SPOT MARKET PRESSURE
            'spot_buy_pressure_per_min': self.spot_buy_pressure_per_min,
            'spot_sell_pressure_per_min': self.spot_sell_pressure_per_min,
            'spot_net_pressure_per_min': self.spot_net_pressure_per_min,

            # PERP MARKET PRESSURE
            'perp_buy_pressure_per_min': self.perp_buy_pressure_per_min,
            'perp_sell_pressure_per_min': self.perp_sell_pressure_per_min,
            'perp_net_pressure_per_min': self.perp_net_pressure_per_min,

            'whale_orders': len(self.whale_orders),
            'spot_orders': len([o for o in self.orders if o.product_type == 'SPOT']),
            'perp_orders': len([o for o in self.orders if o.product_type == 'PERP']),
            'avg_order_size': self.total_volume / self.total_orders if self.total_orders > 0 else 0,
            'avg_duration_hours': sum(
                o.duration_hours for o in self.orders) / self.total_orders if self.total_orders > 0 else 0
        }

    def get_address_summary(self) -> Dict[str, Dict[str, Any]]:
        """Get summary grouped by address"""
        address_data = {}

        for order in self.orders:
            addr = order.full_address
            if addr not in address_data:
                address_data[addr] = {
                    'display': order.display_address,
                    'total_volume': 0,
                    'order_count': 0,
                    'active_count': 0,
                    'orders': []
                }

            address_data[addr]['total_volume'] += order.size
            address_data[addr]['order_count'] += 1
            if order.is_active:
                address_data[addr]['active_count'] += 1
            address_data[addr]['orders'].append(order)

        return address_data

    def compare_with(self, previous: 'TWAPSnapshot') -> Dict[str, Any]:
        """
        Compare this snapshot with a previous one to detect changes.

        FIXED LOGIC:
        - Only track ACTIVE orders in new_orders
        - Detect status changes IN THIS SNAPSHOT (active → canceled/completed/error)
        - Add orders to completed_orders/canceled_orders when status CHANGES
        - Handle orders that were already canceled/error in previous snapshot
        - Ignore orders that disappear if they were already non-active
        """

        def order_key(order):
            """Create unique key: order hash"""
            return order.order_hash

        # Build lookup dictionaries
        current_order_keys = {order_key(o): o for o in self.orders}
        previous_order_keys = {order_key(o): o for o in previous.orders}

        # Find new, gone, and existing orders
        new_order_keys = set(current_order_keys.keys()) - set(previous_order_keys.keys())
        gone_order_keys = set(previous_order_keys.keys()) - set(current_order_keys.keys())
        existing_order_keys = set(current_order_keys.keys()) & set(previous_order_keys.keys())

        # Initialize result arrays
        completed_orders = []
        canceled_orders = []
        status_changes = []

        # ========== FIX: Only add ACTIVE new orders ==========
        new_orders = [
            current_order_keys[key]
            for key in new_order_keys
            if current_order_keys[key].is_active  # ← CRITICAL: Only active orders!
        ]
        # ====================================================

        # === PROCESS EXISTING ORDERS (detect status changes) ===
        for key in existing_order_keys:
            current_order = current_order_keys[key]
            previous_order = previous_order_keys[key]

            # Status changed in THIS snapshot
            if current_order.status != previous_order.status:

                # Order just got canceled (active → canceled)
                if current_order.status == 'canceled' and previous_order.status == 'active':
                    canceled_orders.append(current_order)
                    status_changes.append({
                        'address': current_order.full_address,
                        'order_hash': current_order.order_hash,
                        'side': current_order.side,
                        'size': current_order.size,
                        'product_type': current_order.product_type,
                        'duration_hours': round(current_order.duration_hours, 2),
                        'old_status': previous_order.status,
                        'new_status': current_order.status,
                        'elapsed_minutes': current_order.elapsed_minutes,
                        'progress_percent': current_order.progress_percent,
                        'time_remaining_minutes': current_order.time_remaining_minutes
                    })

                # Order just completed (active → completed)
                elif current_order.status == 'completed' and previous_order.status == 'active':
                    completed_orders.append(current_order)
                    status_changes.append({
                        'address': current_order.full_address,
                        'order_hash': current_order.order_hash,
                        'side': current_order.side,
                        'size': current_order.size,
                        'product_type': current_order.product_type,
                        'duration_hours': round(current_order.duration_hours, 2),
                        'old_status': previous_order.status,
                        'new_status': current_order.status,
                        'elapsed_minutes': current_order.elapsed_minutes,
                        'progress_percent': current_order.progress_percent,
                        'time_remaining_minutes': current_order.time_remaining_minutes
                    })

                # Order went to error state (active → error)
                elif current_order.status == 'error' and previous_order.status == 'active':
                    # Treat errors as cancellations for volume tracking
                    canceled_orders.append(current_order)
                    status_changes.append({
                        'address': current_order.full_address,
                        'order_hash': current_order.order_hash,
                        'side': current_order.side,
                        'size': current_order.size,
                        'product_type': current_order.product_type,
                        'duration_hours': round(current_order.duration_hours, 2),
                        'old_status': previous_order.status,
                        'new_status': current_order.status,
                        'elapsed_minutes': current_order.elapsed_minutes,
                        'progress_percent': current_order.progress_percent,
                        'time_remaining_minutes': current_order.time_remaining_minutes
                    })

                # Track any other status changes (for debugging)
                else:
                    status_changes.append({
                        'address': current_order.full_address,
                        'order_hash': current_order.order_hash,
                        'side': current_order.side,
                        'size': current_order.size,
                        'product_type': current_order.product_type,
                        'duration_hours': round(current_order.duration_hours, 2),
                        'old_status': previous_order.status,
                        'new_status': current_order.status,
                        'elapsed_minutes': current_order.elapsed_minutes,
                        'progress_percent': current_order.progress_percent,
                        'time_remaining_minutes': current_order.time_remaining_minutes
                    })

        # === PROCESS DISAPPEARED ORDERS ===
        # An order disappeared - what to do depends on its last status
        for key in gone_order_keys:
            order = previous_order_keys[key]

            if order.status == 'active':
                # Active order disappeared without changing status first = completed/filled
                completed_orders.append(order)
                status_changes.append({
                    'address': order.full_address,
                    'order_hash': order.order_hash,
                    'side': order.side,
                    'size': order.size,
                    'product_type': order.product_type,
                    'duration_hours': round(order.duration_hours, 2),
                    'old_status': 'active',
                    'new_status': 'completed',
                    'elapsed_minutes': order.elapsed_minutes,
                    'progress_percent': order.progress_percent,
                    'time_remaining_minutes': order.time_remaining_minutes
                })

            elif order.status == 'canceled':
                # Canceled order finally disappeared - add to canceled_orders
                canceled_orders.append(order)
                status_changes.append({
                    'address': order.full_address,
                    'order_hash': order.order_hash,
                    'side': order.side,
                    'size': order.size,
                    'product_type': order.product_type,
                    'duration_hours': round(order.duration_hours, 2),
                    'old_status': 'canceled',
                    'new_status': 'removed',
                    'elapsed_minutes': order.elapsed_minutes,
                    'progress_percent': order.progress_percent,
                    'time_remaining_minutes': order.time_remaining_minutes
                })

            elif order.status == 'error':
                # Error order finally disappeared - add to canceled_orders
                canceled_orders.append(order)
                status_changes.append({
                    'address': order.full_address,
                    'order_hash': order.order_hash,
                    'side': order.side,
                    'size': order.size,
                    'product_type': order.product_type,
                    'duration_hours': round(order.duration_hours, 2),
                    'old_status': 'error',
                    'new_status': 'removed',
                    'elapsed_minutes': order.elapsed_minutes,
                    'progress_percent': order.progress_percent,
                    'time_remaining_minutes': order.time_remaining_minutes
                })

            elif order.status == 'completed':
                # Completed order finally disappeared - already counted, ignore
                pass

        return {
            'new_orders': new_orders,
            'completed_orders': completed_orders,
            'canceled_orders': canceled_orders,
            'status_changes': status_changes,
            'volume_change': self.total_volume - previous.total_volume,
            'net_flow_change': self.net_flow - previous.net_flow,
            'active_orders_change': len(self.active_orders) - len(previous.active_orders)
        }

    def format_summary(self) -> str:
        """Get formatted multi-line summary"""
        stats = self.get_stats()

        return f"""
📊 TWAP Snapshot #{self.update_number} - {self.symbol}
⏰ {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
{'=' * 60}
Orders: {stats['total_orders']} total ({stats['active_orders']} active, {stats['canceled_orders']} canceled)
Volume: {stats['total_volume']:,.0f} {self.symbol}
  Buy:  {stats['buy_volume']:,.0f} {self.symbol}
  Sell: {stats['sell_volume']:,.0f} {self.symbol}
  Net:  {stats['net_flow']:+,.0f} {self.symbol}
Addresses: {stats['unique_addresses']} unique
Whales: {stats['whale_orders']} orders >50k
Products: {stats['spot_orders']} SPOT, {stats['perp_orders']} PERP
Avg Size: {stats['avg_order_size']:,.0f} {self.symbol}
Avg Duration: {stats['avg_duration_hours']:.1f} hours
{'=' * 60}"""