"""Gate E+F: Free descent diagnostics and powered landing for best stable candidate.

Best candidate: J350W×3, nose=4.0kg, margin=1.745 cal, mach=0.836
"""
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
os.environ.setdefault("RAYON_NUM_THREADS", "1")

import jpype
import motor_data
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, validate_hard_constraints, score_official,
    _get_anti_tumble_listener, validate_candidate_geometry,
    _minimum_initial_ascent_stability, MIN_STATIC_MARGIN, MAX_MACH,
    _descent_alignment_diagnostic, _retro_burn_diagnostic,
)

# Best stable candidate from Gate 7
BEST_CANDIDATE = {
    "s0_main": 14, "s1_main": 14,  # J350W×3
    "s0_retro": 19, "s1_retro": 19,  # K550W
    "main_cluster_count": 3,
    "s0_body_rad": 0.074, "s1_body_rad": 0.074,
    "s0_body_len": 0.75, "s1_body_len": 0.80,
    "s1_separation_delay": 0.0,
    "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
    "nose_mass_kg": 4.0, "nose_ballast_pos_m": 0.45, "nose_length_m": 0.50,
    "s0_mid_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0,
    "s0_aft_ballast_kg": 0.0, "s1_aft_ballast_kg": 0.5,
    "s0_fin_count": 4, "s0_fin_root": 0.15, "s0_fin_height": 0.20, "s0_fin_sweep": 8.0,
    "s1_fin_count": 4, "s1_fin_root": 0.22, "s1_fin_height": 0.38, "s1_fin_sweep": 5.0,
    "s1_grid_fin_count": 0, "s1_grid_fin_root": 0.06, "s1_grid_fin_height": 0.06,
    "s1_grid_fin_position_m": 0.03,
    "s0_grid_fin_count": 0, "s0_grid_fin_root": 0.06, "s0_grid_fin_height": 0.06,
    "s0_grid_fin_position_m": 0.03,
    "s0_fin_thickness_m": 0.003, "s1_fin_thickness_m": 0.003,
    "s0_grid_fin_thickness_m": 0.001, "s1_grid_fin_thickness_m": 0.001,
    "s0_fin_material": "fiberglass", "s1_fin_material": "fiberglass",
    "s0_grid_fin_material": "fiberglass", "s1_grid_fin_material": "fiberglass",
    "launch_azimuth": 34.0, "launch_angle_deg": 3.85,
    "wind_levels": parse_wind_csv(WIND_CSV),
}


def run_free_descent(fixture):
    """Run free-descent diagnostic (retro motors disabled)."""
    init_or()
    # Set retro delays to very late (effectively disabled)
    fd_params = dict(fixture)
    fd_params["s0_retro_delay"] = 200.0
    fd_params["s1_retro_delay"] = 200.0
    
    ork_xml = generate_ork(fd_params)
    fd, path = tempfile.mkstemp(suffix=".ork")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()
        
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
        
        results = []
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            n = int(branch.getLength())
            branch_name = str(branch.getName())
            
            times = branch.get(fdt.TYPE_TIME)
            altitudes = branch.get(fdt.TYPE_ALTITUDE)
            positions_x = branch.get(fdt.TYPE_POSITION_X)
            positions_y = branch.get(fdt.TYPE_POSITION_Y)
            velocities_z = branch.get(fdt.TYPE_VELOCITY_Z)
            velocities_xy = branch.get(fdt.TYPE_VELOCITY_XY)
            thetas = branch.get(fdt.TYPE_ORIENTATION_THETA)
            phis = branch.get(fdt.TYPE_ORIENTATION_PHI)
            
            # Find ground hit
            hit_time = None
            for ev in branch.getEvents():
                if ev.getType() == FlightEvent.Type.GROUND_HIT:
                    hit_time = float(ev.getTime())
                    break
            
            if hit_time is None:
                continue
            
            # Find apex
            apex_idx = max(range(n), key=lambda i: float(altitudes[i]))
            
            # Run alignment diagnostic
            alignment = _descent_alignment_diagnostic(
                times, altitudes, positions_x, positions_y,
                velocities_z, velocities_xy, thetas, phis,
            )
            
            # Find tail-first windows
            windows = alignment.get("tail_first_windows", [])
            
            # Get touchdown state
            idx = 1
            for i in range(1, n):
                if float(times[i]) >= hit_time:
                    idx = i
                    break
            t1, t2 = float(times[idx-1]), float(times[idx])
            f = (hit_time - t1) / (t2 - t1) if t2 > t1 else 1.0
            vz = float(velocities_z[idx-1]) + f * (float(velocities_z[idx]) - float(velocities_z[idx-1]))
            vxy = float(velocities_xy[idx-1]) + f * (float(velocities_xy[idx]) - float(velocities_xy[idx-1]))
            total_speed = math.sqrt(vz**2 + vxy**2)
            
            results.append({
                "branch": bi,
                "name": branch_name,
                "samples": n,
                "apex_time_s": float(times[apex_idx]),
                "apex_altitude_m": float(altitudes[apex_idx]),
                "hit_time_s": hit_time,
                "touchdown_vz_ms": vz,
                "touchdown_vxy_ms": vxy,
                "touchdown_total_speed_ms": total_speed,
                "tail_first_windows": len(windows),
                "best_alignment_q": alignment.get("best_alignment_q", -1),
                "alignment_candidates": len(alignment.get("alignment_candidates", [])),
            })
        
        return {
            "branches": results,
            "mach": float(data.getMaxMachNumber()),
            "apogee": float(data.getMaxAltitude()),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_powered_landing(fixture, retro_delay_s0, retro_delay_s1):
    """Run powered landing with specific retro delays."""
    init_or()
    powered = dict(fixture)
    powered["s0_retro_delay"] = retro_delay_s0
    powered["s1_retro_delay"] = retro_delay_s1
    
    ork_xml = generate_ork(powered)
    fd, path = tempfile.mkstemp(suffix=".ork")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()
        
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
        
        landings = []
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            n = int(branch.getLength())
            times = branch.get(fdt.TYPE_TIME)
            altitudes = branch.get(fdt.TYPE_ALTITUDE)
            positions_x = branch.get(fdt.TYPE_POSITION_X)
            positions_y = branch.get(fdt.TYPE_POSITION_Y)
            velocities_z = branch.get(fdt.TYPE_VELOCITY_Z)
            velocities_xy = branch.get(fdt.TYPE_VELOCITY_XY)
            
            hit_time = None
            for ev in branch.getEvents():
                if ev.getType() == FlightEvent.Type.GROUND_HIT:
                    hit_time = float(ev.getTime())
                    break
            if hit_time is None:
                continue
            
            idx = 1
            for i in range(1, n):
                if float(times[i]) >= hit_time:
                    idx = i
                    break
            t1, t2 = float(times[idx-1]), float(times[idx])
            f = (hit_time - t1) / (t2 - t1) if t2 > t1 else 1.0
            vz = float(velocities_z[idx-1]) + f * (float(velocities_z[idx]) - float(velocities_z[idx-1]))
            vxy = float(velocities_xy[idx-1]) + f * (float(velocities_xy[idx]) - float(velocities_xy[idx-1]))
            total_speed = math.sqrt(vz**2 + vxy**2)
            
            landings.append({
                "branch": bi,
                "name": str(branch.getName()),
                "time_s": hit_time,
                "vz_ms": vz,
                "vxy_ms": vxy,
                "total_speed": total_speed,
                "total_speed_ms": total_speed,
                "east_m": float(positions_x[idx-1]) + f * (float(positions_x[idx]) - float(positions_x[idx-1])),
                "north_m": float(positions_y[idx-1]) + f * (float(positions_y[idx]) - float(positions_y[idx-1])),
                "dist_m": math.sqrt(
                    (float(positions_x[idx-1]) + f * (float(positions_x[idx]) - float(positions_x[idx-1])))**2 +
                    (float(positions_y[idx-1]) + f * (float(positions_y[idx]) - float(positions_y[idx-1])))**2
                ),
            })
        
        # Check legal constraints
        mach = float(data.getMaxMachNumber())
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        stab0 = br0.get(fdt.TYPE_STABILITY)
        vz0 = br0.get(fdt.TYPE_VELOCITY_Z)
        apex_idx = max(range(n0), key=lambda i: float(alt0[i]))
        ascent_stab = [float(stab0[i]) for i in range(apex_idx+1) if float(vz0[i]) > 0.01 and math.isfinite(float(stab0[i]))]
        min_margin = min(ascent_stab) if ascent_stab else float("-inf")
        
        legal, violations = validate_hard_constraints(
            {"mach": mach, "min_static_margin": min_margin, "status": "SIMULATED",
             "stage_landings": landings, "event_times": {}, "branch_event_times": []},
            fixture,
        )
        
        return {
            "landings": landings,
            "mach": mach,
            "min_margin": min_margin,
            "legal": legal,
            "violations": violations,
            "retro_delay_s0": retro_delay_s0,
            "retro_delay_s1": retro_delay_s1,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    print("Gate E+F: Free Descent and Powered Landing")
    print("=" * 60)
    print(f"Candidate: J350W×3, nose={BEST_CANDIDATE['nose_mass_kg']}kg")
    print(f"Body: {BEST_CANDIDATE['s0_body_rad']*1000:.0f}mm radius")
    
    # Gate E: Free descent
    print("\n--- Gate E: Free Descent ---")
    fd_result = run_free_descent(BEST_CANDIDATE)
    print(f"  Mach: {fd_result['mach']:.4f}")
    print(f"  Apogee: {fd_result['apogee']:.1f} m")
    for b in fd_result["branches"]:
        print(f"  {b['name']}: touchdown={b['touchdown_total_speed_ms']:.2f} m/s, "
              f"windows={b['tail_first_windows']}, best_q={b['best_alignment_q']:.3f}")
    
    # Gate F: Powered landing — coarse delay search
    print("\n--- Gate F: Powered Landing ---")
    
    # Get impact times from free descent
    s0_impact = fd_result["branches"][0]["hit_time_s"] if len(fd_result["branches"]) > 0 else 20.0
    s1_impact = fd_result["branches"][1]["hit_time_s"] if len(fd_result["branches"]) > 1 else 15.0
    
    # Get motor burn time
    retro = motor_data.load_motor_by_index(19)  # K550W
    burn_time = retro.burn_duration_s
    print(f"  Retro motor: K550W, burn={burn_time:.3f}s")
    print(f"  S0 impact time: {s0_impact:.3f}s")
    print(f"  S1 impact time: {s1_impact:.3f}s")
    
    # Coarse search: sweep delays around ideal (impact - burn - buffer)
    best_speed = float("inf")
    best_delay_s0 = None
    best_delay_s1 = None
    
    for delay_offset in [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0]:
        d_s0 = s0_impact - burn_time + delay_offset
        d_s1 = s1_impact - burn_time + delay_offset
        
        if d_s0 < 0 or d_s1 < 0:
            continue
        
        try:
            result = run_powered_landing(BEST_CANDIDATE, d_s0, d_s1)
            if len(result["landings"]) >= 2:
                speeds = [l["total_speed_ms"] for l in result["landings"][:2]]
                max_speed = max(speeds)
                print(f"    delay_offset={delay_offset:+.1f}s: s0={speeds[0]:.2f} s1={speeds[1]:.2f} max={max_speed:.2f} legal={result['legal']}")
                if max_speed < best_speed:
                    best_speed = max_speed
                    best_delay_s0 = d_s0
                    best_delay_s1 = d_s1
        except Exception as e:
            print(f"    delay_offset={delay_offset:+.1f}s: ERROR {str(e)[:60]}")
    
    if best_delay_s0 is not None:
        print(f"\n  Best coarse: s0_delay={best_delay_s0:.3f}s, s1_delay={best_delay_s1:.3f}s, speed={best_speed:.2f} m/s")
        
        # Fine search around best
        print("\n  Fine search (100ms steps)...")
        fine_best = best_speed
        fine_d0, fine_d1 = best_delay_s0, best_delay_s1
        for offset in [-0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2]:
            d0 = best_delay_s0 + offset
            d1 = best_delay_s1 + offset
            if d0 < 0 or d1 < 0:
                continue
            try:
                result = run_powered_landing(BEST_CANDIDATE, d0, d1)
                if len(result["landings"]) >= 2:
                    speeds = [l["total_speed_ms"] for l in result["landings"][:2]]
                    max_speed = max(speeds)
                    if max_speed < fine_best:
                        fine_best = max_speed
                        fine_d0, fine_d1 = d0, d1
                        print(f"    offset={offset:+.3f}s: speeds={[f'{s:.2f}' for s in speeds]} [{max_speed:.2f}]")
            except:
                pass
        
        print(f"\n  Best fine: s0_delay={fine_d0:.3f}s, s1_delay={fine_d1:.3f}s, speed={fine_best:.2f} m/s")
        
        # Final validation
        final = run_powered_landing(BEST_CANDIDATE, fine_d0, fine_d1)
        speeds = [l["total_speed_ms"] for l in final["landings"][:2]] if len(final["landings"]) >= 2 else []
        is_legal = all(s < 5.0 for s in speeds) and final["legal"] and final["mach"] < MAX_MACH and final["min_margin"] >= MIN_STATIC_MARGIN
        
        print(f"\n  Final validation:")
        print(f"    Speeds: {[f'{s:.2f}' for s in speeds]} m/s")
        print(f"    Mach: {final['mach']:.4f}")
        print(f"    Margin: {final['min_margin']:.4f} cal")
        print(f"    Legal: {final['legal']}")
        print(f"    VIOLATIONS: {final['violations']}")
        print(f"    LEGAL BRANCH: {'YES' if is_legal else 'NO'}")
    else:
        print("\n  No powered landing candidate found")
    
    # Save artifact
    artifact = {
        "test": "free_descent_and_powered_landing",
        "candidate": {k: v for k, v in BEST_CANDIDATE.items() if k != "wind_levels"},
        "free_descent": fd_result,
        "powered_landing": {
            "best_delay_s0": fine_d0 if best_delay_s0 else None,
            "best_delay_s1": fine_d1 if best_delay_s0 else None,
            "best_speed": fine_best if best_delay_s0 else None,
            "is_legal_branch": is_legal if best_delay_s0 else False,
            "final_result": final if best_delay_s0 else None,
        },
    }
    with open("artifacts/phase2b/free-descent-results.json", "w") as f:
        json.dump(artifact, f, indent=2, default=str)
    with open("artifacts/phase2b/powered-landing-results.json", "w") as f:
        json.dump(artifact, f, indent=2, default=str)
    print(f"\nArtifacts written to artifacts/phase2b/")


if __name__ == "__main__":
    main()
