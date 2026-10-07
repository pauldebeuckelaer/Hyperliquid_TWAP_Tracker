#!/usr/bin/env python3
"""
test_cleanup_out_of_window.py — cleanup_stale_orders must never call a
>24h TWAP 'completed': its end always falls outside Hypurrscan's 24h
window, so cleanup has no evidence of how it ended.

Runs against a throwaway DB in /tmp. Never touches data/twap.db.

Expected: BEFORE the cleanup patch, case A fails (it gets 'completed').
          AFTER the patch, all cases pass.

Usage (repo root):
  box:        PYTHONPATH=. venv/bin/python scripts/test_cleanup_out_of_window.py
  PowerShell: $env:PYTHONPATH="."; python scripts\\test_cleanup_out_of_window.py
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.twap_storage import TwapStorage

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def naive_utc(delta):
    """Timestamp in the tracker's normal format: naive ISO, UTC."""
    return (datetime.now(timezone.utc) - delta).replace(tzinfo=None).isoformat()


def insert(st, order_hash, duration, age, status="active"):
    ts = naive_utc(age)
    st.cursor.execute("""
        INSERT INTO orders (order_hash, address, symbol, side, size, product_type,
                            duration_minutes, status, first_seen_at, last_seen_at)
        VALUES (?, '0xtest', 'TEST', 'BUY', 1.0, 'PERP', ?, ?, ?, ?)
    """, (order_hash, duration, status, ts, ts))
    st.conn.commit()


def row(st, order_hash):
    st.cursor.execute("""
        SELECT status, completed_at, final_progress_percent
        FROM orders WHERE order_hash = ?
    """, (order_hash,))
    return tuple(st.cursor.fetchone())


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cleanup_oow_")) / "test.db"
    st = TwapStorage(tmp)
    print(f"throwaway DB: {tmp}\n")

    three_days = timedelta(days=3)
    insert(st, "A_long_due",    2000, three_days)                    # >24h, past its end
    insert(st, "B_short_due",     60, three_days)                    # <=24h, past its end
    insert(st, "C_long_notdue", 2000, timedelta(hours=1))            # >24h, still running
    insert(st, "D_long_closed", 2000, three_days, status="canceled") # already closed

    cleaned = st.cleanup_stale_orders()

    print("A: >24h order past its end -> out_of_window, no end written")
    status, completed_at, prog = row(st, "A_long_due")
    check("status is out_of_window", status == "out_of_window", f"got {status!r}")
    check("completed_at stays NULL", completed_at is None, f"got {completed_at!r}")
    check("final_progress_percent stays NULL", prog is None, f"got {prog!r}")

    print("B: <=24h order past its end -> completed as before")
    status, completed_at, prog = row(st, "B_short_due")
    check("status is completed", status == "completed", f"got {status!r}")
    check("completed_at is set", completed_at is not None)
    check("final_progress_percent is 100", prog == 100.0, f"got {prog!r}")

    print("C: >24h order not yet due -> untouched")
    status, completed_at, _ = row(st, "C_long_notdue")
    check("status still active", status == "active", f"got {status!r}")
    check("completed_at still NULL", completed_at is None, f"got {completed_at!r}")

    print("D: already-closed order -> untouched")
    status, completed_at, _ = row(st, "D_long_closed")
    check("status still canceled", status == "canceled", f"got {status!r}")

    print("Return value")
    check("cleaned == 2 (A and B)", cleaned == 2, f"got {cleaned}")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()