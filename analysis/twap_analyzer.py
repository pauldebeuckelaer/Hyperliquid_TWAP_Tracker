"""
TWAP Data Analyzer
Analyzes collected TWAP tracker data for patterns and insights.

Features:
- Daily summary statistics
- Completion/cancellation pattern analysis
- Orderflow analysis (pressure vs price correlation)
- Whale behavior tracking
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
import statistics


@dataclass
class OrderEvent:
    """Represents a single order event (new, completed, canceled)"""
    timestamp: datetime
    symbol: str
    address: str
    order_hash: str
    side: str
    size: float
    product_type: str
    duration_minutes: int
    elapsed_minutes: int
    progress_percent: float
    event_type: str  # 'new', 'completed', 'canceled'
    price_at_event: float


@dataclass
class SnapshotSummary:
    """Summary data from a single snapshot"""
    timestamp: datetime
    symbol: str
    price: float
    total_orders: int
    active_orders: int
    buy_pressure: float
    sell_pressure: float
    net_pressure: float
    whale_orders: int
    new_orders: int
    completed_orders: int
    canceled_orders: int


class TWAPAnalyzer:
    def __init__(self, data_dir: str = "./allcoins_json_logs"):
        self.data_dir = Path(data_dir)
        self.snapshots: list[SnapshotSummary] = []
        self.order_events: list[OrderEvent] = []
        self.coins_loaded: set[str] = set()

    def load_coin_data(self, symbol: str, date: Optional[str] = None):
        """
        Load data for a specific coin.
        date format: YYYYMMDD (e.g., '20251130')
        If date is None, loads all available dates.
        """
        coin_dir = self.data_dir / symbol
        if not coin_dir.exists():
            print(f"No data directory for {symbol}")
            return

        if date:
            files = [coin_dir / f"{symbol}_{date}.jsonl"]
        else:
            files = sorted(coin_dir.glob(f"{symbol}_*.jsonl"))

        for filepath in files:
            if not filepath.exists():
                print(f"File not found: {filepath}")
                continue
            self._load_jsonl(filepath, symbol)

        self.coins_loaded.add(symbol)
        print(f"Loaded {symbol}: {len([s for s in self.snapshots if s.symbol == symbol])} snapshots")

    def load_all_coins(self, date: Optional[str] = None):
        """Load data for all coins in the data directory"""
        if not self.data_dir.exists():
            print(f"Data directory not found: {self.data_dir}")
            return

        for coin_dir in sorted(self.data_dir.iterdir()):
            if coin_dir.is_dir():
                self.load_coin_data(coin_dir.name, date)

    def _load_jsonl(self, filepath: Path, symbol: str):
        """Parse a JSONL file and extract snapshots + order events"""
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    self._parse_snapshot(data, symbol)
                except json.JSONDecodeError as e:
                    print(f"JSON error in {filepath}: {e}")
                    continue

    def _parse_snapshot(self, data: dict, symbol: str):
        """Parse a single JSON snapshot into structured data"""
        timestamp = datetime.fromisoformat(data['timestamp'])
        price = data.get('current_price', 0)
        summary = data.get('summary', {})
        events = data.get('events', {})

        # Create snapshot summary
        snapshot = SnapshotSummary(
            timestamp=timestamp,
            symbol=symbol,
            price=price,
            total_orders=summary.get('total_orders', 0),
            active_orders=summary.get('active_orders', 0),
            buy_pressure=summary.get('buy_pressure_per_min', 0),
            sell_pressure=summary.get('sell_pressure_per_min', 0),
            net_pressure=summary.get('net_pressure_per_min', 0),
            whale_orders=summary.get('whale_orders', 0),
            new_orders=events.get('new_orders', 0),
            completed_orders=events.get('completed_orders', 0),
            canceled_orders=events.get('canceled_orders', 0),
        )
        self.snapshots.append(snapshot)

        # Extract order events
        for order in data.get('new_orders', []):
            self.order_events.append(OrderEvent(
                timestamp=timestamp,
                symbol=symbol,
                address=order.get('address', ''),
                order_hash=order.get('order_hash', ''),
                side=order.get('side', ''),
                size=order.get('size', 0),
                product_type=order.get('product_type', ''),
                duration_minutes=order.get('duration_minutes', 0),
                elapsed_minutes=order.get('elapsed_minutes', 0),
                progress_percent=order.get('progress_percent', 0),
                event_type='new',
                price_at_event=price,
            ))

        for order in data.get('completed_orders', []):
            self.order_events.append(OrderEvent(
                timestamp=timestamp,
                symbol=symbol,
                address=order.get('address', ''),
                order_hash=order.get('order_hash', ''),
                side=order.get('side', ''),
                size=order.get('size', 0),
                product_type=order.get('product_type', ''),
                duration_minutes=order.get('duration_minutes', 0),
                elapsed_minutes=order.get('elapsed_minutes', 0),
                progress_percent=order.get('progress_percent', 0),
                event_type='completed',
                price_at_event=price,
            ))

        for order in data.get('canceled_orders', []):
            self.order_events.append(OrderEvent(
                timestamp=timestamp,
                symbol=symbol,
                address=order.get('address', ''),
                order_hash=order.get('order_hash', ''),
                side=order.get('side', ''),
                size=order.get('size', 0),
                product_type=order.get('product_type', ''),
                duration_minutes=order.get('duration_minutes', 0),
                elapsed_minutes=order.get('elapsed_minutes', 0),
                progress_percent=order.get('progress_percent', 0),
                event_type='canceled',
                price_at_event=price,
            ))

    # =========================================================================
    # DAILY SUMMARY ANALYSIS
    # =========================================================================

    def daily_summary(self, symbol: Optional[str] = None) -> dict:
        """
        Generate daily summary statistics.
        Returns aggregate stats per coin per day.
        """
        # Filter snapshots
        snapshots = self.snapshots
        if symbol:
            snapshots = [s for s in snapshots if s.symbol == symbol]

        if not snapshots:
            return {}

        # Group by coin and date
        daily_data = defaultdict(lambda: defaultdict(list))
        for s in snapshots:
            date_key = s.timestamp.strftime('%Y-%m-%d')
            daily_data[s.symbol][date_key].append(s)

        results = {}
        for coin, dates in daily_data.items():
            results[coin] = {}
            for date, snaps in dates.items():
                results[coin][date] = self._compute_daily_stats(snaps)

        return results

    def _compute_daily_stats(self, snapshots: list[SnapshotSummary]) -> dict:
        """Compute statistics for a single day's snapshots"""
        if not snapshots:
            return {}

        prices = [s.price for s in snapshots if s.price is not None and s.price > 0]
        buy_pressures = [s.buy_pressure for s in snapshots]
        sell_pressures = [s.sell_pressure for s in snapshots]
        net_pressures = [s.net_pressure for s in snapshots]

        total_new = sum(s.new_orders for s in snapshots)
        total_completed = sum(s.completed_orders for s in snapshots)
        total_canceled = sum(s.canceled_orders for s in snapshots)

        return {
            'snapshot_count': len(snapshots),
            'time_span_hours': (snapshots[-1].timestamp - snapshots[0].timestamp).total_seconds() / 3600,
            'price': {
                'open': prices[0] if prices else 0,
                'close': prices[-1] if prices else 0,
                'high': max(prices) if prices else 0,
                'low': min(prices) if prices else 0,
                'change_pct': ((prices[-1] - prices[0]) / prices[0] * 100) if prices and prices[0] > 0 else 0,
            },
            'pressure': {
                'avg_buy': statistics.mean(buy_pressures) if buy_pressures else 0,
                'avg_sell': statistics.mean(sell_pressures) if sell_pressures else 0,
                'avg_net': statistics.mean(net_pressures) if net_pressures else 0,
                'max_buy': max(buy_pressures) if buy_pressures else 0,
                'max_sell': max(sell_pressures) if sell_pressures else 0,
            },
            'orders': {
                'new': total_new,
                'completed': total_completed,
                'canceled': total_canceled,
                'completion_rate': total_completed / (total_completed + total_canceled) * 100 if (total_completed + total_canceled) > 0 else 0,
            },
            'activity': {
                'avg_active_orders': statistics.mean([s.active_orders for s in snapshots]),
                'max_active_orders': max(s.active_orders for s in snapshots),
                'avg_whale_orders': statistics.mean([s.whale_orders for s in snapshots]),
            }
        }

    # =========================================================================
    # COMPLETION / CANCELLATION PATTERN ANALYSIS
    # =========================================================================

    def completion_patterns(self, symbol: Optional[str] = None) -> dict:
        """
        Analyze patterns in completed vs canceled orders.
        """
        events = self.order_events
        if symbol:
            events = [e for e in events if e.symbol == symbol]

        completed = [e for e in events if e.event_type == 'completed']
        canceled = [e for e in events if e.event_type == 'canceled']
        new_orders = [e for e in events if e.event_type == 'new']

        return {
            'overview': {
                'total_new': len(new_orders),
                'total_completed': len(completed),
                'total_canceled': len(canceled),
                'completion_rate': len(completed) / (len(completed) + len(canceled)) * 100 if (len(completed) + len(canceled)) > 0 else 0,
            },
            'completed_stats': self._analyze_order_group(completed, 'completed'),
            'canceled_stats': self._analyze_order_group(canceled, 'canceled'),
            'by_side': {
                'buy_completed': len([e for e in completed if e.side == 'BUY']),
                'buy_canceled': len([e for e in canceled if e.side == 'BUY']),
                'sell_completed': len([e for e in completed if e.side == 'SELL']),
                'sell_canceled': len([e for e in canceled if e.side == 'SELL']),
            },
            'by_hour': self._events_by_hour(completed, canceled),
        }

    def _analyze_order_group(self, orders: list[OrderEvent], group_name: str) -> dict:
        """Analyze a group of orders (completed or canceled)"""
        if not orders:
            return {'count': 0}

        durations = [o.duration_minutes for o in orders if o.duration_minutes > 0]
        elapsed = [o.elapsed_minutes for o in orders if o.elapsed_minutes > 0]
        progress = [o.progress_percent for o in orders]
        sizes = [o.size for o in orders if o.size > 0]

        return {
            'count': len(orders),
            'duration_minutes': {
                'avg': statistics.mean(durations) if durations else 0,
                'median': statistics.median(durations) if durations else 0,
                'min': min(durations) if durations else 0,
                'max': max(durations) if durations else 0,
            },
            'elapsed_minutes': {
                'avg': statistics.mean(elapsed) if elapsed else 0,
                'median': statistics.median(elapsed) if elapsed else 0,
            },
            'progress_at_event': {
                'avg': statistics.mean(progress) if progress else 0,
                'median': statistics.median(progress) if progress else 0,
            },
            'size': {
                'avg': statistics.mean(sizes) if sizes else 0,
                'median': statistics.median(sizes) if sizes else 0,
                'total': sum(sizes),
            }
        }

    def _events_by_hour(self, completed: list, canceled: list) -> dict:
        """Group events by hour of day"""
        by_hour = defaultdict(lambda: {'completed': 0, 'canceled': 0})

        for e in completed:
            hour = e.timestamp.hour
            by_hour[hour]['completed'] += 1

        for e in canceled:
            hour = e.timestamp.hour
            by_hour[hour]['canceled'] += 1

        return dict(sorted(by_hour.items()))

    # =========================================================================
    # ORDERFLOW ANALYSIS
    # =========================================================================

    def orderflow_analysis(self, symbol: str) -> dict:
        """
        Analyze orderflow (pressure) and its relationship to price.
        """
        snapshots = [s for s in self.snapshots if s.symbol == symbol]
        if len(snapshots) < 2:
            return {'error': 'Not enough data points'}

        snapshots = sorted(snapshots, key=lambda x: x.timestamp)

        # Calculate price changes between snapshots
        price_changes = []
        pressure_data = []

        for i in range(1, len(snapshots)):
            prev = snapshots[i-1]
            curr = snapshots[i]

            if prev.price is not None and curr.price is not None and prev.price > 0 and curr.price > 0:
                pct_change = (curr.price - prev.price) / prev.price * 100
                price_changes.append({
                    'timestamp': curr.timestamp.isoformat(),
                    'price_change_pct': pct_change,
                    'prev_net_pressure': prev.net_pressure,
                    'prev_buy_pressure': prev.buy_pressure,
                    'prev_sell_pressure': prev.sell_pressure,
                })
                pressure_data.append({
                    'net_pressure': prev.net_pressure,
                    'price_change': pct_change,
                })

        # Simple correlation check
        correlation = self._simple_correlation(pressure_data)

        # Pressure regime analysis
        high_buy_moves = [p for p in pressure_data if p['net_pressure'] > 1000]
        high_sell_moves = [p for p in pressure_data if p['net_pressure'] < -1000]
        neutral_moves = [p for p in pressure_data if -1000 <= p['net_pressure'] <= 1000]

        start_price = snapshots[0].price if snapshots[0].price is not None else 0
        end_price = snapshots[-1].price if snapshots[-1].price is not None else 0

        return {
            'data_points': len(price_changes),
            'time_span_hours': (snapshots[-1].timestamp - snapshots[0].timestamp).total_seconds() / 3600,
            'price_summary': {
                'start': start_price,
                'end': end_price,
                'change_pct': (end_price - start_price) / start_price * 100 if start_price > 0 else 0,
            },
            'pressure_correlation': correlation,
            'regime_analysis': {
                'high_buy_pressure': {
                    'count': len(high_buy_moves),
                    'avg_price_change': statistics.mean([p['price_change'] for p in high_buy_moves]) if high_buy_moves else 0,
                },
                'high_sell_pressure': {
                    'count': len(high_sell_moves),
                    'avg_price_change': statistics.mean([p['price_change'] for p in high_sell_moves]) if high_sell_moves else 0,
                },
                'neutral_pressure': {
                    'count': len(neutral_moves),
                    'avg_price_change': statistics.mean([p['price_change'] for p in neutral_moves]) if neutral_moves else 0,
                },
            },
            'recent_pressure': [
                {
                    'time': snapshots[i].timestamp.strftime('%H:%M'),
                    'price': snapshots[i].price if snapshots[i].price is not None else 0,
                    'net_pressure': snapshots[i].net_pressure,
                    'buy': snapshots[i].buy_pressure,
                    'sell': snapshots[i].sell_pressure,
                }
                for i in range(-min(10, len(snapshots)), 0)
            ]
        }

    def _simple_correlation(self, data: list[dict]) -> dict:
        """
        Calculate simple correlation between net pressure and price change.
        Positive = pressure predicts price direction
        """
        if len(data) < 5:
            return {'note': 'Not enough data for correlation'}

        # Count directional agreement
        agreements = 0
        for d in data:
            pressure_direction = 1 if d['net_pressure'] > 0 else (-1 if d['net_pressure'] < 0 else 0)
            price_direction = 1 if d['price_change'] > 0 else (-1 if d['price_change'] < 0 else 0)
            if pressure_direction == price_direction and pressure_direction != 0:
                agreements += 1

        non_zero = [d for d in data if d['net_pressure'] != 0]
        agreement_rate = agreements / len(non_zero) * 100 if non_zero else 0

        return {
            'directional_agreement_pct': agreement_rate,
            'sample_size': len(non_zero),
            'interpretation': 'Pressure aligns with price direction' if agreement_rate > 55 else 'Weak or no correlation' if agreement_rate > 45 else 'Inverse correlation (contrarian signal?)'
        }

    # =========================================================================
    # WHALE TRACKING
    # =========================================================================

    def whale_activity(self, min_size_usd: float = 10000) -> dict:
        """
        Track whale addresses and their behavior.
        """
        # Get all order events with size info
        new_orders = [e for e in self.order_events if e.event_type == 'new']

        # Group by address
        by_address = defaultdict(list)
        for order in new_orders:
            by_address[order.address].append(order)

        # Analyze each address
        whale_stats = []
        for address, orders in by_address.items():
            total_size = sum(o.size for o in orders)
            buys = [o for o in orders if o.side == 'BUY']
            sells = [o for o in orders if o.side == 'SELL']

            whale_stats.append({
                'address': address[:16] + '...',
                'full_address': address,
                'order_count': len(orders),
                'total_size': total_size,
                'buy_count': len(buys),
                'sell_count': len(sells),
                'buy_size': sum(o.size for o in buys),
                'sell_size': sum(o.size for o in sells),
                'coins_traded': list(set(o.symbol for o in orders)),
                'first_seen': min(o.timestamp for o in orders).isoformat(),
                'last_seen': max(o.timestamp for o in orders).isoformat(),
            })

        # Sort by total size
        whale_stats.sort(key=lambda x: x['total_size'], reverse=True)

        return {
            'unique_addresses': len(by_address),
            'top_traders': whale_stats[:20],
        }

    # =========================================================================
    # REPORTING
    # =========================================================================

    def print_daily_report(self, symbol: Optional[str] = None):
        """Print a formatted daily report"""
        print("\n" + "="*70)
        print("📊 TWAP DAILY ANALYSIS REPORT")
        print("="*70)

        summary = self.daily_summary(symbol)

        for coin, dates in summary.items():
            for date, stats in dates.items():
                print(f"\n💰 {coin} - {date}")
                print("-"*50)

                price = stats['price']
                print(f"Price: ${price['open']:.4f} → ${price['close']:.4f} ({price['change_pct']:+.2f}%)")
                print(f"Range: ${price['low']:.4f} - ${price['high']:.4f}")

                pressure = stats['pressure']
                print(f"\nPressure ($/min avg):")
                print(f"  Buy:  ${pressure['avg_buy']:,.0f}  (max: ${pressure['max_buy']:,.0f})")
                print(f"  Sell: ${pressure['avg_sell']:,.0f}  (max: ${pressure['max_sell']:,.0f})")
                print(f"  Net:  ${pressure['avg_net']:+,.0f}")

                orders = stats['orders']
                print(f"\nOrders:")
                print(f"  New: {orders['new']} | Completed: {orders['completed']} | Canceled: {orders['canceled']}")
                print(f"  Completion rate: {orders['completion_rate']:.1f}%")

                activity = stats['activity']
                print(f"\nActivity:")
                print(f"  Avg active orders: {activity['avg_active_orders']:.1f}")
                print(f"  Avg whale orders: {activity['avg_whale_orders']:.1f}")

    def print_completion_report(self, symbol: Optional[str] = None):
        """Print completion/cancellation pattern report"""
        print("\n" + "="*70)
        print("🔄 COMPLETION & CANCELLATION PATTERNS")
        print("="*70)

        patterns = self.completion_patterns(symbol)

        overview = patterns['overview']
        print(f"\nOverview:")
        print(f"  New orders: {overview['total_new']}")
        print(f"  Completed: {overview['total_completed']}")
        print(f"  Canceled: {overview['total_canceled']}")
        print(f"  Completion rate: {overview['completion_rate']:.1f}%")

        by_side = patterns['by_side']
        print(f"\nBy Side:")
        print(f"  BUY  - Completed: {by_side['buy_completed']} | Canceled: {by_side['buy_canceled']}")
        print(f"  SELL - Completed: {by_side['sell_completed']} | Canceled: {by_side['sell_canceled']}")

        if patterns['completed_stats'].get('count', 0) > 0:
            comp = patterns['completed_stats']
            print(f"\nCompleted Order Stats:")
            print(f"  Avg duration: {comp['duration_minutes']['avg']:.0f} min")
            print(f"  Avg progress at completion: {comp['progress_at_event']['avg']:.1f}%")

        if patterns['canceled_stats'].get('count', 0) > 0:
            canc = patterns['canceled_stats']
            print(f"\nCanceled Order Stats:")
            print(f"  Avg duration: {canc['duration_minutes']['avg']:.0f} min")
            print(f"  Avg progress at cancellation: {canc['progress_at_event']['avg']:.1f}%")

    def print_orderflow_report(self, symbol: str):
        """Print orderflow analysis report"""
        print("\n" + "="*70)
        print(f"📈 ORDERFLOW ANALYSIS: {symbol}")
        print("="*70)

        analysis = self.orderflow_analysis(symbol)

        if 'error' in analysis:
            print(f"  {analysis['error']}")
            return

        price = analysis['price_summary']
        print(f"\nPrice: ${price['start']:.4f} → ${price['end']:.4f} ({price['change_pct']:+.2f}%)")
        print(f"Data points: {analysis['data_points']} over {analysis['time_span_hours']:.1f} hours")

        corr = analysis['pressure_correlation']
        print(f"\nPressure-Price Correlation:")
        print(f"  Directional agreement: {corr.get('directional_agreement_pct', 0):.1f}%")
        print(f"  Sample size: {corr.get('sample_size', 0)}")
        print(f"  Interpretation: {corr.get('interpretation', 'N/A')}")

        regime = analysis['regime_analysis']
        print(f"\nRegime Analysis:")
        print(f"  High buy pressure ({regime['high_buy_pressure']['count']} periods): avg price change {regime['high_buy_pressure']['avg_price_change']:+.3f}%")
        print(f"  High sell pressure ({regime['high_sell_pressure']['count']} periods): avg price change {regime['high_sell_pressure']['avg_price_change']:+.3f}%")
        print(f"  Neutral ({regime['neutral_pressure']['count']} periods): avg price change {regime['neutral_pressure']['avg_price_change']:+.3f}%")

        print(f"\nRecent Pressure (last 10):")
        for p in analysis['recent_pressure']:
            print(f"  {p['time']} | ${p['price']:.4f} | Net: ${p['net_pressure']:+,.0f}/min")


def main():
    """Run analysis on available data"""
    import sys

    # Default data directory - adjust as needed
    data_dir = "./allcoins_json_logs"

    if len(sys.argv) > 1:
        data_dir = sys.argv[1]

    analyzer = TWAPAnalyzer(data_dir)

    # Load all available data
    print(f"Loading data from: {data_dir}")
    analyzer.load_all_coins()

    if not analyzer.snapshots:
        print("No data loaded. Check your data directory path.")
        return

    print(f"\nTotal snapshots loaded: {len(analyzer.snapshots)}")
    print(f"Total order events: {len(analyzer.order_events)}")
    print(f"Coins: {', '.join(sorted(analyzer.coins_loaded))}")

    # Run reports
    analyzer.print_daily_report()
    analyzer.print_completion_report()

    # Orderflow for top coins by activity
    coin_activity = defaultdict(int)
    for s in analyzer.snapshots:
        coin_activity[s.symbol] += s.active_orders

    top_coins = sorted(coin_activity.items(), key=lambda x: x[1], reverse=True)[:5]

    for coin, _ in top_coins:
        analyzer.print_orderflow_report(coin)

    # Whale activity
    print("\n" + "="*70)
    print("🐋 WHALE ACTIVITY")
    print("="*70)

    whales = analyzer.whale_activity()
    print(f"\nUnique addresses: {whales['unique_addresses']}")
    print(f"\nTop 10 traders by volume:")
    for i, trader in enumerate(whales['top_traders'][:10], 1):
        print(f"  {i}. {trader['address']}")
        print(f"     Orders: {trader['order_count']} | Size: {trader['total_size']:,.0f}")
        print(f"     Buy: {trader['buy_count']} ({trader['buy_size']:,.0f}) | Sell: {trader['sell_count']} ({trader['sell_size']:,.0f})")
        print(f"     Coins: {', '.join(trader['coins_traded'][:5])}")


if __name__ == "__main__":
    main()