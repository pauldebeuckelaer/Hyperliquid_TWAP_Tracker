#!/usr/bin/env python3
"""
Hyperliquid API Rate Limit Tester
=================================
Tests different request rates to find the rate limit threshold.
"""
import asyncio
import aiohttp
import time
from datetime import datetime

API_URL = "https://api.hyperliquid.xyz/info"

# Test addresses (known whales)
TEST_ADDRESSES = [
    "0x11ae429b414424f21d713b004dcffdce494fd868",
    "0x013fca0778c47bf2f32b53529a9f31fa1506960d",
    "0x041836d6c0617c694b70b57680f45905811076db",
    "0x056295dd90c3a34d9a8f371ec961ce188e35c069",
    "0x059afba094a3ba2b35006121c10ebf7eb2eeaa34",
]


async def make_request(session: aiohttp.ClientSession, address: str, request_type: str):
    """Make a single API request and return result"""

    if request_type == "perp":
        payload = {"type": "clearinghouseState", "user": address}
    elif request_type == "spot":
        payload = {"type": "spotClearinghouseState", "user": address}
    elif request_type == "vault":
        payload = {"type": "userVaultEquities", "user": address}
    else:
        payload = {"type": "clearinghouseState", "user": address}

    start = time.time()
    try:
        async with session.post(
                API_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            elapsed = time.time() - start

            # Check for rate limit headers
            rate_headers = {k: v for k, v in response.headers.items()
                            if 'rate' in k.lower() or 'limit' in k.lower() or 'retry' in k.lower()}

            return {
                "status": response.status,
                "elapsed": elapsed,
                "headers": rate_headers,
                "success": response.status == 200
            }
    except asyncio.TimeoutError:
        return {"status": "timeout", "elapsed": time.time() - start, "success": False}
    except Exception as e:
        return {"status": f"error: {e}", "elapsed": time.time() - start, "success": False}


async def test_sequential(delay: float, num_requests: int = 20):
    """Test sequential requests with a delay"""
    print(f"\n{'=' * 60}")
    print(f"TEST: Sequential requests with {delay}s delay")
    print(f"{'=' * 60}")

    async with aiohttp.ClientSession() as session:
        results = []
        for i in range(num_requests):
            addr = TEST_ADDRESSES[i % len(TEST_ADDRESSES)]
            result = await make_request(session, addr, "perp")
            results.append(result)

            status = "✅" if result["success"] else f"❌ {result['status']}"
            print(f"  [{i + 1:2d}/{num_requests}] {status} ({result['elapsed'] * 1000:.0f}ms)")

            if result.get("headers"):
                print(f"       Rate headers: {result['headers']}")

            if i < num_requests - 1:
                await asyncio.sleep(delay)

        success_rate = sum(1 for r in results if r["success"]) / len(results) * 100
        print(f"\n  Result: {success_rate:.0f}% success ({sum(1 for r in results if r['success'])}/{num_requests})")
        return success_rate


async def test_burst(burst_size: int, num_bursts: int = 3, delay_between: float = 5.0):
    """Test burst requests (parallel) with delay between bursts"""
    print(f"\n{'=' * 60}")
    print(f"TEST: Burst of {burst_size} parallel requests, {delay_between}s between bursts")
    print(f"{'=' * 60}")

    async with aiohttp.ClientSession() as session:
        all_results = []

        for burst in range(num_bursts):
            print(f"\n  Burst {burst + 1}/{num_bursts}:")

            # Fire burst_size requests in parallel
            tasks = []
            for i in range(burst_size):
                addr = TEST_ADDRESSES[i % len(TEST_ADDRESSES)]
                tasks.append(make_request(session, addr, "perp"))

            results = await asyncio.gather(*tasks)
            all_results.extend(results)

            for i, result in enumerate(results):
                status = "✅" if result["success"] else f"❌ {result['status']}"
                print(f"    [{i + 1:2d}/{burst_size}] {status} ({result['elapsed'] * 1000:.0f}ms)")

                if result.get("headers"):
                    print(f"         Rate headers: {result['headers']}")

            if burst < num_bursts - 1:
                print(f"  Waiting {delay_between}s...")
                await asyncio.sleep(delay_between)

        success_rate = sum(1 for r in all_results if r["success"]) / len(all_results) * 100
        print(
            f"\n  Result: {success_rate:.0f}% success ({sum(1 for r in all_results if r['success'])}/{len(all_results)})")
        return success_rate


async def test_3_calls_per_whale(delay: float, num_whales: int = 10):
    """Test realistic scenario: 3 parallel calls per whale, sequential whales"""
    print(f"\n{'=' * 60}")
    print(f"TEST: 3 parallel calls per whale, {delay}s between whales")
    print(f"{'=' * 60}")

    async with aiohttp.ClientSession() as session:
        all_results = []

        for i in range(num_whales):
            addr = TEST_ADDRESSES[i % len(TEST_ADDRESSES)]

            # 3 parallel calls for this whale
            tasks = [
                make_request(session, addr, "perp"),
                make_request(session, addr, "spot"),
                make_request(session, addr, "vault"),
            ]
            results = await asyncio.gather(*tasks)
            all_results.extend(results)

            perp = "✅" if results[0]["success"] else "❌"
            spot = "✅" if results[1]["success"] else "❌"
            vault = "✅" if results[2]["success"] else "❌"
            print(f"  Whale {i + 1:2d}/{num_whales}: perp={perp} spot={spot} vault={vault}")

            if i < num_whales - 1:
                await asyncio.sleep(delay)

        success_rate = sum(1 for r in all_results if r["success"]) / len(all_results) * 100
        print(
            f"\n  Result: {success_rate:.0f}% success ({sum(1 for r in all_results if r['success'])}/{len(all_results)})")
        return success_rate


async def main():
    print("=" * 60)
    print("HYPERLIQUID API RATE LIMIT TESTER")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    # Test 1: Sequential with different delays
    print("\n\n>>> SEQUENTIAL TESTS <<<")
    await test_sequential(delay=0.5, num_requests=20)
    await asyncio.sleep(10)  # Cool down

    await test_sequential(delay=1.0, num_requests=20)
    await asyncio.sleep(10)

    await test_sequential(delay=2.0, num_requests=20)
    await asyncio.sleep(10)

    # Test 2: Burst tests
    print("\n\n>>> BURST TESTS <<<")
    await test_burst(burst_size=3, num_bursts=5, delay_between=3.0)
    await asyncio.sleep(10)

    await test_burst(burst_size=5, num_bursts=5, delay_between=5.0)
    await asyncio.sleep(10)

    await test_burst(burst_size=9, num_bursts=3, delay_between=5.0)
    await asyncio.sleep(10)

    # Test 3: Realistic whale scenario
    print("\n\n>>> REALISTIC WHALE SNAPSHOT TESTS <<<")
    await test_3_calls_per_whale(delay=1.0, num_whales=10)
    await asyncio.sleep(10)

    await test_3_calls_per_whale(delay=2.0, num_whales=10)
    await asyncio.sleep(10)

    await test_3_calls_per_whale(delay=3.0, num_whales=10)

    print("\n\n" + "=" * 60)
    print("TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())