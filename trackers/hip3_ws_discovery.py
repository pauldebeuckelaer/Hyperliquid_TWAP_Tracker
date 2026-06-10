#!/usr/bin/env python3
"""
HIP-3 WS Discovery & Resurrection
=================================
Permanent push-based whale discovery: subscribes to HIP-3 market trade
feeds, harvests counterparty addresses from the `users` array on every
fill, and routes candidates through the EXISTING discovery machinery —
the exact same evaluate -> register -> persist path an order-start event
takes in main.py. No parallel pipeline, no behavioral drift.

Pipeline per fill:
  feed handler (harvest only, ZERO API calls) -> queue -> worker (throttled):
    1. dedup against processed-set (cleared daily)
    2. skip if already active + flagged (one local SELECT)
    3. discovery.evaluate()  — full state fetch, $50K portfolio floor
                               (HIP-3 included: this instance runs with
                               hip3_tracking_enabled=True, so pure-HIP-3
                               whales pass the floor)
    4. discovery.register()  — new: INSERT / inactive: RESURRECT / active: no-op
    5. collector.persist()   — bootstrap snapshot from in-hand state, no re-fetch
                               -> next hourly tier refresh assigns a real tier
    6. flag if HIP-3 notional >= flag_floor (read from the in-hand state,
       zero extra calls) -> fast ladder collects its HIP-3 every cycle

Registration floor ($50K, evaluate's own) decides "is this a whale".
Flag floor ($100K HIP-3 notional) decides "is its HIP-3 worth per-cycle
collection". Separate questions, separate floors.
"""
import asyncio
import json
import logging
from typing import List, Optional, Set

import aiohttp
import websockets

logger = logging.getLogger(__name__)

WS_URI = "wss://api.hyperliquid.xyz/ws"
RECONNECT_DELAY_INITIAL = 1
RECONNECT_DELAY_MAX = 60


class HIP3Discovery:
    """Trade-feed driven HIP-3 whale discovery + resurrection."""

    def __init__(
            self,
            discovery,                     # WhaleDiscovery (hip3_tracking_enabled=True)
            collector,                     # WhaleStateCollector (persist path)
            storage,                       # WhaleStorage (this thread's own connection)
            coins: List[str],              # HIP-3 markets to subscribe
            config: Optional[dict] = None,
    ):
        self.discovery = discovery
        self.collector = collector
        self.storage = storage
        self.coins = coins
        config = config or {}

        self.flag_floor = config.get("flag_floor", 100_000)
        self.worker_sleep = config.get("worker_sleep", 3.0)   # the rate limit
        self.queue_max = config.get("queue_max", 500)
        self.processed_reset_hours = config.get("processed_reset_hours", 24)

        self.queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_max)
        self.processed: Set[str] = set()
        self._running = False

        # Stats
        self.fills_seen = 0
        self.candidates_queued = 0
        self.evaluated = 0
        self.registered = 0
        self.flagged = 0
        self.below_floor = 0
        self.queue_dropped = 0

        logger.info(
            f"HIP3Discovery initialized: {len(coins)} feeds, "
            f"flag_floor=${self.flag_floor:,}, worker_sleep={self.worker_sleep}s"
        )

    # ------------------------------------------------------------------ #
    # FEED SIDE — harvest only, never calls the API
    # ------------------------------------------------------------------ #

    async def _subscribe(self, ws):
        for coin in self.coins:
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": coin},
            }))
        logger.info(f"[hip3-ws] subscribed to {len(self.coins)} HIP-3 trade feeds")

    def _harvest(self, msg: dict):
        if msg.get("channel") != "trades":
            return
        for trade in msg.get("data", []):
            self.fills_seen += 1
            for addr in trade.get("users", []):
                if addr in self.processed:
                    continue
                try:
                    self.queue.put_nowait(addr)
                    # mark immediately so a burst of fills from the same
                    # address doesn't enqueue it repeatedly before the
                    # worker reaches it
                    self.processed.add(addr)
                    self.candidates_queued += 1
                except asyncio.QueueFull:
                    self.queue_dropped += 1
                    # NOT marked processed — a later fill re-queues it
                    # when there's room

    async def run_feed(self):
        """Connect + listen with reconnect/backoff."""
        delay = RECONNECT_DELAY_INITIAL
        while self._running:
            try:
                async with websockets.connect(
                        WS_URI, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("[hip3-ws] connected")
                    delay = RECONNECT_DELAY_INITIAL
                    await self._subscribe(ws)
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            self._harvest(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
            except websockets.ConnectionClosed as e:
                logger.warning(f"[hip3-ws] closed: {e}; reconnect in {delay}s")
            except Exception as e:
                logger.error(f"[hip3-ws] error: {e}; reconnect in {delay}s")
            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    # ------------------------------------------------------------------ #
    # WORKER SIDE — all API access lives here, strictly throttled
    # ------------------------------------------------------------------ #

    def _needs_evaluation(self, addr: str) -> bool:
        """Cheap local check: skip whales already active AND flagged."""
        self.storage.cursor.execute(
            "SELECT is_active, has_hip3 FROM whale_addresses WHERE address = ?",
            (addr,),
        )
        row = self.storage.cursor.fetchone()
        if row is None:
            return True                     # unknown address -> discovery case
        return not (row[0] == 1 and row[1] == 1)

    async def _evaluate(self, addr: str, session: aiohttp.ClientSession):
        self.evaluated += 1

        if not self._needs_evaluation(addr):
            return

        # The exact same path an order-start event takes in main.py.
        state = await self.discovery.evaluate(addr, session)
        if state is None:
            # API failure or portfolio < $50K — stays in processed-set
            # until the daily reset, then gets another look if still trading.
            self.below_floor += 1
            return

        self.discovery.register(addr)        # INSERT / resurrect / no-op
        ok = await self.collector.persist(addr, state)
        if not ok:
            logger.warning(
                f"[hip3-ws] persist failed for {addr[:12]}… "
                f"(registered; refresh/ladders will catch up)")
            return
        self.registered += 1

        # Flag decision from the in-hand state — zero extra API calls.
        hip3_ntl = abs(state.account_data.get("hip3_total_ntl_pos") or 0)
        if hip3_ntl >= self.flag_floor:
            self.storage.set_hip3_flag(addr, 1)
            self.flagged += 1
            logger.info(
                f"[hip3-ws] 🐋 {addr[:12]}… registered + persisted | "
                f"HIP-3 ${hip3_ntl:,.0f} -> flagged")
        else:
            logger.info(
                f"[hip3-ws] {addr[:12]}… registered + persisted | "
                f"HIP-3 ${hip3_ntl:,.0f} below flag floor")

    async def run_worker(self):
        """Single consumer; the sleep IS the rate limit."""
        reset_every = self.processed_reset_hours * 3600
        last_reset = asyncio.get_event_loop().time()

        async with aiohttp.ClientSession() as session:
            while self._running or not self.queue.empty():
                now = asyncio.get_event_loop().time()
                if now - last_reset > reset_every:
                    logger.info(
                        f"[hip3-ws] daily reset: clearing "
                        f"{len(self.processed)} processed addresses")
                    self.processed.clear()
                    last_reset = now

                try:
                    addr = await asyncio.wait_for(self.queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    continue

                try:
                    await self._evaluate(addr, session)
                except Exception as e:
                    logger.warning(
                        f"[hip3-ws] evaluate failed for {addr[:12]}…: {e}")
                await asyncio.sleep(self.worker_sleep)

    # ------------------------------------------------------------------ #

    async def run(self):
        """Entry point: feed + worker as sibling tasks."""
        self._running = True
        await asyncio.gather(self.run_feed(), self.run_worker())

    def stop(self):
        self._running = False

    def get_stats(self) -> dict:
        return {
            "fills_seen": self.fills_seen,
            "candidates_queued": self.candidates_queued,
            "evaluated": self.evaluated,
            "registered": self.registered,
            "flagged": self.flagged,
            "below_floor": self.below_floor,
            "queue_dropped": self.queue_dropped,
            "queue_depth": self.queue.qsize(),
            "processed_set_size": len(self.processed),
        }