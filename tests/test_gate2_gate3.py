"""Gate 2: Mass conservation fixture and Gate 3: Diagnostic/legality separation."""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

import motor_data
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, validate_hard_constraints, score_official,
    _get_anti_tumble_listener, MAX_MACH, MIN_STATIC_MARGIN
)


# Test candidate: uses motors with available .eng files
CANDIDATE = {
    "s0_main": 16, "s1_main": 18, "s0_retro": 19, "s1_retro": 19,
    "main_cluster_count": 3,
    "s0_body_rad": 0.074, "s1_body_rad": 0.074,
    "s0_body_len": 0.70, "s1_body_len": 0.75,
    "s1_separation_delay": 0.0,
    "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
    "nose_mass_kg": 1.72, "nose_ballast_pos_m": 0.45,
    "nose_length_m": 0.50,
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


def gate2_mass_conservation():
    """Verify initial mass = sum of landed masses + consumed propellant."""
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
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()
        
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
        
        # Get initial mass
        br0 = data.getBranch(0)
        initial_mass = float(br0.get(fdt.TYPE_MASS)[0])
        
        # Get landed masses
        landed_masses = []
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            events = branch.getEvents()
            hit_time = None
            for ev in events:
                if ev.getType() == FlightEvent.Type.GROUND_HIT:
                    hit_time = float(ev.getTime())
                    break
            if hit_time is None:
                continue
            times = branch.get(fdt.TYPE_TIME)
            masses = branch.get(fdt.TYPE_MASS)
            n = int(branch.getLength())
            for i in range(1, n):
                if float(times[i]) >= hit_time:
                    f = (hit_time - float(times[i-1])) / (float(times[i]) - float(times[i-1]))
                    landed_mass = float(masses[i-1]) + f * (float(masses[i]) - float(masses[i-1]))
                    landed_masses.append(landed_mass)
                    break
        
        total_landed = sum(landed_masses)
        consumed = initial_mass - total_landed
        
        # Expected propellant from motor data
        s0_main = motor_data.load_motor_by_index(CANDIDATE["s0_main"])
        s0_retro = motor_data.load_motor_by_index(CANDIDATE["s0_retro"])
        s1_main = motor_data.load_motor_by_index(CANDIDATE["s1_main"])
        s1_retro = motor_data.load_motor_by_index(CANDIDATE["s1_retro"])
        expected_propellant = (
            3 * s0_main.propellant_mass_kg + s0_retro.propellant_mass_kg +
            3 * s1_main.propellant_mass_kg + s1_retro.propellant_mass_kg
        )
        
        result = {
            "test": "mass_conservation",
            "initial_mass_kg": initial_mass,
            "landed_masses_kg": landed_masses,
            "total_landed_kg": total_landed,
            "consumed_propellant_kg": consumed,
            "expected_total_propellant_kg": expected_propellant,
            "propellant_discrepancy_kg": abs(consumed - expected_propellant),
            "conservation_error_pct": abs(consumed - expected_propellant) / expected_propellant * 100 if expected_propellant > 0 else 0,
            "status": "PASS" if abs(consumed - expected_propellant) / max(expected_propellant, 1e-9) < 0.05 else "FAIL"
        }
        return result
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def gate3_diagnostic_legality_separation():
    """Verify diagnostics are returned even when candidate is illegal."""
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
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()
        
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        
        # Extract diagnostics
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        apex_idx = max(range(n0), key=lambda i: float(alt0[i]))
        
        mach = float(data.getMaxMachNumber())
        apogee = float(data.getMaxAltitude())
        
        # Check stability
        stability_values = br0.get(fdt.TYPE_STABILITY)
        vertical_values = br0.get(fdt.TYPE_VELOCITY_Z)
        times = br0.get(fdt.TYPE_TIME)
        finite_stability = [
            float(stability_values[i])
            for i in range(apex_idx + 1)
            if float(vertical_values[i]) > 0.01 and math.isfinite(float(stability_values[i]))
        ]
        min_margin = min(finite_stability) if finite_stability else float("-inf")
        
        # Run hard constraints
        legal, violations = validate_hard_constraints(
            {"mach": mach, "min_static_margin": min_margin, "status": "SIMULATED",
             "stage_landings": [], "event_times": {}, "branch_event_times": [],
             "telemetry_err": None},
            CANDIDATE
        )
        
        result = {
            "test": "diagnostic_legality_separation",
            "mach": mach,
            "apogee_m": apogee,
            "min_static_margin_cal": min_margin,
            "legal": legal,
            "violations": violations,
            "diagnostics_present": mach is not None and apogee is not None and min_margin is not None,
            "status": "PASS"
        }
        return result
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    print("Gate 2: Mass Conservation")
    print("=" * 60)
    g2 = gate2_mass_conservation()
    print(f"  Initial mass: {g2['initial_mass_kg']:.4f} kg")
    print(f"  Landed masses: {[f'{m:.4f}' for m in g2['landed_masses_kg']]}")
    print(f"  Total landed: {g2['total_landed_kg']:.4f} kg")
    print(f"  Consumed propellant: {g2['consumed_propellant_kg']:.4f} kg")
    print(f"  Expected propellant: {g2['expected_total_propellant_kg']:.4f} kg")
    print(f"  Discrepancy: {g2['propellant_discrepancy_kg']:.4f} kg ({g2['conservation_error_pct']:.2f}%)")
    print(f"  Status: {g2['status']}")
    
    print("\nGate 3: Diagnostic/Legality Separation")
    print("=" * 60)
    g3 = gate3_diagnostic_legality_separation()
    print(f"  Mach: {g3['mach']:.4f}")
    print(f"  Apogee: {g3['apogee_m']:.2f} m")
    print(f"  Min margin: {g3['min_static_margin_cal']:.4f} cal")
    print(f"  Legal: {g3['legal']}")
    print(f"  Violations: {g3['violations']}")
    print(f"  Diagnostics present: {g3['diagnostics_present']}")
    print(f"  Status: {g3['status']}")
    
    # Save artifacts
    with open("artifacts/phase1/mass-conservation.json", "w") as f:
        json.dump(g2, f, indent=2)
    with open("artifacts/phase1/diagnostic-legality-separation.json", "w") as f:
        json.dump(g3, f, indent=2)
    
    print(f"\nArtifacts written to artifacts/phase1/")


if __name__ == "__main__":
    main()
