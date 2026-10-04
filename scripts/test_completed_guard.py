#!/usr/bin/env python3
"""Throwaway-DB test: does a late 'completed' overwrite an end time set by cleanup?"""
import os, sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from storage import SQLiteBackend

P = Path("/tmp/test_guard.db")
if P.exists():
    P.unlink()
db = SQLiteBackend(P)

# A 5-min order first seen 30 min ago -> long past its scheduled end
first = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(tzinfo=None).isoformat()
order = {"order_hash": "0xtest", "address": "0xabc", "side": "BUY", "size": 1.0,
         "product_type": "PERP", "duration_minutes": 5, "status": "active",
         "asset_id": 1, "placed_at_ms": None}
db.save_snapshot("TEST", {"timestamp": first, "summary": {}, "active_orders": [order]}, {})
db.commit()

print("cleanup closed:", db.cleanup_stale_orders())
row = lambda: sqlite3.connect(P).execute(
    "SELECT status, completed_at, final_progress_percent FROM orders WHERE order_hash='0xtest'").fetchone()
after_cleanup = row()
print("after cleanup:", after_cleanup)

# The frozen coin 'comes back': compare_with reports the order as gone -> completed
late = datetime.now().isoformat()
db.save_snapshot("TEST", {"timestamp": late, "summary": {}, "active_orders": []},
                 {"completed_orders": [{"order_hash": "0xtest", "progress_percent": 42.0}]})
db.commit()
after_late = row()
print("after late completed:", after_late)
print("RESULT:", "KEPT (guard works)" if after_late == after_cleanup else "OVERWRITTEN (bug present)")
