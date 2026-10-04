#!/usr/bin/env python3
"""Probe HIP-4 outcome trade tape for counterparty identification.

One question: does the trades feed carry a `users` field on outcome coins?
If yes, participants are readable off the tape and can be intersected with
whale_addresses. Scratch probe -- sync websocket-client, not the async
`websockets` the collector uses. Do not import from this.
"""
import json
import time
import sys

COINS = ["#12270", "#12100"]      # Fed NoChange leg, Oct-1 BTC terminal
DURATION = 180

try:
    import websocket
except ImportError:
    sys.exit("no websocket-client on this interpreter")

seen, users, samples = 0, set(), []
ws = websocket.create_connection("wss://api.hyperliquid.xyz/ws", timeout=10)
for c in COINS:
    ws.send(json.dumps({"method": "subscribe",
                        "subscription": {"type": "trades", "coin": c}}))

ws.settimeout(5)
end = time.time() + DURATION
while time.time() < end:
    try:
        m = json.loads(ws.recv())
    except Exception:
        continue
    if m.get("channel") != "trades":
        print("meta:", json.dumps(m)[:200])
        continue
    for t in m.get("data", []):
        seen += 1
        if len(samples) < 3:
            samples.append(t)
        for u in t.get("users", []) or []:
            users.add(u.lower())

ws.close()
for s in samples:
    print("RAW TRADE:", json.dumps(s, indent=2))
print(f"\ntrades: {seen}   distinct users: {len(users)}")
for u in sorted(users):
    print(" ", u)
