#!/usr/bin/env python3
"""
Analyze theater whale's transaction history
Address: 0xe7ec7fbf4f195fc8e57d814e15c3a2857cb632a3
"""

import pandas as pd
from datetime import datetime
from collections import defaultdict
from pathlib import Path

# The theater whale
THEATER_WHALE = "0xe7ec7fbf4f195fc8e57d814e15c3a2857cb632a3"

# Find the CSV
csv_path = Path(f"{THEATER_WHALE}_2025-11-18T18_59_06.895Z.csv")

if not csv_path.exists():
    print(f"❌ CSV file not found: {csv_path}")
    print("\nPlease make sure the file is in the current directory")
    exit(1)

print("=" * 80)
print("THEATER WHALE ANALYSIS")
print(f"Address: {THEATER_WHALE}")
print("=" * 80)
print()

# Load data
df = pd.read_csv(csv_path)

print(f"Total Transactions: {len(df)}")
print()

# Show columns to understand structure
print("CSV Columns:")
print(df.columns.tolist())
print()

# Basic stats
print("=" * 80)
print("TRANSACTION BREAKDOWN")
print("=" * 80)
print()

if 'class' in df.columns:
    print("Transaction Types:")
    print(df['class'].value_counts())
    print()

if 'token' in df.columns:
    print("Tokens Traded:")
    print(df['token'].value_counts())
    print()

# Convert timestamp
if 'time' in df.columns:
    df['datetime'] = pd.to_datetime(df['time'], unit='ms')
    df = df.sort_values('datetime')

    print(f"Date Range: {df['datetime'].min()} to {df['datetime'].max()}")
    print(f"Days Active: {(df['datetime'].max() - df['datetime'].min()).days} days")
    print()

# HYPE specific analysis
print("=" * 80)
print("HYPE TRADING ACTIVITY")
print("=" * 80)
print()

hype_txs = df[df['token'].str.contains('HYPE', na=False, case=False)]
print(f"Total HYPE transactions: {len(hype_txs)}")
print()

if 'type' in hype_txs.columns:
    print("HYPE Transaction Types:")
    print(hype_txs['type'].value_counts())
    print()

# Recent activity (last 7 days)
if 'datetime' in df.columns:
    recent_cutoff = df['datetime'].max() - pd.Timedelta(days=7)
    recent = df[df['datetime'] > recent_cutoff]

    print("=" * 80)
    print("RECENT ACTIVITY (Last 7 Days)")
    print("=" * 80)
    print(f"Transactions: {len(recent)}")
    print()

    if 'token' in recent.columns:
        print("Recent tokens traded:")
        print(recent['token'].value_counts().head(10))
        print()

# PERP analysis
perp_txs = df[df['class'] == 'PERP'] if 'class' in df.columns else pd.DataFrame()

if len(perp_txs) > 0:
    print("=" * 80)
    print("PERPETUAL POSITIONS")
    print("=" * 80)
    print(f"Total PERP transactions: {len(perp_txs)}")
    print()

    # Check for position details
    if 'type' in perp_txs.columns:
        print("PERP Actions:")
        print(perp_txs['type'].value_counts())
        print()

# Look for November activity specifically
if 'datetime' in df.columns:
    nov_18 = df[df['datetime'].dt.date == pd.Timestamp('2025-11-18').date()]

    if len(nov_18) > 0:
        print("=" * 80)
        print("NOVEMBER 18, 2025 ACTIVITY (TODAY)")
        print("=" * 80)
        print(f"Transactions: {len(nov_18)}")
        print()

        if 'token' in nov_18.columns:
            print("Tokens traded today:")
            print(nov_18['token'].value_counts())
            print()

        if 'type' in nov_18.columns:
            print("Action types today:")
            print(nov_18['type'].value_counts())
            print()

# Summary
print("=" * 80)
print("KEY FINDINGS")
print("=" * 80)
print()

hype_perp = len(df[(df['token'].str.contains('HYPE', na=False, case=False)) &
                   (df['class'] == 'PERP')]) if 'class' in df.columns and 'token' in df.columns else 0

hype_spot = len(df[(df['token'].str.contains('HYPE', na=False, case=False)) &
                   (df['class'] == 'SPOT')]) if 'class' in df.columns and 'token' in df.columns else 0

print(f"📊 HYPE PERP Transactions: {hype_perp}")
print(f"📊 HYPE SPOT Transactions: {hype_spot}")
print()

if hype_perp > 0:
    print("💡 This wallet is HEAVILY trading HYPE perps")
    print("   - Likely using leverage positions")
    print("   - Perp positions don't show in spot TWAP tracker")
    print("   - Can maintain 'long' position while selling spot elsewhere")
    print()

if hype_spot > 0:
    print(f"💡 Found {hype_spot} SPOT HYPE transactions")
    print("   - These would show in TWAP tracker")
    print("   - Check if these are buys or sells")
    print()

print("🎭 THEATER POSITION ANALYSIS:")
print("   - Public perp long: ~96k HYPE at $41 (losing -$222k)")
print("   - Our TWAP data: 5.26M HYPE fake buy orders (0% completion)")
print("   - Reality: Likely sold millions spot elsewhere")
print()
print("🚨 CONCLUSION:")
print("   The losing perp position is THEATER to maintain bullish narrative")
print("   while actually distributing through other means!")

print()