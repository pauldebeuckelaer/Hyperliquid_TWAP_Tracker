#!/usr/bin/env python3
"""Backfill tier_change events from tier_manager DEBUG logs."""
import re, sqlite3, sys
from pathlib import Path

DB    = Path(__file__).resolve().parent.parent / "data" / "twap.db"
LOGS  = Path(__file__).resolve().parent.parent / "logs"
CUT   = "2026-08-28T19:53:28"          # live branch owns everything >= this
SRC   = "backfill_log"

LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \| DEBUG \| [\w.]+ \| "
    r"[⬆⬇] (0x[0-9a-f]{40}) T(\d+)→T(\d+)"
)

def parse(files):
    rows, seen = [], set()
    for f in files:
        for ln in open(f, encoding="utf-8"):
            m = LINE.match(ln)
            if not m:
                continue
            d, t, addr, prev, tier = m.groups()
            ts = f"{d}T{t}"
            key = (addr, ts)
            if key in seen:               # same wallet twice in one refresh
                print(f"  DUP {addr} @ {ts}", file=sys.stderr)
                continue
            seen.add(key)
            rows.append((addr, ts, int(prev), int(tier)))
    return rows

def main():
    rows = parse(sorted(LOGS.glob("tier_manager.log.2026-08-2[78]")))
    live = [r for r in rows if r[1] >= CUT]
    back = [r for r in rows if r[1] <  CUT]
    print(f"parsed {len(rows)}  ->  backfill {len(back)}  live-window {len(live)}")

    con = sqlite3.connect(DB)
    cur = con.cursor()

    # regression: the post-cut rows must already exist, identically
    ok = bad = 0
    for addr, ts, prev, tier in live:
        hit = cur.execute(
            "SELECT prev_tier, tier FROM whale_lifecycle_events "
            "WHERE address=? AND event_type='tier_change' "
            "AND event_time >= ? AND event_time < datetime(?, '+2 minutes')",
            (addr, ts, ts.replace("T", " "))).fetchone()
        if hit and hit == (prev, tier):
            ok += 1
        else:
            bad += 1
            print(f"  MISMATCH {addr} {ts} log={prev}->{tier} db={hit}")
    print(f"regression: {ok} match, {bad} mismatch (expect 11 / 0)")

    # collision check: does anything already sit in the backfill window?
    n = cur.execute(
        "SELECT COUNT(*) FROM whale_lifecycle_events "
        "WHERE event_type='tier_change' AND event_time < ?", (CUT,)).fetchone()[0]
    print(f"existing tier_change rows before cut: {n} (expect 0)")

    if "--commit" not in sys.argv:
        for r in back[:5]:
            print("  would insert:", r)
        print(f"  ... {len(back)} total. dry run, nothing written.")
        return

    cur.executemany(
        "INSERT INTO whale_lifecycle_events "
        "(address, event_time, event_type, source, tier, prev_tier) "
        "VALUES (?,?,'tier_change',?,?,?)",
        [(a, ts, SRC, tier, prev) for a, ts, prev, tier in back])
    con.commit()
    print(f"inserted {len(back)} rows")

main()