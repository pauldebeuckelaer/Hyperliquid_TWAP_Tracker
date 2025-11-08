#!/usr/bin/env python3
"""
TWAP Order Models for HypurrScan Data
======================================

Lightweight data models for handling TWAP orders from HypurrScan API.
Provides standardized structure and helper methods for TWAP analysis.

Classes:
    TWAPOrder: Individual TWAP order with parsing and analysis methods
    TWAPSnapshot: Collection of orders at a point in time
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@dataclass
class TWAPOrder:
    """
    Standardized TWAP order from HypurrScan.

    Attributes:
        address: Shortened address (first 10 chars)
        full_address: Complete wallet address
        symbol: Trading symbol (e.g., 'HYPE')
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

        # All TWAPs are SPOT orders (validated via HypurrScan UI)
        # Asset ID determines the market, but for HYPE all are SPOT
        product_type = "SPOT"

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

        return cls(
            address=short_address,
            full_address=full_address,
            symbol=symbol,
            size=float(twap_info.get('s', 0)),
            side=side,
            product_type=product_type,
            status=status,
            duration_minutes=twap_info.get('m', 0),
            order_hash=raw_order.get('order_hash', raw_order.get('hash', '')),
            timestamp=datetime.now(),
            raw_data=raw_order
        )

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

    @classmethod
    def from_hypurr_data(cls, raw_orders: List[Dict], symbol: str, update_number: int = 0) -> 'TWAPSnapshot':
        """Create snapshot from raw HypurrScan data"""
        orders = [TWAPOrder.from_hypurr_data(order, symbol) for order in raw_orders]
        return cls(
            orders=orders,
            symbol=symbol,
            timestamp=datetime.now(),
            update_number=update_number
        )

    @property
    def total_orders(self) -> int:
        """Total number of orders"""
        return len(self.orders)

    @property
    def active_orders(self) -> List[TWAPOrder]:
        """Get only active orders"""
        return [o for o in self.orders if o.is_active]

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
        """Calculate total buy-side volume"""
        return sum(o.size for o in self.orders if o.is_buy_side)

    @property
    def sell_volume(self) -> float:
        """Calculate total sell-side volume"""
        return sum(o.size for o in self.orders if o.is_sell_side)

    @property
    def net_flow(self) -> float:
        """Calculate net flow (buy - sell volume)"""
        return self.buy_volume - self.sell_volume

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
            'buy_pressure_per_min': self.buy_pressure_per_min,
            'sell_pressure_per_min': self.sell_pressure_per_min,
            'net_pressure_per_min': self.net_pressure_per_min,
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
        """Compare this snapshot with a previous one to detect changes."""

        def order_key(order):
            """Create unique key: (address, size, duration) tuple"""
            return order.order_hash

        current_order_keys = {order_key(o): o for o in self.orders}
        previous_order_keys = {order_key(o): o for o in previous.orders}

        new_order_keys = set(current_order_keys.keys()) - set(previous_order_keys.keys())
        gone_order_keys = set(previous_order_keys.keys()) - set(current_order_keys.keys())
        existing_order_keys = set(current_order_keys.keys()) & set(previous_order_keys.keys())

        status_changes = []
        for key in existing_order_keys:
            current_order = current_order_keys[key]
            previous_order = previous_order_keys[key]

            if current_order.status != previous_order.status:
                status_changes.append({
                    'address': current_order.display_address,
                    'full_address': current_order.full_address,
                    'old_status': previous_order.status,
                    'new_status': current_order.status,
                    'size': current_order.size,
                    'side': current_order.side
                })

        return {
            'new_orders': [current_order_keys[key] for key in new_order_keys],
            'completed_orders': [previous_order_keys[key] for key in gone_order_keys],
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