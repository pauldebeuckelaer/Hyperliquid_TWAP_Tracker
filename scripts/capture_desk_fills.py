"""
CPI capture - Fri Sep 11 2026 (US CPI 12:30 UTC).

Forward-only: busy desk subs lose their fills from the API after ~2.5-3h
(10k-fill horizon), so this polls every non-empty sub of both desks live.

Run ON THE BOX, not in PyCharm - it has to keep running while your PC sleeps:
  cd ~/bots/Hyperliquid_TWAP_Analyzer
  venv/bin/python scripts/capture_desk_fills.py --once        # smoke test, one cycle now
  nohup venv/bin/python scripts/capture_desk_fills.py > logs/desk_capture.log 2>&1 &

Writes to data/desk_capture.db - its own file, NOT twap.db, so it never
competes with the tracker for the write lock. Delete it when you're done.
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

URL = "https://api.hyperliquid.xyz/info"
MASTERS = {
    "desk1": "0x8c625ff57d8a4374784c7eff585dfdc42ccec974",
    "desk2": "0x85ecf584f25db6f146718b86d493e33c5af72052",
}
START = datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 11, 15, 30, tzinfo=timezone.utc)
CYCLE_S = 300                  # every address once per 5 min, calls spread evenly
LOOKBACK_MS = 30 * 60 * 1000   # first poll per address reaches 30 min back
OVERLAP_MS = 10_000            # each poll re-reads 10 s; the primary key dedups
REFRESH_SUBS_S = 3600          # re-read desk membership hourly

DB = Path(__file__).resolve().parent.parent / "data" / "desk_capture.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
  user TEXT, tid INTEGER, time_ms INTEGER, coin TEXT, side TEXT, px REAL, sz REAL,
  dir TEXT, crossed INTEGER, fee REAL, fee_token TEXT, closed_pnl REAL,
  start_position REAL, oid INTEGER, hash TEXT, raw TEXT,
  PRIMARY KEY (user, tid));
CREATE TABLE IF NOT EXISTS subs (
  captured_ms INTEGER, desk TEXT, master TEXT, sub TEXT, name TEXT,
  account_value REAL, n_perp_pos INTEGER, n_spot_bal INTEGER, raw TEXT);
CREATE TABLE IF NOT EXISTS polls (
  polled_ms INTEGER, user TEXT, start_ms INTEGER, returned INTEGER,
  new INTEGER, pages INTEGER, status TEXT);
"""


def now_ms():
    return int(time.time() * 1000)


def log(msg):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def post(body):
    for attempt in range(4):
        try:
            r = requests.post(URL, json=body, timeout=30)
            if r.status_code == 429:           # rate limited: back off, the tracker shares this IP
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{body['type']} failed after 4 attempts")


def load_subs(con):
    """Both desks' membership via subAccounts; keep every sub that holds anything."""
    captured, subs = now_ms(), {}
    for desk, master in MASTERS.items():
        for s in post({"type": "subAccounts", "user": master}) or []:
            ch = s.get("clearinghouseState") or {}
            av = float((ch.get("marginSummary") or {}).get("accountValue", 0))
            npos = len(ch.get("assetPositions") or [])
            nspot = len((s.get("spotState") or {}).get("balances") or [])
            addr = s["subAccountUser"].lower()
            con.execute("INSERT INTO subs VALUES (?,?,?,?,?,?,?,?,?)",
                        (captured, desk, master, addr, s.get("name"), av, npos, nspot, json.dumps(s)))
            if av > 0 or npos > 0 or nspot > 0:
                subs[addr] = desk
        subs[master] = desk   # masters too: one call each, and we want to know if they ever trade
    con.commit()
    return subs


def poll(con, user, since):
    """All fills since `since`, paging forward past the 2000-per-call cap."""
    start, got, new, pages, max_t = since, 0, 0, 0, None
    while True:
        batch = post({"type": "userFillsByTime", "user": user,
                      "startTime": start, "endTime": now_ms()}) or []
        pages += 1
        got += len(batch)
        rows = [(user, f["tid"], f["time"], f["coin"], f["side"], float(f["px"]), float(f["sz"]),
                 f.get("dir"), int(bool(f.get("crossed"))), float(f.get("fee") or 0),
                 f.get("feeToken"), float(f.get("closedPnl") or 0),
                 float(f.get("startPosition") or 0), f.get("oid"), f.get("hash"), json.dumps(f))
                for f in batch]
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        new += con.total_changes - before
        if batch:
            bmax = max(f["time"] for f in batch)
            max_t = bmax if max_t is None else max(max_t, bmax)
        if len(batch) < 2000 or pages >= 10:
            break
        start = max_t
        time.sleep(0.5)
    con.commit()
    return max_t, got, new, pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one cycle right now, no pacing (smoke test)")
    args = ap.parse_args()

    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    if not args.once:
        wait = (START - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            log(f"waiting {wait / 3600:.1f}h until {START:%Y-%m-%d %H:%M} UTC")
            time.sleep(wait)

    while True:  # never die at the start line over a transient API failure
        try:
            subs = load_subs(con)
            break
        except Exception as e:
            log(f"subAccounts failed, retrying in 30s: {e}")
            time.sleep(30)
    refreshed = time.time()
    base = (now_ms() if args.once else int(START.timestamp() * 1000)) - LOOKBACK_MS
    since = {u: base for u in subs}
    per_call = 0 if args.once else CYCLE_S / max(len(subs), 1)
    log(f"polling {len(subs)} addresses "
        f"({sum(d == 'desk1' for d in subs.values())} desk1, "
        f"{sum(d == 'desk2' for d in subs.values())} desk2) until {END:%H:%M} UTC")

    while True:
        cyc_new, cyc_err = 0, 0
        for user in list(subs):
            t0 = time.time()
            used = since[user]
            try:
                max_t, got, new, pages = poll(con, user, used)
                status = "capped" if pages >= 10 else "ok"
                if max_t:
                    since[user] = max_t - OVERLAP_MS
            except Exception as e:
                got = new = pages = 0
                status = f"error: {str(e)[:100]}"
                cyc_err += 1
            cyc_new += new
            con.execute("INSERT INTO polls VALUES (?,?,?,?,?,?,?)",
                        (now_ms(), user, used, got, new, pages, status))
            con.commit()
            time.sleep(max(0.0, per_call - (time.time() - t0)))

        total = con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        log(f"cycle done: +{cyc_new} fills, {cyc_err} errors, {total} in db")

        if args.once or datetime.now(timezone.utc) >= END:
            break
        if time.time() - refreshed > REFRESH_SUBS_S:
            try:
                fresh = load_subs(con)
            except Exception as e:  # a failed refresh must not end the capture
                log(f"sub refresh failed, keeping current list: {e}")
                fresh = {}
            for u, d in fresh.items():
                if u not in subs:
                    subs[u] = d
                    since[u] = now_ms() - LOOKBACK_MS
                    log(f"new address joined: {u} ({d})")
            refreshed = time.time()
            per_call = CYCLE_S / max(len(subs), 1)

    log("capture finished")


if __name__ == "__main__":
    main()