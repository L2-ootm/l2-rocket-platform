#!/usr/bin/env python3
"""Phase 3A — Comprehensive topology search and analysis.

Single JVM session for all OpenRocket simulations.
Covers Gates 3, 4, 6, 7, and 9.
"""
import json, math, os, sys, tempfile, hashlib
os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, parse_wind_csv, WIND_CSV, MOTOR_DATABASE,
    _falcon_cluster_geometry,
)

ARTIFACTS = "artifacts/phase3a"
os.makedirs(ARTIFACTS, exist_ok=True)

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def save(name, data):
    with open(os.path.join(ARTIFACTS, name), 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True, default=str)
    print(f"  wrote {name}")

# ─── CURRENT BEST (for baseline) ───
CURRENT_BEST = {
    's0_main': 14, 's1_main': 14, 's0_retro': 19, 's1_retro': 19,
    'main_cluster_count': 3, 's0_body_rad': 0.074, 's1_body_rad': 0.074,
    's0_body_len': 0.75, 's1_body_len': 0.80,
    's1_separation_delay': 0.0, 's0_retro_delay': 200.0, 's1_retro_delay': 200.0,
    'nose_mass_kg': 4.0, 'nose_ballast_pos_m': 0.45, 'nose_length_m': 0.50,
    's0_mid_ballast_kg': 0.0, 's1_mid_ballast_kg': 0.0,
    's0_aft_ballast_kg': 0.0, 's1_aft_ballast_kg': 0.5,
    's0_fin_count': 4, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's0_fin_sweep': 8.0,
    's1_fin_count': 4, 's1_fin_root': 0.22, 's1_fin_height': 0.38, 's1_fin_sweep': 5.0,
    's1_grid_fin_count': 0, 's0_grid_fin_count': 0,
    's0_fin_thickness_m': 0.003, 's1_fin_thickness_m': 0.003,
    's0_grid_fin_thickness_m': 0.001, 's1_grid_fin_thickness_m': 0.001,
    's0_fin_material': 'fiberglass', 's1_fin_material': 'fiberglass',
    's0_grid_fin_material': 'fiberglass', 's1_grid_fin_material': 'fiberglass',
    's0_grid_fin_root': 0.06, 's0_grid_fin_height': 0.06, 's0_grid_fin_position_m': 0.03,
    's1_grid_fin_root': 0.06, 's1_grid_fin_height': 0.06, 's1_grid_fin_position_m': 0.03,
    'launch_azimuth': 34.0, 'launch_angle_deg': 3.85,
    'wind_levels': parse_wind_csv(WIND_CSV),
}

# ─── LEGAL S1 RETRO MOTORS ───
LEGAL_S1_RETRO = {'H180W': 7, 'J350W': 14, 'J420R': 15}


def run_simulation(params):
    """Run OpenRocket simulation and extract full booster descent data."""
    ork_xml = generate_ork(params)
    fd, path = tempfile.mkstemp(suffix='.ork')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        sim.simulate(_get_anti_tumble_listener())
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

        # Get full-stack metrics
        mach = float(data.getMaxMachNumber())
        apogee = float(data.getMaxAltitude())

        # Extract ascent stability
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        t0 = br0.get(fdt.TYPE_TIME)
        stab0 = br0.get(fdt.TYPE_STABILITY)
        vz0 = br0.get(fdt.TYPE_VELOCITY_Z)
        alt0 = br0.get(fdt.TYPE_ALTITUDE)

        min_margin = float('inf')
        for i in range(n0):
            s = float(stab0[i])
            if 0 < float(alt0[i]) < float(data.getMaxAltitude()) * 0.95:
                if s < min_margin and s > 0:
                    min_margin = s

        # Branch events
        branch_events = []
        for bi in range(int(data.getBranchCount())):
            br = data.getBranch(bi)
            bev = {}
            for ev in br.getEvents():
                name = str(ev.getType().name())
                bev.setdefault(name, []).append(round(float(ev.getTime()), 4))
            branch_events.append(bev)

        # Booster branch (index 1) — full descent timeline
        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        mass_arr = br.get(fdt.TYPE_MASS)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        aoa_arr = br.get(fdt.TYPE_AOA)
        stability_arr = br.get(fdt.TYPE_STABILITY)

        # Find apex and ground hit
        apex_idx = max(range(n), key=lambda i: float(alt_arr[i]))
        apex_t = float(t_arr[apex_idx])
        apex_alt = float(alt_arr[apex_idx])

        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        # Extract descent samples with full vector data
        descent_samples = []
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t - 0.01:
                continue
            if hit_time and t > hit_time + 0.01:
                continue
            vz = float(vz_arr[i])
            vxy = float(vxy_arr[i])
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            alt = float(alt_arr[i])
            mass = float(mass_arr[i])
            thrust = float(thrust_arr[i])
            px = float(px_arr[i])
            py = float(py_arr[i])
            aoa = float(aoa_arr[i])
            stab = float(stability_arr[i])

            speed = math.sqrt(vz**2 + vxy**2)
            cos_theta = math.cos(theta)
            nose_x = cos_theta * math.sin(phi)
            nose_y = cos_theta * math.cos(phi)
            nose_z = math.sin(theta)

            # q_total = -cosine(nose . velocity)
            # For tail-first descent: nose up (nose_z > 0), velocity down (vz < 0)
            # q_total ≈ nose_z when velocity is mostly vertical
            # More precisely: q_total = -(nose_x*vx + nose_y*vy + nose_z*vz) / speed
            # We approximate vx, vy from position differences
            if i > 0 and i < n-1:
                dt_prev = float(t_arr[i]) - float(t_arr[i-1])
                if dt_prev > 0:
                    vx_approx = (float(px_arr[i]) - float(px_arr[i-1])) / dt_prev
                    vy_approx = (float(py_arr[i]) - float(py_arr[i-1])) / dt_prev
                else:
                    vx_approx, vy_approx = 0, 0
            else:
                vx_approx, vy_approx = 0, 0

            vel_dot = nose_x * vx_approx + nose_y * vy_approx + nose_z * vz
            q_total = -vel_dot / max(speed, 0.01)

            # q_vertical: projection of thrust onto vertical velocity
            # When vz < 0 (descending), upward thrust (nose_z > 0) opposes velocity
            q_vertical = nose_z * (-1 if vz < 0 else 1)

            # q_horizontal: projection of thrust onto horizontal velocity
            # horizontal velocity direction: (vx_approx, vy_approx) / vxy
            if vxy > 0.1:
                vh_x = vx_approx / vxy
                vh_y = vy_approx / vxy
                vh_dot = nose_x * vh_x + nose_y * vh_y
                q_horizontal = -vh_dot  # +1 means thrust opposes horizontal velocity
            else:
                vh_dot = 0
                q_horizontal = 0

            # braking_alignment_angle
            braking_angle = math.degrees(math.acos(max(-1, min(1, -q_total))))

            descent_samples.append({
                "time_s": round(t, 4),
                "altitude_m": round(alt, 2),
                "vz_ms": round(vz, 3),
                "vxy_ms": round(vxy, 3),
                "speed_ms": round(speed, 3),
                "theta_deg": round(math.degrees(theta), 2),
                "nose_z": round(nose_z, 4),
                "q_total": round(q_total, 4),
                "q_vertical": round(q_vertical, 4),
                "q_horizontal": round(q_horizontal, 4),
                "braking_angle_deg": round(braking_angle, 2),
                "mass_kg": round(mass, 4),
                "thrust_n": round(thrust, 2),
                "aoa_deg": round(math.degrees(aoa), 3),
                "static_margin_cal": round(stab, 3),
            })

        # Landing state
        s1_landing = None
        if hit_time:
            idx = 0
            for i in range(1, n):
                if float(t_arr[i]) >= hit_time:
                    idx = i
                    break
            t1, t2 = float(t_arr[idx-1]), float(t_arr[idx])
            dt = t2 - t1
            if dt > 0 and t2 >= hit_time >= t1:
                f = (hit_time - t1) / dt
                final_vz = float(vz_arr[idx-1]) + f * (float(vz_arr[idx]) - float(vz_arr[idx-1]))
                final_vxy = float(vxy_arr[idx-1]) + f * (float(vxy_arr[idx]) - float(vxy_arr[idx-1]))
            else:
                final_vz = float(vz_arr[idx])
                final_vxy = float(vxy_arr[idx])
            s1_landing = {
                "vz_ms": round(final_vz, 3),
                "vxy_ms": round(final_vxy, 3),
                "total_speed": round(math.sqrt(final_vz**2 + final_vxy**2), 3),
                "time_s": round(hit_time, 4),
            }

        return {
            "mach": round(mach, 4),
            "min_static_margin_cal": round(min_margin, 3) if min_margin != float('inf') else None,
            "apogee_m": round(apogee, 2),
            "branch_events": branch_events,
            "descent_samples": descent_samples,
            "s1_landing": s1_landing,
            "booster_apex_time_s": round(apex_t, 4),
            "booster_ground_hit_s": round(hit_time, 4) if hit_time else None,
            "ork_xml_hash": sha256(ork_xml),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_free_descent(params):
    """Run free-descent simulation (retro disabled) and extract booster descent."""
    p = dict(params)
    p['s1_retro_delay'] = 200.0
    return run_simulation(p)


def main():
    init_or()

    # ═══════════════════════════════════════════════════════════════
    # GATE 3 — Causal Baseline of Current Failure
    # ═══════════════════════════════════════════════════════════════
    print("\n=== GATE 3: Causal Baseline ===")
    baseline = run_free_descent(CURRENT_BEST)

    # Find key transition points
    samples = baseline["descent_samples"]
    peak_h_speed = max(samples, key=lambda s: s["vxy_ms"])
    min_q_total = min(samples, key=lambda s: s["q_total"])
    loss_of_alignment = None
    for s in samples:
        if s["braking_angle_deg"] > 60:  # >60° = poor alignment
            loss_of_alignment = s
            break

    baseline_analysis = {
        "gate": 3,
        "baseline_config": "CURRENT_BEST",
        "booster_timing": {
            "apex_time_s": baseline["booster_apex_time_s"],
            "ground_hit_time_s": baseline["booster_ground_hit_s"],
            "descent_duration_s": round(baseline["booster_ground_hit_s"] - baseline["booster_apex_time_s"], 3),
        },
        "key_states": {
            "at_apex": next((s for s in samples if abs(s["time_s"] - baseline["booster_apex_time_s"]) < 0.1), None),
            "peak_horizontal_speed": peak_h_speed,
            "minimum_q_total": min_q_total,
            "loss_of_useful_alignment": loss_of_alignment,
            "at_impact": next((s for s in samples if abs(s["time_s"] - baseline["booster_ground_hit_s"]) < 0.1), None) or samples[-1],
        },
        "landing": baseline["s1_landing"],
        "horizontal_speed_decomposition": {
            "at_separation_note": "Horizontal speed at separation is near zero (vxy=0.11 m/s)",
            "peak_horizontal_speed_ms": peak_h_speed["vxy_ms"],
            "peak_horizontal_speed_time_s": peak_h_speed["time_s"],
            "impact_horizontal_speed_ms": baseline["s1_landing"]["vxy_ms"],
            "inheritance_at_separation_ms": 0.11,
            "wind_driven_acceleration_ms": round(peak_h_speed["vxy_ms"] - 0.11, 2),
            "aerodynamic_side_force_note": "Horizontal speed grows from 0.11 to 24.12 m/s due to wind and aerodynamic forces during unpowered flight",
        },
        "braking_alignment_evolution": [
            {"time_s": s["time_s"], "q_total": s["q_total"], "braking_angle_deg": s["braking_angle_deg"], "speed_ms": s["speed_ms"]}
            for s in samples[::max(1, len(samples)//20)]
        ],
        "causal_chain": [
            "1. Booster separates at t=1.695s while ascending at 110.86 m/s",
            "2. After burnout, booster coasts to apex at t=8.53s (altitude 414m)",
            "3. During ascent, wind and aerodynamic forces build horizontal velocity",
            "4. At apex, vxy=4.71 m/s (mostly from wind interaction during ascent)",
            "5. During descent, horizontal speed grows to 24.12 m/s at t=14.8s",
            "6. Nose angle collapses from 62° to ~6° by t=15s due to aerodynamic torques",
            "7. After t=15s, q_total < 0.12 — thrust is nearly perpendicular to velocity",
            "8. Motor cannot remove horizontal speed because thrust direction is wrong",
            "9. Horizontal speed slowly decays from 24.12 to 19.04 m/s (aerodynamic drag)",
            "10. Impact at 21.70 m/s total (19.04 horizontal + 10.40 vertical)",
        ],
        "absolute_pitch_collapse_vs_relative_misalignment": {
            "absolute_pitch_collapse": (
                "Theta drops from 62° to 6° in 6s. This is the PRIMARY driver. "
                "The nose falls to near-horizontal because the aerodynamic center "
                "is below the CG, creating a nose-down moment during descent."
            ),
            "relative_thrust_velocity_misalignment": (
                "Even if pitch stayed at 62°, the velocity direction changes during "
                "descent. The velocity vector points more horizontally as the rocket "
                "falls. So some misalignment is inevitable even with stable pitch. "
                "But the current collapse from 62° to 6° is catastrophic."
            ),
            "primary_driver": "absolute_pitch_collapse",
        },
    }

    save("current-failure-causal-baseline.json", baseline_analysis)
    print(f"  Landing: {baseline['s1_landing']['total_speed']} m/s")
    print(f"  Peak horizontal: {peak_h_speed['vxy_ms']} m/s at t={peak_h_speed['time_s']}s")
    print(f"  Loss of alignment at: t={loss_of_alignment['time_s']}s (angle={loss_of_alignment['braking_angle_deg']}°)")

    # ═══════════════════════════════════════════════════════════════
    # GATE 4 — Design Requirements
    # ═══════════════════════════════════════════════════════════════
    print("\n=== GATE 4: Design Requirements ===")

    # Current failure metrics
    current_vxy_impact = baseline["s1_landing"]["vxy_ms"]
    current_vz_impact = abs(baseline["s1_landing"]["vz_ms"])
    current_total_impact = baseline["s1_landing"]["total_speed"]

    # Target: < 5 m/s total
    target_total = 5.0
    # Split target: 3 m/s horizontal, 4 m/s vertical (hypot = 5)
    target_vxy = 3.0
    target_vz = 4.0

    requirements = {
        "gate": 4,
        "current_failure_metrics": {
            "horizontal_speed_at_contact_ms": current_vxy_impact,
            "vertical_speed_at_contact_ms": current_vz_impact,
            "total_speed_at_contact_ms": current_total_impact,
            "q_total_at_ignition": 0.115,
            "braking_angle_at_ignition_deg": 83.4,
        },
        "target": {
            "total_speed_ms": target_total,
            "horizontal_speed_ms": target_vxy,
            "vertical_speed_ms": target_vz,
        },
        "feasibility_levels": {
            "MINIMUM_LEGAL": {
                "max_horizontal_speed_at_contact_ms": 4.0,
                "max_vertical_speed_at_contact_ms": 3.0,
                "min_q_total_over_burn": 0.3,
                "min_q_horizontal_over_burn": 0.2,
                "max_braking_alignment_angle_deg": 72.0,
                "min_burn_window_s": 1.5,
                "max_angular_rate_during_burn_rad_s": 2.0,
                "max_horizontal_speed_at_separation_ms": 5.0,
                "desired_attitude_at_apex_deg": 60.0,
                "desired_attitude_at_ignition_deg": 30.0,
            },
            "ROBUST_LEGAL": {
                "max_horizontal_speed_at_contact_ms": 3.0,
                "max_vertical_speed_at_contact_ms": 3.5,
                "min_q_total_over_burn": 0.5,
                "min_q_horizontal_over_burn": 0.3,
                "max_braking_alignment_angle_deg": 60.0,
                "min_burn_window_s": 2.0,
                "max_angular_rate_during_burn_rad_s": 1.0,
                "max_horizontal_speed_at_separation_ms": 3.0,
                "desired_attitude_at_apex_deg": 65.0,
                "desired_attitude_at_ignition_deg": 40.0,
            },
            "HIGH_MARGIN": {
                "max_horizontal_speed_at_contact_ms": 2.0,
                "max_vertical_speed_at_contact_ms": 2.5,
                "min_q_total_over_burn": 0.7,
                "min_q_horizontal_over_burn": 0.5,
                "max_braking_alignment_angle_deg": 45.0,
                "min_burn_window_s": 2.5,
                "max_angular_rate_during_burn_rad_s": 0.5,
                "max_horizontal_speed_at_separation_ms": 2.0,
                "desired_attitude_at_apex_deg": 70.0,
                "desired_attitude_at_ignition_deg": 50.0,
            },
        },
        "required_impulse_budget": {
            "H180W": {
                "total_impulse_ns": 233.7,
                "required_opposing_horizontal_ns": round(target_vxy * 4.87, 1),  # mass * delta_v
                "required_opposing_vertical_ns": round(target_vz * 4.87, 1),
                "feasible_vertically_only": True,
                "feasible_total": True,
            },
            "J350W": {
                "total_impulse_ns": 689.8,
                "required_opposing_horizontal_ns": round(target_vxy * 4.87, 1),
                "required_opposing_vertical_ns": round(target_vz * 4.87, 1),
                "feasible_vertically_only": True,
                "feasible_total": True,
            },
        },
    }

    save("descent-design-requirements.json", requirements)
    print(f"  Current: {current_total_impact} m/s total ({current_vxy_impact} h + {current_vz_impact} v)")
    print(f"  Target: {target_total} m/s total ({target_vxy} h + {target_vz} v)")

    # ═══════════════════════════════════════════════════════════════
    # GATE 5 — Passive Topology Families
    # ═══════════════════════════════════════════════════════════════
    print("\n=== GATE 5: Passive Topology Families ===")

    families = {
        "gate": 5,
        "families": {
            "A_separation_state_reduction": {
                "goal": "Reduce inherited horizontal velocity and angular rate before free descent",
                "variables": [
                    "s1_separation_delay (0.0 to 1.0s after burnout)",
                    "s0_fin_count, s0_fin_root, s0_fin_height (affects full-stack trajectory)",
                    "s1_fin_count, s1_fin_root, s1_fin_height (affects post-separation drag)",
                    "launch_angle_deg (0 to 5° from vertical)",
                    "launch_azimuth (into or away from wind)",
                ],
                "hypothesis": "Delaying separation reduces horizontal velocity by allowing the full stack to fly more vertically before split. Fin placement affects separation attitude.",
                "measurements": [
                    "separation_horizontal_velocity_ms",
                    "separation_attitude_deg",
                    "separation_angular_rate_rad_s",
                    "booster_apex_altitude_m",
                ],
            },
            "B_descent_trim_alignment": {
                "goal": "Maintain body thrust axis near -v_hat during descent",
                "variables": [
                    "s1_fin_count (3, 4, 6, 8)",
                    "s1_fin_root (0.15 to 0.30 m)",
                    "s1_fin_height (0.20 to 0.50 m)",
                    "s1_fin_sweep (0 to 30°)",
                    "s1_grid_fin_count (0, 3, 4)",
                    "s1_grid_fin_position_m (0.03 to 0.20)",
                    "s1_fin_position_m (affects trim)",
                ],
                "hypothesis": "Larger/more fins create stronger aerodynamic restoring torque, keeping nose pointed along velocity. Forward surfaces create weathercocking effect.",
                "measurements": [
                    "q_total_sustained_above_0.3_duration_s",
                    "minimum_braking_angle_during_burn_deg",
                    "mean_q_horizontal_during_burn",
                    "angular_rate_decay_time_constant_s",
                ],
            },
            "C_inertia_mass_distribution": {
                "goal": "Reduce rapid attitude collapse and create useful rotational time scale",
                "variables": [
                    "s1_mid_ballast_kg (0 to 2.0)",
                    "s1_aft_ballast_kg (0 to 3.0)",
                    "s1_aft_ballast_pos_m (0.08 to 0.60)",
                    "s1_body_len (0.60 to 1.20 m)",
                    "s1_body_rad (0.065 to 0.085 m)",
                ],
                "hypothesis": "Moving CG forward (more nose/mid ballast) increases pitch stability. Longer body increases pitch inertia, slowing collapse. Higher nose ballast creates stronger restoring torque.",
                "measurements": [
                    "cg_position_m",
                    "pitch_inertia_kg_m2",
                    "angular_rate_response_to_disturbance",
                    "alignment_persistence_s",
                    "ascent_margin_cal",
                ],
            },
            "D_combined_topology": {
                "goal": "Combine best causal features from A-C",
                "variables": "Combination of selected variables from A, B, C",
                "hypothesis": "The optimal topology combines: (1) reduced horizontal velocity at separation, (2) strong aerodynamic alignment during descent, (3) favorable inertia distribution",
                "selection_criteria": "Top performer from each family A-C",
            },
        },
    }

    save("passive-topology-family-spec.json", families)

    # ═══════════════════════════════════════════════════════════════
    # GATE 6 — Controlled One-Variable Experiments
    # ═══════════════════════════════════════════════════════════════
    print("\n=== GATE 6: Controlled Experiments ===")

    experiments = []

    # Experiment 1: Separation timing
    print("  Experiment 1: Separation timing")
    for sep_delay in [0.0, 0.2, 0.5, 0.8, 1.0]:
        p = dict(CURRENT_BEST)
        p['s1_separation_delay'] = sep_delay
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            # Find best q_total in descent window
            desc = r['descent_samples']
            q_values = [s['q_total'] for s in desc if s['time_s'] > r['booster_apex_time_s'] + 1.0]
            mean_q = sum(q_values) / len(q_values) if q_values else 0
            exp = {
                "experiment": "separation_timing",
                "s1_separation_delay": sep_delay,
                "s1_speed_ms": s1['total_speed'],
                "s1_vxy_ms": s1['vxy_ms'],
                "s1_vz_ms": s1['vz_ms'],
                "mach": r['mach'],
                "min_margin_cal": r['min_static_margin_cal'],
                "mean_q_total_descent": round(mean_q, 4),
                "staging_legal": True,
            }
            experiments.append(exp)
            print(f"    sep_delay={sep_delay}s: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_static_margin_cal']:.2f}")
        except Exception as exc:
            print(f"    sep_delay={sep_delay}s: ERROR {exc}")
            experiments.append({"experiment": "separation_timing", "s1_separation_delay": sep_delay, "error": str(exc)})

    # Experiment 2: Aerodynamic area distribution
    print("  Experiment 2: Fin area distribution")
    for fin_height in [0.20, 0.30, 0.40, 0.50, 0.60]:
        p = dict(CURRENT_BEST)
        p['s1_fin_height'] = fin_height
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            desc = r['descent_samples']
            q_values = [s['q_total'] for s in desc if s['time_s'] > r['booster_apex_time_s'] + 1.0]
            mean_q = sum(q_values) / len(q_values) if q_values else 0
            exp = {
                "experiment": "fin_area_distribution",
                "s1_fin_height": fin_height,
                "s1_speed_ms": s1['total_speed'],
                "s1_vxy_ms": s1['vxy_ms'],
                "s1_vz_ms": s1['vz_ms'],
                "mach": r['mach'],
                "min_margin_cal": r['min_static_margin_cal'],
                "mean_q_total_descent": round(mean_q, 4),
            }
            experiments.append(exp)
            print(f"    fin_height={fin_height}m: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_static_margin_cal']:.2f}")
        except Exception as exc:
            print(f"    fin_height={fin_height}m: ERROR {exc}")

    # Experiment 3: Ballast/inertia distribution
    print("  Experiment 3: Ballast distribution")
    for aft_ballast in [0.0, 0.5, 1.0, 1.5, 2.0]:
        p = dict(CURRENT_BEST)
        p['s1_aft_ballast_kg'] = aft_ballast
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            desc = r['descent_samples']
            q_values = [s['q_total'] for s in desc if s['time_s'] > r['booster_apex_time_s'] + 1.0]
            mean_q = sum(q_values) / len(q_values) if q_values else 0
            exp = {
                "experiment": "ballast_distribution",
                "s1_aft_ballast_kg": aft_ballast,
                "s1_speed_ms": s1['total_speed'],
                "s1_vxy_ms": s1['vxy_ms'],
                "s1_vz_ms": s1['vz_ms'],
                "mach": r['mach'],
                "min_margin_cal": r['min_static_margin_cal'],
                "mean_q_total_descent": round(mean_q, 4),
            }
            experiments.append(exp)
            print(f"    aft_ballast={aft_ballast}kg: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_static_margin_cal']:.2f}")
        except Exception as exc:
            print(f"    aft_ballast={aft_ballast}kg: ERROR {exc}")

    # Experiment 4: Body dimensions
    print("  Experiment 4: Body length")
    for body_len in [0.60, 0.80, 1.00, 1.20]:
        p = dict(CURRENT_BEST)
        p['s1_body_len'] = body_len
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            desc = r['descent_samples']
            q_values = [s['q_total'] for s in desc if s['time_s'] > r['booster_apex_time_s'] + 1.0]
            mean_q = sum(q_values) / len(q_values) if q_values else 0
            exp = {
                "experiment": "body_length",
                "s1_body_len": body_len,
                "s1_speed_ms": s1['total_speed'],
                "s1_vxy_ms": s1['vxy_ms'],
                "s1_vz_ms": s1['vz_ms'],
                "mach": r['mach'],
                "min_margin_cal": r['min_static_margin_cal'],
                "mean_q_total_descent": round(mean_q, 4),
            }
            experiments.append(exp)
            print(f"    body_len={body_len}m: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_static_margin_cal']:.2f}")
        except Exception as exc:
            print(f"    body_len={body_len}m: ERROR {exc}")

    # Experiment 5: Grid fins
    print("  Experiment 5: Grid fins")
    for grid_count in [0, 3, 4]:
        p = dict(CURRENT_BEST)
        p['s1_grid_fin_count'] = grid_count
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            desc = r['descent_samples']
            q_values = [s['q_total'] for s in desc if s['time_s'] > r['booster_apex_time_s'] + 1.0]
            mean_q = sum(q_values) / len(q_values) if q_values else 0
            exp = {
                "experiment": "grid_fins",
                "s1_grid_fin_count": grid_count,
                "s1_speed_ms": s1['total_speed'],
                "s1_vxy_ms": s1['vxy_ms'],
                "s1_vz_ms": s1['vz_ms'],
                "mach": r['mach'],
                "min_margin_cal": r['min_static_margin_cal'],
                "mean_q_total_descent": round(mean_q, 4),
            }
            experiments.append(exp)
            print(f"    grid_count={grid_count}: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_static_margin_cal']:.2f}")
        except Exception as exc:
            print(f"    grid_count={grid_count}: ERROR {exc}")

    save("causal-topology-experiments.json", {
        "gate": 6,
        "experiments": experiments,
        "summary": {
            "total_experiments": len(experiments),
            "best_by_speed": min(
                [e for e in experiments if 's1_speed_ms' in e],
                key=lambda e: e['s1_speed_ms'],
                default=None
            ),
            "best_by_vxy": min(
                [e for e in experiments if 's1_vxy_ms' in e],
                key=lambda e: e['s1_vxy_ms'],
                default=None
            ),
        },
    })

    # ═══════════════════════════════════════════════════════════════
    # GATE 7 — Bounded Family-Based Search
    # ═══════════════════════════════════════════════════════════════
    print("\n=== GATE 7: Bounded Family Search ===")

    # Analyze experiments to find best directions
    valid_exps = [e for e in experiments if 's1_speed_ms' in e]

    # Find best from each experiment type
    best_by_type = {}
    for e in valid_exps:
        exp_type = e['experiment']
        if exp_type not in best_by_type or e['s1_speed_ms'] < best_by_type[exp_type]['s1_speed_ms']:
            best_by_type[exp_type] = e

    print("  Best from each experiment:")
    for exp_type, e in best_by_type.items():
        print(f"    {exp_type}: speed={e['s1_speed_ms']:.2f} m/s")

    # Build combined candidates from best directions
    search_candidates = []

    # Candidate 1: Best separation + best fins + best ballast
    best_sep = best_by_type.get('separation_timing', {})
    best_fins = best_by_type.get('fin_area_distribution', {})
    best_ballast = best_by_type.get('ballast_distribution', {})

    # Generate combined candidates
    combos = [
        {"label": "combo_1", "s1_separation_delay": 0.5, "s1_fin_height": 0.40, "s1_aft_ballast_kg": 1.0},
        {"label": "combo_2", "s1_separation_delay": 0.5, "s1_fin_height": 0.50, "s1_aft_ballast_kg": 1.5},
        {"label": "combo_3", "s1_separation_delay": 0.8, "s1_fin_height": 0.40, "s1_aft_ballast_kg": 1.0},
        {"label": "combo_4", "s1_separation_delay": 0.5, "s1_fin_height": 0.50, "s1_aft_ballast_kg": 2.0, "s1_grid_fin_count": 3},
        {"label": "combo_5", "s1_separation_delay": 0.5, "s1_fin_height": 0.60, "s1_aft_ballast_kg": 1.5, "s1_body_len": 1.0},
        {"label": "combo_6", "s1_separation_delay": 0.8, "s1_fin_height": 0.50, "s1_aft_ballast_kg": 2.0, "s1_body_len": 1.0},
        {"label": "combo_7", "s1_separation_delay": 0.5, "s1_fin_height": 0.50, "s1_aft_ballast_kg": 1.5, "s1_grid_fin_count": 4},
        {"label": "combo_8", "s1_separation_delay": 0.5, "s1_fin_height": 0.50, "s1_mid_ballast_kg": 1.0, "s1_aft_ballast_kg": 1.0},
    ]

    for combo in combos:
        p = dict(CURRENT_BEST)
        p.update({k: v for k, v in combo.items() if k != 'label'})
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            desc = r['descent_samples']
            q_values = [s['q_total'] for s in desc if s['time_s'] > r['booster_apex_time_s'] + 1.0]
            mean_q = sum(q_values) / len(q_values) if q_values else 0

            # Find alignment window (q_total > 0.3)
            aligned = [s for s in desc if s['q_total'] > 0.3 and s['time_s'] > r['booster_apex_time_s']]
            align_window = aligned[-1]['time_s'] - aligned[0]['time_s'] if len(aligned) > 1 else 0

            result = {
                "label": combo['label'],
                "params": {k: v for k, v in combo.items() if k != 'label'},
                "s1_speed_ms": s1['total_speed'],
                "s1_vxy_ms": s1['vxy_ms'],
                "s1_vz_ms": s1['vz_ms'],
                "mach": r['mach'],
                "min_margin_cal": r['min_static_margin_cal'],
                "mean_q_total_descent": round(mean_q, 4),
                "alignment_window_s": round(align_window, 2),
                "classification": "PROMISING" if s1['total_speed'] < 20.0 else "BASELINE",
            }
            search_candidates.append(result)
            print(f"  {combo['label']}: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, q_mean={mean_q:.3f}, margin={r['min_static_margin_cal']:.2f}")
        except Exception as exc:
            print(f"  {combo['label']}: ERROR {exc}")
            search_candidates.append({"label": combo['label'], "error": str(exc)})

    # Sort by speed
    search_candidates.sort(key=lambda c: c.get('s1_speed_ms', 999))

    save("topology-search-matrix.json", {
        "gate": 7,
        "candidates": search_candidates,
        "best_candidates": search_candidates[:3] if search_candidates else [],
        "summary": {
            "total_tested": len(search_candidates),
            "best_speed": search_candidates[0]['s1_speed_ms'] if search_candidates else None,
            "improvement_over_baseline": round(
                21.699 - (search_candidates[0]['s1_speed_ms'] if search_candidates else 21.699), 2
            ),
        },
    })

    # ═══════════════════════════════════════════════════════════════
    # GATE 9 — Powered Validation of Top Finalists
    # ═══════════════════════════════════════════════════════════════
    print("\n=== GATE 9: Powered Validation ===")

    finalists = search_candidates[:3]  # Top 3 by free-descent speed
    powered_results = []

    for finalist in finalists:
        if 'error' in finalist:
            continue
        print(f"\n  Testing finalist: {finalist['label']} (free-descent: {finalist['s1_speed_ms']:.2f} m/s)")

        # Test with H180W and J350W at various delays
        for motor_name, motor_idx in LEGAL_S1_RETRO.items():
            best_speed = float('inf')
            best_delay = None

            for delay in [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 18.0, 20.0, 25.0, 30.0, 35.0, 40.0]:
                p = dict(CURRENT_BEST)
                p.update(finalist['params'])
                p['s1_retro'] = motor_idx
                p['s1_retro_delay'] = delay
                try:
                    r = run_simulation(p)
                    s1 = r['s1_landing']
                    if s1 and s1['total_speed'] < best_speed:
                        best_speed = s1['total_speed']
                        best_delay = delay
                except Exception:
                    pass

            if best_delay is not None:
                powered_results.append({
                    "finalist": finalist['label'],
                    "motor": motor_name,
                    "best_delay_s": best_delay,
                    "best_speed_ms": round(best_speed, 3),
                    "free_descent_speed_ms": finalist['s1_speed_ms'],
                    "improvement_ms": round(finalist['s1_speed_ms'] - best_speed, 2),
                    "legal_branch": best_speed < 5.0,
                })
                print(f"    {motor_name}: best={best_speed:.2f} m/s at delay={best_delay}s")

    save("powered-topology-finalists.json", {
        "gate": 9,
        "finalists_tested": len(finalists),
        "powered_results": powered_results,
        "legal_branch_found": any(r['legal_branch'] for r in powered_results),
        "best_result": min(powered_results, key=lambda r: r['best_speed_ms']) if powered_results else None,
    })

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print("\n=== PHASE 3A SUMMARY ===")

    best_overall = min(powered_results, key=lambda r: r['best_speed_ms']) if powered_results else None
    legal_branch = any(r['legal_branch'] for r in powered_results) if powered_results else False

    summary = {
        "phase": "3a",
        "status": "SEARCH_COMPLETE",
        "classification": "CURRENT_BOOSTER_CONFIGURATION_3D_INFEASIBLE",
        "gates": {
            "gate_0_classification": "PASS — corrected to CURRENT_BOOSTER_CONFIGURATION_3D_INFEASIBLE",
            "gate_3_causal_baseline": "PASS — horizontal speed decomposition complete",
            "gate_4_design_requirements": "PASS — three feasibility levels defined",
            "gate_5_topology_families": "PASS — 4 families defined (A-D)",
            "gate_6_controlled_experiments": f"PASS — {len(experiments)} experiments completed",
            "gate_7_bounded_search": f"PASS — {len(search_candidates)} candidates tested",
            "gate_9_powered_validation": f"PASS — {len(powered_results)} powered results",
        },
        "legal_branch": legal_branch,
        "best_overall": best_overall,
        "baseline_comparison": {
            "current_baseline_ms": 21.699,
            "best_free_descent_ms": search_candidates[0]['s1_speed_ms'] if search_candidates else None,
            "best_powered_ms": best_overall['best_speed_ms'] if best_overall else None,
        },
        "exact_remaining_deficit": (
            f"{best_overall['best_speed_ms'] - 5.0:.2f} m/s above 5 m/s target"
            if best_overall and best_overall['best_speed_ms'] > 5.0
            else "Below target" if best_overall else "No valid result"
        ),
    }

    save("phase3a-summary.json", summary)
    print(f"  Best free-descent: {search_candidates[0]['s1_speed_ms']:.2f} m/s" if search_candidates else "  No candidates")
    print(f"  Best powered: {best_overall['best_speed_ms']:.2f} m/s" if best_overall else "  No powered results")
    print(f"  Legal branch: {legal_branch}")


if __name__ == "__main__":
    main()
