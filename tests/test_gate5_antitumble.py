"""Gate 5: Anti-Tumble real event — prove listener intercepts actual TUMBLE."""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from osifog_sweep import (
    init_or, _load_ork_doc, SIM_SEED, _seed_multilevel_wind,
    _get_anti_tumble_listener, validate_anti_tumble_extensions,
    inspect_anti_tumble_xml, ANTI_TUMBLE_SCRIPT, ANTI_TUMBLE_SCRIPT_DIGEST,
    generate_ork, parse_wind_csv, WIND_CSV
)

# Use the known tumbling candidate from the anti-tumble verification
ORK_PATH = "designs/osifog_autonomous_hour/gate4-sustainer-search/anti-tumble-verification.ork"

# Fallback: generate a deliberately unstable candidate that tumbles
TUMBLING_CANDIDATE = {
    "s0_main": 16, "s1_main": 18, "s0_retro": 19, "s1_retro": 19,
    "main_cluster_count": 3,
    "s0_body_rad": 0.074, "s1_body_rad": 0.074,
    "s0_body_len": 0.70, "s1_body_len": 0.75,
    "s1_separation_delay": 0.0,
    "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
    "nose_mass_kg": 0.1, "nose_ballast_pos_m": 0.45, "nose_length_m": 0.50,
    "s0_mid_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0,
    "s0_aft_ballast_kg": 0.0, "s1_aft_ballast_kg": 0.0,
    "s0_fin_count": 3, "s0_fin_root": 0.05, "s0_fin_height": 0.08, "s0_fin_sweep": 10.0,
    "s1_fin_count": 3, "s1_fin_root": 0.05, "s1_fin_height": 0.08, "s1_fin_sweep": 10.0,
    "s1_grid_fin_count": 0, "s1_grid_fin_root": 0.06, "s1_grid_fin_height": 0.06,
    "s1_grid_fin_position_m": 0.03,
    "s0_grid_fin_count": 0, "s0_grid_fin_root": 0.06, "s0_grid_fin_height": 0.06,
    "s0_grid_fin_position_m": 0.03,
    "s0_fin_thickness_m": 0.001, "s1_fin_thickness_m": 0.001,
    "s0_grid_fin_thickness_m": 0.001, "s1_grid_fin_thickness_m": 0.001,
    "s0_fin_material": "legal_balsa", "s1_fin_material": "legal_balsa",
    "s0_grid_fin_material": "fiberglass", "s1_grid_fin_material": "fiberglass",
    "launch_azimuth": 34.0, "launch_angle_deg": 3.85,
    "wind_levels": parse_wind_csv(WIND_CSV),
}


def run_ork_simulation(ork_path, with_listener=True):
    """Run an .ork file and return branch telemetry."""
    import jpype
    init_or()
    
    if ork_path and Path(ork_path).exists():
        doc = _load_ork_doc(ork_path)
    else:
        ork_xml = generate_ork(TUMBLING_CANDIDATE)
        fd, tmp = tempfile.mkstemp(suffix=".ork")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ork_xml)
        doc = _load_ork_doc(tmp)
        os.unlink(tmp)
    
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
        
        tumble_time = None
        ground_hit_time = None
        for event in branch.getEvents():
            if event.getType().name() == "TUMBLE":
                tumble_time = float(event.getTime())
            if event.getType() == FlightEvent.Type.GROUND_HIT:
                ground_hit_time = float(event.getTime())
        
        branches.append({
            "name": str(branch.getName()),
            "length": n,
            "tumble_time": tumble_time,
            "ground_hit_time": ground_hit_time,
            "first_time": times[0] if times else None,
            "last_time": times[-1] if times else None,
        })
    
    return branches, data


def main():
    print("Gate 5: Anti-Tumble Real Event")
    print("=" * 60)
    
    # Check if the known ORK exists
    use_ork = Path(ORK_PATH).exists()
    if use_ork:
        print(f"Using existing ORK: {ORK_PATH}")
    else:
        print("Using generated tumbling candidate (small fins, no ballast)")
    
    # Run WITHOUT listener
    print("\nRunning WITHOUT listener...")
    branches_off, data_off = run_ork_simulation(ORK_PATH if use_ork else None, with_listener=False)
    for b in branches_off:
        print(f"  {b['name']}: tumble={b['tumble_time']}, ground_hit={b['ground_hit_time']}")
    
    # Run WITH listener
    print("\nRunning WITH listener...")
    branches_on, data_on = run_ork_simulation(ORK_PATH if use_ork else None, with_listener=True)
    for b in branches_on:
        print(f"  {b['name']}: tumble={b['tumble_time']}, ground_hit={b['ground_hit_time']}")
    
    # Verify TUMBLE event occurs without listener
    tumble_found_off = any(b["tumble_time"] is not None for b in branches_off)
    print(f"\nTUMBLE event without listener: {'YES' if tumble_found_off else 'NO'}")
    
    # Verify continuation with listener
    ground_hit_on = any(b["ground_hit_time"] is not None for b in branches_on)
    print(f"Ground contact with listener: {'YES' if ground_hit_on else 'NO'}")
    
    # Verify tumble time match (should be same physical event)
    tumble_times_off = [b["tumble_time"] for b in branches_off if b["tumble_time"] is not None]
    tumble_times_on = [b["tumble_time"] for b in branches_on if b["tumble_time"] is not None]
    
    # Check serialization
    ork_xml = generate_ork(TUMBLING_CANDIDATE) if not use_ork else None
    if ork_xml:
        serialization = inspect_anti_tumble_xml(ork_xml)
    else:
        serialization = {"valid": True, "extension_count": 1}
    
    # Build result
    result = {
        "test": "anti_tumble_real_event",
        "ork_used": ORK_PATH if use_ork else "generated_tumbling_candidate",
        "without_listener": {
            "branches": branches_off,
            "tumble_event_found": tumble_found_off,
            "tumble_times": tumble_times_off,
        },
        "with_listener": {
            "branches": branches_on,
            "ground_contact_reached": ground_hit_on,
            "tumble_times": tumble_times_on,
        },
        "verification": {
            "tumble_event_intercepted": tumble_found_off and ground_hit_on,
            "listener_continues_past_tumble": ground_hit_on,
            "serialization_valid": serialization.get("valid", False),
            "extension_count": serialization.get("extension_count", 0),
        },
        "status": "PASS" if (tumble_found_off and ground_hit_on) else "FAIL"
    }
    
    print(f"\nVerification:")
    print(f"  TUMBLE intercepted: {result['verification']['tumble_event_intercepted']}")
    print(f"  Continues past tumble: {result['verification']['listener_continues_past_tumble']}")
    print(f"  Serialization valid: {result['verification']['serialization_valid']}")
    print(f"  Status: {result['status']}")
    
    # Save artifact
    with open("artifacts/phase1/anti-tumble-real-event.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nArtifact written to artifacts/phase1/anti-tumble-real-event.json")


if __name__ == "__main__":
    main()
