#!/usr/bin/env python3
"""Throwaway test: >24h orders that age out of Hypurrscan's list become
'out_of_window', not 'completed'.
Box:  PYTHONPATH=. venv/bin/python scripts/test_out_of_window.py
PC:   $env:PYTHONPATH="."; python scripts\\test_out_of_window.py"""
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from api_client.models import TWAPOrder
from storage import SQLiteBackend
from trackers.state_tracker import _left_window, AllCoinsStateTracker

NOW_MS = int(time.time() * 1000)
results = []


def check(name, ok):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def mk(h, dur, age_min, placed=True):
    return TWAPOrder(
        address=h[:10], full_address='0xtest', symbol='XMR', size=1.0,
        side='BUY', product_type='PERP', status='active',
        duration_minutes=dur, order_hash=h,
        placed_at_ms=NOW_MS - age_min * 60000 if placed else None,
        elapsed_minutes=age_min,
        progress_percent=round(age_min / dur * 100, 1),
    )


# 1. Classification
check("7d order gone at 24h -> out of window", _left_window(mk('0x1', 10080, 1441)))
check("7d order gone at 10h -> real ending", not _left_window(mk('0x2', 10080, 600)))
check("30m order gone late -> never out of window", not _left_window(mk('0x3', 30, 1441)))
check("no placed_at_ms, elapsed 1439 -> fallback works", _left_window(mk('0x4', 4320, 1439, placed=False)))

# 3. Wiring: _detect_changes must carry aged-out orders into its return dict.
# This is the layer that broke on Oct 5 (key misplaced, then missing).
t = AllCoinsStateTracker.__new__(AllCoinsStateTracker)  # skip __init__: no real DB
aged = mk('0xd1', 10080, 1441)
early = mk('0xd2', 10080, 600)
ch = t._detect_changes('XMR', {'new_orders': [], 'completed_orders': [aged, early],
                               'status_changes': []})
oow = [x.order_hash for x in ch.get('out_of_window_orders', [])]
comp = [x.order_hash for x in ch.get('completed_orders', [])]
check("_detect_changes returns out_of_window_orders key", 'out_of_window_orders' in ch)
check("aged-out order routed to out_of_window", oow == ['0xd1'])
check("early vanish still routed to completed", comp == ['0xd2'])
empty = t._detect_changes('XMR', {'completed_orders': [], 'status_changes': []})
check("key present even with nothing to report", empty.get('out_of_window_orders') == [])

# 2. Storage, throwaway DB
tmp = Path(tempfile.mkdtemp()) / 'test.db'
db = SQLiteBackend(tmp)
o = mk('0xabc', 10080, 1441)
row = {'address': '0xtest', 'order_hash': '0xabc', 'side': 'BUY', 'size': 1.0,
       'product_type': 'PERP', 'duration_minutes': 10080, 'status': 'active',
       'placed_at_ms': o.placed_at_ms}
t0 = datetime.now()
ts = lambda m: (t0 + timedelta(minutes=m)).isoformat()

db.save_snapshot('XMR', {'timestamp': ts(0), 'summary': {}, 'active_orders': [row]}, {})
db.save_snapshot('XMR', {'timestamp': ts(1), 'summary': {}, 'active_orders': []},
                 {'out_of_window_orders': [o]})
db.commit()

con = sqlite3.connect(tmp)
q = lambda sql: con.execute(sql).fetchone()
status, completed_at = q("SELECT status, completed_at FROM orders WHERE order_hash='0xabc'")
n_oow = q("SELECT COUNT(*) FROM events WHERE order_hash='0xabc' AND event_type='out_of_window'")[0]
check("row marked out_of_window, completed_at NULL", status == 'out_of_window' and completed_at is None)
check("one out_of_window event recorded", n_oow == 1)

# A late 'completed' (e.g. a frozen close) must not overwrite it
db.save_snapshot('XMR', {'timestamp': ts(6), 'summary': {}, 'active_orders': []},
                 {'completed_orders': [o]})
db.commit()
status = q("SELECT status FROM orders WHERE order_hash='0xabc'")[0]
n_comp = q("SELECT COUNT(*) FROM events WHERE order_hash='0xabc' AND event_type='completed'")[0]
check("late completion ignored, no completed event", status == 'out_of_window' and n_comp == 0)

# Cleanup must skip it even when long past its scheduled end
con.execute("UPDATE orders SET first_seen_at = ? WHERE order_hash='0xabc'",
            ((t0 - timedelta(days=8)).isoformat(),))
con.commit()
db.cleanup_stale_orders()
status = q("SELECT status FROM orders WHERE order_hash='0xabc'")[0]
check("cleanup leaves out_of_window rows alone", status == 'out_of_window')

con.close()
db.close()
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)