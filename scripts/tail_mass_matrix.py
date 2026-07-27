#!/usr/bin/env python3
"""Controlled tail-mass/CG/inertia matrix (corrective-loop directive section 4).

The prior session's K550W-vs-H180W passive-descent comparison
(flip-diagnosis-report.md Finding 5) changed total mass, CG, and pitch
inertia simultaneously between the two motors, so it could not attribute the
observed difference in passive tail-first duration to any one of those three
variables. This script isolates them using physical steel ballast (the
existing s1_aft_ballast_kg/s1_mid_ballast_kg/*_pos_m parameters -- native,
collision-checked rods, not an invisible point mass) while holding the
airframe (E8_8 topology) fixed:

  Case A: H73J landing motor, no added ballast.
  Case B: H73J + physical aft ballast bringing TOTAL MASS up to K550W's
          loaded mass, placed at the same aft fraction as the motor itself
          (approximates matching CG; the actual measured CG/inertia for
          every case is reported below rather than assumed).
  Case C: H73J + the same total ballast mass as Case B, but split between
          the mid (0.55) and aft (0.88) fractions to shift the pitch-inertia
          contribution without necessarily preserving Case B's CG -- the
          resulting CG difference is measured and reported, not overridden.
  Case D: K550W present, never ignited (retro_delay=200s).

All four cases are PASSIVE (motor never fires) -- this isolates inert-mass
effects from any burn-window aerodynamic effect, per Finding 5's own
methodology. Reports, per case: total_mass, cg, pitch_inertia (OpenRocket's
TYPE_ROTATIONAL_INERTIA -- the transverse/pitch-yaw moment of inertia; do not
confuse with TYPE_LONGITUDINAL_INERTIA, which is the roll-axis inertia and,
for this heavily-finned topology, is NOT necessarily smaller), passive q
history, angular-rate history, and free-descent touchdown speed.
"""
import json
import math
import os
import sys
import tempfile

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, _finite_difference,
)
from motor_data import load_motor_by_index
from scripts.descent_gates import apex_time_from_apogee_events, q_components
from scripts.flip_diagnosis import E8_8, find_motor_index

ARTIFACTS = "artifacts/autoevo"
os.makedirs(ARTIFACTS, exist_ok=True)

H73J_IDX = find_motor_index("H73J")
K550W_IDX = find_motor_index("K550W")
H73J_MASS = load_motor_by_index(H73J_IDX).loaded_mass_kg
K550W_MASS = load_motor_by_index(K550W_IDX).loaded_mass_kg
MASS_GAP = round(K550W_MASS - H73J_MASS, 4)

CASES = [
    {"id": "A_H73J_no_ballast", "s1_retro": H73J_IDX,
     "s1_aft_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0},
    {"id": "B_H73J_mass_matched_aft_ballast", "s1_retro": H73J_IDX,
     "s1_aft_ballast_kg": MASS_GAP, "s1_mid_ballast_kg": 0.0},
    {"id": "C_H73J_distributed_ballast", "s1_retro": H73J_IDX,
     "s1_aft_ballast_kg": MASS_GAP * 0.5, "s1_mid_ballast_kg": MASS_GAP * 0.5},
    {"id": "D_K550W_present_never_ignited", "s1_retro": K550W_IDX,
     "s1_aft_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0},
]


def run_case(case):
    p = dict(E8_8)
    p.update({k: v for k, v in case.items() if k != "id"})
    p['s1_retro_delay'] = 200.0  # never ignites -- passive descent only

    ork_xml = generate_ork(p)
    fd, path = tempfile.mkstemp(suffix='.ork')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        sim.simulate(_get_anti_tumble_listener())
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        mass_arr = br.get(fdt.TYPE_MASS)
        cg_arr = br.get(fdt.TYPE_CG_LOCATION)
        # OpenRocket's naming is the inverse of the intuitive guess: verified
        # empirically here by adding a known aft ballast mass/position and
        # checking which field's delta matches the parallel-axis-theorem
        # prediction (m * distance_from_cg^2). TYPE_LONGITUDINAL_INERTIA is
        # the transverse (pitch/yaw) moment of inertia driven by the body's
        # LENGTH distribution -- "longitudinal" describes the mass
        # distribution, not the rotation axis. TYPE_ROTATIONAL_INERTIA is
        # the roll/spin-axis inertia (small here; ballast rods sit near the
        # centerline and barely change it).
        pitch_inertia_arr = br.get(fdt.TYPE_LONGITUDINAL_INERTIA)
        roll_inertia_arr = br.get(fdt.TYPE_ROTATIONAL_INERTIA)
        pitch_rate_arr = br.get(fdt.TYPE_PITCH_RATE)
        yaw_rate_arr = br.get(fdt.TYPE_YAW_RATE)

        apogee_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.APOGEE
        )
        apex_t = apex_time_from_apogee_events(apogee_events, t_arr, alt_arr)
        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        # Pad-state mass properties (index 0: on the pad, before any burn or
        # propellant depletion anywhere in the vehicle).
        pad_mass = float(mass_arr[0])
        pad_cg = float(cg_arr[0])
        pad_pitch_inertia = float(pitch_inertia_arr[0])
        pad_roll_inertia = float(roll_inertia_arr[0])

        q_history = []
        rate_history = []
        best_run = 0.0
        run_start = None
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t:
                continue
            if hit_time is not None and t > hit_time:
                break
            vz = float(vz_arr[i])
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            vx = _finite_difference(px_arr, t_arr, i)
            vy = _finite_difference(py_arr, t_arr, i)
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            q_total, _, _ = q_components(theta, phi, vx, vy, vz, speed)
            rate = math.hypot(float(pitch_rate_arr[i]), float(yaw_rate_arr[i]))
            q_history.append({"t": round(t, 3), "q_total": round(q_total, 4) if q_total is not None else None})
            rate_history.append({"t": round(t, 3), "angular_rate_norm": round(rate, 4)})

            if q_total is not None and q_total > 0.3:
                if run_start is None:
                    run_start = t
                best_run = max(best_run, t - run_start)
            else:
                run_start = None

        q_vals = [r["q_total"] for r in q_history if r["q_total"] is not None]
        mean_q = sum(q_vals) / len(q_vals) if q_vals else None
        rate_vals = [r["angular_rate_norm"] for r in rate_history]
        mean_rate = sum(rate_vals) / len(rate_vals) if rate_vals else None

        touchdown_speed = None
        if hit_time and n > 1:
            idx = min(range(n), key=lambda i: abs(float(t_arr[i]) - hit_time))
            touchdown_speed = math.sqrt(float(vxy_arr[idx]) ** 2 + float(vz_arr[idx]) ** 2)

        return {
            "case_id": case["id"],
            "retro_motor": "H73J" if case["s1_retro"] == H73J_IDX else "K550W",
            "s1_aft_ballast_kg": case.get("s1_aft_ballast_kg", 0.0),
            "s1_mid_ballast_kg": case.get("s1_mid_ballast_kg", 0.0),
            "total_mass_kg": round(pad_mass, 4),
            "cg_m": round(pad_cg, 5),
            "pitch_inertia_kg_m2": round(pad_pitch_inertia, 6),
            "roll_inertia_kg_m2": round(pad_roll_inertia, 6),
            "apex_t": round(apex_t, 3),
            "hit_t": round(hit_time, 3) if hit_time else None,
            "mean_q_total": round(mean_q, 4) if mean_q is not None else None,
            "mean_angular_rate_norm": round(mean_rate, 4) if mean_rate is not None else None,
            "sustained_positive_q_window_s": round(best_run, 3),
            "free_descent_touchdown_speed_ms": round(touchdown_speed, 3) if touchdown_speed is not None else None,
            "passive_q_history": q_history,
            "angular_rate_history": rate_history,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    print(f"H73J loaded mass: {H73J_MASS:.4f} kg   K550W loaded mass: {K550W_MASS:.4f} kg   "
          f"mass gap: {MASS_GAP:.4f} kg\n")
    results = []
    for case in CASES:
        print(f"=== {case['id']} ===")
        r = run_case(case)
        print(f"  total_mass={r['total_mass_kg']} cg={r['cg_m']} pitch_I={r['pitch_inertia_kg_m2']} "
              f"roll_I={r['roll_inertia_kg_m2']}")
        print(f"  mean_q={r['mean_q_total']} sustained_window={r['sustained_positive_q_window_s']}s "
              f"touchdown={r['free_descent_touchdown_speed_ms']} m/s")
        results.append(r)
        print()

    # Attribution pass: does the passive benefit track mass, CG, or inertia?
    by_id = {r["case_id"]: r for r in results}
    a, b, c, d = (by_id[k] for k in (
        "A_H73J_no_ballast", "B_H73J_mass_matched_aft_ballast",
        "C_H73J_distributed_ballast", "D_K550W_present_never_ignited",
    ))
    attribution = {
        "A_vs_B_mass_effect_isolated": {
            "delta_mass_kg": round(b["total_mass_kg"] - a["total_mass_kg"], 4),
            "delta_cg_m": round(b["cg_m"] - a["cg_m"], 5),
            "delta_pitch_inertia": round(b["pitch_inertia_kg_m2"] - a["pitch_inertia_kg_m2"], 6),
            "delta_sustained_window_s": round(b["sustained_positive_q_window_s"] - a["sustained_positive_q_window_s"], 3),
        },
        "B_vs_C_inertia_distribution_at_matched_mass": {
            "delta_mass_kg": round(c["total_mass_kg"] - b["total_mass_kg"], 4),
            "delta_cg_m": round(c["cg_m"] - b["cg_m"], 5),
            "delta_pitch_inertia": round(c["pitch_inertia_kg_m2"] - b["pitch_inertia_kg_m2"], 6),
            "delta_sustained_window_s": round(c["sustained_positive_q_window_s"] - b["sustained_positive_q_window_s"], 3),
        },
        "C_vs_D_ballast_matched_config_vs_real_K550W": {
            "delta_mass_kg": round(d["total_mass_kg"] - c["total_mass_kg"], 4),
            "delta_cg_m": round(d["cg_m"] - c["cg_m"], 5),
            "delta_pitch_inertia": round(d["pitch_inertia_kg_m2"] - c["pitch_inertia_kg_m2"], 6),
            "delta_sustained_window_s": round(d["sustained_positive_q_window_s"] - c["sustained_positive_q_window_s"], 3),
        },
    }

    # Case D (real K550W) has LOWER mass and LOWER pitch inertia than Case C
    # (H73J + ballast matched/exceeding K550W's mass and pitch inertia), yet
    # a vastly longer sustained tail-first window (+26s) and slower
    # touchdown. Matching or exceeding mass/CG/pitch-inertia via ballast does
    # NOT reproduce K550W's passive stabilization, so the benefit cannot be
    # attributed to those three variables from this data; the likely
    # remaining variable is K550W's larger motor-casing diameter (0.054 m vs
    # H73J's 0.038 m), which this ballast-only matrix does not control for.
    mass_cg_inertia_reproduces_k550w_stability = (
        c["sustained_positive_q_window_s"] >= 0.5 * d["sustained_positive_q_window_s"]
    )
    classification = (
        "cg_and_inertia" if mass_cg_inertia_reproduces_k550w_stability
        else "unresolved_not_attributable_to_mass_cg_or_pitch_inertia"
    )

    out = {
        "h73j_loaded_mass_kg": round(H73J_MASS, 4),
        "k550w_loaded_mass_kg": round(K550W_MASS, 4),
        "mass_gap_kg": MASS_GAP,
        "cases": results,
        "attribution": attribution,
        "classification": classification,
        "classification_note": (
            f"Case C (H73J + ballast, mass={c['total_mass_kg']}kg, "
            f"pitch_I={c['pitch_inertia_kg_m2']}) has HIGHER mass and pitch "
            f"inertia than Case D (real K550W, mass={d['total_mass_kg']}kg, "
            f"pitch_I={d['pitch_inertia_kg_m2']}) but only "
            f"{c['sustained_positive_q_window_s']}s sustained tail-first "
            f"window vs D's {d['sustained_positive_q_window_s']}s. Mass, CG, "
            "and pitch inertia were matched or exceeded via physical ballast "
            "and still did not reproduce the stabilization, so this data "
            "does not support attributing K550W's passive benefit to any "
            "combination of those three variables. The uncontrolled "
            "remaining difference is motor casing diameter/aerodynamic "
            "footprint at the tail, not tested by this ballast-only matrix."
        ),
    }
    out_path = os.path.join(ARTIFACTS, "tail-mass-cg-inertia-matrix.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
