#!/usr/bin/env python3
"""
Vault Census
============
Pulls Hyperliquid's full vault list and answers three questions:

1. Which open vaults are large (TVL >= MIN_TVL)?
2. Which of them already show up in twap.db — depositors in vault_snapshots,
   registered in whale_addresses, trading on the tape (tape_addresses)?
3. Which leaders run several sizable vaults (operators like Systemic Strategies)?

Read-only against twap.db (opened with mode=ro). Writes only:
    data/vaults_list.json   — the normalized vault list
    data/vault_census.csv   — open vaults >= MIN_TVL_OPERATOR with DB columns

Runs without the DB as well (e.g. locally in PyCharm): the cross-reference
columns show '-' and only the API-side sections are meaningful.

NOTE: the stats endpoint and its field names are from memory, not verified.
The first lines printed are the response's actual keys — if a column comes
out empty, check those against normalize().

Place in: Hyperliquid_TWAP_Analyzer/scripts/vault_census.py
"""
import csv
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

VAULTS_URL = "https://stats-data.hyperliquid.xyz/Mainnet/vaults"

ROOT = Path(__file__).resolve().parent.parent  # scripts/ -> project root
DB_PATH = ROOT / "data" / "twap.db"
OUT_JSON = ROOT / "data" / "vaults_list.json"
OUT_CSV = ROOT / "data" / "vault_census.csv"

MIN_TVL = 1_000_000            # "large vault" floor for the main table
MIN_TVL_OPERATOR = 100_000     # floor for counting a vault toward an operator
TOP_N = 30                     # rows in the main table
FOCUS_LEADER = "0x2b804617c6f63c040377e95bb276811747006f4b"  # Systemic Strategies


# =============================================================================
# API
# =============================================================================

def fetch_vaults() -> list:
    req = urllib.request.Request(VAULTS_URL, headers={"User-Agent": "vault-census/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = json.load(resp)

    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"Unexpected response shape: {type(raw).__name__}")

    first = raw[0]
    print(f"entries: {len(raw)} | top-level keys: {sorted(first.keys())}")
    if isinstance(first.get("summary"), dict):
        print(f"summary keys: {sorted(first['summary'].keys())}")
    return raw


def normalize(item: dict) -> dict:
    s = item.get("summary", item)
    rel = s.get("relationship") or {}
    children = []
    if rel.get("type") == "parent":
        children = (rel.get("data") or {}).get("childAddresses") or []
    return {
        "name": (s.get("name") or "").strip(),
        "vault": (s.get("vaultAddress") or "").lower(),
        "leader": (s.get("leader") or "").lower(),
        "tvl": float(s.get("tvl") or 0),
        "closed": bool(s.get("isClosed")),
        "relationship": rel.get("type") or "",
        "children": [c.lower() for c in children],
    }


# =============================================================================
# DB (read-only)
# =============================================================================

class Db:
    def __init__(self, path: Path):
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        self.cur = conn.cursor()
        self.deps = dict(self.cur.execute(
            "SELECT lower(vault_address), COUNT(DISTINCT address) "
            "FROM vault_snapshots GROUP BY lower(vault_address)"
        ))
        self._roster = {}
        self._tape = {}

    def roster(self, addr: str) -> str:
        """'' = never registered, else 'is_active/tier'."""
        if addr not in self._roster:
            r = self.cur.execute(
                "SELECT is_active, tier FROM whale_addresses WHERE address = ?", (addr,)
            ).fetchone()
            self._roster[addr] = f"{r[0]}/{r[1] if r[1] is not None else '-'}" if r else ""
        return self._roster[addr]

    def on_tape(self, addr: str) -> int:
        if addr not in self._tape:
            r = self.cur.execute(
                "SELECT 1 FROM tape_addresses WHERE address = ?", (addr,)
            ).fetchone()
            self._tape[addr] = 1 if r else 0
        return self._tape[addr]


# =============================================================================
# MAIN
# =============================================================================

def main():
    if hasattr(sys.stdout, "reconfigure"):  # vault names contain emoji
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        raw = fetch_vaults()
    except (urllib.error.URLError, TimeoutError) as e:
        raise SystemExit(f"Fetch failed: {e}")

    vaults = [normalize(i) for i in raw]
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(vaults), encoding="utf-8")

    db = Db(DB_PATH) if DB_PATH.exists() else None
    print(f"DB: {DB_PATH if db else 'not found — cross-reference skipped'}")

    def deps(a):
        return db.deps.get(a, 0) if db else "-"

    def roster(a):
        return db.roster(a) if db else "-"

    def tape(a):
        return db.on_tape(a) if db else "-"

    live = [v for v in vaults if not v["closed"]]
    big = sorted((v for v in live if v["tvl"] >= MIN_TVL), key=lambda v: -v["tvl"])

    # --- 1. Summary ---------------------------------------------------------
    print(f"\n=== SUMMARY ===")
    print(f"all vaults: {len(vaults)} | open: {len(live)} | "
          f"open >= ${MIN_TVL/1e6:.0f}M: {len(big)} "
          f"(${sum(v['tvl'] for v in big)/1e6:,.0f}M total)")

    # --- 2. Large vaults ----------------------------------------------------
    print(f"\n=== TOP {TOP_N} OPEN VAULTS BY TVL ===")
    print(f"{'name':30} {'vault':12} {'leader':12} {'tvl_M':>8} {'deps':>5} {'roster':>6} {'tape':>4}")
    for v in big[:TOP_N]:
        print(f"{v['name'][:30]:30} {v['vault'][:12]} {v['leader'][:12]} "
              f"{v['tvl']/1e6:8.2f} {deps(v['vault']):>5} {roster(v['vault']):>6} {tape(v['vault']):>4}")

    if db:
        with_deps = sum(1 for v in big if db.deps.get(v["vault"], 0) > 0)
        on_tape = [v for v in big if db.on_tape(v["vault"])]
        unreg = [v for v in on_tape if not db.roster(v["vault"])]
        print(f"\nlarge vaults: {with_deps} have your wallets as depositors | "
              f"{len(on_tape)} trade on your tape | {len(unreg)} of those never registered")

    # --- 3. Operators -------------------------------------------------------
    by_leader = defaultdict(list)
    for v in live:
        if v["tvl"] >= MIN_TVL_OPERATOR:
            by_leader[v["leader"]].append(v)
    operators = sorted(
        ((l, vs) for l, vs in by_leader.items() if len(vs) >= 2),
        key=lambda x: -sum(v["tvl"] for v in x[1]),
    )
    print(f"\n=== OPERATORS: leaders with 2+ open vaults >= ${MIN_TVL_OPERATOR/1e3:.0f}K "
          f"({len(operators)}) ===")
    for leader, vs in operators[:20]:
        total = sum(v["tvl"] for v in vs)
        print(f"{leader[:12]} roster={roster(leader) or 'none':>5} total ${total/1e6:6.2f}M | "
              + ", ".join(f"{v['name'][:20]} ${v['tvl']/1e6:.1f}M" for v in sorted(vs, key=lambda v: -v['tvl'])))

    # --- 4. Focus leader ----------------------------------------------------
    focus = [v for v in vaults if v["leader"] == FOCUS_LEADER]
    print(f"\n=== VAULTS LED BY {FOCUS_LEADER[:12]} ({len(focus)}) ===")
    for v in sorted(focus, key=lambda v: -v["tvl"]):
        print(f"{v['name'][:30]:30} {v['vault']} tvl ${v['tvl']/1e6:6.2f}M "
              f"closed={int(v['closed'])} deps={deps(v['vault'])} "
              f"roster={roster(v['vault']) or 'none'} tape={tape(v['vault'])}")

    # --- 5. Parent vaults (e.g. HLP trades through child addresses) --------
    parents = [v for v in live if v["children"]]
    if parents:
        print(f"\n=== PARENT VAULTS WITH CHILD ADDRESSES ({len(parents)}) ===")
        for v in parents:
            kids_on_tape = sum(db.on_tape(c) for c in v["children"]) if db else "-"
            print(f"{v['name'][:30]:30} {v['vault'][:12]} children={len(v['children'])} "
                  f"on_tape={kids_on_tape}")
            for c in v["children"]:
                print(f"    {c} roster={roster(c) or 'none'} tape={tape(c)}")

    # --- CSV ----------------------------------------------------------------
    rows = sorted((v for v in live if v["tvl"] >= MIN_TVL_OPERATOR), key=lambda v: -v["tvl"])
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "vault", "leader", "tvl", "relationship",
                    "depositors_in_db", "roster", "on_tape"])
        for v in rows:
            w.writerow([v["name"], v["vault"], v["leader"], round(v["tvl"]),
                        v["relationship"], deps(v["vault"]), roster(v["vault"]), tape(v["vault"])])

    print(f"\nwrote {OUT_JSON} ({len(vaults)} vaults) and {OUT_CSV} ({len(rows)} rows)")


if __name__ == "__main__":
    main()