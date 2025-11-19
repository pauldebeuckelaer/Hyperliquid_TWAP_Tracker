#!/usr/bin/env python3
"""
Analyze the complete transaction history of 0x5aeb1821f596d2d9ffe182d3f914b274a80511cc
Find patterns, timeline, and real purpose of this address
"""

import pandas as pd
from datetime import datetime
from collections import defaultdict
import sys
import os

# DEBUG: Let's see where we are and what files exist
print("Current working directory:", os.getcwd())
print("\nFiles in current directory:")
for file in os.listdir('.'):
    if file.endswith('.csv'):
        print(f"  FOUND CSV: {file}")

# Get CSV file path from command line or use default
if len(sys.argv) > 1:
    csv_file = sys.argv[1]
else:
    # Default path - in the PyCharm project folder
    csv_file = '0xe7ec7fbf4f195fc8e57d814e15c3a2857cb632a3_2025-11-18T18_59_06.895Z.csv'

print(f"\nLooking for: {csv_file}")
print(f"Full path: {os.path.abspath(csv_file)}")
print(f"File exists: {os.path.exists(csv_file)}")

if not os.path.exists(csv_file):
    print(f"\nERROR: File not found: {csv_file}")
    print(f"\nUsage: python {sys.argv[0]} <path_to_csv>")
    print(f"Or place the CSV in the same folder as this script.")
    sys.exit(1)

print(f"\nLoading: {csv_file}\n")

# Load the data
df = pd.read_csv(csv_file)

print("=" * 80)
print("WHALE TRANSACTION HISTORY ANALYSIS")
print("Address: 0x5aeb1821f596d2d9ffe182d3f914b274a80511cc")
print("=" * 80)
print()

# Convert timestamp to datetime
df['datetime'] = pd.to_datetime(df['time'], unit='ms')
df = df.sort_values('datetime')

print(f"Total Transactions: {len(df)}")
print(f"Date Range: {df['datetime'].min()} to {df['datetime'].max()}")
print(f"Days Active: {(df['datetime'].max() - df['datetime'].min()).days} days")
print()

# Transaction type breakdown
print("=" * 80)
print("TRANSACTION TYPE BREAKDOWN")
print("=" * 80)
print(df['class'].value_counts())
print()

# Token breakdown
print("=" * 80)
print("TOKEN ACTIVITY")
print("=" * 80)
print(df['token'].value_counts())
print()

# USD Amount analysis (where available)
df['USDAmount'] = pd.to_numeric(df['USDAmount'], errors='coerce')
total_value = df['USDAmount'].abs().sum()
print(f"Total USD Volume: ${total_value:,.2f}")
print()

# HYPE-specific analysis
hype_txs = df[df['token'] == 'HYPE'].copy()
print("=" * 80)
print(f"HYPE TRANSACTIONS: {len(hype_txs)} total")
print("=" * 80)
print()

# HYPE transaction types
print("HYPE Transaction Types:")
print(hype_txs['type'].value_counts())
print()

# TWAP analysis
twap_txs = hype_txs[hype_txs['type'] == 'Twap']
print(f"\n🔍 TWAP ORDERS: {len(twap_txs)} transactions")
print("=" * 80)

if len(twap_txs) > 0:
    twap_dates = twap_txs['datetime'].dt.date.value_counts().sort_index()
    print("\nTWAP Activity by Date:")
    for date, count in twap_dates.items():
        print(f"  {date}: {count} TWAP transactions")

    # November 7th specific
    nov7_twaps = twap_txs[twap_txs['datetime'].dt.date == pd.Timestamp('2025-11-07').date()]
    print(f"\n🚨 November 7th TWAP Count: {len(nov7_twaps)}")

# Real trades (Buy/Sell)
real_trades = hype_txs[hype_txs['type'].isin(['Buy', 'Sell'])]
print(f"\n💰 ACTUAL HYPE TRADES (Buy/Sell): {len(real_trades)}")
if len(real_trades) > 0:
    print("\nReal Trade Details:")
    for idx, trade in real_trades.iterrows():
        print(f"  {trade['datetime']} - {trade['type']} - {trade['USDAmount']} USD")

# Staking activity
staking_txs = df[df['type'].str.contains('Stak', case=False, na=False)]
print(f"\n🔒 STAKING TRANSACTIONS: {len(staking_txs)}")
if len(staking_txs) > 0:
    print("\nStaking Details:")
    for idx, stake in staking_txs.iterrows():
        print(f"  {stake['datetime']} - {stake['type']} - {stake['token']}")

# Deposit/Withdraw pattern
deposits = df[df['class'] == 'DEPOSIT']
withdraws = df[df['class'] == 'WITHDRAW']

print(f"\n💵 DEPOSITS: {len(deposits)}")
print(f"💵 WITHDRAWALS: {len(withdraws)}")

if len(deposits) > 0:
    print("\nMajor Deposits:")
    major_deposits = deposits[deposits['USDAmount'].abs() > 1000000].sort_values('USDAmount', ascending=False)
    for idx, dep in major_deposits.head(10).iterrows():
        print(f"  {dep['datetime']} - {dep['token']}: ${dep['USDAmount']:,.2f}")

if len(withdraws) > 0:
    print("\nMajor Withdrawals:")
    major_withdraws = withdraws[withdraws['USDAmount'].abs() > 1000000].sort_values('USDAmount', ascending=False)
    for idx, wit in major_withdraws.head(10).iterrows():
        print(f"  {wit['datetime']} - {wit['token']}: ${wit['USDAmount']:,.2f}")

# VALIDATING transactions
validating = df[df['class'] == 'VALIDATING']
print(f"\n⚡ VALIDATING TRANSACTIONS: {len(validating)}")
if len(validating) > 0:
    print("Most recent validating activity:")
    for idx, val in validating.head(5).iterrows():
        print(f"  {val['datetime']} - {val['type']}")

# Timeline analysis
print("\n" + "=" * 80)
print("ACTIVITY TIMELINE")
print("=" * 80)

# Group by month
df['month'] = df['datetime'].dt.to_period('M')
monthly = df.groupby('month').size().sort_index()

print("\nTransactions per Month:")
for month, count in monthly.items():
    print(f"  {month}: {count} transactions")

# Find first significant activity
first_tx = df.iloc[0]
last_tx = df.iloc[-1]

print(f"\nFirst Transaction: {first_tx['datetime']} - {first_tx['class']} - {first_tx['type']}")
print(f"Last Transaction: {last_tx['datetime']} - {last_tx['class']} - {last_tx['type']}")

# Check for suspicious patterns
print("\n" + "=" * 80)
print("SUSPICIOUS PATTERN ANALYSIS")
print("=" * 80)

# Pattern 1: TWAP spam
if len(twap_txs) > 20:
    print(f"⚠️  HIGH TWAP VOLUME: {len(twap_txs)} TWAP orders (potential manipulation)")

# Pattern 2: Concentrated activity
date_counts = df['datetime'].dt.date.value_counts()
max_day = date_counts.idxmax()
max_count = date_counts.max()
print(f"⚠️  BUSIEST DAY: {max_day} with {max_count} transactions")

# Pattern 3: Zero actual HYPE holdings
if len(real_trades) < 5:
    print(f"⚠️  MINIMAL REAL TRADING: Only {len(real_trades)} actual buy/sell trades")

# Pattern 4: Large staking with no trading
if len(staking_txs) > 0 and len(real_trades) < 5:
    print("⚠️  STAKER BUT NOT TRADER: Large stake, minimal trading activity")

# Pattern 5: Recent validation activity
recent_validating = validating[validating['datetime'] > (df['datetime'].max() - pd.Timedelta(days=7))]
if len(recent_validating) > 0:
    print(f"⚠️  RECENT VALIDATION ACTIVITY: {len(recent_validating)} transactions in last 7 days")

print("\n" + "=" * 80)
print("CONCLUSION")
print("=" * 80)

# Calculate key metrics
twap_ratio = len(twap_txs) / len(df) * 100 if len(df) > 0 else 0
trade_ratio = len(real_trades) / len(df) * 100 if len(df) > 0 else 0

print(f"\nTWAP Orders: {twap_ratio:.1f}% of all transactions")
print(f"Real Trades: {trade_ratio:.1f}% of all transactions")
print(f"Staking Activity: {len(staking_txs)} transactions")
print(f"Validation Activity: {len(validating)} transactions")

print("\n🎯 WALLET PROFILE:")
if len(validating) > 10:
    print("   - VALIDATOR NODE (active validation transactions)")
if total_value > 10000000:
    print(f"   - WHALE CAPITAL (${total_value / 1e6:.1f}M in transaction volume)")
if twap_ratio > 50:
    print("   - TWAP MANIPULATOR (>50% of transactions are TWAPs)")
if trade_ratio < 1 and len(twap_txs) > 20:
    print("   - SPOOFER (many TWAPs, almost no real trades)")
if len(staking_txs) > 0:
    print("   - STAKER (actively staking tokens)")

print("\n📊 INTERPRETATION:")
if len(validating) > 10 and len(staking_txs) > 0:
    print("This appears to be a VALIDATOR NODE that uses its position to")
    print("manipulate markets through spoofing. The large stake gives them")
    print("incentive to protect HYPE price, and they use fake TWAP orders")
    print("to create artificial buy pressure without actually trading.")
elif twap_ratio > 50 and trade_ratio < 5:
    print("This is a MANIPULATION WALLET - mostly fake TWAP orders with")
    print("minimal real trading. Classic spoofing behavior.")
else:
    print("Unusual pattern - requires further investigation.")

print()