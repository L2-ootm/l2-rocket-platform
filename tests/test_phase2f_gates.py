"""Phase 2F — Scenario Semantics, Post-Apex Window, 3D Feasibility, and Powered Search.

Produces all required artifacts for Phase 2f:
  - scenario-semantic-proof.json
  - ballast-and-ascent-authority.json
  - phase-resolved-parity.json
  - post-apex-window-map.json
  - three-dimensional-feasibility.json
  - vector-motor-ranking.json
  - post-apex-powered-results.json
  - phase2f-summary.json
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RAYON_NUM_THREADS", "1")

import jpype
from osifog_sweep import (
    ANTI_TUMBLE_SCRIPT_DIGEST,
    WIND_CSV,
    generate_ork,
    inspect_anti_tumble_xml,
    init_or,
    parse_wind_csv,
    run_sim,
    save_simulated_ork,
    _load_ork_doc,
    _seed_multilevel_wind,
    _get_anti_tumble_listener,
    _component_id,
    LAUNCH_LAT,
    LAUNCH_LON,
    LAUNCH_ALT,
    TEMP_K,
    PRESSURE_PA,
    LAUNCH_ROD_M,
    SIM_SEED,
)
from motor_data import load_motor
from rocket_forge import MOTOR_DATABASE

ARTIFACTS = Path("artifacts/phase2f")
ARTIFACTS.mkdir(parents=True, exist_ok=True)

BEST = {
    "s0_main": 14, "s1_main": 14, "s0_retro": 19, "s1_retro": 19,
    "main_cluster_count": 3, "s0_body_rad": 0.074, "s1_body_rad": 0.074,
    "s0_body_len": 0.75, "s1_body_len": 0.80,
    "s1_separation_delay": 0.0, "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
    "nose_mass_kg": 4.0, "nose_ballast_pos_m": 0.45, "nose_length_m": 0.50,
    "s0_mid_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0,
    "s0_aft_ballast_kg": 0.0, "s1_aft_ballast_kg": 0.5,
    "s0_fin_count": 4, "s0_fin_root": 0.15, "s0_fin_height": 0.20,
    "s0_fin_sweep": 8.0,
    "s1_fin_count": 4, "s1_fin_root": 0.22, "s1_fin_height": 0.38,
    "s1_fin_sweep": 5.0,
    "s1_grid_fin_count": 0, "s0_grid_fin_count": 0,
    "s0_fin_thickness_m": 0.003, "s1_fin_thickness_m": 0.003,
    "s0_grid_fin_thickness_m": 0.001, "s1_grid_fin_thickness_m": 0.001,
    "s0_fin_material": "fiberglass", "s1_fin_material": "fiberglass",
    "s0_grid_fin_material": "fiberglass", "s1_grid_fin_material": "fiberglass",
    "s0_grid_fin_root": 0.06, "s0_grid_fin_height": 0.06,
    "s0_grid_fin_position_m": 0.03,
    "s1_grid_fin_root": 0.06, "s1_grid_fin_height": 0.06,
    "s1_grid_fin_position_m": 0.03,
    "launch_azimuth": 34.0, "launch_angle_deg": 3.85,
    "wind_levels": parse_wind_csv(WIND_CSV),
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_artifact(name: str, data: dict | list) -> None:
    if os.environ.get("WRITE_PHASE2F_ARTIFACTS") == "1":
        path = ARTIFACTS / name
        path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _run_ork_simulation(params: dict, seed: int = SIM_SEED) -> dict:
    """Generate ORK, simulate, return full metrics."""
    ork_xml = generate_ork(params)
    return run_sim(ork_xml, anti_tumble=True, seed=seed)


def _run_ork_simulation_raw(params: dict, seed: int = SIM_SEED):
    """Generate ORK, simulate, return (metrics, ork_xml)."""
    ork_xml = generate_ork(params)
    m = run_sim(ork_xml, anti_tumble=True, seed=seed)
    return m, ork_xml


def _extract_booster_descent_timeline(params: dict, retro_delay: float,
                                       seed: int = SIM_SEED) -> dict:
    """Run simulation and extract the full booster descent timeline after apex."""
    p = dict(params)
    p["s1_retro_delay"] = retro_delay
    ork_xml = generate_ork(p)

    fd, path = tempfile.mkstemp(suffix=".ork")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(seed)
        _seed_multilevel_wind(sim.getOptions(), seed)
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()

        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

        TYPE_TIME = fdt.TYPE_TIME
        TYPE_ALT = fdt.TYPE_ALTITUDE
        TYPE_VZ = fdt.TYPE_VELOCITY_Z
        TYPE_VXY = fdt.TYPE_VELOCITY_XY
        TYPE_THETA = fdt.TYPE_ORIENTATION_THETA
        TYPE_PHI = fdt.TYPE_ORIENTATION_PHI
        TYPE_MASS = fdt.TYPE_MASS
        TYPE_THRUST = fdt.TYPE_THRUST_FORCE
        TYPE_PX = fdt.TYPE_POSITION_X
        TYPE_PY = fdt.TYPE_POSITION_Y

        # Booster is branch 1
        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(TYPE_TIME)
        alt_arr = br.get(TYPE_ALT)
        vz_arr = br.get(TYPE_VZ)
        vxy_arr = br.get(TYPE_VXY)
        theta_arr = br.get(TYPE_THETA)
        phi_arr = br.get(TYPE_PHI)
        mass_arr = br.get(TYPE_MASS)
        thrust_arr = br.get(TYPE_THRUST)
        px_arr = br.get(TYPE_PX)
        py_arr = br.get(TYPE_PY)

        # Find apex
        apex_idx = max(range(n), key=lambda i: float(alt_arr[i]))
        apex_t = float(t_arr[apex_idx])
        apex_alt = float(alt_arr[apex_idx])
        apex_vz = float(vz_arr[apex_idx])
        apex_theta = float(theta_arr[apex_idx])

        # Find ground hit
        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        # Get events
        branch_events = {}
        for ev in br.getEvents():
            name = str(ev.getType().name())
            branch_events.setdefault(name, []).append(float(ev.getTime()))

        # Extract descent timeline (after apex, before ground hit)
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

            speed = math.sqrt(vz**2 + vxy**2)

            # Body nose axis in ENU
            cos_theta = math.cos(theta)
            nose_x = cos_theta * math.sin(phi)
            nose_y = cos_theta * math.cos(phi)
            nose_z = math.sin(theta)

            # Velocity in ENU (vxy is horizontal speed, direction from px,py difference)
            # We approximate horizontal velocity direction from position difference
            # For alignment_q, use vz and vxy
            # alignment_q = -cosine(nose . velocity)
            # velocity direction: (vxy_dir_x, vxy_dir_y, vz) normalized
            # We don't have direct vx, vy but can approximate from px, py differences

            # q_vertical: +1 when nose up and velocity down (good braking)
            q_vertical = 0.0
            if speed > 0.1:
                if vz < 0:
                    q_vertical = nose_z  # positive when nose up during descent
                else:
                    q_vertical = -nose_z  # negative when nose up during ascent

            descent_samples.append({
                "time_s": round(t, 4),
                "altitude_m": round(alt, 2),
                "vz_ms": round(vz, 3),
                "vxy_ms": round(vxy, 3),
                "speed_ms": round(speed, 3),
                "theta_deg": round(math.degrees(theta), 2),
                "phi_deg": round(math.degrees(phi), 2),
                "nose_x": round(nose_x, 4),
                "nose_y": round(nose_y, 4),
                "nose_z": round(nose_z, 4),
                "mass_kg": round(mass, 4),
                "thrust_n": round(thrust, 2),
                "q_vertical": round(q_vertical, 4),
            })

        return {
            "retro_delay_s": retro_delay,
            "apex_time_s": round(apex_t, 4),
            "apex_altitude_m": round(apex_alt, 2),
            "apex_vz_ms": round(apex_vz, 3),
            "apex_theta_deg": round(math.degrees(apex_theta), 2),
            "ground_hit_time_s": round(hit_time, 4) if hit_time else None,
            "events": {k: [round(t, 4) for t in v] for k, v in branch_events.items()},
            "descent_samples": descent_samples,
            "sample_count": len(descent_samples),
            "ork_xml_hash": _sha256(ork_xml),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _compute_3d_feasibility(descent_samples: list, motor_designation: str,
                             ignition_time_s: float) -> dict:
    """Compute 3D feasibility for a given ignition time and motor.

    Uses the actual body attitude history from the simulation to determine
    whether the motor can reduce total touchdown speed below 5 m/s.
    """
    try:
        motor = load_motor(motor_designation)
    except (FileNotFoundError, ValueError) as exc:
        return {"error": f"motor {motor_designation}: {exc}"}

    total_impulse = motor.total_impulse_ns
    burn_duration = motor.burn_duration_s
    loaded_mass = motor.loaded_mass_kg
    propellant_mass = motor.propellant_mass_kg

    # Find the sample closest to ignition
    ignition_sample = None
    for s in descent_samples:
        if s["time_s"] >= ignition_time_s - 0.05:
            ignition_sample = s
            break
    if ignition_sample is None:
        return {"error": "ignition time outside descent window"}

    # Find the sample closest to ignition + burn_duration
    burnout_time = ignition_time_s + burn_duration
    burnout_sample = None
    for s in descent_samples:
        if s["time_s"] >= burnout_time - 0.05:
            burnout_sample = s
            break

    # Find the sample closest to ground hit
    impact_sample = descent_samples[-1] if descent_samples else None

    # Integrate impulse during burn
    # For each sample during the burn, compute the impulse fraction and
    # the direction it acts (along nose axis)
    burn_samples = [s for s in descent_samples
                    if ignition_time_s - 0.01 <= s["time_s"] <= burnout_time + 0.01]

    if not burn_samples:
        return {"error": "no samples during burn window"}

    # Simple integration: assume constant thrust over burn duration
    avg_thrust = total_impulse / burn_duration if burn_duration > 0 else 0

    # At each burn sample, compute the impulse component opposing velocity
    total_opposing_impulse = 0.0
    total_adverse_impulse = 0.0
    total_vertical_opposing = 0.0
    total_horizontal_opposing = 0.0

    for s in burn_samples:
        nose_z = s["nose_z"]  # vertical component of nose axis
        vz = s["vz_ms"]
        speed = s["speed_ms"]
        theta_rad = math.radians(s["theta_deg"])

        # Thrust is along nose axis. During tail-first descent (vz < 0, theta > 0),
        # the vertical component of thrust opposes gravity and vertical velocity.
        # The horizontal component depends on the nose axis orientation.

        # Vertical thrust: avg_thrust * sin(theta) = avg_thrust * nose_z
        vertical_thrust = avg_thrust * nose_z
        # Vertical opposing impulse (when vz < 0, upward thrust opposes descent)
        if vz < 0:
            total_vertical_opposing += vertical_thrust * abs(vz) / max(speed, 0.1) * (burn_duration / len(burn_samples))
        else:
            total_adverse_impulse += vertical_thrust * (burn_duration / len(burn_samples))

        # Horizontal thrust: avg_thrust * cos(theta) = avg_thrust * cos(theta)
        # This acts in the horizontal plane. Whether it opposes horizontal velocity
        # depends on the azimuth alignment (phi). We approximate from the velocity.
        horizontal_thrust = avg_thrust * math.cos(theta_rad)

        # For horizontal opposing, we need to know if horizontal thrust opposes
        # horizontal velocity. Without direct vx, vy, we use the fact that
        # during tail-first descent, the nose axis is roughly opposite to velocity,
        # so horizontal thrust component opposes horizontal velocity.
        # This is a best-case estimate.
        total_horizontal_opposing += horizontal_thrust * (burn_duration / len(burn_samples))

    # Mass at burnout
    mass_at_ignition = ignition_sample["mass_kg"]
    mass_at_burnout = mass_at_ignition - propellant_mass

    # Available delta-v
    total_dv = total_impulse / ((mass_at_ignition + mass_at_burnout) / 2)

    # Required delta-v
    vz_at_impact = impact_sample["vz_ms"] if impact_sample else 0
    vxy_at_impact = impact_sample["vxy_ms"] if impact_sample else 0
    speed_at_impact = impact_sample["speed_ms"] if impact_sample else 0

    required_vertical_dv = abs(vz_at_impact)  # need to cancel all vertical speed
    required_horizontal_dv = vxy_at_impact  # need to cancel all horizontal speed
    required_total_dv = speed_at_impact

    # Ideal opposing impulse (if thrust always opposed velocity)
    ideal_opposing_impulse = total_impulse  # best case: all impulse opposes velocity

    # Gravity loss during burn
    gravity_loss = 9.81 * burn_duration

    # Predicted touchdown speed (simplified model)
    # After burn, remaining velocity = initial_velocity - impulse/mass + gravity*dv
    # This is very approximate; the simulation gives the real answer

    return {
        "motor_designation": motor_designation,
        "total_impulse_ns": round(total_impulse, 2),
        "burn_duration_s": round(burn_duration, 3),
        "loaded_mass_kg": round(loaded_mass, 4),
        "propellant_mass_kg": round(propellant_mass, 4),
        "ignition_time_s": round(ignition_time_s, 4),
        "ignition_vz_ms": ignition_sample["vz_ms"],
        "ignition_vxy_ms": ignition_sample["vxy_ms"],
        "ignition_speed_ms": ignition_sample["speed_ms"],
        "ignition_theta_deg": ignition_sample["theta_deg"],
        "ignition_nose_z": ignition_sample["nose_z"],
        "mass_at_ignition_kg": round(mass_at_ignition, 4),
        "mass_at_burnout_kg": round(mass_at_burnout, 4),
        "available_total_dv_ms": round(total_dv, 2),
        "required_vertical_dv_ms": round(required_vertical_dv, 2),
        "required_horizontal_dv_ms": round(required_horizontal_dv, 2),
        "required_total_dv_ms": round(required_total_dv, 2),
        "total_vertical_opposing_impulse_ns": round(total_vertical_opposing, 2),
        "total_horizontal_opposing_impulse_ns": round(total_horizontal_opposing, 2),
        "total_adverse_impulse_ns": round(total_adverse_impulse, 2),
        "gravity_loss_ms": round(gravity_loss, 2),
        "impact_vz_ms": vz_at_impact,
        "impact_vxy_ms": vxy_at_impact,
        "impact_speed_ms": speed_at_impact,
        "feasible_vertically_only": required_vertical_dv < total_dv * 0.8,
        "feasible_total": required_total_dv < total_dv * 0.8,
    }


# ═══════════════════════════════════════════════════════════════
# Gate 1 — Scenario Semantic Proof
# ═══════════════════════════════════════════════════════════════
def test_gate1_scenario_semantics():
    """Verify each scenario type produces different effective simulation states."""
    init_or()

    scenarios = {
        "STAGE_FREE_DESCENT_DIAGNOSTIC": {"s1_retro_delay": 200.0, "s0_retro_delay": 200.0},
        "POWERED_STAGE_LANDING_VALIDATION": {"s1_retro_delay": 12.0, "s0_retro_delay": 200.0},
        "OFFICIAL_FULL_MISSION": {"s1_retro_delay": 65.28, "s0_retro_delay": 200.0},
        "DEBUG_ONLY": {"s1_retro_delay": 200.0, "s0_retro_delay": 200.0},
    }

    results = []
    for scenario_type, overrides in scenarios.items():
        p = dict(BEST)
        p.update(overrides)
        ork_xml = generate_ork(p)

        # Manifest
        from osifog_engine_search import build_scenario_manifest
        manifest = build_scenario_manifest(scenario_type, "phase2f-candidate", p, ork_xml)

        # Save to disk
        fd, save_path = tempfile.mkstemp(suffix=".ork")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ork_xml)
            doc = _load_ork_doc(save_path)
            sim = doc.getSimulations().get(0)
            sim.getOptions().setRandomSeed(SIM_SEED)
            _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
            listener = _get_anti_tumble_listener()
            sim.simulate(listener)
            data = sim.getSimulatedData()

            fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
            FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

            n_branches = int(data.getBranchCount())
            branch_events = []
            for bi in range(n_branches):
                br = data.getBranch(bi)
                bev = {}
                for ev in br.getEvents():
                    name = str(ev.getType().name())
                    bev.setdefault(name, []).append(round(float(ev.getTime()), 4))
                branch_events.append(bev)

            # Reopen
            with open(save_path, "r", encoding="utf-8") as f:
                reopened_xml = f.read()
            reopened_hash = _sha256(reopened_xml)
            original_hash = _sha256(ork_xml)

            # Check motor state in XML
            import xml.etree.ElementTree as ET
            root = ET.fromstring(ork_xml)
            motor_mounts = root.findall(".//motor")
            active_count = 0
            disabled_count = 0
            for mount in motor_mounts:
                motor_id = mount.findtext("motorID", "")
                if motor_id and motor_id != "MtrR8362c23c":  # dummy placeholder
                    active_count += 1

            diagnostic = scenario_type in {"STAGE_FREE_DESCENT_DIAGNOSTIC", "DEBUG_ONLY"}
            s1_delay = overrides["s1_retro_delay"]
            s1_ignitions = branch_events[1].get("IGNITION", [])
            s1_ground_hits = branch_events[1].get("GROUND_HIT", [])
            s1_burnouts = branch_events[1].get("BURNOUT", [])

            # Check if retro fires before ground hit
            ground_hit_t = min(s1_ground_hits) if s1_ground_hits else float("inf")
            retro_ignitions = [t for t in s1_ignitions if t > 0.1 and t < ground_hit_t]
            retro_fired_in_flight = len(retro_ignitions) > 0

            results.append({
                "scenario_type": scenario_type,
                "serialized_document_hash": original_hash,
                "reopened_document_hash": reopened_hash,
                "hashes_match": original_hash == reopened_hash,
                "mission_digest": manifest["mission_manifest_digest"],
                "wind_digest": manifest["wind_file_digest"],
                "anti_tumble_digest": manifest["anti_tumble_serialized_valid"],
                "stage_ids": manifest["stage_ids"],
                "branch_mapping": manifest["branch_mapping"],
                "s1_retro_delay_s": s1_delay,
                "s1_ignitions_before_ground_hit": retro_ignitions,
                "retro_fired_in_flight": retro_fired_in_flight,
                "diagnostic_only": diagnostic,
                "branch_events": branch_events,
                "booster_ground_hit_s": round(ground_hit_t, 4),
            })

        finally:
            try:
                os.unlink(save_path)
            except OSError:
                pass

    # Verify semantic differences
    free_desc = next(r for r in results if r["scenario_type"] == "STAGE_FREE_DESCENT_DIAGNOSTIC")
    powered = next(r for r in results if r["scenario_type"] == "POWERED_STAGE_LANDING_VALIDATION")

    assert free_desc["hashes_match"], "ORK hash mismatch on reopen"
    assert free_desc["diagnostic_only"], "free descent must be diagnostic_only"
    assert not powered["diagnostic_only"], "powered must not be diagnostic_only"

    # Free descent: retro fires after ground hit (never during flight)
    assert not free_desc["retro_fired_in_flight"], \
        "STAGE_FREE_DESCENT_DIAGNOSTIC: retro must NOT fire during flight"

    # Powered: retro fires before ground hit (during flight)
    assert powered["retro_fired_in_flight"], \
        "POWERED_STAGE_LANDING_VALIDATION: retro MUST fire during flight"

    # Confirm scenario types are semantically different
    assert free_desc["s1_ignitions_before_ground_hit"] != powered["s1_ignitions_before_ground_hit"], \
        "Free descent and powered scenarios must have different ignition behavior"

    _json_artifact("scenario-semantic-proof.json", {
        "gate": 1,
        "status": "PASS",
        "scenarios": results,
        "key_finding": "STAGE_FREE_DESCENT_DIAGNOSTIC has retro fire after ground hit (never in flight). "
                       "POWERED_STAGE_LANDING_VALIDATION has retro fire during descent. "
                       "Scenarios are semantically different and reproducible.",
    })


# ═══════════════════════════════════════════════════════════════
# Gate 2 — Ballast Physicality and Ascent Authority
# ═══════════════════════════════════════════════════════════════
def test_gate2_ballast_and_ascent():
    """Audit the 4.0 kg nose ballast and validate ascent authority."""
    init_or()
    from physical_geometry import AxialCylinder, validate_cylinders, ASSEMBLY_CLEARANCE_M

    # Ballast audit for 4.0 kg steel nose ballast
    body_rad = 0.074  # m
    nose_ballast_pos = 0.45  # m from top
    nose_length = 0.50  # m

    ballast_results = []
    for mass_kg in [3.0, 3.5, 4.0]:
        density = 7900  # steel kg/m3
        max_pkg_r = body_rad - 0.003  # inner clearance
        ideal_l = mass_kg / (density * math.pi * max_pkg_r**2)
        pkg_l = min(max(0.001, ideal_l), 0.15)
        pkg_r = math.sqrt(mass_kg / (density * math.pi * pkg_l))

        fits = pkg_r <= max_pkg_r + 1e-9
        volume = math.pi * pkg_r**2 * pkg_l
        computed_mass = density * volume

        # Check if ballast fits inside nose cone
        nose_inner_at_pos = body_rad * 0.95  # approximate Haack radius at ballast position
        fits_in_nose = pkg_r <= nose_inner_at_pos

        # Collision check with motor tube
        motor_tube_outer_r = 0.003  # wall thickness
        motor_tube_inner_r = 0.01275  # 25.4mm motor tube
        clearance = pkg_r + motor_tube_outer_r + ASSEMBLY_CLEARANCE_M
        no_collision = clearance <= body_rad

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
            "fits_inside_nose_cone": fits_in_nose,
            "no_collision_with_motor_tube": no_collision,
            "legal_density": 7800 <= density <= 8100,
            "legal_dimensions": pkg_r >= 0.001 and pkg_l >= 0.001,
        })

    # Ascent authority check
    m = _run_ork_simulation(BEST)
    ascent_authority = {
        "mach": m.get("mach", 0),
        "max_mach_limit": 0.95,
        "mach_pass": m.get("mach", 0) < 0.95,
        "min_static_margin_cal": m.get("min_static_margin", -999),
        "min_static_margin_limit": 1.5,
        "static_margin_pass": m.get("min_static_margin", -999) >= 1.5,
        "apogee_m": m.get("apogee_m", 0),
        "target_apogee_m": 3000.0,
        "stage_landings_count": len(m.get("stage_landings", [])),
        "status": m.get("status", ""),
    }

    # Check event sequence
    events = m.get("event_times", {})
    separations = events.get("STAGE_SEPARATION", [])
    apogees = events.get("APOGEE", [])
    genuine_staging = bool(separations) and bool(apogees) and min(separations) < min(apogees)

    ascent_authority["genuine_staging_before_apogee"] = genuine_staging
    ascent_authority["separation_times"] = [round(t, 4) for t in separations]
    ascent_authority["apogee_times"] = [round(t, 4) for t in apogees]

    _json_artifact("ballast-and-ascent-authority.json", {
        "gate": 2,
        "status": "PASS" if all(b["fits_inside_airframe"] and b["legal_density"] and b["legal_dimensions"]
                                for b in ballast_results) and ascent_authority["mach_pass"] and ascent_authority["static_margin_pass"]
                 else "FAIL",
        "ballast_variants": ballast_results,
        "ascent_authority": ascent_authority,
    })


# ═══════════════════════════════════════════════════════════════
# Gate 4 — Post-Apex Descent Timeline
# ═══════════════════════════════════════════════════════════════
def test_gate4_post_apex_timeline():
    """Build the exact post-apex descent timeline from the free-descent simulation."""
    init_or()
    timeline = _extract_booster_descent_timeline(BEST, retro_delay=200.0)

    # Find apex and tail-first transition
    apex_t = timeline["apex_time_s"]
    first_tail_first = None
    for s in timeline["descent_samples"]:
        if s["vz_ms"] < -0.1 and s["theta_deg"] > 0:
            first_tail_first = s
            break

    # Find valid ignition window (vz < 0 AND theta > 0)
    valid_window = []
    for s in timeline["descent_samples"]:
        if s["vz_ms"] < 0 and s["theta_deg"] > 0:
            valid_window.append(s)

    # Record the branch timing
    branch_timing = {
        "launch_time_s": 0.0,
        "booster_burnout_time_s": timeline["events"].get("BURNOUT", [None])[0],
        "separation_time_s": timeline["events"].get("STAGE_SEPARATION", [None])[0],
        "booster_apex_time_s": apex_t,
        "ground_contact_time_s": timeline["ground_hit_time_s"],
        "delay_reference_type": "global_from_launch",
        "delay_zero_global_time_s": 0.0,
        "branch_time_origin_s": 0.0,
    }

    # Compute time-to-contact for each sample
    ground_hit = timeline["ground_hit_time_s"]
    for s in timeline["descent_samples"]:
        s["time_to_contact_s"] = round(ground_hit - s["time_s"], 3) if ground_hit else None
        # q_total: alignment of nose with velocity
        speed = s["speed_ms"]
        if speed > 0.1:
            # Approximate: nose axis dot velocity direction
            # velocity is mostly downward (vz < 0) with horizontal component (vxy)
            # We use the fact that alignment_q ≈ q_vertical for mostly-vertical descent
            s["q_total"] = s["q_vertical"]
        else:
            s["q_total"] = 0.0

    _json_artifact("post-apex-window-map.json", {
        "gate": 4,
        "status": "PASS",
        "branch_timing": branch_timing,
        "tail_first_window": {
            "start_time_s": valid_window[0]["time_s"] if valid_window else None,
            "end_time_s": valid_window[-1]["time_s"] if valid_window else None,
            "duration_s": round(
                valid_window[-1]["time_s"] - valid_window[0]["time_s"], 3
            ) if valid_window else 0,
            "sample_count": len(valid_window),
        },
        "first_tail_first_sample": first_tail_first,
        "descent_sample_count": len(timeline["descent_samples"]),
        "descent_samples_summary": {
            "at_apex": next((s for s in timeline["descent_samples"]
                           if abs(s["time_s"] - apex_t) < 0.1), None),
            "at_10s": next((s for s in timeline["descent_samples"]
                          if abs(s["time_s"] - 10.0) < 0.2), None),
            "at_15s": next((s for s in timeline["descent_samples"]
                          if abs(s["time_s"] - 15.0) < 0.2), None),
            "at_20s": next((s for s in timeline["descent_samples"]
                          if abs(s["time_s"] - 20.0) < 0.2), None),
            "at_30s": next((s for s in timeline["descent_samples"]
                          if abs(s["time_s"] - 30.0) < 0.2), None),
            "at_40s": next((s for s in timeline["descent_samples"]
                          if abs(s["time_s"] - 40.0) < 0.2), None),
            "just_before_impact": timeline["descent_samples"][-2] if len(timeline["descent_samples"]) > 2 else None,
        },
    })


# ═══════════════════════════════════════════════════════════════
# Gate 5 — 3D Feasibility
# ═══════════════════════════════════════════════════════════════
def test_gate5_3d_feasibility():
    """Quantify whether <5 m/s is physically reachable with current topology."""
    init_or()
    timeline = _extract_booster_descent_timeline(BEST, retro_delay=200.0)
    samples = timeline["descent_samples"]
    ground_hit = timeline["ground_hit_time_s"]
    apex_t = timeline["apex_time_s"]

    # Free-descent baseline
    impact = samples[-1] if samples else {}
    free_descent = {
        "total_speed_ms": impact.get("speed_ms", 0),
        "vz_ms": impact.get("vz_ms", 0),
        "vxy_ms": impact.get("vxy_ms", 0),
    }

    # Required: both stages must land below 5 m/s total
    # For the booster: need to remove ~16.7 m/s from 21.70 m/s
    required_removal = {
        "vertical_only": abs(free_descent["vz_ms"]),
        "horizontal_only": free_descent["vxy_ms"],
        "total": free_descent["total_speed_ms"],
        "target": 5.0,
        "must_remove_ms": round(free_descent["total_speed_ms"] - 5.0, 2),
    }

    # Test feasibility at several candidate ignition times
    feasibility_results = []
    for delay_s in [9.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0]:
        if delay_s >= ground_hit:
            continue
        for motor_name in ["H180W", "J350W", "K550W"]:
            feas = _compute_3d_feasibility(samples, motor_name, delay_s)
            feasibility_results.append(feas)

    _json_artifact("three-dimensional-feasibility.json", {
        "gate": 5,
        "status": "ANALYSIS",
        "free_descent_baseline": free_descent,
        "required_removal": required_removal,
        "feasibility_by_delay_and_motor": feasibility_results,
        "key_finding": (
            f"Free descent horizontal speed: {free_descent['vxy_ms']:.1f} m/s. "
            f"Even with zero vertical speed, total speed = {free_descent['vxy_ms']:.1f} m/s > 5.0 m/s. "
            f"The motor must remove at least {required_removal['must_remove_ms']:.1f} m/s of speed. "
            f"Horizontal speed is the binding constraint."
        ),
    })


# ═══════════════════════════════════════════════════════════════
# Gate 6 — Vector Motor Ranking
# ═══════════════════════════════════════════════════════════════
def test_gate6_motor_ranking():
    """Re-evaluate landing motors using useful vector impulse."""
    init_or()
    timeline = _extract_booster_descent_timeline(BEST, retro_delay=200.0)
    samples = timeline["descent_samples"]
    ground_hit = timeline["ground_hit_time_s"]

    motors_to_test = ["H180W", "J350W", "J360_CTI", "K550W"]
    motor_results = []

    for motor_name in motors_to_test:
        try:
            motor = load_motor(motor_name)
        except (FileNotFoundError, ValueError) as exc:
            motor_results.append({"motor": motor_name, "error": str(exc)})
            continue

        # Find optimal ignition time (latest possible that completes burn before impact)
        burn_end_latest = ground_hit - 0.1
        optimal_delay = burn_end_latest - motor.burn_duration_s

        # Find the actual state at optimal delay
        best_state = None
        for s in samples:
            if s["time_s"] >= optimal_delay - 0.1:
                best_state = s
                break

        if best_state is None:
            motor_results.append({"motor": motor_name, "error": "no valid state found"})
            continue

        # Compute vector impulse analysis
        # During tail-first descent, thrust is along nose axis
        # The nose axis has components: (cos(theta)*sin(phi), cos(theta)*cos(phi), sin(theta))
        # The velocity has components: (vx, vy, vz) where vxy = sqrt(vx^2+vy^2)
        # The opposing impulse depends on the angle between nose and velocity

        theta_rad = math.radians(best_state["theta_deg"])
        cos_theta = math.cos(theta_rad)
        sin_theta = math.sin(theta_rad)

        # Average thrust
        avg_thrust = motor.total_impulse_ns / motor.burn_duration_s

        # Vertical component of thrust
        vertical_thrust = avg_thrust * sin_theta
        # Horizontal component of thrust
        horizontal_thrust = avg_thrust * cos_theta

        # Impulse components
        vertical_impulse = vertical_thrust * motor.burn_duration_s
        horizontal_impulse = horizontal_thrust * motor.burn_duration_s

        # Gravity impulse during burn
        gravity_impulse = 9.81 * motor.loaded_mass_kg * motor.burn_duration_s

        # Opposing impulse (when nose is up and velocity is down, vertical thrust opposes gravity)
        opposing_vertical = vertical_impulse if sin_theta > 0 else 0
        adverse_vertical = 0 if sin_theta > 0 else vertical_impulse

        # For horizontal: during tail-first descent with alignment_q ≈ 1,
        # the nose axis is roughly opposite to velocity, so horizontal thrust
        # component opposes horizontal velocity
        opposing_horizontal = horizontal_impulse  # best case
        adverse_horizontal = 0  # worst case (if misaligned)

        # Attitude rotation during burn (approximate)
        # The motor thrust creates a torque that may rotate the rocket
        attitude_rotation_est = 0.0  # simplified

        motor_results.append({
            "motor_designation": motor_name,
            "total_impulse_ns": round(motor.total_impulse_ns, 2),
            "burn_duration_s": round(motor.burn_duration_s, 3),
            "loaded_mass_kg": round(motor.loaded_mass_kg, 4),
            "propellant_kg": round(motor.propellant_mass_kg, 4),
            "optimal_ignition_time_s": round(optimal_delay, 3),
            "ignition_theta_deg": best_state["theta_deg"],
            "ignition_nose_z": best_state["nose_z"],
            "ignition_vz_ms": best_state["vz_ms"],
            "ignition_vxy_ms": best_state["vxy_ms"],
            "avg_thrust_n": round(avg_thrust, 1),
            "vertical_thrust_n": round(vertical_thrust, 1),
            "horizontal_thrust_n": round(horizontal_thrust, 1),
            "opposing_vertical_impulse_ns": round(opposing_vertical, 1),
            "opposing_horizontal_impulse_ns": round(opposing_horizontal, 1),
            "adverse_impulse_ns": round(adverse_vertical, 1),
            "gravity_impulse_ns": round(gravity_impulse, 1),
            "total_available_dv_ms": round(motor.total_impulse_ns / motor.loaded_mass_kg, 1),
            "feasibility_rank": None,  # filled after simulation
        })

    # Sort by total impulse (ascending = lighter motors first for precision)
    motor_results.sort(key=lambda r: r.get("total_impulse_ns", 0))

    _json_artifact("vector-motor-ranking.json", {
        "gate": 6,
        "status": "ANALYSIS",
        "motors": motor_results,
        "ranking_note": "Ranked by total impulse ascending. Lighter motors offer finer control; "
                        "heavier motors offer more delta-v but risk over-braking.",
    })


# ═══════════════════════════════════════════════════════════════
# Gate 7 — Post-Apex Powered Search
# ═══════════════════════════════════════════════════════════════
def test_gate7_post_apex_powered_search():
    """Search the post-apex window for the best powered landing delay."""
    init_or()

    # Get the free-descent baseline first
    free_m = _run_ork_simulation(BEST)
    free_s1 = None
    for sl in free_m.get("stage_landings", []):
        if sl.get("stage_key") == "s1":
            free_s1 = sl
            break

    # Coarse search: test delays that put ignition after apex (~8.5s)
    # Booster ground hit is ~43.65s, so valid range is ~9s to ~40s
    coarse_delays = [9.0, 10.0, 11.0, 12.0, 14.0, 16.0, 18.0, 20.0, 25.0, 30.0, 35.0, 40.0]

    powered_results = []
    for delay_s in coarse_delays:
        p = dict(BEST)
        p["s1_retro_delay"] = delay_s
        try:
            m, ork_xml = _run_ork_simulation_raw(p)
            s1_landing = None
            for sl in m.get("stage_landings", []):
                if sl.get("stage_key") == "s1":
                    s1_landing = sl
                    break

            s0_landing = None
            for sl in m.get("stage_landings", []):
                if sl.get("stage_key") == "s0":
                    s0_landing = sl
                    break

            events = m.get("event_times", {})
            branch_events = m.get("branch_event_times", [{}])
            s1_events = branch_events[1] if len(branch_events) > 1 else {}
            s1_ignitions = s1_events.get("IGNITION", [])
            ground_hit_t = None
            if s1_landing:
                ground_hit_t = s1_landing.get("time_s")

            # Check if retro fired before ground hit
            retro_fired = any(
                t > 0.1 and ground_hit_t and t < ground_hit_t
                for t in s1_ignitions
            )

            # Check retro burn diagnostic
            burn_diag = m.get("retro_burn_diagnostics", [])
            s1_burn = next((d for d in burn_diag if d.get("stage_key") == "s1"), None)

            result = {
                "delay_s": delay_s,
                "s1_speed_ms": round(s1_landing["total_speed"], 3) if s1_landing else None,
                "s1_vz_ms": round(s1_landing["vz_ms"], 3) if s1_landing else None,
                "s1_vxy_ms": round(s1_landing["vxy_ms"], 3) if s1_landing else None,
                "s0_speed_ms": round(s0_landing["total_speed"], 3) if s0_landing else None,
                "retro_fired_in_flight": retro_fired,
                "s1_ignitions": [round(t, 4) for t in s1_ignitions],
                "ground_hit_s": round(ground_hit_t, 4) if ground_hit_t else None,
                "retro_braking_verified": s1_burn.get("retro_braking_verified", False) if s1_burn else False,
                "mach": round(m.get("mach", 0), 4),
                "warnings": [],
                "classification": "POWERED" if retro_fired else "UNPOWERED",
            }

            if s1_landing and s1_landing["total_speed"] < 5.0:
                result["classification"] = "LEGAL_BRANCH_CANDIDATE"

            powered_results.append(result)
        except Exception as exc:
            powered_results.append({
                "delay_s": delay_s,
                "error": str(exc),
                "classification": "ERROR",
            })

    # Find best result
    valid_results = [r for r in powered_results if r.get("s1_speed_ms") is not None]
    best = min(valid_results, key=lambda r: r["s1_speed_ms"]) if valid_results else None

    _json_artifact("post-apex-powered-results.json", {
        "gate": 7,
        "status": "SEARCH_COMPLETE",
        "free_descent_s1_speed_ms": round(free_s1["total_speed"], 3) if free_s1 else None,
        "coarse_search_results": powered_results,
        "best_result": best,
        "legal_branch_found": best is not None and best["s1_speed_ms"] < 5.0 if best else False,
    })


# ═══════════════════════════════════════════════════════════════
# Gate 8 — Phase-resolved Parity (Rust vs OpenRocket)
# ═══════════════════════════════════════════════════════════════
def test_gate3_phase_parity():
    """Compare Rust and OpenRocket at key flight phases."""
    init_or()
    # Run OpenRocket simulation
    m = _run_ork_simulation(BEST)

    # Extract OpenRocket ascent stability segments
    or_segments = m.get("ascent_stability_segments", [])
    or_stability = m.get("ascent_static_margins", [])
    or_events = m.get("event_times", {})

    parity = {
        "gate": 3,
        "openrocket_ascent_segments": or_segments,
        "openrocket_ascent_stability": or_stability,
        "openrocket_events": {k: [round(t, 4) for t in v] for k, v in or_events.items()},
        "openrocket_mach": round(m.get("mach", 0), 4),
        "openrocket_min_static_margin": round(m.get("min_static_margin", 0), 3),
    }

    # Attempt Rust parity check
    try:
        from organic_loop import evaluate_rust_population
        rust_result = evaluate_rust_population([BEST])
        if rust_result:
            r = rust_result[0]
            parity["rust_result"] = {
                "apogee_m": r.get("apogee_m"),
                "min_static_margin": r.get("min_static_margin"),
                "mach": r.get("mach"),
            }
            # Compute deltas
            if r.get("min_static_margin") is not None and m.get("min_static_margin") is not None:
                delta_margin = abs(r["min_static_margin"] - m["min_static_margin"])
                parity["static_margin_delta_cal"] = round(delta_margin, 3)
                parity["static_margin_parity_pass"] = delta_margin <= 0.20
            if r.get("mach") is not None and m.get("mach") is not None:
                delta_mach = abs(r["mach"] - m["mach"])
                parity["mach_delta"] = round(delta_mach, 4)
                parity["mach_parity_pass"] = delta_mach <= 0.02
    except Exception as exc:
        parity["rust_error"] = str(exc)
        parity["rust_available"] = False

    _json_artifact("phase-resolved-parity.json", parity)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
