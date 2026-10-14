#!/usr/bin/env python3
"""Try exit and entry variants of the HYPE strategy on the replay, fit half only.

Reads the files replay_hype.py saved (_signals.csv and _paths.csv) and re-runs
the bot's position logic for each variant: one HYPE position at a time,
exits checked on every price tick in the order TP, SL, max hold, and a
cooldown after TIME exits only. Entry price is the candle close, as the bot
recorded it. Fees are not in the gross numbers; avg_net subtracts --fee.

Variants:
  --tp / --sl      comma lists, every combination is run (a grid)
  --hold           max hold in minutes (<= 360; the paths stop at 420)
  --type           all | CONTRA | BASELINE
  --flow-max X     only entries with capped flow < X  (e.g. -2000)
  --flow-min X     only entries with capped flow >= X

DISCIPLINE. The fit half is the validated box era, Jun 7 - Sep 13 2026.
Anything on or after Sep 14 is the held-out half: this script refuses to read
it unless --holdout is given. Explore here, write the hypothesis down and
commit it, then run the held-out half ONCE. Picking the best row of a grid
flatters the fit half; that is exactly what the held-out run is for.

Self-check: with the bot's own settings (TP 1.5, SL 2.0, hold 360, all
entries) the result must equal replay_hype.py's own _trades.csv for the
window. It prints whether it does.

Run from ~/bots/Hyperliquid_TWAP_Analyzer:
    python3 scripts/replay_exits.py                       # bot settings + self-check
    python3 scripts/replay_exits.py --tp 0.75,1.0,1.25,1.5 --sl 1.0,1.5,2.0
    python3 scripts/replay_exits.py --type CONTRA --flow-max -2000 --detail
    python3 scripts/replay_exits.py --prefix data/replay_hype_nq     # a --no-quirk replay
"""

import argparse
import sys

import numpy as np
import pandas as pd

FIT_START = "2026-06-07"
HOLDOUT_START = pd.Timestamp("2026-09-14", tz="UTC")
BOT = {"tp": 1.5, "sl": 2.0, "hold": 360, "cooldown": 30}


def to_utc(s):
    try:
        return pd.to_datetime(s, utc=True, format="ISO8601")
    except (TypeError, ValueError):
        return pd.to_datetime(s, utc=True)


def load(prefix, start, end):
    sig = pd.read_csv(f"{prefix}_signals.csv")
    sig["T"] = to_utc(sig["T"])
    sig = sig[(sig["T"] >= start) & (sig["T"] < end)]
    ent = sig[sig["entry"].astype(str) == "True"].sort_values("T").reset_index(drop=True)

    paths = pd.read_csv(f"{prefix}_paths.csv")
    paths["T"] = to_utc(paths["T"])
    paths = paths[(paths["T"] >= start) & (paths["T"] < end)]
    by_t = {T: (g["minute"].to_numpy(), g["ret_pct"].to_numpy())
            for T, g in paths.sort_values(["T", "minute"]).groupby("T")}
    return sig, ent, by_t


def simulate(ent, by_t, tp, sl, hold, cooldown, offset_sec):
    trades = []
    free_at = None
    for e in ent.itertuples(index=False):
        as_of = e.T + pd.Timedelta(seconds=offset_sec)
        if free_at is not None and as_of < free_at:
            continue
        m, r = by_t.get(e.T, (np.array([]), np.array([])))
        if len(m) == 0:
            continue
        tp_h, sl_h, th = r >= tp, r <= -sl, m >= hold
        hit = tp_h | sl_h | th
        if hit.any():
            k = int(np.argmax(hit))
            kind = "TP" if tp_h[k] else ("SL" if sl_h[k] else "TIME")
        else:
            k, kind = len(m) - 1, "NO_DATA"
        exit_time = (as_of + pd.Timedelta(minutes=float(m[k]))).round("s")
        free_at = exit_time + pd.Timedelta(minutes=cooldown) if kind == "TIME" else exit_time
        seg = r[: k + 1]
        trades.append({"T": e.T, "signal_type": e.signal_type, "flow": e.flow,
                       "exit_kind": kind, "hold_min": float(m[k]), "pnl_pct": float(r[k]),
                       "best_pct": max(0.0, float(seg.max())), "worst_pct": min(0.0, float(seg.min()))})
    return pd.DataFrame(trades)


def stats(t, fee):
    if t.empty:
        return {"n": 0}
    p = t["pnl_pct"]
    sd = p.std(ddof=1) if len(p) > 1 else np.nan
    k = t["exit_kind"].value_counts()
    return {"n": len(p), "win_pct": round(100 * (p > 0).mean()),
            "avg_pct": round(p.mean(), 3), "avg_net": round(p.mean() - fee, 3),
            "sum_pct": round(p.sum(), 2), "t": round(p.mean() / sd * np.sqrt(len(p)), 2) if sd and sd > 0 else np.nan,
            "TP": int(k.get("TP", 0)), "SL": int(k.get("SL", 0)), "TIME": int(k.get("TIME", 0)),
            "NO_DATA": int(k.get("NO_DATA", 0))}


def show(df):
    with pd.option_context("display.max_rows", 500, "display.width", 220, "display.max_columns", 30):
        print(df.to_string(index=False))


def self_check(prefix, trades, start, end):
    try:
        ref = pd.read_csv(f"{prefix}_trades.csv")
    except FileNotFoundError:
        print("Self-check skipped: no _trades.csv")
        return
    ref["T"] = to_utc(ref["T"])
    ref = ref[(ref["T"] >= start) & (ref["T"] < end)]
    a = trades.set_index("T")["pnl_pct"]
    b = ref.set_index("T")["pnl_pct"]
    same_t = a.index.equals(b.index)
    diff = (a - b).abs().max() if same_t else np.nan
    kinds = (trades["exit_kind"].to_numpy() == ref["exit_kind"].to_numpy()).all() if same_t else False
    ok = same_t and kinds and diff < 0.002
    print(f"Self-check vs replay_hype trades: {'OK' if ok else 'MISMATCH'} "
          f"({len(trades)} vs {len(ref)} trades, same entries: {same_t}, same exits: {kinds}, "
          f"max pnl diff: {diff if diff == diff else 'n/a'})")
    if not ok:
        print("  If the replay was run before paths went to 420 min, rerun replay_hype.py first.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", default="data/replay_hype")
    ap.add_argument("--start", default=FIT_START)
    ap.add_argument("--end", default=HOLDOUT_START.strftime("%Y-%m-%d"), help="exclusive")
    ap.add_argument("--holdout", action="store_true", help="allow dates from Sep 14 2026 on (run once!)")
    ap.add_argument("--tp", default=str(BOT["tp"]))
    ap.add_argument("--sl", default=str(BOT["sl"]))
    ap.add_argument("--hold", type=float, default=BOT["hold"])
    ap.add_argument("--cooldown", type=float, default=BOT["cooldown"])
    ap.add_argument("--type", default="all", choices=["all", "CONTRA", "BASELINE"])
    ap.add_argument("--flow-max", type=float, default=None)
    ap.add_argument("--flow-min", type=float, default=None)
    ap.add_argument("--fee", type=float, default=0.06, help="round-trip fee in %% for avg_net (default 0.06)")
    ap.add_argument("--offset-sec", type=int, default=20, help="same as replay_hype.py")
    ap.add_argument("--detail", action="store_true", help="per month / type / exit for each variant")
    a = ap.parse_args()

    start, end = pd.Timestamp(a.start, tz="UTC"), pd.Timestamp(a.end, tz="UTC")
    if end > HOLDOUT_START and not a.holdout:
        sys.exit(f"Refusing: the window reaches into the held-out half (from {HOLDOUT_START:%Y-%m-%d}). "
                 f"Freeze the hypothesis first, then run once with --holdout.")
    if a.hold > 360:
        sys.exit("--hold above 360 is not supported (paths stop at 420 min).")
    if a.holdout:
        print("=" * 70 + "\nHELD-OUT RUN. One shot: the result stands, whatever it says.\n" + "=" * 70)

    sig, ent, by_t = load(a.prefix, start, end)
    ent_all = ent
    if a.type != "all":
        ent = ent[ent["signal_type"] == a.type]
    if a.flow_max is not None:
        ent = ent[ent["flow"] < a.flow_max]
    if a.flow_min is not None:
        ent = ent[ent["flow"] >= a.flow_min]
    print(f"Window {start:%Y-%m-%d} to {end:%Y-%m-%d} | candle closes {len(sig)} | entry signals "
          f"{len(ent_all)} (CONTRA {int((ent_all['signal_type'] == 'CONTRA').sum())}, "
          f"BASELINE {int((ent_all['signal_type'] == 'BASELINE').sum())}) | after filter {len(ent)}")
    missing = sum(1 for T in ent["T"] if T not in by_t)
    if missing:
        print(f"Note: {missing} entry signals have no price path and are skipped.")

    rows, details = [], []
    for tp in [float(x) for x in a.tp.split(",")]:
        for sl in [float(x) for x in a.sl.split(",")]:
            tr = simulate(ent, by_t, tp, sl, a.hold, a.cooldown, a.offset_sec)
            rows.append({"tp": tp, "sl": sl, "hold": a.hold, **stats(tr, a.fee)})
            details.append((tp, sl, tr))

    print(f"\nVariants (type={a.type}, flow_max={a.flow_max}, flow_min={a.flow_min}, fee {a.fee}% round trip):")
    show(pd.DataFrame(rows))

    is_bot = (a.type == "all" and a.flow_max is None and a.flow_min is None and a.hold == BOT["hold"]
              and a.cooldown == BOT["cooldown"])
    for tp, sl, tr in details:
        if is_bot and tp == BOT["tp"] and sl == BOT["sl"]:
            print()
            self_check(a.prefix, tr, start, end)
        if a.detail and not tr.empty:
            print(f"\n-- TP {tp} / SL {sl} --")
            tr = tr.assign(month=tr["T"].dt.strftime("%Y-%m"))
            for col in ("month", "signal_type", "exit_kind"):
                show(pd.DataFrame([{col: key, **stats(g, a.fee)} for key, g in tr.groupby(col)]))
                print()


if __name__ == "__main__":
    main()