#!/usr/bin/env python3
"""Motor-on attitude flip diagnosis (mission section 11).

Phase 4A found every powered candidate lands around 57 m/s, worse than free
descent (~14-15 m/s best), with the whole-descent mean alignment `q` flipping
sign between free descent and powered runs. That comparison mixes the free-
fall portion of the powered run with the actual burn window, so it cannot by
itself localize the cause.

This script reruns a representative Phase 4A candidate (family E8, the best
free-descent family) through OpenRocket with a retro motor at several delays,
and decomposes the descent into:

  1. pre-ignition free-fall alignment q(t)      (thrust <= 1 N)
  2. burn-window-only alignment, via the existing tested
     `_retro_burn_diagnostic` helper (thrust > 1 N samples only)
  3. finite-difference angular rate d(theta)/dt around ignition
  4. speed(t) to locate the near-hover minimum

Because the airframe is axisymmetric (nose ballast on the centerline, 3 main
motors in a symmetric ring, one retro motor on the centerline -- see
`_falcon_cluster_geometry` in osifog_sweep.py) the thrust line passes through
the CG by construction for every sample: r_thrust and F_thrust are both purely
axial, so M_thrust = r x F = 0 exactly, with no numerical integration needed
to demonstrate it. This script does not compute M_thrust from force data; it
records the fact and focuses on distinguishing the remaining mechanisms in
section 11 (aerodynamic moment collapse under falling dynamic pressure vs.
inertial/mass-depletion coupling vs. an event/frame artifact) using timing
correlation between ignition, the q sign flip, and the speed minimum.
"""
import json
import math
import os
import sys
import tempfile

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, parse_wind_csv, WIND_CSV, MOTOR_DATABASE,
    _retro_burn_diagnostic, _finite_difference,
)
from scripts.descent_gates import apex_time_from_apogee_events

ARTIFACTS = "artifacts/autoevo"
os.makedirs(ARTIFACTS, exist_ok=True)

E8_8 = {
    's0_main': 14, 's1_main': 14, 's0_retro': 19, 's1_retro': 19,
    'main_cluster_count': 3, 's0_body_rad': 0.074, 's1_body_rad': 0.074,
    's0_body_len': 0.75, 's1_body_len': 1.0,
    's1_separation_delay': 0.0, 's0_retro_delay': 200.0, 's1_retro_delay': 200.0,
    'nose_mass_kg': 4.0, 'nose_ballast_pos_m': 0.45, 'nose_length_m': 0.50,
    's0_mid_ballast_kg': 0.0, 's1_mid_ballast_kg': 0.0,
    's0_aft_ballast_kg': 0.0, 's1_aft_ballast_kg': 0.0,
    's0_fin_count': 4, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's0_fin_sweep': 8.0,
    's1_fin_count': 8, 's1_fin_root': 0.22, 's1_fin_height': 0.70, 's1_fin_sweep': 5.0,
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


def _min_speed_after_ignition(trace, ignition_t, hit_time):
    """Minimum-speed sample after ignition, excluding ground-contact/null-q.

    OpenRocket zeroes velocity (and this script's trace records q=None) at
    and after GROUND_HIT, so an unfiltered min() over ``speed_ms`` always
    picks that zeroed ground-hit sample instead of a genuine near-hover
    point during the actual flight.
    """
    candidates = [
        pt for pt in trace
        if (ignition_t is None or pt["t"] >= ignition_t)
        and pt["q"] is not None
        and (hit_time is None or pt["t"] < hit_time)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda pt: pt["speed_ms"])


def find_motor_index(name):
    for i, m in enumerate(MOTOR_DATABASE):
        if m[1] == name:
            return i
    raise ValueError(f"motor {name} not in MOTOR_DATABASE")


def run_diagnosis(params, label):
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

        br = data.getBranch(1)  # booster branch
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        mass_arr = br.get(fdt.TYPE_MASS)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)
        stability_arr = br.get(fdt.TYPE_STABILITY)

        apogee_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.APOGEE
        )
        apex_t = apex_time_from_apogee_events(apogee_events, t_arr, alt_arr)

        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        ignition_t = None
        for i in range(n):
            if float(t_arr[i]) > apex_t and float(thrust_arr[i]) > 1.0:
                ignition_t = float(t_arr[i])
                break

        # Full per-sample q(t) + angular-rate trace across the descent.
        trace = []
        prev_theta = None
        prev_t = None
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t:
                continue
            vz = float(vz_arr[i])
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            vx = _finite_difference(px_arr, t_arr, i)
            vy = _finite_difference(py_arr, t_arr, i)
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            if speed > 0.5:
                cos_th = math.cos(theta)
                nose_x = cos_th * math.sin(phi)
                nose_y = cos_th * math.cos(phi)
                nose_z = math.sin(theta)
                vel_dot = nose_x * vx + nose_y * vy + nose_z * vz
                q = -vel_dot / speed
            else:
                q = None
            theta_rate = None
            if prev_theta is not None and prev_t is not None and t > prev_t:
                theta_rate = (theta - prev_theta) / (t - prev_t)
            prev_theta, prev_t = theta, t
            trace.append({
                "t": round(t, 4),
                "thrust_n": round(float(thrust_arr[i]), 2),
                "speed_ms": round(speed, 3),
                "q": round(q, 4) if q is not None else None,
                "theta_rate_rad_s": round(theta_rate, 4) if theta_rate is not None else None,
                "stability_cal": round(float(stability_arr[i]), 4),
                "mass_kg": round(float(mass_arr[i]), 4),
            })

        burn_diag = _retro_burn_diagnostic(
            t_arr, px_arr, py_arr, vz_arr, theta_arr, phi_arr, thrust_arr, apex_t,
        )

        # Locate q sign flip and speed minimum relative to ignition.
        flip_t = None
        prev_q = None
        for pt in trace:
            if pt["q"] is None:
                continue
            if prev_q is not None and prev_q >= 0 and pt["q"] < 0 and pt["t"] >= (ignition_t or 0):
                flip_t = pt["t"]
                break
            prev_q = pt["q"]

        min_speed_pt = _min_speed_after_ignition(trace, ignition_t, hit_time)
        pre_ignition_q = [pt["q"] for pt in trace if pt["q"] is not None and pt["t"] < (ignition_t or 1e9)]
        post_ignition_q = [pt["q"] for pt in trace if pt["q"] is not None and pt["t"] >= (ignition_t or 0)]

        result = {
            "label": label,
            "apex_t": round(apex_t, 3),
            "ignition_t": round(ignition_t, 3) if ignition_t else None,
            "hit_t": round(hit_time, 3) if hit_time else None,
            "q_mean_pre_ignition": round(sum(pre_ignition_q) / len(pre_ignition_q), 4) if pre_ignition_q else None,
            "q_mean_post_ignition": round(sum(post_ignition_q) / len(post_ignition_q), 4) if post_ignition_q else None,
            "q_flip_time_s": flip_t,
            "q_flip_delay_after_ignition_s": round(flip_t - ignition_t, 3) if (flip_t and ignition_t) else None,
            "min_speed_after_ignition": min_speed_pt,
            "burn_window_diagnostic": {
                k: v for k, v in burn_diag.items() if k != "peak_thrust_sample"
            },
            "burn_direction_cosine_mean": burn_diag.get("mean_direction_cosine"),
            "thrust_line_moment": "zero_by_construction (axisymmetric single centerline retro motor + symmetric 3-motor ring + centerline nose ballast; r_thrust and F_thrust colinear with body axis, CG on axis)",
        }
        return result, trace
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    motor_name = sys.argv[1] if len(sys.argv) > 1 else "J350W"
    delays = [float(x) for x in sys.argv[2:]] if len(sys.argv) > 2 else [10.0, 15.0, 25.0]
    motor_idx = find_motor_index(motor_name)

    all_results = []
    all_traces = {}
    for delay in delays:
        p = dict(E8_8)
        p['s1_retro'] = motor_idx
        p['s1_retro_delay'] = delay
        label = f"E8_8_{motor_name}_d{delay}"
        print(f"Running {label} ...")
        result, trace = run_diagnosis(p, label)
        all_results.append(result)
        all_traces[label] = trace
        print(json.dumps(result, indent=2, default=str))

    with open(os.path.join(ARTIFACTS, "flip-diagnosis-summary.json"), "w") as f:
        json.dump(all_results, f, indent=2, sort_keys=True, default=str)
    with open(os.path.join(ARTIFACTS, "flip-diagnosis-traces.json"), "w") as f:
        json.dump(all_traces, f, default=str)
    print(f"\nWrote {ARTIFACTS}/flip-diagnosis-summary.json and flip-diagnosis-traces.json")


if __name__ == "__main__":
    main()
