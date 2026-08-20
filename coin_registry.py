#!/usr/bin/env python3
"""
Coin Registry - Asset ID Mappings for Hyperliquid
==================================================
Central registry for all asset IDs and their mappings to coin symbols.
Provides helper functions for coin name resolution and market type detection.

Dynamic spot token resolution + HIP-3 markets.
- Static mappings for known perps and common spots
- Dynamic resolution via Hyperliquid spotMeta API for unknown tokens
- HIP-3 market resolution via perpDexs API (all deployed dexes)
- Call init_dynamic_registry(hl_client) at startup to enable

Market Types:
- PERP: Perpetual futures (asset_id < 10000 OR asset_id >= 110000 for HIP-3)
- SPOT: Spot markets (10000 <= asset_id < 110000)

Asset ID Ranges:
- 0-9999: Standard perp markets
- 10000-109999: Standard spot markets (10000 + market_index)
- 110000+: HIP-3 perps. Each dex gets a 10K block assigned at init in
  perpDexs order; ranges are dynamic, not fixed to any dex name.

Usage:
    from coin_registry import get_coin_name, get_market_type, init_dynamic_registry

    # At startup (once)
    init_dynamic_registry(hyperliquid_client)

    # Then use as normal
    coin = get_coin_name(159)      # 'HYPE'
    coin = get_coin_name(120002)   # 'flx:TSLA'
    market = get_market_type(159)  # 'PERP'
    market = get_market_type(110005) # 'PERP' (HIP-3)
"""

import logging
from typing import Optional, Dict, List
import time

logger = logging.getLogger(__name__)

# ============================================================================
# HYPE CONSTANTS
# ============================================================================
HYPE_PERP_ID = 159
HYPE_SPOT_ID = 10107

# ============================================================================
# HIP-3 RANGE CONSTANTS
# ============================================================================
HIP3_START = 110000  # First HIP-3 asset ID
SPOT_START = 10000  # First spot asset ID
SPOT_END = 110000  # End of spot range (exclusive)



# ============================================================================
# COMPLETE ASSET ID MAPPINGS (STATIC)
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
    220: 'AERO',
    221: 'STABLE',
    222: 'FOGO',
    223: 'LIT',
    224: 'XMR',
    225: 'AXS',
    226: 'DASH',
    227: 'SKR',
    228: 'AZTEC',

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
}

# ============================================================================
# HIP-3 MARKET MAPPINGS (dynamic, all deployed dexes)
# ============================================================================
# These are tokenized equity markets with separate asset ID ranges
# Populated at runtime by init_hip3_registry()

_hip3_market_map: Dict[str, Dict[int, str]] = {}

# Range configuration for HIP-3 markets
# Dynamic range mapping — populated at runtime by init_hip3_registry()
# Maps dex_name -> (range_start, range_end)
_hip3_ranges: Dict[str, tuple] = {}

# ============================================================================
# DYNAMIC REGISTRY CLASS
# ============================================================================

class DynamicCoinRegistry:
    """
    Coin registry with dynamic spot token resolution.

    Falls back to static ASSET_ID_TO_NAME when dynamic data unavailable.
    Resolves unknown spot tokens by fetching spotMeta from Hyperliquid API.

    IMPORTANT: Uses TWO mappings:
    - _spot_token_map: token index -> token name (for individual tokens)
    - _spot_market_map: market/universe index -> market name (for trading pairs)

    Asset IDs in TWAP data use MARKET indices, not token indices!
    """

    def __init__(self):
        # Token index -> name mapping (from "tokens" array)
        self._spot_token_map: Dict[int, str] = {}

        # Market/Universe index -> name mapping (from "universe" array)
        # This is what we need for resolving TWAP asset IDs!
        self._spot_market_map: Dict[int, str] = {}

        self._initialized = False
        self._token_count = 0
        self._market_count = 0

        # Perp universe index -> name (from "meta" endpoint)
        # Fixes: native perps listed after static dict was last hand-edited
        # (CC/ICP/CHIP/GRAM/CASHCAT class of bug)
        self._perp_market_map: Dict[int, str] = {}

        # Client handle for on-miss refresh (set by init_dynamic_registry)
        self._hl_client = None

        # Rate limiter for on-miss refreshes: scope -> monotonic timestamp
        self._last_refresh: Dict[str, float] = {}

    def update_from_spot_meta(self, spot_meta: Dict) -> int:
        """
        Update registry with fresh data from Hyperliquid spotMeta API.

        Args:
            spot_meta: Response from hl_client._make_request("spotMeta", {})
                       Contains {"tokens": [...], "universe": [...]}

        Returns:
            Number of markets loaded
        """
        try:
            # Parse tokens (individual tokens like USDC, HYPE, etc.)
            tokens = spot_meta.get("tokens", [])
            for token in tokens:
                index = token.get("index")
                name = token.get("name")
                if index is not None and name:
                    self._spot_token_map[index] = name

            self._token_count = len(self._spot_token_map)

            # Parse universe (trading pairs/markets - THIS IS WHAT WE NEED!)
            # Universe entries have "name" like "PURR/USDC", "xyz:MSFT", etc.
            # and "index" which corresponds to the market index used in TWAP data
            universe = spot_meta.get("universe", [])

            for market in universe:
                # Use the API's explicit index field — position in the
                # array is coincidental, the "index" field is the contract
                market_index = market.get("index")
                if market_index is None:
                    continue

                # Get market name - format varies:
                # - Regular spot: "PURR/USDC" -> we want "PURR"
                # - xyz synthetics: "xyz:MSFT" -> we want "xyz:MSFT"
                # - Unnamed markets: "@123" -> resolve from tokens array
                # - Other formats: keep as-is
                market_name = market.get("name", "")

                if market_name:
                    # BUGFIX: Some HIP-1 markets have "@index" as name instead of real name
                    # Resolve these from the tokens array (first token is the base asset)
                    if market_name.startswith("@"):
                        token_indices = market.get("tokens", [])
                        if token_indices:
                            base_token_idx = token_indices[0]
                            resolved_name = self._spot_token_map.get(base_token_idx)
                            if resolved_name:
                                self._spot_market_map[market_index] = resolved_name
                                logger.debug(f"Resolved {market_name} -> {resolved_name} (token idx {base_token_idx})")
                                continue
                        # Fallback: keep the @name if we can't resolve
                        self._spot_market_map[market_index] = market_name
                    # For regular spot pairs like "PURR/USDC", extract base token
                    elif "/" in market_name and not market_name.startswith("xyz:"):
                        base_token = market_name.split("/")[0]
                        self._spot_market_map[market_index] = base_token
                    else:
                        # For xyz:MSFT, vntl:SPACEX, etc. - keep full name
                        self._spot_market_map[market_index] = market_name

            self._market_count = len(self._spot_market_map)
            self._initialized = True

            logger.info(f"Dynamic registry loaded {self._token_count} tokens, {self._market_count} markets")

            # Log some samples for verification
            if logger.isEnabledFor(logging.DEBUG):
                token_samples = list(self._spot_token_map.items())[:5]
                market_samples = list(self._spot_market_map.items())[:10]
                logger.debug(f"Sample tokens: {token_samples}")
                logger.debug(f"Sample markets: {market_samples}")

            return self._market_count

        except Exception as e:
            logger.error(f"Error updating dynamic registry: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return 0

    def update_from_perp_meta(self, meta: Dict) -> int:
        """
        Update perp registry from Hyperliquid 'meta' endpoint.
        universe[i]['name'] maps directly to asset_id i (verified 2026-07-16:
        218=CC, 219=ICP, 229=CHIP, 230=GRAM, 231=CASHCAT).
        """
        try:
            universe = meta.get("universe", [])
            for i, asset in enumerate(universe):
                name = asset.get("name")
                if name:
                    self._perp_market_map[i] = name
            logger.info(f"Perp registry loaded {len(self._perp_market_map)} markets")
            return len(self._perp_market_map)
        except Exception as e:
            logger.error(f"Error updating perp registry: {e}")
            return 0

    def get_coin_name(self, asset_id: int) -> str:
        """
        Get coin symbol from asset ID with dynamic resolution.

        Resolution priority:
        1. Static registry (ASSET_ID_TO_NAME) - for known perps and common spots
        2. HIP-3 market map (dynamic ranges)
        3. Dynamic spot token map - for newer/unknown spot tokens
        4. UNKNOWN_{asset_id} fallback

        Args:
            asset_id: Asset identifier from API

        Returns:
            Coin symbol (e.g., 'BTC', 'HYPE', 'flx:TSLA') or 'UNKNOWN_{asset_id}'
        """
        # 1. Static registry (fastest, keeps existing behavior/overrides)
        if asset_id in ASSET_ID_TO_NAME:
            return ASSET_ID_TO_NAME[asset_id]

        # 2. Dynamic perp map (native perps, asset_id < 10000)
        if 0 <= asset_id < SPOT_START:
            if asset_id in self._perp_market_map:
                return self._perp_market_map[asset_id]
            # New listing since last refresh? One rate-limited retry.
            if self._maybe_refresh("perp"):
                if asset_id in self._perp_market_map:
                    logger.info(f"Resolved new perp listing: {asset_id} -> "
                                f"{self._perp_market_map[asset_id]}")
                    return self._perp_market_map[asset_id]
            return f'UNKNOWN_{asset_id}'

        # 3. HIP-3 dynamic resolution (110000+)
        # NOT gated on self._initialized — that flag belongs to the spot
        # registry; HIP-3 has its own init and must resolve independently.
        if asset_id >= HIP3_START:
            resolved = self._resolve_spot_token(asset_id)
            # Real resolutions are "dex:SYMBOL"; a placeholder like
            # "hyna_2" (range known, symbol missing) is also a miss.
            if resolved and ":" in resolved:
                return resolved
            # Miss: new dex after boot, failed init, or new listing
            # inside a known dex. One rate-limited registry rebuild.
            if self._maybe_refresh("hip3"):
                retry = self._resolve_spot_token(asset_id)
                if retry and ":" in retry:
                    logger.info(f"Resolved HIP-3 after registry rebuild: "
                                f"{asset_id} -> {retry}")
                    return retry
                resolved = retry or resolved
            return resolved or f'UNKNOWN_{asset_id}'

        # 4. Standard spot dynamic resolution (10000-109999)
        if SPOT_START <= asset_id < HIP3_START and self._initialized:
            resolved = self._resolve_spot_token(asset_id)
            if resolved:
                return resolved
            # New spot listing? One rate-limited retry.
            if self._maybe_refresh("spot"):
                resolved = self._resolve_spot_token(asset_id)
                if resolved:
                    logger.info(f"Resolved new spot listing: {asset_id} -> {resolved}")
                    return resolved

        # 5. Fallback
        return f'UNKNOWN_{asset_id}'

    REFRESH_COOLDOWN_S = 600  # max one refresh per scope per 10 min

    def _maybe_refresh(self, scope: str) -> bool:
        """
        Rate-limited registry refresh on lookup miss.
        Returns True if a refresh was performed.
        NOTE: timestamp is set BEFORE the request, so a failing API
        can't be hammered by a burst of misses.
        """
        if self._hl_client is None:
            return False
        now = time.monotonic()
        if now - self._last_refresh.get(scope, 0.0) < self.REFRESH_COOLDOWN_S:
            return False
        self._last_refresh[scope] = now
        try:
            if scope == "perp":
                meta = self._hl_client._make_request("meta", {})
                if meta:
                    self.update_from_perp_meta(meta)
                    return True
            elif scope == "spot":
                spot_meta = self._hl_client.get_spot_meta()
                if spot_meta:
                    self.update_from_spot_meta(spot_meta)
                    return True
            elif scope == "hip3":
                if init_hip3_registry(self._hl_client):
                    return True
        except Exception as e:
            logger.warning(f"Registry refresh ({scope}) failed: {e}")
        return False

    def _resolve_spot_token(self, asset_id: int) -> Optional[str]:
        """
        Resolve a spot/HIP-3 token asset_id to its name.
        Uses dynamically discovered HIP-3 ranges.
        """
        # HIP-3 range (110000+)
        if asset_id >= HIP3_START:
            prefix = None
            market_index = None

            for dex_name, (range_start, range_end) in _hip3_ranges.items():
                if range_start <= asset_id < range_end:
                    prefix = dex_name
                    market_index = asset_id - range_start
                    break

            if prefix is None:
                # Unknown HIP-3 range — new dex deployed after init
                return None

            if prefix in _hip3_market_map:
                if market_index in _hip3_market_map[prefix]:
                    symbol = _hip3_market_map[prefix][market_index]
                    result = f"{prefix}:{symbol}"
                    logger.debug(
                        f"HIP-3 resolved: asset_id {asset_id} -> "
                        f"{prefix} index {market_index} -> {result}"
                    )
                    return result
                else:
                    logger.debug(f"HIP-3 market not found: {prefix} index {market_index}")
                    return f"{prefix}_{market_index}"

            return f"{prefix}_{market_index}"

        # Standard spot range (10000-109999)
        elif SPOT_START <= asset_id < HIP3_START:
            market_index = asset_id - SPOT_START

            if market_index in self._spot_market_map:
                name = self._spot_market_map[market_index]
                logger.debug(f"Resolved asset_id {asset_id} -> market_index {market_index} -> {name}")
                return name

            if market_index in self._spot_token_map:
                name = self._spot_token_map[market_index]
                logger.debug(f"Resolved asset_id {asset_id} via token map -> {name}")
                return name

            logger.debug(f"Could not resolve asset_id {asset_id} (market_index={market_index})")
            return None

        return None

    @property
    def is_initialized(self) -> bool:
        """Check if dynamic registry has been initialized"""
        return self._initialized

    @property
    def token_count(self) -> int:
        """Get number of dynamically loaded tokens"""
        return self._token_count

    @property
    def market_count(self) -> int:
        """Get number of dynamically loaded markets"""
        return self._market_count

    def get_stats(self) -> Dict:
        """Get registry statistics"""
        return {
            "initialized": self._initialized,
            "dynamic_tokens": self._token_count,
            "dynamic_markets": self._market_count,
            "static_tokens": len(ASSET_ID_TO_NAME),
            "total_coverage": self._market_count + len(ASSET_ID_TO_NAME),
            "hip3_markets": {k: len(v) for k, v in _hip3_market_map.items()}
        }


# ============================================================================
# GLOBAL INSTANCE & INITIALIZATION
# ============================================================================

# Global instance for module-level functions
_registry = DynamicCoinRegistry()


def init_hip3_registry(hl_client) -> bool:
    """
    Initialize HIP-3 market mappings DYNAMICALLY.

    Step 1: perpDexs → discover dex names and assign ID ranges
    Step 2: metaAndAssetCtxs per dex → get correct asset order for ID mapping

    Each dex gets a 10K range starting at 110000:
      - dex index 0 → 110000-119999
      - dex index 1 → 120000-129999
      - etc.
    """
    global _hip3_market_map, _hip3_ranges

    try:
        # Step 1: Get dex names from perpDexs
        perp_dexs = hl_client.get_perp_dexs()

        if not perp_dexs:
            logger.error("Failed to fetch perpDexs - HIP-3 resolution disabled "
                         "(will self-heal via _maybe_refresh on first HIP-3 miss)")
            return False

        # Collect dex names in order
        # Discover dex names and assign ranges
        for dex_index, dex in enumerate(perp_dexs):
            if dex is None or not isinstance(dex, dict) or not dex.get("name"):
                continue

            name = dex["name"]
            range_start = HIP3_START + ((dex_index - 1) * 10000)
            range_end = range_start + 10000

            _hip3_ranges[name] = (range_start, range_end)

            logger.info(f"HIP-3 dex '{name}': range {range_start}-{range_end - 1}")

        if not _hip3_ranges:
            logger.warning("No named HIP-3 dexes found")
            return False

        # Step 2: Fetch metaAndAssetCtxs per dex for correct universe order
        loaded_count = 0

        for name, (range_start, range_end) in _hip3_ranges.items():
            try:
                result = hl_client._make_request("metaAndAssetCtxs", {"dex": name})

                if not result or not isinstance(result, list) or len(result) < 2:
                    logger.warning(f"No metaAndAssetCtxs data for dex '{name}'")
                    _hip3_market_map[name] = {}
                    continue

                meta = result[0]
                universe = meta.get("universe", [])

                _hip3_market_map[name] = {}

                for i, asset in enumerate(universe):
                    asset_name = asset.get("name")
                    if asset_name:
                        # Strip dex prefix if present (universe returns "xyz:COIN", we want "COIN")
                        if asset_name.startswith(f"{name}:"):
                            asset_name = asset_name[len(name) + 1:]
                        _hip3_market_map[name][i] = asset_name

                loaded_count += len(_hip3_market_map[name])

                logger.info(
                    f"HIP-3 dex '{name}': {len(_hip3_market_map[name])} assets loaded "
                    f"(universe order)"
                )

                # Log first 5 for verification
                if logger.isEnabledFor(logging.DEBUG):
                    samples = list(_hip3_market_map[name].items())[:5]
                    logger.debug(f"  Sample: {samples}")

            except Exception as e:
                logger.warning(f"Failed to fetch universe for dex '{name}': {e}")
                _hip3_market_map[name] = {}
                continue

        if loaded_count > 0:
            logger.info(
                f"✅ HIP-3 registry initialized: {loaded_count} assets across "
                f"{len(_hip3_ranges)} dexes: {list(_hip3_ranges.keys())}"
            )
            return True
        else:
            logger.warning("No HIP-3 assets loaded")
            return False

    except Exception as e:
        logger.error(f"❌ Failed to initialize HIP-3 registry: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False


def init_dynamic_registry(hl_client) -> bool:
    """
    Initialize the dynamic registry with fresh data from Hyperliquid API.

    Call this ONCE at application startup:

        from coin_registry import init_dynamic_registry
        from api_client.hyperliquid_client import HyperliquidClient

        hl_client = HyperliquidClient()
        init_dynamic_registry(hl_client)

    Args:
        hl_client: HyperliquidClient instance with get_spot_meta() method

    Returns:
        True if initialization succeeded, False otherwise
    """

    _registry._hl_client = hl_client

    # Load perp universe (native perps — previously static-only)
    try:
        meta = hl_client._make_request("meta", {})
        if meta:
            _registry.update_from_perp_meta(meta)
        else:
            logger.warning("Failed to fetch perp meta - new perp listings won't resolve")
    except Exception as e:
        logger.error(f"Failed to initialize perp registry: {e}")

    success = True

    # Initialize spot token registry
    try:
        spot_meta = hl_client.get_spot_meta()

        if not spot_meta:
            logger.warning("Failed to fetch spotMeta - dynamic resolution disabled")
            success = False
        else:
            count = _registry.update_from_spot_meta(spot_meta)
            if count > 0:
                logger.info(f"✅ Dynamic coin registry initialized with {count} spot tokens")
            else:
                logger.warning("spotMeta returned no tokens")
                success = False

    except Exception as e:
        logger.error(f"❌ Failed to initialize dynamic registry: {e}")
        success = False

    # Initialize HIP-3 registry
    try:
        if hasattr(hl_client, 'get_perp_dexs'):
            init_hip3_registry(hl_client)
        else:
            logger.warning("HyperliquidClient missing get_perp_dexs() - HIP-3 resolution disabled")
    except Exception as e:
        logger.warning(f"Failed to initialize HIP-3 registry: {e}")

    return success


def get_registry_status() -> Dict:
    """Get current status of the dynamic registry"""
    stats = _registry.get_stats()
    stats["hip3_loaded"] = len(_hip3_market_map) > 0
    stats["hip3_markets"] = {k: list(v.values()) for k, v in _hip3_market_map.items()}
    stats["hip3_ranges"] = {k: f"{v[0]}-{v[1]-1}" for k, v in _hip3_ranges.items()}
    return stats


# ============================================================================
# HELPER FUNCTIONS (use dynamic resolution)
# ============================================================================

def get_market_type(asset_id: int) -> str:
    """
    Determine if asset is SPOT or PERP based on ID range.

    IMPORTANT: HIP-3 markets are PERP markets, not SPOT!

    Ranges:
        - 0-9999: Standard perps
        - 10000-109999: Spot markets
        - 110000+: HIP-3 builder-deployed perps

    Args:
        asset_id: Asset identifier from API

    Returns:
        'SPOT', 'PERP', or 'UNKNOWN'
    """
    if asset_id >= HIP3_START:
        return 'PERP'  # HIP-3 builder-deployed perps
    elif asset_id >= SPOT_START:
        return 'SPOT'  # Standard spot markets (10000-109999)
    elif asset_id >= 0:
        return 'PERP'  # Standard perps (0-9999)
    else:
        return 'UNKNOWN'


def get_coin_name(asset_id: int) -> str:
    """
    Get coin symbol from asset ID.

    Uses dynamic resolution if initialized, otherwise falls back to static.

    Args:
        asset_id: Asset identifier from API

    Returns:
        Coin symbol (e.g., 'BTC', 'HYPE', 'flx:TSLA') or 'UNKNOWN_{asset_id}' if not found
    """
    return _registry.get_coin_name(asset_id)


def is_spot(asset_id: int) -> bool:
    """
    Check if asset_id represents a SPOT market.

    Note: HIP-3 markets (110000+) are NOT spot, they are perps!
    """
    return SPOT_START <= asset_id < SPOT_END


def is_perp(asset_id: int) -> bool:
    """
    Check if asset_id represents a PERP market (including HIP-3).

    Includes:
        - Standard perps (0-9999)
        - HIP-3 perps (110000+)
    """
    return (0 <= asset_id < SPOT_START) or (asset_id >= HIP3_START)


def is_hip3(asset_id: int) -> bool:
    """Check if asset_id represents a HIP-3 market"""
    return asset_id >= HIP3_START


def get_hip3_type(asset_id: int) -> Optional[str]:
    """
    Get the HIP-3 dex name for an asset ID.
    Uses dynamically discovered ranges.
    """
    if asset_id < HIP3_START:
        return None

    for dex_name, (range_start, range_end) in _hip3_ranges.items():
        if range_start <= asset_id < range_end:
            return dex_name

    return None

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
    """Get statistics about the registry (static + dynamic)"""
    perp_count = sum(1 for aid in ASSET_ID_TO_NAME.keys() if is_perp(aid))
    spot_count = sum(1 for aid in ASSET_ID_TO_NAME.keys() if is_spot(aid))
    unique_coins = len(set(ASSET_ID_TO_NAME.values()))

    stats = {
        'static_assets': len(ASSET_ID_TO_NAME),
        'static_perp_markets': perp_count,
        'static_spot_markets': spot_count,
        'static_unique_coins': unique_coins,
        'dynamic_initialized': _registry.is_initialized,
        'dynamic_tokens': _registry.token_count,
        'dynamic_markets': _registry.market_count,
        'hip3_initialized': len(_hip3_market_map) > 0,
        'hip3_markets': {k: len(v) for k, v in _hip3_market_map.items()}
    }

    return stats


# ============================================================================
# VALIDATION / TEST
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

    # Test HIP-3 markets (without dynamic init)
    print("\n🔍 HIP-3 Markets (market type detection):")
    hip3_ids = [110000, 110005, 120002, 130000]
    for aid in hip3_ids:
        hip3_type = get_hip3_type(aid)
        market_type = get_market_type(aid)
        print(f"  ID {aid}: type={hip3_type}, market={market_type}, is_perp={is_perp(aid)}, is_spot={is_spot(aid)}")

    # Registry stats
    print("\n📊 Registry Statistics:")
    stats = get_registry_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Verify the fix
    print("\n" + "=" * 70)
    print("VERIFICATION: HIP-3 Market Type Fix")
    print("=" * 70)
    print(f"  is_spot(110000) = {is_spot(110000)} (should be False)")
    print(f"  is_perp(110000) = {is_perp(110000)} (should be True)")
    print(f"  get_market_type(110000) = {get_market_type(110000)} (should be PERP)")
    print(f"  get_market_type(10107) = {get_market_type(10107)} (should be SPOT)")

    # Dynamic init test
    print("\n" + "=" * 70)
    print("DYNAMIC REGISTRY TEST")
    print("=" * 70)
    print("\nTo test dynamic resolution, run:")
    print("  from api_client.hyperliquid_client import HyperliquidClient")
    print("  from coin_registry import init_dynamic_registry, get_coin_name")
    print("  ")
    print("  hl = HyperliquidClient()")
    print("  init_dynamic_registry(hl)")
    print("  print(get_coin_name(120002))  # Should show 'flx:TSLA'")

    print("\n✅ Registry tests complete!")
    print("=" * 70)