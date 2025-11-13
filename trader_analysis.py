"""
Comprehensive Trader Analysis Script for Hyperliquid Address Data
Analyzes trader behavior, portfolio composition, and trading patterns
"""

import json
from typing import Dict, List, Tuple
from datetime import datetime
from collections import defaultdict
import statistics


class TraderAnalyzer:
    """Analyzes trader data from Hyperliquid address collection"""

    def __init__(self, data_path: str):
        """Initialize analyzer with JSON data file"""
        with open(data_path, 'r') as f:
            self.data = json.load(f)

        self.collection_info = self.data.get('collection_info', {})
        self.traders = self.data.get('traders', {})

    def get_collection_summary(self) -> Dict:
        """Get overview of the collection"""
        return {
            'total_addresses': self.collection_info.get('total_addresses', 0),
            'created': self.collection_info.get('created', ''),
            'last_updated': self.collection_info.get('last_updated', ''),
            'light_fetches': self.collection_info.get('light_fetches', 0),
            'deep_fetches': self.collection_info.get('deep_fetches', 0)
        }

    def classify_traders_by_size(self) -> Dict[str, List[Dict]]:
        """Classify traders into tiers based on portfolio value"""
        classifications = {
            'whales': [],  # > $100k
            'dolphins': [],  # $10k - $100k
            'fish': [],  # $1k - $10k
            'shrimp': [],  # < $1k
        }

        for address, trader_info in self.traders.items():
            account = trader_info.get('data', {}).get('account', {})
            portfolio_value = account.get('total_portfolio_value', 0)

            trader_summary = {
                'address': address,
                'portfolio_value': portfolio_value,
                'cumulative_volume': trader_info['data'].get('cumulative_volume', 0),
                'leverage_ratio': account.get('leverage_ratio', 0),
                'num_positions': account.get('num_positions', 0)
            }

            if portfolio_value >= 100000:
                classifications['whales'].append(trader_summary)
            elif portfolio_value >= 10000:
                classifications['dolphins'].append(trader_summary)
            elif portfolio_value >= 1000:
                classifications['fish'].append(trader_summary)
            else:
                classifications['shrimp'].append(trader_summary)

        # Sort each tier by portfolio value
        for tier in classifications.values():
            tier.sort(key=lambda x: x['portfolio_value'], reverse=True)

        return classifications

    def analyze_twap_usage(self) -> Dict:
        """Analyze TWAP order usage patterns"""
        twap_traders = []
        heavy_twap_users = []  # traders where TWAP fills > 50% of total fills

        for address, trader_info in self.traders.items():
            data = trader_info.get('data', {})
            twap_count = data.get('twap_fills_count', 0)
            total_fills = data.get('fills_count', 0)

            if twap_count > 0:
                twap_ratio = twap_count / total_fills if total_fills > 0 else 0

                trader_twap = {
                    'address': address,
                    'twap_fills': twap_count,
                    'total_fills': total_fills,
                    'twap_ratio': twap_ratio,
                    'cumulative_volume': data.get('cumulative_volume', 0),
                    'portfolio_value': data.get('account', {}).get('total_portfolio_value', 0)
                }

                twap_traders.append(trader_twap)

                if twap_ratio >= 0.5:
                    heavy_twap_users.append(trader_twap)

        # Sort by TWAP fill count
        twap_traders.sort(key=lambda x: x['twap_fills'], reverse=True)
        heavy_twap_users.sort(key=lambda x: x['twap_fills'], reverse=True)

        return {
            'total_twap_users': len(twap_traders),
            'heavy_twap_users': len(heavy_twap_users),
            'top_twap_traders': twap_traders[:10],
            'heavy_twap_traders': heavy_twap_users[:10]
        }

    def analyze_leverage_patterns(self) -> Dict:
        """Analyze leverage usage across traders"""
        leverage_data = []
        high_leverage_traders = []  # leverage > 2x

        for address, trader_info in self.traders.items():
            account = trader_info.get('data', {}).get('account', {})
            leverage = account.get('leverage_ratio', 0)

            if leverage > 0:
                trader_lev = {
                    'address': address,
                    'leverage': leverage,
                    'portfolio_value': account.get('total_portfolio_value', 0),
                    'margin_used': account.get('margin_used', 0),
                    'position_value': account.get('position_value', 0),
                    'num_positions': account.get('num_positions', 0)
                }

                leverage_data.append(trader_lev)

                if leverage > 2.0:
                    high_leverage_traders.append(trader_lev)

        leverage_values = [t['leverage'] for t in leverage_data]

        stats = {}
        if leverage_values:
            stats = {
                'avg_leverage': statistics.mean(leverage_values),
                'median_leverage': statistics.median(leverage_values),
                'max_leverage': max(leverage_values),
                'min_leverage': min(leverage_values),
                'std_dev': statistics.stdev(leverage_values) if len(leverage_values) > 1 else 0
            }

        high_leverage_traders.sort(key=lambda x: x['leverage'], reverse=True)

        return {
            'stats': stats,
            'total_leveraged_traders': len(leverage_data),
            'high_leverage_traders_count': len(high_leverage_traders),
            'top_leveraged': high_leverage_traders[:10]
        }

    def analyze_positions(self) -> Dict:
        """Analyze open positions across all traders"""
        position_analysis = {
            'long_positions': [],
            'short_positions': [],
            'coins_traded': defaultdict(int),
            'total_long_exposure': 0,
            'total_short_exposure': 0
        }

        for address, trader_info in self.traders.items():
            positions = trader_info.get('data', {}).get('positions', [])

            for pos in positions:
                coin = pos.get('coin', '')
                size = abs(pos.get('size', 0))
                side = pos.get('side', '')

                position_analysis['coins_traded'][coin] += 1

                pos_detail = {
                    'address': address,
                    'coin': coin,
                    'size': size,
                    'entry_price': pos.get('entry_price', 0),
                    'unrealized_pnl': pos.get('unrealized_pnl', 0),
                    'leverage': pos.get('leverage', 0),
                    'margin_used': pos.get('margin_used', 0)
                }

                if side == 'LONG':
                    position_analysis['long_positions'].append(pos_detail)
                    position_analysis['total_long_exposure'] += pos.get('margin_used', 0)
                else:
                    position_analysis['short_positions'].append(pos_detail)
                    position_analysis['total_short_exposure'] += pos.get('margin_used', 0)

        return position_analysis

    def analyze_spot_holdings(self) -> Dict:
        """Analyze spot token holdings"""
        spot_holdings = defaultdict(lambda: {'total_value': 0, 'holders': 0, 'total_amount': 0})

        for address, trader_info in self.traders.items():
            account = trader_info.get('data', {}).get('account', {})
            spot_balances = account.get('spot_balances_detail', [])

            for balance in spot_balances:
                coin = balance.get('coin', '')
                value = balance.get('value', 0)
                amount = balance.get('amount', 0)

                spot_holdings[coin]['total_value'] += value
                spot_holdings[coin]['holders'] += 1
                spot_holdings[coin]['total_amount'] += amount

        # Convert to sorted list
        holdings_list = [
            {
                'coin': coin,
                'total_value': data['total_value'],
                'holders': data['holders'],
                'total_amount': data['total_amount']
            }
            for coin, data in spot_holdings.items()
        ]

        holdings_list.sort(key=lambda x: x['total_value'], reverse=True)

        return {
            'unique_tokens': len(holdings_list),
            'holdings': holdings_list
        }

    def analyze_account_types(self) -> Dict:
        """Analyze distribution of account types"""
        types = {
            'user': 0,
            'subAccount': 0,
            'vault': 0
        }

        subaccount_masters = defaultdict(int)

        for address, trader_info in self.traders.items():
            role_data = trader_info.get('data', {}).get('user_role', {})
            role = role_data.get('role', 'user')

            types[role] = types.get(role, 0) + 1

            if role == 'subAccount':
                master = role_data.get('data', {}).get('master', '')
                if master:
                    subaccount_masters[master] += 1

        # Find masters with most subaccounts
        top_masters = sorted(
            subaccount_masters.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]

        return {
            'distribution': types,
            'top_masters': [
                {'master_address': addr, 'subaccount_count': count}
                for addr, count in top_masters
            ]
        }

    def analyze_trading_activity(self) -> Dict:
        """Analyze trading activity metrics"""
        activity_data = []

        for address, trader_info in self.traders.items():
            data = trader_info.get('data', {})

            activity = {
                'address': address,
                'cumulative_volume': data.get('cumulative_volume', 0),
                'fills_count': data.get('fills_count', 0),
                'twap_fills_count': data.get('twap_fills_count', 0),
                'portfolio_value': data.get('account', {}).get('total_portfolio_value', 0),
                'open_orders': data.get('open_orders_count', 0)
            }

            # Calculate volume to portfolio ratio
            if activity['portfolio_value'] > 0:
                activity['volume_to_portfolio_ratio'] = (
                        activity['cumulative_volume'] / activity['portfolio_value']
                )
            else:
                activity['volume_to_portfolio_ratio'] = 0

            activity_data.append(activity)

        # Sort by volume
        activity_data.sort(key=lambda x: x['cumulative_volume'], reverse=True)

        # Calculate stats
        volumes = [a['cumulative_volume'] for a in activity_data if a['cumulative_volume'] > 0]

        stats = {}
        if volumes:
            stats = {
                'total_volume': sum(volumes),
                'avg_volume': statistics.mean(volumes),
                'median_volume': statistics.median(volumes),
                'max_volume': max(volumes),
            }

        return {
            'stats': stats,
            'top_by_volume': activity_data[:20],
            'high_turnover': sorted(
                [a for a in activity_data if a['volume_to_portfolio_ratio'] > 10],
                key=lambda x: x['volume_to_portfolio_ratio'],
                reverse=True
            )[:10]
        }

    def generate_full_report(self) -> str:
        """Generate a comprehensive analysis report"""
        report_lines = []

        # Header
        report_lines.append("=" * 80)
        report_lines.append("HYPERLIQUID TRADER ANALYSIS REPORT")
        report_lines.append("=" * 80)
        report_lines.append("")

        # Collection Info
        summary = self.get_collection_summary()
        report_lines.append("COLLECTION SUMMARY")
        report_lines.append("-" * 80)
        report_lines.append(f"Total Addresses: {summary['total_addresses']}")
        report_lines.append(f"Created: {summary['created']}")
        report_lines.append(f"Last Updated: {summary['last_updated']}")
        report_lines.append(f"Light Fetches: {summary['light_fetches']}")
        report_lines.append(f"Deep Fetches: {summary['deep_fetches']}")
        report_lines.append("")

        # Trader Classification
        classifications = self.classify_traders_by_size()
        report_lines.append("TRADER CLASSIFICATION BY PORTFOLIO SIZE")
        report_lines.append("-" * 80)
        for tier, traders in classifications.items():
            total_value = sum(t['portfolio_value'] for t in traders)
            report_lines.append(f"{tier.upper()}: {len(traders)} traders (${total_value:,.2f} total)")
        report_lines.append("")

        # Top Whales
        if classifications['whales']:
            report_lines.append("TOP 10 WHALES:")
            for i, whale in enumerate(classifications['whales'][:10], 1):
                report_lines.append(
                    f"{i}. {whale['address'][:10]}... | "
                    f"Portfolio: ${whale['portfolio_value']:,.2f} | "
                    f"Volume: ${whale['cumulative_volume']:,.2f} | "
                    f"Leverage: {whale['leverage_ratio']:.2f}x"
                )
            report_lines.append("")

        # TWAP Analysis
        twap_analysis = self.analyze_twap_usage()
        report_lines.append("TWAP USAGE ANALYSIS")
        report_lines.append("-" * 80)
        report_lines.append(f"Total TWAP Users: {twap_analysis['total_twap_users']}")
        report_lines.append(f"Heavy TWAP Users (>50% fills): {twap_analysis['heavy_twap_users']}")
        report_lines.append("")
        report_lines.append("TOP 5 TWAP TRADERS:")
        for i, trader in enumerate(twap_analysis['top_twap_traders'][:5], 1):
            report_lines.append(
                f"{i}. {trader['address'][:10]}... | "
                f"TWAP Fills: {trader['twap_fills']} ({trader['twap_ratio']:.1%}) | "
                f"Portfolio: ${trader['portfolio_value']:,.2f}"
            )
        report_lines.append("")

        # Leverage Analysis
        leverage_analysis = self.analyze_leverage_patterns()
        if leverage_analysis['stats']:
            report_lines.append("LEVERAGE ANALYSIS")
            report_lines.append("-" * 80)
            stats = leverage_analysis['stats']
            report_lines.append(f"Average Leverage: {stats['avg_leverage']:.2f}x")
            report_lines.append(f"Median Leverage: {stats['median_leverage']:.2f}x")
            report_lines.append(f"Max Leverage: {stats['max_leverage']:.2f}x")
            report_lines.append(f"High Leverage Traders (>2x): {leverage_analysis['high_leverage_traders_count']}")
            report_lines.append("")

        # Position Analysis
        positions = self.analyze_positions()
        report_lines.append("POSITION ANALYSIS")
        report_lines.append("-" * 80)
        report_lines.append(f"Long Positions: {len(positions['long_positions'])}")
        report_lines.append(f"Short Positions: {len(positions['short_positions'])}")
        report_lines.append(f"Total Long Exposure: ${positions['total_long_exposure']:,.2f}")
        report_lines.append(f"Total Short Exposure: ${positions['total_short_exposure']:,.2f}")
        report_lines.append("")
        report_lines.append("Most Traded Coins:")
        for coin, count in sorted(positions['coins_traded'].items(), key=lambda x: x[1], reverse=True)[:10]:
            report_lines.append(f"  {coin}: {count} positions")
        report_lines.append("")

        # Spot Holdings
        spot_analysis = self.analyze_spot_holdings()
        report_lines.append("SPOT HOLDINGS ANALYSIS")
        report_lines.append("-" * 80)
        report_lines.append(f"Unique Tokens: {spot_analysis['unique_tokens']}")
        report_lines.append("")
        report_lines.append("TOP 10 TOKENS BY VALUE:")
        for i, holding in enumerate(spot_analysis['holdings'][:10], 1):
            report_lines.append(
                f"{i}. {holding['coin']}: ${holding['total_value']:,.2f} "
                f"({holding['holders']} holders)"
            )
        report_lines.append("")

        # Account Types
        account_types = self.analyze_account_types()
        report_lines.append("ACCOUNT TYPE DISTRIBUTION")
        report_lines.append("-" * 80)
        for acc_type, count in account_types['distribution'].items():
            report_lines.append(f"{acc_type}: {count}")
        report_lines.append("")

        # Trading Activity
        activity = self.analyze_trading_activity()
        if activity['stats']:
            report_lines.append("TRADING ACTIVITY")
            report_lines.append("-" * 80)
            stats = activity['stats']
            report_lines.append(f"Total Volume: ${stats['total_volume']:,.2f}")
            report_lines.append(f"Average Volume per Trader: ${stats['avg_volume']:,.2f}")
            report_lines.append(f"Median Volume: ${stats['median_volume']:,.2f}")
            report_lines.append("")

        report_lines.append("=" * 80)
        report_lines.append("END OF REPORT")
        report_lines.append("=" * 80)

        return "\n".join(report_lines)

    def export_summary_to_json(self, output_path: str) -> None:
        """Export analysis summary to JSON"""
        summary = {
            'collection_info': self.get_collection_summary(),
            'trader_classifications': self.classify_traders_by_size(),
            'twap_analysis': self.analyze_twap_usage(),
            'leverage_analysis': self.analyze_leverage_patterns(),
            'position_analysis': self.analyze_positions(),
            'spot_holdings': self.analyze_spot_holdings(),
            'account_types': self.analyze_account_types(),
            'trading_activity': self.analyze_trading_activity()
        }

        with open(output_path, 'w') as f:
            json.dump(summary, f, indent=2)

        print(f"Analysis exported to {output_path}")


def main():
    """Main execution function"""
    # Initialize analyzer
    analyzer = TraderAnalyzer('trader_metrics.json')

    # Generate and print full report
    report = analyzer.generate_full_report()
    print(report)

    # Export detailed analysis to JSON


    # Optional: Access specific analyses programmatically
    # whales = analyzer.classify_traders_by_size()['whales']
    # twap_users = analyzer.analyze_twap_usage()
    # etc...


if __name__ == "__main__":
    main()