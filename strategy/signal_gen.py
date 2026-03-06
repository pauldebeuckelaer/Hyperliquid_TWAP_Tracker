"""
Signal Generation Module
Generates the capped flow signal from orders + price data.
Exact logic validated across 18 days (avg r=+0.28, 16/18 positive).
"""

import pandas as pd
import numpy as np
from pathlib import Path
import config


def load_data(data_dir: str = None) -> dict:
    """Load all CSV files and parse timestamps."""
    data_dir = Path(data_dir or config.DATA_DIR)

    orders = pd.read_csv(data_dir / config.ORDERS_FILE)
    snapshots = pd.read_csv(data_dir / config.SNAPSHOTS_FILE)
    market = pd.read_csv(data_dir / config.MARKET_FILE)

    # Parse timestamps (mixed formats, some have timezone)
    orders['first_seen_at'] = pd.to_datetime(orders['first_seen_at'], format='mixed', utc=True)
    orders['completed_at'] = pd.to_datetime(orders['completed_at'], format='mixed', utc=True)
    snapshots['timestamp'] = pd.to_datetime(snapshots['timestamp'], format='mixed', utc=True)
    market['snapshot_time'] = pd.to_datetime(market['snapshot_time'], format='mixed', utc=True)

    # Load candles if available (for OI delta on earlier dates)
    candles_path = data_dir / config.CANDLES_FILE
    candles = None
    if candles_path.exists():
        candles = pd.read_csv(candles_path)
        candles['candle_time'] = pd.to_datetime(candles['candle_time'], format='mixed', utc=True)

    # Load orderbook if available
    ob_path = data_dir / config.ORDERBOOK_FILE
    orderbook = None
    if ob_path.exists():
        orderbook = pd.read_csv(ob_path)
        orderbook['snapshot_time'] = pd.to_datetime(orderbook['snapshot_time'], format='mixed', utc=True)

    return {
        'orders': orders,
        'snapshots': snapshots,
        'market': market,
        'candles': candles,
        'orderbook': orderbook
    }


def build_price_bins(snapshots: pd.DataFrame) -> pd.DataFrame:
    """
    Resample price data into 30min bins.
    Returns DataFrame with columns: [bin, price, fwd_return]
    """
    snapshots = snapshots.sort_values('timestamp')
    snapshots['bin'] = snapshots['timestamp'].dt.floor(config.BIN_SIZE)

    price_bins = snapshots.groupby('bin')['price'].first().reset_index()
    price_bins = price_bins.sort_values('bin')

    # Forward return: next bin's price / current bin's price - 1
    # Expressed as percentage
    price_bins['fwd_return'] = (
        price_bins['price'].shift(-1) / price_bins['price'] - 1
    ) * 100

    return price_bins


def build_capped_flow(orders: pd.DataFrame) -> pd.DataFrame:
    """
    Build capped flow signal from completed orders.
    Logic:
        1. Filter to completed orders only
        2. Bin by first_seen_at (30min)
        3. Group by (bin, address), sum signed_size
        4. Cap at +/- CAP per address per bin
        5. Sum capped flow per bin

    Returns DataFrame with columns: [bin, capped_flow, raw_flow, n_orders, n_addrs]
    """
    completed = orders[orders['status'] == 'completed'].copy()
    completed['bin'] = completed['first_seen_at'].dt.floor(config.BIN_SIZE)
    completed['signed_size'] = completed['size'] * completed['side'].map({
        'BUY': 1, 'SELL': -1
    })

    # Group by (bin, address) and cap
    addr_bin = completed.groupby(['bin', 'address'])['signed_size'].sum().reset_index()
    addr_bin['capped'] = addr_bin['signed_size'].clip(-config.CAP, config.CAP)

    # Aggregate per bin
    flow = addr_bin.groupby('bin').agg(
        capped_flow=('capped', 'sum'),
        raw_flow=('signed_size', 'sum'),
        n_addrs=('address', 'nunique')
    ).reset_index()

    # Order count per bin
    order_counts = completed.groupby('bin').size().reset_index(name='n_orders')
    flow = flow.merge(order_counts, on='bin', how='left')

    return flow


def build_oi_delta(market: pd.DataFrame, candles: pd.DataFrame = None) -> pd.DataFrame:
    """
    Build OI delta from market snapshots and/or candles.
    Candles fill in earlier dates where market snapshots don't exist.

    Returns DataFrame with columns: [bin, oi_delta]
    """
    frames = []

    # From candles (10min data, resample to 30min)
    if candles is not None and len(candles) > 0:
        candles = candles.sort_values('candle_time')
        candles['bin'] = candles['candle_time'].dt.floor(config.BIN_SIZE)
        candles_30m = candles.groupby('bin').agg(
            oi=('close_oi', 'last')
        ).reset_index()
        candles_30m['oi_delta'] = candles_30m['oi'].diff()
        frames.append(candles_30m[['bin', 'oi_delta']])

    # From market snapshots (~1min data)
    if market is not None and len(market) > 0:
        market = market.sort_values('snapshot_time')
        market['bin'] = market['snapshot_time'].dt.floor(config.BIN_SIZE)
        market_30m = market.groupby('bin').agg(
            oi=('open_interest', 'last')
        ).reset_index()
        market_30m['oi_delta'] = market_30m['oi'].diff()
        frames.append(market_30m[['bin', 'oi_delta']])

    if not frames:
        return pd.DataFrame(columns=['bin', 'oi_delta'])

    # Combine: prefer market_snapshots (higher res) where available
    combined = pd.concat(frames).drop_duplicates(subset='bin', keep='last')
    return combined.sort_values('bin').reset_index(drop=True)


def generate_signals(data: dict) -> pd.DataFrame:
    """
    Main signal generation pipeline.
    Combines capped flow + OI delta into a composite signal per 30min bin.

    Returns DataFrame with columns:
        [bin, date, price, fwd_return, capped_flow, raw_flow,
         oi_delta, cf_z, oi_z, signal, signal_z]
    """
    # Build components
    price_bins = build_price_bins(data['snapshots'])
    flow = build_capped_flow(data['orders'])
    oi_delta = build_oi_delta(data['market'], data.get('candles'))

    # Merge everything on bin
    signals = price_bins.merge(flow, on='bin', how='left')
    signals = signals.merge(oi_delta[['bin', 'oi_delta']], on='bin', how='left')

    # Fill missing flow bins with 0 (no orders = no flow)
    signals['capped_flow'] = signals['capped_flow'].fillna(0)
    signals['raw_flow'] = signals['raw_flow'].fillna(0)
    signals['oi_delta'] = signals['oi_delta'].fillna(0)

    # Add date column
    signals['date'] = signals['bin'].dt.date

    # Z-score per day (normalize within each day)
    signals['cf_z'] = 0.0
    signals['oi_z'] = 0.0

    for date, grp in signals.groupby('date'):
        idx = grp.index

        cf_std = grp['capped_flow'].std()
        if cf_std > 0:
            signals.loc[idx, 'cf_z'] = (
                (grp['capped_flow'] - grp['capped_flow'].mean()) / cf_std
            )

        oi_std = grp['oi_delta'].std()
        if oi_std > 0:
            signals.loc[idx, 'oi_z'] = (
                (grp['oi_delta'] - grp['oi_delta'].mean()) / oi_std
            )

    # Composite signal
    signals['signal'] = (
        config.WEIGHT_CF * signals['cf_z'] +
        config.WEIGHT_OI * signals['oi_z']
    )

    # Overall z-score of the composite (for thresholding)
    sig_std = signals['signal'].std()
    if sig_std > 0:
        signals['signal_z'] = (
            (signals['signal'] - signals['signal'].mean()) / sig_std
        )
    else:
        signals['signal_z'] = 0.0

    # Sort and clean
    signals = signals.sort_values('bin').reset_index(drop=True)

    # Drop last bin per day (no forward return)
    signals = signals.dropna(subset=['fwd_return'])

    return signals


def get_daily_volume(market: pd.DataFrame, candles: pd.DataFrame = None) -> pd.DataFrame:
    """
    Get daily notional volume for filtering.
    Returns DataFrame with columns: [date, daily_volume]
    """
    frames = []

    if candles is not None and len(candles) > 0:
        candles['date'] = candles['candle_time'].dt.date
        cv = candles.groupby('date')['volume'].last().reset_index()
        cv.columns = ['date', 'daily_volume']
        frames.append(cv)

    if market is not None and len(market) > 0:
        market['date'] = market['snapshot_time'].dt.date
        mv = market.groupby('date')['day_ntl_vlm'].last().reset_index()
        mv.columns = ['date', 'daily_volume']
        frames.append(mv)

    if not frames:
        return pd.DataFrame(columns=['date', 'daily_volume'])

    combined = pd.concat(frames).drop_duplicates(subset='date', keep='last')
    return combined.sort_values('date').reset_index(drop=True)