#!/usr/bin/env python3
"""Join tape prints to a wallet's own fills on tid; let `crossed` reveal users[] order."""
import asyncio, json, sys, collections, urllib.request
import websockets

WS = "wss://api.hyperliquid.xyz/ws"
INFO = "https://api.hyperliquid.xyz/info"

def user_fills(addr):
    req = urllib.request.Request(
        INFO, method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"type": "userFills", "user": addr}).encode())
    return json.load(urllib.request.urlopen(req))

async def tape(coin, seconds):
    out = []
    async with websockets.connect(WS, ping_interval=20) as ws:
        await ws.send(json.dumps({"method": "subscribe",
                                  "subscription": {"type": "trades", "coin": coin}}))
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            try:
                msg = json.loads(await asyncio.wait_for(
                    ws.recv(), timeout=end - asyncio.get_event_loop().time()))
            except asyncio.TimeoutError:
                break
            if msg.get("channel") == "trades":
                out.extend(msg["data"])
    return out

async def main(addr, coin, secs):
    trades = await tape(coin, secs)
    fills = {f["tid"]: f for f in user_fills(addr)}
    print(f"tape prints: {len(trades)}   wallet fills in buffer: {len(fills)}")

    tally = collections.Counter()
    matched = 0
    for t in trades:
        f = fills.get(t["tid"])
        if not f:
            continue
        matched += 1
        try:
            idx = [u.lower() for u in t["users"]].index(addr.lower())
        except ValueError:
            tally["addr_NOT_in_users"] += 1
            continue
        tally[f"idx{idx}_crossed={f['crossed']}"] += 1
        tally[f"idx{idx}_side_tape={t['side']}_fill={f['side']}"] += 1

    print(f"matched on tid: {matched}")
    for k, v in sorted(tally.items()):
        print(f"  {k}: {v}")

if __name__ == "__main__":
    a = sys.argv[1]
    c = sys.argv[2] if len(sys.argv) > 2 else "BTC"
    s = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    asyncio.run(main(a, c, s))
