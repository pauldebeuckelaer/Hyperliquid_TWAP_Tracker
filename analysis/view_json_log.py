#!/usr/bin/env python3
"""
Quick utility to view JSON logs in readable format
"""
import json
import sys
from pathlib import Path
from datetime import datetime


def view_last_entry(file_path):
    """Show the last entry in pretty format"""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if not lines:
        print("File is empty!")
        return

    last_entry = json.loads(lines[-1])
    print(json.dumps(last_entry, indent=2))


def view_all_entries(file_path):
    """Show all entries in pretty format"""
    with open(file_path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            entry = json.loads(line)
            print(f"\n{'=' * 80}")
            print(f"ENTRY {i} - {entry['timestamp']}")
            print('=' * 80)
            print(json.dumps(entry, indent=2))


def view_summary(file_path):
    """Show summary of all entries"""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    print(f"\n{'=' * 80}")
    print(f"LOG SUMMARY - {file_path.name}")
    print('=' * 80)
    print(f"Total entries: {len(lines)}")

    if lines:
        first = json.loads(lines[0])
        last = json.loads(lines[-1])

        print(f"\nFirst entry: {first['timestamp']}")
        print(f"Last entry:  {last['timestamp']}")
        print(f"Updates:     {first['update_number']} to {last['update_number']}")

        print(f"\n--- LATEST SNAPSHOT ---")
        print(json.dumps(last, indent=2))


def main():
    # Find today's log file
    today = datetime.now().strftime('%Y%m%d')
    log_dir = Path('json_logs')

    # Look for HYPE log file
    log_file = log_dir / f'HYPE_{today}.jsonl'

    if not log_file.exists():
        print(f"❌ Log file not found: {log_file}")
        print(f"\nAvailable files in {log_dir}:")
        for f in log_dir.glob('*.jsonl'):
            print(f"  - {f.name}")
        return

    print(f"📁 Reading: {log_file}")
    print()

    # Show summary by default
    view_summary(log_file)

    # Uncomment these if you want to see more:
    # view_last_entry(log_file)
    # view_all_entries(log_file)


if __name__ == "__main__":
    main()