#!/usr/bin/env python3
"""
Hyperliquid API Client - REFACTORED WITH PROPER LOGGING LEVELS AND PRICE VALIDATION
Provides access to Hyperliquid Info endpoint for trader data

LOGGING STRATEGY:
- DEBUG: Individual price lookups, cache hits, strategy attempts, API calls
- INFO: Important initializations, summaries, cache refreshes
- WARNING: Illiquid tokens, fallback attempts, degraded functionality, suspicious prices
- ERROR: Complete failures, API errors

HIP-3 INTEGRATION (Phase 1):
- get_active_hip3_dexes(): Cached discovery of active HIP-3 dex names
- get_user_state_hip3_async(): Fetch clearinghouseState for a specific HIP-3 dex
- get_all_user_positions_async(): Unified fetch — main perp + all HIP-3 dexes in parallel
"""
import logging
import requests
import aiohttp
import asyncio
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

UNDERLYING_NAME_OVERRIDES = {
    "FART": "FARTCOIN",
}


class HyperliquidClient:
    """Client for Hyperliquid Info API"""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize Hyperliquid client

        Args:
            config: Optional configuration dict with:
                - api_url: Base API URL (default: https://api.hyperliquid.xyz)
                - timeout: Request timeout in seconds (default: 10)
                - rate_limit_delay: Delay between requests (default: 0.5)
        """
        config = config or {}
        self.api_url = config.get('api_url', 'https://api.hyperliquid.xyz')
        self.info_endpoint = f"{self.api_url}/info"
        self.timeout = config.get('timeout', 10)
        self.rate_limit_delay = config.get('rate_limit_delay', 0.5)

        self._all_mids_cache = None
        self._all_mids_cache_time = None
        self._all_mids_cache_ttl = config.get('all_mids_cache_ttl', 5)

        self._spot_token_map = None  # Will be lazy loaded
        self._spot_meta_cache_time = None

        self._token_name_cache = {}

        # =====================================================================
        # HIP-3 CACHES
        # =====================================================================
        self._hip3_dex_cache: Optional[List[str]] = None
        self._hip3_dex_cache_time: Optional[datetime] = None
        self._hip3_dex_cache_ttl = config.get('hip3_dex_cache_ttl', 3600)  # 1 hour
        # =====================================================================

        logger.info(f"Hyperliquid client initialized: {self.api_url}")

    def _make_request(self, request_type: str, params: Dict[str, Any], retry_count: int = 2) -> Optional[Any]:
        """
        Make a request to the Hyperliquid info endpoint

        Args:
            request_type: The type of info request
            params: Additional parameters for the request
            retry_count: Number of retries on failure

        Returns:
            Response data or None on error
        """
        payload = {"type": request_type, **params}

        for attempt in range(retry_count + 1):
            try:
                logger.debug(f"🌐 API call: {request_type} (attempt {attempt + 1}/{retry_count + 1})")

                response = requests.post(
                    self.info_endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout
                )

                logger.debug(f"   Response status: {response.status_code}")

                response.raise_for_status()

                result = response.json()

                # Log response details for critical endpoints
                if request_type == "allMids" and logger.isEnabledFor(logging.DEBUG):
                    if isinstance(result, dict):
                        logger.debug(f"   allMids response: {len(result)} entries")
                    elif isinstance(result, list):
                        logger.debug(f"   allMids response: list with {len(result)} items")
                    else:
                        logger.debug(f"   allMids response type: {type(result)}")

                return result

            except requests.exceptions.Timeout:
                logger.warning(
                    f"⏱️  Timeout for {request_type} "
                    f"(attempt {attempt + 1}/{retry_count + 1})"
                )
                if attempt == retry_count:
                    logger.error(
                        f"❌ {request_type} failed after {retry_count + 1} attempts (timeout)"
                    )
                    return None

            except requests.exceptions.HTTPError as e:
                logger.error(
                    f"❌ HTTP error for {request_type}: {e.response.status_code} - {e.response.text}"
                )
                if attempt == retry_count:
                    return None

            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Request error for {request_type}: {e}")
                if attempt == retry_count:
                    return None

            except ValueError as e:
                # JSON decode error
                logger.error(f"❌ Invalid JSON response for {request_type}: {e}")
                if attempt == retry_count:
                    return None

            except Exception as e:
                logger.error(f"❌ Unexpected error for {request_type}: {e}")
                if attempt == retry_count:
                    return None

        return None

    # =============================================================================
    # User State & Positions
    # =============================================================================

    def get_user_state(self, address: str) -> Optional[Dict]:
        """
        Get user's clearinghouse state (positions, balances, margin)

        Args:
            address: User address in 0x format

        Returns:
            {
                "assetPositions": [...],
                "marginSummary": {
                    "accountValue": "1000.0",
                    "totalNtlPos": "500.0",
                    "totalMarginUsed": "50.0"
                },
                "withdrawable": "950.0",
                ...
            }
        """
        return self._make_request("clearinghouseState", {"user": address})

    def _get_spot_token_map(self, force_refresh: bool = False) -> Dict[str, int]:
        """
        Map token name -> SPOT MARKET index (the universe entry's `index` field,
        which is what all_mids keys spot prices by, e.g. "@107").

        NOT the token's own array index from the tokens[] list — that mis-resolves
        to an unrelated market and produces phantom prices (the WAR-at-$712 bug).
        Empirically confirmed: universe entry `index` field is canonical; array
        position is NOT.

        Only USDC-quoted pairs (quote token index 0) are mapped.
        """
        if (not force_refresh and
                self._spot_token_map is not None and
                self._spot_meta_cache_time is not None):
            age = (datetime.now() - self._spot_meta_cache_time).total_seconds()
            if age < 3600:
                logger.debug(f"📦 Using cached spot token map (age: {age:.1f}s)")
                return self._spot_token_map

        try:
            meta = self.get_spot_meta()
            if not meta:
                logger.warning("Failed to fetch spot metadata")
                return self._spot_token_map or {}

            tokens = meta.get("tokens", [])
            universe = meta.get("universe", [])

            # token array-index -> token name
            idx_to_name = {
                t["index"]: t["name"]
                for t in tokens
                if t.get("name") and t.get("index") is not None
            }

            # name -> spot MARKET index (universe `index` field), USDC pairs only
            token_map = {}
            for pair in universe:
                pair_tokens = pair.get("tokens", [])
                market_index = pair.get("index")
                if market_index is None or len(pair_tokens) != 2:
                    continue
                base_idx, quote_idx = pair_tokens
                if quote_idx != 0:  # quote must be USDC (token index 0)
                    continue
                base_name = idx_to_name.get(base_idx)
                if base_name and base_name not in token_map:
                    token_map[base_name] = market_index

            self._spot_token_map = token_map
            self._spot_meta_cache_time = datetime.now()
            logger.info(f"Loaded spot token map with {len(token_map)} USDC-quoted markets")
            logger.debug(f"Sample: {list(token_map.items())[:5]}")
            return token_map

        except Exception as e:
            logger.error(f"Error building spot token map: {e}")
            return self._spot_token_map or {}

    def get_spot_clearinghouse_state(self, address: str) -> Optional[Dict]:
        """
        Get user's spot clearinghouse state (spot token balances)

        Args:
            address: User address in 0x format

        Returns:
            {
                "balances": [
                    {
                        "coin": "USDC",
                        "token": "0x...",
                        "hold": "100.0",      # Amount locked in orders
                        "total": "1000.0",     # Total balance
                        "entryNtl": "1000.0"   # Cost basis
                    }
                ]
            }
        """
        return self._make_request("spotClearinghouseState", {"user": address})

    def get_user_role(self, address: str) -> Optional[Dict]:
        """
        Get user's role type

        Args:
            address: User address in 0x format

        Returns:
            {"role": "user" | "agent" | "vault" | "subAccount" | "missing"}
        """
        return self._make_request("userRole", {"user": address})

    # =============================================================================
    # Trading History
    # =============================================================================

    def get_user_fills(
            self,
            address: str,
            aggregate_by_time: bool = False
    ) -> Optional[List[Dict]]:
        """
        Get user's recent fills (up to 2000 most recent)

        Args:
            address: User address in 0x format
            aggregate_by_time: Combine partial fills for crossing orders

        Returns:
            List of fills with px, sz, side, time, closedPnl, etc
        """
        return self._make_request("userFills", {
            "user": address,
            "aggregateByTime": aggregate_by_time
        })

    def get_user_fills_by_time(
            self,
            address: str,
            start_time: int,
            end_time: Optional[int] = None,
            aggregate_by_time: bool = False
    ) -> Optional[List[Dict]]:
        """
        Get user's fills within a time range

        Args:
            address: User address in 0x format
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds (None = now)
            aggregate_by_time: Combine partial fills

        Returns:
            List of fills in time range (up to 2000)
        """
        params = {
            "user": address,
            "startTime": start_time,
            "aggregateByTime": aggregate_by_time
        }
        if end_time:
            params["endTime"] = end_time

        return self._make_request("userFillsByTime", params)

    def get_twap_slice_fills(self, address: str) -> Optional[List[Dict]]:
        """
        Get user's TWAP slice fills (up to 2000 most recent)

        Args:
            address: User address in 0x format

        Returns:
            List of TWAP fills with twapId and fill details
        """
        return self._make_request("userTwapSliceFills", {"user": address})

    # =============================================================================
    # Orders
    # =============================================================================

    def get_open_orders(self, address: str) -> Optional[List[Dict]]:
        """
        Get user's currently open orders

        Args:
            address: User address in 0x format

        Returns:
            List of open orders
        """
        return self._make_request("openOrders", {"user": address})

    def get_frontend_open_orders(self, address: str) -> Optional[List[Dict]]:
        """
        Get user's open orders with additional frontend info

        Args:
            address: User address in 0x format

        Returns:
            List of open orders with extended info
        """
        return self._make_request("frontendOpenOrders", {"user": address})

    def get_historical_orders(self, address: str) -> Optional[List[Dict]]:
        """
        Get user's historical orders (up to 2000 most recent)

        Args:
            address: User address in 0x format

        Returns:
            List of orders with status (filled, canceled, etc)
        """
        return self._make_request("historicalOrders", {"user": address})

    def get_order_status(self, address: str, oid: int) -> Optional[Dict]:
        """
        Get status of a specific order

        Args:
            address: User address in 0x format
            oid: Order ID or client order ID

        Returns:
            Order details with current status
        """
        return self._make_request("orderStatus", {
            "user": address,
            "oid": oid
        })

    # =============================================================================
    # Portfolio & Performance
    # =============================================================================

    def get_user_portfolio(self, address: str) -> Optional[List]:
        """
        Get user's portfolio history (PnL over time)

        Args:
            address: User address in 0x format

        Returns:
            List of [period, data] tuples for day/week/month/allTime
        """
        return self._make_request("portfolio", {"user": address})

    def get_user_fees(self, address: str) -> Optional[Dict]:
        """
        Get user's fee information and tier

        Args:
            address: User address in 0x format

        Returns:
            Fee rates, daily volume, tier info
        """
        return self._make_request("userFees", {"user": address})

    def get_referral_info(self, address: str) -> Optional[Dict]:
        """
        Get user's referral information

        Args:
            address: User address in 0x format

        Returns:
            Cumulative volume, rewards, referral code
        """
        return self._make_request("referral", {"user": address})

    # =============================================================================
    # Account Relationships
    # =============================================================================

    def get_sub_accounts(self, address: str) -> Optional[List[Dict]]:
        """
        Get user's subaccounts

        Args:
            address: User address in 0x format

        Returns:
            List of subaccounts with their states
        """
        return self._make_request("subAccounts", {"user": address})

    # =============================================================================
    # Vault Methods
    # =============================================================================

    def get_vault_details(
            self,
            vault_address: str,
            user: Optional[str] = None
    ) -> Optional[Dict]:
        """Get detailed information about a specific vault"""
        params = {"vaultAddress": vault_address}
        if user:
            params["user"] = user
        return self._make_request("vaultDetails", params)

    def get_user_vault_equities(self, address: str):
        """Get user's vault equity positions"""
        return self._make_request("userVaultEquities", {"user": address})

    def get_vault_equities(self, address: str):
        """Get user's vault deposits (legacy name)"""
        return self._get_user_vault_equities(address)

    # =============================================================================
    # Market Data
    # =============================================================================

    def get_all_mids(self, use_cache: bool = True) -> Optional[Dict[str, str]]:
        """
        Get mid prices for all assets (perps + spot)

        Cache TTL is configurable (default 5 seconds)

        Args:
            use_cache: If True, return cached data if fresh enough

        Returns:
            Dict of {coin: price} where:
            - Perp coins: "BTC", "ETH", "HYPE", "kBONK", etc.
            - Spot coins: "@1", "@150", "@223", etc. (@ + spot index)
        """
        # Check cache freshness
        if use_cache and self._all_mids_cache is not None and self._all_mids_cache_time is not None:
            age_seconds = (datetime.now() - self._all_mids_cache_time).total_seconds()

            if age_seconds < self._all_mids_cache_ttl:
                logger.debug(f"📦 Using cached all_mids (age: {age_seconds:.1f}s)")
                return self._all_mids_cache
            else:
                logger.debug(f"🔄 Cache expired (age: {age_seconds:.1f}s), refreshing...")

        # Fetch fresh data
        result = self._make_request("allMids", {})

        if result:
            # Update cache
            self._all_mids_cache = result
            self._all_mids_cache_time = datetime.now()
            logger.debug(
                f"✅ Cached fresh all_mids data: {len(result)} entries, "
                f"TTL={self._all_mids_cache_ttl}s"
            )
        else:
            logger.warning(f"⚠️  Failed to fetch all_mids, cache not updated")
            # Return stale cache if available (better than nothing)
            if self._all_mids_cache is not None:
                logger.warning(f"⚠️  Returning stale cache as fallback")
                return self._all_mids_cache

        return result

    def get_l2_book(
            self,
            coin: str,
            n_sig_figs: Optional[int] = None
    ) -> Optional[List]:
        """
        Get L2 order book snapshot

        Args:
            coin: Asset name (e.g., "BTC", "HYPE")
            n_sig_figs: Aggregation level (2-5 or None for full precision)

        Returns:
            [bids, asks] with price levels
        """
        params = {"coin": coin}
        if n_sig_figs is not None:
            params["nSigFigs"] = n_sig_figs
        return self._make_request("l2Book", params)

    def get_candles(
            self,
            coin: str,
            interval: str,
            start_time: int,
            end_time: Optional[int] = None
    ) -> Optional[List[Dict]]:
        """
        Get candle data for an asset

        Args:
            coin: Asset name
            interval: "1m", "5m", "15m", "1h", "4h", "1d", etc
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds

        Returns:
            List of candles (up to 5000 most recent)
        """
        req = {
            "coin": coin,
            "interval": interval,
            "startTime": start_time
        }
        if end_time:
            req["endTime"] = end_time

        return self._make_request("candleSnapshot", {"req": req})

    def get_spot_price(self, token: str, quote: str = "USDC") -> Optional[float]:
        """
        Get current mid price for a spot token pair from L2 order book

        Args:
            token: Token name (e.g., "HYPE", "PURR")
            quote: Quote currency (default: "USDC")

        Returns:
            Mid price or None if order book is empty
        """
        try:
            # Determine the coin identifier
            if token.startswith('@'):
                # Already in index format
                coin = token
            elif token in ["USDC", "USDT", "USD"]:
                # Stablecoins don't need price lookup
                logger.debug(f"⚠️  {token} is a stablecoin, returning 1.0")
                return 1.0
            else:
                # Look up the index for this token
                token_map = self._get_spot_token_map()

                if token not in token_map:
                    logger.warning(f"⚠️  Token {token} not found in spot metadata")
                    return None

                index = token_map[token]
                coin = f"@{index}"
                logger.debug(f"📍 Mapped {token} -> {coin}")

            # Fetch orderbook
            book = self.get_l2_book(coin)

            if not book:
                logger.debug(f"❌ {coin}: book is None or empty")
                return None

            # Handle response structure
            if isinstance(book, dict):
                levels = book.get('levels')
            else:
                levels = book

            if not levels or len(levels) < 2:
                logger.debug(f"❌ {coin}: insufficient levels")
                return None

            bids = levels[0]
            asks = levels[1]

            if not bids or not asks:
                logger.debug(f"❌ {coin}: empty bids or asks")
                return None

            best_bid = float(bids[0]['px'])
            best_ask = float(asks[0]['px'])
            mid = (best_bid + best_ask) / 2

            logger.debug(f"✅ {token} ({coin}): bid={best_bid}, ask={best_ask}, mid={mid}")
            return mid

        except Exception as e:
            logger.warning(f"❌ Failed to get spot price for {token}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def get_spot_meta(self) -> Optional[Dict]:
        """
        Get spot token metadata (indices, names, tokens)

        Returns:
            {
                "tokens": [
                    {"index": 0, "name": "USDC", "szDecimals": 6, ...},
                    {"index": 150, "name": "HYPE", "szDecimals": 8, ...},
                    ...
                ],
                "universe": [...]
            }
        """
        return self._make_request("spotMeta", {})

    def _validate_price(self, token: str, price: float) -> float:
        """
        Validate and log warnings for suspicious prices

        Args:
            token: Token name
            price: Price to validate

        Returns:
            The same price (warnings only, doesn't filter)
        """
        # SANITY CHECK: Flag absurdly high prices
        if price > 50000:
            logger.debug(
                f"⚠️  {token}: Suspicious high price ${price:,.2f} "
                f"(>$50k threshold) - likely illiquid/stale data"
            )

        # SANITY CHECK: Flag suspiciously exact $1.00 for non-stablecoins
        if 0.9999 <= price <= 1.0001:
            logger.debug(
                f"⚠️  {token}: Suspicious $1.00 price - likely stale/default data"
            )

        return price

    def get_token_price(self, token: str) -> Optional[float]:
        """
        Get best available price for any token using multi-strategy approach
        NOW WITH PRICE VALIDATION

        Priority waterfall:
        1. Stablecoins (1:1 USD)
        2. Bridged assets (U-prefix): 5 strategies
        3. Direct perp lookup
        4. Direct spot lookup via index
        5. Orderbook fallback (slowest, last resort)

        Args:
            token: Token name (e.g., "HYPE", "PURR", "UBTC", "PUMP")

        Returns:
            Price or None if not available (illiquid/delisted)
        """
        try:
            # =================================================================
            # STRATEGY 0: Stablecoins (always 1:1 USD)
            # =================================================================
            if token in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0"]:
                logger.debug(f"✅ {token}: stablecoin = $1.00")
                return 1.0

            # =================================================================
            # Get all_mids (cached for performance)
            # =================================================================
            all_mids = self.get_all_mids()

            if all_mids is None:
                logger.error(f"❌ {token}: all_mids API call returned None (API failure)")
                # Try orderbook fallback immediately
                return self.get_spot_price_from_orderbook(token)

            if not all_mids:
                logger.error(f"❌ {token}: all_mids returned empty dict")
                return self.get_spot_price_from_orderbook(token)

            logger.debug(f"✅ all_mids loaded: {len(all_mids)} price entries")

            # =================================================================
            # BRIDGED ASSETS (U-prefix): Multi-strategy approach
            # =================================================================
            if token.startswith('U') and len(token) > 1:
                underlying = token[1:]  # UBONK -> BONK, UFART -> FART

                logger.debug(f"🔍 {token}: Detected bridged asset, underlying = '{underlying}'")

                # -----------------------------------------------------------------
                # STRATEGY 1: Exact underlying name in perps
                # -----------------------------------------------------------------
                logger.debug(f"   Strategy 1: Exact perp match '{underlying}'")

                underlying_lookup = UNDERLYING_NAME_OVERRIDES.get(underlying, underlying)
                logger.debug(f"   Strategy 1: Exact perp match '{underlying_lookup}'")

                if underlying_lookup in all_mids:
                    price = all_mids[underlying_lookup]
                    price_float = float(price)
                    logger.debug(f"💱 {token}: Strategy 1 SUCCESS - '{underlying_lookup}' perp = ${price_float:,.6f}")
                    return self._validate_price(token, price_float)
                elif underlying != underlying_lookup:
                    logger.debug(f"   ❌ '{underlying_lookup}' (override) not in perps")
                else:
                    logger.debug(f"   ❌ '{underlying}' not in perps")

                # -----------------------------------------------------------------
                # STRATEGY 2: k-prefix variant (for scaled tokens)
                # -----------------------------------------------------------------
                k_variant = f"k{underlying}"
                logger.debug(f"   Strategy 2: k-prefix match '{k_variant}'")

                if k_variant in all_mids:
                    price = all_mids[k_variant]
                    price_float = float(price)
                    logger.debug(f"💱 {token}: Strategy 2 SUCCESS - '{k_variant}' perp = ${price_float:,.6f}")
                    # Cache this mapping for future lookups
                    self._token_name_cache[underlying] = k_variant
                    return self._validate_price(token, price_float)
                else:
                    logger.debug(f"   ❌ '{k_variant}' not in perps")

                # -----------------------------------------------------------------
                # STRATEGY 3: Fuzzy match in perps (cached)
                # -----------------------------------------------------------------
                logger.debug(f"   Strategy 3: Fuzzy perp match for '{underlying}'")

                # ADD THIS CHECK:
                if len(underlying) < 3:
                    logger.debug(f"   ⚠️  Skipping fuzzy perp match - '{underlying}' too short (min 3 chars)")
                else:
                    fuzzy_match = self._find_similar_token_in_all_mids(underlying, all_mids)
                    if fuzzy_match and fuzzy_match not in [underlying, k_variant]:
                        price = all_mids[fuzzy_match]
                        price_float = float(price)
                        logger.debug(
                            f"💱 {token}: Strategy 3 SUCCESS - '{fuzzy_match}' via fuzzy = ${price_float:,.6f}"
                        )
                        return self._validate_price(token, price_float)
                    else:
                        logger.debug(f"   ❌ No fuzzy perp match found")

                # -----------------------------------------------------------------
                # STRATEGY 4: Underlying spot market via index
                # -----------------------------------------------------------------
                logger.debug(f"   Strategy 4: Spot index lookup for '{underlying}'")

                underlying_spot_index = self._get_spot_token_index(underlying)

                if underlying_spot_index is not None:
                    spot_key = f"@{underlying_spot_index}"
                    logger.debug(f"   Found spot index {underlying_spot_index}, checking '{spot_key}'")

                    if spot_key in all_mids:
                        price = all_mids[spot_key]
                        price_float = float(price)
                        logger.debug(
                            f"💱 {token}: Strategy 4 SUCCESS - '{underlying}' spot = ${price_float:,.6f}"
                        )
                        return self._validate_price(token, price_float)
                    else:
                        logger.debug(f"   ❌ '{spot_key}' not in all_mids (illiquid spot)")
                else:
                    logger.debug(f"   ❌ '{underlying}' not in spot metadata")

                # -----------------------------------------------------------------
                # STRATEGY 5: Fuzzy match on spot token names
                # -----------------------------------------------------------------
                logger.debug(f"   Strategy 5: Fuzzy spot name match for '{underlying}'")

                spot_tokens = self._get_spot_token_map()
                fuzzy_spot = self._find_similar_token_name(underlying, list(spot_tokens.keys()))

                if fuzzy_spot and fuzzy_spot != underlying:
                    logger.debug(f"   Found fuzzy spot match: '{fuzzy_spot}'")
                    fuzzy_spot_index = spot_tokens.get(fuzzy_spot)

                    if fuzzy_spot_index is not None:
                        spot_key = f"@{fuzzy_spot_index}"

                        if spot_key in all_mids:
                            price = all_mids[spot_key]
                            price_float = float(price)
                            logger.debug(
                                f"💱 {token}: Strategy 5 SUCCESS - '{fuzzy_spot}' fuzzy spot = ${price_float:,.6f}"
                            )
                            return self._validate_price(token, price_float)
                        else:
                            logger.debug(f"   ❌ '{spot_key}' not in all_mids")
                else:
                    logger.debug(f"   ❌ No fuzzy spot match found")

                # All bridged asset strategies failed
                logger.debug(
                    f"⚠️  {token}: All 5 strategies FAILED for underlying '{underlying}' "
                    f"(tried: exact perp, k-prefix, fuzzy perp, spot index, fuzzy spot)"
                )

            # =================================================================
            # NON-BRIDGED TOKENS: Direct lookups
            # =================================================================

            # -----------------------------------------------------------------
            # STEP 1: Direct perp price
            # -----------------------------------------------------------------
            logger.debug(f"🔍 {token}: Checking direct perp price")

            if token in all_mids:
                price = all_mids[token]
                price_float = float(price)
                # DEBUG for individual price lookups
                logger.debug(f"📊 {token}: Found perp price = ${price_float:,.6f}")
                return self._validate_price(token, price_float)
            else:
                logger.debug(f"   ❌ '{token}' not in perps")

            # -----------------------------------------------------------------
            # STEP 2: Spot price via index
            # -----------------------------------------------------------------
            logger.debug(f"🔍 {token}: Checking spot price via index")

            token_index = self._get_spot_token_index(token)

            if token_index is not None:
                spot_key = f"@{token_index}"
                logger.debug(f"   Found spot index {token_index}, checking '{spot_key}'")

                if spot_key in all_mids:
                    price = all_mids[spot_key]
                    price_float = float(price)
                    # DEBUG for individual price lookups
                    logger.debug(f"📈 {token}: Found spot price = ${price_float:,.6f}")
                    return self._validate_price(token, price_float)
                else:
                    logger.debug(
                        f"⚠️  {token}: Has spot index {token_index} but '@{token_index}' "
                        f"not in all_mids (likely illiquid - no recent trades)"
                    )
            else:
                logger.debug(f"   ❌ '{token}' not in spot metadata")

            # -----------------------------------------------------------------
            # STEP 3: Orderbook fallback (slow, last resort)
            # -----------------------------------------------------------------
            logger.debug(
                f"⚠️  {token}: Not found in all_mids anywhere, trying orderbook fallback..."
            )

            orderbook_price = self.get_spot_price_from_orderbook(token)

            if orderbook_price:
                # INFO level when fallback succeeds (unusual but important)
                logger.info(f"📖 {token}: Got price from orderbook fallback = ${orderbook_price:,.6f}")
                return self._validate_price(token, orderbook_price)

            # -----------------------------------------------------------------
            # Complete failure - token is illiquid or delisted
            # -----------------------------------------------------------------
            logger.debug(
                f"❌ {token}: NO PRICE AVAILABLE - all strategies exhausted "
                f"(likely illiquid or delisted)"
            )
            return None

        except Exception as e:
            logger.debug(f"❌ {token}: Unexpected error in get_token_price: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    def _find_similar_token_in_all_mids(
            self,
            token: str,
            all_mids: Dict[str, str]
    ) -> Optional[str]:
        """
        Find a similar token name in all_mids (perp markets only)
        Uses intelligent caching to avoid repeated lookups
        """
        # Check cache first
        if token in self._token_name_cache:
            cached_name = self._token_name_cache[token]
            if cached_name in all_mids:
                logger.debug(f"📦 Cache hit: '{token}' -> '{cached_name}'")
                return cached_name
            else:
                # Cached name no longer valid (token delisted?), clear it
                logger.debug(f"🗑️  Cache miss: '{token}' -> '{cached_name}' no longer valid")
                del self._token_name_cache[token]

        token_upper = token.upper()

        # Exact match (no fuzzy needed)
        if token in all_mids:
            return token

        # Get perp tokens only (exclude spot indices like @123)
        perp_keys = [k for k in all_mids.keys() if not k.startswith('@')]

        # Find tokens containing the search term
        matches = [k for k in perp_keys if token_upper in k.upper()]

        if not matches:
            logger.debug(f"   No perp tokens contain '{token}'")
            return None

        logger.debug(f"   Found {len(matches)} potential matches: {matches}")

        # Single match - easy
        if len(matches) == 1:
            match = matches[0]
            logger.debug(f"🔍 Fuzzy matched '{token}' -> '{match}' (only match)")
            # Cache it
            self._token_name_cache[token] = match
            return match

        # Multiple matches - use priority rules

        # Priority 1: Case-insensitive exact match
        for match in matches:
            if match.upper() == token_upper:
                logger.debug(f"🔍 Fuzzy matched '{token}' -> '{match}' (case-insensitive exact)")
                self._token_name_cache[token] = match
                return match

        # Priority 2: Token that ends with our search term
        suffix_matches = [m for m in matches if m.upper().endswith(token_upper)]
        if suffix_matches:
            match = suffix_matches[0]
            logger.debug(f"🔍 Fuzzy matched '{token}' -> '{match}' (suffix match)")
            self._token_name_cache[token] = match
            return match

        # Priority 3: Token that starts with our search term
        prefix_matches = [m for m in matches if m.upper().startswith(token_upper)]
        if prefix_matches:
            match = prefix_matches[0]
            logger.debug(f"🔍 Fuzzy matched '{token}' -> '{match}' (prefix match)")
            self._token_name_cache[token] = match
            return match

        # Priority 4: Shortest match (most likely to be correct)
        match = min(matches, key=len)
        logger.debug(
            f"🔍 Fuzzy matched '{token}' -> '{match}' "
            f"(shortest of {len(matches)} matches)"
        )
        self._token_name_cache[token] = match
        return match

    def _find_similar_token_name(
            self,
            token: str,
            token_names: List[str]
    ) -> Optional[str]:
        """Find similar token name in a list (for spot token names)"""
        token_upper = token.upper()

        # Exact match
        if token in token_names:
            return token

        # Find similar names
        matches = [name for name in token_names if token_upper in name.upper()]

        if not matches:
            return None

        if len(matches) == 1:
            return matches[0]

        # Priority 1: Case-insensitive exact match
        for match in matches:
            if match.upper() == token_upper:
                return match

        # Priority 2: Suffix match
        for match in matches:
            if match.upper().endswith(token_upper):
                return match

        # Priority 3: Prefix match
        for match in matches:
            if match.upper().startswith(token_upper):
                return match

        # Priority 4: Shortest match
        return min(matches, key=len)

    def _get_spot_token_index(self, token: str) -> Optional[int]:
        """Get spot token index from cached metadata"""
        token_map = self._get_spot_token_map()
        return token_map.get(token)

    def get_spot_price_from_orderbook(self, token: str) -> Optional[float]:
        """
        Get spot price from L2 orderbook (fallback method)
        This is slower - only use when all_mids doesn't have the price
        """
        try:
            # Get token index
            token_index = self._get_spot_token_index(token)
            if token_index is None:
                logger.debug(f"❌ {token}: not found in spot metadata")
                return None

            coin = f"@{token_index}"

            # Fetch orderbook
            book = self.get_l2_book(coin)

            if not book:
                logger.debug(f"❌ {coin}: book is None or empty")
                return None

            # Handle response structure
            if isinstance(book, dict):
                levels = book.get('levels')
            else:
                levels = book

            if not levels or len(levels) < 2:
                logger.debug(f"❌ {coin}: insufficient levels")
                return None

            bids = levels[0]
            asks = levels[1]

            if not bids or not asks:
                logger.debug(f"❌ {coin}: empty bids or asks")
                return None

            best_bid = float(bids[0]['px'])
            best_ask = float(asks[0]['px'])
            mid = (best_bid + best_ask) / 2

            logger.debug(f"✅ {token} ({coin}): orderbook mid = ${mid:,.6f}")
            return mid

        except Exception as e:
            logger.warning(f"❌ Failed to get orderbook price for {token}: {e}")
            return None

    def get_perp_dexs(self) -> Optional[List]:
        """
        Get HIP-3 perp dex metadata (xyz, flx, vntl)

        Returns list of dex info with asset mappings for tokenized equities
        """
        return self._make_request("perpDexs", {})

    def get_meta_and_asset_ctxs(self) -> Optional[Dict]:
        """
        Get perp metadata + asset contexts (funding, OI, volume, prices)

        Single API call returns all market data for all perps.

        Returns:
            {
                'meta': {universe: [...], marginTables: [...]},
                'asset_ctxs': {
                    'BTC': {funding, open_interest, day_ntl_vlm, mark_px, ...},
                    'ETH': {...},
                    ...
                }
            }
            or None on error
        """
        result = self._make_request("metaAndAssetCtxs", {})

        if not result or len(result) < 2:
            logger.warning("Failed to get metaAndAssetCtxs")
            return None

        meta = result[0]
        raw_ctxs = result[1]
        universe = meta.get('universe', [])

        # Zip universe names with contexts
        asset_ctxs = {}
        for i, asset in enumerate(universe):
            name = asset.get('name')
            if name and i < len(raw_ctxs):
                ctx = raw_ctxs[i]
                asset_ctxs[name] = {
                    'funding': float(ctx.get('funding') or 0),
                    'open_interest': float(ctx.get('openInterest') or 0),
                    'day_ntl_vlm': float(ctx.get('dayNtlVlm') or 0),
                    'mark_px': float(ctx.get('markPx') or 0),
                    'oracle_px': float(ctx.get('oraclePx') or 0),
                    'mid_px': float(ctx.get('midPx') or 0) if ctx.get('midPx') else None,
                    'prev_day_px': float(ctx.get('prevDayPx') or 0),
                    'premium': float(ctx.get('premium') or 0) if ctx.get('premium') else None,
                    'is_delisted': asset.get('isDelisted', False),
                    'max_leverage': asset.get('maxLeverage', 0),
                }

        # DEBUG: Log which coins we got
        logger.info(f"📋 API returned {len(asset_ctxs)} perpetual markets")

        # Check for specific missing coins that we saw on Hypurrscan
        missing_coins = ['ZEREBRO', 'BNB', 'XPL', 'WLFI', 'IP', 'ASTER']
        found = [c for c in missing_coins if c in asset_ctxs]
        not_found = [c for c in missing_coins if c not in asset_ctxs]

        if found:
            logger.info(f"   ✅ Found: {found}")
        if not_found:
            logger.info(f"   ❌ Missing: {not_found}")

        # Log all coins at DEBUG level
        logger.debug(f"   All coins: {sorted(asset_ctxs.keys())}")

        return {
            'meta': meta,
            'asset_ctxs': asset_ctxs
        }

    # =========================================================================
    # HIP-3 MARKET DATA METHODS (existing)
    # =========================================================================

    def get_hip3_mids(self) -> Dict[str, str]:
        """
        Get mark prices for ALL active HIP-3 dexes (xyz, flx, vntl, etc.)

        Returns prices in the same format as allMids: {"xyz:TSLA": "399.71", ...}
        so they can be merged directly into the allMids price dict.

        Uses perpDexs to discover active dex names, then fetches metaAndAssetCtxs
        for each one.

        Returns:
            Dict of {"dex:ASSET": "price_string", ...}
            Empty dict on error (never None — safe to merge)
        """
        hip3_prices = {}

        try:
            # Use cached dex list
            dex_names = self.get_active_hip3_dexes()

            if not dex_names:
                logger.debug("No active HIP-3 dexes found")
                return hip3_prices

            logger.debug(f"Fetching prices for {len(dex_names)} HIP-3 dexes: {dex_names}")

            # Fetch metaAndAssetCtxs for each dex
            for dex_name in dex_names:
                try:
                    result = self._make_request("metaAndAssetCtxs", {"dex": dex_name})

                    if not result or not isinstance(result, list) or len(result) < 2:
                        logger.debug(f"No data for HIP-3 dex '{dex_name}'")
                        continue

                    meta = result[0]
                    ctxs = result[1]
                    universe = meta.get("universe", [])

                    for i, asset in enumerate(universe):
                        name = asset.get("name")
                        is_delisted = asset.get("isDelisted", False)

                        if not name or is_delisted:
                            continue

                        if i < len(ctxs):
                            mark_px = ctxs[i].get("markPx")
                            if mark_px:
                                # Key format: "xyz:TSLA" — matches coin_registry format
                                clean_name = name.split(":", 1)[-1] if ":" in name else name
                                full_key = f"{dex_name}:{clean_name}"
                                hip3_prices[full_key] = str(mark_px)

                    active_count = sum(
                        1 for a in universe if not a.get("isDelisted", False)
                    )
                    logger.info(
                        f"HIP-3 '{dex_name}': {active_count} active assets, "
                        f"{len([k for k in hip3_prices if k.startswith(dex_name)])} prices loaded"
                    )

                except Exception as e:
                    logger.warning(f"Failed to fetch HIP-3 dex '{dex_name}': {e}")
                    continue

            logger.info(f"📊 HIP-3 total: {len(hip3_prices)} prices across {len(dex_names)} dexes")

        except Exception as e:
            logger.error(f"Error fetching HIP-3 mids: {e}")

        return hip3_prices

    def get_hip3_meta_and_asset_ctxs(self, dex: str) -> Optional[Dict]:
        """
        Get perp metadata + asset contexts for a specific HIP-3 dex.

        Same structure as get_meta_and_asset_ctxs() but for builder-deployed
        perp dexes (xyz, flx, vntl).

        Args:
            dex: Dex name (e.g., "xyz", "flx", "vntl")

        Returns:
            {
                'dex': 'xyz',
                'meta': {universe: [...], ...},
                'asset_ctxs': {
                    'xyz:TSLA': {funding, open_interest, day_ntl_vlm, mark_px, ...},
                    'xyz:NVDA': {...},
                    ...
                }
            }
            or None on error
        """
        result = self._make_request("metaAndAssetCtxs", {"dex": dex})

        if not result or not isinstance(result, list) or len(result) < 2:
            logger.warning(f"Failed to get metaAndAssetCtxs for HIP-3 dex '{dex}'")
            return None

        meta = result[0]
        raw_ctxs = result[1]
        universe = meta.get("universe", [])

        # Zip universe names with contexts — prefix with dex name
        asset_ctxs = {}
        for i, asset in enumerate(universe):
            name = asset.get("name")
            if name and i < len(raw_ctxs):
                ctx = raw_ctxs[i]
                clean_name = name.split(":", 1)[-1] if ":" in name else name
                full_key = f"{dex}:{clean_name}"
                asset_ctxs[full_key] = {
                    'funding': float(ctx.get('funding') or 0),
                    'open_interest': float(ctx.get('openInterest') or 0),
                    'day_ntl_vlm': float(ctx.get('dayNtlVlm') or 0),
                    'mark_px': float(ctx.get('markPx') or 0),
                    'oracle_px': float(ctx.get('oraclePx') or 0),
                    'mid_px': float(ctx.get('midPx') or 0) if ctx.get('midPx') else None,
                    'prev_day_px': float(ctx.get('prevDayPx') or 0),
                    'premium': float(ctx.get('premium') or 0) if ctx.get('premium') else None,
                    'is_delisted': asset.get('isDelisted', False),
                    'max_leverage': asset.get('maxLeverage', 0),
                }

        active = sum(1 for v in asset_ctxs.values() if not v.get('is_delisted'))
        logger.info(f"📋 HIP-3 '{dex}': {active} active / {len(asset_ctxs)} total markets")

        return {
            'dex': dex,
            'meta': meta,
            'asset_ctxs': asset_ctxs
        }

    # =========================================================================
    # HIP-3 WHALE TRACKING METHODS (NEW — Phase 1)
    # =========================================================================

    def get_active_hip3_dexes(self, force_refresh: bool = False) -> List[str]:
        """
        Get list of active HIP-3 dex names, cached for 1 hour.

        Dex names change rarely (new deployments are infrequent), so
        caching avoids a wasted API call every cycle.

        Args:
            force_refresh: Force refresh the cache

        Returns:
            List of dex name strings, e.g. ["xyz", "flx", "vntl"]
            Empty list on error (never None — safe to iterate)
        """
        # Check cache
        if (not force_refresh
                and self._hip3_dex_cache is not None
                and self._hip3_dex_cache_time is not None):
            age = (datetime.now() - self._hip3_dex_cache_time).total_seconds()
            if age < self._hip3_dex_cache_ttl:
                logger.debug(f"📦 Using cached HIP-3 dex list (age: {age:.0f}s): {self._hip3_dex_cache}")
                return self._hip3_dex_cache

        # Fetch fresh
        try:
            perp_dexs = self.get_perp_dexs()

            if not perp_dexs:
                logger.debug("perpDexs returned empty — no HIP-3 dexes active")
                self._hip3_dex_cache = []
                self._hip3_dex_cache_time = datetime.now()
                return []

            dex_names = []
            for dex in perp_dexs:
                if dex and isinstance(dex, dict):
                    name = dex.get("name")
                    if name:
                        dex_names.append(name)

            self._hip3_dex_cache = dex_names
            self._hip3_dex_cache_time = datetime.now()

            logger.info(f"🏗️  HIP-3 dex list refreshed: {dex_names} ({len(dex_names)} active)")
            return dex_names

        except Exception as e:
            logger.error(f"Error fetching HIP-3 dex list: {e}")
            # Return stale cache if available
            if self._hip3_dex_cache is not None:
                logger.warning("Returning stale HIP-3 dex cache")
                return self._hip3_dex_cache
            return []

    async def get_user_state_hip3_async(
            self,
            address: str,
            dex: str,
            session: aiohttp.ClientSession
    ) -> Optional[Dict]:
        """
        Fetch user's clearinghouseState for a specific HIP-3 dex.

        Same response format as regular clearinghouseState but only
        contains positions on the specified HIP-3 dex.

        Args:
            address: User address in 0x format
            dex: HIP-3 dex name (e.g., "xyz", "flx", "vntl")
            session: Shared aiohttp session

        Returns:
            clearinghouseState dict with HIP-3 positions, or None on error.
            Position coins will be in "dex:COIN" format (e.g., "xyz:TSLA").
        """
        return await self._make_request_async(
            "clearinghouseState",
            {"user": address, "dex": dex},
            session
        )

    async def get_all_user_positions_async(
            self,
            address: str,
            session: aiohttp.ClientSession,
            hip3_dexes: Optional[List[str]] = None
    ) -> Dict:
        """
        Fetch user positions across main perp dex AND all active HIP-3 dexes.

        Makes parallel API calls and merges results into a single unified
        response. HIP-3 position coins are automatically prefixed with
        "dex:" (e.g., "xyz:TSLA").

        This is the method to use when you want a complete picture of
        a whale's perp exposure across all Hyperliquid markets.

        Args:
            address: User address in 0x format
            session: Shared aiohttp session
            hip3_dexes: Optional list of dex names. If None, uses cached list.

        Returns:
            {
                "main_state": <clearinghouseState dict or None>,
                "hip3_positions": [
                    {"coin": "xyz:TSLA", "size": ..., "side": ..., ...},
                    {"coin": "xyz:NVDA", "size": ..., "side": ..., ...},
                    ...
                ],
                "hip3_margin_values": {
                    "xyz": 1234.56,   # accountValue on xyz dex
                    "flx": 0.0,
                    ...
                },
                "dexes_fetched": ["xyz", "flx", "vntl"],
                "dexes_failed": [],
            }
        """
        if hip3_dexes is None:
            hip3_dexes = self.get_active_hip3_dexes()

        # Build tasks: main perp + each HIP-3 dex
        tasks = [self.get_user_state_async(address, session)]
        for dex in hip3_dexes:
            tasks.append(self.get_user_state_hip3_async(address, dex, session))

        # Fire all in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Parse results
        main_state = results[0] if not isinstance(results[0], Exception) else None
        if isinstance(results[0], Exception):
            logger.warning(f"Main perp state failed for {address[:10]}...: {results[0]}")

        hip3_positions = []
        hip3_margin_values = {}
        dexes_failed = []

        for i, dex in enumerate(hip3_dexes):
            hip3_result = results[i + 1]  # +1 because index 0 is main

            if isinstance(hip3_result, Exception):
                logger.debug(f"HIP-3 '{dex}' failed for {address[:10]}...: {hip3_result}")
                dexes_failed.append(dex)
                hip3_margin_values[dex] = 0.0
                continue

            if not hip3_result:
                # Empty response — whale has no positions on this dex
                hip3_margin_values[dex] = 0.0
                continue

            # Extract account value for this dex
            margin_summary = hip3_result.get("marginSummary", {})
            hip3_margin_values[dex] = float(margin_summary.get("accountValue", 0))

            # Extract positions — coin names from HIP-3 already include "dex:" prefix
            # e.g., the API returns coin="xyz:TSLA" for the xyz dex
            for pos_data in hip3_result.get("assetPositions", []):
                position = pos_data.get("position", {})
                size = float(position.get("szi", 0))

                if size == 0:
                    continue

                coin = position.get("coin", "")

                # Safety: ensure dex prefix is present
                # The API should return "xyz:TSLA" but if it returns just "TSLA",
                # we add the prefix ourselves
                if ":" not in coin:
                    coin = f"{dex}:{coin}"

                hip3_positions.append({
                    "coin": coin,
                    "dex": dex,
                    "size": size,
                    "side": "LONG" if size > 0 else "SHORT",
                    "entry_price": float(position.get("entryPx", 0)),
                    "liquidation_price": float(position.get("liquidationPx") or 0),
                    "leverage": float(position.get("leverage", {}).get("value", 1)),
                    "margin_used": float(position.get("marginUsed", 0)),
                    "unrealized_pnl": float(position.get("unrealizedPnl", 0)),
                    "position_value": float(position.get("positionValue", 0)),
                })

        if hip3_positions:
            logger.debug(
                f"🏗️  {address[:10]}...: {len(hip3_positions)} HIP-3 positions "
                f"across {len(hip3_dexes) - len(dexes_failed)} dexes"
            )

        return {
            "main_state": main_state,
            "hip3_positions": hip3_positions,
            "hip3_margin_values": hip3_margin_values,
            "dexes_fetched": hip3_dexes,
            "dexes_failed": dexes_failed,
        }

    # =============================================================================
    # ASYNC METHODS
    # =============================================================================

    async def _make_request_async(
            self,
            request_type: str,
            params: Dict[str, Any],
            session: Optional[aiohttp.ClientSession] = None,
            retry_count: int = 2
    ) -> Optional[Any]:
        """
        Async version of _make_request using aiohttp

        Args:
            request_type: The type of info request
            params: Additional parameters for the request
            session: Optional shared aiohttp session (recommended for multiple calls)
            retry_count: Number of retries on failure

        Returns:
            Response data or None on error
        """
        payload = {"type": request_type, **params}

        # Create session if not provided
        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            for attempt in range(retry_count + 1):
                try:
                    logger.debug(f"🌐 Async API call: {request_type} (attempt {attempt + 1}/{retry_count + 1})")

                    async with session.post(
                            self.info_endpoint,
                            json=payload,
                            headers={"Content-Type": "application/json"},
                            timeout=aiohttp.ClientTimeout(total=self.timeout)
                    ) as response:
                        logger.debug(f"   Response status: {response.status}")

                        if response.status != 200:
                            text = await response.text()
                            logger.error(f"❌ HTTP error for {request_type}: {response.status} - {text}")
                            if attempt == retry_count:
                                return None
                            await asyncio.sleep(2)
                            continue

                        result = await response.json()
                        return result

                except asyncio.TimeoutError:
                    logger.warning(f"⏱️  Async timeout for {request_type} (attempt {attempt + 1}/{retry_count + 1})")
                    if attempt == retry_count:
                        logger.error(f"❌ {request_type} failed after {retry_count + 1} attempts (timeout)")
                        return None

                except aiohttp.ClientError as e:
                    logger.error(f"❌ Async client error for {request_type}: {e}")
                    if attempt == retry_count:
                        return None

                except Exception as e:
                    logger.error(f"❌ Unexpected async error for {request_type}: {e}")
                    if attempt == retry_count:
                        return None

        finally:
            if close_session:
                await session.close()

        logger.error(f"❌ _make_request_async fell through to final return None for {request_type}")
        return None

    async def get_user_state_async(
            self,
            address: str,
            session: Optional[aiohttp.ClientSession] = None
    ) -> Optional[Dict]:
        """Async version of get_user_state"""
        return await self._make_request_async("clearinghouseState", {"user": address}, session)

    async def get_spot_clearinghouse_state_async(
            self,
            address: str,
            session: Optional[aiohttp.ClientSession] = None
    ) -> Optional[Dict]:
        """Async version of get_spot_clearinghouse_state"""
        return await self._make_request_async("spotClearinghouseState", {"user": address}, session)

    async def get_user_vault_equities_async(
            self,
            address: str,
            session: Optional[aiohttp.ClientSession] = None
    ) -> Optional[Dict]:
        """Async version of get_user_vault_equities"""
        return await self._make_request_async("userVaultEquities", {"user": address}, session)

    # =============================================================================
    # Convenience Methods
    # =============================================================================

    def get_trader_profile(self, address: str, whitelisted_tokens: Optional[Set[str]] = None) -> Dict:
        """
        Get comprehensive trader profile (includes spot balances with filtering)

        Args:
            address: Trader address
            whitelisted_tokens: Set of sub-$1 tokens to allow (uses default if None)
        """
        if whitelisted_tokens is None:
            whitelisted_tokens = {"PUMP", "kBONK", "BONK", "PEPE", "SHIB", "FLOKI", "WIF"}

        profile = {
            "address": address,
            "timestamp": datetime.now().isoformat(),
            "metrics": {}
        }

        # Role
        role_data = self.get_user_role(address)
        if role_data:
            profile["metrics"]["role"] = role_data.get("role", "unknown")

        # State & positions (PERPETUALS)
        state = self.get_user_state(address)
        if state:
            margin = state.get("marginSummary", {})
            profile["metrics"]["account_value"] = float(margin.get("accountValue", 0))
            profile["metrics"]["position_value"] = float(margin.get("totalNtlPos", 0))
            profile["metrics"]["margin_used"] = float(margin.get("totalMarginUsed", 0))
            profile["metrics"]["withdrawable"] = float(state.get("withdrawable", 0))

            positions = state.get("assetPositions", [])
            profile["metrics"]["num_positions"] = len([
                p for p in positions
                if p.get("position", {}).get("szi") != "0"
            ])

        # Spot balances - WITH FILTERING
        spot_state = self.get_spot_clearinghouse_state(address)
        spot_value = 0

        if spot_state:
            for balance in spot_state.get("balances", []):
                coin = balance.get("coin", "")
                total = float(balance.get("total", 0))

                if total <= 0:
                    continue

                # Stablecoins
                if coin in ["USDC", "USDT", "USD", "FEUSD", "USDC.e", "USDT0"]:
                    spot_value += total
                else:
                    price = self.get_token_price(coin)

                    if price:
                        # FILTER 1: Exact $1.00 for non-stablecoins (stale data)
                        if price == 1.0:
                            logger.debug(f"🚫 {coin}: Filtered out - exact $1.00 default price (amount: {total:.2f})")
                            continue

                        # FILTER 2: Sub-$1 tokens must be whitelisted
                        if price < 1.0 and coin not in whitelisted_tokens:
                            token_value = total * price  # Calculate BEFORE logging
                            logger.debug(
                                f"🚫 {coin}: Filtered out - sub-$1 token not whitelisted "
                                f"(price: ${price:.6f}, amount: {total:.2f}, value: ${token_value:.2f})"
                            )
                            continue

                        # Price passed filters, add to spot value
                        spot_value += total * price

        profile["metrics"]["spot_value"] = spot_value

        # Calculate total portfolio value (perps + spot)
        perp_value = profile["metrics"].get("account_value", 0)
        spot_val = profile["metrics"].get("spot_value", 0)
        profile["metrics"]["total_portfolio_value"] = perp_value + spot_val

        # Volume & referral
        referral = self.get_referral_info(address)
        if referral:
            profile["metrics"]["cumulative_volume"] = float(referral.get("cumVlm", 0))
            profile["metrics"]["unclaimed_rewards"] = float(referral.get("unclaimedRewards", 0))

        # Fills
        fills = self.get_user_fills(address)
        if fills:
            profile["metrics"]["num_fills"] = len(fills)
            if fills:
                profile["metrics"]["last_trade_time"] = datetime.fromtimestamp(
                    fills[0].get("time", 0) / 1000
                ).isoformat()

        # TWAP fills
        twap_fills = self.get_twap_slice_fills(address)
        if twap_fills:
            profile["metrics"]["num_twap_fills"] = len(twap_fills)

        # Fees
        fees = self.get_user_fees(address)
        if fees:
            profile["metrics"]["fee_cross_rate"] = float(fees.get("userCrossRate", 0))
            profile["metrics"]["fee_add_rate"] = float(fees.get("userAddRate", 0))

        # Portfolio
        portfolio = self.get_user_portfolio(address)
        if portfolio:
            for period_data in portfolio:
                if period_data[0] == "allTime":
                    all_time = period_data[1]
                    pnl_history = all_time.get("pnlHistory", [])
                    if pnl_history:
                        profile["metrics"]["all_time_pnl"] = float(pnl_history[-1][1])
                    profile["metrics"]["all_time_vlm"] = float(all_time.get("vlm", 0))

        return profile

    def clear_price_cache(self):
        """Clear all price-related caches"""
        self._all_mids_cache = None
        self._all_mids_cache_time = None
        self._token_name_cache = {}
        logger.info("🗑️  Cleared all price caches")

    def get_cache_stats(self) -> Dict:
        """Get cache statistics for monitoring"""
        stats = {
            "all_mids_cached": self._all_mids_cache is not None,
            "all_mids_age_seconds": None,
            "token_name_mappings": len(self._token_name_cache),
            "cached_mappings": dict(self._token_name_cache),
            # HIP-3 cache stats
            "hip3_dex_cached": self._hip3_dex_cache is not None,
            "hip3_dex_list": self._hip3_dex_cache,
            "hip3_dex_cache_age_seconds": None,
        }

        if self._all_mids_cache_time:
            stats["all_mids_age_seconds"] = (
                    datetime.now() - self._all_mids_cache_time
            ).total_seconds()

        if self._hip3_dex_cache_time:
            stats["hip3_dex_cache_age_seconds"] = (
                    datetime.now() - self._hip3_dex_cache_time
            ).total_seconds()

        return stats

    def get_recent_24h_fills(self, address: str) -> Optional[List[Dict]]:
        """Get fills from last 24 hours"""
        now = int(datetime.now().timestamp() * 1000)
        start = now - (24 * 60 * 60 * 1000)
        return self.get_user_fills_by_time(address, start, now)

    def get_recent_7d_fills(self, address: str) -> Optional[List[Dict]]:
        """Get fills from last 7 days"""
        now = int(datetime.now().timestamp() * 1000)
        start = now - (7 * 24 * 60 * 60 * 1000)
        return self.get_user_fills_by_time(address, start, now)