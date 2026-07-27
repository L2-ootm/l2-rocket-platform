"""Campaign M phase 6 -- locate the separation time where the Sustainer's legal
retro-ignition window collapses.

Constraint from Davi: the delivered candidate must retain a usable ignition
window, not a single legal sample.  Late separation buys score by collapsing the
mean-touchdown-position penalty but narrows the Sustainer window:

    sep 23.1  floor 0.912 m/s   window  12 ms
    sep 37.5  floor 4.533 m/s   window   0 ms   (1 sample at 0.16 ms)
    sep 40.5  floor 4.479 m/s   window   0 ms

The Booster window is not the constraint (51-77 ms), so only s0 is profiled here.

Usage: venv/Scripts/python.exe -X utf8 scripts/campaign_m_window_knee.py <sep> [sep ...]
"""
import copy
import json
import math
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork
from scripts.osifog_direct_driver import BASE as DRIVER_BASE
from rocket_forge import MOTOR_DATABASE

G = 9.80665
PARKED = 1100.0
SEED = 16000
LEGAL = 5.0
CATALOG = json.load(open("OSIFOG/experiments-2026-07-25/or_motor_catalog.json",
                         encoding="utf-8"))
_BY_DIGEST = {m["digest"]: m for m in CATALOG}
K = json.load(open("designs/osifog_visuals/candidate_K_celestial_datum_v7.json",
                   encoding="utf-8"))


def sample(sep, delay):
    p = copy.deepcopy(DRIVER_BASE)
    p.update(K)
    p.pop("livery", None)
    p.pop("livery_decals", None)
    p["s1_separation_delay"] = sep
    p["s1_retro_delay"] = PARKED
    p["s0_retro_delay"] = round(delay, 7)
    m = run_sim(generate_ork(p), seed=SEED)
    for s in m.get("stage_landings", []):
        if s["stage_key"] == "s0":
            return {"delay": round(delay, 7), "v": s["total_speed"],
                    "E": s["east_m"], "N": s["north_m"],
                    "vz": s["vz_ms"], "vxy": s["vxy_ms"],
                    "theta": s["orientation_theta_deg"]}
    return None


def freefall(sep):
    p = copy.deepcopy(DRIVER_BASE)
    p.update(K)
    p.pop("livery", None)
    p.pop("livery_decals", None)
    p["s1_separation_delay"] = sep
    p["s0_retro_delay"] = PARKED
    p["s1_retro_delay"] = PARKED
    m = run_sim(generate_ork(p), seed=SEED)
    for s in m.get("stage_landings", []):
        if s["stage_key"] == "s0":
            return s, m
    return None, m


def main():
    seps = [float(a) for a in sys.argv[1:]]
    init_or()
    out = []
    for sep in seps:
        t0 = time.time()
        ld, m = freefall(sep)
        thrust = _BY_DIGEST[MOTOR_DATABASE[K["s0_retro"]][5]]["avg_thrust_n"]
        ratio = thrust / (ld["mass_kg"] * G)
        centre = ld["time_s"] - (ld["total_speed"] / (2 * G)) * math.log(ratio / (ratio - 1))

        best, span, step = None, 2.5, 0.5
        for _ in range(6):
            d = centre - span
            while d <= centre + span + 1e-9:
                s = sample(sep, d)
                if s and (best is None or s["v"] < best["v"]):
                    best = s
                d += step
            centre = best["delay"]
            span, step = step * 1.2, step / 5.0
        floor = best["v"]

        # window at 1 ms around the floor
        got = {best["delay"]: best} if floor < LEGAL else {}
        if floor < LEGAL:
            for direction in (-1, 1):
                for i in range(120):
                    s = sample(sep, best["delay"] + direction * 0.001 * (i + 1))
                    if s is None or s["v"] >= LEGAL:
                        break
                    got[s["delay"]] = s
        width = (max(got) - min(got)) * 1000 if len(got) > 1 else 0.0
        row = {"sep": sep, "floor": floor, "delay": best["delay"],
               "window_ms": width, "n_legal": len(got),
               "vz": best["vz"], "vxy": best["vxy"], "theta": best["theta"],
               "lo": min(got) if got else None, "hi": max(got) if got else None}
        out.append(row)
        print("  sep=%5.1f floor=%7.3f window=%6.1f ms (n=%3d) vz=%7.3f vxy=%7.3f "
              "theta=%6.1f  [%.0fs]"
              % (sep, floor, width, len(got), best["vz"], best["vxy"],
                 best["theta"], time.time() - t0), flush=True)
        json.dump(out, open("OSIFOG/experiments-2026-07-26/m6_window_knee_%s.json"
                            % "_".join("%g" % s for s in seps), "w"), indent=1)


main()
