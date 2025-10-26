#!/usr/bin/env python3
"""
Hyperliquid API Client
Provides access to Hyperliquid Info endpoint for trader data
"""
import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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

        logger.info(f"Hyperliquid client initialized: {self.api_url}")

    def _make_request(
            self,
            request_type: str,
            params: Dict[str, Any],
            retry_count: int = 2
    ) -> Optional[Any]:
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
                response = requests.post(
                    self.info_endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout for {request_type} (attempt {attempt + 1}/{retry_count + 1})")
                if attempt == retry_count:
                    logger.error(f"Failed {request_type} after {retry_count + 1} attempts")
                    return None

            except requests.exceptions.RequestException as e:
                logger.error(f"Request error for {request_type}: {e}")
                if attempt == retry_count:
                    return None

            except Exception as e:
                logger.error(f"Unexpected error for {request_type}: {e}")
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

    def get_vault_equities(self, address: str) -> Optional[List[Dict]]:
        """
        Get user's vault deposits

        Args:
            address: User address in 0x format

        Returns:
            List of vault deposits
        """
        return self._make_request("userVaultEquities", {"user": address})

    def get_vault_details(
            self,
            vault_address: str,
            user: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get details for a specific vault

        Args:
            vault_address: Vault address
            user: Optional user address for follower-specific info

        Returns:
            Vault details, performance, followers
        """
        params = {"vaultAddress": vault_address}
        if user:
            params["user"] = user
        return self._make_request("vaultDetails", params)

    # =============================================================================
    # Market Data
    # =============================================================================

    def get_all_mids(self) -> Optional[Dict[str, str]]:
        """
        Get mid prices for all assets

        Returns:
            Dict of {coin: price}
        """
        return self._make_request("allMids", {})

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

    # =============================================================================
    # Convenience Methods
    # =============================================================================

    def get_trader_profile(self, address: str) -> Dict:
        """
        Get comprehensive trader profile

        Args:
            address: User address in 0x format

        Returns:
            Dict with all key metrics combined
        """
        profile = {
            "address": address,
            "timestamp": datetime.now().isoformat(),
            "metrics": {}
        }

        # Role
        role_data = self.get_user_role(address)
        if role_data:
            profile["metrics"]["role"] = role_data.get("role", "unknown")

        # State & positions
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

    def get_recent_24h_fills(self, address: str) -> Optional[List[Dict]]:
        """
        Convenience method to get fills from last 24 hours

        Args:
            address: User address

        Returns:
            List of fills from last 24h
        """
        now = int(datetime.now().timestamp() * 1000)
        start = now - (24 * 60 * 60 * 1000)
        return self.get_user_fills_by_time(address, start, now)

    def get_recent_7d_fills(self, address: str) -> Optional[List[Dict]]:
        """
        Convenience method to get fills from last 7 days

        Args:
            address: User address

        Returns:
            List of fills from last 7 days
        """
        now = int(datetime.now().timestamp() * 1000)
        start = now - (7 * 24 * 60 * 60 * 1000)
        return self.get_user_fills_by_time(address, start, now)


# =============================================================================
# Usage Example
# =============================================================================

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Initialize client
    client = HyperliquidClient()

    # Example address
    test_address = "0x0606f126f03ee5f51f168fbe9be39ea5370a9bee"

    print("\n" + "=" * 70)
    print("Testing Hyperliquid Client")
    print("=" * 70)

    # Get trader profile
    print(f"\nFetching profile for {test_address}...")
    profile = client.get_trader_profile(test_address)

    print("\n📊 Trader Profile:")
    for key, value in profile.get("metrics", {}).items():
        print(f"  {key}: {value}")

    # Get recent fills
    print("\n📈 Recent Fills:")
    fills = client.get_user_fills(test_address)
    if fills:
        print(f"  Found {len(fills)} fills")
        if fills:
            latest = fills[0]
            print(f"  Latest: {latest.get('side')} {latest.get('sz')} @ {latest.get('px')}")
    else:
        print("  No fills found")

    # Get TWAP fills
    print("\n🎯 TWAP Fills:")
    twap_fills = client.get_twap_slice_fills(test_address)
    if twap_fills:
        print(f"  Found {len(twap_fills)} TWAP fills")
    else:
        print("  No TWAP fills found")