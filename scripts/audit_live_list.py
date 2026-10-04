#!/usr/bin/env python3
"""Check Hypurrscan's LIVE twap list against Hyperliquid twapHistory.
Binding key: (user, Hypurrscan 'time' == twapHistory state.timestamp)."""
import json, time, collections, statistics
from pathlib import Path
from datetime import datetime, timezone
import requests
from api_client.hypurrscan_client import HypurrScanClient

URL = "https://api.hyperliquid.xyz/info"
cfg = json.load(open("twap_config.json")).get("hypurr_data", {})
twap_data = HypurrScanClient(cfg).get_whale_activity(['*']).get('twap_data', {})
now_ms = int(time.time() * 1000)

listed = []
for coin, orders in twap_data.items():
    for o in orders or []:
        label = o.get('ended') or ('error' if o.get('error') else 'active')
        listed.append({"coin": coin, "user": o.get("user"), "time": o.get("time"), "label": label})
by_user = collections.defaultdict(list)
for x in listed: by_user[x["user"]].append(x)
print(f"{len(listed)} listed orders, {len(by_user)} users, labels: {dict(collections.Counter(x['label'] for x in listed))}")

cross = collections.Counter(); linger = []; flagged = []
for i, (user, xs) in enumerate(by_user.items(), 1):
    try:
        h = requests.post(URL, json={"type": "twapHistory", "user": user}, timeout=30).json() or []
    except Exception as e:
        print(f"  FAIL {user}: {e}"); h = []
    latest = {}
    for e in h:
        k = e["state"]["timestamp"]
        if k not in latest or e["time"] > latest[k]["time"]: latest[k] = e
    for x in xs:
        e = latest.get(x["time"])
        chain = e["status"]["status"] if e else "no_match"
        cross[(x["label"], chain)] += 1
        if e and x["label"] != "active" and chain != "activated":
            t = e["time"] * 1000 if e["time"] < 1e11 else e["time"]
            linger.append((now_ms - t) / 60000)
        if (x["label"] == "active") != (chain == "activated"):
            ex = f'{float(e["state"]["executedSz"]) / float(e["state"]["sz"]):.1%}' if e else "-"
            rel = ""
            if e:
                t = e["time"] * 1000 if e["time"] < 1e11 else e["time"]
                rel = f'{(t - now_ms) / 60000:+.1f}m'
            flagged.append(f'  {x["label"]:9s} -> {chain:17s} exec={ex:6s} end_vs_fetch={rel:>9s} {x["coin"]:12s} {user[:10]}')
    if i % 25 == 0: print(f"  {i}/{len(by_user)} users")
    time.sleep(2.0)

print("\n=== Hypurrscan label -> chain status ===")
for (lab, ch), n in sorted(cross.items()): print(f"  {lab:10s} -> {ch:18s} {n}")
if linger:
    print(f"\nLinger after chain end (min): median {statistics.median(linger):.1f}, max {max(linger):.1f}, n={len(linger)}")
print(f"\n=== {len(flagged)} mismatches (active vs activated) ===")
print("\n".join(flagged[:60]))
out = Path("data") / f"audit_live_{datetime.now(timezone.utc):%Y%m%dT%H%M}.json"
out.write_text(json.dumps({"cross": {f"{a}->{b}": n for (a, b), n in cross.items()}, "flagged": flagged}, indent=1))
print(f"\nsaved {out}")
