#!/usr/bin/env python3
"""
Hyperliquid TWAP Whale Intelligence System
===========================================
Professional-grade analysis framework for whale trading patterns.

Architecture:
    - DataConnector: Database abstraction layer
    - AnalysisEngine: Core analysis logic
    - WhaleProfiler: Individual whale deep-dive
    - MarketSentiment: Aggregate market analysis
    - TimeSeriesAnalyzer: Temporal pattern detection
    - ReportGenerator: Multi-format output
    - Visualizer: Chart generation
    - CacheManager: Performance optimization

Author: Paul De Beuckelaer
Date: December 2025
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict
import json
import logging
from functools import lru_cache
import warnings

warnings.filterwarnings('ignore')


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class AnalysisConfig:
    """Configuration for analysis framework"""
    db_path: Path = Path('data/twap.db')
    output_dir: Path = Path('analysis/reports')
    cache_enabled: bool = True
    min_whale_orders: int = 50  # Minimum orders to qualify as "whale"
    lookback_days: int = 30
    top_n_whales: int = 20

    def __post_init__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class WhaleProfile:
    """Complete profile of a whale trader"""
    address: str
    display_address: str
    total_orders: int
    buy_orders: int
    sell_orders: int
    coins_traded: List[str]
    total_volume: float
    first_seen: datetime
    last_seen: datetime
    avg_order_size: float
    favorite_coin: str
    strategy_type: str  # 'accumulator', 'distributor', 'balanced', 'swing_trader'
    activity_score: float
    success_rate: float

    @property
    def buy_ratio(self) -> float:
        """Percentage of buy orders"""
        return (self.buy_orders / self.total_orders * 100) if self.total_orders > 0 else 0

    @property
    def sell_ratio(self) -> float:
        """Percentage of sell orders"""
        return (self.sell_orders / self.total_orders * 100) if self.total_orders > 0 else 0

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            'address': self.address,
            'display_address': self.display_address,
            'total_orders': self.total_orders,
            'buy_orders': self.buy_orders,
            'sell_orders': self.sell_orders,
            'buy_ratio': round(self.buy_ratio, 2),
            'sell_ratio': round(self.sell_ratio, 2),
            'coins_traded': len(self.coins_traded),
            'favorite_coin': self.favorite_coin,
            'strategy_type': self.strategy_type,
            'activity_score': round(self.activity_score, 2),
            'success_rate': round(self.success_rate, 2)
        }


@dataclass
class CoinSentiment:
    """Market sentiment for a specific coin"""
    symbol: str
    total_orders: int
    buy_orders: int
    sell_orders: int
    net_pressure: float
    unique_whales: int
    avg_order_size: float
    sentiment_score: float  # -100 to +100
    trend: str  # 'strong_buy', 'buy', 'neutral', 'sell', 'strong_sell'


@dataclass
class MarketSnapshot:
    """Aggregate market state at a point in time"""
    timestamp: datetime
    active_whales: int
    total_buy_pressure: float
    total_sell_pressure: float
    net_flow: float
    top_coins: List[str]
    market_sentiment: str


# ============================================================================
# DATABASE CONNECTION LAYER
# ============================================================================

class DataConnector:
    """
    Abstraction layer for database operations.
    Handles connection pooling, query optimization, and caching.
    """

    def __init__(self, db_path: Path, cache_enabled: bool = True):
        self.db_path = db_path
        self.cache_enabled = cache_enabled
        self.logger = logging.getLogger(__name__)
        self._validate_database()

    def _validate_database(self):
        """Ensure database exists and has required tables"""
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        required_tables = ['orders', 'snapshots', 'events', 'addresses']
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}

        missing = set(required_tables) - existing_tables
        if missing:
            self.logger.warning(f"Missing tables: {missing}")

        conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @lru_cache(maxsize=32)
    def get_date_range(self) -> Tuple[datetime, datetime]:
        """Get the date range of available data"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                MIN(first_seen_at) as earliest,
                MAX(last_seen_at) as latest
            FROM orders
        """)

        row = cursor.fetchone()
        conn.close()

        earliest = datetime.fromisoformat(row['earliest']) if row['earliest'] else datetime.now()
        latest = datetime.fromisoformat(row['latest']) if row['latest'] else datetime.now()

        return earliest, latest

    def execute_query(self, query: str, params: Tuple = ()) -> pd.DataFrame:
        """
        Execute SQL query and return results as DataFrame.
        Includes query optimization and error handling.
        """
        try:
            conn = self._get_connection()
            df = pd.read_sql_query(query, conn, params=params)
            conn.close()
            return df
        except Exception as e:
            self.logger.error(f"Query execution failed: {e}")
            self.logger.debug(f"Query: {query}")
            raise

    def get_whale_data(self, min_orders: int = 50,
                       start_date: Optional[str] = None,
                       end_date: Optional[str] = None) -> pd.DataFrame:
        """Get comprehensive whale trading data"""
        query = """
            SELECT 
                address,
                symbol,
                side,
                size,
                product_type,
                status,
                duration_minutes,
                first_seen_at,
                last_seen_at,
                completed_at,
                canceled_at,
                final_progress_percent
            FROM orders
            WHERE address IN (
                SELECT address 
                FROM orders 
                GROUP BY address 
                HAVING COUNT(*) >= ?
            )
        """

        params = [min_orders]

        if start_date:
            query += " AND DATE(first_seen_at) >= ?"
            params.append(start_date)

        if end_date:
            query += " AND DATE(first_seen_at) <= ?"
            params.append(end_date)

        query += " ORDER BY first_seen_at DESC"

        return self.execute_query(query, tuple(params))

    def get_coin_data(self, symbol: str,
                      start_date: Optional[str] = None) -> pd.DataFrame:
        """Get all orders for a specific coin"""
        query = """
            SELECT * FROM orders 
            WHERE symbol = ?
        """

        params = [symbol]

        if start_date:
            query += " AND DATE(first_seen_at) >= ?"
            params.append(start_date)

        query += " ORDER BY first_seen_at DESC"

        return self.execute_query(query, tuple(params))

    def get_market_summary(self) -> Dict[str, Any]:
        """Get overall market statistics"""
        query = """
            SELECT 
                COUNT(*) as total_orders,
                COUNT(DISTINCT address) as unique_addresses,
                COUNT(DISTINCT symbol) as unique_coins,
                SUM(CASE WHEN side = 'BUY' THEN 1 ELSE 0 END) as total_buys,
                SUM(CASE WHEN side = 'SELL' THEN 1 ELSE 0 END) as total_sells,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) as canceled,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                MIN(first_seen_at) as earliest_order,
                MAX(last_seen_at) as latest_order
            FROM orders
        """

        df = self.execute_query(query)
        return df.to_dict('records')[0] if not df.empty else {}


# ============================================================================
# WHALE PROFILER
# ============================================================================

class WhaleProfiler:
    """
    Deep analysis of individual whale behavior.
    Generates comprehensive profiles with trading patterns and strategies.
    """

    def __init__(self, connector: DataConnector):
        self.connector = connector
        self.logger = logging.getLogger(__name__)

    def profile_whale(self, address: str) -> WhaleProfile:
        """Generate complete profile for a whale address"""
        query = """
            SELECT 
                address,
                symbol,
                side,
                size,
                status,
                first_seen_at,
                last_seen_at
            FROM orders
            WHERE address = ?
            ORDER BY first_seen_at
        """

        df = self.connector.execute_query(query, (address,))

        if df.empty:
            raise ValueError(f"No data found for address: {address}")

        # Calculate metrics
        total_orders = len(df)
        buy_orders = len(df[df['side'] == 'BUY'])
        sell_orders = len(df[df['side'] == 'SELL'])
        coins_traded = df['symbol'].unique().tolist()
        total_volume = df['size'].sum()

        first_seen = pd.to_datetime(df['first_seen_at'].iloc[0])
        last_seen = pd.to_datetime(df['last_seen_at'].iloc[-1])

        avg_order_size = df['size'].mean()

        # Find favorite coin
        coin_counts = df['symbol'].value_counts()
        favorite_coin = coin_counts.index[0] if not coin_counts.empty else 'N/A'

        # Determine strategy
        buy_ratio = buy_orders / total_orders if total_orders > 0 else 0
        strategy = self._classify_strategy(buy_ratio, len(coins_traded), total_orders)

        # Calculate activity score (orders per day)
        days_active = max((last_seen - first_seen).days, 1)
        activity_score = total_orders / days_active

        # Calculate success rate (completed vs canceled)
        completed = len(df[df['status'] == 'completed'])
        success_rate = (completed / total_orders * 100) if total_orders > 0 else 0

        # Create display address
        display_address = f"{address[:6]}...{address[-4:]}"

        return WhaleProfile(
            address=address,
            display_address=display_address,
            total_orders=total_orders,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            coins_traded=coins_traded,
            total_volume=total_volume,
            first_seen=first_seen,
            last_seen=last_seen,
            avg_order_size=avg_order_size,
            favorite_coin=favorite_coin,
            strategy_type=strategy,
            activity_score=activity_score,
            success_rate=success_rate
        )

    def _classify_strategy(self, buy_ratio: float, coins_count: int, total_orders: int) -> str:
        """Classify whale strategy based on behavior"""
        if buy_ratio >= 0.8:
            return 'accumulator'
        elif buy_ratio <= 0.2:
            return 'distributor'
        elif 0.4 <= buy_ratio <= 0.6:
            if coins_count > 10:
                return 'diversified_trader'
            else:
                return 'balanced_trader'
        else:
            if total_orders > 500:
                return 'active_swing_trader'
            else:
                return 'opportunistic_trader'

    def get_top_whales(self, limit: int = 20,
                       strategy_filter: Optional[str] = None) -> List[WhaleProfile]:
        """Get profiles of top N whales"""
        query = """
            SELECT DISTINCT address 
            FROM orders
            GROUP BY address
            ORDER BY COUNT(*) DESC
            LIMIT ?
        """

        df = self.connector.execute_query(query, (limit * 2,))  # Get extra to filter

        profiles = []
        for address in df['address']:
            try:
                profile = self.profile_whale(address)

                if strategy_filter and profile.strategy_type != strategy_filter:
                    continue

                profiles.append(profile)

                if len(profiles) >= limit:
                    break

            except Exception as e:
                self.logger.warning(f"Failed to profile {address}: {e}")
                continue

        return profiles


# ============================================================================
# MARKET SENTIMENT ANALYZER
# ============================================================================

class MarketSentimentAnalyzer:
    """
    Analyzes aggregate market sentiment from whale trading patterns.
    Identifies buy/sell pressure, trending coins, and market shifts.
    """

    def __init__(self, connector: DataConnector):
        self.connector = connector
        self.logger = logging.getLogger(__name__)

    def analyze_coin_sentiment(self, symbol: str,
                               lookback_days: int = 7) -> CoinSentiment:
        """Analyze sentiment for a specific coin"""
        cutoff_date = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

        query = """
            SELECT 
                symbol,
                side,
                size,
                address,
                status
            FROM orders
            WHERE symbol = ?
                AND DATE(first_seen_at) >= ?
        """

        df = self.connector.execute_query(query, (symbol, cutoff_date))

        if df.empty:
            return CoinSentiment(
                symbol=symbol,
                total_orders=0,
                buy_orders=0,
                sell_orders=0,
                net_pressure=0,
                unique_whales=0,
                avg_order_size=0,
                sentiment_score=0,
                trend='neutral'
            )

        total_orders = len(df)
        buy_orders = len(df[df['side'] == 'BUY'])
        sell_orders = len(df[df['side'] == 'SELL'])

        buy_volume = df[df['side'] == 'BUY']['size'].sum()
        sell_volume = df[df['side'] == 'SELL']['size'].sum()
        net_pressure = buy_volume - sell_volume

        unique_whales = df['address'].nunique()
        avg_order_size = df['size'].mean()

        # Calculate sentiment score (-100 to +100)
        if buy_orders + sell_orders == 0:
            sentiment_score = 0
        else:
            sentiment_score = ((buy_orders - sell_orders) / (buy_orders + sell_orders)) * 100

        # Classify trend
        if sentiment_score >= 50:
            trend = 'strong_buy'
        elif sentiment_score >= 20:
            trend = 'buy'
        elif sentiment_score >= -20:
            trend = 'neutral'
        elif sentiment_score >= -50:
            trend = 'sell'
        else:
            trend = 'strong_sell'

        return CoinSentiment(
            symbol=symbol,
            total_orders=total_orders,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            net_pressure=net_pressure,
            unique_whales=unique_whales,
            avg_order_size=avg_order_size,
            sentiment_score=sentiment_score,
            trend=trend
        )

    def get_trending_coins(self, limit: int = 20,
                           trend_filter: Optional[str] = None) -> List[CoinSentiment]:
        """Get coins with strongest sentiment signals"""
        # Get all coins
        query = "SELECT DISTINCT symbol FROM orders ORDER BY symbol"
        df = self.connector.execute_query(query)

        sentiments = []
        for symbol in df['symbol']:
            try:
                sentiment = self.analyze_coin_sentiment(symbol)

                if trend_filter and sentiment.trend != trend_filter:
                    continue

                sentiments.append(sentiment)
            except Exception as e:
                self.logger.warning(f"Failed to analyze {symbol}: {e}")
                continue

        # Sort by absolute sentiment score
        sentiments.sort(key=lambda x: abs(x.sentiment_score), reverse=True)

        return sentiments[:limit]

    def get_market_snapshot(self) -> MarketSnapshot:
        """Get current market state snapshot"""
        # Get active whales in last 24h
        cutoff = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')

        query = """
            SELECT 
                COUNT(DISTINCT address) as active_whales,
                SUM(CASE WHEN side = 'BUY' THEN size ELSE 0 END) as buy_volume,
                SUM(CASE WHEN side = 'SELL' THEN size ELSE 0 END) as sell_volume
            FROM orders
            WHERE last_seen_at >= ?
        """

        df = self.connector.execute_query(query, (cutoff,))
        row = df.iloc[0] if not df.empty else {}

        buy_vol = row.get('buy_volume', 0) or 0
        sell_vol = row.get('sell_volume', 0) or 0
        net_flow = buy_vol - sell_vol

        # Determine market sentiment
        if net_flow > 0:
            if net_flow / (buy_vol + sell_vol) > 0.3:
                sentiment = 'strong_bullish'
            else:
                sentiment = 'bullish'
        elif net_flow < 0:
            if abs(net_flow) / (buy_vol + sell_vol) > 0.3:
                sentiment = 'strong_bearish'
            else:
                sentiment = 'bearish'
        else:
            sentiment = 'neutral'

        # Get top coins
        trending = self.get_trending_coins(limit=5)
        top_coins = [s.symbol for s in trending]

        return MarketSnapshot(
            timestamp=datetime.now(),
            active_whales=int(row.get('active_whales', 0) or 0),
            total_buy_pressure=buy_vol,
            total_sell_pressure=sell_vol,
            net_flow=net_flow,
            top_coins=top_coins,
            market_sentiment=sentiment
        )


# ============================================================================
# REPORT GENERATOR
# ============================================================================

class ReportGenerator:
    """
    Generates comprehensive analysis reports in multiple formats.
    Supports markdown, JSON, HTML, and console output.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def generate_whale_report(self, profiles: List[WhaleProfile],
                              format: str = 'markdown') -> str:
        """Generate whale analysis report"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        if format == 'markdown':
            return self._generate_markdown_whale_report(profiles, timestamp)
        elif format == 'json':
            return self._generate_json_whale_report(profiles, timestamp)
        elif format == 'console':
            return self._generate_console_whale_report(profiles)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _generate_markdown_whale_report(self, profiles: List[WhaleProfile],
                                        timestamp: str) -> str:
        """Generate markdown report"""
        output_path = self.output_dir / f'whale_analysis_{timestamp}.md'

        with open(output_path, 'w') as f:
            f.write("# 🐋 Hyperliquid Whale Intelligence Report\n\n")
            f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"**Total Whales Analyzed:** {len(profiles)}\n\n")
            f.write("---\n\n")

            # Summary statistics
            f.write("## 📊 Summary Statistics\n\n")
            total_orders = sum(p.total_orders for p in profiles)
            total_buys = sum(p.buy_orders for p in profiles)
            total_sells = sum(p.sell_orders for p in profiles)

            f.write(f"- **Total Orders:** {total_orders:,}\n")
            f.write(f"- **Total Buy Orders:** {total_buys:,}\n")
            f.write(f"- **Total Sell Orders:** {total_sells:,}\n")
            f.write(f"- **Average Orders per Whale:** {total_orders / len(profiles):.1f}\n\n")

            # Strategy breakdown
            f.write("## 🎯 Strategy Distribution\n\n")
            strategies = {}
            for profile in profiles:
                strategies[profile.strategy_type] = strategies.get(profile.strategy_type, 0) + 1

            for strategy, count in sorted(strategies.items(), key=lambda x: x[1], reverse=True):
                f.write(f"- **{strategy.replace('_', ' ').title()}:** {count} whales\n")

            f.write("\n---\n\n")

            # Top whales
            f.write("## 🏆 Top Whales\n\n")

            for i, profile in enumerate(profiles[:10], 1):
                f.write(f"### {i}. {profile.display_address}\n\n")
                f.write(f"- **Total Orders:** {profile.total_orders:,}\n")
                f.write(f"- **Buy/Sell Ratio:** {profile.buy_ratio:.1f}% / {profile.sell_ratio:.1f}%\n")
                f.write(f"- **Strategy:** {profile.strategy_type.replace('_', ' ').title()}\n")
                f.write(f"- **Favorite Coin:** {profile.favorite_coin}\n")
                f.write(f"- **Coins Traded:** {len(profile.coins_traded)}\n")
                f.write(f"- **Success Rate:** {profile.success_rate:.1f}%\n")
                f.write(f"- **Activity Score:** {profile.activity_score:.2f} orders/day\n")
                f.write(f"- **First Seen:** {profile.first_seen.strftime('%Y-%m-%d')}\n")
                f.write(f"- **Last Active:** {profile.last_seen.strftime('%Y-%m-%d')}\n\n")

        self.logger.info(f"Whale report generated: {output_path}")
        return str(output_path)

    def _generate_json_whale_report(self, profiles: List[WhaleProfile],
                                    timestamp: str) -> str:
        """Generate JSON report"""
        output_path = self.output_dir / f'whale_analysis_{timestamp}.json'

        data = {
            'generated_at': datetime.now().isoformat(),
            'total_whales': len(profiles),
            'whales': [p.to_dict() for p in profiles]
        }

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

        self.logger.info(f"JSON report generated: {output_path}")
        return str(output_path)

    def _generate_console_whale_report(self, profiles: List[WhaleProfile]) -> str:
        """Generate console-friendly report"""
        output = []
        output.append("=" * 80)
        output.append("🐋 HYPERLIQUID WHALE INTELLIGENCE REPORT")
        output.append("=" * 80)
        output.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output.append(f"Total Whales: {len(profiles)}")
        output.append("")

        output.append("TOP 10 WHALES:")
        output.append("-" * 80)

        for i, profile in enumerate(profiles[:10], 1):
            output.append(f"\n{i}. {profile.display_address}")
            output.append(f"   Orders: {profile.total_orders:,} | "
                          f"Buy: {profile.buy_ratio:.0f}% | "
                          f"Sell: {profile.sell_ratio:.0f}%")
            output.append(f"   Strategy: {profile.strategy_type.replace('_', ' ').title()} | "
                          f"Favorite: {profile.favorite_coin}")
            output.append(f"   Success Rate: {profile.success_rate:.1f}% | "
                          f"Activity: {profile.activity_score:.1f} orders/day")

        output.append("")
        output.append("=" * 80)

        report = "\n".join(output)
        print(report)
        return report

    def generate_sentiment_report(self, sentiments: List[CoinSentiment],
                                  format: str = 'markdown') -> str:
        """Generate market sentiment report"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        if format == 'console':
            return self._generate_console_sentiment_report(sentiments)
        else:
            output_path = self.output_dir / f'market_sentiment_{timestamp}.md'

            with open(output_path, 'w') as f:
                f.write("# 📊 Market Sentiment Analysis\n\n")
                f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("---\n\n")

                f.write("## 🔥 Trending Coins\n\n")

                for sentiment in sentiments:
                    emoji = self._get_trend_emoji(sentiment.trend)
                    f.write(f"### {emoji} {sentiment.symbol}\n\n")
                    f.write(f"- **Trend:** {sentiment.trend.upper()}\n")
                    f.write(f"- **Sentiment Score:** {sentiment.sentiment_score:.1f}/100\n")
                    f.write(f"- **Buy Orders:** {sentiment.buy_orders}\n")
                    f.write(f"- **Sell Orders:** {sentiment.sell_orders}\n")
                    f.write(f"- **Unique Whales:** {sentiment.unique_whales}\n")
                    f.write(f"- **Net Pressure:** {sentiment.net_pressure:,.2f}\n\n")

            self.logger.info(f"Sentiment report generated: {output_path}")
            return str(output_path)

    def _generate_console_sentiment_report(self, sentiments: List[CoinSentiment]) -> str:
        """Generate console sentiment report"""
        output = []
        output.append("=" * 80)
        output.append("📊 MARKET SENTIMENT ANALYSIS")
        output.append("=" * 80)
        output.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output.append("")

        for sentiment in sentiments[:15]:
            emoji = self._get_trend_emoji(sentiment.trend)
            output.append(f"{emoji} {sentiment.symbol:10s} | "
                          f"Score: {sentiment.sentiment_score:6.1f} | "
                          f"Trend: {sentiment.trend:12s} | "
                          f"Whales: {sentiment.unique_whales:3d}")

        output.append("")
        output.append("=" * 80)

        report = "\n".join(output)
        print(report)
        return report

    def _get_trend_emoji(self, trend: str) -> str:
        """Get emoji for trend"""
        emojis = {
            'strong_buy': '🚀',
            'buy': '📈',
            'neutral': '➡️',
            'sell': '📉',
            'strong_sell': '💥'
        }
        return emojis.get(trend, '❓')


# ============================================================================
# MAIN ANALYSIS ENGINE
# ============================================================================

class WhaleIntelligenceSystem:
    """
    Main analysis engine coordinating all components.
    Provides high-level API for whale intelligence analysis.
    """

    def __init__(self, config: Optional[AnalysisConfig] = None):
        self.config = config or AnalysisConfig()
        self.connector = DataConnector(self.config.db_path, self.config.cache_enabled)
        self.profiler = WhaleProfiler(self.connector)
        self.sentiment = MarketSentimentAnalyzer(self.connector)
        self.reporter = ReportGenerator(self.config.output_dir)
        self.logger = self._setup_logging()

    def _setup_logging(self) -> logging.Logger:
        """Configure logging"""
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        return logger

    def run_whale_analysis(self, limit: int = 20,
                           output_format: str = 'console') -> str:
        """Run complete whale analysis"""
        self.logger.info("🚀 Starting whale analysis...")

        # Get top whales
        self.logger.info(f"Profiling top {limit} whales...")
        profiles = self.profiler.get_top_whales(limit=limit)

        self.logger.info(f"✅ Analyzed {len(profiles)} whales")

        # Generate report
        report_path = self.reporter.generate_whale_report(profiles, format=output_format)

        self.logger.info("🎉 Whale analysis complete!")
        return report_path

    def run_sentiment_analysis(self, limit: int = 20,
                               output_format: str = 'console') -> str:
        """Run market sentiment analysis"""
        self.logger.info("📊 Starting sentiment analysis...")

        # Get trending coins
        self.logger.info(f"Analyzing top {limit} coins...")
        sentiments = self.sentiment.get_trending_coins(limit=limit)

        self.logger.info(f"✅ Analyzed {len(sentiments)} coins")

        # Generate report
        report_path = self.reporter.generate_sentiment_report(sentiments, format=output_format)

        self.logger.info("🎉 Sentiment analysis complete!")
        return report_path

    def run_full_analysis(self, output_format: str = 'markdown') -> Dict[str, str]:
        """Run complete analysis suite"""
        self.logger.info("🚀 Starting FULL analysis...")

        results = {}

        # Whale analysis
        results['whale_report'] = self.run_whale_analysis(
            limit=self.config.top_n_whales,
            output_format=output_format
        )

        # Sentiment analysis
        results['sentiment_report'] = self.run_sentiment_analysis(
            limit=20,
            output_format=output_format
        )

        # Market snapshot
        snapshot = self.sentiment.get_market_snapshot()
        self.logger.info(f"📸 Market Snapshot: {snapshot.market_sentiment} | "
                         f"Active Whales: {snapshot.active_whales} | "
                         f"Net Flow: {snapshot.net_flow:,.0f}")

        self.logger.info("🎉 Full analysis complete!")

        return results


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """Command-line interface"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Hyperliquid TWAP Whale Intelligence System'
    )

    parser.add_argument(
        'command',
        choices=['whales', 'sentiment', 'full'],
        help='Analysis command to run'
    )

    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        help='Number of results to return'
    )

    parser.add_argument(
        '--format',
        choices=['console', 'markdown', 'json'],
        default='console',
        help='Output format'
    )

    parser.add_argument(
        '--db-path',
        type=str,
        default='data/twap.db',
        help='Path to database file'
    )

    args = parser.parse_args()

    # Create config
    config = AnalysisConfig(db_path=Path(args.db_path))

    # Initialize system
    system = WhaleIntelligenceSystem(config)

    # Run analysis
    if args.command == 'whales':
        system.run_whale_analysis(limit=args.limit, output_format=args.format)
    elif args.command == 'sentiment':
        system.run_sentiment_analysis(limit=args.limit, output_format=args.format)
    elif args.command == 'full':
        system.run_full_analysis(output_format=args.format)


if __name__ == "__main__":
    main()