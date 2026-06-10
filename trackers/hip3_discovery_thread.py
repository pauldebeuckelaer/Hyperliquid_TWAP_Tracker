#!/usr/bin/env python3
"""
Thread host for HIP3Discovery.

main.py's loop is synchronous (asyncio.run per call); the WS feed needs a
persistent event loop. This runs one in a daemon thread, with its OWN
storage connection (sqlite3 connections are not safe to share across
threads; WAL mode makes multi-connection writes safe).

Builds its own WhaleDiscovery with hip3_tracking_enabled=True — REQUIRED
so pure-HIP-3 whales (tiny main-dex, big builder-dex) pass evaluate's
$50K portfolio floor. The main path's discovery instance and its config
are untouched.
"""
import asyncio
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def start_hip3_discovery_thread(hl_client, db_path: Path, config: dict):
    """
    Build an isolated storage+discovery+collector stack and run the WS
    discovery loop in a daemon thread.

    Returns (thread, discovery_ref). discovery_ref is a dict whose "obj"
    key is populated from inside the thread once construction completes —
    use it for stats. The thread is fire-and-forget (daemon: dies with
    the process; internal reconnect handles feed drops; if the whole
    thread dies, the bot keeps running without WS discovery until the
    next restart).
    """
    from storage.whale_storage import WhaleStorage
    from trackers.whale_discovery import WhaleDiscovery, TokenFilter
    from trackers.whale_state_collector import WhaleStateCollector
    from trackers.hip3_ws_discovery import HIP3Discovery

    coins = config.get("hip3_ws_coins", [
        "xyz:XYZ100", "xyz:SP500", "xyz:INTC", "xyz:MU",
        "xyz:SNDK", "xyz:CL", "xyz:BRENTOIL", "xyz:DRAM",
    ])

    discovery_ref = {"obj": None}

    def _thread_main():
        # Everything DB-touching is created INSIDE the thread.
        storage = WhaleStorage(db_path)

        token_filter = TokenFilter()

        discovery = WhaleDiscovery(
            hl_client,
            storage,
            token_filter,
            config={
                "min_portfolio_value": config.get("min_portfolio_value", 50_000),
                "hip3_tracking_enabled": True,   # forced ON — see module docstring
            },
        )

        collector = WhaleStateCollector(
            hl_client=hl_client,
            storage=storage,
            tier_manager=None,               # persist path doesn't use it
            token_filter=token_filter,
            config={"hip3_tracking_enabled": True},
        )

        ws_discovery = HIP3Discovery(
            discovery=discovery,
            collector=collector,
            storage=storage,
            coins=coins,
            config=config,
        )
        discovery_ref["obj"] = ws_discovery

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ws_discovery.run())
        except Exception as e:
            logger.error(f"[hip3-ws] discovery thread died: {e}", exc_info=True)
        finally:
            storage.close()
            loop.close()

    thread = threading.Thread(
        target=_thread_main, name="hip3-ws-discovery", daemon=True)
    thread.start()
    logger.info(f"[hip3-ws] discovery thread started ({len(coins)} feeds)")
    return thread, discovery_ref