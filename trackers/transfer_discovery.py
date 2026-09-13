#!/usr/bin/env python3
"""
Transfer Discovery Poller
=========================
Polls the Hypurrscan /transfers endpoint and persists the venue-wide
transfer graph into data/transfers.db (transfers table).

WHY THIS EXISTS
---------------
Every other instrument in this repo is per-user keyed: you supply an
address, you get that address's data. That is fine for expanding a wallet
you already know and useless for finding one you don't.

/transfers is the only venue-wide transfer feed. No address parameter.
It is also FORWARD-ONLY - the windowed variant 401s, so there is no
backfill. Every day this poller is not running is ~82,000 edges lost
permanently. Contrast /fees, which returns full history on every call
and could be collected at any time without loss.

Once a master address surfaces here, the Hyperliquid subAccounts call
returns the entire desk in one request. Discovery here, expansion there.

DESIGN
------
Dumb endpoint, smart storage - same pattern as the fees collector.
A page is 500 records covering ~8 minutes. `hash` is the primary key
and is stable across overlapping pages, so we poll every ~2 minutes,
throw the whole page at the table, and let SQLite drop the duplicates.
The overlap is the point: it is what survives a missed cycle.

SEPARATE DATABASE - deliberate
------------------------------
This writes to data/transfers.db, NOT twap.db. Not for volume (~82k
rows/day is smaller than a mid-tier coin on the tape) but for the write
lock. twap.db is 60 GB with the orderbook tracker already writing to it,
and that tracker still DROPS prints on lock failure rather than retrying.
The planned 30-day retention delete and schema migration will hold that
lock for a long stretch; this poller needs to keep running through it,
precisely because its data is the unrecoverable kind.

If cross-source joins get painful later, ATTACH, or copy the table into
twap.db with a single CREATE TABLE AS SELECT. That direction is cheap.
Moving out of a 60 GB file is not.

USAGE
-----
    # One poll, print what landed, write nothing to a service:
    python -m trackers.transfer_discovery --once

    # Standalone continuous loop:
    python -m trackers.transfer_discovery

    # Or wire into the main loop like HypurrscanFeesCollector:
    #   if poller.should_poll(cycle): poller.poll()
"""
import argparse
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# A page covers ~8 minutes. Polling every 2 cycles (120s) gives ~4x margin,
# so three consecutive failures still lose nothing.
DEFAULT_POLL_INTERVAL_CYCLES = 2

# Standalone-mode sleep, seconds. Matches the 2-cycle cadence above.
DEFAULT_SLEEP_SECONDS = 120

DEFAULT_DB_PATH = os.path.expanduser(
    "~/bots/Hyperliquid_TWAP_Analyzer/data/transfers.db"
)


# =========================================================================
# Storage
# =========================================================================

class TransferStore:
    """
    Own sqlite3 connection to transfers.db.

    Not a BaseStorage subclass because BaseStorage binds to twap.db and the
    whole point here is a different file. If base.py takes a db_path argument
    this can be refactored to subclass it without touching the schema.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA journal_mode=WAL")
        self.cursor.execute("PRAGMA busy_timeout=10000")
        self._create_tables()
        logger.info(f"TransferStore ready at {db_path}")

    def _create_tables(self):
        # -----------------------------------------------------------------
        # transfers
        #
        # Source: GET https://api.hypurrscan.io/transfers
        #   500-record pages, ~8 min of coverage, forward-only.
        #
        # `hash` is the dedup key - confirmed unique per record and stable
        # across overlapping pages.
        #
        # One table with a `kind` column rather than three tables, so a new
        # action type never needs a migration.
        #
        # Errored records (roughly 1 in 5) are KEPT. A failed transfer still
        # evidences a relationship between two addresses even though no money
        # moved. Filter with `WHERE error IS NULL` at query time when you want
        # actual flow.
        #
        # action_json holds the full original action, so any field we did not
        # break out can be recovered later with an UPDATE instead of a refetch
        # we cannot make.
        # -----------------------------------------------------------------
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS transfers (
                hash        TEXT PRIMARY KEY,
                time_ms     INTEGER NOT NULL,
                kind        TEXT,
                signer      TEXT,
                src         TEXT,
                dst         TEXT,
                error       TEXT,
                action_json TEXT NOT NULL,
                inserted_at TEXT NOT NULL
            )
        """)

        # -----------------------------------------------------------------
        # transfer_gaps - mirrors tape_gaps on the orderbook tracker.
        #
        # The failure mode worth recording is SATURATION: if the venue
        # produces more than 500 transfers between two polls, the page no
        # longer reaches back to where we left off and the difference is
        # gone for good. Detectable, so record it rather than discovering an
        # unexplained hole months later.
        # -----------------------------------------------------------------
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS transfer_gaps (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                gap_start_ms INTEGER NOT NULL,
                gap_end_ms   INTEGER NOT NULL,
                reason       TEXT NOT NULL,
                detected_at  TEXT NOT NULL
            )
        """)

        self.conn.commit()

    # NOTE: no secondary indexes yet, by decision. Add them when a query is
    # measurably slow, not before - the same way the tape indexes were added
    # on Sep 5 once the query patterns were actually known.

    def insert_batch(self, records: List[Dict]) -> Tuple[int, int]:
        """
        Insert a page of transfer records. Duplicates are dropped by the PK.

        Returns (inserted, skipped_malformed). Never raises on a single bad
        record - one malformed row must not block the rest of the page.
        """
        if not records:
            return 0, 0

        inserted_at = datetime.now(timezone.utc).isoformat()
        before = self.count()
        skipped = 0

        for rec in records:
            try:
                row = _extract(rec, inserted_at)
            except Exception as e:
                skipped += 1
                logger.warning(f"Skipping malformed transfer record: {e}")
                continue

            self.cursor.execute(
                "INSERT OR IGNORE INTO transfers "
                "(hash, time_ms, kind, signer, src, dst, error, action_json, inserted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )

        self.conn.commit()
        return self.count() - before, skipped

    def record_gap(self, start_ms: int, end_ms: int, reason: str):
        self.cursor.execute(
            "INSERT INTO transfer_gaps (gap_start_ms, gap_end_ms, reason, detected_at) "
            "VALUES (?, ?, ?, ?)",
            (start_ms, end_ms, reason, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        logger.warning(f"transfer_gap recorded: {start_ms}->{end_ms} ({reason})")

    def count(self) -> int:
        self.cursor.execute("SELECT COUNT(*) FROM transfers")
        return self.cursor.fetchone()[0]

    def max_time_ms(self) -> Optional[int]:
        self.cursor.execute("SELECT MAX(time_ms) FROM transfers")
        return self.cursor.fetchone()[0]

    def close(self):
        self.conn.close()


# =========================================================================
# Record extraction
# =========================================================================

def _norm(addr) -> Optional[str]:
    """Lowercase an address, or None. Case inconsistency is a silent join killer."""
    if not addr or not isinstance(addr, str):
        return None
    return addr.lower()


def _extract(rec: Dict, inserted_at: str) -> tuple:
    """
    Flatten one /transfers record into a row.

    sendAsset is handled exactly as confirmed from raw payloads on Sep 10:
        signer = record['user']          (always the signer)
        src    = action['fromSubAccount'] if non-empty else user
        dst    = action['destination']

    subAccountTransfer is a DOCUMENTED GUESS - the direction is inferred from
    isDeposit and has not been verified against a raw payload. Check it on the
    first run.

    Every other kind gets src/dst = NULL rather than a guess. action_json keeps
    the full original either way, so the columns can be backfilled with an
    UPDATE once the shapes are known. Guessing wrong writes bad edges into a
    graph you cannot refetch; NULL is recoverable.
    """
    action = rec.get("action") or {}
    kind = action.get("type")
    signer = _norm(rec.get("user"))

    src = dst = None

    if kind == "sendAsset":
        from_sub = action.get("fromSubAccount") or ""
        src = _norm(from_sub) if from_sub else signer
        dst = _norm(action.get("destination"))

    elif kind == "subAccountTransfer":
        # GUESS: isDeposit True means master -> sub.
        sub = _norm(action.get("subAccountUser"))
        if action.get("isDeposit"):
            src, dst = signer, sub
        else:
            src, dst = sub, signer

    elif kind == "usdClassTransfer":
        # No destination in the payload - a wallet moving its OWN USDC between
        # spot and perp. Not an edge between two addresses. Stored as a
        # self-loop so `WHERE src != dst` excludes it from graph queries while
        # keeping it as a capital-deployment signal: money moving INTO perp
        # (toPerp true) usually precedes trading.
        src = dst = signer

    elif kind in ("SystemSendAssetAction", "SystemSpotSendAction"):
        # Protocol-generated, zero errors. Signers are system addresses
        # (0x2000...) or a single protocol address, so signer IS the source.
        src = signer
        dst = _norm(action.get("destination"))

    elif kind in ("usdSend", "spotSend"):
        src = signer
        dst = _norm(action.get("destination"))

    h = rec.get("hash")
    if not h:
        raise ValueError(f"record has no hash: {str(rec)[:150]}")

    return (
        h,
        int(rec.get("time", 0)),
        kind,
        signer,
        src,
        dst,
        rec.get("error"),
        json.dumps(action, separators=(",", ":")),
        inserted_at,
    )


# =========================================================================
# Poller
# =========================================================================

class TransferDiscoveryPoller:
    """Polls /transfers and persists to transfers.db."""

    def __init__(self, hypurr_client, store: TransferStore = None, config: dict = None):
        self.client = hypurr_client
        self.store = store or TransferStore()
        self.config = config or {}

        # Dark on first deploy, same as the fees collector.
        self.enabled = self.config.get("collection_enabled", False)
        self.poll_interval_cycles = self.config.get(
            "poll_interval_cycles", DEFAULT_POLL_INTERVAL_CYCLES
        )

        self.poll_count = 0
        self.rows_inserted_total = 0
        self.last_error: Optional[str] = None
        self._last_page_max_ms: Optional[int] = None

        logger.info(
            f"TransferDiscoveryPoller initialized "
            f"(enabled={self.enabled}, interval={self.poll_interval_cycles} cycles, "
            f"rows={self.store.count()})"
        )

    def should_poll(self, cycle: int) -> bool:
        if not self.enabled or cycle <= 0:
            return False
        return (cycle % self.poll_interval_cycles) == 0

    def poll(self, force: bool = False) -> int:
        """
        Pull one page of /transfers and write what is new.

        Never raises. A Hypurrscan outage must not take down the main loop -
        but unlike the fees collector, a long outage here loses data, so
        failures are logged at WARNING rather than swallowed quietly.
        """
        if not self.enabled and not force:
            return 0

        try:
            records = self.client.get_transfers()
            if records is None:
                self.last_error = "poll: /transfers returned None"
                logger.warning(self.last_error)
                return 0
            if not records:
                logger.warning("Transfer poll got an empty page")
                return 0

            times = [int(r.get("time", 0)) for r in records if r.get("time")]
            page_min = min(times) if times else 0
            page_max = max(times) if times else 0

            # Saturation check: if this page starts AFTER the last page ended,
            # the firehose outran us and the difference is gone.
            if self._last_page_max_ms and page_min > self._last_page_max_ms:
                self.store.record_gap(
                    self._last_page_max_ms, page_min,
                    f"saturation: page of {len(records)} did not reach previous poll",
                )

            inserted, skipped = self.store.insert_batch(records)
            self._last_page_max_ms = page_max
            self.poll_count += 1
            self.rows_inserted_total += inserted

            span_s = (page_max - page_min) / 1000 if page_max else 0
            logger.info(
                f"Transfer poll #{self.poll_count}: +{inserted} new "
                f"({len(records)} in page, {len(records) - inserted - skipped} dupes, "
                f"{skipped} malformed, span {span_s:.0f}s, total {self.store.count()})"
            )
            return inserted

        except Exception as e:
            self.last_error = f"poll: {type(e).__name__}: {e}"
            logger.error(f"Transfer poll raised: {e}", exc_info=True)
            return 0

    def get_stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "poll_interval_cycles": self.poll_interval_cycles,
            "poll_count": self.poll_count,
            "rows_inserted_total": self.rows_inserted_total,
            "last_error": self.last_error,
            "table_row_count": self.store.count(),
        }


# =========================================================================
# Standalone entry point
# =========================================================================

def _inspect(store: TransferStore):
    """Print what actually landed, so the first run is verifiable."""
    print(f"\n  total rows: {store.count()}")

    store.cursor.execute(
        "SELECT kind, COUNT(*) n, SUM(error IS NOT NULL) errs "
        "FROM transfers GROUP BY kind ORDER BY n DESC"
    )
    print(f"\n  {'kind':<26}{'n':>7}{'errored':>9}")
    for r in store.cursor.fetchall():
        print(f"  {str(r['kind']):<26}{r['n']:>7}{r['errs'] or 0:>9}")

    store.cursor.execute(
        "SELECT kind, COUNT(*) n FROM transfers "
        "WHERE src IS NULL OR dst IS NULL GROUP BY kind ORDER BY n DESC"
    )
    unmapped = store.cursor.fetchall()
    if unmapped:
        print("\n  UNMAPPED src/dst (extraction needs these shapes):")
        for r in unmapped:
            print(f"    {r['kind']}: {r['n']}")

    store.cursor.execute("SELECT * FROM transfers ORDER BY time_ms DESC LIMIT 3")
    print("\n  most recent 3:")
    for r in store.cursor.fetchall():
        print(f"    {r['kind']:<20} {str(r['src'])[:12]}... -> {str(r['dst'])[:12]}...")
        print(f"      {r['action_json'][:160]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single poll, then inspect and exit")
    ap.add_argument("--sleep", type=int, default=DEFAULT_SLEEP_SECONDS)
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from api_client.hypurrscan_client import HypurrScanClient

    store = TransferStore(args.db)
    poller = TransferDiscoveryPoller(
        HypurrScanClient(), store, {"collection_enabled": True}
    )

    if args.once:
        poller.poll(force=True)
        _inspect(store)
        store.close()
        return

    logger.info(f"Continuous mode, polling every {args.sleep}s. Ctrl-C to stop.")
    try:
        while True:
            poller.poll(force=True)
            time.sleep(args.sleep)
    except KeyboardInterrupt:
        logger.info(f"Stopped. {poller.get_stats()}")
    finally:
        store.close()


if __name__ == "__main__":
    main()