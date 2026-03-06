"""
Backtester Module
Simulates trades bin-by-bin using generated signals.
Outputs detailed trade log and performance metrics.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import config
from risk_manager import RiskManager


@dataclass
class Trade:
    """Represents a single trade from entry to exit."""
    entry_bin: pd.Timestamp
    entry_price: float
    direction: str              # 'long' or 'short'
    size_units: float
    size_usd: float
    leverage: float
    stop_price: float
    target_price: float
    signal_strength: float      # z-score at entry

    # Filled on exit
    exit_bin: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    peak_price: Optional[float] = None
    hold_bins: int = 0
    pnl_gross: float = 0.0
    pnl_net: float = 0.0
    costs: float = 0.0
    return_pct: float = 0.0
    reversal_count: int = 0

class Backtester:
    """
    Bin-by-bin backtesting engine.

    For each 30min bin:
        1. Check if we have an open position -> manage exits
        2. If no position -> check for entry signal
        3. Track P&L and metrics
    """

    def __init__(self):
        self.risk = RiskManager()
        self.trades: list[Trade] = []
        self.current_trade: Optional[Trade] = None
        self.equity_curve: list[dict] = []

    def _should_enter(self, row: pd.Series) -> Optional[str]:
        """
        Determine if we should enter a trade on this bin.
        Returns 'long', 'short', or None.
        """
        signal = row['signal']
        signal_z = row['signal_z']

        # Apply entry threshold
        if config.ENTRY_MODE == 'any':
            if signal == 0:
                return None
            direction = 'long' if signal > 0 else 'short'

        elif config.ENTRY_MODE == 'zscore':
            if abs(signal_z) < config.ENTRY_ZSCORE:
                return None
            direction = 'long' if signal > 0 else 'short'

        elif config.ENTRY_MODE == 'quantile':
            # Quantile is computed across the full dataset, passed in signal_z
            if abs(signal_z) < config.ENTRY_ZSCORE:  # approximate
                return None
            direction = 'long' if signal > 0 else 'short'

        else:
            return None

        # Direction filter
        if config.DIRECTION == 'long_only' and direction == 'short':
            return None
        if config.DIRECTION == 'short_only' and direction == 'long':
            return None

        return direction

    def _check_exit(self, trade: Trade, current_price: float,
                    current_bin: pd.Timestamp, signal: float) -> Optional[str]:
        """
        Check all exit conditions for an open trade.
        Returns exit reason string or None.
        """
        direction = trade.direction

        # Update peak price
        if direction == 'long':
            if trade.peak_price is None or current_price > trade.peak_price:
                trade.peak_price = current_price
        else:
            if trade.peak_price is None or current_price < trade.peak_price:
                trade.peak_price = current_price

        # 1. Hard stop loss (always checked first)
        if direction == 'long' and current_price <= trade.stop_price:
            return 'hard_stop'
        if direction == 'short' and current_price >= trade.stop_price:
            return 'hard_stop'

        # 2. Fixed target
        if config.EXIT_MODE in ('fixed_target', 'combined'):
            if direction == 'long' and current_price >= trade.target_price:
                return 'target'
            if direction == 'short' and current_price <= trade.target_price:
                return 'target'

        # 3. Trailing stop
        if config.EXIT_MODE in ('trailing', 'combined'):
            if self.risk.should_activate_trailing(
                trade.entry_price, current_price, direction
            ):
                trail_stop = self.risk.get_trailing_stop(
                    trade.peak_price, direction
                )
                if direction == 'long' and current_price <= trail_stop:
                    return 'trailing_stop'
                if direction == 'short' and current_price >= trail_stop:
                    return 'trailing_stop'

        # 4. Signal reversal (require consecutive opposing bins)
        if config.EXIT_ON_REVERSAL and config.EXIT_MODE in ('reversal', 'combined'):
            if direction == 'long' and signal < 0:
                trade.reversal_count += 1
            elif direction == 'short' and signal > 0:
                trade.reversal_count += 1
            else:
                trade.reversal_count = 0  # reset if signal aligns again

            if trade.reversal_count >= config.REVERSAL_CONFIRM_BINS:
                return 'signal_reversal'

        # 5. Next bin exit
        if config.EXIT_MODE == 'next_bin':
            return 'next_bin'

        # 6. Max hold time
        if config.MAX_HOLD_BINS > 0 and trade.hold_bins >= config.MAX_HOLD_BINS:
            return 'max_hold'

        return None

    def _close_trade(self, trade: Trade, exit_price: float,
                     exit_bin: pd.Timestamp, reason: str):
        """Close an open trade and calculate P&L."""
        trade.exit_bin = exit_bin
        trade.exit_price = exit_price
        trade.exit_reason = reason

        # Gross P&L
        if trade.direction == 'long':
            trade.pnl_gross = (exit_price - trade.entry_price) * trade.size_units
        else:
            trade.pnl_gross = (trade.entry_price - exit_price) * trade.size_units

        # Costs
        cost_breakdown = self.risk.calculate_costs(trade.size_usd, trade.hold_bins)
        trade.costs = cost_breakdown['total']

        # Net P&L
        trade.pnl_net = trade.pnl_gross - trade.costs

        # Return percentage (on capital, not position)
        trade.return_pct = trade.pnl_net / self.risk.capital * 100

        # Update capital
        self.risk.update_capital(trade.pnl_net)

        self.trades.append(trade)
        self.current_trade = None

    def run(self, signals: pd.DataFrame, daily_volume: pd.DataFrame = None) -> dict:
        """
        Run the backtest on signal data.

        Args:
            signals: DataFrame from signal.generate_signals()
            daily_volume: DataFrame with [date, daily_volume] for filtering

        Returns:
            dict with trades, equity curve, and performance metrics
        """
        # Apply date filter
        if config.START_DATE:
            signals = signals[signals['date'] >= pd.Timestamp(config.START_DATE).date()]
        if config.END_DATE:
            signals = signals[signals['date'] <= pd.Timestamp(config.END_DATE).date()]

        # Apply volume filter
        if config.MIN_DAILY_VOLUME > 0 and daily_volume is not None:
            vol_dates = set(
                daily_volume[
                    daily_volume['daily_volume'] >= config.MIN_DAILY_VOLUME
                ]['date'].tolist()
            )
            signals = signals[signals['date'].isin(vol_dates)]

        signals = signals.sort_values('bin').reset_index(drop=True)

        print(f"Backtesting {len(signals)} bins across {signals['date'].nunique()} days")
        print(f"Capital: ${config.INITIAL_CAPITAL:,.0f} | "
              f"Risk/trade: {config.RISK_PER_TRADE:.1%} | "
              f"Entry: {config.ENTRY_MODE} (z>{config.ENTRY_ZSCORE}) | "
              f"Exit: {config.EXIT_MODE} | "
              f"Direction: {config.DIRECTION}")
        print("-" * 70)

        for i, row in signals.iterrows():
            current_price = row['price']
            current_bin = row['bin']
            signal = row['signal']

            # Track equity
            unrealized = 0
            if self.current_trade:
                if self.current_trade.direction == 'long':
                    unrealized = (current_price - self.current_trade.entry_price) * \
                                 self.current_trade.size_units
                else:
                    unrealized = (self.current_trade.entry_price - current_price) * \
                                 self.current_trade.size_units

            self.equity_curve.append({
                'bin': current_bin,
                'price': current_price,
                'capital': self.risk.capital,
                'equity': self.risk.capital + unrealized,
                'in_position': self.current_trade is not None,
                'signal': signal
            })

            # --- MANAGE OPEN POSITION ---
            if self.current_trade:
                self.current_trade.hold_bins += 1

                exit_reason = self._check_exit(
                    self.current_trade, current_price, current_bin, signal
                )

                if exit_reason:
                    self._close_trade(
                        self.current_trade, current_price, current_bin, exit_reason
                    )
                else:
                    continue  # still in trade, skip entry check

            # --- CHECK FOR NEW ENTRY ---
            direction = self._should_enter(row)
            if direction is None:
                continue

            # Calculate position
            stop_price = self.risk.get_stop_price(current_price, direction)
            pos = self.risk.calculate_position_size(current_price, stop_price)

            if pos['size_usd'] <= 0:
                continue

            target_price = self.risk.get_target_price(current_price, direction)

            self.current_trade = Trade(
                entry_bin=current_bin,
                entry_price=current_price,
                direction=direction,
                size_units=pos['size_units'],
                size_usd=pos['size_usd'],
                leverage=pos['leverage_used'],
                stop_price=stop_price,
                target_price=target_price,
                signal_strength=row['signal_z'],
                peak_price=current_price
            )

        # Close any remaining open trade at last price
        if self.current_trade:
            last_row = signals.iloc[-1]
            self._close_trade(
                self.current_trade, last_row['price'],
                last_row['bin'], 'end_of_data'
            )

        return self._calculate_results()

    def _calculate_results(self) -> dict:
        """Calculate performance metrics from completed trades."""
        if not self.trades:
            return {
                'trades': [],
                'equity_curve': pd.DataFrame(self.equity_curve),
                'metrics': {'total_trades': 0, 'message': 'No trades generated'}
            }

        # Build trade log
        trade_log = []
        for t in self.trades:
            trade_log.append({
                'entry_bin': t.entry_bin,
                'exit_bin': t.exit_bin,
                'direction': t.direction,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'size_usd': t.size_usd,
                'leverage': t.leverage,
                'hold_bins': t.hold_bins,
                'pnl_gross': t.pnl_gross,
                'pnl_net': t.pnl_net,
                'costs': t.costs,
                'return_pct': t.return_pct,
                'exit_reason': t.exit_reason,
                'signal_strength': t.signal_strength
            })

        trade_df = pd.DataFrame(trade_log)
        equity_df = pd.DataFrame(self.equity_curve)

        # Metrics
        winners = trade_df[trade_df['pnl_net'] > 0]
        losers = trade_df[trade_df['pnl_net'] <= 0]

        total_pnl = trade_df['pnl_net'].sum()
        total_costs = trade_df['costs'].sum()

        win_rate = len(winners) / len(trade_df) * 100 if len(trade_df) > 0 else 0
        avg_win = winners['pnl_net'].mean() if len(winners) > 0 else 0
        avg_loss = losers['pnl_net'].mean() if len(losers) > 0 else 0
        profit_factor = (
            abs(winners['pnl_net'].sum() / losers['pnl_net'].sum())
            if len(losers) > 0 and losers['pnl_net'].sum() != 0
            else float('inf')
        )

        # Expectancy: avg $ per trade
        expectancy = total_pnl / len(trade_df) if len(trade_df) > 0 else 0

        # Max drawdown from equity curve
        equity_df['peak_equity'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = (
            (equity_df['peak_equity'] - equity_df['equity']) / equity_df['peak_equity']
        )
        max_drawdown = equity_df['drawdown'].max()

        # Sharpe-like ratio (using per-trade returns)
        if trade_df['return_pct'].std() > 0:
            sharpe = trade_df['return_pct'].mean() / trade_df['return_pct'].std()
        else:
            sharpe = 0

        # Average hold time
        avg_hold = trade_df['hold_bins'].mean()

        # Exit reason breakdown
        exit_reasons = trade_df['exit_reason'].value_counts().to_dict()

        # Direction breakdown
        longs = trade_df[trade_df['direction'] == 'long']
        shorts = trade_df[trade_df['direction'] == 'short']

        metrics = {
            'total_trades': len(trade_df),
            'winners': len(winners),
            'losers': len(losers),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'total_costs': total_costs,
            'total_return_pct': (self.risk.capital - config.INITIAL_CAPITAL) / config.INITIAL_CAPITAL * 100,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'expectancy': expectancy,
            'max_drawdown': max_drawdown,
            'sharpe': sharpe,
            'avg_hold_bins': avg_hold,
            'avg_hold_hours': avg_hold * 0.5,
            'exit_reasons': exit_reasons,
            'final_capital': self.risk.capital,
            'long_trades': len(longs),
            'short_trades': len(shorts),
            'long_pnl': longs['pnl_net'].sum() if len(longs) > 0 else 0,
            'short_pnl': shorts['pnl_net'].sum() if len(shorts) > 0 else 0,
        }

        return {
            'trades': trade_df,
            'equity_curve': equity_df,
            'metrics': metrics
        }


def print_results(results: dict):
    """Pretty-print backtest results."""
    m = results['metrics']

    if m.get('message'):
        print(m['message'])
        return

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)

    print(f"\n--- Performance ---")
    print(f"Total trades:     {m['total_trades']}")
    print(f"Win rate:         {m['win_rate']:.1f}%")
    print(f"Profit factor:    {m['profit_factor']:.2f}")
    print(f"Sharpe ratio:     {m['sharpe']:.2f}")

    print(f"\n--- P&L ---")
    print(f"Starting capital: ${config.INITIAL_CAPITAL:,.2f}")
    print(f"Final capital:    ${m['final_capital']:,.2f}")
    print(f"Total P&L:        ${m['total_pnl']:,.2f} ({m['total_return_pct']:+.2f}%)")
    print(f"Total costs:      ${m['total_costs']:,.2f}")
    print(f"Max drawdown:     {m['max_drawdown']:.2%}")

    print(f"\n--- Trade Stats ---")
    print(f"Avg winner:       ${m['avg_win']:,.2f}")
    print(f"Avg loser:        ${m['avg_loss']:,.2f}")
    print(f"Expectancy:       ${m['expectancy']:,.2f} per trade")
    print(f"Avg hold time:    {m['avg_hold_hours']:.1f} hours ({m['avg_hold_bins']:.0f} bins)")

    print(f"\n--- Direction ---")
    print(f"Long trades:      {m['long_trades']} (P&L: ${m['long_pnl']:,.2f})")
    print(f"Short trades:     {m['short_trades']} (P&L: ${m['short_pnl']:,.2f})")

    print(f"\n--- Exit Reasons ---")
    for reason, count in m['exit_reasons'].items():
        print(f"  {reason:20s}: {count}")

    # Show last 10 trades
    trades = results['trades']
    if len(trades) > 0:
        print(f"\n--- Recent Trades (last 10) ---")
        cols = ['entry_bin', 'direction', 'entry_price', 'exit_price',
                'hold_bins', 'pnl_net', 'return_pct', 'exit_reason']
        print(trades[cols].tail(10).to_string(index=False, float_format='%.2f'))