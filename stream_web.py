import asyncio, json, websockets

WS_URL = "wss://api.hyperliquid.xyz/ws"
COIN   = "xyz:XYZ100"   # an active xyz market from your probe; try xyz:SP500 if quiet

async def go():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "method": "subscribe",
            "subscription": {"type": "trades", "coin": COIN}
        }))
        seen = 0
        while seen < 3:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            msg = json.loads(raw)
            if msg.get("channel") != "trades":      # skip the subscription-ack frame
                print("(non-trade frame:", msg.get("channel"), ")")
                continue
            print(json.dumps(msg, indent=2))
            seen += 1
        print("\n--- done ---")

asyncio.run(go())