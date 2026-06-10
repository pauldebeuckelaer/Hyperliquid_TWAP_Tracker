#!/usr/bin/env python3
"""
drain_graveyard.py — resurrect deactivated whales that still hold HIP-3.

One-shot / re-runnable / idempotent:
- Only probes whales that are CURRENTLY is_active=0 (drained ones get
  reactivated, so re-runs skip them automatically).
- Phase 1: gentle HIP-3 probe over recently-deactivated whales.
- Phase 2: for holders above the floor — flag (has_hip3=1) + full
  fetch_and_persist via the production collector path. persist() flips
  is_active=1 itself, and the bootstrap data lands in the snapshot tables
  so the next hourly tier refresh assigns a real tier (well inside the
  130-min window). From then on the normal ladders own the whale.

Run from project root, inside the venv:
    python3 drain_graveyard.py --dry-run     # report only, no writes
    python3 drain_graveyard.py               # the real drain
"""
import asyncio
import argparse
import logging
from pathlib import Path

import aiohttp

from storage.whale_storage import WhaleStorage
from api_client.hyperliquid_client import HyperliquidClient
from trackers.whale_discovery import TokenFilter
from trackers.whale_state_collector import WhaleStateCollector

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("drain")

DB_PATH = Path("data/twap.db")

# Throttle — deliberately gentler than the probes that hit 429 walls.
PROBE_SEM = 2          # max concurrent per-dex calls within one address
PROBE_SLEEP = 1.0      # pause between addresses, phase 1
PERSIST_SLEEP = 2.0    # pause between addresses, phase 2 (11-call burst each)


def get_candidates(storage: WhaleStorage, lookback_days: int) -> list:
    """Currently-deactivated whales within the lookback window."""
    storage.cursor.execute(f"""
        SELECT address FROM whale_addresses
        WHERE is_active = 0
          AND last_updated >= strftime('%Y-%m-%dT%H:%M:%f','now','-{lookback_days} days')
    """)
    return [row[0] for row in storage.cursor.fetchall()]


async def probe_hip3_notional(client, dexes, address, session, sem) -> float:
    """Sum live HIP-3 totalNtlPos across dexes. Errors count as 0 (whale
    stays inactive, so a re-run picks it up — no permanent loss)."""
    async def one(dex):
        async with sem:
            try:
                return await client.get_user_state_hip3_async(address, dex, session)
            except Exception:
                return None
    results = await asyncio.gather(*[one(d) for d in dexes])
    ntl = 0.0
    for res in results:
        if res:
            ntl += abs(float(res.get("marginSummary", {}).get("totalNtlPos", 0)))
    return ntl


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--floor", type=float, default=100_000,
                    help="min HIP-3 notional to reactivate (default: T5 tier floor)")
    ap.add_argument("--dry-run", action="store_true",
                    help="probe and report only — no flags, no reactivation, no persists")
    args = ap.parse_args()

    storage = WhaleStorage(DB_PATH)
    client = HyperliquidClient()
    collector = WhaleStateCollector(
        hl_client=client,
        storage=storage,
        tier_manager=None,                      # not used by fetch_and_persist path
        token_filter=TokenFilter(),
        config={"hip3_tracking_enabled": True}, # full HIP-3 fan-out in the bootstrap fetch
    )

    candidates = get_candidates(storage, args.lookback_days)
    dexes = client.get_active_hip3_dexes()
    logger.info(f"candidates (inactive, <= {args.lookback_days}d): {len(candidates)}")
    logger.info(f"HIP-3 dexes: {dexes}")
    logger.info(f"floor: ${args.floor:,.0f} | dry_run: {args.dry_run}")

    # ---- PHASE 1: identify holders (gentle probe) ----
    sem = asyncio.Semaphore(PROBE_SEM)
    holders = []
    errors = 0
    async with aiohttp.ClientSession() as session:
        for i, addr in enumerate(candidates, 1):
            try:
                ntl = await probe_hip3_notional(client, dexes, addr, session, sem)
            except Exception as e:
                errors += 1
                ntl = 0.0
            if ntl >= args.floor:
                holders.append((addr, ntl))
            if i % 50 == 0:
                logger.info(f"phase1: {i}/{len(candidates)} probed, "
                            f"{len(holders)} holders >= floor")
            await asyncio.sleep(PROBE_SLEEP)

    holders.sort(key=lambda x: -x[1])
    total_ntl = sum(n for _, n in holders)
    logger.info(f"phase1 done: {len(holders)} holders >= ${args.floor:,.0f} "
                f"| total notional ${total_ntl:,.0f} | probe errors: {errors}")
    for addr, ntl in holders[:25]:
        logger.info(f"   {addr}  ${ntl:,.0f}")

    if args.dry_run:
        logger.info("DRY RUN — stopping before any writes.")
        return
    if not holders:
        logger.info("nothing to drain.")
        return

    # ---- PHASE 2: flag + bootstrap-persist (production code path) ----
    drained, failed = 0, []
    async with aiohttp.ClientSession() as session:
        for i, (addr, ntl) in enumerate(holders, 1):
            try:
                ok = await collector.fetch_and_persist(addr, session)
            except Exception as e:
                logger.warning(f"fetch_and_persist raised for {addr[:12]}…: {e}")
                ok = False
            if ok:
                storage.set_hip3_flag(addr, 1)   # flag AFTER successful persist
                drained += 1
                logger.info(f"phase2 [{i}/{len(holders)}] ✅ {addr[:12]}… "
                            f"(${ntl:,.0f}) reactivated + flagged + persisted")
            else:
                failed.append(addr)
                logger.warning(f"phase2 [{i}/{len(holders)}] ❌ {addr[:12]}… "
                               f"persist failed — left inactive, re-run will retry")
            await asyncio.sleep(PERSIST_SLEEP)

    logger.info(f"DRAIN COMPLETE: {drained}/{len(holders)} resurrected, "
                f"{len(failed)} failed (will be retried on re-run)")
    logger.info("next hourly tier refresh will assign real tiers from the "
                "bootstrap snapshots; check the 🆕 lines in tier_manager.log.")

    storage.close()


if __name__ == "__main__":
    asyncio.run(main())