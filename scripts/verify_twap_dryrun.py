#!/usr/bin/env python3
"""
verify_twap_dryrun.py — READ-ONLY dry run of the twapHistory verifier.

One address in. Fetches twapHistory, collapses entries per twapId, binds
chain orders to DB rows on (address, placed_at_ms), and PRINTS what the
verifier would write. The DB is opened with mode=ro: this script cannot
write, even by accident.

Collapse rules (decided Oct 2 2026):
  - group on twapId; entries without twapId (pre 2025-10-12) are counted, skipped
  - placed_at_ms = EARLIEST state.timestamp in the group (trigger TWAPs
    carry a second, later timestamp once the trigger fires)
  - representative entry: terminal > activated > waitingForTrigger
  - executed_sz / executed_ntl / chain_end_s only from a TERMINAL entry

Usage (from repo root):
  python3 scripts/verify_twap_dryrun.py <address> [--all]
"""
import json
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"
DB = "data/twap.db"
TERMINAL = {"finished", "terminated", "error", "stopped"}
STATE_NAME = {"waitingForTrigger": "waiting", "activated": "running"}


def fetch(addr):
    body = json.dumps({"type": "twapHistory", "user": addr}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def rank(status):
    if status in TERMINAL:
        return 2
    return {"activated": 1, "waitingForTrigger": 0}.get(status, -1)


def collapse(entries):
    groups, no_id = defaultdict(list), 0
    for e in entries:
        tid = e.get("twapId")
        if tid is None:
            no_id += 1
            continue
        groups[tid].append(e)

    orders = {}
    for tid, es in groups.items():
        best = max(es, key=lambda e: (rank(e["status"]["status"]), e["time"]))
        raw = best["status"]["status"]
        terminal = raw in TERMINAL
        st = best["state"]
        orders[tid] = dict(
            twap_id=tid,
            placed_at_ms=min(e["state"]["timestamp"] for e in es),
            coin=st.get("coin"),
            side=st.get("side"),
            chain_state=raw if terminal else STATE_NAME.get(raw, "unknown"),
            raw_status=raw,
            executed_sz=float(st["executedSz"]) if terminal else None,
            executed_ntl=float(st["executedNtl"]) if terminal else None,
            chain_end_s=best["time"] if terminal else None,
            n_entries=len(es),
        )
    return orders, no_id


def iso_to_s(s):
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # first_seen_at is UTC (checked Oct 3)
    return dt.timestamp()


def fmt_ms(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M:%S")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    addr = sys.argv[1].lower()
    show_all = "--all" in sys.argv

    entries = fetch(addr)
    chain, no_id = collapse(entries)
    oldest_ms = min((e["state"]["timestamp"] for e in entries), default=None)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT order_hash, symbol, side, status, placed_at_ms,
               completed_at, canceled_at
        FROM orders WHERE lower(address) = ?
    """, (addr,)).fetchall()

    chain_by_ms = defaultdict(list)
    for o in chain.values():
        chain_by_ms[o["placed_at_ms"]].append(o)

    db_by_ms, legacy = defaultdict(list), []
    for r in rows:
        if r["placed_at_ms"] is None:
            legacy.append(r)
        else:
            db_by_ms[r["placed_at_ms"]].append(r)

    bound, ambiguous, unmatched, outside = [], [], [], []
    for ms, d in db_by_ms.items():
        c = chain_by_ms.get(ms, [])
        if len(d) == 1 and len(c) == 1:
            bound.append((d[0], c[0]))
        elif not c:
            (outside if oldest_ms and ms < oldest_ms else unmatched).extend(d)
        else:
            ambiguous.append((ms, d, c))
    chain_only = sum(len(v) for ms, v in chain_by_ms.items() if ms not in db_by_ms)

    # ---- header ----
    print(f"address        {addr}")
    print(f"chain entries  {len(entries)}  ->  {len(chain)} orders by twapId"
          f"  ({no_id} entries without twapId skipped)")
    if oldest_ms:
        print(f"history window from {fmt_ms(oldest_ms)} UTC")
    print(f"db rows        {len(rows)}  ({len(legacy)} legacy, placed_at_ms NULL)")
    print()
    print(f"bound 1:1      {len(bound)}")
    print(f"ambiguous      {len(ambiguous)}  (same ms, several rows or twapIds)")
    print(f"db unmatched   {len(unmatched)}  (inside window, no chain order)")
    print(f"db outside     {len(outside)}  (placed before history window)")
    print(f"chain only     {chain_only}  (on chain, not in DB)")
    print()

    # ---- agreement matrix: DB status vs chain state ----
    print("DB status -> chain state")
    for (db_st, ch_st), n in Counter((d["status"], c["chain_state"]) for d, c in bound).most_common():
        print(f"  {db_st:<10} -> {ch_st:<11} {n}")
    print()

    # ---- the bindings themselves ----
    print(f"{'placed (UTC)':<15} {'sym/coin':<14} {'db status':<10} {'chain':<11} "
          f"{'exec_ntl':>12} {'Δend min':>9}  twap_id")
    shown = bound if show_all else bound[:25]
    for d, c in sorted(shown, key=lambda x: x[1]["placed_at_ms"], reverse=True):
        db_end = iso_to_s(d["completed_at"] or d["canceled_at"])
        delta = ""
        if db_end is not None and c["chain_end_s"] is not None:
            delta = f"{(db_end - c['chain_end_s']) / 60:+.0f}"
        ntl = f"{c['executed_ntl']:,.0f}" if c["executed_ntl"] is not None else "-"
        sym = f"{d['symbol']}/{c['coin']}"
        print(f"{fmt_ms(c['placed_at_ms']):<15} {sym:<14} {d['status']:<10} "
              f"{c['chain_state']:<11} {ntl:>12} {delta:>9}  {c['twap_id']}")
    if not show_all and len(bound) > 25:
        print(f"  ... {len(bound) - 25} more (use --all)")

    for ms, d, c in ambiguous:
        print(f"\nAMBIGUOUS at {fmt_ms(ms)}: db {[r['order_hash'][:10] for r in d]}"
              f"  chain {[o['twap_id'] for o in c]}")


if __name__ == "__main__":
    main()