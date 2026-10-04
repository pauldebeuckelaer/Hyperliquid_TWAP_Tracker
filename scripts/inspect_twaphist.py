import json, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

data = json.load(open(sys.argv[1]))
by_id = defaultdict(list)
for e in data:
    by_id[e["twapId"]].append(e)
for v in by_id.values():
    v.sort(key=lambda x: x["time"])

print("distinct twapIds:", len(by_id))
print("entries per twapId:", dict(Counter(len(v) for v in by_id.values())))

print("\nstatus sequences per twapId:")
seqs = Counter(" -> ".join(x["status"]["status"] for x in v) for v in by_id.values())
for s, n in seqs.most_common():
    print(f"  {n:4d}  {s}")

print("\nstill open (last event = activated):")
for tid, v in by_id.items():
    if v[-1]["status"]["status"] == "activated":
        s = v[-1]["state"]
        print(f"  {tid} {s['coin']} {s['side']} sz={s['sz']} min={s['minutes']} "
              f"placed={iso(s['timestamp'])} exec={s['executedSz']} "
              f"trigger={s['trigger']} stopPx={s['stopPx']}")

print("\nerror descriptions:")
for d, n in Counter(x["status"].get("description") for x in data
                    if x["status"]["status"] == "error").most_common():
    print(f"  {n:4d}  {d}")

print("\ntrigger values:", Counter(json.dumps(x["state"]["trigger"]) for x in data).most_common(5))
print("stopPx values:", Counter(json.dumps(x["state"]["stopPx"]) for x in data).most_common(5))

seen = set()
for x in data:
    st = x["status"]["status"]
    if st not in seen:
        seen.add(st)
        print(f"\n--- example: {st}  (time={iso(x['time'])}) ---")
        print(json.dumps(x, indent=2))
