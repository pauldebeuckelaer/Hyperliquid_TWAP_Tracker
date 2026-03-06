"""
Strategy Runner
Entry point for backtesting and parameter optimization.

Usage:
    python run_backtest.py                    # single run with config defaults
    python run_backtest.py --sweep            # parameter sweep
    python run_backtest.py --data ./my_csvs/  # custom data path
"""

import sys
import argparse
import pandas as pd
import numpy as np

import config
import signal_gen as sig
from backtester import Backtester, print_results


def run_single(data: dict, daily_volume: pd.DataFrame) -> dict:
    """Run a single backtest with current config settings."""
    signals = sig.generate_signals(data)
    bt = Backtester()
    results = bt.run(signals, daily_volume)
    print_results(results)
    return results


def run_sweep(data: dict, daily_volume: pd.DataFrame):
    """
    Sweep across key parameters to find optimal configuration.
    Tests combinations of: entry threshold, exit mode, direction.
    """
    signals_base = sig.generate_signals(data)

    sweep_configs = []

    # Define parameter grid
    entry_modes = [
        ('any', 0),
        ('zscore', 0.5),
        ('zscore', 1.0),
        ('zscore', 1.5),
    ]

    exit_modes = [
        ('next_bin', False),
        ('trailing', False),
        ('fixed_target', False),
        ('combined', True),
        ('combined', False),
    ]

    directions = ['both', 'long_only']

    total = len(entry_modes) * len(exit_modes) * len(directions)
    print(f"Running parameter sweep: {total} combinations")
    print("=" * 90)

    results_list = []
    run_num = 0

    for entry_mode, entry_z in entry_modes:
        for exit_mode, exit_reversal in exit_modes:
            for direction in directions:
                run_num += 1

                # Override config
                config.ENTRY_MODE = entry_mode
                config.ENTRY_ZSCORE = entry_z
                config.EXIT_MODE = exit_mode
                config.EXIT_ON_REVERSAL = exit_reversal
                config.DIRECTION = direction

                # Re-generate signals (z-scores may differ)
                signals = sig.generate_signals(data)

                bt = Backtester()
                result = bt.run(signals, daily_volume)
                m = result['metrics']

                if m.get('message'):
                    continue

                label = (f"entry={entry_mode}"
                         f"{'(z>' + str(entry_z) + ')' if entry_mode == 'zscore' else ''}"
                         f" exit={exit_mode}"
                         f"{'(+rev)' if exit_reversal else ''}"
                         f" dir={direction}")

                results_list.append({
                    'config': label,
                    'trades': m['total_trades'],
                    'win_rate': m['win_rate'],
                    'total_pnl': m['total_pnl'],
                    'return_pct': m['total_return_pct'],
                    'max_dd': m['max_drawdown'],
                    'sharpe': m['sharpe'],
                    'profit_factor': m['profit_factor'],
                    'expectancy': m['expectancy'],
                    'avg_hold_h': m['avg_hold_hours'],
                    'costs': m['total_costs'],
                })

    # Sort by total return
    sweep_df = pd.DataFrame(results_list)
    sweep_df = sweep_df.sort_values('total_pnl', ascending=False)

    print(f"\n{'=' * 90}")
    print(f"SWEEP RESULTS - Top 15 by P&L")
    print(f"{'=' * 90}")

    display_cols = ['config', 'trades', 'win_rate', 'total_pnl',
                    'return_pct', 'max_dd', 'sharpe', 'profit_factor']

    pd.set_option('display.max_colwidth', 60)
    pd.set_option('display.width', 200)
    print(sweep_df[display_cols].head(15).to_string(
        index=False,
        float_format=lambda x: f'{x:.2f}'
    ))

    print(f"\n--- Bottom 5 (worst) ---")
    print(sweep_df[display_cols].tail(5).to_string(
        index=False,
        float_format=lambda x: f'{x:.2f}'
    ))

    # Best config details
    if len(sweep_df) > 0:
        best = sweep_df.iloc[0]
        print(f"\n{'=' * 90}")
        print(f"BEST CONFIG: {best['config']}")
        print(f"  Return: {best['return_pct']:.2f}% | "
              f"Win rate: {best['win_rate']:.1f}% | "
              f"Sharpe: {best['sharpe']:.2f} | "
              f"Max DD: {best['max_dd']:.2%} | "
              f"Trades: {int(best['trades'])}")

    return sweep_df


def main():
    parser = argparse.ArgumentParser(description='HYPE Trading Strategy Backtester')
    parser.add_argument('--data', type=str, default=config.DATA_DIR,
                        help='Path to CSV data directory')
    parser.add_argument('--sweep', action='store_true',
                        help='Run parameter sweep instead of single backtest')
    parser.add_argument('--start', type=str, default=None,
                        help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default=None,
                        help='End date (YYYY-MM-DD)')
    parser.add_argument('--volume-filter', type=float, default=None,
                        help='Minimum daily volume in millions (e.g. 500)')

    args = parser.parse_args()

    # Override config from args
    if args.start:
        config.START_DATE = args.start
    if args.end:
        config.END_DATE = args.end
    if args.volume_filter:
        config.MIN_DAILY_VOLUME = args.volume_filter * 1_000_000

    # Load data
    print("Loading data...")
    data = sig.load_data(args.data)
    daily_volume = sig.get_daily_volume(data['market'], data.get('candles'))

    print(f"Orders: {len(data['orders'])} | "
          f"Snapshots: {len(data['snapshots'])} | "
          f"Market: {len(data['market'])}")
    if data.get('candles') is not None:
        print(f"Candles: {len(data['candles'])}")
    print()

    if args.sweep:
        run_sweep(data, daily_volume)
    else:
        run_single(data, daily_volume)


if __name__ == '__main__':
    main()