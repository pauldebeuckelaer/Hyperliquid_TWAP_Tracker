import pandas as pd
import config
import signal_gen as sig
from backtester import Backtester, print_results

data = sig.load_data()
daily_volume = sig.get_daily_volume(data['market'], data.get('candles'))

# Test the top configs and compare cost efficiency
configs = [
    ('z>1.0 next_bin both',      'zscore', 1.0, 'next_bin',  False, 'both'),
    ('z>1.0 combined+rev both',  'zscore', 1.0, 'combined',  True,  'both'),
    ('z>1.5 next_bin both',      'zscore', 1.5, 'next_bin',  False, 'both'),
    ('z>1.5 combined+rev both',  'zscore', 1.5, 'combined',  True,  'both'),
    ('z>1.0 next_bin long',      'zscore', 1.0, 'next_bin',  False, 'long_only'),
    ('z>1.0 combined+rev long',  'zscore', 1.0, 'combined',  True,  'long_only'),
]

rows = []
for label, entry, z, exit_m, rev, direction in configs:
    config.ENTRY_MODE = entry
    config.ENTRY_ZSCORE = z
    config.EXIT_MODE = exit_m
    config.EXIT_ON_REVERSAL = rev
    config.DIRECTION = direction
    config.REVERSAL_CONFIRM_BINS = 1

    signals = sig.generate_signals(data)
    bt = Backtester()
    r = bt.run(signals, daily_volume)
    m = r['metrics']
    if m.get('message'):
        continue

    cost_per_trade = m['total_costs'] / m['total_trades']
    gross_pnl = m['total_pnl'] + m['total_costs']
    cost_pct_of_gross = m['total_costs'] / gross_pnl * 100 if gross_pnl > 0 else 0

    rows.append({
        'config': label,
        'trades': m['total_trades'],
        'win%': f"{m['win_rate']:.0f}",
        'gross_pnl': f"${gross_pnl:,.0f}",
        'costs': f"${m['total_costs']:,.0f}",
        'cost/trade': f"${cost_per_trade:,.0f}",
        'costs_%_gross': f"{cost_pct_of_gross:.0f}%",
        'net_pnl': f"${m['total_pnl']:,.0f}",
        'net/trade': f"${m['expectancy']:,.0f}",
        'sharpe': f"{m['sharpe']:.2f}",
        'max_dd': f"{m['max_drawdown']:.1%}",
    })

df = pd.DataFrame(rows)
print(df.to_string(index=False))