#!/usr/bin/env python3
"""
OSIFOG Level 3 — Generate Winner .ork
Runs the corrected pipeline with the best-known starting configuration
and saves the winner to designs/osifog_level3/falcon_winner.ork

Usage:
  python generate_winner.py              # Single run with best-known params
  python generate_winner.py --sweep      # Run full 3-phase sweep first
  python generate_winner.py --precision  # Fine-tune current best params only
"""
import sys
import os
import json
import argparse

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from osifog_sweep import (
    parse_wind_csv, generate_ork, init_or, run_sim,
    score_official, validate_hard_constraints,
    build_fine_grid, build_precision_grid,
    run_sweep, print_top_results, save_results, save_ork,
    MOTOR_DATABASE, LAUNCH_ROD_M,
)

WIND_CSV  = "OSIFOG/OpenWind_File.csv"
OUT_DIR   = "designs/osifog_level3"

# ─────────────────────────────────────────────────────────────
# Best-known starting configuration (from prior simulations)
# NOTE: This needs re-evaluation under corrected scoring formula.
#       J800T (idx=17) + L1150 (idx=24) + F50T (idx=0) retro
# ─────────────────────────────────────────────────────────────
BEST_KNOWN_PARAMS = {
    "s0_main":  17,   # J800T — sustainer ascent (54mm motor, fits in 44mm body)
    "s0_retro":  0,   # F50T  — sustainer landing (29mm motor)
    "s1_main":  24,   # L1150 — booster ascent (75mm motor, needs >= 43mm body)
    "s1_retro":  0,   # F50T  — booster landing (29mm motor)
    # CRITICAL: Both stages must have the SAME body radius at the joint.
    # Booster L1150 is 75mm dia -> 37.5mm radius + ~6mm wall = 43.5mm min.
    # Use 44mm for both. Sustainer J800T (54mm) fits easily in 44mm body.
    "s0_body_rad":  0.044,   # 44mm radius = 88mm diameter (same as booster)
    "s0_body_len":  0.560,   # 0.314 (J800T) + 0.098 (F50T) + 0.15 margin
    "s1_body_rad":  0.044,   # 44mm radius = 88mm diameter (fits L1150 75mm)
    "s1_body_len":  0.760,   # 0.531 (L1150) + 0.098 (F50T) + 0.13 margin
    "s0_retro_delay": 174.0, # Sustainer retro ignition delay (s from launch)
    "s1_retro_delay":  30.0, # Booster retro ignition delay (s from launch)
    "nose_mass_kg":   2.5, # Nose ballast
    "s0_mid_ballast_kg": 0.0,
    "s0_aft_ballast_kg": 0.0,  # Promotes tail-first orientation
    "s1_mid_ballast_kg": 0.0,
    "s1_aft_ballast_kg": 0.0,
    "s0_fin_count":  4,
    "s0_fin_sweep":  25,
    "s0_fin_root":   0.100,   # scaled for 44mm body
    "s0_fin_height": 0.050,
    "s1_fin_count":  4,
    "s1_fin_sweep":  25,
    "s1_fin_root":   0.400,
    "s1_fin_height": 0.200,
    # 288 = directly into the wind (wind originates from 288 deg WNW)
    "launch_azimuth":   288.0,
    "launch_angle_deg":   0.0,
}


def run_single(wind, helper, params=None):
    """Run one simulation with given params (or BEST_KNOWN_PARAMS)."""
    if params is None:
        params = BEST_KNOWN_PARAMS
    p = {**params, "wind_levels": wind}
    ork = generate_ork(p)
    m = run_sim(ork, helper, anti_tumble=True)
    s = score_official(m, p)
    is_legal, violations = validate_hard_constraints(m, p)
    return m, s, violations


def print_result(m, s, violations):
    is_legal = len(violations) == 0
    sim_complete = "ABORTED" not in m.get("status", "").upper()
    
    print("\nLEGAL: " + str(is_legal).lower())
    print("SIMULATION_COMPLETE: " + str(sim_complete).lower())
    print("\nApogee:")
    print(f"  altitude: {m.get('apogee_m', 0):.2f} m")
    print(f"  east: {s.get('E_ap', 0):.1f} m")
    print(f"  north: {s.get('N_ap', 0):.1f} m")
    print(f"  time: {m.get('flight_time_s', 0):.2f} s")

    stages = m.get("stage_landings", [])
    for i, st in enumerate(stages):
        print(f"\nStage {i} touchdown:")
        # We lack precise event times per stage for landing in Python script without more data, using flight_time as proxy
        print(f"  time: {m.get('flight_time_s', 0):.3f} s")
        print(f"  east: {st['east_m']:.1f} m")
        print(f"  north: {st['north_m']:.1f} m")
        print(f"  vertical speed: {st['vz_ms']:.2f} m/s")
        print(f"  horizontal speed: {st['vxy_ms']:.2f} m/s")
        print(f"  total speed: {st['total_speed']:.2f} m/s")

    print("\nMean touchdown:")
    print(f"  east: {s.get('mean_E', 0):.1f} m")
    print(f"  north: {s.get('mean_N', 0):.1f} m")
    print(f"  total speed: {s.get('mean_V', 0):.2f} m/s")

    print(f"\nPropellant consumed: {s.get('m_prop_kg', 0):.3f} kg")
    print(f"Maximum Mach: {m.get('mach', 0):.3f}")
    
    print("Official score terms:")
    print(f"  apogee penalty: {s.get('apogee_alt_pen', 0):.0f}")
    print(f"  horizontal-apogee penalty: {s.get('apogee_horiz_pen', 0):.0f}")
    print(f"  touchdown-position penalty: {s.get('touch_pos_pen', 0):.0f}")
    print(f"  touchdown-speed penalty: {s.get('touch_vel_pen', 0):.0f}")
    print(f"  propellant penalty: {s.get('prop_pen', 0):.0f}")
    print(f"Final score: {s.get('score', 0):.0f}")
    
    if violations:
        print("\nVIOLATIONS:")
        for v in violations:
            print(f"  - {v}")


def main():
    parser = argparse.ArgumentParser(description="OSIFOG Level 3 winner generator")
    parser.add_argument("--sweep", action="store_true",
                        help="Run full 3-phase sweep before generating winner")
    parser.add_argument("--precision", action="store_true",
                        help="Run precision timing sweep around best-known params")
    parser.add_argument("--seeds", type=int, default=1,
                        help="Number of random seeds for robustness check (default: 1)")
    args = parser.parse_args()

    wind   = parse_wind_csv(WIND_CSV)
    helper = init_or()

    if args.sweep:
        # Full pipeline — import and run main
        from osifog_sweep import main as sweep_main
        sweep_main()
        return

    if args.precision:
        print("Running precision timing sweep around best-known parameters...")
        base = BEST_KNOWN_PARAMS
        grid = build_precision_grid(base, wind)
        print(f"Precision grid: {len(grid)} candidates")
        results = run_sweep(grid, helper, label="precision")
        print_top_results(results, n=10, label="PRECISION TOP 10")
        save_results(results, "precision")

        winner_r = results[0]
        best_p = winner_r["params"]
        m, s, violations = run_single(wind, helper, {**best_p, "wind_levels": wind})
        print_result(m, s, violations)

        if args.seeds > 1:
            print(f"\nRunning {args.seeds}-seed robustness check...")
            import random
            seed_scores = []
            for sd in [16000 + i * 1000 for i in range(args.seeds)]:
                ork = generate_ork({**best_p, "wind_levels": wind})
                m_sd = run_sim(ork, helper, seed=sd)
                s_sd = score_official(m_sd, best_p)
                seed_scores.append(s_sd["score"])
                print(f"  Seed {sd}: score={s_sd['score']:.0f}  apogee={m_sd.get('apogee_m',0):.1f}m")
            print(f"\n  Robustness: mean={sum(seed_scores)/len(seed_scores):.0f}  "
                  f"min={min(seed_scores):.0f}  max={max(seed_scores):.0f}")

        save_ork(best_p, wind, "falcon_precision_winner")
        return

    # Default: single run with best-known params
    print("Running single simulation with best-known configuration...")
    m, s, violations = run_single(wind, helper)
    print_result(m, s, violations)

    # Save the .ork regardless
    os.makedirs(OUT_DIR, exist_ok=True)
    save_ork(BEST_KNOWN_PARAMS, wind, "falcon_best")

    # Offer robustness check
    if args.seeds > 1:
        print(f"\nRunning {args.seeds}-seed robustness check...")
        seed_scores = []
        for sd in [16000 + i * 1000 for i in range(args.seeds)]:
            ork = generate_ork({**BEST_KNOWN_PARAMS, "wind_levels": wind})
            m_sd = run_sim(ork, helper, seed=sd)
            s_sd = score_official(m_sd, BEST_KNOWN_PARAMS)
            seed_scores.append(s_sd["score"])
            print(f"  Seed {sd}: score={s_sd['score']:.0f}  apogee={m_sd.get('apogee_m',0):.1f}m")
        print(f"\n  Robustness: mean={sum(seed_scores)/len(seed_scores):.0f}  "
              f"min={min(seed_scores):.0f}  max={max(seed_scores):.0f}")


if __name__ == "__main__":
    main()
