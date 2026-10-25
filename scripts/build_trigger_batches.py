#!/usr/bin/env python3
"""
build_trigger_batches.py - derive oracle-triggered batches from tape_prints.

A "tick" is one transaction hash (tape_prints.order_id) whose fills involve
2 or more distinct takers. On Hyperliquid one user's action is signed by one
account, so many takers under one hash means the engine fired many accounts'
trigger orders (stops / take-profits / liquidations) in one oracle-update
transaction. Verified on 2026-10-24: multi-coin, mixed-side, ~3 s cadence.

Taker convention: side='A' -> seller is the taker, side='B' -> buyer is.

Output goes to a SEPARATE database (triggers.db) so the tape collector
never waits on a lock held by this script. twap.db is opened read-only.

Tables
  trigger_ticks          one row per tick, all coins combined
  trigger_tick_coins     one row per tick x coin: size, notional, price range
  trigger_episodes       runs of BIG ticks no more than --gap seconds apart
                         (fully rebuilt on every run, so params can change)
  trigger_episode_ticks  which ticks belong to which episode, in order
  build_state            last processed timestamp and the params used

Usage
  python3 build_trigger_batches.py                     incremental update
  python3 build_trigger_batches.py --since 2026-10-24  rebuild from a date (UTC)
  python3 build_trigger_batches.py --big 50 --gap 10   episode parameters
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

DATA_DIR = os.path.expanduser("~/bots/Hyperliquid_TWAP_Analyzer/data")
TWAP_DB = os.path.join(DATA_DIR, "twap.db")
OUT_DB = os.path.join(DATA_DIR, "triggers.db")

CHUNK_MS = 6 * 3600 * 1000      # process the tape in 6-hour slices
PAD_MS = 10_000                 # look 10 s past slice edges so no tick is cut
SAFETY_LAG_MS = 120_000         # skip the last 2 min: collector may still be writing it
RESUME_OVERLAP_MS = 60_000      # incremental runs re-read the last minute

TAKER = "CASE WHEN side='A' THEN seller ELSE buyer END"
P_TAKER = "CASE WHEN p.side='A' THEN p.seller ELSE p.buyer END"
P_NOTIONAL = "CAST(p.px AS REAL) * CAST(p.sz AS REAL)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trigger_ticks (
    hash      TEXT PRIMARY KEY,
    ts_ms     INTEGER NOT NULL,
    n_takers  INTEGER NOT NULL,
    n_coins   INTEGER NOT NULL,
    n_sides   INTEGER NOT NULL,
    n_prints  INTEGER NOT NULL,
    sell_usd  REAL    NOT NULL,     -- taker-sell notional (longs stopped / liquidated)
    buy_usd   REAL    NOT NULL      -- taker-buy notional (shorts stopped / TPs)
);
CREATE INDEX IF NOT EXISTS idx_ticks_ts     ON trigger_ticks(ts_ms);
CREATE INDEX IF NOT EXISTS idx_ticks_takers ON trigger_ticks(n_takers);

CREATE TABLE IF NOT EXISTS trigger_tick_coins (
    hash      TEXT    NOT NULL,
    coin      TEXT    NOT NULL,
    ts_ms     INTEGER NOT NULL,
    n_takers  INTEGER NOT NULL,
    n_prints  INTEGER NOT NULL,
    sell_sz   REAL    NOT NULL,
    buy_sz    REAL    NOT NULL,
    sell_usd  REAL    NOT NULL,
    buy_usd   REAL    NOT NULL,
    px_hi     REAL,
    px_lo     REAL,
    PRIMARY KEY (hash, coin)
);
CREATE INDEX IF NOT EXISTS idx_tc_coin_ts ON trigger_tick_coins(coin, ts_ms);

CREATE TABLE IF NOT EXISTS trigger_episodes (
    episode_id  INTEGER PRIMARY KEY,
    start_ms    INTEGER NOT NULL,
    end_ms      INTEGER NOT NULL,
    n_ticks     INTEGER NOT NULL,
    max_takers  INTEGER NOT NULL,
    sell_usd    REAL    NOT NULL,
    buy_usd     REAL    NOT NULL,
    direction   TEXT    NOT NULL,   -- 'down' = sell-dominated, 'up' = buy-dominated
    peak_hash   TEXT    NOT NULL,   -- tick with the largest dominant-side notional
    peak_pos    INTEGER NOT NULL    -- its position in the episode (1 = first tick)
);
CREATE INDEX IF NOT EXISTS idx_ep_start ON trigger_episodes(start_ms);

CREATE TABLE IF NOT EXISTS trigger_episode_ticks (
    episode_id INTEGER NOT NULL,
    pos        INTEGER NOT NULL,
    hash       TEXT    NOT NULL,
    PRIMARY KEY (episode_id, pos)
);
CREATE INDEX IF NOT EXISTS idx_ept_hash ON trigger_episode_ticks(hash);

CREATE TABLE IF NOT EXISTS build_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

TICK_SQL = f"""
SELECT order_id,
       MIN(ts)                  AS ts_ms,
       COUNT(DISTINCT {TAKER})  AS n_takers,
       COUNT(DISTINCT coin)     AS n_coins,
       COUNT(DISTINCT side)     AS n_sides,
       COUNT(*)                 AS n_prints,
       SUM(CASE WHEN side='A' THEN CAST(px AS REAL)*CAST(sz AS REAL) ELSE 0 END),
       SUM(CASE WHEN side='B' THEN CAST(px AS REAL)*CAST(sz AS REAL) ELSE 0 END)
FROM tape_prints
WHERE order_id IS NOT NULL AND ts >= :lo AND ts < :hi
GROUP BY order_id
HAVING n_takers >= 2 AND MIN(ts) >= :a AND MIN(ts) < :b
"""

COIN_SQL = f"""
WITH t AS (
    SELECT order_id
    FROM tape_prints
    WHERE order_id IS NOT NULL AND ts >= :lo AND ts < :hi
    GROUP BY order_id
    HAVING COUNT(DISTINCT {TAKER}) >= 2 AND MIN(ts) >= :a AND MIN(ts) < :b
)
SELECT p.order_id,
       p.coin,
       MIN(p.ts),
       COUNT(DISTINCT {P_TAKER}),
       COUNT(*),
       SUM(CASE WHEN p.side='A' THEN CAST(p.sz AS REAL) ELSE 0 END),
       SUM(CASE WHEN p.side='B' THEN CAST(p.sz AS REAL) ELSE 0 END),
       SUM(CASE WHEN p.side='A' THEN {P_NOTIONAL} ELSE 0 END),
       SUM(CASE WHEN p.side='B' THEN {P_NOTIONAL} ELSE 0 END),
       MAX(CAST(p.px AS REAL)),
       MIN(CAST(p.px AS REAL))
FROM tape_prints p
JOIN t USING (order_id)
WHERE p.ts >= :lo AND p.ts < :hi
GROUP BY p.order_id, p.coin
"""


def hx(v):
    """order_id is a 32-byte BLOB; store it as the 0x-hex string Hypurrscan shows."""
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "0x" + bytes(v).hex()
    return str(v).lower()


def parse_utc(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass
    sys.exit(f"--since: can't parse '{s}', use YYYY-MM-DD or 'YYYY-MM-DD HH:MM' (UTC)")


def fmt_ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_state(out, key):
    row = out.execute("SELECT value FROM build_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(out, key, value):
    out.execute("INSERT OR REPLACE INTO build_state(key, value) VALUES (?, ?)", (key, str(value)))


def process_slice(src, out, a, b):
    params = {"lo": a - PAD_MS, "hi": b + PAD_MS, "a": a, "b": b}
    ticks = [(hx(r[0]),) + tuple(r[1:]) for r in src.execute(TICK_SQL, params)]
    coins = [(hx(r[0]),) + tuple(r[1:]) for r in src.execute(COIN_SQL, params)]
    out.executemany("INSERT OR REPLACE INTO trigger_ticks VALUES (?,?,?,?,?,?,?,?)", ticks)
    out.executemany("INSERT OR REPLACE INTO trigger_tick_coins VALUES (?,?,?,?,?,?,?,?,?,?,?)", coins)
    return len(ticks)


def rebuild_episodes(out, big, gap_s):
    """Chain BIG ticks (n_takers >= big) whose gap is <= gap_s into episodes.
    Small ticks between them do not bridge a gap."""
    gap_ms = gap_s * 1000
    rows = out.execute(
        "SELECT hash, ts_ms, n_takers, sell_usd, buy_usd FROM trigger_ticks "
        "WHERE n_takers >= ? ORDER BY ts_ms", (big,)).fetchall()

    groups, cur = [], []
    for r in rows:
        if cur and r[1] - cur[-1][1] > gap_ms:
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)

    out.execute("DELETE FROM trigger_episodes")
    out.execute("DELETE FROM trigger_episode_ticks")
    for eid, g in enumerate(groups, start=1):
        sell = sum(r[3] for r in g)
        buy = sum(r[4] for r in g)
        direction = "down" if sell >= buy else "up"
        k = 3 if direction == "down" else 4
        peak_pos, peak = max(enumerate(g, start=1), key=lambda x: x[1][k])
        out.execute(
            "INSERT INTO trigger_episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, g[0][1], g[-1][1], len(g), max(r[2] for r in g),
             sell, buy, direction, peak[0], peak_pos))
        out.executemany(
            "INSERT INTO trigger_episode_ticks VALUES (?,?,?)",
            [(eid, pos, r[0]) for pos, r in enumerate(g, start=1)])
    return len(groups)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="rebuild from this UTC date/time (deletes newer rows first)")
    ap.add_argument("--big", type=int, default=50, help="min takers for a BIG tick (default 50)")
    ap.add_argument("--gap", type=int, default=10, help="max seconds between big ticks in one episode (default 10)")
    ap.add_argument("--twap-db", default=TWAP_DB)
    ap.add_argument("--out-db", default=OUT_DB)
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{args.twap_db}?mode=ro", uri=True, timeout=60)
    out = sqlite3.connect(args.out_db, timeout=60)
    out.executescript(SCHEMA)

    tape_min, tape_max = src.execute("SELECT MIN(ts), MAX(ts) FROM tape_prints").fetchone()
    if tape_max is None:
        sys.exit("tape_prints is empty")
    end = tape_max - SAFETY_LAG_MS

    if args.since:
        start = parse_utc(args.since)
        out.execute("DELETE FROM trigger_ticks WHERE ts_ms >= ?", (start,))
        out.execute("DELETE FROM trigger_tick_coins WHERE ts_ms >= ?", (start,))
        out.commit()
    else:
        last = get_state(out, "last_ts_ms")
        start = int(last) - RESUME_OVERLAP_MS if last else tape_min

    if start >= end:
        print("Nothing new to process.")
    else:
        print(f"Processing {fmt_ms(start)} -> {fmt_ms(end)} UTC")
        t0, total, a = time.time(), 0, start
        while a < end:
            b = min(a + CHUNK_MS, end)
            n = process_slice(src, out, a, b)
            set_state(out, "last_ts_ms", b)
            out.commit()
            total += n
            print(f"  {fmt_ms(a)}  +{n:>5} ticks   ({time.time() - t0:5.0f}s)", flush=True)
            a = b
        print(f"Ticks written: {total}")

    n_ep = rebuild_episodes(out, args.big, args.gap)
    set_state(out, "big", args.big)
    set_state(out, "gap_s", args.gap)
    set_state(out, "built_at", fmt_ms(int(time.time() * 1000)))
    out.commit()

    n_ticks, n_big = out.execute(
        "SELECT COUNT(*), SUM(n_takers >= ?) FROM trigger_ticks", (args.big,)).fetchone()
    print(f"\nTotal ticks: {n_ticks}   big (>= {args.big} takers): {n_big or 0}   "
          f"episodes (gap <= {args.gap}s): {n_ep}")

    print("\nLatest episodes:")
    print(f"  {'start UTC':19}  {'ticks':>5}  {'max_tk':>6}  {'dir':4}  {'sell $M':>8}  {'buy $M':>7}  peak_pos")
    for r in out.execute(
            "SELECT start_ms, n_ticks, max_takers, direction, sell_usd, buy_usd, peak_pos "
            "FROM trigger_episodes ORDER BY start_ms DESC LIMIT 10"):
        print(f"  {fmt_ms(r[0])}  {r[1]:>5}  {r[2]:>6}  {r[3]:4}  {r[4]/1e6:8.1f}  {r[5]/1e6:7.1f}  {r[6]:>3}")


if __name__ == "__main__":
    main()