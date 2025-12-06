#!/usr/bin/env python3
"""
All Coins TWAP State Tracker
=============================
Tracks TWAP order states and changes for ALL coins on Hyperliquid.

NOW WITH SQLITE STORAGE - replaces JSONL for efficient querying and smaller storage.

Single tracker instance managing multiple coins with:
- Per-coin state tracking (current/previous snapshots)
- Consolidated logging (all coins in one log)
- SQLite database storage (replaces JSONL)
- Unified address history (stored in SQLite)
- Full depth tracking (new/completed/canceled orders, state changes)
- Progress tracking (elapsed time, progress %, time remaining)
"""
import logging
from datetime import datetime
from typing import List, Dict, Optional, Set
from pathlib import Path

# Import models
from api_client.models import TWAPOrder, TWAPSnapshot
from storage.sqlite_backend import SQLiteBackend

logger = logging.getLogger(__name__)

# =============================================================================
# DATA PATHS
# =============================================================================
DATA_DIR = Path('data')
DB_PATH = DATA_DIR / 'twap.db'


def format_size(size: float) -> str:
    """Format size with appropriate precision based on magnitude"""
    if size < 1:
        return f"{size:>10.4f}"
    elif size < 10:
        return f"{size:>10.2f}"
    else:
        return f"{size:>10,.0f}"


def format_usd_pressure(size: float, duration_hours: float, price: float) -> str:
    """Calculate and format USD pressure per minute"""
    if duration_hours <= 0 or price is None:
        return ""

    duration_minutes = duration_hours * 60
    usd_per_min = (size / duration_minutes) * price

    if usd_per_min >= 1000:
        return f"${usd_per_min:>8,.0f}/min"
    else:
        return f"${usd_per_min:>8.2f}/min"


def format_progress(order) -> str:
    """
    Format elapsed/progress/remaining for an order.
    """
    if isinstance(order, dict):
        elapsed = order.get('elapsed_minutes')
        remaining = order.get('time_remaining_minutes')
        progress = order.get('progress_percent')
        duration = order.get('duration_minutes', int(order.get('duration_hours', 0) * 60))
    else:
        elapsed = order.elapsed_minutes
        remaining = order.time_remaining_minutes
        progress = order.progress_percent
        duration = order.duration_minutes

    if elapsed is None:
        return ""

    if remaining is not None and progress is not None:
        return f"{elapsed}m/{duration}m ({progress:.0f}%)"
    elif progress is not None:
        return f"{progress:.0f}%"
    else:
        return f"{elapsed}m elapsed"


class CoinState:
    """State container for a single coin"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.update_count = 0
        self.current_snapshot: Optional[TWAPSnapshot] = None
        self.previous_snapshot: Optional[TWAPSnapshot] = None


class AllCoinsStateTracker:
    """Tracks TWAP order states for ALL coins on Hyperliquid"""

    U_PREFIX_EXCEPTIONS = {'USDC', 'USDT', 'USUAL', 'UNI', 'UMA', 'USTC', 'UP'}

    def __init__(self, exclude_coins: List[str] = None):
        """
        Initialize the all-coins tracker.

        Args:
            exclude_coins: List of coins to exclude
        """
        self.exclude_coins = set(exclude_coins or [])

        # Track state per coin
        self.coin_states: Dict[str, CoinState] = {}

        # Global update counter
        self.global_update_count = 0

        # SQLite storage backend
        self.db = SQLiteBackend(DB_PATH)

        # Load addresses from database
        self.all_addresses_seen: Set[str] = set(self.db.get_all_addresses())

        logger.info("=" * 70)
        logger.info("All Coins TWAP State Tracker Initialized (SQLite)")
        logger.info(f"Database: {DB_PATH}")
        logger.info(f"Loaded {len(self.all_addresses_seen)} addresses from history")
        if self.exclude_coins:
            logger.info(f"Excluding coins: {', '.join(sorted(self.exclude_coins))}")
        logger.info("=" * 70)

    def _get_price(self, symbol: str, prices: Dict[str, float]) -> Optional[float]:
        """Get price for symbol, resolving U-prefix spot derivatives"""
        if symbol in prices:
            return prices[symbol]

        if symbol.startswith('U') and len(symbol) > 1 and symbol not in self.U_PREFIX_EXCEPTIONS:
            underlying = symbol[1:]
            if underlying in prices:
                return prices[underlying]

        return None

    def _detect_changes(self, symbol: str, raw_changes: Dict) -> Dict:
        """Process raw changes to separate completed/canceled orders"""
        completed_orders = []
        canceled_orders = []
        handled_hashes = set()

        for order in raw_changes.get('completed_orders', []):
            if order.status in ['canceled', 'error']:
                handled_hashes.add(order.order_hash)
                continue
            else:
                handled_hashes.add(order.order_hash)
                completed_orders.append(order)

        for change in raw_changes.get('status_changes', []):
            if change.get('new_status') in ['canceled', 'error']:
                if change['order_hash'] not in handled_hashes:
                    handled_hashes.add(change['order_hash'])
                    canceled_orders.append({
                        'full_address': change['address'],
                        'order_hash': change['order_hash'],
                        'side': change['side'],
                        'size': change['size'],
                        'product_type': change['product_type'],
                        'duration_hours': change['duration_hours'],
                        'duration_minutes': int(change['duration_hours'] * 60),
                        'status': change['new_status'],
                        'elapsed_minutes': change.get('elapsed_minutes'),
                        'progress_percent': change.get('progress_percent'),
                        'time_remaining_minutes': change.get('time_remaining_minutes')
                    })

        filtered_status_changes = [
            change for change in raw_changes.get('status_changes', [])
            if change['order_hash'] not in handled_hashes
               and change.get('old_status') not in ['canceled', 'error']
        ]

        return {
            'new_orders': raw_changes.get('new_orders', []),
            'completed_orders': completed_orders,
            'canceled_orders': canceled_orders,
            'status_changes': filtered_status_changes
        }

    def update(self, all_coins_data: Dict[str, List[Dict]], prices: Dict[str, float] = None):
        """Update with new TWAP data for all coins."""
        prices = prices or {}
        self.global_update_count += 1

        logger.info("\n" + "=" * 70)
        logger.info(f"ALL COINS UPDATE #{self.global_update_count}")
        logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 70)

        coins_with_orders = []
        total_orders = 0
        total_active_orders = 0

        for symbol, raw_orders in all_coins_data.items():
            if symbol in self.exclude_coins:
                logger.debug(f"Skipping excluded coin: {symbol}")
                continue

            if not raw_orders:
                continue

            if symbol not in self.coin_states:
                self.coin_states[symbol] = CoinState(symbol)
                logger.debug(f"Created new state for: {symbol}")

            coin_state = self.coin_states[symbol]
            coin_state.update_count += 1

            new_snapshot = TWAPSnapshot.from_hypurr_data(
                raw_orders,
                symbol,
                coin_state.update_count,
                current_price=self._get_price(symbol, prices)
            )

            for order in new_snapshot.orders:
                logger.debug(
                    f"[{symbol}] Order: {order.display_address} - "
                    f"Hash: {order.order_hash[:16]}... - Size: {order.size}"
                )

            coin_state.previous_snapshot = coin_state.current_snapshot
            coin_state.current_snapshot = new_snapshot

            # Track addresses
            addresses_before = len(self.all_addresses_seen)
            self.all_addresses_seen.update(new_snapshot.unique_addresses)

            if len(self.all_addresses_seen) > addresses_before:
                new_count = len(self.all_addresses_seen) - addresses_before
                logger.debug(
                    f"[{symbol}] Discovered {new_count} new address(es). "
                    f"Total: {len(self.all_addresses_seen)}"
                )

            if new_snapshot.active_orders:
                coins_with_orders.append(symbol)
                total_orders += new_snapshot.total_orders
                total_active_orders += len(new_snapshot.active_orders)

            # Detect changes
            if coin_state.previous_snapshot:
                raw_changes = new_snapshot.compare_with(coin_state.previous_snapshot)
                changes = self._detect_changes(symbol, raw_changes)
            else:
                changes = {
                    'new_orders': new_snapshot.active_orders,
                    'completed_orders': [],
                    'canceled_orders': [],
                    'status_changes': []
                }

            # Log this coin's snapshot
            self._log_coin_snapshot(symbol, new_snapshot, changes)

            # Save to SQLite (replaces _save_coin_to_json)
            self._save_to_sqlite(symbol, coin_state, new_snapshot, changes)

        # Log global summary
        logger.info("=" * 70)
        logger.info(f"📊 GLOBAL SUMMARY")
        logger.info(f"Coins with active orders: {len(coins_with_orders)}")
        logger.info(f"Total orders tracked: {total_orders} ({total_active_orders} active)")
        logger.info(f"Total addresses seen: {len(self.all_addresses_seen)}")
        logger.info(f"Active coins: {', '.join(sorted(coins_with_orders)[:20])}")
        if len(coins_with_orders) > 20:
            logger.info(f"... and {len(coins_with_orders) - 20} more")
        logger.info("=" * 70)

    def _save_to_sqlite(self, symbol: str, coin_state: CoinState, snapshot: TWAPSnapshot, changes: Dict):
        """Save snapshot data to SQLite database"""
        try:
            stats = snapshot.get_stats()
            active = snapshot.active_orders

            # Build snapshot data dict
            snapshot_data = {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'update_number': coin_state.update_count,
                'current_price': snapshot.current_price,
                'summary': {
                    'total_orders': stats['total_orders'],
                    'active_orders': stats['active_orders'],
                    'buy_volume': stats['buy_volume'],
                    'sell_volume': stats['sell_volume'],
                    'net_flow': stats['net_flow'],
                    'spot_buy_pressure': sum(
                        o.get_execution_rate() for o in active if o.is_buy_side and o.product_type == 'SPOT'
                    ),
                    'spot_sell_pressure': sum(
                        o.get_execution_rate() for o in active if o.is_sell_side and o.product_type == 'SPOT'
                    ),
                    'perp_buy_pressure': sum(
                        o.get_execution_rate() for o in active if o.is_buy_side and o.product_type == 'PERP'
                    ),
                    'perp_sell_pressure': sum(
                        o.get_execution_rate() for o in active if o.is_sell_side and o.product_type == 'PERP'
                    ),
                    'buy_pressure_per_min': stats['buy_pressure_per_min'],
                    'sell_pressure_per_min': stats['sell_pressure_per_min'],
                    'net_pressure_per_min': stats['net_pressure_per_min'],
                    'unique_addresses': stats['unique_addresses']
                },
                'active_orders': [self._order_to_dict(o) for o in snapshot.active_orders]
            }

            # Convert changes for SQLite backend
            changes_for_db = {
                'new_orders': changes.get('new_orders', []),
                'completed_orders': changes.get('completed_orders', []),
                'canceled_orders': changes.get('canceled_orders', [])
            }

            # Save to database
            self.db.save_snapshot(symbol, snapshot_data, changes_for_db)

            logger.debug(f"Saved {symbol} snapshot #{coin_state.update_count} to SQLite")

        except Exception as e:
            logger.error(f"Error saving {symbol} to SQLite: {e}")
            logger.exception(e)

    def _order_to_dict(self, order) -> Dict:
        """Convert TWAPOrder to dict for storage"""
        if isinstance(order, dict):
            return {
                'address': order.get('full_address', order.get('address', '')),
                'order_hash': order.get('order_hash', ''),
                'side': order.get('side', ''),
                'size': order.get('size', 0),
                'product_type': order.get('product_type', ''),
                'duration_minutes': order.get('duration_minutes', int(order.get('duration_hours', 0) * 60)),
                'status': order.get('status', 'unknown'),
                'elapsed_minutes': order.get('elapsed_minutes'),
                'progress_percent': order.get('progress_percent'),
            }
        return {
            'address': order.full_address,
            'order_hash': order.order_hash,
            'side': order.side,
            'size': order.size,
            'product_type': order.product_type,
            'duration_minutes': order.duration_minutes,
            'status': order.status,
            'elapsed_minutes': order.elapsed_minutes,
            'progress_percent': order.progress_percent,
        }

    def _log_coin_snapshot(self, symbol: str, snapshot: TWAPSnapshot, changes: Dict):
        """Log snapshot and changes for a single coin"""
        stats = snapshot.get_stats()

        logger.info(f"\n{'─' * 70}")
        logger.info(f"💰 {symbol}")
        logger.info(f"{'─' * 70}")

        spot_orders = [o for o in snapshot.orders if o.product_type == 'SPOT']
        perp_orders = [o for o in snapshot.orders if o.product_type == 'PERP']

        spot_orders.sort(key=lambda o: o.size, reverse=True)
        perp_orders.sort(key=lambda o: o.size, reverse=True)

        active_spot = sum(1 for o in spot_orders if o.is_active)
        active_perp = sum(1 for o in perp_orders if o.is_active)

        if snapshot.current_price:
            if active_spot > 0 and active_perp > 0:
                market_breakdown = f"({active_spot} SPOT, {active_perp} PERP)"
            elif active_spot > 0:
                market_breakdown = "SPOT"
            elif active_perp > 0:
                market_breakdown = "PERP"
            else:
                market_breakdown = ""

            logger.info(
                f"Price: ${snapshot.current_price:,.4f} | "
                f"Orders: {stats['total_orders']} total | "
                f"Active: {stats['active_orders']} {market_breakdown}"
            )

            price = snapshot.current_price

            spot_buy_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in spot_orders if o.is_active and o.is_buy_side and o.duration_hours > 0
            )
            spot_sell_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in spot_orders if o.is_active and o.is_sell_side and o.duration_hours > 0
            )
            perp_buy_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in perp_orders if o.is_active and o.is_buy_side and o.duration_hours > 0
            )
            perp_sell_usd = sum(
                (o.size / (o.duration_hours * 60)) * price
                for o in perp_orders if o.is_active and o.is_sell_side and o.duration_hours > 0
            )

            total_buy_usd = spot_buy_usd + perp_buy_usd
            total_sell_usd = spot_sell_usd + perp_sell_usd

            if active_spot > 0:
                logger.info(
                    f"💵 SPOT Pressure: Buy ${spot_buy_usd:,.0f}/min | "
                    f"Sell ${spot_sell_usd:,.0f}/min | Net ${spot_buy_usd - spot_sell_usd:+,.0f}/min"
                )
            if active_perp > 0:
                logger.info(
                    f"⚡ PERP Pressure: Buy ${perp_buy_usd:,.0f}/min | "
                    f"Sell ${perp_sell_usd:,.0f}/min | Net ${perp_buy_usd - perp_sell_usd:+,.0f}/min"
                )
            if active_spot > 0 and active_perp > 0:
                logger.info(
                    f"📊 TOTAL Pressure: Buy ${total_buy_usd:,.0f}/min | "
                    f"Sell ${total_sell_usd:,.0f}/min | Net ${total_buy_usd - total_sell_usd:+,.0f}/min"
                )
        else:
            if active_spot > 0 and active_perp > 0:
                market_breakdown = f"({active_spot} SPOT, {active_perp} PERP)"
            elif active_spot > 0:
                market_breakdown = "SPOT"
            elif active_perp > 0:
                market_breakdown = "PERP"
            else:
                market_breakdown = ""

            logger.info(
                f"Orders: {stats['total_orders']} total | "
                f"Active: {stats['active_orders']} {market_breakdown}"
            )

        # SPOT MARKET
        if active_spot > 0:
            logger.info(f"  💵 SPOT - {active_spot} Active:")
            for order in spot_orders:
                if not order.is_active:
                    continue

                side_emoji = "🟢" if order.is_buy_side else "🔴"
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"

                if snapshot.current_price:
                    usd_pressure = format_usd_pressure(order.size, order.duration_hours, snapshot.current_price)
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str} | {usd_pressure}"
                    )
                else:
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str}"
                    )

        # PERP MARKET
        if active_perp > 0:
            logger.info(f"  ⚡ PERP - {active_perp} Active:")
            for order in perp_orders:
                if not order.is_active:
                    continue

                side_emoji = "🟢" if order.is_buy_side else "🔴"
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"

                if snapshot.current_price:
                    usd_pressure = format_usd_pressure(order.size, order.duration_hours, snapshot.current_price)
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str} | {usd_pressure}"
                    )
                else:
                    logger.info(
                        f"    {side_emoji} {order.full_address} | {order.side:4s} | "
                        f"{format_size(order.size)} | {order.duration_hours:>5.1f}h{progress_str}"
                    )

        self._log_coin_changes(symbol, changes)

    def _log_coin_changes(self, symbol: str, changes: Dict):
        """Log detected changes for a coin"""
        if changes['new_orders']:
            logger.info(f"  🆕 [{symbol}] New orders: {len(changes['new_orders'])}")
            for order in changes['new_orders']:
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"
                logger.info(
                    f"    NEW: {order.full_address} {order.side:4s} {format_size(order.size)} "
                    f"{order.product_type} {order.duration_hours:.1f}h{progress_str}"
                )

        completed_orders = changes.get('completed_orders', [])
        if completed_orders:
            logger.info(f"  ✅ [{symbol}] Completed: {len(completed_orders)}")
            for order in completed_orders:
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"
                logger.info(
                    f"    COMPLETED: {order.full_address} {order.side:4s} {format_size(order.size)} "
                    f"{order.product_type} {order.duration_hours:.1f}h{progress_str}"
                )

        canceled_orders = changes.get('canceled_orders', [])
        if canceled_orders:
            logger.warning(f"  ❌ [{symbol}] Canceled: {len(canceled_orders)}")
            for order in canceled_orders:
                progress_str = format_progress(order)
                if progress_str:
                    progress_str = f" | {progress_str}"

                if isinstance(order, dict):
                    addr = order['full_address']
                    side = order['side']
                    size = order['size']
                    ptype = order['product_type']
                    dur = order['duration_hours']
                else:
                    addr = order.full_address
                    side = order.side
                    size = order.size
                    ptype = order.product_type
                    dur = order.duration_hours

                logger.warning(
                    f"    CANCELED: {addr} {side:4s} {format_size(size)} "
                    f"{ptype} {dur:.1f}h{progress_str}"
                )

        if changes['status_changes']:
            logger.warning(f"  🔄 [{symbol}] Status changes: {len(changes['status_changes'])}")
            for change in changes['status_changes']:
                elapsed = change.get('elapsed_minutes')
                progress = change.get('progress_percent')
                if elapsed is not None and progress is not None:
                    progress_str = f" | {elapsed}m ({progress:.0f}%)"
                else:
                    progress_str = ""
                logger.warning(
                    f"    STATUS: {change['address']} {change['side']:4s} {format_size(change['size'])} "
                    f"{change['product_type']} {change['old_status']} → {change['new_status']}{progress_str}"
                )

    def get_current_stats(self) -> Dict:
        """Get current statistics across all coins"""
        total_orders = 0
        total_active = 0
        total_volume = 0
        coins_with_activity = 0

        for coin_state in self.coin_states.values():
            if coin_state.current_snapshot:
                snapshot = coin_state.current_snapshot
                total_orders += snapshot.total_orders
                total_active += len(snapshot.active_orders)
                total_volume += snapshot.total_volume

                if snapshot.active_orders:
                    coins_with_activity += 1

        # Add database stats
        db_stats = self.db.get_stats()

        return {
            'global_update_count': self.global_update_count,
            'total_coins_tracked': len(self.coin_states),
            'coins_with_activity': coins_with_activity,
            'total_orders': total_orders,
            'total_active_orders': total_active,
            'total_volume': total_volume,
            'all_time_addresses': len(self.all_addresses_seen),
            'excluded_coins': list(self.exclude_coins),
            'db_stats': db_stats
        }

    def get_coin_stats(self, symbol: str) -> Optional[Dict]:
        """Get stats for a specific coin"""
        coin_state = self.coin_states.get(symbol)
        if not coin_state or not coin_state.current_snapshot:
            return None

        stats = coin_state.current_snapshot.get_stats()
        stats['update_count'] = coin_state.update_count
        return stats

    def get_address_history(self) -> Dict:
        """Get address history from database"""
        return {
            'total_addresses': len(self.all_addresses_seen),
            'addresses': self.db.get_all_addresses()
        }

    def get_active_coins(self) -> List[str]:
        """Get list of coins with active orders"""
        active = []
        for symbol, coin_state in self.coin_states.items():
            if coin_state.current_snapshot and coin_state.current_snapshot.active_orders:
                active.append(symbol)
        return sorted(active)

    def close(self):
        """Close database connection"""
        if self.db:
            self.db.close()