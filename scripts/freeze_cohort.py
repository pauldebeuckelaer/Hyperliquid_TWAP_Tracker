#!/usr/bin/env python3
"""
freeze_cohort.py - freeze a baseline snapshot of a wallet cohort for one coin, and score it later.

FREEZE
  python3 scripts/freeze_cohort.py freeze 0x9e2cbb5d 0x0748e296 0x734531e8 --coin HYPE
  -> data/freeze_HYPE_cohort_<YYYY-MM-DDTHHMMZ>.json
     raw clearinghouseState + open orders for the coin, per wallet, plus the mid at freeze time.
     Prefixes are resolved through tape_addresses (must match exactly one address).

SCORE
  python3 scripts/freeze_cohort.py score data/freeze_HYPE_cohort_<...>.json
  Per wallet, baseline szi vs current szi:
     B closed   (<10% left)
     C reduced  (10-75%)
     D held     (75-125%)
     E added    (>125%) or flipped side
     A (liquidated / stopped out) cannot be told from positions alone:
       every B or C is flagged "check tape for forced/stop exit".
  Wallets flat at baseline: "re-entered" = yes if |szi| >= --reentry (default 10000).
  Also prints whether any current stop on the coin sits on the safe side of liquidation
  (above liq for a long, below liq for a short).
  Spot balance and (for HYPE) staking are frozen too, and score prints the NET delta
  (perp + spot + staked) before and after. A perp short can be a hedge on spot holdings
  (0xf02d16a2 on Oct 29: perp -728k vs ~720k spot+staked = flat); its A-E letter describes
  the hedge only, the net line describes the real exposure.

Also reads the first baseline written by the heredoc version
(data/freeze_hype_cohort_2026-10-29.json: keys hype_mid / hype_orders, no coin key -> HYPE).
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.hyperliquid.xyz/info"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = DATA_DIR / "twap.db"


def post(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M")


def resolve(db, prefix):
    p = prefix.lower()
    if len(p) == 42:
        rows = db.execute("SELECT address FROM tape_addresses WHERE address = ?", (p,)).fetchall()
    else:
        hi = "0x" + format(int(p, 16) + 1, "x").rjust(len(p) - 2, "0")
        rows = db.execute("SELECT address FROM tape_addresses "
                          "WHERE address >= ? AND address < ?", (p, hi)).fetchall()
    return [r[0] for r in rows]


def coin_position(cs, coin):
    return next((x["position"] for x in cs["assetPositions"]
                 if x["position"]["coin"] == coin), None)


def holdings(addr, coin):
    """Spot balance + staked amount of the coin (staking only exists for HYPE).
    Returns (spot_total, staked, raw_spot_state, raw_staking)."""
    sp = post({"type": "spotClearinghouseState", "user": addr})
    spot = sum(float(b["total"]) for b in sp.get("balances", []) if b["coin"] == coin)
    stk, staked = None, 0.0
    if coin == "HYPE":
        stk = post({"type": "delegatorSummary", "user": addr})
        staked = (float(stk.get("delegated", 0)) + float(stk.get("undelegated", 0))
                  + float(stk.get("totalPendingWithdrawal", 0)))
    return spot, staked, sp, stk


def stored_holdings(w, coin):
    """Spot + staked from a frozen wallet entry; None if the file predates spot capture."""
    if "spotClearinghouseState" not in w:
        return None
    spot = sum(float(b["total"]) for b in w["spotClearinghouseState"].get("balances", [])
               if b["coin"] == coin)
    s = w.get("delegatorSummary") or {}
    staked = (float(s.get("delegated", 0)) + float(s.get("undelegated", 0))
              + float(s.get("totalPendingWithdrawal", 0)))
    return spot, staked


def freeze(a):
    db = sqlite3.connect(a.db)
    coin = a.coin
    now = datetime.now(timezone.utc)
    mid = float(post({"type": "allMids"})[coin])
    out = {"coin": coin, "snapshot_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mid": mid, "wallets": {}}
    print(f"freeze {out['snapshot_utc']}  {coin} mid {mid}\n")

    for p in a.prefixes:
        addrs = resolve(db, p)
        if len(addrs) != 1:
            print(f"{p}: {len(addrs)} matches, skipped\n")
            continue
        addr = addrs[0]
        cs = post({"type": "clearinghouseState", "user": addr})
        oo = [o for o in post({"type": "frontendOpenOrders", "user": addr}) if o["coin"] == coin]
        spot, staked, sp_raw, stk_raw = holdings(addr, coin)
        out["wallets"][addr] = {"clearinghouseState": cs, f"{coin.lower()}_orders": oo,
                                "spotClearinghouseState": sp_raw, "delegatorSummary": stk_raw}

        ms = cs["marginSummary"]
        eq, ntl = float(ms["accountValue"]), float(ms["totalNtlPos"])
        print(f"== {addr}")
        print(f"   equity {eq:,.0f}  ntl {ntl:,.0f}  eff_lev {ntl / eq if eq else 0:.1f}x")
        pos = coin_position(cs, coin)
        if pos:
            print(f"   {coin} szi {pos['szi']}  entry {pos['entryPx']}  liq {pos['liquidationPx']}  "
                  f"{pos['leverage']['type']} {pos['leverage']['value']}x  "
                  f"uPnL {float(pos['unrealizedPnl']):,.0f}")
        else:
            print(f"   {coin}: no position")
        perp = float(pos["szi"]) if pos else 0.0
        if spot or staked:
            print(f"   spot {spot:,.0f}  staked {staked:,.0f}  ->  NET {coin} delta "
                  f"{perp + spot + staked:,.0f}  (perp {perp:,.0f})")
        trig = [o for o in oo if o.get("isTrigger") or "Stop" in o["orderType"] or "Take" in o["orderType"]]
        print(f"   {coin} open orders: {len(oo)} ({len(trig)} trigger/TP-SL)")
        for o in trig:
            print(f"     {o['side']} sz={o['sz']} {o['orderType']} trig={o.get('triggerPx')} "
                  f"ro={o.get('reduceOnly')} placed={fmt(o['timestamp'])}")
        print()

    fn = Path(a.out_dir) / f"freeze_{coin}_cohort_{now.strftime('%Y-%m-%dT%H%MZ')}.json"
    fn.write_text(json.dumps(out, indent=1))
    print("saved", fn)


def score(a):
    data = json.loads(Path(a.file).read_text())
    coin = data.get("coin", "HYPE")
    base_mid = data.get("mid", data.get("hype_mid"))
    mid = float(post({"type": "allMids"})[coin])
    print(f"baseline {data['snapshot_utc']}  {coin} {base_mid}  ->  now "
          f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}  {coin} {mid}\n")

    for addr, w in data["wallets"].items():
        bp = coin_position(w["clearinghouseState"], coin)
        base = float(bp["szi"]) if bp else 0.0
        cs = post({"type": "clearinghouseState", "user": addr})
        np_ = coin_position(cs, coin)
        cur = float(np_["szi"]) if np_ else 0.0

        if abs(base) < 1e-9:
            verdict = f"flat at baseline -> re-entered: {'YES' if abs(cur) >= a.reentry else 'no'}"
        elif cur * base < 0:
            verdict = "E flipped"
        else:
            r = cur / base
            if r < 0.10:
                verdict = "B closed  (check tape for forced/stop exit)"
            elif r < 0.75:
                verdict = f"C reduced {r:.0%}  (check tape for forced/stop exit)"
            elif r <= 1.25:
                verdict = f"D held {r:.0%}"
            else:
                verdict = f"E added {r:.0%}"

        print(f"== {addr}")
        print(f"   szi {base:,.0f} -> {cur:,.0f}   {verdict}")
        bh = stored_holdings(w, coin)
        if bh is not None:
            spot_now, staked_now, _, _ = holdings(addr, coin)
            nb, nn = base + bh[0] + bh[1], cur + spot_now + staked_now
            if bh[0] or bh[1] or spot_now or staked_now:
                print(f"   NET delta (perp+spot+staked) {nb:,.0f} -> {nn:,.0f}")
        if np_ and abs(cur) > 0:
            liq = np_["liquidationPx"]
            oo = [o for o in post({"type": "frontendOpenOrders", "user": addr})
                  if o["coin"] == coin and "Stop" in o["orderType"]]
            if liq is None:
                safe = bool(oo)
            elif cur > 0:
                safe = any(float(o["triggerPx"]) > float(liq) for o in oo)
            else:
                safe = any(float(o["triggerPx"]) < float(liq) for o in oo)
            print(f"   liq {liq}   stops: {len(oo)}   stop on safe side of liq: {'YES' if safe else 'no'}")
        print()


def main():
    ap = argparse.ArgumentParser(description="Freeze and score a wallet cohort for one coin.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("freeze")
    f.add_argument("prefixes", nargs="+", help="addresses or hex prefixes")
    f.add_argument("--coin", default="HYPE")
    f.add_argument("--db", default=str(DEFAULT_DB))
    f.add_argument("--out-dir", default=str(DATA_DIR))

    s = sub.add_parser("score")
    s.add_argument("file", help="freeze JSON written by 'freeze'")
    s.add_argument("--reentry", type=float, default=10000.0,
                   help="|szi| threshold for 're-entered' on wallets flat at baseline")

    a = ap.parse_args()
    if a.cmd == "freeze":
        freeze(a)
    else:
        score(a)


if __name__ == "__main__":
    sys.exit(main())