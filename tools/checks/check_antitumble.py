#!/usr/bin/env python3
"""Verify best config 5 times for consistency."""
import sys, os, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from osifog_sweep import parse_wind_csv, generate_ork, init_or, run_sim, score
from rocket_forge import MOTOR_DATABASE

wind = parse_wind_csv("OSIFOG/OpenWind_File.csv")
helper = init_or()

p = {
    "s0_main": 19, "s0_retro": 0,
    "s1_main": 24, "s1_retro": 0,
    "s0_body_len": max(0.60, 0.314 + 0.098 + 0.12),
    "s0_body_rad": 0.034,
    "s1_body_len": max(0.70, 0.531 + 0.098 + 0.12),
    "s1_body_rad": max(0.035, 0.075 / 2 + 0.006),
    "s0_retro_delay": 174.0,
    "s1_retro_delay": 30.0,
    "nose_mass": 0.300,
    "wind_levels": wind,
}

print("Running best config 5 times for consistency check...")
print(f"{'run':>3}  {'apogee':>8} {'err':>6}  {'S0_vz':>6} {'S0_d':>7}  {'S1_vz':>6} {'S1_d':>7}  {'total_d':>8} {'penalty':>12}")
print("-" * 75)

for run in range(5):
    ork = generate_ork(p)
    m = run_sim(ork, helper, anti_tumble=True)
    s = score(m, p)

    s0_vz = s.get("s0_vz", 0)
    s0_d = s.get("s0_dist", 0)
    s1_vz = s.get("s1_vz", 0)
    s1_d = s.get("s1_dist", 0)
    total_pen = s["apogee_pen"] + s["prop_pen"] + s["pos_pen"] + s["vel_pen"]

    print(f"{run+1:3d}  {m['apogee_m']:8.1f} {s['apogee_err_m']:6.1f}  "
          f"{s0_vz:6.1f} {s0_d:7.1f}  {s1_vz:6.1f} {s1_d:7.1f}  "
          f"{s['landing_dist_m']:8.1f} {total_pen:12,.0f}")
