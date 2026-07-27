#!/usr/bin/env python3
"""Wide search over the PodSet external-3+1 vehicle genome.

Two-tier search (fast analytic filter -> OpenRocket ground truth), instead
of a full Rust engine rewrite for this still-stabilizing topology (see
session notes -- duplicating an unstable geometry model into a second
engine risks propagating the same bugs twice, for a multi-day cost this
project's deadline does not have room for). The analytic tier is a coarse
rocket-equation-level liftoff-TWR/delta-v estimate with NO OpenRocket call;
only candidates that pass it spend a real (slower) OpenRocket simulation.

Pod-to-core distance is a searched, MINIMIZED variable per candidate: the
gap is set to exactly what that candidate's own chosen pod fin height
needs (plus a fixed strut/manufacturing margin), not a padded constant.
"""
import itertools
import json
import math
import os
import sqlite3
import sys

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from osifog_sweep import init_or, SIM_SEED, run_sim, save_simulated_ork, MIN_DIMENSION_M
from osifog_podset import generate_podset_ork, resolve_motor, motor_stats, _MOTOR_DB_PATH

ARTIFACTS = "artifacts/podset"
os.makedirs(ARTIFACTS, exist_ok=True)

WIND_LEVELS = [
    (0, 3.0, 270.0, 0.5), (500, 6.0, 270.0, 1.0), (1000, 9.0, 270.0, 1.5),
    (2000, 12.0, 270.0, 2.0), (3000, 15.0, 270.0, 2.5),
]

TARGET_APOGEE_M = 3000.0
MIN_LIFTOFF_TWR = 4.0
G0 = 9.81

# Structural mass estimate constants (rough, for the analytic pre-filter
# ONLY -- OpenRocket's own mass calculation is the ground truth used for
# every number actually reported/saved).
SHELL_AREAL_DENSITY_KG_M2 = 1.8 * 0.002  # fiberglass, 2mm wall


def _pod_ascent_candidates():
    """Query the full local motor DB for a spread of pod-friendly ascent
    motors (18-38mm diameter, E through J impulse classes) -- this is the
    part that actually uses the full 1458-motor catalog instead of the
    38-motor curated MOTOR_DATABASE."""
    conn = sqlite3.connect(str(_MOTOR_DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT designation, diameter, total_impulse, avg_thrust, burn_time
               FROM motors
               WHERE diameter BETWEEN 18 AND 38
                 AND impulse_class IN ('E','F','G','H','I','J')
                 AND total_impulse IS NOT NULL AND avg_thrust IS NOT NULL
               ORDER BY total_impulse ASC"""
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    # Thin the list to a manageable spread across the impulse range instead
    # of testing all ~200+ matches.
    if len(rows) > 24:
        step = len(rows) / 24.0
        rows = [rows[int(i * step)] for i in range(24)]
    return [r[0] for r in rows]


def analytic_liftoff_check(main_designation, retro_designation, core_radius,
                          core_length, pod_radius, pod_length, nose_mass_kg,
                          ballast_kg):
    """Rough two-stage liftoff-TWR + optimistic (no-drag) delta-v/altitude
    estimate. Returns (passes: bool, estimate: dict)."""
    main_impulse, main_mass, main_prop, main_burn = motor_stats(main_designation)
    main_avg_thrust = main_impulse / main_burn if main_burn else 0.0
    retro_impulse, retro_mass, retro_prop, retro_burn = motor_stats(retro_designation)

    shell_area = 2 * math.pi * core_radius * core_length + 3 * (2 * math.pi * pod_radius * pod_length)
    shell_mass = shell_area * SHELL_AREAL_DENSITY_KG_M2
    stage_dry_mass = shell_mass + retro_mass + ballast_kg + 0.15  # +0.15 fins/rings/pylons fudge
    stage_loaded_mass = stage_dry_mass + 3 * main_mass

    # Two identical stages (this search shares dimensions across s0/s1).
    liftoff_mass = 2 * stage_loaded_mass + nose_mass_kg
    liftoff_thrust = 3 * main_avg_thrust
    liftoff_twr = liftoff_thrust / (liftoff_mass * G0) if liftoff_mass > 0 else 0.0

    total_impulse = 2 * 3 * main_impulse  # both stages' ascent motors
    # optimistic: all impulse converts to momentum on the full liftoff mass,
    # then ballistic coast to apogee (ignores drag/staging mass drop -- a
    # deliberately generous upper bound, only useful for rejecting motors
    # that are obviously an order of magnitude too weak).
    delta_v_est = total_impulse / liftoff_mass if liftoff_mass > 0 else 0.0
    apogee_est = delta_v_est ** 2 / (2 * G0)

    passes = liftoff_twr >= MIN_LIFTOFF_TWR and apogee_est >= TARGET_APOGEE_M * 0.3
    return passes, {
        "liftoff_twr": liftoff_twr, "apogee_est_optimistic_m": apogee_est,
        "liftoff_mass_kg": liftoff_mass, "stage_loaded_mass_kg": stage_loaded_mass,
    }


def build_params(main_designation, retro_designation, core_radius_scale,
                 nose_mass_kg, pod_fin_height_frac, core_fin_scale, ballast_kg):
    main_diam = resolve_motor(main_designation)[2]
    main_len = resolve_motor(main_designation)[3]
    retro_diam = resolve_motor(retro_designation)[2]

    # Must exceed _pod_xml's own requirement: pod_radius - 0.002 >= mount_or
    # + MIN_DIMENSION_M, where mount_or = diam/2 + MOTOR_TUBE_WALL_M(0.001)
    # + MOTOR_INSERTION_CLEARANCE_M(0.00025) -- i.e. diam/2 + 0.00425 minimum.
    pod_radius = max(0.016, main_diam / 2.0 + 0.006)
    pod_length = main_len + 0.05
    core_radius = max(retro_diam / 2.0 + 0.006, 0.02) * core_radius_scale
    core_length = max(0.4, pod_length * 0.9)

    # Minimize pod-to-core distance: gap = exactly what this candidate's own
    # fin height needs, plus a small fixed strut/manufacturing margin.
    pod_fin_height = pod_radius * 2.0 * pod_fin_height_frac
    gap = pod_fin_height + 0.008
    pod_offset = core_radius + pod_radius + gap

    p = dict(
        s0_retro=retro_designation, s0_retro_delay=200.0,
        s1_retro=retro_designation, s1_retro_delay=200.0, s1_separation_delay=0.5,
        nose_mass_kg=nose_mass_kg,
        launch_azimuth=270.0, launch_angle_deg=3.0,
        wind_levels=WIND_LEVELS,
    )
    for prefix in ("s0", "s1"):
        p[f"{prefix}_main"] = main_designation
        p[f"{prefix}_core_radius"] = core_radius
        p[f"{prefix}_core_length"] = core_length
        p[f"{prefix}_pod_radius"] = pod_radius
        p[f"{prefix}_pod_length"] = pod_length
        p[f"{prefix}_pod_radial_offset"] = pod_offset
        p[f"{prefix}_core_fin_count"] = 4
        p[f"{prefix}_core_fin_root"] = max(0.10, core_radius * 4.0) * core_fin_scale
        p[f"{prefix}_core_fin_height"] = max(0.05, core_radius * 2.0) * core_fin_scale
        p[f"{prefix}_pod_fin_count"] = 3 if pod_fin_height > 0.005 else 0
        p[f"{prefix}_pod_fin_root"] = pod_length * 0.15
        p[f"{prefix}_pod_fin_height"] = pod_fin_height
        p[f"{prefix}_pod_nose_shape"] = "ogive"
        p[f"{prefix}_ballast_kg"] = ballast_kg
    return p, {"pod_offset": pod_offset, "gap": gap, "core_radius": core_radius}


def score(m):
    mach = m.get("mach", 999.0)
    apogee = m.get("apogee_m", 0.0)
    segs = m.get("ascent_stability_segments", [])
    margins = [s["min_calibers"] for s in segs if s.get("min_calibers") is not None]
    min_margin = min(margins) if margins else -999.0
    legal = mach < 1.0 and min_margin >= 1.5
    return legal, abs(apogee - TARGET_APOGEE_M), mach, min_margin, apogee


def main():
    init_or()
    motors = _pod_ascent_candidates()
    retro_designation = "F50T"  # small, light -- only needs to slow a landing, not lift
    print(f"Screening {len(motors)} ascent motor candidates from the full local catalog",
          file=sys.stderr)

    combos = list(itertools.product(
        motors,
        (0.7, 1.0, 1.4, 1.8),     # core_radius_scale
        (0.05, 0.1, 0.15),        # nose_mass_kg (small -- the nose cavity is
                                  # only ~5cm of usable length at this scale;
                                  # a real containment check now rejects
                                  # anything that doesn't physically fit,
                                  # unlike the earlier unvalidated 0.3-2.5kg
                                  # sweep that silently overflowed 13.7cm
                                  # past the nose on its "winning" candidate)
        (0.6, 1.0),               # pod_fin_height_frac
        (1.0,),                   # core_fin_scale
        (0.0, 0.3, 0.6),          # ballast_kg (core ballast -- much more
                                  # usable length than the nose, a better
                                  # lever for stability margin now)
    ))
    print(f"{len(combos)} total combos before analytic filter", file=sys.stderr)

    analytic_pass = []
    for combo in combos:
        main_d, core_scale, nose_mass, fin_frac, core_fin_scale, ballast_kg = combo
        try:
            p, geo = build_params(main_d, retro_designation, core_scale, nose_mass,
                                  fin_frac, core_fin_scale, ballast_kg)
        except Exception:
            continue
        ok, est = analytic_liftoff_check(
            main_d, retro_designation, p["s0_core_radius"], p["s0_core_length"],
            p["s0_pod_radius"], p["s0_pod_length"], nose_mass, ballast_kg,
        )
        if ok:
            analytic_pass.append((combo, p, geo, est))

    print(f"{len(analytic_pass)}/{len(combos)} pass the analytic pre-filter "
          f"(liftoff TWR>={MIN_LIFTOFF_TWR}, optimistic apogee>={TARGET_APOGEE_M*0.3:.0f}m)",
          file=sys.stderr)

    # Budget the expensive OpenRocket tier.
    OR_BUDGET = 60
    if len(analytic_pass) > OR_BUDGET:
        step = len(analytic_pass) / OR_BUDGET
        analytic_pass = [analytic_pass[int(i * step)] for i in range(OR_BUDGET)]

    results = []
    for i, (combo, p, geo, est) in enumerate(analytic_pass):
        label = f"{combo[0]}_r{combo[1]}_m{combo[2]}_f{combo[3]}"
        try:
            ork_xml = generate_podset_ork(p)
            m = run_sim(ork_xml, seed=SIM_SEED)
            legal, penalty, mach, margin, apogee = score(m)
            print(f"[{i+1}/{len(analytic_pass)}] {label}: mach={mach:.3f} margin={margin:.3f} "
                  f"apogee={apogee:.1f} legal={legal}", file=sys.stderr)
            results.append({
                "label": label, "params": p, "geo": geo, "analytic_est": est,
                "mach": mach, "min_margin_cal": margin, "apogee_m": apogee,
                "legal": legal, "apogee_penalty": penalty,
            })
        except ValueError as exc:
            print(f"[{i+1}/{len(analytic_pass)}] {label}: REJECTED_GEOMETRY {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"[{i+1}/{len(analytic_pass)}] {label}: SIM_ERROR {exc}", file=sys.stderr)

    legal_results = [r for r in results if r["legal"]]
    legal_results.sort(key=lambda r: r["apogee_penalty"])

    with open(os.path.join(ARTIFACTS, "full-search-results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)

    if legal_results:
        best = legal_results[0]
        print(f"\nBEST: {best['label']} mach={best['mach']:.3f} margin={best['min_margin_cal']:.3f} "
              f"apogee={best['apogee_m']:.1f} gap={best['geo']['gap']:.4f}m", file=sys.stderr)
        ork_xml = generate_podset_ork(best["params"])
        out_path = "designs/osifog_level3/octaweb_experiment/podset_best_candidate.ork"
        save_simulated_ork(ork_xml, out_path, seed=SIM_SEED)
        print(f"Wrote {out_path}", file=sys.stderr)
        print(json.dumps({"best": best["label"], "path": out_path, "apogee_m": best["apogee_m"],
                          "mach": best["mach"], "margin": best["min_margin_cal"]}, indent=2))
    else:
        print("\nNo legal candidate found in this search pass.", file=sys.stderr)
        results.sort(key=lambda r: (not r.get("legal", False), r.get("apogee_penalty", 9e9)))
        if results:
            print("Closest miss:", json.dumps({k: results[0][k] for k in
                  ("label", "mach", "min_margin_cal", "apogee_m")}, indent=2), file=sys.stderr)
        print(json.dumps({"best": None}, indent=2))


if __name__ == "__main__":
    main()
