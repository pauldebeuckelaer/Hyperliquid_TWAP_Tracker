#!/usr/bin/env python3
"""Throwaway test for the restart blind spot (duplicate 'new' events).

Uses a temp DB in a temp dir. Never touches data/twap.db.
Run from repo root:  PYTHONPATH=. python scripts/test_restart_new_events.py

Expected BEFORE the fix: case 1 and 3 FAIL, case 2 PASS.
Expected AFTER the fix:  ALL PASS.
"""
import sqlite3
import tempfile
import time
from pathlib import Path

import trackers.state_tracker as st

USER = "0x" + "ab" * 20
NOW_MS = int(time.time() * 1000)


def raw(h, asset_id):
    """Minimal Hypurrscan-shaped active TWAP order."""
    return {
        "hash": h,
        "user": USER,
        "time": NOW_MS - 60_000,
        "action": {"type": "twapOrder",
                   "twap": {"a": asset_id, "b": True, "s": 10.0, "m": 30}},
        "ended": None,
        "error": None,
    }


def new_db():
    return Path(tempfile.mkdtemp()) / "test.db"


def tracker_on(db):
    """Open a tracker on an existing (or new) DB path. A second call on the
    same path is a 'restart': same DB, empty in-memory state."""
    st.DB_PATH = db  # must be set before the tracker opens its DB
    return st.AllCoinsStateTracker()


def count(db, h, event_type):
    con = sqlite3.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM events WHERE order_hash = ? AND event_type = ?",
        (h, event_type),
    ).fetchone()[0]
    con.close()
    return n


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    return ok


def test_restart_no_duplicate():
    """Order seen, tracker restarts, same order still listed."""
    db = new_db()
    t = tracker_on(db)
    t.update({"AAA": [raw("h1", 1)]})
    t.close()

    t = tracker_on(db)  # restart: previous_snapshot is None again
    t.update({"AAA": [raw("h1", 1)]})
    t.close()

    n = count(db, "h1", "new")
    return check("restart: known order not re-announced", n == 1, f"new_events={n}")


def test_truly_new_on_first_poll():
    """After a restart, an order the DB has never seen is still new."""
    db = new_db()
    t = tracker_on(db)
    t.update({"AAA": [raw("h1", 1)]})
    t.close()

    t = tracker_on(db)
    t.update({"AAA": [raw("h1", 1), raw("h4", 1)]})  # h4 placed during downtime
    t.close()

    n_old, n_new = count(db, "h1", "new"), count(db, "h4", "new")
    return check("first poll: truly new order still announced", n_new == 1,
                 f"h4_new={n_new} (h1_new={n_old}, informational)")


def test_flicker_no_duplicate():
    """Order drops out of its coin's list for one poll, then comes back.
    The coin stays present (h3), so compare_with sees h1 as gone, then new."""
    db = new_db()
    t = tracker_on(db)
    t.update({"AAA": [raw("h1", 1), raw("h3", 1)]})
    t.update({"AAA": [raw("h3", 1)]})                 # h1 flickers out
    t.update({"AAA": [raw("h1", 1), raw("h3", 1)]})   # h1 back
    t.close()

    n = count(db, "h1", "new")
    return check("flicker: returning order not re-announced", n == 1, f"new_events={n}")


if __name__ == "__main__":
    results = [
        test_restart_no_duplicate(),
        test_truly_new_on_first_poll(),
        test_flicker_no_duplicate(),
    ]
    print("ALL PASS" if all(results) else "SOME FAILED")