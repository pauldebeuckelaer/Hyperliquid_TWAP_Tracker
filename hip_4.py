#!/usr/bin/env python3
"""
Polymarket HYPE/Hyperliquid Discovery Probe (v2)
=================================================

v2 changes:
- PAGE_SIZE corrected to 100 (Gamma's real limit)
- MAX_PAGES bumped to 50 to walk past the top-volume political/sports markets
- datetime.utcnow() -> datetime.now(timezone.utc) to silence DeprecationWarning
- Added running keyword-match counter so you can see progress

Purpose: Discover what HYPE/Hyperliquid-related markets exist on Polymarket.
Read-only probe. No DB writes, no auth.
"""

import csv
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

GAMMA_API = "https://gamma-api.polymarket.com/markets"
KEYWORDS = ["hyperliquid", "hype"]

# Filtering knobs
MIN_VOLUME_USD = 0           # Set to e.g. 1000 to filter out micro-markets
EXCLUDE_5MIN_NOISE = True    # Drop the 5-minute "Up or Down" markets
PAGE_SIZE = 100              # Gamma silently caps the limit param at 100
MAX_PAGES = 50               # 100 * 50 = 5,000 markets scanned

OUTPUT_CSV = Path("polymarket_hype_markets.csv")

# ---------------------------------------------------------------------------
# DATA MODEL
# ---------------------------------------------------------------------------

@dataclass
class HypeMarket:
    question: str
    slug: str
    condition_id: str
    yes_price: float
    no_price: float
    volume_total_usd: float
    volume_24h_usd: float
    liquidity_usd: float
    spread: float
    start_date: str
    end_date: str
    closed: bool
    active: bool
    category: str = ""
    url: str = field(init=False)

    def __post_init__(self):
        self.url = f"https://polymarket.com/event/{self.slug}" if self.slug else ""

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def fetch_page(offset: int = 0, limit: int = PAGE_SIZE) -> list[dict]:
    """One page of active markets from Gamma, sorted by 24h volume desc."""
    params = {
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
    }
    try:
        r = requests.get(GAMMA_API, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [ERROR offset={offset}] {e}")
        return []

def fetch_all_active() -> list[dict]:
    """Walk pages until we get an empty page or hit MAX_PAGES."""
    all_markets = []
    matched_running = 0
    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        batch = fetch_page(offset, PAGE_SIZE)
        if not batch:
            print(f"  page {page:3d}: empty, stopping")
            break
        all_markets.extend(batch)
        page_matched = sum(1 for m in batch if matches_keyword(m, KEYWORDS))
        matched_running += page_matched
        print(f"  page {page:3d}: +{len(batch):4d}  (total {len(all_markets):5d}, "
              f"matched_this_page {page_matched:2d}, matched_running {matched_running:3d})")
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(0.25)  # Gentle on the API
    return all_markets

# ---------------------------------------------------------------------------
# FILTERING
# ---------------------------------------------------------------------------

def matches_keyword(market: dict, keywords: list[str]) -> bool:
    """True if any keyword appears in question/slug/description (case-insensitive)."""
    fields = [
        (market.get("question") or "").lower(),
        (market.get("slug") or "").lower(),
        (market.get("description") or "").lower(),
    ]
    text = " ".join(fields)
    return any(kw in text for kw in keywords)

def is_5min_noise(market: dict) -> bool:
    """The 5-minute Up/Down markets — high count, no analytical value."""
    q = (market.get("question") or "").lower()
    slug = (market.get("slug") or "").lower()
    if "up or down" in q or "updown" in slug:
        return True
    if "5m" in slug or "-5m-" in slug:
        return True
    return False

# ---------------------------------------------------------------------------
# NORMALIZATION
# ---------------------------------------------------------------------------

def to_market(m: dict) -> Optional[HypeMarket]:
    """Coerce a Gamma market dict into our typed model. None if malformed."""
    try:
        return HypeMarket(
            question=m.get("question") or "",
            slug=m.get("slug") or "",
            condition_id=m.get("conditionId") or "",
            yes_price=float(m.get("lastTradePrice") or 0),
            no_price=round(1 - float(m.get("lastTradePrice") or 0), 4),
            volume_total_usd=float(m.get("volume") or 0),
            volume_24h_usd=float(m.get("volume24hr") or 0),
            liquidity_usd=float(m.get("liquidity") or 0),
            spread=float(m.get("spread") or 0),
            start_date=(m.get("startDate") or "")[:10],
            end_date=(m.get("endDate") or "")[:10],
            closed=bool(m.get("closed")),
            active=bool(m.get("active")),
            category=m.get("category") or "",
        )
    except Exception as e:
        print(f"  [skip malformed market] {e}")
        return None

# ---------------------------------------------------------------------------
# REPORTING
# ---------------------------------------------------------------------------

def print_market(idx: int, m: HypeMarket) -> None:
    print(f"\n[{idx}] {m.question[:90]}")
    print(f"    slug:       {m.slug}")
    print(f"    yes/no:     {m.yes_price:.3f} / {m.no_price:.3f}")
    print(f"    vol_total:  ${m.volume_total_usd:>14,.0f}")
    print(f"    vol_24h:    ${m.volume_24h_usd:>14,.0f}")
    print(f"    liquidity:  ${m.liquidity_usd:>14,.0f}")
    print(f"    window:     {m.start_date}  ->  {m.end_date}")
    print(f"    url:        {m.url}")

def summary(markets: list[HypeMarket]) -> None:
    print("\n" + "=" * 90)
    print(f"SUMMARY")
    print("=" * 90)
    print(f"  Total matched markets:    {len(markets)}")
    if not markets:
        return
    total_vol = sum(m.volume_total_usd for m in markets)
    total_24h = sum(m.volume_24h_usd for m in markets)
    total_liq = sum(m.liquidity_usd for m in markets)
    print(f"  Aggregate total volume:   ${total_vol:>16,.0f}")
    print(f"  Aggregate 24h volume:     ${total_24h:>16,.0f}")
    print(f"  Aggregate liquidity:      ${total_liq:>16,.0f}")
    print(f"  Top market by 24h vol:    {markets[0].question[:70]}")
    print(f"     volume_24h:  ${markets[0].volume_24h_usd:,.0f}")

def write_csv(markets: list[HypeMarket], path: Path) -> None:
    if not markets:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(markets[0]).keys()))
        writer.writeheader()
        for m in markets:
            writer.writerow(asdict(m))
    print(f"\n  CSV written: {path.resolve()}")

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    started = datetime.now(timezone.utc)
    print(f"Polymarket HYPE/Hyperliquid Discovery Probe (v2)")
    print(f"  Started: {started.isoformat()}")
    print(f"  Keywords: {KEYWORDS}")
    print(f"  Filters:  min_vol={MIN_VOLUME_USD}  exclude_5min={EXCLUDE_5MIN_NOISE}")
    print(f"  Pagination: {PAGE_SIZE} per page, up to {MAX_PAGES} pages "
          f"({PAGE_SIZE * MAX_PAGES} markets max)")
    print(f"\nFetching active markets (paged):")

    raw = fetch_all_active()
    print(f"\n  Total active markets scanned: {len(raw)}")

    # Stage 1: keyword match
    matched_raw = [m for m in raw if matches_keyword(m, KEYWORDS)]
    print(f"  After keyword filter:         {len(matched_raw)}")

    # Stage 2: 5-min noise filter
    if EXCLUDE_5MIN_NOISE:
        matched_raw = [m for m in matched_raw if not is_5min_noise(m)]
        print(f"  After 5min-noise filter:      {len(matched_raw)}")

    # Stage 3: normalize
    markets = [m for m in (to_market(d) for d in matched_raw) if m is not None]

    # Stage 4: min volume
    if MIN_VOLUME_USD > 0:
        markets = [m for m in markets if m.volume_total_usd >= MIN_VOLUME_USD]
        print(f"  After min-volume filter:      {len(markets)}")

    # Sort by 24h volume desc
    markets.sort(key=lambda m: -m.volume_24h_usd)

    # Display
    for i, m in enumerate(markets, 1):
        print_market(i, m)

    summary(markets)
    write_csv(markets, OUTPUT_CSV)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\n  Elapsed: {elapsed:.1f}s")

if __name__ == "__main__":
    main()