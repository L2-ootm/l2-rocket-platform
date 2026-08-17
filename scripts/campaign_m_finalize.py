"""Campaign M phase 5 -- full-score optimisation at one separation time.

After separation the two branches are physically independent, so each stage's
(east, north, total_speed) can be profiled against its OWN retro delay with the
other parked, and the joint official score computed analytically over the
product grid.  That is O(n+m) simulations instead of O(n*m).

The official score (rule PDF sec 3) is

    900000 - 3000(apo-3000)^2 - 16(apoE^2 + apoN^2)
           - 2((E0+E1)/2)^2 - 2((N0+N1)/2)^2
           - 500((V0+V1)/2)^2 - 7500 m_prop

so touchdown position and speed BOTH move with the delays; optimising landing
speed alone is the wrong objective.

Usage: venv/Scripts/python.exe -X utf8 scripts/campaign_m_finalize.py <sep> [tag]
"""
import copy
import json
import math
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork, score_official
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

SEP = float(sys.argv[1])
TAG = sys.argv[2] if len(sys.argv) > 2 else ("sep%g" % SEP)
OUT = "OSIFOG/experiments-2026-07-26/m5_%s.json" % TAG


def base():
    p = copy.deepcopy(DRIVER_BASE)
    p.update(K)
    p.pop("livery", None)
    p.pop("livery_decals", None)
    p["s1_separation_delay"] = SEP
    return p


def land(m, key):
    for s in m.get("stage_landings", []):
        if s["stage_key"] == key:
            return s
    return None


def sample(stage, delay):
    """One stage's landing state at one delay, with the other branch parked."""
    p = base()
    p["s0_retro_delay"] = PARKED
    p["s1_retro_delay"] = PARKED
    p[stage + "_retro_delay"] = round(delay, 7)
    m = run_sim(generate_ork(p), seed=SEED)
    ld = land(m, stage)
    if ld is None:
        return None
    return {
        "delay": round(delay, 7), "v": ld["total_speed"],
        "E": ld["east_m"], "N": ld["north_m"],
        "theta": ld["orientation_theta_deg"], "t": ld["time_s"],
        "apo": m["apogee_m"], "apoE": m["apogee_east_m"], "apoN": m["apogee_north_m"],
        "mach": m["mach"], "mprop": m.get("m_prop_kg_actual", 0.0),
    }


def descend(stage, centre, log):
    """6-round grid descent, 500 ms down to 0.16 ms."""
    best = None
    span, step = 2.5, 0.5
    for _ in range(6):
        d = centre - span
        while d <= centre + span + 1e-9:
            s = sample(stage, d)
            if s is not None and (best is None or s["v"] < best["v"]):
                best = s
            d += step
        if best is None:
            return None
        log("      %s step=%8.3fms best=%8.3f @ %s"
            % (stage, step * 1000, best["v"], best["delay"]))
        if best["v"] > 60.0:
            return best
        centre = best["delay"]
        span, step = step * 1.2, step / 5.0
    return best


def profile(stage, centre, step, log):
    """Every legal sample walking outward from a legal centre."""
    got = {}
    s0 = sample(stage, centre)
    if s0 is not None and s0["v"] < LEGAL:
        got[s0["delay"]] = s0
    for direction in (-1, 1):
        for i in range(200):
            s = sample(stage, centre + direction * step * (i + 1))
            if s is None or s["v"] >= LEGAL:
                break
            got[s["delay"]] = s
    width = (max(got) - min(got)) * 1000 if got else 0.0
    log("      %s legal samples=%d width=%.1f ms" % (stage, len(got), width))
    return sorted(got.values(), key=lambda s: s["delay"])


def main():
    init_or()
    logf = open(OUT.replace(".json", ".log"), "w", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        logf.write(msg + "\n")
        logf.flush()

    t0 = time.time()
    log("== Campaign M finalize, separation %g s ==" % SEP)

    p = base()
    p["s0_retro_delay"] = PARKED
    p["s1_retro_delay"] = PARKED
    ff = run_sim(generate_ork(p), seed=SEED)
    log("  freefall: apo=%.3f mach=%.4f apoEN=(%.2f,%.2f)"
        % (ff["apogee_m"], ff["mach"], ff["apogee_east_m"], ff["apogee_north_m"]))

    prof = {}
    for stage in ("s0", "s1"):
        ld = land(ff, stage)
        thrust = _BY_DIGEST[MOTOR_DATABASE[p[stage + "_retro"]][5]]["avg_thrust_n"]
        ratio = thrust / (ld["mass_kg"] * G)
        centre = ld["time_s"] - (ld["total_speed"] / (2 * G)) * math.log(ratio / (ratio - 1))
        log("  %s: ff vT=%.2f t=%.2f m=%.3f predicted ignition %.4f"
            % (stage, ld["total_speed"], ld["time_s"], ld["mass_kg"], centre))
        best = descend(stage, centre, log)
        floor = best["v"] if best is not None else float("nan")
        if best is None or floor >= LEGAL:
            log("  %s: FLOOR %.3f m/s -- NOT LANDABLE, abort" % (stage, floor))
            json.dump({"sep": SEP, "abort": stage, "floor": floor},
                      open(OUT, "w"), indent=1)
            return
        log("  %s: floor %.3f m/s @ %s" % (stage, floor, best["delay"]))
        prof[stage] = profile(stage, best["delay"], 0.001, log)

    apo, apoE, apoN = ff["apogee_m"], ff["apogee_east_m"], ff["apogee_north_m"]
    const = 900000.0 - 3000.0 * (apo - 3000.0) ** 2 - 16.0 * (apoE ** 2 + apoN ** 2)

    best = None
    for a in prof["s0"]:
        for b in prof["s1"]:
            mE = (a["E"] + b["E"]) / 2.0
            mN = (a["N"] + b["N"]) / 2.0
            mV = (a["v"] + b["v"]) / 2.0
            mprop = max(a["mprop"], b["mprop"])
            sc = const - 2.0 * (mE ** 2 + mN ** 2) - 500.0 * mV ** 2 - 7500.0 * mprop
            if best is None or sc > best[0]:
                best = (sc, a, b, mE, mN, mV)

    sc, a, b, mE, mN, mV = best
    log("\n  analytic best: score~%.0f  s0_delay=%s (%.3f m/s)  s1_delay=%s (%.3f m/s)"
        "  meanR=%.1f meanV=%.3f"
        % (sc, a["delay"], a["v"], b["delay"], b["v"], math.hypot(mE, mN), mV))

    q = base()
    q["s0_retro_delay"] = a["delay"]
    q["s1_retro_delay"] = b["delay"]
    m = run_sim(generate_ork(q), seed=SEED)
    real = score_official(m, q)
    log("  VERIFIED joint: raw_score=%.1f legal=%s v=(%.3f,%.3f) apo=%.3f mach=%.4f minSM=%.3f"
        % (real["raw_score"], real["is_legal"], m["s0_landing_speed"],
           m["s1_landing_speed"], m["apogee_m"], m["mach"], m.get("min_static_margin", float("nan"))))
    if real["violations"]:
        log("  violations: %s" % (real["violations"],))
    log("  decomposition: alt=%.0f apoH=%.0f pos=%.0f vel=%.0f prop=%.0f"
        % (real["apogee_alt_pen"], real["apogee_horiz_pen"], real["touch_pos_pen"],
           real["touch_vel_pen"], real["prop_pen"]))

    params = base()
    params["s0_retro_delay"] = a["delay"]
    params["s1_retro_delay"] = b["delay"]
    s0w = (max(x["delay"] for x in prof["s0"]) - min(x["delay"] for x in prof["s0"])) * 1000
    s1w = (max(x["delay"] for x in prof["s1"]) - min(x["delay"] for x in prof["s1"])) * 1000
    json.dump({
        "sep": SEP, "analytic_score": sc, "verified": real,
        "s0_delay": a["delay"], "s1_delay": b["delay"],
        "s0_window_ms": s0w, "s1_window_ms": s1w,
        "s0_profile": prof["s0"], "s1_profile": prof["s1"],
        "params": {k: v for k, v in params.items() if k != "wind_levels"},
    }, open(OUT, "w"), indent=1)
    log("[%.0fs] wrote %s" % (time.time() - t0, OUT))


main()
