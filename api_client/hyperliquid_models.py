#!/usr/bin/env python3
"""
Data models for Hyperliquid trader information
UPDATED: Added spot balance tracking for total portfolio value
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime


@dataclass
class Position:
    """Represents a trading position"""
    coin: str
    size: float  # Positive for long, negative for short
    entry_price: float
    unrealized_pnl: float
    leverage: float
    liquidation_price: Optional[float] = None
    margin_used: float = 0.0

    @property
    def side(self) -> str:
        """Get position side"""
        return "LONG" if self.size > 0 else "SHORT"

    @property
    def notional_value(self) -> float:
        """Get position notional value"""
        return abs(self.size) * self.entry_price

    @classmethod
    def from_api_data(cls, data: Dict) -> 'Position':
        """Create Position from API response"""
        position = data.get("position", {})
        return cls(
            coin=position.get("coin", ""),
            size=float(position.get("szi", 0)),
            entry_price=float(position.get("entryPx", 0)),
            unrealized_pnl=float(position.get("unrealizedPnl", 0)),
            leverage=float(position.get("leverage", {}).get("value", 1)),
            liquidation_price=float(position.get("liquidationPx", 0)) or None,
            margin_used=float(position.get("marginUsed", 0))
        )


@dataclass
class SpotBalance:
    """Represents a spot token balance - NEW MODEL"""
    coin: str
    token: str  # Token address or identifier
    hold: float  # Amount locked in orders
    total: float  # Total balance
    entry_notional: float  # Cost basis

    @property
    def available(self) -> float:
        """Get available (not locked) balance"""
        return self.total - self.hold

    @classmethod
    def from_api_data(cls, data: Dict) -> 'SpotBalance':
        """Create SpotBalance from API response"""
        return cls(
            coin=data.get("coin", ""),
            token=data.get("token", ""),
            hold=float(data.get("hold", 0)),
            total=float(data.get("total", 0)),
            entry_notional=float(data.get("entryNtl", 0))
        )


@dataclass
class Fill:
    """Represents a trade fill"""
    coin: str
    price: float
    size: float
    side: str  # "B" for buy, "A" for sell/ask
    timestamp: int  # milliseconds
    closed_pnl: float
    fee: float
    order_id: int
    trade_id: int
    crossed: bool
    direction: str  # "Open Long", "Close Short", etc
    hash: str

    @property
    def datetime(self) -> datetime:
        """Get datetime from timestamp"""
        return datetime.fromtimestamp(self.timestamp / 1000)

    @property
    def notional_value(self) -> float:
        """Get trade notional value"""
        return self.size * self.price

    @property
    def is_buy(self) -> bool:
        """Check if this is a buy"""
        return self.side == "B"

    @property
    def is_sell(self) -> bool:
        """Check if this is a sell"""
        return self.side == "A"

    @classmethod
    def from_api_data(cls, data: Dict) -> 'Fill':
        """Create Fill from API response"""
        return cls(
            coin=data.get("coin", ""),
            price=float(data.get("px", 0)),
            size=float(data.get("sz", 0)),
            side=data.get("side", ""),
            timestamp=int(data.get("time", 0)),
            closed_pnl=float(data.get("closedPnl", 0)),
            fee=float(data.get("fee", 0)),
            order_id=int(data.get("oid", 0)),
            trade_id=int(data.get("tid", 0)),
            crossed=bool(data.get("crossed", False)),
            direction=data.get("dir", ""),
            hash=data.get("hash", "")
        )


@dataclass
class TWAPFill:
    """Represents a TWAP slice fill"""
    fill: Fill
    twap_id: int

    @classmethod
    def from_api_data(cls, data: Dict) -> 'TWAPFill':
        """Create TWAPFill from API response"""
        return cls(
            fill=Fill.from_api_data(data.get("fill", {})),
            twap_id=int(data.get("twapId", 0))
        )


@dataclass
class AccountState:
    """Represents account state"""
    account_value: float
    total_position_value: float
    total_margin_used: float
    withdrawable: float
    positions: List[Position] = field(default_factory=list)

    @property
    def num_positions(self) -> int:
        """Get number of open positions"""
        return len(self.positions)

    @property
    def total_unrealized_pnl(self) -> float:
        """Get total unrealized PnL across all positions"""
        return sum(p.unrealized_pnl for p in self.positions)

    @property
    def leverage_ratio(self) -> float:
        """Get overall leverage ratio"""
        if self.account_value == 0:
            return 0
        return self.total_position_value / self.account_value

    @classmethod
    def from_api_data(cls, data: Dict) -> 'AccountState':
        """Create AccountState from API response"""
        margin_summary = data.get("marginSummary", {})

        # Parse positions
        positions = []
        for pos_data in data.get("assetPositions", []):
            position_info = pos_data.get("position", {})
            if position_info.get("szi") != "0":
                positions.append(Position.from_api_data(pos_data))

        return cls(
            account_value=float(margin_summary.get("accountValue", 0)),
            total_position_value=float(margin_summary.get("totalNtlPos", 0)),
            total_margin_used=float(margin_summary.get("totalMarginUsed", 0)),
            withdrawable=float(data.get("withdrawable", 0)),
            positions=positions
        )


@dataclass
class PortfolioPeriod:
    """Portfolio data for a specific period"""
    period: str  # "day", "week", "month", "allTime"
    account_value_history: List[tuple]  # [(timestamp, value), ...]
    pnl_history: List[tuple]  # [(timestamp, pnl), ...]
    volume: float

    @property
    def current_account_value(self) -> float:
        """Get latest account value"""
        if self.account_value_history:
            return float(self.account_value_history[-1][1])
        return 0

    @property
    def current_pnl(self) -> float:
        """Get latest PnL"""
        if self.pnl_history:
            return float(self.pnl_history[-1][1])
        return 0

    @property
    def pnl_change(self) -> float:
        """Get PnL change over period"""
        if len(self.pnl_history) >= 2:
            return float(self.pnl_history[-1][1]) - float(self.pnl_history[0][1])
        return 0

    @classmethod
    def from_api_data(cls, period: str, data: Dict) -> 'PortfolioPeriod':
        """Create PortfolioPeriod from API response"""
        return cls(
            period=period,
            account_value_history=data.get("accountValueHistory", []),
            pnl_history=data.get("pnlHistory", []),
            volume=float(data.get("vlm", 0))
        )


@dataclass
class TraderMetrics:
    """Comprehensive trader metrics - UPDATED with spot balance support"""
    address: str
    timestamp: datetime

    # Account info - UPDATED
    role: str = "unknown"
    account_value: float = 0  # Perpetuals account value
    spot_value: float = 0  # NEW: Spot wallet value
    total_portfolio_value: float = 0  # NEW: Total value (perps + spot)
    position_value: float = 0
    margin_used: float = 0
    withdrawable: float = 0
    num_positions: int = 0

    # Trading activity
    cumulative_volume: float = 0
    num_fills: int = 0
    num_twap_fills: int = 0
    last_trade_time: Optional[datetime] = None

    # Performance
    all_time_pnl: float = 0
    all_time_vlm: float = 0
    daily_pnl: float = 0
    weekly_pnl: float = 0
    monthly_pnl: float = 0

    # Fee tier
    fee_cross_rate: float = 0
    fee_add_rate: float = 0

    # Rewards
    unclaimed_rewards: float = 0
    claimed_rewards: float = 0

    # Relationships
    num_subaccounts: int = 0

    @property
    def roi(self) -> float:
        """Calculate ROI if possible - UPDATED to use total_portfolio_value"""
        if self.total_portfolio_value > 0 and self.all_time_pnl != 0:
            initial_value = self.total_portfolio_value - self.all_time_pnl
            if initial_value > 0:
                return (self.all_time_pnl / initial_value) * 100
        return 0

    @property
    def leverage_ratio(self) -> float:
        """Calculate leverage ratio - UPDATED to use total_portfolio_value"""
        if self.total_portfolio_value > 0:
            return self.position_value / self.total_portfolio_value
        return 0

    @property
    def is_active(self) -> bool:
        """Check if trader is active"""
        return self.num_fills > 0 or self.num_positions > 0

    def to_dict(self) -> Dict:
        """Convert to dictionary - UPDATED with new fields"""
        return {
            "address": self.address,
            "timestamp": self.timestamp.isoformat(),
            "role": self.role,
            "account_value": self.account_value,
            "spot_value": self.spot_value,  # NEW
            "total_portfolio_value": self.total_portfolio_value,  # NEW
            "position_value": self.position_value,
            "margin_used": self.margin_used,
            "withdrawable": self.withdrawable,
            "num_positions": self.num_positions,
            "cumulative_volume": self.cumulative_volume,
            "num_fills": self.num_fills,
            "num_twap_fills": self.num_twap_fills,
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
            "all_time_pnl": self.all_time_pnl,
            "all_time_vlm": self.all_time_vlm,
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "monthly_pnl": self.monthly_pnl,
            "fee_cross_rate": self.fee_cross_rate,
            "fee_add_rate": self.fee_add_rate,
            "unclaimed_rewards": self.unclaimed_rewards,
            "claimed_rewards": self.claimed_rewards,
            "num_subaccounts": self.num_subaccounts,
            "roi": self.roi,
            "leverage_ratio": self.leverage_ratio,
            "is_active": self.is_active
        }

    @classmethod
    def from_profile_data(cls, profile: Dict) -> 'TraderMetrics':
        """Create TraderMetrics from trader profile dict - UPDATED"""
        metrics = profile.get("metrics", {})

        # Parse timestamp
        timestamp = datetime.fromisoformat(profile.get("timestamp", datetime.now().isoformat()))

        # Parse last trade time
        last_trade = metrics.get("last_trade_time")
        last_trade_time = None
        if last_trade:
            try:
                last_trade_time = datetime.fromisoformat(last_trade)
            except:
                pass

        return cls(
            address=profile.get("address", ""),
            timestamp=timestamp,
            role=metrics.get("role", "unknown"),
            account_value=metrics.get("account_value", 0),
            spot_value=metrics.get("spot_value", 0),  # NEW
            total_portfolio_value=metrics.get("total_portfolio_value", 0),  # NEW
            position_value=metrics.get("position_value", 0),
            margin_used=metrics.get("margin_used", 0),
            withdrawable=metrics.get("withdrawable", 0),
            num_positions=metrics.get("num_positions", 0),
            cumulative_volume=metrics.get("cumulative_volume", 0),
            num_fills=metrics.get("num_fills", 0),
            num_twap_fills=metrics.get("num_twap_fills", 0),
            last_trade_time=last_trade_time,
            all_time_pnl=metrics.get("all_time_pnl", 0),
            all_time_vlm=metrics.get("all_time_vlm", 0),
            daily_pnl=metrics.get("daily_pnl", 0),
            weekly_pnl=metrics.get("weekly_pnl", 0),
            monthly_pnl=metrics.get("monthly_pnl", 0),
            fee_cross_rate=metrics.get("fee_cross_rate", 0),
            fee_add_rate=metrics.get("fee_add_rate", 0),
            unclaimed_rewards=metrics.get("unclaimed_rewards", 0),
            claimed_rewards=metrics.get("claimed_rewards", 0),
            num_subaccounts=metrics.get("num_subaccounts", 0)
        )


@dataclass
class TradingStats:
    """Trading statistics calculated from fills"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_volume: float
    total_pnl: float
    total_fees: float

    # Calculated metrics
    win_rate: float = 0
    avg_win: float = 0
    avg_loss: float = 0
    profit_factor: float = 0
    net_pnl: float = 0

    def __post_init__(self):
        """Calculate derived metrics"""
        if self.total_trades > 0:
            self.win_rate = self.winning_trades / self.total_trades

        if self.winning_trades > 0:
            self.avg_win = self.total_pnl / self.winning_trades if self.total_pnl > 0 else 0

        if self.losing_trades > 0:
            loss_total = abs(min(0, self.total_pnl))
            self.avg_loss = loss_total / self.losing_trades

        gross_profit = max(0, self.total_pnl)
        gross_loss = abs(min(0, self.total_pnl))
        if gross_loss > 0:
            self.profit_factor = gross_profit / gross_loss

        self.net_pnl = self.total_pnl - self.total_fees

    @classmethod
    def from_fills(cls, fills: List[Fill]) -> 'TradingStats':
        """Calculate statistics from list of fills"""
        if not fills:
            return cls(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                total_volume=0,
                total_pnl=0,
                total_fees=0
            )

        total_pnl = sum(f.closed_pnl for f in fills)
        total_volume = sum(f.notional_value for f in fills)
        total_fees = sum(f.fee for f in fills)

        winning_trades = sum(1 for f in fills if f.closed_pnl > 0)
        losing_trades = sum(1 for f in fills if f.closed_pnl < 0)

        return cls(
            total_trades=len(fills),
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_volume=total_volume,
            total_pnl=total_pnl,
            total_fees=total_fees
        )