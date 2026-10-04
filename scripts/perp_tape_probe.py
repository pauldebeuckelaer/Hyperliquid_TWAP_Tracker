#!/usr/bin/env python3
"""Does the perp trades feed carry a `users` array? One coin, N seconds."""
import asyncio, json, sys, collections
import websockets

URL = "wss://api.hyperliquid.xyz/ws"

async def probe(coin, seconds):
    trades = []
    async with websockets.connect(URL, ping_interval=20) as ws:
        await ws.send(json.dumps({
            "method": "subscribe",
            "subscription": {"type": "trades", "coin": coin},
        }))
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            try:
                remaining = end - asyncio.get_event_loop().time()
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                break
            ch = msg.get("channel")
            if ch == "trades":
                trades.extend(msg["data"])
            elif ch in ("error", "subscriptionResponse"):
                print(f"[{ch}] {json.dumps(msg)[:300]}")

    print(f"coin: {coin}   window: {seconds}s   trades: {len(trades)}")
    if not trades:
        print("NO PRINTS - illiquid coin, wrong name, or sub rejected")
        return

    print("keys:", sorted(trades[0].keys()))
    print("sample:", json.dumps(trades[0]))

    if "users" not in trades[0]:
        print("\n>>> NO `users` FIELD. Tape route is dead for perps.")
        return

    addrs = collections.Counter()
    for t in trades:
        for u in t["users"]:
            addrs[u] += 1
    print(f"\n>>> `users` PRESENT.")
    print(f"distinct addresses: {len(addrs)}")
    print(f"arity check (should all be 2): {set(len(t['users']) for t in trades)}")
    print("top 5 by print count:")
    for a, n in addrs.most_common(5):
        print(f"  {a}  {n}")

if __name__ == "__main__":
    coin = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    asyncio.run(probe(coin, secs))
