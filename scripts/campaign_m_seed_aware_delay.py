"""Campaign M phase 8 -- seed-aware retro delay selection.

Optimising the retro delay against seed 16000 alone puts the stage at the bottom
of that seed's speed-vs-delay curve, which is NOT where the curve is robust:

    candidate            seed-16000 score   sustainer pass   joint pass
    K (shipped delays)          602,553        50/100          49/100
    MS (seed-16000 optimal)     607,708        24/100          21/100
    M  (sep 30, s16k optimal)   635,887        15/100          14/100

So the objective here is the fraction of seeds in which the stage lands under
5 m/s, evaluated over a seed panel, scanned across the delay grid.  The other
branch is parked; after separation the two branches are independent.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/campaign_m_seed_aware_delay.py \
      <sep> <stage> <centre> <half_width_ms> <step_ms> [n_seeds] [tag]
"""
import copy
import json
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork
from scripts.osifog_direct_driver import BASE as DRIVER_BASE
from scripts.certify_100_seeds import SEEDS

PARKED = 1100.0
LEGAL = 5.0
K = json.load(open("designs/osifog_visuals/candidate_K_celestial_datum_v7.json",
                   encoding="utf-8"))

SEP = float(sys.argv[1])
STAGE = sys.argv[2]
CENTRE = float(sys.argv[3])
HALF = float(sys.argv[4]) / 1000.0
STEP = float(sys.argv[5]) / 1000.0
NSEED = int(sys.argv[6]) if len(sys.argv) > 6 else 15
TAG = sys.argv[7] if len(sys.argv) > 7 else ("%s_sep%g" % (STAGE, SEP))
PANEL = SEEDS[:NSEED]
OUT = "OSIFOG/experiments-2026-07-26/m8_%s.json" % TAG


def build(delay):
    p = copy.deepcopy(DRIVER_BASE)
    p.update(K)
    p.pop("livery", None)
    p.pop("livery_decals", None)
    p["s1_separation_delay"] = SEP
    p["s0_retro_delay"] = PARKED
    p["s1_retro_delay"] = PARKED
    p[STAGE + "_retro_delay"] = round(delay, 7)
    return generate_ork(p)


def main():
    init_or()
    logf = open(OUT.replace(".json", ".log"), "w", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        logf.write(msg + "\n")
        logf.flush()

    log("== seed-aware %s delay scan, sep=%g, panel=%d seeds ==" % (STAGE, SEP, NSEED))
    log("   centre=%.7f  +/-%.1f ms  step=%.1f ms" % (CENTRE, HALF * 1000, STEP * 1000))

    rows = []
    t0 = time.time()
    n = int(round(2 * HALF / STEP)) + 1
    for i in range(n):
        d = CENTRE - HALF + i * STEP
        xml = build(d)
        speeds = []
        for seed in PANEL:
            try:
                m = run_sim(xml, seed=seed)
                speeds.append(m[STAGE + "_landing_speed"])
            except Exception:
                speeds.append(float("inf"))
        npass = sum(1 for v in speeds if v < LEGAL)
        finite = [v for v in speeds if v != float("inf")]
        row = {"delay": round(d, 7), "pass": npass, "n": len(PANEL),
               "median": sorted(finite)[len(finite) // 2] if finite else None,
               "worst": max(finite) if finite else None,
               "speeds": [round(v, 4) for v in speeds]}
        rows.append(row)
        log("   d=%.7f  pass %2d/%d  median %7.3f  worst %8.3f"
            % (d, npass, len(PANEL), row["median"], row["worst"]))
        json.dump({"sep": SEP, "stage": STAGE, "panel": PANEL, "rows": rows},
                  open(OUT, "w"), indent=1)

    best = max(rows, key=lambda r: (r["pass"], -(r["median"] or 9e9)))
    log("\n[%.0fs] BEST %s delay %.7f -> %d/%d seeds legal (median %.3f m/s)"
        % (time.time() - t0, STAGE, best["delay"], best["pass"], len(PANEL), best["median"]))
    top = [r for r in rows if r["pass"] == best["pass"]]
    log("   plateau: %.7f .. %.7f  (%.1f ms wide, %d grid points)"
        % (min(r["delay"] for r in top), max(r["delay"] for r in top),
           (max(r["delay"] for r in top) - min(r["delay"] for r in top)) * 1000, len(top)))
    log("wrote " + OUT)


main()
