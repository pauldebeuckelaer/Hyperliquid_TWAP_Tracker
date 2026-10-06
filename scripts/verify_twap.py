#!/usr/bin/env python3
"""
verify_twap.py — write twapHistory (chain) truth into the orders table.

Reuses fetch / collapse / bind from verify_twap_dryrun.py, so there is one
copy of the collapse rules. Writes ONLY these columns, on rows bound 1:1
on (address, placed_at_ms):
    twap_id, chain_status            always
    chain_end_s, executed_sz,
    executed_ntl                     only when the chain state is terminal
Never touches status / completed_at / canceled_at (the tracker's view).

Rows placed before a wallet's twapHistory window (2000-entry cap) can never
be resolved; they get chain_status = 'unreachable'.

Guard: rows whose chain_status is already final (terminal or 'unreachable')
are never rewritten. running / waiting rows are refreshed on every run.

Scope: Era 2 only (placed_at_ms NOT NULL) — binding needs the exact key.

Usage (repo root):
  PYTHONPATH=. venv/bin/python scripts/verify_twap.py --db PATH [--limit N] [--pause S] [--dry]
    --db     required, no default: the live DB is never hit by accident
    --limit  max wallets this run (default 10)
    --pause  seconds between twapHistory calls (default 3)
    --dry    do all writes per wallet, then roll back; exact counts, no change
"""
import argparse
import sqlite3
import time
from collections import Counter

from scripts.verify_twap_dryrun import analyze, TERMINAL

FINAL = TERMINAL | {"unreachable"}
FINAL_SQL = "(" + ",".join(f"'{s}'" for s in sorted(FINAL)) + ")"
NOT_FINAL = f"(chain_status IS NULL OR chain_status NOT IN {FINAL_SQL})"

UPD_BOUND = f"""
UPDATE orders SET twap_id = ?, chain_status = ?, chain_end_s = ?,
                  executed_sz = ?, executed_ntl = ?
WHERE order_hash = ? AND {NOT_FINAL}"""

UPD_UNREACHABLE = f"""
UPDATE orders SET chain_status = 'unreachable'
WHERE order_hash = ? AND {NOT_FINAL}"""


def pick_wallets(con, limit):
    """Wallets with unresolved Era 2 rows, oldest unresolved first:
    those are the closest to falling off the 2000-entry cap."""
    return [r[0] for r in con.execute(f"""
        SELECT lower(address) AS a FROM orders
        WHERE placed_at_ms IS NOT NULL AND {NOT_FINAL}
        GROUP BY a ORDER BY MIN(placed_at_ms) LIMIT ?""", (limit,))]


def write_wallet(con, r):
    """Apply one wallet's bind results. Returns counters; caller commits."""
    n = Counter()
    for d, c in r["bound"]:
        cur = con.execute(UPD_BOUND, (
            c["twap_id"], c["chain_state"], c["chain_end_s"],
            c["executed_sz"], c["executed_ntl"], d["order_hash"]))
        if cur.rowcount:
            n["final" if c["chain_state"] in TERMINAL else "live"] += 1
            n[f"state:{c['chain_state']}"] += 1
        else:
            n["guarded"] += 1
    for d in r["outside"]:
        n["unreachable"] += con.execute(UPD_UNREACHABLE, (d["order_hash"],)).rowcount
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--pause", type=float, default=3.0)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(a.db, timeout=10)
    con.row_factory = sqlite3.Row
    wallets = pick_wallets(con, a.limit)

    mode = "DRY RUN (each wallet rolled back)" if a.dry else "WRITE"
    print(f"{mode}  db={a.db}  wallets={len(wallets)}  pause={a.pause}s\n")
    print(f"{'addr':<12} {'entries':>8} {'bound':>6} {'final':>6} {'live':>5} "
          f"{'guard':>6} {'unreach':>8} {'amb':>4} {'unm':>4}")

    tot, failed = Counter(), []
    for i, addr in enumerate(wallets):
        if i:
            time.sleep(a.pause)
        try:
            r = analyze(addr, con, 0)
            n = write_wallet(con, r)
            if a.dry:
                con.rollback()
            else:
                con.commit()
        except Exception as e:
            con.rollback()
            failed.append((addr, repr(e)))
            print(f"{addr[:10]:<12} FAILED  {e!r}")
            continue

        n["bound"] += len(r["bound"])
        n["amb"] += len(r["ambiguous"])
        n["unm"] += len(r["unmatched"])
        tot.update(n)
        cap = "*" if len(r["entries"]) >= 2000 else " "
        print(f"{addr[:10]:<12} {len(r['entries']):>7}{cap} {len(r['bound']):>6} "
              f"{n['final']:>6} {n['live']:>5} {n['guarded']:>6} {n['unreachable']:>8} "
              f"{len(r['ambiguous']):>4} {len(r['unmatched']):>4}")

    print("  (* = at the 2000-entry cap)\n")
    print(f"TOTAL  bound {tot['bound']}  written: final {tot['final']}, live {tot['live']}, "
          f"unreachable {tot['unreachable']}  guarded {tot['guarded']}  "
          f"ambiguous {tot['amb']}  unmatched {tot['unm']}  failed {len(failed)}")
    states = sorted(((k[6:], v) for k, v in tot.items() if k.startswith("state:")),
                    key=lambda x: -x[1])
    if states:
        print("chain states written: " + ", ".join(f"{k} {v}" for k, v in states))
    for addr, err in failed:
        print(f"FAILED {addr}: {err}")
    con.close()


if __name__ == "__main__":
    main()