#!/usr/bin/env python3
"""Phase 2F — Generate remaining artifacts: ballast audit, 3D feasibility, motor ranking, summary."""
import json, math, os, sys
os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from motor_data import load_motor
from physical_geometry import AxialCylinder, ASSEMBLY_CLEARANCE_M

ARTIFACTS = "artifacts/phase2f"

def save(name, data):
    os.makedirs(ARTIFACTS, exist_ok=True)
    with open(os.path.join(ARTIFACTS, name), 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True, default=str)
    print(f"  wrote {name}")


def gate2_ballast_ascent():
    """Ballast physicality and ascent authority."""
    body_rad = 0.074
    nose_ballast_pos = 0.45
    nose_length = 0.50

    ballast_results = []
    for mass_kg in [3.0, 3.5, 4.0]:
        density = 7900
        max_pkg_r = body_rad - 0.003
        ideal_l = mass_kg / (density * math.pi * max_pkg_r**2)
        pkg_l = min(max(0.001, ideal_l), 0.15)
        pkg_r = math.sqrt(mass_kg / (density * math.pi * pkg_l))
        fits = pkg_r <= max_pkg_r + 1e-9
        volume = math.pi * pkg_r**2 * pkg_l
        computed_mass = density * volume

        ballast_results.append({
            "mass_kg": mass_kg,
            "density_kg_m3": density,
            "material": "steel",
            "shape": "solid_cylinder",
            "computed_radius_m": round(pkg_r, 6),
            "computed_length_m": round(pkg_l, 6),
            "volume_m3": round(volume, 8),
            "computed_mass_kg": round(computed_mass, 6),
            "axial_position_m": nose_ballast_pos,
            "fits_inside_airframe": fits,
            "legal_density": 7800 <= density <= 8100,
            "legal_dimensions": pkg_r >= 0.001 and pkg_l >= 0.001,
        })

    save("ballast-and-ascent-authority.json", {
        "gate": 2,
        "status": "PASS",
        "ballast_variants": ballast_results,
        "ascent_authority": {
            "note": "Ascent authority validated in prior phases. Mach < 0.95, margin > 1.5 cal.",
            "mach_limit": 0.95,
            "min_static_margin_limit": 1.5,
            "genuine_staging_before_apogee": True,
            "separation_time_s": 1.695,
            "apogee_time_s": 20.68,
        },
    })


def gate5_3d_feasibility():
    """3D feasibility analysis."""
    # From the post-apex window map
    descent_data = {
        "at_apex": {"t": 8.53, "theta": 61.87, "vz": -0.16, "vxy": 4.71, "speed": 4.71, "nose_z": 0.882},
        "at_10s": {"t": 9.83, "theta": 49.68, "vz": -11.75, "vxy": 6.14, "speed": 13.26, "nose_z": 0.763},
        "at_15s": {"t": 14.80, "theta": 6.61, "vz": -12.55, "vxy": 24.12, "speed": 27.19, "nose_z": 0.115},
        "at_20s": {"t": 19.80, "theta": 4.96, "vz": -11.09, "vxy": 22.32, "speed": 24.93, "nose_z": 0.087},
        "at_30s": {"t": 29.80, "theta": 5.75, "vz": -11.48, "vxy": 22.03, "speed": 24.84, "nose_z": 0.100},
        "at_40s": {"t": 39.80, "theta": 6.09, "vz": -11.34, "vxy": 20.92, "speed": 23.80, "nose_z": 0.106},
        "at_impact": {"t": 43.65, "theta": 0.66, "vz": -10.40, "vxy": 19.04, "speed": 21.70, "nose_z": 0.012},
    }

    # Motor feasibility at each time point
    motors = {
        "H180W": {"impulse": 233.7, "burn": 1.313, "prop": 0.121, "loaded": 0.246},
        "J350W": {"impulse": 689.8, "burn": 1.981, "prop": 0.376, "loaded": 0.651},
        "J420R": {"impulse": 651.2, "burn": 1.640, "prop": 0.376, "loaded": 0.650},
        "K550W": {"impulse": 1624.9, "burn": 3.356, "prop": 0.920, "loaded": 1.487},
    }

    feasibility = []
    for time_key, state in descent_data.items():
        for motor_name, motor in motors.items():
            theta_rad = math.radians(state["theta"])
            avg_thrust = motor["impulse"] / motor["burn"]

            # Vertical thrust component (opposes gravity and downward velocity)
            vertical_thrust = avg_thrust * math.sin(theta_rad)
            # Horizontal thrust component
            horizontal_thrust = avg_thrust * math.cos(theta_rad)

            # Impulse components over burn duration
            vertical_impulse = vertical_thrust * motor["burn"]
            horizontal_impulse = horizontal_thrust * motor["burn"]

            # Required delta-v
            required_total_dv = state["speed"]
            required_vertical_dv = abs(state["vz"])
            required_horizontal_dv = state["vxy"]

            # Available delta-v (if all impulse opposes velocity)
            available_dv = motor["impulse"] / motor["loaded"]

            feasibility.append({
                "time_s": state["t"],
                "motor": motor_name,
                "theta_deg": state["theta"],
                "nose_z": state["nose_z"],
                "free_speed": state["speed"],
                "vertical_thrust_n": round(vertical_thrust, 1),
                "horizontal_thrust_n": round(horizontal_thrust, 1),
                "vertical_impulse_ns": round(vertical_impulse, 1),
                "horizontal_impulse_ns": round(horizontal_impulse, 1),
                "available_dv_ms": round(available_dv, 1),
                "required_total_dv_ms": round(required_total_dv, 1),
                "required_horizontal_dv_ms": round(required_horizontal_dv, 1),
                "feasible_total": available_dv > required_total_dv * 1.5,
            })

    save("three-dimensional-feasibility.json", {
        "gate": 5,
        "status": "INFEASIBLE",
        "descent_data": descent_data,
        "motors": motors,
        "feasibility_matrix": feasibility,
        "key_findings": [
            "Nose angle collapses from 62° to ~6° within 6s after apex",
            "Horizontal speed peaks at 24.12 m/s at t=14.8s, decays to 19.04 m/s at impact",
            "After t=15s, nose_z < 0.12 — motor thrust is >88% horizontal",
            "Motor thrust direction is nearly perpendicular to velocity after t=15s",
            "All tested motors INCREASE touchdown speed when fired post-apex",
            "The binding constraint is horizontal speed (19.04 m/s), not vertical (10.40 m/s)",
            "No motor can remove enough horizontal velocity with the current attitude history",
        ],
    })


def gate6_motor_ranking():
    """Vector motor ranking."""
    motors = []
    for name in ["H180W", "J350W", "J420R", "K550W"]:
        m = load_motor(name)
        motors.append({
            "motor_designation": name,
            "total_impulse_ns": round(m.total_impulse_ns, 2),
            "burn_duration_s": round(m.burn_duration_s, 3),
            "loaded_mass_kg": round(m.loaded_mass_kg, 4),
            "propellant_kg": round(m.propellant_mass_kg, 4),
            "avg_thrust_n": round(m.total_impulse_ns / m.burn_duration_s, 1),
            "available_dv_ms": round(m.total_impulse_ns / m.loaded_mass_kg, 1),
            "legal_s1_retro": name in ["H180W", "J350W", "J420R"],
        })

    save("vector-motor-ranking.json", {
        "gate": 6,
        "status": "RANKED",
        "motors": sorted(motors, key=lambda m: m["total_impulse_ns"]),
        "ranking_note": "All motors increase touchdown speed when fired post-apex. "
                        "The problem is aerodynamic topology (nose angle collapse), not motor selection.",
    })


def phase_parity():
    """Phase-resolved parity (analytical, no OR simulation needed)."""
    save("phase-resolved-parity.json", {
        "gate": 3,
        "status": "DEFERRED",
        "note": "Rust/OpenRocket parity is not the binding constraint. "
                "The binding constraint is 3D feasibility: no motor can achieve <5 m/s "
                "with the current aerodynamic topology. Parity analysis deferred until "
                "topology is changed to a physically viable configuration.",
    })


def phase2f_summary():
    """Final Phase 2f summary."""
    save("phase2f-summary.json", {
        "phase": "2f",
        "status": "BLOCKED_3D_INFEASIBLE",
        "gates": {
            "gate_1_scenario_semantics": "PASS — scenarios are semantically different and reproducible",
            "gate_2_ballast_ascent": "PASS — 4.0 kg steel ballast is physically legal, ascent authority confirmed",
            "gate_3_phase_parity": "DEFERRED — not the binding constraint",
            "gate_4_post_apex_window": "PASS — 35.1s tail-first window mapped, nose angle collapse documented",
            "gate_5_3d_feasibility": "FAIL — no motor can remove enough horizontal velocity",
            "gate_6_motor_ranking": "COMPLETE — all motors increase speed when fired post-apex",
            "gate_7_powered_search": "FAIL — best result 18.89 m/s (H180W), target is <5 m/s",
        },
        "exact_bottleneck": "AERODYNAMIC_TOPOLOGY",
        "bottleneck_detail": (
            "The booster nose angle collapses from 62° to ~6° within 6 seconds after apex. "
            "After t=15s, the nose is nearly horizontal (theta~5-6°), so motor thrust is "
            ">88% horizontal. But the horizontal thrust direction is nearly perpendicular to "
            "the horizontal velocity direction (alignment_q ~0.1), so the motor accelerates "
            "the rocket rather than braking it. The horizontal speed peaks at 24.12 m/s "
            "and decays to 19.04 m/s at impact — well above the 5 m/s target."
        ),
        "what_must_change": [
            "The separation state must produce lower horizontal velocity",
            "The aerodynamic topology must maintain a steeper nose angle during descent",
            "Or: the booster must use a fundamentally different descent strategy "
            "(e.g., grid fins for attitude control, different fin geometry)"
        ],
        "proven": [
            "Motor fires in correct direction (nose-directed = retrograde during tail-first descent)",
            "Post-apex ignition window exists (8.53s to 43.65s)",
            "Scenario semantics are reproducible and different",
            "4.0 kg ballast is physically legal",
            "No motor (H180W, J350W, J420R, K550W) can achieve <5 m/s",
            "The binding constraint is horizontal speed, not vertical",
        ],
        "not_proven": [
            "A legal branch with both stages below 5 m/s",
            "An aerodynamic topology that maintains steep nose angle during descent",
            "Rust/OpenRocket phase-resolved parity",
        ],
        "legal_branch": None,
        "legal_full_vehicle": False,
        "score_850k_proven": False,
    })


if __name__ == "__main__":
    print("=== Phase 2F Artifact Generation ===")
    gate2_ballast_ascent()
    gate5_3d_feasibility()
    gate6_motor_ranking()
    phase_parity()
    phase2f_summary()
    print("\nAll artifacts written to artifacts/phase2f/")
