#!/usr/bin/env python3
"""
PodSet Evolutionary Optimizer: Uses scipy's differential_evolution and the official
OSIFOG scoring formula. Evolving S0 and S1 independently to find the ultimate
L2 PodSet 3+1 architecture.
"""
import json
import os
import sys
from scipy.optimize import differential_evolution

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from osifog_sweep import init_or, SIM_SEED, run_sim, save_simulated_ork, MOTOR_DATABASE, WIND_CSV, parse_wind_csv
from osifog_podset import generate_podset_ork, resolve_motor
from osifog_precision import score_from_mission_contract, load_mission_contract

ARTIFACTS = "artifacts/podset"
os.makedirs(ARTIFACTS, exist_ok=True)
WIND_LEVELS = parse_wind_csv(WIND_CSV)

init_or()
mission = load_mission_contract()

def get_valid_motors(allowed_list):
    valid = []
    for m in allowed_list:
        idx = -1
        for i, db_m in enumerate(MOTOR_DATABASE):
            if db_m[1] == m:
                idx = i
                break
        if idx != -1:
            try:
                resolve_motor(idx)
                valid.append(idx)
            except ValueError:
                pass
    return valid

MAIN_MOTORS = get_valid_motors(mission["motor_pool"]["allowed_designations"])
RETRO_MOTORS = get_valid_motors(mission["motor_pool"]["retro_allowed_designations"])
print(f"Loaded {len(MAIN_MOTORS)} main motors, {len(RETRO_MOTORS)} retro motors.", file=sys.stderr)

def build_genome(x):
    # Decode 21 variables
    def get_motor(val, lst):
        idx = int(round(val))
        return lst[max(0, min(len(lst)-1, idx))]
        
    s0_main_d = get_motor(x[0], MAIN_MOTORS)
    s1_main_d = get_motor(x[1], MAIN_MOTORS)
    s0_retro_d = get_motor(x[2], RETRO_MOTORS)
    s1_retro_d = get_motor(x[3], RETRO_MOTORS)
    
    core_r_extra = x[4]
    pod_l_extra = x[5]
    core_l_scale = x[6]
    nose_mass = x[7]
    s0_ballast_kg = x[8]
    s1_ballast_kg = x[9]
    
    s0_pod_fin_h_frac = x[10]
    s1_pod_fin_h_frac = x[11]
    s0_pod_fin_c = 3 if x[12] > 0.5 else 0
    s1_pod_fin_c = 3 if x[13] > 0.5 else 0
    
    s0_core_fin_h_scale = x[14]
    s1_core_fin_h_scale = x[15]
    s0_core_fin_c = int(round(x[16]))
    s1_core_fin_c = int(round(x[17]))
    
    s1_sep_delay = x[18]
    s0_retro_delay = x[19]
    s1_retro_delay = x[20]
    
    # Calculate geometries based on largest motor per stage
    s0_main_diam = resolve_motor(s0_main_d)[2]
    s1_main_diam = resolve_motor(s1_main_d)[2]
    s0_retro_diam = resolve_motor(s0_retro_d)[2]
    s1_retro_diam = resolve_motor(s1_retro_d)[2]
    s0_main_len = resolve_motor(s0_main_d)[3]
    s1_main_len = resolve_motor(s1_main_d)[3]
    
    max_retro_diam = max(s0_retro_diam, s1_retro_diam)
    # Guarantee at least 10mm clearance on radius, plus extra
    core_r = max(max_retro_diam / 2.0 + 0.010, 0.025) + core_r_extra
    
    s0_pod_r = max(0.016, s0_main_diam / 2.0 + 0.006)
    s1_pod_r = max(0.016, s1_main_diam / 2.0 + 0.006)
    
    s0_pod_l = s0_main_len + pod_l_extra
    s1_pod_l = s1_main_len + pod_l_extra
    
    max_pod_l = max(s0_pod_l, s1_pod_l)
    core_l = max(0.6, max_pod_l * core_l_scale)

    # Convert fractions to absolute heights
    s0_pod_fin_h = s0_pod_r * 2.0 * s0_pod_fin_h_frac
    s1_pod_fin_h = s1_pod_r * 2.0 * s1_pod_fin_h_frac
    
    # Gap prevents fins hitting core
    s0_gap = s0_pod_fin_h + 0.008
    s1_gap = s1_pod_fin_h + 0.008
    s0_pod_offset = core_r + s0_pod_r + s0_gap
    s1_pod_offset = core_r + s1_pod_r + s1_gap

    p = dict(
        nose_mass_kg=nose_mass,
        launch_azimuth=270.0, launch_angle_deg=3.0,
        wind_levels=WIND_LEVELS,
        s1_separation_delay=s1_sep_delay,
        
        # Stage 0
        s0_main=s0_main_d, s0_retro=s0_retro_d, s0_retro_delay=s0_retro_delay,
        s0_core_radius=core_r, s0_core_length=core_l, 
        s0_pod_radius=s0_pod_r, s0_pod_length=s0_pod_l, s0_pod_radial_offset=s0_pod_offset,
        s0_pod_fin_count=s0_pod_fin_c if s0_pod_fin_h > 0.005 else 0,
        s0_pod_fin_height=s0_pod_fin_h, s0_pod_fin_root=s0_pod_l * 0.15,
        s0_pod_nose_shape="ogive",
        s0_core_fin_count=s0_core_fin_c,
        s0_core_fin_root=max(0.10, core_r * 4.0),
        s0_core_fin_height=max(0.08, core_r * 3.0) * s0_core_fin_h_scale,
        s0_ring_thickness_m=0.004, s0_ring_fwd_material="fiberglass", s0_ring_aft_material="aluminum",
        s0_ballast_kg=s0_ballast_kg,
        
        # Stage 1
        s1_main=s1_main_d, s1_retro=s1_retro_d, s1_retro_delay=s1_retro_delay,
        s1_core_radius=core_r, s1_core_length=core_l, 
        s1_pod_radius=s1_pod_r, s1_pod_length=s1_pod_l, s1_pod_radial_offset=s1_pod_offset,
        s1_pod_fin_count=s1_pod_fin_c if s1_pod_fin_h > 0.005 else 0,
        s1_pod_fin_height=s1_pod_fin_h, s1_pod_fin_root=s1_pod_l * 0.15,
        s1_pod_nose_shape="ogive",
        s1_core_fin_count=s1_core_fin_c,
        s1_core_fin_root=max(0.10, core_r * 4.0),
        s1_core_fin_height=max(0.08, core_r * 3.0) * s1_core_fin_h_scale,
        s1_ring_thickness_m=0.004, s1_ring_fwd_material="fiberglass", s1_ring_aft_material="aluminum",
        s1_ballast_kg=s1_ballast_kg,
        
        # Aliases for osifog_sweep.py's validate_hard_constraints (legacy structure)
        s0_body_rad=core_r, s1_body_rad=core_r,
        s0_body_len=core_l, s1_body_len=core_l,
    )
    return p

iteration_count = 0

def base_objective(x):
    global iteration_count
    iteration_count += 1
    
    try:
        p = build_genome(x)
        ork_xml = generate_podset_ork(p)
        metrics = run_sim(ork_xml, seed=SIM_SEED)
    except Exception as e:
        if iteration_count < 10:
            print(f"[Eval {iteration_count} FAILS] {e}", file=sys.stderr)
        return 9e9, None
        
    try:
        landings = metrics.get("stage_landings", [])
        if len(landings) < 2:
            metrics["stage_landings"] = [{"east_m": 0, "north_m": 0, "total_speed": 999.0}] * 2
            
        res = score_from_mission_contract(metrics, p)
        
        if res["is_legal"]:
            loss = 1000000.0 - res["score"]
        else:
            mach = metrics.get("mach", 999.0)
            apo = metrics.get("apogee_m", 0.0)
            segs = metrics.get("ascent_stability_segments", [])
            margins = [s["min_calibers"] for s in segs if s.get("min_calibers") is not None]
            min_margin = min(margins) if margins else -999.0
            
            s0_vel = metrics["stage_landings"][0]["total_speed"]
            s1_vel = metrics["stage_landings"][1]["total_speed"]
            
            loss = 1000000.0 + abs(apo - 3000.0) * 10.0
            if mach > 0.95: loss += (mach - 0.95) * 10000.0
            if min_margin < 1.5: loss += (1.5 - min_margin) * 20000.0
            if s0_vel > 5.0: loss += (s0_vel - 5.0) * 1000.0
            if s1_vel > 5.0: loss += (s1_vel - 5.0) * 1000.0
            
        return loss, res
    except Exception as e:
        if iteration_count < 10:
            print(f"[Eval {iteration_count} SCORING FAILS] {e}", file=sys.stderr)
        return 9e9, None

def objective(x):
    loss, res = base_objective(x)
    
    # Memetic Algorithm: Baldwinian local search for hoverslam delay window
    # If the geometry is physically sound (loss < 1,050,000, meaning apogee and stability are good,
    # and the remaining penalty is purely the ~39k landing velocity penalty), we freeze the geometry
    # and run a rapid 2D sweep purely on the ignition delays to find the exact millisecond.
    if loss < 1050000.0:
        print(f"!!! ELITE GEOMETRY FOUND (Loss: {loss:.1f}). Running local delay sweep... !!!", file=sys.stderr)
        import scipy.optimize as opt
        
        def delay_obj(delays):
            x_local = list(x)
            x_local[19] = delays[0]
            x_local[20] = delays[1]
            local_loss, _ = base_objective(x_local)
            return local_loss
            
        local_res = opt.minimize(
            delay_obj, 
            [x[19], x[20]], 
            method='Nelder-Mead', 
            options={'maxiter': 15, 'xatol': 0.1, 'fatol': 100}
        )
        if local_res.fun < loss:
            print(f"!!! LOCAL SWEEP IMPROVED LOSS: {loss:.1f} -> {local_res.fun:.1f} !!!", file=sys.stderr)
            # Update the x array in-place so DE adopts the exact delays (Lamarckian crossover)
            x[19] = local_res.x[0]
            x[20] = local_res.x[1]
            loss = local_res.fun
            
    if iteration_count % 10 == 0 or (res and res.get("is_legal")):
        print(f"[Eval {iteration_count}] Legal={res['is_legal'] if res else False} Loss={loss:.1f}", file=sys.stderr)
        
    return loss

def main():
    bounds = [
        (0, len(MAIN_MOTORS)-1),   # s0_main
        (0, len(MAIN_MOTORS)-1),   # s1_main
        (0, len(RETRO_MOTORS)-1),  # s0_retro
        (0, len(RETRO_MOTORS)-1),  # s1_retro
        (0.0, 0.05), # core_r_extra
        (0.05, 0.2), # pod_l_extra
        (1.5, 3.0), # core_l_scale
        (0.05, 0.25), # nose_mass
        (0.0, 0.6), # s0_ballast_kg
        (0.0, 0.6), # s1_ballast_kg
        (0.0, 1.0), # s0_pod_fin_h_frac
        (0.0, 1.0), # s1_pod_fin_h_frac
        (0, 1), # s0_pod_fin_c
        (0, 1), # s1_pod_fin_c
        (1.0, 2.0), # s0_core_fin_h_scale
        (1.0, 2.0), # s1_core_fin_h_scale
        (3, 4), # s0_core_fin_c
        (3, 4), # s1_core_fin_c
        (0.0, 2.0), # s1_sep_delay
        (20.0, 60.0), # s0_retro_delay
        (20.0, 60.0), # s1_retro_delay
    ]
    
    print("Starting Official Differential Evolution search...", file=sys.stderr)
    # Fast initial population for quick feedback
    result = differential_evolution(objective, bounds, maxiter=20, popsize=5, disp=True)
    
    print("\nEvolution complete!")
    print(f"Best Loss: {result.fun}")
    
    p = build_genome(result.x)
    ork_xml = generate_podset_ork(p)
    out = "designs/osifog_level3/octaweb_experiment/podset_official_ga.ork"
    save_simulated_ork(ork_xml, out, seed=SIM_SEED)
    print(f"Saved best evolutionary candidate to {out}")

if __name__ == "__main__":
    main()
