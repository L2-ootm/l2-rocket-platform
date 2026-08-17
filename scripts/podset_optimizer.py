#!/usr/bin/env python3
"""
PodSet Optimizer: Full search + Delay Sweep for Landing < 5 m/s
"""
import itertools
import json
import math
import os
import sqlite3
import sys
import random

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from osifog_sweep import init_or, SIM_SEED, run_sim, save_simulated_ork
from osifog_podset import generate_podset_ork, resolve_motor
from scripts.podset_full_search import _pod_ascent_candidates, analytic_liftoff_check

ARTIFACTS = "artifacts/podset"
os.makedirs(ARTIFACTS, exist_ok=True)
WIND_LEVELS = [
    (0, 3.0, 270.0, 0.5), (500, 6.0, 270.0, 1.0), (1000, 9.0, 270.0, 1.5),
    (2000, 12.0, 270.0, 2.0), (3000, 15.0, 270.0, 2.5),
]
TARGET_APOGEE = 3000.0

def build_p(main_d, retro_d, core_r, core_l, pod_r, pod_l, nose_mass, ballast_kg, 
            pod_fin_h, pod_fin_c, core_fin_h, core_fin_c, ring_thick, ring_fwd_mat, 
            s0_retro_delay=200.0, s1_retro_delay=200.0):
    
    # ensure physical gaps
    gap = pod_fin_h + 0.008
    pod_offset = core_r + pod_r + gap

    p = dict(
        s0_retro=retro_d, s0_retro_delay=s0_retro_delay,
        s1_retro=retro_d, s1_retro_delay=s1_retro_delay, s1_separation_delay=0.5,
        nose_mass_kg=nose_mass,
        launch_azimuth=270.0, launch_angle_deg=3.0,
        wind_levels=WIND_LEVELS,
    )
    for prefix in ("s0", "s1"):
        p[f"{prefix}_main"] = main_d
        p[f"{prefix}_core_radius"] = core_r
        p[f"{prefix}_core_length"] = core_l
        p[f"{prefix}_pod_radius"] = pod_r
        p[f"{prefix}_pod_length"] = pod_l
        p[f"{prefix}_pod_radial_offset"] = pod_offset
        
        p[f"{prefix}_pod_fin_count"] = pod_fin_c if pod_fin_h > 0.005 else 0
        p[f"{prefix}_pod_fin_height"] = pod_fin_h
        p[f"{prefix}_pod_fin_root"] = pod_l * 0.15
        p[f"{prefix}_pod_nose_shape"] = "ogive"
        
        p[f"{prefix}_core_fin_count"] = core_fin_c
        p[f"{prefix}_core_fin_root"] = max(0.10, core_r * 4.0)
        p[f"{prefix}_core_fin_height"] = core_fin_h
        
        p[f"{prefix}_ring_thickness_m"] = ring_thick
        p[f"{prefix}_ring_fwd_material"] = ring_fwd_mat
        p[f"{prefix}_ring_aft_material"] = "aluminum"
        
        p[f"{prefix}_ballast_kg"] = ballast_kg
    return p, gap

def score_run(m):
    mach = m.get("mach", 999.0)
    apogee = m.get("apogee_m", 0.0)
    segs = m.get("ascent_stability_segments", [])
    margins = [s["min_calibers"] for s in segs if s.get("min_calibers") is not None]
    min_margin = min(margins) if margins else -999.0
    
    # Touchdown speed
    s0_vel = m.get("s0_touchdown_vel", 999.0)
    s1_vel = m.get("s1_touchdown_vel", 999.0)

    legal_ascent = mach < 1.0 and min_margin >= 1.5
    legal_landing = s0_vel < 5.0 and s1_vel < 5.0
    return legal_ascent, legal_landing, abs(apogee - TARGET_APOGEE), mach, min_margin, apogee, s0_vel, s1_vel

def random_search(budget=100):
    init_or()
    motors = _pod_ascent_candidates()
    retro_d = "F50T"
    
    valid_motors = []
    print("Filtering live motors...")
    for m in motors:
        try:
            resolve_motor(m)
            valid_motors.append(m)
        except ValueError:
            pass
    motors = valid_motors
    
    candidates = []
    print(f"Generating random candidates...")
    for _ in range(budget * 10):
        m = random.choice(motors)
        main_diam = resolve_motor(m)[2]
        main_len = resolve_motor(m)[3]
        retro_diam = resolve_motor(retro_d)[2]
        
        core_r = max(retro_diam / 2.0 + 0.006, 0.02) * random.uniform(0.7, 1.8)
        pod_r = max(0.016, main_diam / 2.0 + 0.006)
        pod_l = main_len + random.uniform(0.05, 0.2)
        core_l = max(0.6, pod_l * random.uniform(1.2, 2.0))
        
        nose_mass = random.uniform(0.1, 1.2)
        ballast_kg = random.choice([0.0, 0.5, 1.0, 1.5])
        
        pod_fin_h = pod_r * 2.0 * random.choice([0.0, 0.5, 1.0])
        pod_fin_c = random.choice([0, 3])
        
        core_fin_h = max(0.08, core_r * 3.0) * random.choice([1.0, 1.5, 2.0])
        core_fin_c = random.choice([3, 4])
        
        ring_thick = random.uniform(0.002, 0.006)
        ring_fwd_mat = random.choice(["fiberglass", "aluminum", "cardboard"])
        
        ok, est = analytic_liftoff_check(m, retro_d, core_r, core_l, pod_r, pod_l, nose_mass, ballast_kg)
        if ok:
            candidates.append((m, retro_d, core_r, core_l, pod_r, pod_l, nose_mass, ballast_kg, 
                               pod_fin_h, pod_fin_c, core_fin_h, core_fin_c, ring_thick, ring_fwd_mat))
            if len(candidates) >= budget:
                break
                
    print(f"Running {len(candidates)} candidates in OpenRocket...")
    best_ascent = None
    best_penalty = 9e9
    
    for i, c in enumerate(candidates):
        try:
            p, gap = build_p(*c)
            ork_xml = generate_podset_ork(p)
            m = run_sim(ork_xml, seed=SIM_SEED)
            leg_a, leg_l, pen, mach, marg, apo, s0v, s1v = score_run(m)
            print(f"[{i+1}/{len(candidates)}] mach={mach:.3f} margin={marg:.3f} apogee={apo:.1f} legal_ascent={leg_a}")
            if leg_a and pen < best_penalty:
                best_penalty = pen
                best_ascent = c
        except Exception as e:
            pass

    if not best_ascent:
        print("No legal ascent found!")
        return

    print("Found best ascent legal candidate. Tuning retro delays...")
    
    # Phase 2: Retro delay sweep
    best_delay_penalty = 9e9
    best_final_p = None
    best_final_res = None
    
    for s0_d in range(25, 45):
        for s1_d in range(25, 45):
            p, gap = build_p(*best_ascent, s0_retro_delay=s0_d, s1_retro_delay=s1_d)
            try:
                ork_xml = generate_podset_ork(p)
                m = run_sim(ork_xml, seed=SIM_SEED)
                leg_a, leg_l, pen, mach, marg, apo, s0v, s1v = score_run(m)
                
                print(f"Delays ({s0_d}, {s1_d}): v0={s0v:.2f}, v1={s1v:.2f}, legal_landing={leg_l}")
                delay_pen = s0v + s1v
                if leg_l and delay_pen < best_delay_penalty:
                    best_delay_penalty = delay_pen
                    best_final_p = p
                    best_final_res = (apo, mach, marg, s0v, s1v)
                    # We can break early or keep searching
                    if delay_pen < 8.0:
                        break
            except:
                pass
                
    if best_final_p:
        print(f"WINNER: Apo={best_final_res[0]:.1f}, Mach={best_final_res[1]:.3f}, Marg={best_final_res[2]:.3f}, V0={best_final_res[3]:.1f}, V1={best_final_res[4]:.1f}")
        ork_xml = generate_podset_ork(best_final_p)
        out = "designs/osifog_level3/octaweb_experiment/podset_landing_optimized.ork"
        save_simulated_ork(ork_xml, out, seed=SIM_SEED)
        print(f"Saved to {out}")
    else:
        print("Could not find a delay that lands < 5 m/s.")
        
if __name__ == "__main__":
    random_search(150)
