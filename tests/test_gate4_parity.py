"""Gate 4: Phase-margin parity — compare Rust and OpenRocket on simple fixtures."""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
os.environ.setdefault("RAYON_NUM_THREADS", "1")

import motor_data
from rocket_forge import MOTOR_DATABASE
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, _get_anti_tumble_listener,
    _minimum_initial_ascent_stability
)

# Simple fixture: single-stage vehicle with known motor
# Using J510W (available .eng file)
FIXTURE = {
    "s0_main": 16,  # J510W
    "s0_retro": 19,  # K550W (will be disabled for exposed-stage test)
    "s1_main": 16,  # J510W (same motor for simplicity)
    "s1_retro": 19,
    "main_cluster_count": 3,  # OSIFOG 3+1 topology
    "s0_body_rad": 0.074,  # Standard OSIFOG 3+1 cluster radius
    "s1_body_rad": 0.074,
    "s0_body_len": 0.65,
    "s1_body_len": 0.65,
    "s1_separation_delay": 0.0,
    "s0_retro_delay": 200.0,
    "s1_retro_delay": 200.0,
    "nose_mass_kg": 0.3,
    "nose_ballast_pos_m": 0.20,
    "nose_length_m": 0.30,
    "s0_mid_ballast_kg": 0.0,
    "s1_mid_ballast_kg": 0.0,
    "s0_aft_ballast_kg": 0.0,
    "s1_aft_ballast_kg": 0.0,
    "s0_fin_count": 4,
    "s0_fin_root": 0.12,
    "s0_fin_height": 0.10,
    "s0_fin_sweep": 5.0,
    "s1_fin_count": 4,
    "s1_fin_root": 0.12,
    "s1_fin_height": 0.10,
    "s1_fin_sweep": 5.0,
    "s0_grid_fin_count": 0,
    "s1_grid_fin_count": 0,
    "s0_fin_thickness_m": 0.003,
    "s1_fin_thickness_m": 0.003,
    "s0_grid_fin_thickness_m": 0.001,
    "s1_grid_fin_thickness_m": 0.001,
    "s0_fin_material": "fiberglass",
    "s1_fin_material": "fiberglass",
    "s0_grid_fin_material": "fiberglass",
    "s1_grid_fin_material": "fiberglass",
    "launch_azimuth": 0.0,
    "launch_angle_deg": 0.0,
    "wind_levels": parse_wind_csv(WIND_CSV),
}


def rust_phase_margins(fixture):
    """Compute Rust phase margins using the AST evaluator."""
    import subprocess
    import json as _json
    
    # Build AST nodes
    from osifog_engine_search import parameters_to_ast
    from organic_loop import ast_to_dicts
    
    ast_nodes = parameters_to_ast(fixture)
    ast_dicts = ast_to_dicts(ast_nodes)
    
    # Build the Rust evaluation request
    mission = _json.loads(Path("missions/osifog_l3_precision.json").read_text())
    payload = {
        "target_apogee_m": 3000.0,
        "physics_mode": "openrocket",
        "execution_profile": "authority-heavy",
        "objectives": [],
        "constraints": {
            "max_height_m": 4.0,
            "simulation_phase": "ascent",
        },
        "phase_machs": [0.3, 0.5, 0.7, 0.3, 0.3],
        "candidates": [{
            "id": "parity-fixture",
            "ast": ast_dicts,
            "signature": "parity_test",
            "environment": {
                "launch_rod_length_m": 6.0,
                "launch_rod_angle_rad": 0.0,
                "launch_rod_direction_rad": 0.0,
                "wind_speed_mps": 0.0,
                "wind_direction_rad": 0.0,
                "relative_humidity": 0.82,
                "base_temperature_k": 303.25,
                "base_pressure_pa": 100000.0,
                "launch_altitude_m": 3.0,
                "wind_levels": [],
            },
        }],
        "calibrations": {},
    }
    
    engine_dir = Path("l2_engine")
    binary_name = "ast_eval.exe" if os.name == "nt" else "ast_eval"
    binary_path = engine_dir / "target" / "release" / binary_name
    
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(payload, f)
        input_path = f.name
    
    try:
        result = subprocess.run(
            [str(binary_path), "--input", input_path],
            capture_output=True, text=True, timeout=30,
            cwd=str(engine_dir)
        )
        if result.returncode != 0:
            return {"error": result.stderr[:500]}
        response = _json.loads(result.stdout)
        return response.get("results", [{}])[0]
    finally:
        os.unlink(input_path)


def openrocket_phase_margins(fixture):
    """Extract OpenRocket phase margins from a simulated ORK."""
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
        
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        vz0 = br0.get(fdt.TYPE_VELOCITY_Z)
        stab0 = br0.get(fdt.TYPE_STABILITY)
        times0 = br0.get(fdt.TYPE_TIME)
        
        apex_idx = max(range(n0), key=lambda i: float(alt0[i]))
        
        # Extract stability at 5 representative phases
        # Phase 1: early ascent (10% of time to apogee)
        # Phase 2: 25% of time to apogee
        # Phase 3: 50% of time to apogee (max q region)
        # Phase 4: near apogee (90% of time to apogee)
        # Phase 5: just before apogee
        
        time_to_apogee = float(times0[apex_idx])
        phase_times = [
            0.1 * time_to_apogee,
            0.25 * time_to_apogee,
            0.5 * time_to_apogee,
            0.9 * time_to_apogee,
            0.99 * time_to_apogee,
        ]
        
        phase_stabilities = []
        for target_time in phase_times:
            # Find closest sample
            closest_idx = min(range(n0), key=lambda i: abs(float(times0[i]) - target_time))
            stability = float(stab0[closest_idx])
            phase_stabilities.append({
                "time_s": float(times0[closest_idx]),
                "target_time_s": target_time,
                "stability_cal": stability,
            })
        
        mach = float(data.getMaxMachNumber())
        apogee = float(data.getMaxAltitude())
        
        return {
            "mach": mach,
            "apogee_m": apogee,
            "apex_time_s": time_to_apogee,
            "phase_stabilities": phase_stabilities,
            "min_stability_cal": min(s["stability_cal"] for s in phase_stabilities if math.isfinite(s["stability_cal"])),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    print("Gate 4: Phase-Margin Parity")
    print("=" * 60)
    print(f"Fixture: single-stage, J510W motor, 4 fiberglass fins")
    print(f"Body radius: {FIXTURE['s0_body_rad']*1000:.0f} mm")
    print(f"Fin root: {FIXTURE['s0_fin_root']*1000:.0f} mm, height: {FIXTURE['s0_fin_height']*1000:.0f} mm")
    
    # Run Rust evaluation
    print("\nRunning Rust evaluator...")
    rust_result = rust_phase_margins(FIXTURE)
    if "error" in rust_result:
        print(f"  Rust error: {rust_result['error']}")
    else:
        print(f"  Rust status: {rust_result.get('status', 'unknown')}")
        print(f"  Rust apogee: {rust_result.get('apogee_m', 'N/A')} m")
        print(f"  Rust mach: {rust_result.get('mach', 'N/A')}")
        print(f"  Rust min margin: {rust_result.get('min_static_margin', 'N/A')} cal")
        if "margins" in rust_result:
            print(f"  Rust margins: {rust_result['margins']}")
    
    # Run OpenRocket
    print("\nRunning OpenRocket simulation...")
    or_result = openrocket_phase_margins(FIXTURE)
    print(f"  OR mach: {or_result['mach']:.4f}")
    print(f"  OR apogee: {or_result['apogee_m']:.2f} m")
    print(f"  OR min stability: {or_result['min_stability_cal']:.4f} cal")
    for ps in or_result['phase_stabilities']:
        print(f"    Phase t={ps['time_s']:.3f}s: stability={ps['stability_cal']:.4f} cal")
    
    # Compare
    print("\nComparison:")
    if "error" not in rust_result:
        rust_margin = rust_result.get("min_static_margin", float("-inf"))
        or_margin = or_result["min_stability_cal"]
        if math.isfinite(rust_margin) and math.isfinite(or_margin):
            delta = abs(rust_margin - or_margin)
            sign_match = (rust_margin > 0) == (or_margin > 0)
            print(f"  Rust min margin: {rust_margin:.4f} cal")
            print(f"  OR min margin:   {or_margin:.4f} cal")
            print(f"  Delta:           {delta:.4f} cal")
            print(f"  Sign match:      {sign_match}")
            print(f"  Within 0.10:     {delta <= 0.10}")
            print(f"  Within 0.20:     {delta <= 0.20}")
        else:
            print(f"  Cannot compare: Rust margin={rust_margin}, OR margin={or_margin}")
    
    # Save artifact
    artifact = {
        "test": "phase_margin_parity",
        "fixture": {k: v for k, v in FIXTURE.items() if k != "wind_levels"},
        "rust": rust_result,
        "openrocket": or_result,
    }
    with open("artifacts/phase2/phase-margin-parity.json", "w") as f:
        json.dump(artifact, f, indent=2, default=str)
    print(f"\nArtifact written to artifacts/phase2/phase-margin-parity.json")


if __name__ == "__main__":
    main()
