"""Certify a submission params file across 100 wind/integrator seeds.

The reviewer reruns the delivered .ork; what decides whether the result holds is
the fraction of wind realisations in which BOTH stages land under 5 m/s, not the
depth of any single seed. This reports that fraction directly, per stage and
jointly, plus the official score distribution over the seeds where the flight is
legal.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/certify_100_seeds.py <params.json> [tag]
"""
import copy
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork, score_official

LEGAL = 5.0
# 100 seeds: the 40 used for Candidate J's certification, then a deterministic
# spread so the set is reproducible and not cherry-picked.
SEEDS = ([16000, 7, 12345, 101, 202, 303, 404, 505, 606, 707, 808, 909,
          1111, 2222, 3333, 4444, 5555, 6666, 7777, 8888, 9999, 1234,
          4321, 2468, 1357, 8642, 9753, 1470, 2581, 3692, 13, 27, 42,
          55, 68, 71, 84, 97, 110, 123]
         + [1000 * i + 17 for i in range(1, 31)]
         + [77 * i + 5 for i in range(1, 31)])


def main():
    params_path = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(params_path)
    from scripts.osifog_direct_driver import BASE as DRIVER_BASE
    p = copy.deepcopy(DRIVER_BASE)
    p.update(json.load(open(params_path, encoding="utf-8")))

    init_or()
    xml = generate_ork(p)
    rows = []
    for n, seed in enumerate(SEEDS, 1):
        try:
            m = run_sim(xml, seed=seed)
        except Exception as exc:
            print("  seed %-6d SIM-FAIL %s" % (seed, str(exc)[:70]), flush=True)
            continue
        s0 = m["s0_landing_speed"]
        s1 = m["s1_landing_speed"]
        try:
            sc = score_official(m, p)["raw_score"]
        except Exception:
            sc = float("nan")
        rows.append({"seed": seed, "s0": s0, "s1": s1, "apogee": m["apogee_m"],
                     "mach": m["mach"], "score": sc})
        if n % 10 == 0:
            j = sum(1 for r in rows if r["s0"] < LEGAL and r["s1"] < LEGAL)
            print("  %3d/%3d  joint %d/%d" % (n, len(SEEDS), j, len(rows)), flush=True)

    n = len(rows)
    a = sum(1 for r in rows if r["s0"] < LEGAL)
    b = sum(1 for r in rows if r["s1"] < LEGAL)
    j = sum(1 for r in rows if r["s0"] < LEGAL and r["s1"] < LEGAL)
    legal_scores = sorted(r["score"] for r in rows
                          if r["s0"] < LEGAL and r["s1"] < LEGAL)
    print("\n=== %s over %d seeds ===" % (tag, n))
    print("  Sustainer legal : %3d/%d  (%.0f%%)" % (a, n, 100.0 * a / n))
    print("  Booster   legal : %3d/%d  (%.0f%%)" % (b, n, 100.0 * b / n))
    print("  BOTH      legal : %3d/%d  (%.0f%%)" % (j, n, 100.0 * j / n))
    if legal_scores:
        mid = legal_scores[len(legal_scores) // 2]
        print("  score over legal seeds: min %.0f  median %.0f  max %.0f"
              % (legal_scores[0], mid, legal_scores[-1]))
    ap = [r["apogee"] for r in rows]
    print("  apogee range: %.1f .. %.1f m   max Mach %.3f"
          % (min(ap), max(ap), max(r["mach"] for r in rows)))

    dst = "OSIFOG/experiments-2026-07-25/l_twoburn/cert100_%s.json" % (
        tag.replace(".json", "").replace("/", "_"))
    json.dump({"tag": tag, "params": params_path, "n": n,
               "sustainer_pass": a, "booster_pass": b, "joint_pass": j,
               "rows": rows}, open(dst, "w"), indent=1)
    print("wrote", dst)


if __name__ == "__main__":
    main()
