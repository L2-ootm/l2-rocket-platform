"""Experiment A: Anti-Tumble pre-event invariance.

Compare OpenRocket simulation with and without the anti-tumble listener.
Verify no state mutation before the TUMBLE event.
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from osifog_sweep import (
    init_or, generate_ork, ANTI_TUMBLE_SCRIPT, SIM_SEED,
    _seed_multilevel_wind, _load_ork_doc, parse_wind_csv, WIND_CSV,
    _get_anti_tumble_listener, LAUNCH_LAT, LAUNCH_LON, LAUNCH_ALT,
    TEMP_K, PRESSURE_PA, LAUNCH_ROD_M
)

# Use the current authority candidate parameters
CANDIDATE = {
    "s0_main": 37, "s1_main": 18, "s0_retro": 19, "s1_retro": 19,
    "main_cluster_count": 3,
    "s0_body_rad": 0.074, "s1_body_rad": 0.074,
    "s0_body_len": 0.70, "s1_body_len": 0.75,
    "s1_separation_delay": 0.0,
    "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
    "nose_mass_kg": 1.72, "nose_ballast_pos_m": 0.45,
    "s0_mid_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0,
    "s0_aft_ballast_kg": 0.0, "s1_aft_ballast_kg": 2.725,
    "s1_aft_ballast_pos_m": 0.084, "s1_aft_ballast_rod_radius_m": 0.014,
    "s1_aft_ballast_attachment": "central_bonded",
    "s0_fin_count": 4, "s0_fin_root": 0.20, "s0_fin_height": 0.25, "s0_fin_sweep": 10.0,
    "s1_fin_count": 4, "s1_fin_root": 0.24, "s1_fin_height": 0.38, "s1_fin_sweep": 0.05,
    "s1_grid_fin_count": 4, "s1_grid_fin_root": 0.10, "s1_grid_fin_height": 0.08,
    "s1_grid_fin_position_m": 0.03,
    "s0_grid_fin_count": 0, "s0_grid_fin_root": 0.06, "s0_grid_fin_height": 0.06,
    "s0_grid_fin_position_m": 0.03,
    "s0_fin_thickness_m": 0.003, "s1_fin_thickness_m": 0.003,
    "s0_grid_fin_thickness_m": 0.001, "s1_grid_fin_thickness_m": 0.003,
    "s0_fin_material": "fiberglass", "s1_fin_material": "fiberglass",
    "s0_grid_fin_material": "fiberglass", "s1_grid_fin_material": "fiberglass",
    "launch_azimuth": 34.0, "launch_angle_deg": 3.85,
    "wind_levels": parse_wind_csv(WIND_CSV),
}


def run_simulation(with_listener=True):
    """Run one simulation and return branch telemetry."""
    import jpype
    
    init_or()
    ork_xml = generate_ork(CANDIDATE)
    
    fd, path = tempfile.mkstemp(suffix=".ork")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        
        if with_listener:
            listener = _get_anti_tumble_listener()
            sim.simulate(listener)
        else:
            sim.simulate()
        
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
        
        branches = []
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            n = int(branch.getLength())
            times = [float(branch.get(fdt.TYPE_TIME)[i]) for i in range(n)]
            altitudes = [float(branch.get(fdt.TYPE_ALTITUDE)[i]) for i in range(n)]
            positions_x = [float(branch.get(fdt.TYPE_POSITION_X)[i]) for i in range(n)]
            positions_y = [float(branch.get(fdt.TYPE_POSITION_Y)[i]) for i in range(n)]
            velocities_z = [float(branch.get(fdt.TYPE_VELOCITY_Z)[i]) for i in range(n)]
            velocities_xy = [float(branch.get(fdt.TYPE_VELOCITY_XY)[i]) for i in range(n)]
            masses = [float(branch.get(fdt.TYPE_MASS)[i]) for i in range(n)]
            thetas = [float(branch.get(fdt.TYPE_ORIENTATION_THETA)[i]) for i in range(n)]
            phis = [float(branch.get(fdt.TYPE_ORIENTATION_PHI)[i]) for i in range(n)]
            
            # Find TUMBLE event
            tumble_time = None
            for event in branch.getEvents():
                if event.getType().name() == "TUMBLE":
                    tumble_time = float(event.getTime())
                    break
            
            # Find GROUND_HIT event
            ground_hit_time = None
            for event in branch.getEvents():
                if event.getType() == FlightEvent.Type.GROUND_HIT:
                    ground_hit_time = float(event.getTime())
                    break
            
            branches.append({
                "name": str(branch.getName()),
                "length": n,
                "times": times,
                "altitudes": altitudes,
                "positions_x": positions_x,
                "positions_y": positions_y,
                "velocities_z": velocities_z,
                "velocities_xy": velocities_xy,
                "masses": masses,
                "thetas": thetas,
                "phis": phis,
                "tumble_time": tumble_time,
                "ground_hit_time": ground_hit_time,
            })
        
        return branches
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def compare_branches(with_listener, without_listener, tolerance_abs=1e-6, tolerance_rel=1e-6):
    """Compare branch telemetry before TUMBLE event."""
    results = []
    
    for i, (b_on, b_off) in enumerate(zip(with_listener, without_listener)):
        # Find the earlier TUMBLE time
        tumble_on = b_on["tumble_time"]
        tumble_off = b_off["tumble_time"]
        
        if tumble_off is not None:
            # Use the no-listener tumble time as the cutoff
            cutoff = tumble_off
        elif tumble_on is not None:
            cutoff = tumble_on
        else:
            # No tumble in either run — compare all samples
            cutoff = float("inf")
        
        # Find common sample count before cutoff
        common_count = 0
        for j in range(min(b_on["length"], b_off["length"])):
            if b_on["times"][j] > cutoff + 1e-9:
                break
            common_count = j + 1
        
        if common_count == 0:
            results.append({
                "branch": i,
                "name": b_on["name"],
                "status": "NO_COMMON_SAMPLES",
                "tumble_on": tumble_on,
                "tumble_off": tumble_off,
            })
            continue
        
        # Compare each field
        field_differences = {}
        for field in ["altitudes", "positions_x", "positions_y", "velocities_z", 
                       "velocities_xy", "masses", "thetas", "phis"]:
            on_vals = b_on[field][:common_count]
            off_vals = b_off[field][:common_count]
            
            max_abs_diff = max(abs(a - b) for a, b in zip(on_vals, off_vals))
            max_rel_diff = max(
                abs(a - b) / max(abs(a), abs(b), 1e-12)
                for a, b in zip(on_vals, off_vals)
            )
            
            field_differences[field] = {
                "max_abs_diff": max_abs_diff,
                "max_rel_diff": max_rel_diff,
                "within_abs_tol": max_abs_diff <= tolerance_abs,
                "within_rel_tol": max_rel_diff <= tolerance_rel,
            }
        
        all_within = all(
            d["within_abs_tol"] or d["within_rel_tol"]
            for d in field_differences.values()
        )
        
        results.append({
            "branch": i,
            "name": b_on["name"],
            "common_samples": common_count,
            "tumble_on": tumble_on,
            "tumble_off": tumble_off,
            "tumble_time_match": (
                abs(tumble_on - tumble_off) < 1e-6
                if tumble_on is not None and tumble_off is not None
                else None
            ),
            "field_differences": field_differences,
            "all_within_tolerance": all_within,
        })
    
    return results


def main():
    print("Experiment A: Anti-Tumble Pre-Event Invariance")
    print("=" * 60)
    print(f"Candidate: s0_main={CANDIDATE['s0_main']}, s1_main={CANDIDATE['s1_main']}")
    print(f"Seed: {SIM_SEED}")
    print()
    
    print("Running simulation WITH listener...")
    branches_on = run_simulation(with_listener=True)
    print(f"  Branches: {len(branches_on)}")
    for b in branches_on:
        print(f"  {b['name']}: {b['length']} samples, tumble={b['tumble_time']}, ground_hit={b['ground_hit_time']}")
    
    print()
    print("Running simulation WITHOUT listener...")
    branches_off = run_simulation(with_listener=False)
    print(f"  Branches: {len(branches_off)}")
    for b in branches_off:
        print(f"  {b['name']}: {b['length']} samples, tumble={b['tumble_time']}, ground_hit={b['ground_hit_time']}")
    
    print()
    print("Comparing pre-TUMBLE samples...")
    comparison = compare_branches(branches_on, branches_off)
    
    for r in comparison:
        status = "PASS" if r.get("all_within_tolerance") else "FAIL"
        print(f"  Branch {r['branch']} ({r['name']}): {status}")
        print(f"    Common samples: {r['common_samples']}")
        print(f"    Tumble (with listener): {r['tumble_on']}")
        print(f"    Tumble (without listener): {r['tumble_off']}")
        if r.get("tumble_time_match") is not None:
            print(f"    Tumble time match: {r['tumble_time_match']}")
        for field, diff in r.get("field_differences", {}).items():
            marker = "OK" if diff["within_abs_tol"] or diff["within_rel_tol"] else "DIFF"
            print(f"    {field}: max_abs={diff['max_abs_diff']:.2e}, max_rel={diff['max_rel_diff']:.2e} [{marker}]")
    
    # Save results
    output = Path("docs/research/osifog-2026-deep-audit/experiments")
    output.mkdir(parents=True, exist_ok=True)
    artifact = {
        "experiment": "A_anti_tumble_invariance",
        "candidate": {k: v for k, v in CANDIDATE.items() if k != "wind_levels"},
        "seed": SIM_SEED,
        "tolerance_abs": 1e-6,
        "tolerance_rel": 1e-6,
        "branches_on": [
            {"name": b["name"], "length": b["length"], "tumble_time": b["tumble_time"], 
             "ground_hit_time": b["ground_hit_time"]}
            for b in branches_on
        ],
        "branches_off": [
            {"name": b["name"], "length": b["length"], "tumble_time": b["tumble_time"],
             "ground_hit_time": b["ground_hit_time"]}
            for b in branches_off
        ],
        "comparison": comparison,
        "overall": all(r.get("all_within_tolerance", False) for r in comparison),
    }
    (output / "antitumble_invariance.json").write_text(
        json.dumps(artifact, indent=2), encoding="utf-8"
    )
    print(f"\nResults saved to {output / 'antitumble_invariance.json'}")
    
    overall = artifact["overall"]
    print(f"\nOVERALL: {'PASS — pre-TUMBLE invariance confirmed' if overall else 'FAIL — differences detected'}")


if __name__ == "__main__":
    main()
