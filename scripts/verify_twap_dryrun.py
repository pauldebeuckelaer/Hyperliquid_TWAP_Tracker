#!/usr/bin/env python3
"""
verify_twap_dryrun.py — READ-ONLY dry run of the twapHistory verifier.

Fetches twapHistory, collapses entries per twapId, binds chain orders to DB
rows on (address, placed_at_ms), and PRINTS what the verifier would write.
The DB is opened with mode=ro: this script cannot write, even by accident.

Collapse rules (decided Oct 2 2026):
  - group on twapId; entries without twapId (pre 2025-10-12) are counted, skipped
  - placed_at_ms = EARLIEST state.timestamp in the group (trigger TWAPs
    carry a second, later timestamp once the trigger fires)
  - representative entry: terminal > activated > waitingForTrigger
  - executed_sz / executed_ntl / chain_end_s only from a TERMINAL entry

Notes from the first runs (Oct 3 2026):
  - final_progress_percent in the DB is ELAPSED TIME / duration, not fill.
  - Δend = DB end minus chain end, minutes. Positive = DB noticed late.
    Negative = DB ended an order the chain was still running (24h drop).
  - capture cutoff = latest first_seen_at of any row WITHOUT placed_at_ms.
    Chain-only orders placed after it are real misses (never seen in the
    Hypurrscan list); before it they are legacy rows we can't bind exactly.
  - HIP-3 endings arrive late from Hypurrscan (3 wallets: 65% >5 min late
    vs 0.7% for the rest).

Usage (from repo root):
  python3 scripts/verify_twap_dryrun.py <address> [--all]
  python3 scripts/verify_twap_dryrun.py --top N [--pause S] [--all]
      aggregate over the N wallets with the most rows since the cutoff;
      one twapHistory call per wallet, S seconds apart (default 3)
"""
import json
import sqlite3
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"
DB = "data/twap.db"
TERMINAL = {"finished", "terminated", "error", "stopped"}
STATE_NAME = {"waitingForTrigger": "waiting", "activated": "running"}
INSTANT_MIN = 1.0   # misses that lived less than this are "instant cancels"


# ---------------------------------------------------------------- helpers

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
            minutes=st.get("minutes"),
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


def delta_min(d, c):
    """DB end minus chain end, in minutes. None if either side has no end."""
    db_end = iso_to_s(d["completed_at"] or d["canceled_at"])
    if db_end is None or c["chain_end_s"] is None:
        return None
    return (db_end - c["chain_end_s"]) / 60


def lived_min(o):
    """Chain lifetime in minutes; None while still running."""
    if o["chain_end_s"] is None:
        return None
    return (o["chain_end_s"] - o["placed_at_ms"] / 1000) / 60


def is_hip3(coin):
    return ":" in (coin or "")


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def get_cutoff_ms(con):
    cut = con.execute(
        "SELECT MAX(first_seen_at) FROM orders WHERE placed_at_ms IS NULL"
    ).fetchone()[0]
    return int(iso_to_s(cut) * 1000) if cut else 0


# ---------------------------------------------------------------- core

def analyze(addr, con, cutoff_ms):
    """Fetch + collapse + bind one address. Returns a dict, prints nothing."""
    entries = fetch(addr)
    chain, no_id = collapse(entries)
    oldest_ms = min((e["state"]["timestamp"] for e in entries), default=None)

    rows = con.execute("""
        SELECT order_hash, symbol, side, status, placed_at_ms,
               completed_at, canceled_at
        FROM orders WHERE address = ?
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
    chain_only = [o for ms, v in chain_by_ms.items() if ms not in db_by_ms for o in v]
    misses = sorted((o for o in chain_only if o["placed_at_ms"] > cutoff_ms),
                    key=lambda o: o["placed_at_ms"], reverse=True)
    chain_post = sum(1 for o in chain.values() if o["placed_at_ms"] > cutoff_ms)

    return dict(addr=addr, entries=entries, chain=chain, no_id=no_id,
                oldest_ms=oldest_ms, rows=rows, legacy=legacy, bound=bound,
                ambiguous=ambiguous, unmatched=unmatched, outside=outside,
                chain_only=chain_only, misses=misses, chain_post=chain_post)


def matrix(bound):
    return Counter((d["status"], c["chain_state"]) for d, c in bound)


def delta_groups(bound):
    groups = {"hip3": [], "other": []}
    for d, c in bound:
        dm = delta_min(d, c)
        if dm is not None:
            groups["hip3" if is_hip3(c["coin"]) else "other"].append(dm)
    return groups


def print_matrix(m):
    print("DB status -> chain state")
    for (db_st, ch_st), n in m.most_common():
        print(f"  {db_st:<10} -> {ch_st:<11} {n}")
    print()


def print_deltas(groups):
    print("Δend minutes (DB end - chain end)")
    print(f"  {'group':<6} {'n':>5} {'min':>7} {'median':>7} {'p90':>7} {'max':>7} {'>5min':>6}")
    for g, xs in groups.items():
        if xs:
            print(f"  {g:<6} {len(xs):>5} {min(xs):>7.0f} {q(xs, .5):>7.0f} {q(xs, .9):>7.0f}"
                  f" {max(xs):>7.0f} {sum(x > 5 for x in xs):>6}")
        else:
            print(f"  {g:<6} {0:>5}")
    print()


def print_misses(misses, show_all, with_addr=False):
    """misses: list of chain-order dicts (optionally with 'addr')."""
    head = f"{'addr':<12} " if with_addr else ""
    print(f"{head}{'placed (UTC)':<15} {'coin':<16} {'min':>5} {'lived':>7} "
          f"{'chain':<11} {'exec_ntl':>12}  twap_id")
    for o in (misses if show_all else misses[:25]):
        ntl = f"{o['executed_ntl']:,.0f}" if o["executed_ntl"] is not None else "-"
        lv = lived_min(o)
        lived = f"{lv:.1f}" if lv is not None else "LIVE"
        pre = f"{o['addr'][:10]:<12} " if with_addr else ""
        print(f"{pre}{fmt_ms(o['placed_at_ms']):<15} {o['coin']:<16} {o['minutes'] or '':>5} "
              f"{lived:>7} {o['chain_state']:<11} {ntl:>12}  {o['twap_id']}")
    if not show_all and len(misses) > 25:
        print(f"  ... {len(misses) - 25} more (use --all)")


# ---------------------------------------------------------------- single

def run_single(addr, con, cutoff_ms, show_all):
    r = analyze(addr, con, cutoff_ms)
    print(f"address        {addr}")
    print(f"chain entries  {len(r['entries'])}  ->  {len(r['chain'])} orders by twapId"
          f"  ({r['no_id']} entries without twapId skipped)")
    if r["oldest_ms"]:
        print(f"history window from {fmt_ms(r['oldest_ms'])} UTC")
    print(f"capture cutoff {fmt_ms(cutoff_ms) if cutoff_ms else '-'} UTC  (last row without placed_at_ms)")
    print(f"db rows        {len(r['rows'])}  ({len(r['legacy'])} legacy, placed_at_ms NULL)")
    print()
    n_co, n_m = len(r["chain_only"]), len(r["misses"])
    print(f"bound 1:1      {len(r['bound'])}")
    print(f"ambiguous      {len(r['ambiguous'])}  (same ms, several rows or twapIds)")
    print(f"db unmatched   {len(r['unmatched'])}  (inside window, no chain order)")
    print(f"db outside     {len(r['outside'])}  (placed before history window)")
    print(f"chain only     {n_co}  ({n_co - n_m} before cutoff = legacy, {n_m} after = REAL MISSES)")
    print()

    print_matrix(matrix(r["bound"]))
    print_deltas(delta_groups(r["bound"]))

    print(f"{'placed (UTC)':<15} {'sym/coin':<24} {'db status':<10} {'chain':<11} "
          f"{'exec_ntl':>12} {'Δend min':>9}  twap_id")
    ordered = sorted(r["bound"], key=lambda x: x[1]["placed_at_ms"], reverse=True)
    for d, c in (ordered if show_all else ordered[:25]):
        dm = delta_min(d, c)
        delta = f"{dm:+.0f}" if dm is not None else ""
        ntl = f"{c['executed_ntl']:,.0f}" if c["executed_ntl"] is not None else "-"
        sym = f"{d['symbol']}/{c['coin']}"
        print(f"{fmt_ms(c['placed_at_ms']):<15} {sym:<24} {d['status']:<10} "
              f"{c['chain_state']:<11} {ntl:>12} {delta:>9}  {c['twap_id']}")
    if not show_all and len(ordered) > 25:
        print(f"  ... {len(ordered) - 25} more (use --all)")

    for ms, d, c in r["ambiguous"]:
        print(f"\nAMBIGUOUS at {fmt_ms(ms)}: db {[x['order_hash'][:10] for x in d]}"
              f"  chain {[o['twap_id'] for o in c]}")

    if r["misses"]:
        print("\nREAL MISSES (placed after cutoff, not in DB)")
        print_misses(r["misses"], show_all)


# ---------------------------------------------------------------- aggregate

def run_aggregate(con, cutoff_ms, top, pause, show_all):
    cut_iso = datetime.fromtimestamp(cutoff_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    addrs = [row[0].lower() for row in con.execute("""
        SELECT address, COUNT(*) n FROM orders
        WHERE placed_at_ms IS NOT NULL AND first_seen_at > ?
        GROUP BY address ORDER BY n DESC LIMIT ?
    """, (cut_iso, top)).fetchall()]

    print(f"capture cutoff {fmt_ms(cutoff_ms)} UTC — {len(addrs)} wallets, {pause}s apart\n")
    print(f"{'addr':<12} {'entries':>7} {'bound':>6} {'amb':>4} {'unm':>4} "
          f"{'post':>5} {'miss':>5} {'long':>5} {'hip3':>5} {'h>5m':>5} {'o>5m':>5} {'neg':>4}")

    tot_m, tot_g = Counter(), {"hip3": [], "other": []}
    all_misses, failed = [], []
    t = dict(bound=0, amb=0, unm=0, post=0, miss=0, long=0)

    for i, addr in enumerate(addrs):
        if i:
            time.sleep(pause)
        try:
            r = analyze(addr, con, cutoff_ms)
        except Exception as e:
            failed.append((addr, repr(e)))
            print(f"{addr[:10]:<12} FAILED  {e!r}")
            continue

        g = delta_groups(r["bound"])
        tot_m.update(matrix(r["bound"]))
        for k in g:
            tot_g[k].extend(g[k])
        for o in r["misses"]:
            all_misses.append(dict(o, addr=addr))
        long_ = sum(1 for o in r["misses"]
                    if lived_min(o) is None or lived_min(o) >= INSTANT_MIN)
        neg = sum(x < -5 for x in g["hip3"] + g["other"])

        t["bound"] += len(r["bound"]); t["amb"] += len(r["ambiguous"])
        t["unm"] += len(r["unmatched"]); t["post"] += r["chain_post"]
        t["miss"] += len(r["misses"]); t["long"] += long_

        cap = "*" if len(r["entries"]) >= 2000 else " "
        print(f"{addr[:10]:<12} {len(r['entries']):>6}{cap} {len(r['bound']):>6} "
              f"{len(r['ambiguous']):>4} {len(r['unmatched']):>4} {r['chain_post']:>5} "
              f"{len(r['misses']):>5} {long_:>5} {len(g['hip3']):>5} "
              f"{sum(x > 5 for x in g['hip3']):>5} {sum(x > 5 for x in g['other']):>5} {neg:>4}")

    print("  (* = at the 2000-entry cap)\n")
    print(f"TOTAL  bound {t['bound']}  ambiguous {t['amb']}  unmatched {t['unm']}  failed {len(failed)}")
    if t["post"]:
        print(f"       chain orders after cutoff {t['post']}  ->  misses {t['miss']}"
              f" ({1000 * t['miss'] / t['post']:.1f} per 1000),"
              f" of which lived >= {INSTANT_MIN:g} min or still live: {t['long']}"
              f" ({1000 * t['long'] / t['post']:.1f} per 1000)")
    print()
    print_matrix(tot_m)
    print_deltas(tot_g)

    if all_misses:
        # LIVE first, then longest-lived: the interesting ones float up
        all_misses.sort(key=lambda o: (lived_min(o) is not None, -(lived_min(o) or 0)))
        print("REAL MISSES, all wallets (longest-lived first)")
        print_misses(all_misses, show_all, with_addr=True)

    for addr, err in failed:
        print(f"FAILED {addr}: {err}")


# ---------------------------------------------------------------- main

def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    show_all = "--all" in args

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cutoff_ms = get_cutoff_ms(con)

    if "--top" in args:
        top = int(args[args.index("--top") + 1])
        pause = float(args[args.index("--pause") + 1]) if "--pause" in args else 3.0
        run_aggregate(con, cutoff_ms, top, pause, show_all)
    else:
        run_single(args[0].lower(), con, cutoff_ms, show_all)


if __name__ == "__main__":
    main()