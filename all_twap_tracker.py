#!/usr/bin/env python3
"""
All Coins TWAP Fetcher
Fetches TWAP orders for ALL coins on Hyperliquid using /twap/* endpoint
"""
import logging
from typing import List, Dict
import time

from api_client.hypurrscan_client import HypurrScanClient

logger = logging.getLogger(__name__)

# Hardcoded asset ID mappings (PERP assets use index, SPOT assets use higher numbers)
ASSET_ID_TO_NAME = {
    # Major PERP assets (index-based)
    0: 'BTC',
    1: 'ETH',
    2: 'ATOM',
    3: 'MATIC',
    4: 'DYDX',
    5: 'SOL',
    6: 'AVAX',
    7: 'BNB',
    8: 'APE',
    9: 'OP',
    10: 'LTC',
    11: 'ARB',
    12: 'DOGE',
    13: 'INJ',
    14: 'SUI',
    15: 'kPEPE',
    16: 'CRV',
    17: 'LDO',
    18: 'LINK',
    19: 'STX',
    20: 'RNDR',
    21: 'CFX',
    22: 'FTM',
    23: 'GMX',
    24: 'SNX',
    25: 'XRP',
    26: 'BCH',
    27: 'APT',
    28: 'AAVE',
    29: 'COMP',
    30: 'MKR',
    31: 'WLD',
    32: 'FXS',
    33: 'HPOS',
    34: 'RLB',
    35: 'UNIBOT',
    36: 'YGG',
    37: 'TRX',
    38: 'kSHIB',
    39: 'UNI',
    40: 'SEI',
    41: 'RUNE',
    42: 'OX',
    43: 'FRIEND',
    44: 'SHIA',
    45: 'CYBER',
    46: 'ZRO',
    47: 'BLZ',
    48: 'DOT',
    49: 'BANANA',
    50: 'TRB',
    51: 'FTT',
    52: 'LOOM',
    53: 'OGN',
    54: 'RDNT',
    55: 'ARK',
    56: 'BNT',
    57: 'CANTO',
    58: 'REQ',
    59: 'BIGTIME',
    60: 'KAS',
    61: 'ORBS',
    62: 'BLUR',
    63: 'TIA',
    64: 'BSV',
    65: 'ADA',
    66: 'TON',
    67: 'MINA',
    68: 'POLYX',
    69: 'GAS',
    70: 'PENDLE',
    71: 'STG',
    72: 'FET',
    73: 'STRAX',
    74: 'NEAR',
    75: 'MEME',
    76: 'ORDI',
    77: 'BADGER',
    78: 'NEO',
    79: 'ZEN',
    80: 'FIL',
    81: 'PYTH',
    82: 'SUSHI',
    83: 'ILV',
    84: 'IMX',
    85: 'kBONK',
    86: 'GMT',
    87: 'SUPER',
    88: 'USTC',
    89: 'NFTI',
    90: 'JUP',
    91: 'kLUNC',
    92: 'RSR',
    93: 'GALA',
    94: 'JTO',
    95: 'NTRN',
    96: 'ACE',
    97: 'MAV',
    98: 'WIF',
    99: 'CAKE',
    100: 'PEOPLE',
    101: 'ENS',
    102: 'ETC',
    103: 'XAI',
    104: 'MANTA',
    105: 'UMA',
    106: 'ONDO',
    107: 'ALT',
    108: 'ZETA',
    109: 'DYM',
    110: 'MAVIA',
    111: 'W',
    112: 'PANDORA',
    113: 'STRK',
    114: 'PIXEL',
    115: 'AI',
    116: 'TAO',
    117: 'AR',
    118: 'MYRO',
    119: 'kFLOKI',
    120: 'BOME',
    121: 'ETHFI',
    122: 'ENA',
    123: 'MNT',
    124: 'TNSR',
    125: 'SAGA',
    126: 'MERL',
    127: 'HBAR',
    128: 'POPCAT',
    129: 'OMNI',
    130: 'EIGEN',
    131: 'REZ',
    132: 'NOT',
    133: 'TURBO',
    134: 'BRETT',
    135: 'IO',
    136: 'ZK',
    137: 'BLAST',
    138: 'LISTA',
    139: 'MEW',
    140: 'RENDER',
    141: 'kDOGS',
    142: 'POL',
    143: 'CATI',
    144: 'CELO',
    145: 'HMSTR',
    146: 'SCR',
    147: 'NEIROETH',
    148: 'kNEIRO',
    149: 'GOAT',
    150: 'MOODENG',
    151: 'GRASS',
    152: 'PURR',
    153: 'PNUT',
    154: 'XLM',
    155: 'CHILLGUY',
    156: 'SAND',
    157: 'IOTA',
    158: 'ALGO',
    159: 'HYPE',
    160: 'ME',
    161: 'MOVE',
    162: 'VIRTUAL',
    163: 'PENGU',
    164: 'USUAL',
    165: 'FARTCOIN',
    166: 'AI16Z',
    167: 'AIXBT',
    168: 'ZEREBRO',
    169: 'BIO',
    170: 'GRIFFAIN',
    171: 'SPX',
    172: 'S',
    173: 'MORPHO',
    174: 'TRUMP',
    175: 'MELANIA',
    176: 'ANIME',
    177: 'VINE',
    178: 'VVV',
    179: 'JELLY',
    180: 'BERA',
    181: 'TST',
    182: 'LAYER',
    183: 'IP',
    184: 'OM',
    185: 'KAITO',
    186: 'NIL',
    187: 'PAXG',
    188: 'PROMPT',
    189: 'BABY',
    190: 'WCT',
    191: 'HYPER',
    192: 'ZORA',
    193: 'INIT',
    194: 'DOOD',
    195: 'LAUNCHCOIN',
    196: 'NXPC',
    197: 'SOPH',
    198: 'RESOLV',
    199: 'SYRUP',
    200: 'PUMP',
    201: 'PROVE',
    202: 'YZY',
    203: 'XPL',
    204: 'WLFI',
    205: 'LINEA',
    206: 'SKY',
    207: 'ASTER',
    208: 'AVNT',
    209: 'STBL',
    210: '0G',
    211: 'HEMI',
    212: 'APEX',
    213: '2Z',
    214: 'ZEC',
    215: 'MON',
    216: 'MET',
    217: 'MEGA',
    218: 'CC',
    219: 'ICP',
    220: 'AERO',

    # Major SPOT assets (higher index numbers)
    10000: 'PURR',
    10100: 'UP',
    10107: 'HYPE',
    10142: 'UBTC',  # @142
    10150: 'USDE',  # @150
    10151: 'UETH',  # @151
    10152: 'USDXL',  # @152
    10156: 'USOL',  # @156
    10162: 'UFART',  # @162
    10166: 'USDT0',  # @166
    10171: 'USH',  # @171
    10178: 'USR',  # @178
    10180: 'USDHL',  # @180
    10188: 'UPUMP',  # @188
    10189: 'USPYX',  # @189
    10193: 'UUUSPX',  # @193
    10194: 'UBONK',  # @194
    10200: 'UMOG',  # @200
    10206: 'UENA',  # @206
    10210: 'UXPL',  # @210
    10224: 'UWLD',  # @224
    10228: '2Z',  # @228
    10230: 'USDH',  # @230
    10231: 'UPHL',  # @231
    10233: 'UXPL',  # @233
    10234: 'UBTC',  # @234
    10235: 'UETH',  # @235
    10243: 'UMON',  # @243
    10244: 'USDXL',  # @244
    110000: 'xyz:XYZ100',
    110001: 'xyz:TSLA',
    110002: 'xyz:NVDA',
    130000: 'vntl:SPACEX'
    # Add more spot assets as needed...
}


# Determine market type by asset ID range
def get_market_type(asset_id: int) -> str:
    """Determine if asset is SPOT or PERP based on ID"""
    if asset_id >= 10000:
        return 'SPOT'
    else:
        return 'PERP'


class AllCoinsTWAPFetcher:
    """Fetches TWAP orders for all coins on Hyperliquid using /twap/* wildcard"""

    def __init__(self, hypurrscan_client: HypurrScanClient):
        self.client = hypurrscan_client
        self.last_fetch_time = 0
        self.fetch_interval = 10  # seconds between fetches

        logger.info("All Coins TWAP Fetcher initialized")
        logger.info(f"Loaded {len(ASSET_ID_TO_NAME)} asset mappings")

    def get_coin_name(self, asset_id: int) -> str:
        """Get coin name from asset ID"""
        return ASSET_ID_TO_NAME.get(asset_id, f"UNKNOWN_{asset_id}")

    def get_market_type_from_id(self, asset_id: int) -> str:
        """Get market type (SPOT/PERP) from asset ID"""
        return get_market_type(asset_id)

    def fetch_all_twap_orders(self) -> Dict[str, List[Dict]]:
        """
        Fetch TWAP orders for ALL coins using /twap/* wildcard

        Returns:
            Dict mapping coin symbol -> list of TWAP orders
            Example: {
                'BTC': [{order1}, {order2}],
                'ETH': [{order3}],
                'HYPE': [{order4}, {order5}]
            }
        """
        # Rate limiting
        current_time = time.time()
        if current_time - self.last_fetch_time < self.fetch_interval:
            wait_time = self.fetch_interval - (current_time - self.last_fetch_time)
            logger.debug(f"Rate limiting: waiting {wait_time:.1f}s")
            time.sleep(wait_time)

        self.last_fetch_time = time.time()

        try:
            logger.info("Fetching ALL TWAP orders using /twap/*...")

            # Use the internal _get method to fetch from /twap/*
            endpoint = 'twap/*'
            all_twap_data = self.client._get(endpoint)

            if not all_twap_data:
                logger.warning("No TWAP data returned from /twap/*")
                return {}

            if not isinstance(all_twap_data, list):
                logger.error(f"Expected list, got {type(all_twap_data)}")
                return {}

            logger.info(f"Received {len(all_twap_data)} total TWAP orders")

            # Organize by coin symbol
            orders_by_coin = {}
            unknown_assets = set()  # Track unknown asset IDs

            for order in all_twap_data:
                try:
                    # Extract asset_id
                    action = order.get('action', {})
                    twap_info = action.get('twap', {})
                    asset_id = twap_info.get('a')

                    if asset_id is None:
                        logger.debug(f"Order missing asset_id: {order.get('hash', 'unknown')}")
                        continue

                    # Get coin name from asset ID
                    coin = self.get_coin_name(asset_id)

                    # Track unknown assets
                    if coin.startswith('UNKNOWN_'):
                        unknown_assets.add(asset_id)

                    if coin not in orders_by_coin:
                        orders_by_coin[coin] = []

                    # Enrich the order with useful fields
                    enriched_order = self._enrich_order(order, asset_id)
                    orders_by_coin[coin].append(enriched_order)

                except Exception as e:
                    logger.error(f"Error processing order: {e}")
                    continue

            # Log unknown assets
            if unknown_assets:
                logger.warning(
                    f"Found {len(unknown_assets)} unknown asset IDs: "
                    f"{sorted(unknown_assets)}"
                )

            # Log summary
            total_orders = len(all_twap_data)
            coins_with_twaps = len(orders_by_coin)

            logger.info(
                f"Organized {total_orders} orders across {coins_with_twaps} coins"
            )

            # Log per-coin breakdown
            for coin in sorted(orders_by_coin.keys()):
                order_count = len(orders_by_coin[coin])
                logger.debug(f"  {coin}: {order_count} orders")

            return orders_by_coin

        except Exception as e:
            logger.error(f"Error fetching all TWAP orders: {e}")
            logger.exception(e)
            return {}

    def _enrich_order(self, order: Dict, asset_id: int) -> Dict:
        """Enrich order with useful extracted fields"""
        try:
            enriched = order.copy()

            # Extract TWAP info
            action = order.get('action', {})
            twap_info = action.get('twap', {})

            # Add useful fields
            enriched['address'] = order.get('user', 'unknown')
            enriched['coin'] = self.get_coin_name(asset_id)
            enriched['asset_id'] = asset_id
            enriched['product_type'] = self.get_market_type_from_id(asset_id)
            enriched['side'] = 'BUY' if twap_info.get('b', True) else 'SELL'
            enriched['size'] = twap_info.get('s', 0)
            enriched['duration_ms'] = twap_info.get('m', 0)
            enriched['order_hash'] = order.get('hash', '')

            # Determine status
            ended_value = order.get('ended')
            error_field = order.get('error')

            if ended_value == 'canceled':
                enriched['status'] = 'canceled'
            elif ended_value == 'error' or error_field:
                enriched['status'] = 'error'
            elif ended_value is None and not error_field:
                enriched['status'] = 'active'
            elif ended_value:
                enriched['status'] = 'completed'
            else:
                enriched['status'] = 'unknown'

            return enriched

        except Exception as e:
            logger.error(f"Error enriching order: {e}")
            return order

    def fetch_excluding_coin(self, excluded_coin: str) -> Dict[str, List[Dict]]:
        """
        Fetch TWAP orders for all coins EXCEPT the specified one

        Args:
            excluded_coin: Coin symbol to exclude (e.g., 'HYPE')

        Returns:
            Dict of all TWAP orders excluding the specified coin
        """
        all_orders = self.fetch_all_twap_orders()

        # Remove the excluded coin if present
        if excluded_coin in all_orders:
            excluded_count = len(all_orders[excluded_coin])
            logger.info(f"Excluding {excluded_coin} ({excluded_count} orders)")
            del all_orders[excluded_coin]

        return all_orders

    def get_summary(self) -> Dict:
        """
        Get summary of current TWAP activity

        Returns:
            {
                'total_orders': 150,
                'coins_with_twaps': 12,
                'active_coins': ['BTC', 'ETH', 'SOL', ...]
            }
        """
        all_orders = self.fetch_all_twap_orders()

        total_orders = sum(len(orders) for orders in all_orders.values())

        return {
            'total_orders': total_orders,
            'coins_with_twaps': len(all_orders),
            'active_coins': sorted(all_orders.keys())
        }


if __name__ == "__main__":
    """Test the fetcher"""
    import json

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
    )

    # Initialize HypurrScanClient
    config = {}
    hypurrscan_client = HypurrScanClient(config)

    # Create fetcher
    fetcher = AllCoinsTWAPFetcher(hypurrscan_client)

    # Test: Fetch all TWAP orders using wildcard
    print("\n" + "=" * 70)
    print("FETCHING ALL TWAP ORDERS (/twap/*)")
    print("=" * 70)
    orders = fetcher.fetch_all_twap_orders()

    # Print ALL coins with their transaction hashes
    print("\n" + "=" * 70)
    print("ALL COINS WITH TRANSACTION HASHES")
    print("=" * 70)
    # After fetching orders, add this:
    print("\n" + "=" * 70)
    print("UNMAPPED ASSETS FOUND")
    print("=" * 70)

    unknown_coins = [coin for coin in orders.keys() if coin.startswith('UNKNOWN_')]
    if unknown_coins:
        for coin in sorted(unknown_coins):
            print(f"\n{coin}: {len(orders[coin])} orders")
            # Show first order details
            first_order = orders[coin][0]
            print(f"  Sample hash: {first_order['order_hash']}")
            print(f"  Link: https://hypurrscan.io/tx/{first_order['order_hash']}")
            print(f"  Market: {first_order['product_type']}")
    else:
        print("✓ No unmapped assets found!")
    for coin in sorted(orders.keys()):
        coin_orders = orders[coin]
        print(f"\n{coin}: {len(coin_orders)} orders")

        # Show first 3 orders with hashes
        for order in coin_orders[:3]:
            asset_id = order.get('asset_id')
            tx_hash = order.get('order_hash', 'N/A')
            size = order.get('size')
            side = order.get('side')
            product_type = order.get('product_type')

            # Create HypurrScan link
            hypurrscan_link = f"https://hypurrscan.io/tx/{tx_hash}"

            print(f"  [{product_type}] {side} {size} | Asset ID: {asset_id}")
            print(f"    Hash: {tx_hash}")
            print(f"    Link: {hypurrscan_link}")

        if len(coin_orders) > 3:
            print(f"  ... and {len(coin_orders) - 3} more orders")