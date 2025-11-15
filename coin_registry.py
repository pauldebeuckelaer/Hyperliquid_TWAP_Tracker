#!/usr/bin/env python3
"""
Coin Registry - Asset ID Mappings for Hyperliquid
==================================================
Central registry for all asset IDs and their mappings to coin symbols.
Provides helper functions for coin name resolution and market type detection.

Market Types:
- PERP: Perpetual futures (asset_id < 10000)
- SPOT: Spot markets (asset_id >= 10000)

Usage:
    from coin_registry import get_coin_name, get_market_type, HYPE_PERP_ID

    coin = get_coin_name(159)  # 'HYPE'
    market = get_market_type(159)  # 'PERP'
"""

from typing import Optional

# ============================================================================
# HYPE CONSTANTS
# ============================================================================
HYPE_PERP_ID = 159
HYPE_SPOT_ID = 10107

# ============================================================================
# COMPLETE ASSET ID MAPPINGS
# ============================================================================
# PERP assets use index-based IDs (0-999)
# SPOT assets use higher IDs (10000+)

ASSET_ID_TO_NAME = {
    # ========== PERP MARKETS (0-999) ==========
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
    159: 'HYPE',  # PERP market
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

    # ========== SPOT MARKETS (10000+) ==========
    10000: 'PURR',
    10100: 'UP',
    10107: 'HYPE',  # SPOT market
    10142: 'UBTC',
    10150: 'USDE',
    10151: 'UETH',
    10152: 'USDXL',
    10156: 'USOL',
    10162: 'UFART',
    10166: 'USDT0',
    10171: 'USH',
    10178: 'USR',
    10180: 'USDHL',
    10188: 'UPUMP',
    10189: 'USPYX',
    10193: 'UUUSPX',
    10194: 'UBONK',
    10200: 'UMOG',
    10206: 'UENA',
    10210: 'UXPL',
    10224: 'UWLD',
    10228: '2Z',
    10230: 'USDH',
    10231: 'UPHL',
    10233: 'UXPL',
    10234: 'UBTC',
    10235: 'UETH',
    10243: 'UMON',
    10244: 'USDXL',
    110000: 'xyz:XYZ100',
    110001: 'xyz:TSLA',
    110002: 'xyz:NVDA',
    130000: 'vntl:SPACEX',
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_market_type(asset_id: int) -> str:
    """
    Determine if asset is SPOT or PERP based on ID range.

    Args:
        asset_id: Asset identifier from API

    Returns:
        'SPOT', 'PERP', or 'UNKNOWN'
    """
    if asset_id >= 10000:
        return 'SPOT'
    elif asset_id >= 0:
        return 'PERP'
    else:
        return 'UNKNOWN'


def get_coin_name(asset_id: int) -> str:
    """
    Get coin symbol from asset ID.

    Args:
        asset_id: Asset identifier from API

    Returns:
        Coin symbol (e.g., 'BTC', 'HYPE') or 'UNKNOWN_{asset_id}' if not found
    """
    return ASSET_ID_TO_NAME.get(asset_id, f'UNKNOWN_{asset_id}')


def is_spot(asset_id: int) -> bool:
    """Check if asset_id represents a SPOT market"""
    return asset_id >= 10000


def is_perp(asset_id: int) -> bool:
    """Check if asset_id represents a PERP market"""
    return 0 <= asset_id < 10000


def is_hype(asset_id: int) -> bool:
    """Check if asset_id is HYPE (either SPOT or PERP)"""
    return asset_id in [HYPE_PERP_ID, HYPE_SPOT_ID]


def get_hype_market_type(asset_id: int) -> str:
    """
    Get specific market type for HYPE asset.

    Args:
        asset_id: Asset identifier

    Returns:
        'PERP', 'SPOT', or 'UNKNOWN'
    """
    if asset_id == HYPE_PERP_ID:
        return 'PERP'
    elif asset_id == HYPE_SPOT_ID:
        return 'SPOT'
    else:
        return 'UNKNOWN'


def get_all_coins() -> list:
    """Get list of all known coin symbols (deduplicated)"""
    return sorted(set(ASSET_ID_TO_NAME.values()))


def get_asset_ids_for_coin(coin_symbol: str) -> list:
    """
    Get all asset IDs for a given coin symbol.
    Some coins have both SPOT and PERP markets.

    Args:
        coin_symbol: Coin symbol (e.g., 'HYPE', 'BTC')

    Returns:
        List of asset IDs for that coin
    """
    return [
        asset_id
        for asset_id, symbol in ASSET_ID_TO_NAME.items()
        if symbol == coin_symbol
    ]


def get_registry_stats() -> dict:
    """Get statistics about the registry"""
    perp_count = sum(1 for aid in ASSET_ID_TO_NAME.keys() if is_perp(aid))
    spot_count = sum(1 for aid in ASSET_ID_TO_NAME.keys() if is_spot(aid))
    unique_coins = len(set(ASSET_ID_TO_NAME.values()))

    return {
        'total_assets': len(ASSET_ID_TO_NAME),
        'perp_markets': perp_count,
        'spot_markets': spot_count,
        'unique_coins': unique_coins
    }


# ============================================================================
# VALIDATION
# ============================================================================

if __name__ == "__main__":
    """Test the registry"""
    print("=" * 70)
    print("COIN REGISTRY TEST")
    print("=" * 70)

    # Test HYPE
    print("\n🔍 HYPE Markets:")
    print(f"  PERP (ID {HYPE_PERP_ID}): {get_coin_name(HYPE_PERP_ID)} - {get_market_type(HYPE_PERP_ID)}")
    print(f"  SPOT (ID {HYPE_SPOT_ID}): {get_coin_name(HYPE_SPOT_ID)} - {get_market_type(HYPE_SPOT_ID)}")

    # Test other major coins
    print("\n🔍 Major Coins:")
    test_ids = [0, 1, 5, 25, 159]  # BTC, ETH, SOL, XRP, HYPE
    for aid in test_ids:
        print(f"  ID {aid:3d}: {get_coin_name(aid):10s} ({get_market_type(aid)})")

    # Test SPOT markets
    print("\n🔍 Sample SPOT Markets:")
    spot_ids = [10000, 10107, 10142, 10150]
    for aid in spot_ids:
        print(f"  ID {aid}: {get_coin_name(aid):10s} ({get_market_type(aid)})")

    # Test unknown
    print("\n🔍 Unknown Asset:")
    print(f"  ID 99999: {get_coin_name(99999)} ({get_market_type(99999)})")

    # Registry stats
    print("\n📊 Registry Statistics:")
    stats = get_registry_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Test get_asset_ids_for_coin
    print("\n🔍 Asset IDs for HYPE:")
    hype_ids = get_asset_ids_for_coin('HYPE')
    print(f"  Found {len(hype_ids)} markets: {hype_ids}")

    print("\n✅ Registry tests complete!")
    print("=" * 70)