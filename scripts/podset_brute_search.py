#!/usr/bin/env python3
"""Quick brute-force tuning pass for the new PodSet external-3+1 vehicle
(osifog_podset.py). Pods are fixed non-separating (native PodSet -- verified
no separation semantics exist), each pod has its own nose + fins; this
script only searches ascent motor choice and core/pod/fin dimensions to
find a subsonic, near-3000m-apogee, stability-legal candidate. Retro motors
stay disabled (delay=200s) -- this is an ascent-legality search only, not a
landing search.
"""
import itertools
import json
import math
import os
import sys

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from osifog_sweep import init_or, SIM_SEED, run_sim, save_simulated_ork
from osifog_podset import generate_podset_ork

ARTIFACTS = "artifacts/podset"
os.makedirs(ARTIFACTS, exist_ok=True)

WIND_LEVELS = [
    (0, 3.0, 270.0, 0.5), (500, 6.0, 270.0, 1.0), (1000, 9.0, 270.0, 1.5),
    (2000, 12.0, 270.0, 2.0), (3000, 15.0, 270.0, 2.5),
]

# (motor_index, designation) -- G/H class per pod (3 pods cluster their
# thrust, so per-pod impulse needs to be much smaller than a traditional
# single-motor design of similar target apogee).
ASCENT_CANDIDATES = [(2, "G71R"), (4, "G80T"), (6, "H128W"), (7, "H180W")]
RETRO_IDX = 19  # K550W, disabled (delay=200) for this ascent-only pass

BASE = dict(
    s0_retro=RETRO_IDX, s0_retro_delay=200.0,
    s1_retro=RETRO_IDX, s1_retro_delay=200.0, s1_separation_delay=0.5,
    nose_mass_kg=0.15,
    launch_azimuth=270.0, launch_angle_deg=3.0,
    wind_levels=WIND_LEVELS,
)


def build_params(main_idx, core_radius, core_length, pod_radius, pod_length,
                 pod_offset, core_fin_root, core_fin_height,
                 pod_fin_root, pod_fin_height):
    p = dict(BASE)
    for prefix in ("s0", "s1"):
        p[f"{prefix}_main"] = main_idx
        p[f"{prefix}_core_radius"] = core_radius
        p[f"{prefix}_core_length"] = core_length
        p[f"{prefix}_pod_radius"] = pod_radius
        p[f"{prefix}_pod_length"] = pod_length
        p[f"{prefix}_pod_radial_offset"] = pod_offset
        p[f"{prefix}_core_fin_count"] = 4
        p[f"{prefix}_core_fin_root"] = core_fin_root
        p[f"{prefix}_core_fin_height"] = core_fin_height
        p[f"{prefix}_pod_fin_count"] = 3
        p[f"{prefix}_pod_fin_root"] = pod_fin_root
        p[f"{prefix}_pod_fin_height"] = pod_fin_height
        p[f"{prefix}_pod_nose_shape"] = "ogive"
    return p


def score(m):
    mach = m.get("mach", 999.0)
    apogee = m.get("apogee_m", 0.0)
    segments = m.get("ascent_stability_segments", [])
    margins = [s["min_calibers"] for s in segments if s.get("min_calibers") is not None]
    min_margin = min(margins) if margins else -999.0
    legal = mach < 1.0 and min_margin >= 1.5
    apogee_penalty = abs(apogee - 3000.0)
    return legal, apogee_penalty, mach, min_margin, apogee


def main():
    init_or()
    from osifog_sweep import MOTOR_DATABASE

    results = []
    for (main_idx, main_name) in ASCENT_CANDIDATES:
        motor_len = MOTOR_DATABASE[main_idx][3]
        motor_diam = MOTOR_DATABASE[main_idx][2]
        pod_radius = max(0.02, motor_diam / 2.0 + 0.0035)
        pod_length = motor_len + 0.05
        for scale in (0.8, 1.0, 1.3, 1.6):
            core_radius = 0.04 * scale
            core_length = max(0.5, pod_length * 0.9)
            pod_offset = core_radius + pod_radius + 0.01
            core_fin_root = max(0.12, core_radius * 4.0)
            core_fin_height = max(0.06, core_radius * 2.0)
            pod_fin_root = pod_length * 0.15
            pod_fin_height = pod_radius * 2.0
            label = f"{main_name}_scale{scale}"
            try:
                p = build_params(
                    main_idx, core_radius, core_length, pod_radius, pod_length,
                    pod_offset, core_fin_root, core_fin_height,
                    pod_fin_root, pod_fin_height,
                )
                ork_xml = generate_podset_ork(p)
                m = run_sim(ork_xml, seed=SIM_SEED)
                legal, penalty, mach, margin, apogee = score(m)
                print(f"{label}: mach={mach:.3f} margin={margin:.3f} apogee={apogee:.1f} legal={legal}",
                      file=sys.stderr)
                results.append({
                    "label": label, "params": p, "mach": mach, "min_margin_cal": margin,
                    "apogee_m": apogee, "legal": legal, "apogee_penalty": penalty,
                })
            except ValueError as exc:
                print(f"{label}: REJECTED_GEOMETRY {exc}", file=sys.stderr)
                results.append({"label": label, "status": "REJECTED_GEOMETRY", "error": str(exc)})

    legal_results = [r for r in results if r.get("legal")]
    legal_results.sort(key=lambda r: r["apogee_penalty"])

    with open(os.path.join(ARTIFACTS, "brute-search-results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)

    if legal_results:
        best = legal_results[0]
        print(f"\nBEST: {best['label']} mach={best['mach']:.3f} margin={best['min_margin_cal']:.3f} "
              f"apogee={best['apogee_m']:.1f}", file=sys.stderr)
        ork_xml = generate_podset_ork(best["params"])
        out_path = "designs/osifog_level3/octaweb_experiment/podset_best_candidate.ork"
        save_simulated_ork(ork_xml, out_path, seed=SIM_SEED)
        print(f"Wrote {out_path}", file=sys.stderr)
        print(json.dumps({"best": best["label"], "path": out_path}, indent=2))
    else:
        print("\nNo legal (Mach<1, margin>=1.5) candidate found in this quick sweep.", file=sys.stderr)
        print(json.dumps({"best": None}, indent=2))


if __name__ == "__main__":
    main()
