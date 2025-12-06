#!/usr/bin/env python3
"""
JSONL to SQLite Migration Script
=================================
Migrates existing JSONL data to SQLite database.

Usage:
    python -m storage.migrate_jsonl

Or:
    python storage/migrate_jsonl.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.sqlite_backend import SQLiteBackend


def migrate_jsonl_to_sqlite(
        jsonl_dir: Path = Path('data/json_logs'),
        db_path: Path = Path('data/twap.db'),
        verbose: bool = True
):
    """
    Migrate all JSONL files to SQLite database.

    Args:
        jsonl_dir: Directory containing coin folders with JSONL files
        db_path: Path for SQLite database
        verbose: Print progress
    """
    if not jsonl_dir.exists():
        print(f"❌ JSONL directory not found: {jsonl_dir}")
        return False

    # Initialize database
    db = SQLiteBackend(db_path)

    # Stats
    total_files = 0
    total_snapshots = 0
    total_orders = 0
    total_events = 0
    errors = 0

    # Find all coin directories
    coin_dirs = [d for d in jsonl_dir.iterdir() if d.is_dir()]

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"JSONL to SQLite Migration")
        print(f"{'=' * 60}")
        print(f"Source: {jsonl_dir}")
        print(f"Target: {db_path}")
        print(f"Coin folders found: {len(coin_dirs)}")
        print(f"{'=' * 60}\n")

    for coin_dir in sorted(coin_dirs):
        symbol = coin_dir.name.replace('_', ':')  # xyz_TSLA -> xyz:TSLA

        # Find all JSONL files for this coin
        jsonl_files = list(coin_dir.glob('*.jsonl'))

        if not jsonl_files:
            continue

        if verbose:
            print(f"📁 {symbol}: {len(jsonl_files)} files")

        coin_snapshots = 0
        coin_orders = 0
        coin_events = 0

        for jsonl_file in sorted(jsonl_files):
            total_files += 1

            try:
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)

                            # Build changes dict from the data
                            changes = {
                                'new_orders': [],
                                'completed_orders': [],
                                'canceled_orders': [],
                                'status_changes': []
                            }

                            # Convert new_orders (they're dicts in JSONL)
                            for order in data.get('new_orders', []):
                                changes['new_orders'].append(order)

                            # Convert completed_orders
                            for order in data.get('completed_orders', []):
                                changes['completed_orders'].append(order)

                            # Convert canceled_orders
                            for order in data.get('canceled_orders', []):
                                changes['canceled_orders'].append(order)

                            # Save to SQLite
                            db.save_snapshot(symbol, data, changes)

                            coin_snapshots += 1
                            coin_orders += len(data.get('active_orders', []))
                            coin_events += len(changes['new_orders']) + len(changes['completed_orders']) + len(
                                changes['canceled_orders'])

                        except json.JSONDecodeError as e:
                            errors += 1
                            if verbose:
                                print(f"  ⚠️ JSON error in {jsonl_file.name}:{line_num}: {e}")
                        except Exception as e:
                            errors += 1
                            if verbose:
                                print(f"  ⚠️ Error in {jsonl_file.name}:{line_num}: {e}")

            except Exception as e:
                errors += 1
                if verbose:
                    print(f"  ❌ Failed to read {jsonl_file.name}: {e}")

        total_snapshots += coin_snapshots
        total_orders += coin_orders
        total_events += coin_events

        if verbose:
            print(f"   ✓ {coin_snapshots} snapshots, {coin_orders} order records, {coin_events} events")

    # Final commit
    db.conn.commit()

    # Get final stats
    stats = db.get_stats()

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Migration Complete!")
        print(f"{'=' * 60}")
        print(f"Files processed: {total_files}")
        print(f"Snapshots migrated: {total_snapshots}")
        print(f"Order records: {total_orders}")
        print(f"Events recorded: {total_events}")
        print(f"Errors: {errors}")
        print(f"\nDatabase stats:")
        print(f"  Total orders: {stats['total_orders']}")
        print(f"  Active orders: {stats['active_orders']}")
        print(f"  Total snapshots: {stats['total_snapshots']}")
        print(f"  Total events: {stats['total_events']}")
        print(f"  Unique addresses: {stats['total_addresses']}")
        print(f"  Unique symbols: {stats['unique_symbols']}")
        print(f"  Database size: {stats['db_size_mb']} MB")
        print(f"{'=' * 60}\n")

    db.close()
    return True


def migrate_address_list(
        address_file: Path = Path('data/address_list.json'),
        db_path: Path = Path('data/twap.db'),
        verbose: bool = True
):
    """
    Migrate address_list.json to SQLite.

    Args:
        address_file: Path to address_list.json
        db_path: Path to SQLite database
        verbose: Print progress
    """
    if not address_file.exists():
        if verbose:
            print(f"⚠️ Address file not found: {address_file}")
        return False

    db = SQLiteBackend(db_path)

    try:
        with open(address_file, 'r') as f:
            data = json.load(f)

        # Handle different formats
        if isinstance(data, dict):
            addresses = list(data.keys())
        elif isinstance(data, list):
            addresses = data
        else:
            addresses = data.get('addresses', [])

        timestamp = datetime.now().isoformat()

        for addr in addresses:
            db._upsert_address(addr, timestamp)

        db.conn.commit()

        if verbose:
            print(f"✓ Migrated {len(addresses)} addresses from {address_file}")

        db.close()
        return True

    except Exception as e:
        if verbose:
            print(f"❌ Error migrating addresses: {e}")
        db.close()
        return False


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Migrate JSONL data to SQLite')
    parser.add_argument('--jsonl-dir', type=Path, default=Path('data/json_logs'),
                        help='Source directory with JSONL files')
    parser.add_argument('--db-path', type=Path, default=Path('data/twap.db'),
                        help='Target SQLite database path')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress output')
    parser.add_argument('--addresses-only', action='store_true',
                        help='Only migrate address list')

    args = parser.parse_args()

    verbose = not args.quiet

    if args.addresses_only:
        migrate_address_list(db_path=args.db_path, verbose=verbose)
    else:
        # Migrate both JSONL and addresses
        migrate_jsonl_to_sqlite(
            jsonl_dir=args.jsonl_dir,
            db_path=args.db_path,
            verbose=verbose
        )
        migrate_address_list(db_path=args.db_path, verbose=verbose)