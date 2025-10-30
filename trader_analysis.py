#!/usr/bin/env python3
"""
Trader Analysis Script
Analyzes trader_metrics.json to provide insights
"""
import json
from pathlib import Path
from typing import Dict, List
from datetime import datetime
from collections import defaultdict


class TraderAnalyzer:
    """Analyze trader metrics data"""

    def __init__(self, metrics_file: str = 'trader_metrics.json'):
        self.metrics_file = Path(metrics_file)
        self.data = self._load_data()
        self.traders = self.data.get('traders', {})

    def _load_data(self) -> Dict:
        """Load metrics data"""
        if not self.metrics_file.exists():
            print(f"❌ {self.metrics_file} not found!")
            return {}

        with open(self.metrics_file, 'r') as f:
            return json.load(f)

    def get_trader_list(self) -> List[Dict]:
        """Get list of all traders with their data"""
        traders = []
        for address, trader_data in self.traders.items():
            data = trader_data.get('data', {})
            account = data.get('account', {})

            traders.append({
                'address': address,
                'perps_value': account.get('value', 0),
                'spot_value': account.get('spot_value', 0),
                'total_portfolio': account.get('total_portfolio_value', 0),
                'position_value': account.get('position_value', 0),
                'leverage_ratio': account.get('leverage_ratio', 0),
                'num_positions': account.get('num_positions', 0),
                'cumulative_volume': data.get('cumulative_volume', 0),
                'open_orders': data.get('open_orders_count', 0),
                'withdrawable': account.get('withdrawable', 0),
                'margin_used': account.get('margin_used', 0),
                'first_seen': trader_data.get('first_seen'),
                'positions': data.get('positions', [])
            })

        return traders

    # =========================================================================
    # TOP TRADERS ANALYSES
    # =========================================================================

    def top_by_portfolio(self, n: int = 10) -> List[Dict]:
        """Top N traders by total portfolio value"""
        traders = self.get_trader_list()
        sorted_traders = sorted(traders, key=lambda x: x['total_portfolio'], reverse=True)
        return sorted_traders[:n]

    def top_by_volume(self, n: int = 10) -> List[Dict]:
        """Top N traders by cumulative trading volume"""
        traders = self.get_trader_list()
        sorted_traders = sorted(traders, key=lambda x: x['cumulative_volume'], reverse=True)
        return sorted_traders[:n]

    def top_by_spot(self, n: int = 10) -> List[Dict]:
        """Top N traders by spot wallet value"""
        traders = self.get_trader_list()
        sorted_traders = sorted(traders, key=lambda x: x['spot_value'], reverse=True)
        return sorted_traders[:n]

    def most_leveraged(self, n: int = 10) -> List[Dict]:
        """Top N most leveraged traders"""
        traders = self.get_trader_list()
        # Filter out those with no positions
        with_positions = [t for t in traders if t['position_value'] > 0]
        sorted_traders = sorted(with_positions, key=lambda x: x['leverage_ratio'], reverse=True)
        return sorted_traders[:n]

    # =========================================================================
    # PORTFOLIO COMPOSITION ANALYSES
    # =========================================================================

    def spot_vs_perps_distribution(self) -> Dict:
        """Analyze how traders split between spot and perps"""
        traders = self.get_trader_list()

        categories = {
            'all_spot': [],  # 100% spot, 0% perps
            'mostly_spot': [],  # >75% spot
            'balanced': [],  # 25-75% spot
            'mostly_perps': [],  # <25% spot
            'all_perps': []  # 0% spot
        }

        for trader in traders:
            total = trader['total_portfolio']
            if total == 0:
                continue

            spot_pct = (trader['spot_value'] / total) * 100

            if spot_pct == 100:
                categories['all_spot'].append(trader)
            elif spot_pct > 75:
                categories['mostly_spot'].append(trader)
            elif spot_pct >= 25:
                categories['balanced'].append(trader)
            elif spot_pct > 0:
                categories['mostly_perps'].append(trader)
            else:
                categories['all_perps'].append(trader)

        return categories

    def portfolio_allocation_stats(self) -> Dict:
        """Get statistics on portfolio allocation"""
        traders = self.get_trader_list()

        total_perps = sum(t['perps_value'] for t in traders)
        total_spot = sum(t['spot_value'] for t in traders)
        total_all = total_perps + total_spot

        return {
            'total_capital': total_all,
            'total_perps': total_perps,
            'total_spot': total_spot,
            'perps_percentage': (total_perps / total_all * 100) if total_all > 0 else 0,
            'spot_percentage': (total_spot / total_all * 100) if total_all > 0 else 0,
            'avg_portfolio': total_all / len(traders) if traders else 0,
            'avg_perps': total_perps / len(traders) if traders else 0,
            'avg_spot': total_spot / len(traders) if traders else 0
        }

    # =========================================================================
    # TRADING ACTIVITY ANALYSES
    # =========================================================================

    def active_vs_passive(self) -> Dict:
        """Categorize traders by activity level"""
        traders = self.get_trader_list()

        categories = {
            'no_activity': [],  # No volume, no positions
            'hodlers': [],  # Has portfolio but no positions
            'inactive': [],  # Low volume (<$100k)
            'moderate': [],  # $100k - $1M volume
            'active': [],  # $1M - $10M volume
            'very_active': [],  # $10M - $100M volume
            'whales': []  # >$100M volume
        }

        for trader in traders:
            vol = trader['cumulative_volume']
            positions = trader['num_positions']

            if vol == 0 and positions == 0:
                categories['no_activity'].append(trader)
            elif positions == 0 and vol > 0:
                categories['hodlers'].append(trader)
            elif vol < 100_000:
                categories['inactive'].append(trader)
            elif vol < 1_000_000:
                categories['moderate'].append(trader)
            elif vol < 10_000_000:
                categories['active'].append(trader)
            elif vol < 100_000_000:
                categories['very_active'].append(trader)
            else:
                categories['whales'].append(trader)

        return categories

    def volume_to_portfolio_ratio(self) -> List[Dict]:
        """Traders with highest volume relative to portfolio size"""
        traders = self.get_trader_list()

        ratios = []
        for trader in traders:
            if trader['total_portfolio'] > 0:
                ratio = trader['cumulative_volume'] / trader['total_portfolio']
                ratios.append({
                    **trader,
                    'volume_to_portfolio_ratio': ratio
                })

        return sorted(ratios, key=lambda x: x['volume_to_portfolio_ratio'], reverse=True)

    # =========================================================================
    # RISK ANALYSES
    # =========================================================================

    def risk_profile(self) -> Dict:
        """Categorize traders by risk level"""
        traders = self.get_trader_list()

        categories = {
            'no_risk': [],  # No positions
            'ultra_safe': [],  # <0.1x leverage
            'conservative': [],  # 0.1-0.5x leverage
            'moderate': [],  # 0.5-2x leverage
            'aggressive': [],  # 2-5x leverage
            'very_aggressive': [],  # 5-10x leverage
            'extreme': []  # >10x leverage
        }

        for trader in traders:
            lev = trader['leverage_ratio']

            if trader['num_positions'] == 0:
                categories['no_risk'].append(trader)
            elif lev < 0.1:
                categories['ultra_safe'].append(trader)
            elif lev < 0.5:
                categories['conservative'].append(trader)
            elif lev < 2:
                categories['moderate'].append(trader)
            elif lev < 5:
                categories['aggressive'].append(trader)
            elif lev < 10:
                categories['very_aggressive'].append(trader)
            else:
                categories['extreme'].append(trader)

        return categories

    def liquidation_risk_analysis(self) -> List[Dict]:
        """Find traders with positions near liquidation"""
        traders = self.get_trader_list()

        at_risk = []
        for trader in traders:
            for position in trader['positions']:
                liq_price = position.get('liquidation_price', 0)
                entry_price = position.get('entry_price', 0)

                if liq_price > 0 and entry_price > 0:
                    # Calculate distance to liquidation
                    if position['side'] == 'LONG':
                        distance = ((entry_price - liq_price) / entry_price) * 100
                    else:  # SHORT
                        distance = ((liq_price - entry_price) / entry_price) * 100

                    if distance < 30:  # Within 30% of liquidation
                        at_risk.append({
                            'address': trader['address'],
                            'coin': position['coin'],
                            'side': position['side'],
                            'size': position['size'],
                            'entry_price': entry_price,
                            'liquidation_price': liq_price,
                            'distance_pct': distance,
                            'total_portfolio': trader['total_portfolio']
                        })

        return sorted(at_risk, key=lambda x: x['distance_pct'])

    # =========================================================================
    # POSITION ANALYSES
    # =========================================================================

    def position_distribution(self) -> Dict:
        """What coins are traders holding?"""
        coin_stats = defaultdict(lambda: {
            'total_traders': 0,
            'total_long_size': 0,
            'total_short_size': 0,
            'avg_leverage': [],
            'total_margin': 0
        })

        traders = self.get_trader_list()

        for trader in traders:
            for position in trader['positions']:
                coin = position['coin']
                size = position['size']

                coin_stats[coin]['total_traders'] += 1
                coin_stats[coin]['total_margin'] += position.get('margin_used', 0)
                coin_stats[coin]['avg_leverage'].append(position.get('leverage', 1))

                if size > 0:  # LONG
                    coin_stats[coin]['total_long_size'] += size
                else:  # SHORT
                    coin_stats[coin]['total_short_size'] += abs(size)

        # Calculate averages
        for coin in coin_stats:
            leverages = coin_stats[coin]['avg_leverage']
            coin_stats[coin]['avg_leverage'] = sum(leverages) / len(leverages) if leverages else 0

        return dict(coin_stats)

    def most_popular_coins(self, n: int = 10) -> List[tuple]:
        """Most traded coins by number of traders"""
        dist = self.position_distribution()
        sorted_coins = sorted(dist.items(), key=lambda x: x[1]['total_traders'], reverse=True)
        return sorted_coins[:n]

    # =========================================================================
    # SUMMARY STATISTICS
    # =========================================================================

    def overall_summary(self) -> Dict:
        """Get overall market summary"""
        traders = self.get_trader_list()

        if not traders:
            return {}

        total_portfolio = sum(t['total_portfolio'] for t in traders)
        total_volume = sum(t['cumulative_volume'] for t in traders)
        total_positions = sum(t['num_positions'] for t in traders)
        with_positions = len([t for t in traders if t['num_positions'] > 0])

        return {
            'total_traders': len(traders),
            'traders_with_positions': with_positions,
            'total_portfolio_value': total_portfolio,
            'total_cumulative_volume': total_volume,
            'total_open_positions': total_positions,
            'avg_portfolio': total_portfolio / len(traders),
            'avg_volume': total_volume / len(traders),
            'median_portfolio': sorted([t['total_portfolio'] for t in traders])[len(traders) // 2],
            'largest_portfolio': max(t['total_portfolio'] for t in traders),
            'smallest_portfolio': min(t['total_portfolio'] for t in traders if t['total_portfolio'] > 0),
        }


# =============================================================================
# DISPLAY FUNCTIONS
# =============================================================================

def print_top_traders(analyzer: TraderAnalyzer):
    """Print top traders by various metrics"""
    print("\n" + "=" * 70)
    print("TOP 10 TRADERS BY TOTAL PORTFOLIO")
    print("=" * 70)

    top = analyzer.top_by_portfolio(10)
    for i, trader in enumerate(top, 1):
        addr = f"{trader['address']}"
        total = trader['total_portfolio']
        perps = trader['perps_value']
        spot = trader['spot_value']
        spot_pct = (spot / total * 100) if total > 0 else 0

        print(f"{i:2d}. {addr} - ${total:,.0f}")
        print(f"    Perps: ${perps:,.0f} | Spot: ${spot:,.0f} ({spot_pct:.0f}%)")
        print(f"    Volume: ${trader['cumulative_volume']:,.0f} | Positions: {trader['num_positions']}")
        print()


def print_volume_leaders(analyzer: TraderAnalyzer):
    """Print top traders by volume"""
    print("\n" + "=" * 70)
    print("TOP 10 TRADERS BY CUMULATIVE VOLUME")
    print("=" * 70)

    top = analyzer.top_by_volume(10)
    for i, trader in enumerate(top, 1):
        addr = f"{trader['address']}"
        vol = trader['cumulative_volume']
        portfolio = trader['total_portfolio']
        ratio = vol / portfolio if portfolio > 0 else 0

        print(f"{i:2d}. {addr} - ${vol:,.0f}")
        print(f"    Portfolio: ${portfolio:,.0f} | Ratio: {ratio:.1f}x")
        print()


def print_spot_holders(analyzer: TraderAnalyzer):
    """Print top spot wallet holders"""
    print("\n" + "=" * 70)
    print("TOP 10 SPOT WALLET HOLDERS")
    print("=" * 70)

    top = analyzer.top_by_spot(10)
    for i, trader in enumerate(top, 1):
        addr = f"{trader['address']}"
        spot = trader['spot_value']
        total = trader['total_portfolio']
        spot_pct = (spot / total * 100) if total > 0 else 0

        print(f"{i:2d}. {addr} - ${spot:,.0f} ({spot_pct:.0f}% of portfolio)")
        print(f"    Total Portfolio: ${total:,.0f}")
        print()


def print_leveraged_traders(analyzer: TraderAnalyzer):
    """Print most leveraged traders"""
    print("\n" + "=" * 70)
    print("TOP 10 MOST LEVERAGED TRADERS")
    print("=" * 70)

    top = analyzer.most_leveraged(10)
    for i, trader in enumerate(top, 1):
        addr = f"{trader['address']}"
        lev = trader['leverage_ratio']
        pos_val = trader['position_value']
        portfolio = trader['total_portfolio']

        print(f"{i:2d}. {addr} - {lev:.2f}x leverage")
        print(f"    Position: ${pos_val:,.0f} | Portfolio: ${portfolio:,.0f}")
        print(f"    Positions: {trader['num_positions']}")
        print()


def print_portfolio_allocation(analyzer: TraderAnalyzer):
    """Print portfolio allocation statistics"""
    print("\n" + "=" * 70)
    print("PORTFOLIO ALLOCATION STATISTICS")
    print("=" * 70)

    stats = analyzer.portfolio_allocation_stats()

    print(f"Total Capital Tracked: ${stats['total_capital']:,.0f}")
    print(f"  In Perps Accounts: ${stats['total_perps']:,.0f} ({stats['perps_percentage']:.1f}%)")
    print(f"  In Spot Wallets:   ${stats['total_spot']:,.0f} ({stats['spot_percentage']:.1f}%)")
    print()
    print(f"Average Portfolio: ${stats['avg_portfolio']:,.0f}")
    print(f"  Avg Perps: ${stats['avg_perps']:,.0f}")
    print(f"  Avg Spot:  ${stats['avg_spot']:,.0f}")

    # Distribution
    print("\n" + "-" * 70)
    print("SPOT VS PERPS DISTRIBUTION")
    print("-" * 70)

    dist = analyzer.spot_vs_perps_distribution()

    for category, traders in dist.items():
        print(f"{category.replace('_', ' ').title():20s}: {len(traders):3d} traders")


def print_activity_levels(analyzer: TraderAnalyzer):
    """Print trader activity levels"""
    print("\n" + "=" * 70)
    print("TRADER ACTIVITY LEVELS")
    print("=" * 70)

    categories = analyzer.active_vs_passive()

    for category, traders in categories.items():
        total_vol = sum(t['cumulative_volume'] for t in traders)
        print(f"{category.replace('_', ' ').title():20s}: {len(traders):3d} traders | ${total_vol:,.0f} volume")


def print_risk_profiles(analyzer: TraderAnalyzer):
    """Print risk profile distribution"""
    print("\n" + "=" * 70)
    print("RISK PROFILE DISTRIBUTION")
    print("=" * 70)

    risk = analyzer.risk_profile()

    for category, traders in risk.items():
        total_capital = sum(t['total_portfolio'] for t in traders)
        print(f"{category.replace('_', ' ').title():20s}: {len(traders):3d} traders | ${total_capital:,.0f}")


def print_popular_coins(analyzer: TraderAnalyzer):
    """Print most popular trading coins"""
    print("\n" + "=" * 70)
    print("MOST POPULAR TRADING PAIRS")
    print("=" * 70)

    top_coins = analyzer.most_popular_coins(15)

    for i, (coin, stats) in enumerate(top_coins, 1):
        print(f"{i:2d}. {coin:8s} - {stats['total_traders']:2d} traders")
        print(f"    Avg Leverage: {stats['avg_leverage']:.1f}x")
        print(f"    Total Margin: ${stats['total_margin']:,.0f}")
        print()


def print_overall_summary(analyzer: TraderAnalyzer):
    """Print overall market summary"""
    print("\n" + "=" * 70)
    print("OVERALL MARKET SUMMARY")
    print("=" * 70)

    summary = analyzer.overall_summary()

    print(f"Total Traders Tracked: {summary['total_traders']}")
    print(f"Traders with Positions: {summary['traders_with_positions']}")
    print(f"Total Portfolio Value: ${summary['total_portfolio_value']:,.0f}")
    print(f"Total Cumulative Volume: ${summary['total_cumulative_volume']:,.0f}")
    print(f"Total Open Positions: {summary['total_open_positions']}")
    print()
    print(f"Average Portfolio: ${summary['avg_portfolio']:,.0f}")
    print(f"Median Portfolio: ${summary['median_portfolio']:,.0f}")
    print(f"Largest Portfolio: ${summary['largest_portfolio']:,.0f}")
    print(f"Smallest Portfolio: ${summary['smallest_portfolio']:,.0f}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Run all analyses"""
    print("\n" + "=" * 70)
    print("TRADER METRICS ANALYSIS")
    print("=" * 70)

    analyzer = TraderAnalyzer()

    if not analyzer.traders:
        print("No trader data found!")
        return

    # Run all analyses
    print_overall_summary(analyzer)
    print_top_traders(analyzer)
    print_volume_leaders(analyzer)
    print_spot_holders(analyzer)
    print_leveraged_traders(analyzer)
    print_portfolio_allocation(analyzer)
    print_activity_levels(analyzer)
    print_risk_profiles(analyzer)
    print_popular_coins(analyzer)

    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()