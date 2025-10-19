#!/usr/bin/env python3
"""
Process Existing JSON Logs - Extract and Rank Addresses
Reads your existing JSONL logs and fetches ranks for all addresses
"""
import json
from pathlib import Path
from api_client.hypurrscan_client import HypurrScanClient
from address_tracker import AddressRankTracker
from logging_config import setup_logging, get_module_logger

# Setup logging
setup_logging({
    'logging': {
        'root_level': 'INFO',
        'console': True,
        'modules': {
            'api_client.hypurrscan_client': 'INFO'
        }
    }
})

logger = get_module_logger(__name__)


def extract_addresses_from_jsonl(jsonl_file: Path) -> set:
    """Extract all unique addresses from a JSONL log file"""
    addresses = set()

    logger.info(f"📂 Reading {jsonl_file}...")

    with open(jsonl_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                data = json.loads(line.strip())

                # Extract from active_orders
                for order in data.get('active_orders', []):
                    addr = order.get('address')
                    if addr:
                        addresses.add(addr)

                # Extract from completed_orders
                for order in data.get('completed_orders', []):
                    addr = order.get('address')
                    if addr:
                        addresses.add(addr)

                # Extract from canceled_orders
                for order in data.get('canceled_orders', []):
                    addr = order.get('address')
                    if addr:
                        addresses.add(addr)

            except json.JSONDecodeError as e:
                logger.warning(f"⚠️ Line {line_num}: Invalid JSON - {e}")
            except Exception as e:
                logger.warning(f"⚠️ Line {line_num}: Error - {e}")

    logger.info(f"✅ Found {len(addresses)} unique addresses")
    return addresses


def main():
    print("=" * 80)
    print("📊 Process Existing JSON Logs & Fetch Address Rankings")
    print("=" * 80)
    print()

    # Find all JSONL files in json_logs directory
    log_dir = Path('json_logs')

    if not log_dir.exists():
        logger.error(f"❌ Directory not found: {log_dir}")
        logger.info("   Please specify the correct path to your JSON logs")
        return

    jsonl_files = list(log_dir.glob('*.jsonl'))

    if not jsonl_files:
        logger.error(f"❌ No .jsonl files found in {log_dir}")
        return

    logger.info(f"📁 Found {len(jsonl_files)} log files")
    for f in jsonl_files:
        logger.info(f"   - {f.name}")

    # Extract all unique addresses
    all_addresses = set()
    for jsonl_file in jsonl_files:
        addresses = extract_addresses_from_jsonl(jsonl_file)
        all_addresses.update(addresses)

    print()
    logger.info(f"📊 Total unique addresses across all logs: {len(all_addresses)}")
    print()

    # Show addresses
    logger.info("Addresses found:")
    for i, addr in enumerate(sorted(all_addresses), 1):
        logger.info(f"  {i}. {addr}")

    print()
    print("=" * 80)
    print("Fetching holder ranks for all addresses...")
    print("=" * 80)
    print()

    # Initialize client and tracker
    client = HypurrScanClient()
    tracker = AddressRankTracker(client, {'data_file': 'address_ranks.json'})

    # Add all addresses to tracker
    for addr in all_addresses:
        tracker.add_address(addr)

    # Fetch ranks for all addresses
    logger.info(f"🔄 Fetching ranks for {len(all_addresses)} addresses...")
    success = 0
    failed = 0

    for i, addr in enumerate(sorted(all_addresses), 1):
        logger.info(f"[{i}/{len(all_addresses)}] Processing {addr[:10]}...")
        if tracker.fetch_and_update_rank(addr):
            success += 1
        else:
            failed += 1

    print()
    print("=" * 80)
    logger.info(f"✅ Complete! Success: {success}, Failed: {failed}")
    print("=" * 80)
    print()

    # Show summary
    tracker.log_summary()

    print()
    print("=" * 80)
    print("Top Addresses by HYPE Rank:")
    print("=" * 80)

    top_addresses = tracker.get_top_addresses(20, by='rank')
    for i, addr_data in enumerate(top_addresses, 1):
        rank = addr_data['hype_rank']
        classification = addr_data['classification']
        addr = addr_data['address']

        emoji = {'mega_whale': '🐋🐋', 'whale': '🐋', 'dolphin': '🐬',
                 'fish': '🐟', 'shrimp': '🦐', 'unknown': '❓'}

        if rank:
            print(f"{i:2}. {emoji.get(classification, '  ')} {addr[:10]}... | "
                  f"Rank #{rank:6} | {classification}")
        else:
            print(f"{i:2}. ❓ {addr[:10]}... | No rank data")

    print()
    print("=" * 80)
    print("Exporting report...")
    print("=" * 80)

    tracker.export_report('address_classification_report.json')

    print()
    print("✅ Files created:")
    print("   - address_ranks.json (address database)")
    print("   - address_classification_report.json (detailed report)")
    print()

    client.close()


if __name__ == "__main__":
    main()