#!/usr/bin/env python3
"""Throwaway test for the frozen-coin fix (bug 1).

Uses a temp DB in a temp dir. Never touches data/twap.db.
Run from repo root:  PYTHONPATH=. python scripts/test_frozen_coin_fix.py
"""
import sqlite3
import tempfile
import time
from pathlib import Path

import trackers.state_tracker as st

USER = "0x" + "ab" * 20
NOW_MS = int(time.time() * 1000)
N = st.EMPTY_POLLS_TO_CLOSE


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


def fresh_tracker():
    db = Path(tempfile.mkdtemp()) / "test.db"
    st.DB_PATH = db  # must be set before the tracker opens its DB
    return st.AllCoinsStateTracker(), db


def row(db, h):
    con = sqlite3.connect(db)
    r = con.execute(
        "SELECT status, completed_at FROM orders WHERE order_hash = ?", (h,)
    ).fetchone()
    n = con.execute(
        "SELECT COUNT(*) FROM events WHERE order_hash = ? AND event_type = 'completed'", (h,)
    ).fetchone()[0]
    con.close()
    return r, n


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    return ok


def test_closes_after_n():
    t, db = fresh_tracker()
    t.update({"AAA": [raw("h1", 1)], "BBB": [raw("h2", 2)]})
    seen = []
    for _ in range(N):
        t.update({"BBB": [raw("h2", 2)]})  # AAA absent
        seen.append(row(db, "h1")[0][0])
    (status, _), n = row(db, "h1")
    other = row(db, "h2")[0][0]
    t.close()
    ok = (seen[:-1] == ["active"] * (N - 1) and status == "completed"
          and n == 1 and other == "active")
    return check("closes after exactly N empty polls", ok,
                 f"per-poll={seen} events={n} BBB={other}")


def test_brake_holds():
    t, db = fresh_tracker()
    coins = {f"C{i}": [raw(f"c{i}", 10 + i)] for i in range(6)}
    coins["KEEP"] = [raw("k", 99)]
    t.update(coins)
    for _ in range(2 * N):
        t.update({"KEEP": [raw("k", 99)]})  # 6 of 7 coins vanish together
    statuses = [row(db, f"c{i}")[0][0] for i in range(6)]
    t.close()
    return check("brake holds on mass vanish", all(s == "active" for s in statuses),
                 f"statuses={statuses}")


def test_no_duplicate_after_cleanup():
    t, db = fresh_tracker()
    t.update({"AAA": [raw("h1", 1)], "BBB": [raw("h2", 2)]})
    con = sqlite3.connect(db)  # simulate cleanup closing h1 first
    con.execute("UPDATE orders SET status = 'completed', completed_at = 'CLEANUP' "
                "WHERE order_hash = 'h1'")
    con.commit()
    con.close()
    for _ in range(N):
        t.update({"BBB": [raw("h2", 2)]})
    (status, completed_at), n = row(db, "h1")
    t.close()
    ok = status == "completed" and completed_at == "CLEANUP" and n == 0
    return check("cleanup's close survives, no duplicate event", ok,
                 f"status={status} completed_at={completed_at} events={n}")


if __name__ == "__main__":
    results = [test_closes_after_n(), test_brake_holds(), test_no_duplicate_after_cleanup()]
    print("ALL PASS" if all(results) else "SOME FAILED")