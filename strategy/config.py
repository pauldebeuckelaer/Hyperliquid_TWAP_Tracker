"""
Strategy Configuration
All tunable parameters in one place.
"""

# ============================================================
# DATA PATHS
# ============================================================
DATA_DIR = "../data/csv"  # relative to strategy folder

ORDERS_FILE = "orders_hype.csv"
SNAPSHOTS_FILE = "snapshots_hype.csv"
MARKET_FILE = "market_snapshots_hype.csv"
CANDLES_FILE = "market_candles_hype.csv"
ORDERBOOK_FILE = "orderbook_snapshots.csv"

# ============================================================
# SIGNAL PARAMETERS
# ============================================================
COIN = "HYPE"
CAP = 5000                  # max flow per address per bin
BIN_SIZE = "30min"          # time bin for aggregation

# Composite signal weights (CF + OI delta)
WEIGHT_CF = 0.5
WEIGHT_OI = 0.5

# ============================================================
# ENTRY RULES
# ============================================================
# Threshold mode: 'any', 'zscore', 'quantile'
ENTRY_MODE = "zscore"
ENTRY_ZSCORE = 1.0          # for zscore mode: min abs(z) to enter
ENTRY_QUANTILE = 0.75       # for quantile mode: min percentile

# Direction: 'long_only', 'short_only', 'both'
DIRECTION = "both"

# ============================================================
# EXIT RULES
# ============================================================
# Exit mode: 'trailing', 'fixed_target', 'next_bin', 'reversal', 'combined'
EXIT_MODE = "combined"

# Hard stop loss (always active regardless of mode)
HARD_STOP_PCT = 0.02        # 2% max loss

# Fixed target
FIXED_TARGET_PCT = 0.05     # 5% take profit

# Fixed position size (overrides percentage-based sizing for small accounts)
FIXED_POSITION_USD = 12.5

# Trailing stop
TRAIL_ACTIVATION_PCT = 0.03 # start trailing after 3% profit
TRAIL_STOP_PCT = 0.015      # trail distance: 1.5% from peak

# Max hold time (in number of bins, 0 = unlimited)
MAX_HOLD_BINS = 0

# Signal reversal: exit when CF flips sign
EXIT_ON_REVERSAL = True

# Signal reversal confirmation
REVERSAL_CONFIRM_BINS = 1

# ============================================================
# POSITION SIZING
# ============================================================
INITIAL_CAPITAL = 123    # starting capital in USD
RISK_PER_TRADE = 0.02       # risk 2% of capital per trade
MAX_POSITION_PCT = 0.20     # max 20% of capital in one position
LEVERAGE = 3                # max leverage

# ============================================================
# COSTS
# ============================================================
TAKER_FEE = 0.00035         # 0.035% per side (Hyperliquid)
MAKER_FEE = 0.0002          # 0.02% per side
SLIPPAGE = 0.001            # 0.1% estimated slippage
FUNDING_RATE_8H = 0.0001    # avg funding rate per 8h (will use actual if available)
# Entry uses maker, exit uses taker
ENTRY_FEE = 0.0002     # 0.02% maker
EXIT_FEE = 0.00035     # 0.035% taker

# ============================================================
# BACKTEST SETTINGS
# ============================================================
# Volume filter: minimum daily notional volume to trade
MIN_DAILY_VOLUME = 0        # 0 = no filter, 500_000_000 = 500M+

# Date range (None = use all available data)
START_DATE = None           # e.g. "2026-02-01"
END_DATE = None             # e.g. "2026-02-18"