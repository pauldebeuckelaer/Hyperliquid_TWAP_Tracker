import requests


class HyperliquidBalance:
    BASE_URL = "https://api.hyperliquid.xyz/info"
    HLP_VAULT = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
    HYPE_MAX_SUPPLY = 1_000_000_000  # 1 billion HYPE tokens

    def __init__(self, user_address):
        self.user_address = user_address

    def _post(self, request_type, **kwargs):
        payload = {"type": request_type, **kwargs}
        response = requests.post(self.BASE_URL, json=payload)
        response.raise_for_status()
        return response.json()

    def get_vault_balance(self, vault_address=None):
        """Get user's vault equity"""
        vault = vault_address or self.HLP_VAULT
        try:
            data = self._post("vaultDetails",
                              vaultAddress=vault,
                              user=self.user_address)
            return {
                'vault_address': vault,
                'vault_name': data.get('name'),
                'vault_equity': float(data['followerState']['vaultEquity']),
                'pnl': float(data['followerState']['pnl']),
                'all_time_pnl': float(data['followerState']['allTimePnl'])
            }
        except Exception as e:
            print(f"Error fetching vault balance: {e}")
            return None

    def get_staking_balance(self):
        """Calculate staking balance from discount info"""
        try:
            data = self._post("userFees", user=self.user_address)
            staking_info = data.get('activeStakingDiscount')

            if not staking_info:
                return {
                    'staked_hype': 0,
                    'discount': 0,
                    'bps_of_max_supply': 0
                }

            bps = float(staking_info['bpsOfMaxSupply'])
            discount = float(staking_info['discount'])

            # Calculate actual staked amount
            # bps is in basis points of max supply
            staked_hype = (bps / 100) * self.HYPE_MAX_SUPPLY

            return {
                'staked_hype': staked_hype,
                'discount': discount,
                'bps_of_max_supply': bps,
                'percentage_of_supply': bps
            }
        except Exception as e:
            print(f"Error fetching staking info: {e}")
            return None

    def get_spot_balances(self):
        """Get all spot token balances"""
        try:
            data = self._post("spotClearinghouseState", user=self.user_address)
            balances = data.get('balances', [])

            # Format balances nicely
            formatted = []
            for bal in balances:
                formatted.append({
                    'token': bal['coin'],
                    'total': float(bal['total']),
                    'hold': float(bal.get('hold', 0))
                })
            return formatted
        except Exception as e:
            print(f"Error fetching spot balances: {e}")
            return []

    def get_hype_price(self):
        """Get current HYPE token price"""
        try:
            data = self._post("allMids")
            # HYPE price might be under spot or perp markets
            # This is a simplified approach
            return data.get('HYPE', None)
        except Exception as e:
            return None

    def get_complete_summary(self):
        """Get complete balance summary"""
        print("=" * 80)
        print("HYPERLIQUID BALANCE SUMMARY")
        print("=" * 80)
        print(f"\nAddress: {self.user_address}\n")

        # Staking
        print("-" * 80)
        print("STAKING")
        print("-" * 80)
        staking = self.get_staking_balance()
        if staking:
            print(f"Staked HYPE: {staking['staked_hype']:,.2f} HYPE")
            print(f"% of Max Supply: {staking['percentage_of_supply']:.6f}%")
            print(f"Fee Discount: {staking['discount'] * 100:.0f}%")

        # Vault
        print("\n" + "-" * 80)
        print("VAULT (HLP)")
        print("-" * 80)
        vault = self.get_vault_balance()
        if vault:
            print(f"Vault: {vault['vault_name']}")
            print(f"Equity: ${vault['vault_equity']:,.8f}")
            print(f"PnL: ${vault['pnl']:,.8f}")
            print(f"All-Time PnL: ${vault['all_time_pnl']:,.8f}")

        # Spot Balances
        print("\n" + "-" * 80)
        print("SPOT BALANCES")
        print("-" * 80)
        spot_balances = self.get_spot_balances()
        if spot_balances:
            for bal in spot_balances:
                if bal['total'] > 0:
                    print(f"{bal['token']}: {bal['total']:,.6f} (Hold: {bal['hold']:,.6f})")
        else:
            print("No spot balances found")

        print("\n" + "=" * 80)


# Usage
address = "0x525232cb6ed5030d2b052b36bfc0baec96986ee3"
hl = HyperliquidBalance(address)

# Get complete summary
hl.get_complete_summary()

# Or get individual components
staking = hl.get_staking_balance()
print(f"\n📊 You have {staking['staked_hype']:,.2f} HYPE staked")
print(f"💰 This gives you a {staking['discount'] * 100:.0f}% fee discount")