import requests


def investigate_staking(address):
    base_url = "https://api.hyperliquid.xyz/info"

    print("=" * 80)
    print("INVESTIGATING STAKING MECHANISMS")
    print("=" * 80)

    # 1. Check userFees (staking discount)
    print("\n1️⃣ UserFees (Staking Discount):")
    response = requests.post(base_url, json={"type": "userFees", "user": address})
    user_fees = response.json()
    print(f"   Raw response: {user_fees}")

    # 2. Check clearinghouse state for all balances
    print("\n2️⃣ Spot Clearinghouse State:")
    response = requests.post(base_url, json={"type": "spotClearinghouseState", "user": address})
    spot_state = response.json()
    print(f"   Total balances: {len(spot_state.get('balances', []))}")
    for bal in spot_state.get('balances', []):
        if 'HYPE' in bal['coin'] or 'stak' in bal['coin'].lower():
            print(f"   → {bal['coin']}: {bal}")

    # 3. Try to get user state (might show staking)
    print("\n3️⃣ User State:")
    try:
        response = requests.post(base_url, json={"type": "userState", "user": address})
        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            print(f"   Data: {response.json()}")
    except Exception as e:
        print(f"   Error: {e}")

    # 4. Check validators for delegations
    print("\n4️⃣ Checking Validators for Delegations:")
    response = requests.post(base_url, json={"type": "validatorSummaries"})
    validators = response.json()
    print(f"   Total validators: {len(validators)}")

    # 5. Try stakingStateUser again with different parameters
    print("\n5️⃣ Trying stakingStateUser:")
    try:
        response = requests.post(base_url, json={"type": "stakingStateUser", "user": address})
        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            print(f"   Data: {response.json()}")
        else:
            print(f"   Error response: {response.text}")
    except Exception as e:
        print(f"   Error: {e}")

    # 6. Check spotMeta for HYPE token info
    print("\n6️⃣ Spot Meta (Token Info):")
    response = requests.post(base_url, json={"type": "spotMeta"})
    spot_meta = response.json()
    for token in spot_meta.get('tokens', []):
        if 'HYPE' in token.get('name', ''):
            print(f"   HYPE token: {token}")

    # 7. Calculate what bpsOfMaxSupply actually means
    print("\n7️⃣ Analyzing bpsOfMaxSupply:")
    bps = float(user_fees['activeStakingDiscount']['bpsOfMaxSupply'])
    max_supply = 1_000_000_000  # 1B HYPE
    calculated_amount = (bps / 100) * max_supply

    spot_hype = 16424.952770  # From your spot balance
    difference = calculated_amount - spot_hype

    print(f"   BPS of Max Supply: {bps}%")
    print(f"   Calculated amount: {calculated_amount:,.2f} HYPE")
    print(f"   Spot HYPE balance: {spot_hype:,.2f} HYPE")
    print(f"   Difference: {difference:,.2f} HYPE")
    print(f"   → This suggests {difference:,.2f} HYPE is staked SEPARATELY")


# Run investigation
address = "0x525232cb6ed5030d2b052b36bfc0baec96986ee3"
investigate_staking(address)