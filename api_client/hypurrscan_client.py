#!/usr/bin/env python3
"""
HypurrScan Client - WILDCARD SUPPORT FOR ALL COINS
===================================================
Supports both specific symbols and wildcard ['*'] to fetch ALL coins.

Usage:
    # Fetch specific coins
    client.get_whale_activity(['HYPE', 'BTC'])

    # Fetch ALL coins
    client.get_whale_activity(['*'])
"""

import requests
import logging
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

# Import coin registry
from coin_registry import (
    get_coin_name,
    get_market_type,
    get_hype_market_type,
    is_hype,
    HYPE_PERP_ID,
    HYPE_SPOT_ID
)

logger = logging.getLogger(__name__)


@dataclass
class HypurrScanData:
    """HypurrScan data container"""
    whale_activity_data: Dict[str, Any]
    network_health_data: Dict[str, Any]
    data_quality_info: Dict[str, Any]
    fetch_time_seconds: float
    timestamp: datetime


class HypurrScanClient:
    """HypurrScan Client - SPOT + PERP Support via /twap/* with wildcard"""

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.base_url = self.config.get('base_url', 'https://api.hypurrscan.io').rstrip('/')
        self.timeout = self.config.get('timeout_seconds', 30)
        self.rate_limit_delay = self.config.get('rate_limit_delay', 0.1)

        # Session
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'TWAPTracker/1.0',
            'Accept': 'application/json'
        })

        # Stats
        self.stats = {
            'requests_made': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'last_fetch_time': None
        }

        logger.info(f"HypurrScan client initialized (SPOT + PERP via /twap/*)")

    def _get(self, endpoint: str) -> Optional[Dict]:
        """Simple GET request with error handling"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            logger.debug(f"API Request: {url}")
            self.stats['requests_made'] += 1
            response = self.session.get(url, timeout=self.timeout)

            logger.debug(f"Response Status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                self.stats['successful_requests'] += 1

                # DEBUG: Log data structure
                if isinstance(data, list):
                    logger.debug(f"{endpoint}: Returned list with {len(data)} items")
                elif isinstance(data, dict):
                    logger.debug(f"{endpoint}: Returned dict with keys: {list(data.keys())}")

                return data
            else:
                self.stats['failed_requests'] += 1
                logger.warning(f"{endpoint}: HTTP {response.status_code}")
                logger.debug(f"  Response body: {response.text[:200]}")
                return None

        except Exception as e:
            self.stats['failed_requests'] += 1
            logger.error(f"{endpoint}: {e}")
            logger.debug(f"  Full URL was: {url}")
            return None
        finally:
            time.sleep(self.rate_limit_delay)

    def get_whale_activity(self, symbols: List[str] = None) -> Dict[str, Any]:
        """
        Get whale activity for specified symbols OR all coins.

        Args:
            symbols: List of symbols ['HYPE', 'BTC'] OR ['*'] for all coins

        Returns:
            Dict with twap_data organized by coin symbol
        """
        if symbols is None:
            symbols = ['HYPE']

        # Check for wildcard
        fetch_all_coins = symbols == ['*']

        if fetch_all_coins:
            logger.info(f"Fetching whale activity for ALL COINS (wildcard mode)")
        else:
            logger.info(f"Fetching whale activity for: {', '.join(symbols)}")

        data = {
            'token_holders': {},
            'twap_data': {},
            'endpoints_working': [],
            'endpoints_failed': [],
            'data_points': 0
        }

        # For wildcard mode, we skip per-symbol holder fetching
        if not fetch_all_coins:
            for symbol in symbols:
                # Get holders
                endpoint = f'holdersWithLimit/{symbol}/10'
                result = self._get(endpoint)
                if result:
                    data['token_holders'][f'{symbol}_top_10'] = result
                    data['endpoints_working'].append(endpoint)
                    data['data_points'] += 1
                    logger.info(f"Got {symbol} holders")
                else:
                    data['endpoints_failed'].append(endpoint)

        # Fetch ALL TWAPs using wildcard endpoint
        logger.info(f"Fetching ALL TWAPs via /twap/*...")
        endpoint = 'twap/*'
        all_twaps = self._get(endpoint)

        if all_twaps and isinstance(all_twaps, list):
            logger.info(f"Got {len(all_twaps)} total TWAPs across all tokens/markets")

            if fetch_all_coins:
                # WILDCARD MODE: Organize ALL orders by coin
                organized_data = self._organize_all_coins(all_twaps)
                data['twap_data'] = organized_data

                # Log summary
                total_orders = sum(len(orders) for orders in organized_data.values())
                logger.info(f"Organized {total_orders} orders across {len(organized_data)} coins")

                # Log top coins by order count
                sorted_coins = sorted(
                    organized_data.items(),
                    key=lambda x: len(x[1]),
                    reverse=True
                )[:10]

                logger.info("Top 10 coins by TWAP order count:")
                for coin, orders in sorted_coins:
                    spot_count = len([o for o in orders if o.get('product_type') == 'SPOT'])
                    perp_count = len([o for o in orders if o.get('product_type') == 'PERP'])
                    logger.info(f"  {coin}: {len(orders)} orders ({spot_count} SPOT, {perp_count} PERP)")

                data['data_points'] = total_orders

            else:
                # SPECIFIC SYMBOLS MODE: Filter for requested symbols only
                for symbol in symbols:
                    filtered_orders = self._filter_orders_for_symbol(all_twaps, symbol)

                    if filtered_orders:
                        logger.info(f"Filtered to {len(filtered_orders)} {symbol} TWAPs")

                        # Enrich with proper status and product type detection
                        enriched_orders = self._enrich_orders(filtered_orders, symbol)
                        data['twap_data'][symbol] = enriched_orders

                        # Log summary
                        spot_count = len([o for o in enriched_orders if o.get('product_type') == 'SPOT'])
                        perp_count = len([o for o in enriched_orders if o.get('product_type') == 'PERP'])
                        active_count = len([o for o in enriched_orders if o.get('status') == 'active'])

                        logger.info(f"{symbol} TWAP: {spot_count} SPOT, {perp_count} PERP")
                        logger.info(f"Status: {active_count} active, {len(enriched_orders) - active_count} inactive")

                        data['data_points'] += len(enriched_orders)

            data['endpoints_working'].append(endpoint)
        else:
            data['endpoints_failed'].append(endpoint)
            logger.warning("Failed to fetch TWAPs from /twap/*")

        return data

    def _organize_all_coins(self, all_orders: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Organize all orders by coin symbol.

        Args:
            all_orders: Raw orders from /twap/*

        Returns:
            Dict mapping coin_symbol -> list of enriched orders
        """
        organized = {}
        unknown_assets = set()

        for order in all_orders:
            try:
                # Extract asset ID
                action = order.get('action', {})
                twap_info = action.get('twap', {})
                asset_id = twap_info.get('a')

                if asset_id is None:
                    logger.debug(f"Order missing asset_id: {order.get('hash', 'unknown')}")
                    continue

                # Get coin name from registry
                coin = get_coin_name(asset_id)

                # Track unknown assets
                if coin.startswith('UNKNOWN_'):
                    unknown_assets.add(asset_id)

                # Initialize list for this coin if needed
                if coin not in organized:
                    organized[coin] = []

                # Enrich and add order
                enriched_order = self._enrich_single_order(order, coin, asset_id)
                organized[coin].append(enriched_order)

            except Exception as e:
                logger.error(f"Error organizing order: {e}")
                continue

        # Log unknown assets
        if unknown_assets:
            logger.warning(
                f"Found {len(unknown_assets)} unknown asset IDs: "
                f"{sorted(unknown_assets)}"
            )

        return organized

    def _filter_orders_for_symbol(self, all_orders: List[Dict], symbol: str) -> List[Dict]:
        """
        Filter orders for a specific symbol (handles both SPOT and PERP).

        Args:
            all_orders: All orders from API
            symbol: Symbol to filter for (e.g., 'HYPE')

        Returns:
            List of orders matching the symbol
        """
        # Special handling for HYPE (has both PERP and SPOT)
        if symbol == 'HYPE':
            target_ids = [HYPE_PERP_ID, HYPE_SPOT_ID]
        else:
            # For other symbols, we'd need to look up their asset IDs
            # For now, we'll do a simple name match
            from coin_registry import get_asset_ids_for_coin
            target_ids = get_asset_ids_for_coin(symbol)

        filtered = []
        for order in all_orders:
            action = order.get('action', {})
            twap_info = action.get('twap', {})
            asset_id = twap_info.get('a')

            if asset_id in target_ids:
                filtered.append(order)

        return filtered

    def _enrich_single_order(self, order: Dict, coin: str, asset_id: int) -> Dict:
        """
        Enrich a single order with standardized fields.

        Args:
            order: Raw order dict
            coin: Coin symbol
            asset_id: Asset ID

        Returns:
            Enriched order dict
        """
        try:
            enriched_order = order.copy()

            # Extract TWAP info
            action = order.get('action', {})
            twap_info = action.get('twap', {})
            user_address = order.get('user', 'unknown')

            # Get market type from registry
            product_type = get_market_type(asset_id)

            # Get fields
            b_field = twap_info.get('b', True)
            side = 'BUY' if b_field else 'SELL'

            # Add enriched fields
            enriched_order['address'] = user_address
            enriched_order['coin'] = coin
            enriched_order['side'] = side
            enriched_order['size'] = twap_info.get('s', 0)
            enriched_order['duration_ms'] = twap_info.get('m', 0)
            enriched_order['product_type'] = product_type
            enriched_order['asset_id'] = asset_id
            enriched_order['order_hash'] = order.get('hash', '')

            # Determine status
            ended_value = order.get('ended')
            error_field = order.get('error')

            if ended_value == 'canceled':
                enriched_order['status'] = 'canceled'
            elif ended_value == 'error' or error_field:
                enriched_order['status'] = 'error'
            elif ended_value is None and not error_field:
                enriched_order['status'] = 'active'
            elif ended_value:
                enriched_order['status'] = 'completed'
            else:
                enriched_order['status'] = 'unknown'

            return enriched_order

        except Exception as e:
            logger.error(f"Error enriching single order: {e}")
            return order

    def _enrich_orders(self, orders: List[Dict], symbol: str) -> List[Dict]:
        """
        Enrich orders with correct status and product type.
        (Legacy method for HYPE-specific tracking)
        """
        enriched = []

        for order in orders:
            try:
                enriched_order = order.copy()

                # Extract TWAP info
                action = order.get('action', {})
                twap_info = action.get('twap', {})
                user_address = order.get('user', 'unknown')

                # Get asset ID and determine market type
                asset_id = twap_info.get('a', 0)

                # Use registry for market type
                if symbol == 'HYPE':
                    product_type = get_hype_market_type(asset_id)
                else:
                    product_type = get_market_type(asset_id)

                # Get fields
                b_field = twap_info.get('b', True)
                side = 'BUY' if b_field else 'SELL'

                # Add enriched fields
                enriched_order['address'] = user_address
                enriched_order['side'] = side
                enriched_order['size'] = twap_info.get('s', 0)
                enriched_order['duration_ms'] = twap_info.get('m', 0)
                enriched_order['product_type'] = product_type
                enriched_order['asset_id'] = asset_id
                enriched_order['order_hash'] = order.get('hash', '')

                ended_value = order.get('ended')
                error_field = order.get('error')

                if ended_value == 'canceled':
                    enriched_order['status'] = 'canceled'
                elif ended_value == 'error' or error_field:
                    enriched_order['status'] = 'error'
                elif ended_value is None and not error_field:
                    enriched_order['status'] = 'active'
                elif ended_value:
                    enriched_order['status'] = 'completed'
                    logger.debug(f"🎯 COMPLETED ORDER DETECTED - ended field: {ended_value}")
                else:
                    enriched_order['status'] = str(ended_value) if ended_value else 'unknown'

                enriched.append(enriched_order)

            except Exception as e:
                logger.error(f"Error enriching order: {e}")
                logger.debug(f"  Problematic order: {order}")
                continue

        logger.debug(f"Enriched {len(enriched)}/{len(orders)} orders")
        return enriched

    def get_network_health(self, symbol: str = 'HYPE') -> Dict[str, Any]:
        """Get network health metrics"""
        logger.info(f"Fetching network health...")

        endpoint = f'holders/{symbol}'
        result = self._get(endpoint)

        health_data = {
            'total_addresses': 0,
            'endpoint_status': 'failed'
        }

        if result:
            addresses = result.get('holders', [])
            health_data['total_addresses'] = len(addresses)
            health_data['endpoint_status'] = 'success'
            logger.info(f"Network health: {len(addresses)} addresses")
        else:
            logger.warning("Network health check failed")

        return health_data

    def fetch_all_data(self, symbols: List[str] = None) -> HypurrScanData:
        """Fetch all data for specified symbols"""
        if symbols is None:
            symbols = ['HYPE']

        start_time = time.time()
        logger.info(f"Starting data collection for: {', '.join(symbols)}")

        # Get whale activity data (includes both SPOT and PERP now via /twap/*)
        whale_data = self.get_whale_activity(symbols)

        # Get network health (use first symbol, skip for wildcard)
        if symbols != ['*']:
            network_health = self.get_network_health(symbols[0])
        else:
            network_health = {'total_addresses': 0, 'endpoint_status': 'skipped'}

        # Calculate stats
        fetch_time = time.time() - start_time
        self.stats['last_fetch_time'] = fetch_time

        data_quality = {
            'working_endpoints': len(whale_data['endpoints_working']),
            'failed_endpoints': len(whale_data['endpoints_failed']),
            'success_rate': (
                len(whale_data['endpoints_working']) /
                (len(whale_data['endpoints_working']) + len(whale_data['endpoints_failed'])) * 100
                if (len(whale_data['endpoints_working']) + len(whale_data['endpoints_failed'])) > 0
                else 0
            ),
            'data_points_collected': whale_data['data_points']
        }

        logger.info("Data Collection Complete:")
        logger.info(f"Working endpoints: {data_quality['working_endpoints']}")
        logger.info(f"Failed endpoints: {data_quality['failed_endpoints']}")
        logger.info(f"Success rate: {data_quality['success_rate']:.1f}%")
        logger.info(f"Data points: {data_quality['data_points_collected']}")
        logger.info(f"Fetch time: {fetch_time:.1f}s")

        return HypurrScanData(
            whale_activity_data=whale_data,
            network_health_data=network_health,
            data_quality_info=data_quality,
            fetch_time_seconds=fetch_time,
            timestamp=datetime.now()
        )

    # ========== LEGACY METHODS (kept for compatibility) ==========

    def discover_address_endpoints(self, address: str, symbol: str = 'HYPE') -> Dict[str, Any]:
        """Test various endpoint patterns to find what's available for addresses"""
        logger.info(f"Discovering endpoints for address: {address[:10]}...")

        endpoints_to_test = [
            f'balance/{symbol}/{address}',
            f'balance/{address}',
            f'holder/{symbol}/{address}',
            f'holder/{address}',
            f'user/{address}',
            f'address/{address}',
            f'wallet/{address}',
            f'account/{address}',
            f'{symbol}/balance/{address}',
            f'{symbol}/holder/{address}',
            f'portfolio/{address}',
            f'holdings/{address}',
            f'assets/{address}',
            f'twap/user/{address}',
            f'userTwap/{address}',
            f'orders/{address}',
        ]

        results = {
            'address': address,
            'working': [],
            'failed': [],
            'details': {}
        }

        for endpoint in endpoints_to_test:
            logger.debug(f"Testing: {endpoint}")
            result = self._get(endpoint)

            if result is not None:
                data_type = type(result).__name__
                data_len = len(result) if isinstance(result, (list, dict)) else 'N/A'

                logger.info(f"  ✓ {endpoint} - Type: {data_type}, Length: {data_len}")

                results['working'].append(endpoint)
                results['details'][endpoint] = {
                    'status': 'working',
                    'type': data_type,
                    'length': data_len,
                    'sample': str(result)[:200] if result else None,
                    'full_response': result
                }
            else:
                results['failed'].append(endpoint)
                results['details'][endpoint] = {'status': 'failed'}

        logger.info(f"Working endpoints: {len(results['working'])}/{len(endpoints_to_test)}")

        return results

    def inspect_holder_data(self, symbol: str = 'HYPE', limit: int = 10) -> Dict:
        """Inspect and log the structure of holder data"""
        logger.info(f"Inspecting holder data structure for {symbol}...")

        endpoint = f'holdersWithLimit/{symbol}/{limit}'
        result = self._get(endpoint)

        inspection = {
            'endpoint': endpoint,
            'success': result is not None,
            'top_level_keys': [],
            'holder_keys': [],
            'sample_holder': None,
            'holders_count': 0
        }

        if result:
            inspection['top_level_keys'] = list(result.keys())
            logger.info(f"Top-level keys: {inspection['top_level_keys']}")

            if 'holders' in result:
                holders = result['holders']
                inspection['holders_count'] = len(holders)
                logger.info(f"Number of holders: {len(holders)}")

                if holders and len(holders) > 0:
                    first_holder = holders[0]
                    inspection['holder_keys'] = list(first_holder.keys())
                    inspection['sample_holder'] = first_holder

                    logger.debug(f"Holder object keys: {inspection['holder_keys']}")
                    logger.debug(f"Sample holder: {first_holder}")
                else:
                    logger.warning("Holders list is empty!")
            else:
                logger.warning("No 'holders' key in response!")
        else:
            logger.error("Failed to fetch holder data")

        return inspection

    def get_address_balance(self, address: str, symbol: str = 'HYPE') -> Optional[float]:
        """Try to get balance for a specific address"""
        patterns = [
            f'balance/{symbol}/{address}',
            f'holder/{symbol}/{address}',
            f'address/{address}/{symbol}',
            f'user/{address}',
        ]

        for pattern in patterns:
            result = self._get(pattern)
            if result:
                logger.info(f"Found working endpoint: {pattern}")
                logger.debug(f"  Response: {result}")

                if isinstance(result, dict):
                    balance = (
                            result.get('balance') or
                            result.get('amount') or
                            result.get('value')
                    )
                    if balance is not None:
                        return float(balance)
                elif isinstance(result, (int, float)):
                    return float(result)

        logger.debug(f"No balance endpoint found for {address}")
        return None

    def get_holders(self, symbol: str = 'HYPE', limit: int = 100) -> Optional[Dict]:
        """Get top token holders with their balances"""
        endpoint = f'holdersWithLimit/{symbol}/{limit}'
        logger.info(f"Fetching top {limit} holders for {symbol}")

        result = self._get(endpoint)

        if result:
            holder_count = len(result.get('holders', []))
            logger.info(f"Retrieved {holder_count} holders")

            if result.get('holders'):
                first_holder = result['holders'][0]
                logger.debug(f"Holder structure: {list(first_holder.keys())}")

        return result

    def get_address_rank(self, address: str) -> Optional[Dict]:
        """Get holder ranks for an address"""
        endpoint = f'rank/{address}'
        return self._get(endpoint)