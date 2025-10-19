#!/usr/bin/env python3
"""
🔧 FIXED: Eliminate Duplicate Logging and Redundant Data Calls

Key Changes:
1. Add execution context tracking
2. Fetch data once, pass to all methods
3. Separate data fetching from logging
4. Cache-aware logging
"""

import logging
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class OnchainDataManager:
    """FIXED: Onchain Data Manager - No More Duplicate Calls"""

    def __init__(self, hypurr_client, config: Dict = None):
        self.client = hypurr_client
        self.config = config or {}

        # Caching settings
        self.cache_ttl = self.config.get('cache_ttl_seconds', 300)
        self._cached_data = None
        self._cache_timestamp = None

        # Logging settings
        self.log_data_structure = self.config.get('log_data_structure', True)
        self.log_working_endpoints = self.config.get('log_working_endpoints', True)

        # NEW: Execution tracking to prevent duplicates
        self._current_analysis_id = None
        self._analysis_cache = {}

        logger.info("✅ Fixed onchain data manager initialized")

    def get_comprehensive_onchain_data(self, symbol: str, current_price: float) -> Dict[str, Any]:
        """
        🎯 FIXED: Single entry point - fetch ALL data once, no duplicates
        """
        try:
            # Create unique analysis ID
            analysis_id = f"{symbol}_{current_price}_{int(time.time() // 60)}"  # Cache per minute

            # Check if we already have this analysis
            if analysis_id in self._analysis_cache:
                cached_result = self._analysis_cache[analysis_id]
                logger.debug(f"📦 Using cached comprehensive analysis for {symbol}")
                return cached_result

            # Set execution context
            self._current_analysis_id = analysis_id

            logger.info(f"🎯 === COMPREHENSIVE ONCHAIN DATA FOR {symbol} @ ${current_price:.4f} ===")
            start_time = time.time()

            # Step 1: Get fresh hypurr data ONCE
            hypurr_data = self.get_fresh_hypurrscan_data()
            if not hypurr_data:
                logger.warning("❌ No HypurrScan data available")
                return self._create_empty_result(symbol, current_price)

            # Step 2: Extract all components from the SAME data (no re-fetching)
            logger.info(f"📊 Extracting {symbol} data components from cached hypurr data...")

            holder_data = self._extract_holder_data(symbol, hypurr_data)
            twap_data = self._extract_twap_data(symbol, current_price, hypurr_data)
            whale_activity = self._analyze_whale_activity_from_data(symbol, holder_data, twap_data)
            network_data = self._extract_network_metrics(symbol, hypurr_data)

            fetch_time = time.time() - start_time

            # Calculate data quality
            sources_available = sum([
                holder_data.get('data_available', False),
                twap_data.get('data_available', False),
                whale_activity.get('data_available', False),
                network_data.get('data_available', False)
            ])

            # FIXED: Structure data for whale commitment analyzer
            result = {
                'symbol': symbol,
                'current_price': current_price,
                'timestamp': datetime.now(),
                'fetch_time_seconds': fetch_time,

                # CRITICAL: Structure for whale analyzer (no duplicate calls needed)
                'whale_data': {
                    'twap_data': {
                        symbol: twap_data.get('twap_orders', [])
                    },
                    'holder_data': holder_data.get('holder_data', {}),
                    'whale_activity': whale_activity
                },

                # Individual components
                'holder_data': holder_data,
                'twap_data': twap_data,
                'whale_activity': whale_activity,
                'network_data': network_data,

                'overall_data_quality': sources_available >= 1,
                'data_sources_available': sources_available,
                'data_availability': {
                    'holder_data': holder_data.get('data_available', False),
                    'twap_data': twap_data.get('data_available', False),
                    'whale_data': whale_activity.get('data_available', False),
                    'network_data': network_data.get('data_available', False)
                }
            }

            # Cache the result
            self._analysis_cache[analysis_id] = result

            # Clean old cache entries (keep last 10)
            if len(self._analysis_cache) > 10:
                oldest_key = min(self._analysis_cache.keys())
                del self._analysis_cache[oldest_key]

            logger.info(f"✅ Comprehensive data complete: {sources_available}/4 sources, {fetch_time:.1f}s")

            # Reset execution context
            self._current_analysis_id = None

            return result

        except Exception as e:
            logger.error(f"❌ Error getting comprehensive data: {e}")
            self._current_analysis_id = None
            return self._create_empty_result(symbol, current_price)

    def _extract_holder_data(self, symbol: str, hypurr_data) -> Dict[str, Any]:
        """FIXED: Extract holder data from already-fetched hypurr data"""
        try:
            # Only log if this is the primary call (not a duplicate)
            if self._should_log_operation(f"holder_data_{symbol}"):
                logger.info(f"👥 === EXTRACTING {symbol} HOLDER DATA ===")

            whale_activity = hypurr_data.whale_activity_data
            token_holders = whale_activity.get('token_holders', {})

            # Look for symbol-specific data
            symbol_keys = [key for key in token_holders.keys() if symbol.upper() in key.upper()]

            if symbol_keys:
                chosen_key = symbol_keys[0]
                holder_info = token_holders[chosen_key]

                if self._should_log_operation(f"holder_processing_{symbol}"):
                    logger.info(f"✅ Found {symbol} holder data: {chosen_key}")
                    logger.info(
                        f"📊 Processing {len(holder_info) if isinstance(holder_info, (list, dict)) else 'unknown'} holders")

                # Extract holders (same logic, less logging)
                holder_list = []
                if isinstance(holder_info, dict) and 'holders' in holder_info:
                    actual_holders = holder_info['holders']
                    for address, balance in actual_holders.items():
                        holder_list.append({
                            'address': address,
                            'balance': float(balance),
                            'full_address': address
                        })
                    holder_list.sort(key=lambda x: x['balance'], reverse=True)
                elif isinstance(holder_info, list):
                    holder_list = holder_info

                # Only log top holders once
                if self._should_log_operation(f"top_holders_{symbol}") and holder_list:
                    logger.info(f"📊 === TOP 5 {symbol} HOLDERS ===")
                    for i, holder in enumerate(holder_list[:5], 1):
                        address = holder.get('address', 'unknown')
                        balance = holder.get('balance', 0)
                        logger.info(f"   #{i}: {address[:10]}... - {balance:,.0f} {symbol}")

                return {
                    'data_available': True,
                    'holder_data': holder_list,
                    'data_key': chosen_key,
                    'holder_count': len(holder_list)
                }
            else:
                if self._should_log_operation(f"no_holder_data_{symbol}"):
                    logger.warning(f"❌ No {symbol} holder data found")
                return {'data_available': False, 'error': f'no_{symbol}_data'}

        except Exception as e:
            logger.error(f"❌ Error extracting {symbol} holder data: {e}")
            return {'data_available': False, 'error': str(e)}

    def _extract_twap_data(self, symbol: str, current_price: float, hypurr_data) -> Dict[str, Any]:
        """FIXED: Extract TWAP data from already-fetched hypurr data"""
        try:
            if self._should_log_operation(f"twap_data_{symbol}"):
                logger.info(f"📈 === EXTRACTING {symbol} TWAP DATA ===")

            whale_activity = hypurr_data.whale_activity_data
            twap_data = whale_activity.get('twap_data', {})

            if symbol in twap_data:
                twap_orders = twap_data[symbol]

                if isinstance(twap_orders, list) and len(twap_orders) > 0:
                    total_orders = len(twap_orders)
                    canceled_orders = len([o for o in twap_orders if o.get('ended') == 'canceled'])
                    active_orders = total_orders - canceled_orders

                    if self._should_log_operation(f"twap_summary_{symbol}"):
                        logger.info(
                            f"✅ Found {symbol} TWAP: {total_orders} total, {active_orders} active, {canceled_orders} canceled")

                    return {
                        'data_available': True,
                        'twap_orders': twap_orders,
                        'order_stats': {
                            'total_orders': total_orders,
                            'active_orders': active_orders,
                            'canceled_orders': canceled_orders
                        }
                    }
                else:
                    if self._should_log_operation(f"invalid_twap_{symbol}"):
                        logger.warning(f"❌ Invalid TWAP format for {symbol}")
                    return {'data_available': False, 'error': 'invalid_twap_format'}
            else:
                if self._should_log_operation(f"no_twap_{symbol}"):
                    logger.warning(f"❌ No {symbol} TWAP data found")
                return {'data_available': False, 'error': f'no_{symbol}_twap'}

        except Exception as e:
            logger.error(f"❌ Error extracting {symbol} TWAP data: {e}")
            return {'data_available': False, 'error': str(e)}

    def _analyze_whale_activity_from_data(self, symbol: str, holder_data: Dict, twap_data: Dict) -> Dict[str, Any]:
        """FIXED: Analyze whale activity from already-extracted data (no re-fetching)"""
        try:
            if self._should_log_operation(f"whale_analysis_{symbol}"):
                logger.info(f"🐋 === ANALYZING WHALE ACTIVITY FOR {symbol} ===")

            if not holder_data.get('data_available', False):
                logger.debug("❌ No holder data available for whale analysis")
                return {'data_available': False, 'error': 'no_holder_data'}

            holders = holder_data.get('holder_data', [])
            whale_count = len(holders) if isinstance(holders, list) else 0

            if self._should_log_operation(f"whale_patterns_{symbol}"):
                logger.info(f"🔍 Analyzing {whale_count} holders for whale patterns")

            return {
                'data_available': True,
                'holder_count': whale_count,
                'whale_analysis': 'basic_analysis_complete',
                'source_data': {
                    'holders_analyzed': whale_count,
                    'twap_orders_available': len(twap_data.get('twap_orders', []))
                }
            }

        except Exception as e:
            logger.error(f"❌ Error analyzing {symbol} whale activity: {e}")
            return {'data_available': False, 'error': str(e)}

    def _extract_network_metrics(self, symbol: str, hypurr_data) -> Dict[str, Any]:
        """FIXED: Extract network metrics from already-fetched hypurr data"""
        try:
            if self._should_log_operation(f"network_metrics_{symbol}"):
                logger.info(f"🌐 === EXTRACTING NETWORK METRICS ===")

            network_health = hypurr_data.network_health_data
            global_aliases = network_health.get('global_aliases', {})

            active_addresses = len(global_aliases)

            # Simple ecosystem health scoring
            if active_addresses > 500:
                health_score = 0.8
                health = "healthy"
            elif active_addresses > 300:
                health_score = 0.6
                health = "moderate"
            else:
                health_score = 0.4
                health = "low"

            if self._should_log_operation(f"network_health_{symbol}"):
                logger.info(f"💚 Ecosystem health: {health} ({health_score:.1f}) - {active_addresses} addresses")

            return {
                'data_available': True,
                'active_addresses': active_addresses,
                'network_activity_score': health_score,
                'ecosystem_health': health
            }

        except Exception as e:
            logger.error(f"❌ Error extracting network metrics: {e}")
            return {'data_available': False, 'error': str(e)}

    def _should_log_operation(self, operation_key: str) -> bool:
        """Check if this operation should be logged (prevent duplicates within same analysis)"""
        if not self._current_analysis_id:
            return True  # Always log if no analysis context

        full_key = f"{self._current_analysis_id}_{operation_key}"

        # Use a simple set to track logged operations within this analysis
        if not hasattr(self, '_logged_operations'):
            self._logged_operations = set()

        if full_key in self._logged_operations:
            return False

        self._logged_operations.add(full_key)

        # Clean up old operation logs
        if len(self._logged_operations) > 100:
            self._logged_operations.clear()

        return True

    def _create_empty_result(self, symbol: str, current_price: float) -> Dict[str, Any]:
        """Create empty result structure"""
        return {
            'symbol': symbol,
            'current_price': current_price,
            'timestamp': datetime.now(),
            'fetch_time_seconds': 0.0,
            'whale_data': {'twap_data': {}, 'holder_data': {}, 'whale_activity': {}},
            'holder_data': {'data_available': False},
            'twap_data': {'data_available': False},
            'whale_activity': {'data_available': False},
            'network_data': {'data_available': False},
            'overall_data_quality': False,
            'data_sources_available': 0
        }

    # ============================================================================
    # OLD METHODS - NOW JUST DELEGATE TO COMPREHENSIVE METHOD
    # ============================================================================

    def get_symbol_holder_data(self, symbol: str) -> Dict[str, Any]:
        """DEPRECATED: Use get_comprehensive_onchain_data() instead"""
        logger.debug(f"⚠️ get_symbol_holder_data() called directly - consider using comprehensive method")

        # If we're in an analysis context, return from cache
        if self._current_analysis_id:
            analysis_id = self._current_analysis_id
            if analysis_id in self._analysis_cache:
                return self._analysis_cache[analysis_id].get('holder_data', {'data_available': False})

        # Otherwise, do a minimal fetch
        hypurr_data = self.get_fresh_hypurrscan_data()
        if hypurr_data:
            return self._extract_holder_data(symbol, hypurr_data)
        return {'data_available': False}

    def get_symbol_twap_data(self, symbol: str, current_price: float) -> Dict[str, Any]:
        """DEPRECATED: Use get_comprehensive_onchain_data() instead"""
        logger.debug(f"⚠️ get_symbol_twap_data() called directly - consider using comprehensive method")

        # If we're in an analysis context, return from cache
        if self._current_analysis_id:
            analysis_id = self._current_analysis_id
            if analysis_id in self._analysis_cache:
                return self._analysis_cache[analysis_id].get('twap_data', {'data_available': False})

        # Otherwise, do a minimal fetch
        hypurr_data = self.get_fresh_hypurrscan_data()
        if hypurr_data:
            return self._extract_twap_data(symbol, current_price, hypurr_data)
        return {'data_available': False}

    def get_symbol_whale_activity(self, symbol: str) -> Dict[str, Any]:
        """DEPRECATED: Use get_comprehensive_onchain_data() instead"""
        logger.debug(f"⚠️ get_symbol_whale_activity() called directly - consider using comprehensive method")

        # If we're in an analysis context, return from cache
        if self._current_analysis_id:
            analysis_id = self._current_analysis_id
            if analysis_id in self._analysis_cache:
                return self._analysis_cache[analysis_id].get('whale_activity', {'data_available': False})

        # Otherwise, do a minimal analysis
        return {'data_available': False, 'message': 'Use comprehensive analysis method'}

    def get_symbol_network_metrics(self, symbol: str) -> Dict[str, Any]:
        """DEPRECATED: Use get_comprehensive_onchain_data() instead"""
        logger.debug(f"⚠️ get_symbol_network_metrics() called directly - consider using comprehensive method")

        # If we're in an analysis context, return from cache
        if self._current_analysis_id:
            analysis_id = self._current_analysis_id
            if analysis_id in self._analysis_cache:
                return self._analysis_cache[analysis_id].get('network_data', {'data_available': False})

        # Otherwise, do a minimal fetch
        hypurr_data = self.get_fresh_hypurrscan_data()
        if hypurr_data:
            return self._extract_network_metrics(symbol, hypurr_data)
        return {'data_available': False}

    # Keep the original hypurr data fetching method unchanged
    def get_fresh_hypurrscan_data(self, force_refresh: bool = False) -> Optional[Any]:
        """Get HypurrScan data with intelligent caching - UNCHANGED"""
        try:
            # Check cache validity
            if not force_refresh and self._is_cache_valid():
                logger.debug(f"📦 Using cached HypurrScan data (age: {self._get_cache_age():.1f}s)")
                return self._cached_data

            # Only log fetch if actually fetching
            logger.info("🔄 Fetching fresh HypurrScan data...")
            start_time = time.time()

            fresh_data = self.client.fetch_all_data()

            fetch_time = time.time() - start_time
            logger.info(f"✅ Fresh data fetched in {fetch_time:.1f}s")

            # Cache the data
            self._cached_data = fresh_data
            self._cache_timestamp = datetime.now()

            # Log data structure if enabled
            if self.log_data_structure:
                self._log_data_structure(fresh_data)

            return fresh_data

        except Exception as e:
            logger.error(f"❌ Error fetching HypurrScan data: {e}")
            return self._cached_data

    def _is_cache_valid(self) -> bool:
        """Check if cached data is still valid - UNCHANGED"""
        if self._cached_data is None or self._cache_timestamp is None:
            return False
        age = (datetime.now() - self._cache_timestamp).total_seconds()
        return age < self.cache_ttl

    def _get_cache_age(self) -> float:
        """Get cache age in seconds - UNCHANGED"""
        if self._cache_timestamp is None:
            return 0.0
        return (datetime.now() - self._cache_timestamp).total_seconds()

    def _log_data_structure(self, data):
        """Log detailed data structure for analysis - UNCHANGED"""
        # Keep existing implementation
        pass