#!/usr/bin/env python3
"""Do large mainnet tape participants exist outside whale_addresses?"""
import asyncio, json, sys, sqlite3, collections
import websockets

WS = "wss://api.hyperliquid.xyz/ws"
DB = "data/twap.db"

async def tape(coins, seconds):
    out = []
    async with websockets.connect(WS, ping_interval=20) as ws:
        for c in coins:
            await ws.send(json.dumps({"method": "subscribe",
                "subscription": {"type": "trades", "coin": c}}))
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

async def main(seconds, floor):
    coins = ["BTC", "HYPE"]
    trades = await tape(coins, seconds)
    print(f"prints: {len(trades)}  over {seconds}s on {coins}")

    ntl = collections.defaultdict(float)
    big = collections.defaultdict(int)
    for t in trades:
        v = float(t["px"]) * float(t["sz"])
        for u in t["users"]:
            ntl[u.lower()] += v
            if v >= floor:
                big[u.lower()] += 1

    print(f"distinct addresses: {len(ntl)}")
    print(f"addresses with >=1 print over ${floor:,.0f}: {len(big)}")

    con = sqlite3.connect(DB)
    known = {r[0].lower() for r in
             con.execute("SELECT address FROM whale_addresses")}
    con.close()
    print(f"whale_addresses rows: {len(known)}")

    cand = sorted(big, key=lambda a: ntl[a], reverse=True)
    miss = [a for a in cand if a not in known]
    print(f"\nlarge-print addresses: {len(cand)}   NOT tracked: {len(miss)}")
    print("\ntop 20 untracked by traded notional:")
    for a in miss[:20]:
        print(f"  {a}  ${ntl[a]:>14,.0f}  big_prints={big[a]}")

if __name__ == "__main__":
    s = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    f = float(sys.argv[2]) if len(sys.argv) > 2 else 50_000
    asyncio.run(main(s, f))
