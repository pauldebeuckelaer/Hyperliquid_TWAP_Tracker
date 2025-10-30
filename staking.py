import requests


class HyperliquidCompleteBalance:
    BASE_URL = "https://api.hyperliquid.xyz/info"
    HLP_VAULT = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"

    # Known staked HYPE derivative tokens
    STAKED_HYPE_TOKENS = ['STHYPE', 'KHYPE', 'DHYPE', 'HWHYPE', 'MHYPE']

    def __init__(self, user_address):
        self.user_address = user_address

    def _post(self, request_type, **kwargs):
        payload = {"type": request_type, **kwargs}
        response = requests.post(self.BASE_URL, json=payload)
        response.raise_for_status()
        return response.json()

    def get_all_spot_balances(self):
        """Get all spot balances including LST tokens"""
        data = self._post("spotClearinghouseState", user=self.user_address)
        return data.get('balances', [])

    def get_staking_breakdown(self):
        """Get breakdown of regular HYPE vs staked HYPE tokens"""
        balances = self.get_all_spot_balances()

        regular_hype = 0
        staked_tokens = {}
        total_staked_value = 0

        for bal in balances:
            coin = bal['coin']
            amount = float(bal['total'])

            if coin == 'HYPE':
                regular_hype = amount
            elif coin in self.STAKED_HYPE_TOKENS:
                staked_tokens[coin] = amount
                # LST tokens typically represent 1:1 with HYPE
                total_staked_value += amount

        return {
            'regular_hype': regular_hype,
            'staked_tokens': staked_tokens,
            'total_staked_hype': total_staked_value,
            'total_hype_exposure': regular_hype + total_staked_value
        }

    def get_hype_price(self):
        """Get current HYPE price"""
        try:
            data = self._post("allMids")
            # Check if HYPE-USD perpetual exists
            if 'HYPE-USD' in data:
                return float(data['HYPE-USD'])
            return None
        except:
            return None

    def get_complete_balance_summary(self):
        """Get complete balance with proper staking calculation"""
        print("=" * 80)
        print("HYPERLIQUID COMPLETE BALANCE")
        print("=" * 80)
        print(f"Address: {self.user_address}\n")

        # Get HYPE price
        hype_price = self.get_hype_price()
        print(f"HYPE Price: ${hype_price:,.3f}\n" if hype_price else "HYPE Price: Unable to fetch\n")

        # Staking breakdown
        print("-" * 80)
        print("HYPE HOLDINGS BREAKDOWN")
        print("-" * 80)
        staking = self.get_staking_breakdown()

        print(f"Regular HYPE: {staking['regular_hype']:,.2f} HYPE", end="")
        if hype_price:
            print(f" (${staking['regular_hype'] * hype_price:,.2f})")
        else:
            print()

        print(f"\nStaked HYPE Derivatives:")
        if staking['staked_tokens']:
            for token, amount in staking['staked_tokens'].items():
                print(f"  {token}: {amount:,.2f}", end="")
                if hype_price:
                    print(f" (${amount * hype_price:,.2f})")
                else:
                    print()
        else:
            print("  None found")

        print(f"\nTotal Staked: {staking['total_staked_hype']:,.2f} HYPE", end="")
        if hype_price:
            print(f" (${staking['total_staked_hype'] * hype_price:,.2f})")
        else:
            print()

        print(f"Total HYPE Exposure: {staking['total_hype_exposure']:,.2f} HYPE", end="")
        if hype_price:
            print(f" (${staking['total_hype_exposure'] * hype_price:,.2f})")
        else:
            print()

        # Staking discount info
        print("\n" + "-" * 80)
        print("STAKING DISCOUNT INFO")
        print("-" * 80)
        fees_data = self._post("userFees", user=self.user_address)
        discount_info = fees_data.get('activeStakingDiscount', {})

        bps = float(discount_info.get('bpsOfMaxSupply', 0))
        discount = float(discount_info.get('discount', 0))
        implied_stake = (bps / 100) * 1_000_000_000  # 1B max supply

        print(f"Fee Discount: {discount * 100:.0f}%")
        print(f"BPS of Max Supply: {bps:.10f}%")
        print(f"Implied Total Stake: {implied_stake:,.2f} HYPE")

        # Check if there's a discrepancy
        if staking['total_hype_exposure'] > 0:
            discrepancy = implied_stake - staking['total_hype_exposure']
            if abs(discrepancy) > 100:  # More than 100 HYPE difference
                print(f"\n⚠️  DISCREPANCY DETECTED:")
                print(f"   Visible in wallet: {staking['total_hype_exposure']:,.2f} HYPE")
                print(f"   Counted for discount: {implied_stake:,.2f} HYPE")
                print(f"   Difference: {discrepancy:,.2f} HYPE")
                print(f"\n   This likely means you have {discrepancy:,.2f} HYPE staked")
                print(f"   in a validator that's not visible in spot balances.")

        # All spot balances
        print("\n" + "-" * 80)
        print("ALL SPOT BALANCES")
        print("-" * 80)
        all_balances = self.get_all_spot_balances()
        for bal in all_balances:
            amount = float(bal['total'])
            if amount > 0:
                coin = bal['coin']
                hold = float(bal.get('hold', 0))
                print(f"{coin}: {amount:,.6f} (Hold: {hold:,.6f})")

        # Vault
        print("\n" + "-" * 80)
        print("VAULT (HLP)")
        print("-" * 80)
        try:
            vault = self._post("vaultDetails", vaultAddress=self.HLP_VAULT, user=self.user_address)
            equity = float(vault['followerState']['vaultEquity'])
            pnl = float(vault['followerState']['pnl'])
            all_time_pnl = float(vault['followerState']['allTimePnl'])

            print(f"Vault: {vault['name']}")
            print(f"Equity: ${equity:,.8f}")
            print(f"Current PnL: ${pnl:,.8f}")
            print(f"All-Time PnL: ${all_time_pnl:,.2f}")
        except Exception as e:
            print(f"Error fetching vault: {e}")

        print("\n" + "=" * 80)


# Usage
address = "0x525232cb6ed5030d2b052b36bfc0baec96986ee3"
hl = HyperliquidCompleteBalance(address)
hl.get_complete_balance_summary()