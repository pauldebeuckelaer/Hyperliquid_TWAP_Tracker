#!/usr/bin/env python3
"""
 HypurrScan Client - Multi-Symbol Support
Simplified client for fetching TWAP data for multiple symbols
"""

import requests
import logging
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

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
    """ HypurrScan Client - Multi-Symbol Support"""

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

        logger.info(f"HypurrScan client initialized")

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
        """Get whale activity for specified symbols"""
        if symbols is None:
            symbols = ['HYPE']

        logger.info(f"Fetching whale activity for: {', '.join(symbols)}")

        data = {
            'token_holders': {},
            'twap_data': {},
            'endpoints_working': [],
            'endpoints_failed': [],
            'data_points': 0
        }

        for symbol in symbols:
            # 1. Get holders
            endpoint = f'holdersWithLimit/{symbol}/10'
            result = self._get(endpoint)
            if result:
                data['token_holders'][f'{symbol}_top_10'] = result
                data['endpoints_working'].append(endpoint)
                data['data_points'] += 1
                logger.info(f"Got {symbol} holders")
            else:
                data['endpoints_failed'].append(endpoint)

            # 2. Get TWAP data
            endpoint = f'twap/{symbol}'
            result = self._get(endpoint)

            if result and isinstance(result, list):
                logger.info(f"Processing {len(result)} {symbol} TWAP orders...")

                # ADD THIS NEW DEBUG BLOCK HERE:
                if len(result) > 0:
                    logger.debug("=" * 60)
                    logger.debug("RAW FIRST ORDER FROM API:")
                    import json
                    logger.debug(json.dumps(result[0], indent=2))
                    logger.debug("=" * 60)

                # DEBUG: Log all orders from API
                logger.debug("=" * 60)
                logger.debug("DEBUG: ALL ORDERS FROM API:")
                for i, order in enumerate(result, 1):
                    addr = order.get('user', 'unknown')
                    action = order.get('action', {})
                    twap = action.get('twap', {})
                    size = twap.get('s', 0)
                    ended = order.get('ended', 'N/A')
                    logger.debug(f"  Order {i}: {addr} - Size: {size} - Ended: {ended}")
                logger.debug("=" * 60)

                # Enrich with proper status and product type detection
                enriched_orders = self._enrich_orders(result, symbol)
                data['twap_data'][symbol] = enriched_orders

                # Log summary
                spot_count = len([o for o in enriched_orders if o.get('product_type') == 'SPOT'])
                perp_count = len([o for o in enriched_orders if o.get('product_type') == 'PERP'])
                active_count = len([o for o in enriched_orders if o.get('status') == 'active'])

                logger.info(f"{symbol} TWAP: {spot_count} SPOT, {perp_count} PERP")
                logger.info(f"Status: {active_count} active, {len(enriched_orders) - active_count} inactive")

                data['endpoints_working'].append(endpoint)
                data['data_points'] += len(enriched_orders)
            else:
                data['endpoints_failed'].append(endpoint)

        return data

    def _enrich_orders(self, orders: List[Dict], symbol: str) -> List[Dict]:
        """Enrich orders with correct status and product type"""
        enriched = []

        for order in orders:
            try:
                enriched_order = order.copy()

                # Extract TWAP info
                action = order.get('action', {})
                twap_info = action.get('twap', {})
                user_address = order.get('user', 'unknown')

                # Get fields
                asset_id = twap_info.get('a', 0)
                b_field = twap_info.get('b', True)
                t_field = twap_info.get('t', False)

                # Simple detection: For now, assume all TWAPs are SPOT
                # When PERP TWAPs appear, they'll have different asset IDs
                product_type = "SPOT"
                side = 'BUY' if b_field else 'SELL'

                # Add enriched fields
                enriched_order['address'] = user_address
                enriched_order['side'] = side
                enriched_order['size'] = twap_info.get('s', 0)
                enriched_order['duration_ms'] = twap_info.get('m', 0)
                enriched_order['product_type'] = product_type
                enriched_order['order_hash'] = order.get('hash', '')  # ← ADD THIS LINE

                ended_value = order.get('ended')
                error_field = order.get('error')

                if ended_value == 'canceled':
                    enriched_order['status'] = 'canceled'
                elif ended_value == 'error' or error_field:
                    enriched_order['status'] = 'error'
                elif ended_value is None and not error_field:
                    enriched_order['status'] = 'active'
                elif ended_value:  # Has an 'ended' value that's not canceled/error
                    # This is likely a completion - log it for investigation
                    enriched_order['status'] = 'completed'  # Normalize to 'completed'
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

        # Get whale activity data
        whale_data = self.get_whale_activity(symbols)

        # Get network health (use first symbol)
        network_health = self.get_network_health(symbols[0])

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

    def discover_address_endpoints(self, address: str, symbol: str = 'HYPE') -> Dict[str, Any]:
        """
        Test various endpoint patterns to find what's available for addresses

        Args:
            address: A known wallet address (e.g., from TWAP orders)
            symbol: Token symbol

        Returns:
            Dict with results of endpoint discovery
        """
        logger.info(f"Discovering endpoints for address: {address[:10]}...")

        endpoints_to_test = [
            # Balance/holder endpoints
            f'balance/{symbol}/{address}',
            f'balance/{address}',
            f'holder/{symbol}/{address}',
            f'holder/{address}',

            # User/address endpoints
            f'user/{address}',
            f'address/{address}',
            f'wallet/{address}',
            f'account/{address}',

            # Token-specific
            f'{symbol}/balance/{address}',
            f'{symbol}/holder/{address}',

            # Portfolio/holdings
            f'portfolio/{address}',
            f'holdings/{address}',
            f'assets/{address}',

            # TWAP-specific
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
        """
        Inspect and log the structure of holder data

        Useful for debugging to see what data is available.

        Args:
            symbol: Token symbol
            limit: Number of holders to inspect

        Returns:
            Dict with inspection results
        """
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

            # Check if holders list exists and has items
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
        """
        Try to get balance for a specific address

        This will test various endpoint patterns to find balance data.

        Args:
            address: Wallet address
            symbol: Token symbol

        Returns:
            Balance as float, or None if not found
        """
        # Test different endpoint patterns
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

                # Try to extract balance from various possible structures
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
        """
        Get top token holders with their balances

        Args:
            symbol: Token symbol (e.g., 'HYPE')
            limit: Number of top holders to fetch

        Returns:
            Dict with holder data or None if failed
        """
        endpoint = f'holdersWithLimit/{symbol}/{limit}'
        logger.info(f"Fetching top {limit} holders for {symbol}")

        result = self._get(endpoint)

        if result:
            holder_count = len(result.get('holders', []))
            logger.info(f"Retrieved {holder_count} holders")

            # Debug: Log structure of first holder
            if result.get('holders'):
                first_holder = result['holders'][0]
                logger.debug(f"Holder structure: {list(first_holder.keys())}")

        return result

    def get_address_rank(self, address: str) -> Optional[Dict]:
        """Get holder ranks for an address"""
        endpoint = f'rank/{address}'
        return self._get(endpoint)