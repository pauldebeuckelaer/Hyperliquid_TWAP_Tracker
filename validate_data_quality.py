#!/usr/bin/env python3
"""
TWAP Data Quality Validator
============================
Analyzes JSON logs to detect edge cases, anomalies, and data quality issues.

Checks for:
1. Status transition anomalies (canceled->active, etc.)
2. Orders appearing/disappearing unexpectedly
3. Missing or malformed data fields
4. Duplicate orders
5. Impossible state combinations
6. Timing anomalies
"""

import json
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Set, Any
from pathlib import Path


class TWAPDataValidator:
    """Validate TWAP data quality and detect anomalies"""

    def __init__(self, log_file: str):
        self.log_file = log_file
        self.issues = []
        self.warnings = []
        self.stats = defaultdict(int)

        # Track order lifecycles
        self.order_history = {}  # order_hash -> list of states
        self.order_first_seen = {}  # order_hash -> update_number
        self.order_last_seen = {}  # order_hash -> update_number

    def validate(self):
        """Run all validation checks"""
        print(f"Starting data quality validation on: {self.log_file}")
        print("=" * 80)

        # Load and process all updates
        self._load_and_process()

        # Run validation checks
        self._check_status_transitions()
        self._check_field_completeness()
        self._check_duplicate_orders()
        self._check_impossible_states()
        self._check_order_lifecycle_sanity()

        # Report findings
        self._generate_report()

    def _load_and_process(self):
        """Load all updates and build order history"""
        print("Loading updates from log file...")

        update_count = 0

        with open(self.log_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    update_count += 1

                    update_num = data.get('update_number', line_num)
                    timestamp = data.get('timestamp')

                    # Process all orders in this update
                    all_orders = data.get('active_orders', [])

                    for order in all_orders:
                        self._track_order_state(order, update_num, timestamp)

                    # Track events
                    for order in data.get('new_orders', []):
                        self.stats['new_order_events'] += 1

                    for order in data.get('completed_orders', []):
                        self.stats['completed_order_events'] += 1

                    for order in data.get('canceled_orders', []):
                        self.stats['canceled_order_events'] += 1

                except json.JSONDecodeError as e:
                    self.issues.append({
                        'type': 'JSON_PARSE_ERROR',
                        'line': line_num,
                        'error': str(e)
                    })
                except Exception as e:
                    self.issues.append({
                        'type': 'PROCESSING_ERROR',
                        'line': line_num,
                        'error': str(e)
                    })

        print(f"✓ Loaded {update_count} updates")
        print(f"✓ Tracked {len(self.order_history)} unique orders")
        print()

    def _track_order_state(self, order: Dict, update_num: int, timestamp: str):
        """Track order state across updates"""
        # Try multiple ways to get order identifier
        order_hash = (
                order.get('order_hash') or
                order.get('hash') or
                f"{order.get('address')}_{order.get('side')}_{order.get('size')}"
        )

        if not order_hash:
            self.warnings.append({
                'type': 'MISSING_ORDER_HASH',
                'update': update_num,
                'order': order
            })
            return

        # Initialize tracking for this order
        if order_hash not in self.order_history:
            self.order_history[order_hash] = []
            self.order_first_seen[order_hash] = update_num

        self.order_last_seen[order_hash] = update_num

        # Record state
        state = {
            'update': update_num,
            'timestamp': timestamp,
            'status': order.get('status'),
            'is_active': order.get('is_active'),
            'side': order.get('side'),
            'size': order.get('size'),
            'product_type': order.get('product_type'),
            'address': order.get('address') or order.get('full_address'),
            'duration_hours': order.get('duration_hours'),
        }

        self.order_history[order_hash].append(state)

    def _check_status_transitions(self):
        """Check for invalid status transitions"""
        print("Checking status transitions...")

        invalid_transitions = []

        # Define valid transitions
        VALID_TRANSITIONS = {
            'active': {'canceled', 'completed', 'error', 'active'},
            'canceled': {'canceled'},  # Should stay canceled
            'completed': {'completed'},  # Should stay completed
            'error': {'error'},  # Should stay error
        }

        for order_hash, states in self.order_history.items():
            for i in range(len(states) - 1):
                current = states[i]
                next_state = states[i + 1]

                current_status = current['status']
                next_status = next_state['status']

                # Check if transition is valid
                if current_status in VALID_TRANSITIONS:
                    if next_status not in VALID_TRANSITIONS[current_status]:
                        invalid_transitions.append({
                            'order_hash': order_hash,
                            'from_update': current['update'],
                            'to_update': next_state['update'],
                            'from_status': current_status,
                            'to_status': next_status,
                            'address': current['address'],
                            'side': current['side'],
                            'size': current['size']
                        })

        if invalid_transitions:
            print(f"❌ Found {len(invalid_transitions)} invalid status transitions!")
            for trans in invalid_transitions[:10]:  # Show first 10
                print(f"  Order {trans['order_hash'][:16]}...")
                print(
                    f"    Update {trans['from_update']}: {trans['from_status']} → {trans['to_status']} (update {trans['to_update']})")
                print(f"    {trans['address']} {trans['side']} {trans['size']}")

            self.issues.extend(invalid_transitions)
        else:
            print("✓ No invalid status transitions detected")

        print()

    def _check_field_completeness(self):
        """Check for missing or malformed required fields"""
        print("Checking field completeness...")

        REQUIRED_FIELDS = ['address', 'side', 'size', 'status', 'product_type']

        missing_fields = defaultdict(int)
        orders_with_issues = []

        for order_hash, states in self.order_history.items():
            for state in states:
                for field in REQUIRED_FIELDS:
                    if not state.get(field):
                        missing_fields[field] += 1
                        orders_with_issues.append({
                            'order_hash': order_hash,
                            'update': state['update'],
                            'missing_field': field,
                            'state': state
                        })

        if missing_fields:
            print(f"⚠️  Found missing fields:")
            for field, count in missing_fields.items():
                print(f"  {field}: missing in {count} order states")

            self.warnings.extend(orders_with_issues)
        else:
            print("✓ All required fields present")

        print()

    def _check_duplicate_orders(self):
        """Check for duplicate orders with same characteristics"""
        print("Checking for duplicate orders...")

        # Group by address + side + size + product_type
        order_signatures = defaultdict(list)

        for order_hash, states in self.order_history.items():
            first_state = states[0]
            signature = (
                first_state.get('address'),
                first_state.get('side'),
                first_state.get('size'),
                first_state.get('product_type')
            )
            order_signatures[signature].append({
                'order_hash': order_hash,
                'first_seen': self.order_first_seen[order_hash],
                'last_seen': self.order_last_seen[order_hash]
            })

        duplicates = {sig: orders for sig, orders in order_signatures.items() if len(orders) > 1}

        if duplicates:
            print(f"⚠️  Found {len(duplicates)} sets of potential duplicate orders:")
            for sig, orders in list(duplicates.items())[:5]:  # Show first 5
                addr, side, size, ptype = sig
                print(f"  {addr} {side} {size} {ptype}:")
                for order in orders:
                    print(
                        f"    Hash: {order['order_hash'][:16]}... (updates {order['first_seen']}-{order['last_seen']})")

            self.warnings.append({
                'type': 'DUPLICATE_ORDERS',
                'count': len(duplicates),
                'details': duplicates
            })
        else:
            print("✓ No duplicate orders detected")

        print()

    def _check_impossible_states(self):
        """Check for logically impossible state combinations"""
        print("Checking for impossible states...")

        impossible_states = []

        for order_hash, states in self.order_history.items():
            for state in states:
                status = state.get('status')
                is_active = state.get('is_active')

                # Rule 1: status='active' should have is_active=True
                if status == 'active' and is_active is False:
                    impossible_states.append({
                        'order_hash': order_hash,
                        'update': state['update'],
                        'issue': 'status=active but is_active=False',
                        'state': state
                    })

                # Rule 2: status='canceled' should have is_active=False
                if status == 'canceled' and is_active is True:
                    impossible_states.append({
                        'order_hash': order_hash,
                        'update': state['update'],
                        'issue': 'status=canceled but is_active=True',
                        'state': state
                    })

                # Rule 3: status='completed' should have is_active=False
                if status == 'completed' and is_active is True:
                    impossible_states.append({
                        'order_hash': order_hash,
                        'update': state['update'],
                        'issue': 'status=completed but is_active=True',
                        'state': state
                    })

                # Rule 4: Check for negative or zero sizes
                size = state.get('size')
                if size is not None and size <= 0:
                    impossible_states.append({
                        'order_hash': order_hash,
                        'update': state['update'],
                        'issue': f'size={size} (should be positive)',
                        'state': state
                    })

        if impossible_states:
            print(f"❌ Found {len(impossible_states)} impossible states!")
            for issue in impossible_states[:10]:  # Show first 10
                print(f"  Update {issue['update']}: {issue['issue']}")
                print(f"    Order: {issue['order_hash'][:16]}...")

            self.issues.extend(impossible_states)
        else:
            print("✓ No impossible states detected")

        print()

    def _check_order_lifecycle_sanity(self):
        """Check for weird order lifecycle patterns"""
        print("Checking order lifecycle patterns...")

        issues = []

        for order_hash, states in self.order_history.items():
            # Pattern 1: Orders that appear, disappear, then reappear
            # This shouldn't happen with order hashes
            gaps = []
            for i in range(len(states) - 1):
                update_gap = states[i + 1]['update'] - states[i]['update']
                if update_gap > 2:  # More than 1 update gap
                    gaps.append({
                        'from_update': states[i]['update'],
                        'to_update': states[i + 1]['update'],
                        'gap': update_gap
                    })

            if gaps:
                issues.append({
                    'type': 'ORDER_REAPPEARANCE',
                    'order_hash': order_hash,
                    'gaps': gaps,
                    'first_state': states[0],
                    'last_state': states[-1]
                })

            # Pattern 2: Orders that stay in canceled/completed state for a long time
            final_status = states[-1]['status']
            if final_status in ['canceled', 'completed', 'error']:
                # Count how many updates it stayed in this terminal state
                terminal_count = 0
                for state in reversed(states):
                    if state['status'] == final_status:
                        terminal_count += 1
                    else:
                        break

                if terminal_count > 30:  # More than 30 minutes (assuming 1 update/min)
                    issues.append({
                        'type': 'LINGERING_TERMINAL_ORDER',
                        'order_hash': order_hash,
                        'terminal_status': final_status,
                        'updates_in_terminal': terminal_count,
                        'first_terminal_update': states[-terminal_count]['update'],
                        'last_update': states[-1]['update']
                    })

        if issues:
            print(f"⚠️  Found {len(issues)} lifecycle anomalies:")

            # Group by type
            by_type = defaultdict(list)
            for issue in issues:
                by_type[issue['type']].append(issue)

            for issue_type, items in by_type.items():
                print(f"  {issue_type}: {len(items)} cases")
                for item in items[:3]:  # Show first 3 of each type
                    print(f"    Order {item['order_hash'][:16]}...")
                    if issue_type == 'LINGERING_TERMINAL_ORDER':
                        print(f"      Status '{item['terminal_status']}' for {item['updates_in_terminal']} updates")

            self.warnings.extend(issues)
        else:
            print("✓ No lifecycle anomalies detected")

        print()

    def _generate_report(self):
        """Generate final validation report"""
        print("=" * 80)
        print("VALIDATION REPORT")
        print("=" * 80)
        print()

        print(f"Log file: {self.log_file}")
        print(f"Total orders tracked: {len(self.order_history)}")
        print(f"New order events: {self.stats['new_order_events']}")
        print(f"Completed order events: {self.stats['completed_order_events']}")
        print(f"Canceled order events: {self.stats['canceled_order_events']}")
        print()

        print(f"Critical Issues: {len([i for i in self.issues if i.get('type') != 'PROCESSING_ERROR'])}")
        print(f"Warnings: {len(self.warnings)}")
        print()

        if not self.issues and not self.warnings:
            print("✅ DATA QUALITY: EXCELLENT")
            print("No issues or anomalies detected!")
        elif self.issues:
            print("❌ DATA QUALITY: POOR")
            print(f"Found {len(self.issues)} critical issues that need fixing")
        else:
            print("⚠️  DATA QUALITY: ACCEPTABLE")
            print(f"Found {len(self.warnings)} warnings (non-critical)")

        print()
        print("=" * 80)

        # Offer to save detailed report
        self._save_detailed_report()

    def _save_detailed_report(self):
        """Save detailed report to JSON file"""
        report_file = f"validation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        report = {
            'timestamp': datetime.now().isoformat(),
            'log_file': self.log_file,
            'summary': {
                'total_orders': len(self.order_history),
                'critical_issues': len(self.issues),
                'warnings': len(self.warnings),
                'stats': dict(self.stats)
            },
            'issues': self.issues,
            'warnings': self.warnings
        }

        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)

        print(f"Detailed report saved to: {report_file}")


def main():
    import sys
    from glob import glob

    # Auto-detect log file
    log_dir = Path('json_logs')

    if not log_dir.exists():
        print(f"Error: json_logs directory not found!")
        print(f"Please create it or adjust the path in the script.")
        sys.exit(1)

    # Find all HYPE log files
    log_files = sorted(glob(str(log_dir / 'HYPE_*.jsonl')), reverse=True)

    if not log_files:
        print(f"Error: No HYPE_*.jsonl files found in {log_dir}")
        sys.exit(1)

    # Use the most recent log file
    log_file = log_files[0]

    print(f"Auto-detected log file: {log_file}")
    print()

    validator = TWAPDataValidator(log_file)
    validator.validate()


if __name__ == "__main__":
    main()