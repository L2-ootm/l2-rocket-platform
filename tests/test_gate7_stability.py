"""Gate 7: Bounded diverse stability and landing search.

Tests a deliberately diverse matrix of exposed-sustainer configurations
separating motor arrangement, aerodynamic topology, body geometry,
ballast distribution, and fin geometry.
"""
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")

import motor_data
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, validate_hard_constraints, score_official,
    _get_anti_tumble_listener, validate_candidate_geometry,
    _minimum_initial_ascent_stability, _falcon_cluster_geometry,
    MIN_STATIC_MARGIN, MAX_MACH
)


# Diverse topology families
TOPOLOGIES = [
    # Family 1: Standard 3+1 with aft-only fins (no forward fins)
    {
        "name": "aft_only_fins",
        "s0_fin_count": 4, "s0_fin_root": 0.15, "s0_fin_height": 0.18,
        "s0_fin_sweep": 10.0, "s0_grid_fin_count": 0,
        "s1_fin_count": 4, "s1_fin_root": 0.20, "s1_fin_height": 0.35,
        "s1_fin_sweep": 5.0, "s1_grid_fin_count": 0,
        "nose_mass_kg": 1.5, "s1_aft_ballast_kg": 2.0,
    },
    # Family 2: Aft fins + small forward surfaces
    {
        "name": "aft_plus_small_forward",
        "s0_fin_count": 4, "s0_fin_root": 0.15, "s0_fin_height": 0.18,
        "s0_fin_sweep": 10.0, "s0_grid_fin_count": 0,
        "s1_fin_count": 4, "s1_fin_root": 0.20, "s1_fin_height": 0.35,
        "s1_fin_sweep": 5.0, "s1_grid_fin_count": 3,
        "s1_grid_fin_root": 0.06, "s1_grid_fin_height": 0.06,
        "s1_grid_fin_position_m": 0.05,
        "nose_mass_kg": 1.5, "s1_aft_ballast_kg": 2.0,
    },
    # Family 3: Large aft fins, heavy nose ballast
    {
        "name": "large_aft_heavy_nose",
        "s0_fin_count": 4, "s0_fin_root": 0.18, "s0_fin_height": 0.22,
        "s0_fin_sweep": 8.0, "s0_grid_fin_count": 0,
        "s1_fin_count": 4, "s1_fin_root": 0.24, "s1_fin_height": 0.45,
        "s1_fin_sweep": 3.0, "s1_grid_fin_count": 0,
        "nose_mass_kg": 2.5, "s1_aft_ballast_kg": 1.0,
    },
    # Family 4: 6 aft fins, no forward, moderate ballast
    {
        "name": "six_aft_fins",
        "s0_fin_count": 6, "s0_fin_root": 0.12, "s0_fin_height": 0.15,
        "s0_fin_sweep": 12.0, "s0_grid_fin_count": 0,
        "s1_fin_count": 6, "s1_fin_root": 0.18, "s1_fin_height": 0.30,
        "s1_fin_sweep": 5.0, "s1_grid_fin_count": 0,
        "nose_mass_kg": 1.2, "s1_aft_ballast_kg": 2.5,
    },
    # Family 5: Standard with mid-body ballast
    {
        "name": "mid_ballast",
        "s0_fin_count": 4, "s0_fin_root": 0.15, "s0_fin_height": 0.20,
        "s0_fin_sweep": 8.0, "s0_grid_fin_count": 0,
        "s1_fin_count": 4, "s1_fin_root": 0.22, "s1_fin_height": 0.38,
        "s1_fin_sweep": 5.0, "s1_grid_fin_count": 0,
        "nose_mass_kg": 1.0, "s1_aft_ballast_kg": 1.5,
        "s1_mid_ballast_kg": 1.0,
    },
]

# Base parameters for all fixtures
BASE = {
    "s0_main": 16,  # J510W (available .eng file)
    "s1_main": 16,
    "s0_retro": 19,  # K550W
    "s1_retro": 19,
    "main_cluster_count": 3,
    "s0_body_rad": 0.074,
    "s1_body_rad": 0.074,
    "s0_body_len": 0.70,
    "s1_body_len": 0.75,
    "s1_separation_delay": 0.0,
    "s0_retro_delay": 200.0,
    "s1_retro_delay": 200.0,
    "nose_ballast_pos_m": 0.45,
    "nose_length_m": 0.50,
    "s0_mid_ballast_kg": 0.0,
    "s0_aft_ballast_kg": 0.0,
    "s0_fin_thickness_m": 0.003,
    "s1_fin_thickness_m": 0.003,
    "s0_grid_fin_thickness_m": 0.001,
    "s1_grid_fin_thickness_m": 0.001,
    "s0_grid_fin_root": 0.06,
    "s0_grid_fin_height": 0.06,
    "s0_grid_fin_position_m": 0.03,
    "s0_grid_fin_sweep": 0.0,
    "s1_grid_fin_sweep": 0.0,
    "s0_fin_material": "fiberglass",
    "s1_fin_material": "fiberglass",
    "s0_grid_fin_material": "fiberglass",
    "s1_grid_fin_material": "fiberglass",
    "launch_azimuth": 34.0,
    "launch_angle_deg": 3.85,
    "wind_levels": parse_wind_csv(WIND_CSV),
}


def run_exposed_ascent(fixture):
    """Run OpenRocket exposed-stage ascent simulation."""
    import jpype
    init_or()
    ork_xml = generate_ork(fixture)
    
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
        
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        vz0 = br0.get(fdt.TYPE_VELOCITY_Z)
        stab0 = br0.get(fdt.TYPE_STABILITY)
        times0 = br0.get(fdt.TYPE_TIME)
        
        apex_idx = max(range(n0), key=lambda i: float(alt0[i]))
        time_to_apogee = float(times0[apex_idx])
        
        # Extract stability during ascent (before apogee, while vz > 0)
        ascent_stability = [
            float(stab0[i])
            for i in range(apex_idx + 1)
            if float(vz0[i]) > 0.01 and math.isfinite(float(stab0[i]))
        ]
        
        mach = float(data.getMaxMachNumber())
        apogee = float(data.getMaxAltitude())
        min_margin = min(ascent_stability) if ascent_stability else float("-inf")
        
        # Check staging
        events = {}
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            for event in branch.getEvents():
                ename = str(event.getType().name())
                events.setdefault(ename, []).append(float(event.getTime()))
        
        separations = events.get("STAGE_SEPARATION", [])
        apogees = events.get("APOGEE", [])
        genuine_staging = (
            separations and apogees and min(separations) < min(apogees)
        ) if separations and apogees else False
        
        # Count branches with ground hits
        branches_with_ground = 0
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            for event in branch.getEvents():
                if event.getType() == FlightEvent.Type.GROUND_HIT:
                    branches_with_ground += 1
                    break
        
        return {
            "mach": mach,
            "apogee_m": apogee,
            "min_stability_cal": min_margin,
            "ascent_stability_count": len(ascent_stability),
            "genuine_staging": genuine_staging,
            "branches_with_ground": branches_with_ground,
            "time_to_apogee_s": time_to_apogee,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    print("Gate 7: Bounded Diverse Stability Search")
    print("=" * 60)
    
    all_results = []
    
    for topo in TOPOLOGIES:
        fixture = dict(BASE)
        fixture.update(topo)
        name = topo["name"]
        
        print(f"\n--- {name} ---")
        print(f"  Fins: s0={fixture['s0_fin_count']}f, s1={fixture['s1_fin_count']}f + {fixture.get('s1_grid_fin_count', 0)} grid")
        print(f"  Ballast: nose={fixture['nose_mass_kg']}kg, aft={fixture['s1_aft_ballast_kg']}kg")
        
        # Check geometry
        violations = validate_candidate_geometry(fixture)
        if violations:
            print(f"  GEOMETRY REJECTED: {violations[0]}")
            all_results.append({
                "name": name,
                "status": "GEOMETRY_REJECTED",
                "violations": violations,
            })
            continue
        
        try:
            t0 = time.time()
            result = run_exposed_ascent(fixture)
            elapsed = time.time() - t0
            
            # Classify
            mach_ok = result["mach"] < MAX_MACH
            margin_ok = result["min_stability_cal"] >= MIN_STATIC_MARGIN
            staging_ok = result["genuine_staging"]
            
            if mach_ok and margin_ok:
                status = "STABLE"
            elif margin_ok:
                status = "STABLE_BUT_SUPERSONIC"
            elif mach_ok:
                status = "UNSTABLE_SUBSONIC"
            else:
                status = "UNSTABLE_SUPERSONIC"
            
            print(f"  Mach: {result['mach']:.4f} ({'OK' if mach_ok else 'FAIL'})")
            print(f"  Min margin: {result['min_stability_cal']:.4f} cal ({'OK' if margin_ok else 'FAIL'})")
            print(f"  Apogee: {result['apogee_m']:.1f} m")
            print(f"  Staging: {'genuine' if staging_ok else 'not genuine'}")
            print(f"  Ground branches: {result['branches_with_ground']}")
            print(f"  Status: {status}")
            print(f"  Time: {elapsed:.1f}s")
            
            all_results.append({
                "name": name,
                "status": status,
                "mach": result["mach"],
                "min_stability_cal": result["min_stability_cal"],
                "apogee_m": result["apogee_m"],
                "genuine_staging": staging_ok,
                "branches_with_ground": result["branches_with_ground"],
                "time_s": elapsed,
            })
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results.append({
                "name": name,
                "status": "ERROR",
                "error": str(e),
            })
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    stable = [r for r in all_results if r["status"] == "STABLE"]
    print(f"Total topologies: {len(all_results)}")
    print(f"Stable subsonic: {len(stable)}")
    for r in all_results:
        print(f"  {r['name']:<30} {r['status']:<25} mach={r.get('mach', 'N/A'):<8} margin={r.get('min_stability_cal', 'N/A')}")
    
    # Save artifact
    artifact = {
        "test": "stability_matrix",
        "topologies_tested": len(all_results),
        "stable_subsonic": len(stable),
        "results": all_results,
    }
    with open("artifacts/phase2/stability-matrix.json", "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"\nArtifact written to artifacts/phase2/stability-matrix.json")


if __name__ == "__main__":
    main()
