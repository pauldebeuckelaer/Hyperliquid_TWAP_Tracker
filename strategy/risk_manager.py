"""
Risk Manager Module
Handles position sizing, stop losses, trailing stops, and exit logic.
"""

import numpy as np
import config


class RiskManager:
    """Manages position sizing and trade exits."""

    def __init__(self):
        self.capital = config.INITIAL_CAPITAL
        self.peak_capital = config.INITIAL_CAPITAL

    def calculate_position_size(self, entry_price: float, stop_price: float) -> dict:
        # Fixed position size for small accounts
        if config.FIXED_POSITION_USD > 0:
            size_usd = config.FIXED_POSITION_USD
            size_units = size_usd / entry_price
            leverage_used = size_usd / self.capital
            return {
                'size_usd': size_usd,
                'size_units': size_units,
                'leverage_used': leverage_used
            }

        # Percentage-based sizing (for larger accounts)
        risk_amount = self.capital * config.RISK_PER_TRADE
        price_risk = abs(entry_price - stop_price)

        if price_risk == 0:
            return {'size_usd': 0, 'size_units': 0, 'leverage_used': 0}

        size_units = risk_amount / price_risk
        size_usd = size_units * entry_price

        max_usd = self.capital * config.MAX_POSITION_PCT * config.LEVERAGE
        if size_usd > max_usd:
            size_usd = max_usd
            size_units = size_usd / entry_price

        leverage_used = size_usd / self.capital

        return {
            'size_usd': size_usd,
            'size_units': size_units,
            'leverage_used': leverage_used
        }

    def get_stop_price(self, entry_price: float, direction: str) -> float:
        """Calculate initial hard stop price."""
        if direction == 'long':
            return entry_price * (1 - config.HARD_STOP_PCT)
        else:
            return entry_price * (1 + config.HARD_STOP_PCT)

    def get_target_price(self, entry_price: float, direction: str) -> float:
        """Calculate fixed target price."""
        if direction == 'long':
            return entry_price * (1 + config.FIXED_TARGET_PCT)
        else:
            return entry_price * (1 - config.FIXED_TARGET_PCT)

    def get_trailing_stop(self, peak_price: float, direction: str) -> float:
        """Calculate trailing stop based on peak price since entry."""
        if direction == 'long':
            return peak_price * (1 - config.TRAIL_STOP_PCT)
        else:
            return peak_price * (1 + config.TRAIL_STOP_PCT)

    def should_activate_trailing(self, entry_price: float, current_price: float,
                                  direction: str) -> bool:
        """Check if trailing stop should be activated."""
        if direction == 'long':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
        return pnl_pct >= config.TRAIL_ACTIVATION_PCT

    def calculate_costs(self, size_usd: float, hold_bins: int) -> dict:
        entry_fee = size_usd * config.ENTRY_FEE
        exit_fee = size_usd * config.EXIT_FEE
        slippage_cost = size_usd * config.SLIPPAGE  # only on exit (market order)

        hold_hours = hold_bins * 0.5
        funding_periods = hold_hours / 8
        funding_cost = size_usd * config.FUNDING_RATE_8H * funding_periods

        total = entry_fee + exit_fee + slippage_cost + funding_cost

        return {
            'entry_fee': entry_fee,
            'exit_fee': exit_fee,
            'slippage': slippage_cost,
            'funding': funding_cost,
            'total': total
        }

    def update_capital(self, pnl: float):
        """Update capital after a trade."""
        self.capital += pnl
        self.peak_capital = max(self.peak_capital, self.capital)

    def get_drawdown(self) -> float:
        """Current drawdown from peak."""
        if self.peak_capital == 0:
            return 0
        return (self.peak_capital - self.capital) / self.peak_capital