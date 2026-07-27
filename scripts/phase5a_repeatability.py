#!/usr/bin/env python3
"""Fresh-process repeatability check for selected booster-basin delays."""
import json
import subprocess
import sys

DELAYS = [float(x) for x in sys.argv[1:]] or [29.860, 29.8645, 29.865, 29.8665, 29.864]

results = []
for d in DELAYS:
    proc = subprocess.run(
        [sys.executable, "scripts/phase5a_booster_basin.py", "repeat", str(d)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        results.append({"delay_s": d, "error": proc.stderr[-2000:]})
        continue
    try:
        row = json.loads(proc.stdout)
    except json.JSONDecodeError:
        results.append({"delay_s": d, "error": "unparseable stdout", "stdout": proc.stdout[-1000:]})
        continue
    results.append(row)
    print(f"delay={d}: touchdown_total={row.get('touchdown_total_mps')}", file=sys.stderr)

with open("artifacts/autoevo/phase5a/booster-fresh-process-repeatability.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, sort_keys=True, default=str)
print(json.dumps(results, default=str))
