"""
Standalone probe: what does clearinghouseState actually return?

Read-only. No DB access. No imports from the project.
Dumps the raw JSON to a file, then prints a field inventory.

Run it, read the summary, keep the JSON, delete the script.
"""

import json
import os
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------- config

API_URL = "https://api.hyperliquid.xyz/info"

# A wallet with BOTH a mainnet leg and an xyz: leg, so the HIP-3
# question gets answered in the same run.
ADDRESS = "0xb83de012dba672c76a7dbbbf3e459cb59d7d6e36"

# None = mainnet (no dex param). Add HIP-3 dexes to compare.
DEXES = [None, "xyz"]

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- fetch


def fetch(address, dex=None):
    payload = {"type": "clearinghouseState", "user": address}
    if dex:
        payload["dex"] = dex
    r = requests.post(API_URL, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- report


def describe(value):
    """One-line type description, so nested shapes are visible."""
    if isinstance(value, dict):
        return f"dict({', '.join(value.keys())})"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return f"{type(value).__name__} = {value!r}"


def report(state, label):
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")

    if not state:
        print("  EMPTY RESPONSE")
        return

    print("\n-- TOP-LEVEL KEYS " + "-" * 52)
    for k, v in state.items():
        print(f"  {k:<24} {describe(v)}")

    ms = state.get("marginSummary", {})
    if ms:
        print("\n-- marginSummary " + "-" * 53)
        for k, v in ms.items():
            print(f"  {k:<24} {v}")

    cms = state.get("crossMarginSummary", {})
    if cms:
        print("\n-- crossMarginSummary " + "-" * 48)
        for k, v in cms.items():
            print(f"  {k:<24} {v}")

    positions = state.get("assetPositions", [])
    print(f"\n-- assetPositions: {len(positions)} " + "-" * 48)

    if not positions:
        print("  none open")
        return

    # Full field inventory from the first position.
    first = positions[0].get("position", {})
    print("\n  FIELDS ON A POSITION OBJECT:")
    for k, v in first.items():
        print(f"    {k:<22} {describe(v)}")

    # The point of the exercise: cumFunding per coin, next to size.
    print("\n  CUMFUNDING PER POSITION")
    print(f"  {'coin':<18} {'szi':>16} {'allTime':>14} "
          f"{'sinceOpen':>14} {'sinceChange':>14}")
    print("  " + "-" * 80)

    missing = []
    for entry in positions:
        p = entry.get("position", {})
        coin = p.get("coin", "?")
        szi = p.get("szi", "?")
        cf = p.get("cumFunding")
        if cf is None:
            missing.append(coin)
            print(f"  {coin:<18} {szi:>16} {'-- NO cumFunding --':>44}")
        else:
            print(f"  {coin:<18} {szi:>16} "
                  f"{cf.get('allTime', '?'):>14} "
                  f"{cf.get('sinceOpen', '?'):>14} "
                  f"{cf.get('sinceChange', '?'):>14}")

    if missing:
        print(f"\n  !! cumFunding ABSENT on: {', '.join(missing)}")
    else:
        print("\n  cumFunding present on every position.")


# ---------------------------------------------------------------- main


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = {}

    for dex in DEXES:
        label = f"dex={dex}" if dex else "mainnet (no dex param)"
        try:
            state = fetch(ADDRESS, dex)
        except Exception as exc:
            print(f"\n{label}: REQUEST FAILED -- {exc}")
            continue
        dump[dex or "mainnet"] = state
        report(state, f"{ADDRESS[:10]}...  |  {label}")

    out_path = os.path.join(OUT_DIR, f"clearinghouse_probe_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(dump, fh, indent=2)

    print(f"\n\nRaw JSON written to:\n  {out_path}")
    print("\nKeep that file. It is the reference for the table audit.")


if __name__ == "__main__":
    main()