"""Gate 7 Round 2: Subsonic-focused stability search with weaker motors."""
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
    parse_wind_csv, WIND_CSV, validate_candidate_geometry,
    _get_anti_tumble_listener, MIN_STATIC_MARGIN, MAX_MACH
)

# Weaker motor options
MOTOR_OPTIONS = [
    {"s0_main": 14, "s1_main": 14, "label": "J350W×3"},  # 690 Ns each
    {"s0_main": 15, "s1_main": 15, "label": "J420R×3"},  # 651 Ns each
    {"s0_main": 18, "s1_main": 18, "label": "J360×3"},    # 816 Ns each
    {"s0_main": 14, "s1_main": 18, "label": "J350W/J360"},  # Mixed
]

# Body radius options
BODY_RADII = [0.074, 0.080, 0.085]

# Fin configurations
FIN_CONFIGS = [
    {"s0_fin_count": 4, "s0_fin_root": 0.15, "s0_fin_height": 0.20, "s0_fin_sweep": 8.0,
     "s1_fin_count": 4, "s1_fin_root": 0.22, "s1_fin_height": 0.38, "s1_fin_sweep": 5.0,
     "s1_grid_fin_count": 0, "label": "4f_aft_only"},
    {"s0_fin_count": 4, "s0_fin_root": 0.15, "s0_fin_height": 0.20, "s0_fin_sweep": 8.0,
     "s1_fin_count": 4, "s1_fin_root": 0.22, "s1_fin_height": 0.38, "s1_fin_sweep": 5.0,
     "s1_grid_fin_count": 3, "s1_grid_fin_root": 0.06, "s1_grid_fin_height": 0.06,
     "s1_grid_fin_position_m": 0.05, "label": "4f_aft_plus_3grid"},
]

# Ballast options
BALLAST_CONFIGS = [
    {"nose_mass_kg": 1.5, "s1_aft_ballast_kg": 1.5, "label": "balanced"},
    {"nose_mass_kg": 2.5, "s1_aft_ballast_kg": 0.5, "label": "heavy_nose"},
    {"nose_mass_kg": 1.0, "s1_aft_ballast_kg": 2.5, "label": "heavy_aft"},
]

BASE = {
    "s0_retro": 19, "s1_retro": 19,
    "main_cluster_count": 3,
    "s0_body_len": 0.75, "s1_body_len": 0.80,
    "s1_separation_delay": 0.0,
    "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
    "nose_ballast_pos_m": 0.45, "nose_length_m": 0.50,
    "s0_mid_ballast_kg": 0.0, "s0_aft_ballast_kg": 0.0,
    "s0_fin_thickness_m": 0.003, "s1_fin_thickness_m": 0.003,
    "s0_grid_fin_thickness_m": 0.001, "s1_grid_fin_thickness_m": 0.001,
    "s0_grid_fin_count": 0, "s0_grid_fin_root": 0.06,
    "s0_grid_fin_height": 0.06, "s0_grid_fin_position_m": 0.03,
    "s0_grid_fin_sweep": 0.0, "s1_grid_fin_sweep": 0.0,
    "s0_grid_fin_material": "fiberglass", "s1_grid_fin_material": "fiberglass",
    "s0_fin_material": "fiberglass", "s1_fin_material": "fiberglass",
    "launch_azimuth": 34.0, "launch_angle_deg": 3.85,
    "wind_levels": parse_wind_csv(WIND_CSV),
}


def run_exposed_ascent(fixture):
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
        ascent_stability = [
            float(stab0[i])
            for i in range(apex_idx + 1)
            if float(vz0[i]) > 0.01 and math.isfinite(float(stab0[i]))
        ]
        mach = float(data.getMaxMachNumber())
        apogee = float(data.getMaxAltitude())
        min_margin = min(ascent_stability) if ascent_stability else float("-inf")
        events = {}
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            for event in branch.getEvents():
                events.setdefault(str(event.getType().name()), []).append(float(event.getTime()))
        separations = events.get("STAGE_SEPARATION", [])
        apogees = events.get("APOGEE", [])
        genuine_staging = separations and apogees and min(separations) < min(apogees)
        return {
            "mach": mach, "apogee_m": apogee, "min_stability_cal": min_margin,
            "genuine_staging": bool(genuine_staging),
        }
    finally:
        try: os.unlink(path)
        except: pass


def main():
    print("Gate 7 Round 2: Subsonic Stability Search")
    print("=" * 60)
    
    results = []
    tested = 0
    
    for motor in MOTOR_OPTIONS:
        for body_rad in BODY_RADII:
            for fin in FIN_CONFIGS:
                for ballast in BALLAST_CONFIGS:
                    if tested >= 20:  # Budget limit
                        break
                    
                    fixture = dict(BASE)
                    fixture.update(motor)
                    fixture["s0_body_rad"] = body_rad
                    fixture["s1_body_rad"] = body_rad
                    fixture.update({k: v for k, v in fin.items() if k != "label"})
                    fixture.update({k: v for k, v in ballast.items() if k != "label"})
                    
                    label = f"{motor['label']}/r{body_rad*1000:.0f}/{fin['label']}/{ballast['label']}"
                    
                    violations = validate_candidate_geometry(fixture)
                    if violations:
                        results.append({"label": label, "status": "GEOMETRY_REJECTED"})
                        continue
                    
                    try:
                        t0 = time.time()
                        result = run_exposed_ascent(fixture)
                        elapsed = time.time() - t0
                        tested += 1
                        
                        mach_ok = result["mach"] < MAX_MACH
                        margin_ok = result["min_stability_cal"] >= MIN_STATIC_MARGIN
                        
                        if mach_ok and margin_ok:
                            status = "STABLE_SUBSONIC"
                        elif margin_ok:
                            status = "STABLE_SUPERSONIC"
                        elif mach_ok:
                            status = "UNSTABLE_SUBSONIC"
                        else:
                            status = "UNSTABLE_SUPERSONIC"
                        
                        r = {
                            "label": label, "status": status,
                            "mach": round(result["mach"], 4),
                            "margin": round(result["min_stability_cal"], 4),
                            "apogee": round(result["apogee_m"], 1),
                            "staging": result["genuine_staging"],
                            "time_s": round(elapsed, 1),
                        }
                        results.append(r)
                        
                        marker = "***" if status == "STABLE_SUBSONIC" else "   "
                        print(f"{marker} {label:<50} mach={result['mach']:.3f} margin={result['min_stability_cal']:.3f} [{status}]")
                    except Exception as e:
                        results.append({"label": label, "status": "ERROR", "error": str(e)[:80]})
        
        if tested >= 20:
            break
    
    stable = [r for r in results if r["status"] == "STABLE_SUBSONIC"]
    print(f"\n{'='*60}")
    print(f"Tested: {tested}, Stable subsonic: {len(stable)}")
    if stable:
        print("STABLE CANDIDATES:")
        for r in stable:
            print(f"  {r['label']}: mach={r['mach']}, margin={r['margin']}, apogee={r['apogee']}m")
    
    artifact = {"test": "stability_matrix_round2", "tested": tested, "stable": len(stable), "results": results}
    with open("artifacts/phase2/stability-matrix.json", "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"\nArtifact written to artifacts/phase2/stability-matrix.json")


if __name__ == "__main__":
    main()
