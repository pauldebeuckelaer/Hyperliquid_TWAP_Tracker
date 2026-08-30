#!/usr/bin/env python3
"""
Hyperliquid HIP-4 Outcome Market Probe (v1)
===========================================

Purpose: find out whether HIP-4 outcome positions are visible for addresses we
already track, before deciding whether any collector is worth building.

READ-ONLY. No writes to twap.db. No auth. No orders. Nothing is placed.

Runs in three stages, each gated so nothing hammers the API by accident:

  Stage 1  outcomeMeta          -> what markets exist, what the payload looks like
  Stage 2  spotClearinghouseState for ONE address
                                -> THE LOAD-BEARING TEST. Do outcome token
                                   balances surface here at all? If not, the
                                   cross-instrument join is dead and we learn it
                                   in one call instead of after a build.
  Stage 3  same, looped over tracked addresses   (OFF by default)

Encoding, per Hyperliquid docs:
    encoding    = 10 * outcome + side      (side is 0 or 1 only)
    spot coin   = #<encoding>
    token name  = +<encoding>
    asset id    = 100_000_000 + encoding

Drop this in scripts/ next to build_liq_episodes.py and run it from PyCharm.
"""

import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

INFO_API = "https://api.hyperliquid.xyz/info"
REQUEST_TIMEOUT = 30
SLEEP_BETWEEN_CALLS = 0.30          # gentle; we are a guest here

# DB path is resolved from THIS FILE's location, never the working directory.
# Assumes the script lives in scripts/ so the repo root is one level up.
# This is deliberate: a relative path would silently hit the 0-byte twap.db
# that sits at repo root and every query would return nothing, with no error.
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / "data" / "twap.db"

# Stage 2: which address to probe. None -> pull the lowest-tier (highest
# priority) active address out of whale_addresses.
PROBE_ADDRESS: Optional[str] = None

# Stage 3: population scan. Leave False until Stage 2 proves the concept.
RUN_POPULATION_SCAN = False
POPULATION_LIMIT = 25               # raise once you know the call is cheap

OUT_DIR = SCRIPT_DIR / "outcome_probe_out"


# ---------------------------------------------------------------------------
# ENCODING HELPERS
# ---------------------------------------------------------------------------

def encode(outcome_id: int, side: int) -> int:
    if side not in (0, 1):
        raise ValueError(f"side must be 0 or 1, got {side}")
    return 10 * outcome_id + side


def spot_coin(enc: int) -> str:
    return f"#{enc}"


def token_name(enc: int) -> str:
    return f"+{enc}"


def asset_id(enc: int) -> int:
    return 100_000_000 + enc


def decode(enc: int) -> tuple[int, int]:
    """Inverse of encode(). Returns (outcome_id, side)."""
    return divmod(enc, 10)


def looks_like_outcome(coin: str) -> bool:
    """Outcome spot coins are '#<n>', outcome tokens are '+<n>'."""
    if not coin:
        return False
    return (coin[0] in "#+") and coin[1:].isdigit()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def post_info(body: dict) -> Optional[Any]:
    """POST to the info endpoint. Returns parsed JSON or None on failure."""
    try:
        r = requests.post(
            INFO_API,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [ERROR] {body.get('type')}: {e}")
        return None


# ---------------------------------------------------------------------------
# SHAPE INSPECTION
#   We do not know the outcomeMeta payload shape. Describe it rather than
#   assuming keys, then let the structure drive the next version.
# ---------------------------------------------------------------------------

def describe(obj: Any, indent: int = 0, max_items: int = 2, depth: int = 0) -> None:
    pad = "  " * (indent + 1)
    if depth > 4:
        print(f"{pad}...")
        return

    if isinstance(obj, dict):
        print(f"{pad}dict, {len(obj)} keys: {list(obj.keys())[:12]}")
        for k, v in list(obj.items())[:max_items]:
            print(f"{pad}  .{k} ->")
            describe(v, indent + 2, max_items, depth + 1)
    elif isinstance(obj, list):
        print(f"{pad}list, len={len(obj)}")
        for v in obj[:max_items]:
            describe(v, indent + 1, max_items, depth + 1)
    else:
        val = repr(obj)
        if len(val) > 80:
            val = val[:77] + "..."
        print(f"{pad}{type(obj).__name__}: {val}")


def dump_raw(obj: Any, name: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    print(f"  raw payload written: {path}")


# ---------------------------------------------------------------------------
# DB (read-only)
# ---------------------------------------------------------------------------

def check_db() -> bool:
    if not DB_PATH.exists():
        print(f"  [FATAL] no DB at {DB_PATH}")
        return False
    size = DB_PATH.stat().st_size
    if size == 0:
        print(f"  [FATAL] DB at {DB_PATH} is 0 bytes -- wrong path")
        return False
    print(f"  DB: {DB_PATH}  ({size / 1e9:.1f} GB)")
    return True


def fetch_addresses(limit: int) -> list[str]:
    """Active tracked addresses, best tier first. Read-only."""
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            """
            SELECT address
            FROM whale_addresses
            WHERE is_active = 1 AND tier IS NOT NULL
            ORDER BY tier ASC, position_value DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [r[0] for r in cur.fetchall()]
        con.close()
        return rows
    except Exception as e:
        print(f"  [ERROR] address query failed: {e}")
        return []


# ---------------------------------------------------------------------------
# STAGES
# ---------------------------------------------------------------------------

def stage1_outcome_meta() -> Optional[Any]:
    print("\n" + "=" * 78)
    print("STAGE 1  outcomeMeta")
    print("=" * 78)

    meta = post_info({"type": "outcomeMeta"})
    if meta is None:
        print("  no payload -- endpoint name may differ, or it may be gated")
        return None

    print("\n  STRUCTURE:")
    describe(meta)
    dump_raw(meta, "outcome_meta")

    # Try to count outcomes without hard-coding a schema we have not seen.
    if isinstance(meta, dict):
        for key in ("outcomes", "universe", "meta", "outcomeMeta"):
            if key in meta and isinstance(meta[key], list):
                print(f"\n  {key}: {len(meta[key])} entries")
                if meta[key]:
                    print("  first entry:")
                    describe(meta[key][0], indent=1, max_items=20)
                break
    elif isinstance(meta, list):
        print(f"\n  top-level list: {len(meta)} entries")
        if meta:
            print("  first entry:")
            describe(meta[0], indent=1, max_items=20)

    return meta


def stage2_single_address(address: str) -> bool:
    """Returns True if any outcome-shaped balance was found."""
    print("\n" + "=" * 78)
    print("STAGE 2  spotClearinghouseState  (the load-bearing test)")
    print("=" * 78)
    print(f"  address: {address}")

    state = post_info({"type": "spotClearinghouseState", "user": address})
    if state is None:
        return False

    print("\n  STRUCTURE:")
    describe(state)
    dump_raw(state, f"spot_state_{address[:10]}")

    balances = state.get("balances", []) if isinstance(state, dict) else []
    print(f"\n  balances: {len(balances)}")

    outcome_rows, normal_rows = [], []
    for b in balances:
        coin = str(b.get("coin") or b.get("token") or "")
        (outcome_rows if looks_like_outcome(coin) else normal_rows).append((coin, b))

    print(f"    normal spot tokens:  {len(normal_rows)}")
    print(f"    outcome-shaped:      {len(outcome_rows)}")

    if normal_rows:
        print("\n  sample normal balances:")
        for coin, b in normal_rows[:5]:
            print(f"    {coin:<16} total={b.get('total')}")

    if not outcome_rows:
        print("\n  -> no outcome tokens in this wallet.")
        print("     Ambiguous: either they hold none, or outcomes do not surface")
        print("     here at all. Try a few more addresses before concluding.")
        return False

    print("\n  -> OUTCOME POSITIONS FOUND:")
    for coin, b in outcome_rows:
        enc = int(coin[1:])
        oid, side = decode(enc)
        print(f"    {coin:<12} outcome={oid:<6} side={side}  "
              f"total={b.get('total')}  asset_id={asset_id(enc)}")
    return True


def stage3_population(addresses: list[str]) -> None:
    print("\n" + "=" * 78)
    print(f"STAGE 3  population scan  ({len(addresses)} addresses)")
    print("=" * 78)

    holders, counts = [], Counter()
    for i, addr in enumerate(addresses, 1):
        state = post_info({"type": "spotClearinghouseState", "user": addr})
        time.sleep(SLEEP_BETWEEN_CALLS)
        if not isinstance(state, dict):
            continue

        found = [
            str(b.get("coin") or b.get("token") or "")
            for b in state.get("balances", [])
            if looks_like_outcome(str(b.get("coin") or b.get("token") or ""))
        ]
        flag = f"{len(found)} outcome" if found else "-"
        print(f"  [{i:3d}/{len(addresses)}] {addr[:12]}...  {flag}")

        if found:
            holders.append((addr, found))
            counts.update(found)

    print("\n" + "-" * 78)
    print(f"  addresses scanned:  {len(addresses)}")
    print(f"  holding outcomes:   {len(holders)}")
    if holders:
        print("\n  most-held outcome tokens:")
        for coin, n in counts.most_common(10):
            oid, side = decode(int(coin[1:]))
            print(f"    {coin:<12} outcome={oid:<6} side={side}  held_by={n}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    started = datetime.now(timezone.utc)
    print("Hyperliquid HIP-4 Outcome Market Probe (v1)")
    print(f"  Started: {started.isoformat()}")
    print("  READ-ONLY: no DB writes, no orders, no auth")

    if not check_db():
        return

    stage1_outcome_meta()

    address = PROBE_ADDRESS
    if address is None:
        picked = fetch_addresses(1)
        if not picked:
            print("\n  no tracked address available -- set PROBE_ADDRESS manually")
            return
        address = picked[0]

    stage2_single_address(address)

    if RUN_POPULATION_SCAN:
        stage3_population(fetch_addresses(POPULATION_LIMIT))
    else:
        print("\n  Stage 3 skipped (RUN_POPULATION_SCAN = False)")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\n  Elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()