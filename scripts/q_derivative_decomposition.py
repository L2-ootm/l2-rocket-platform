#!/usr/bin/env python3
"""q-derivative decomposition (corrective-loop directive sections 1 and 3).

Separates the observed sign change in q = -u_T . v_hat (nose-vector /
velocity-vector alignment) into its two mathematically distinct causes:

  dq/dt = -(du_T/dt . v_hat) - (u_T . d(v_hat)/dt)

  body_rotation_contribution      = -(du_T/dt . v_hat)   (the body physically
                                     rotating relative to the world)
  velocity_direction_contribution = -(u_T . d(v_hat)/dt) (the velocity VECTOR
                                     changing direction -- e.g. a near-hover
                                     deceleration/reversal -- while the body's
                                     orientation is comparatively static)

Both terms are computed from the same theta/phi (nose axis) and vx/vy/vz
(velocity) telemetry OpenRocket already provides; no extra simulation
capability is required. Runs the E8 baseline with H180W at the two delays
named in the corrective-loop directive (9.5s, 10.0s) and writes full vector
telemetry plus the decomposition to
artifacts/autoevo/q-derivative-decomposition.json.
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
    _get_anti_tumble_listener, _finite_difference,
)
from scripts.descent_gates import apex_time_from_apogee_events
from scripts.flip_diagnosis import E8_8, find_motor_index

ARTIFACTS = "artifacts/autoevo"
os.makedirs(ARTIFACTS, exist_ok=True)


def _nose_axis(theta, phi):
    cos_th = math.cos(theta)
    return (cos_th * math.sin(phi), cos_th * math.cos(phi), math.sin(theta))


def _vec_finite_diff(values, times, index):
    n = len(values)
    lo, hi = max(0, index - 1), min(n - 1, index + 1)
    if lo == hi:
        return (0.0, 0.0, 0.0)
    dt = times[hi] - times[lo]
    if abs(dt) < 1.0e-12:
        return (0.0, 0.0, 0.0)
    return tuple((values[hi][k] - values[lo][k]) / dt for k in range(3))


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def run_case(motor_name, delay, label):
    motor_idx = find_motor_index(motor_name)
    p = dict(E8_8)
    p['s1_retro'] = motor_idx
    p['s1_retro_delay'] = delay

    ork_xml = generate_ork(p)
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

        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)
        mass_arr = br.get(fdt.TYPE_MASS)
        pitch_rate_arr = br.get(fdt.TYPE_PITCH_RATE)
        yaw_rate_arr = br.get(fdt.TYPE_YAW_RATE)
        roll_rate_arr = br.get(fdt.TYPE_ROLL_RATE)
        pitch_mc_arr = br.get(fdt.TYPE_PITCH_MOMENT_COEFF)
        yaw_mc_arr = br.get(fdt.TYPE_YAW_MOMENT_COEFF)
        cg_arr = br.get(fdt.TYPE_CG_LOCATION)
        long_inertia_arr = br.get(fdt.TYPE_LONGITUDINAL_INERTIA)
        rot_inertia_arr = br.get(fdt.TYPE_ROTATIONAL_INERTIA)

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
        burnout_t = None
        for i in range(n):
            t = float(t_arr[i])
            if t >= apex_t and float(thrust_arr[i]) > 1.0 and ignition_t is None:
                ignition_t = t
            if ignition_t is not None and t > ignition_t and float(thrust_arr[i]) <= 1.0 and burnout_t is None:
                burnout_t = t

        times = []
        u_T_series = []
        v_hat_series = []
        rows = []
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t:
                continue
            if hit_time is not None and t >= hit_time:
                break
            vx = _finite_difference(px_arr, t_arr, i)
            vy = _finite_difference(py_arr, t_arr, i)
            vz = float(vz_arr[i])
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            u_T = _nose_axis(theta, phi)
            v_hat = (vx / speed, vy / speed, vz / speed) if speed > 1.0e-6 else None

            times.append(t)
            u_T_series.append(u_T)
            v_hat_series.append(v_hat if v_hat is not None else (0.0, 0.0, 0.0))
            rows.append({
                "t": round(t, 4),
                "position_enu_m": [round(float(px_arr[i]), 4), round(float(py_arr[i]), 4), round(float(alt_arr[i]), 4)],
                "velocity_enu_mps": [round(vx, 4), round(vy, 4), round(vz, 4)] if v_hat else None,
                "v_hat": [round(c, 6) for c in v_hat] if v_hat else None,
                "speed_mps": round(speed, 4),
                "body_nose_axis_enu": [round(c, 6) for c in u_T],
                "thrust_axis_enu": [round(c, 6) for c in u_T],  # zero_by_construction: thrust colinear with nose axis (Finding 1)
                "thrust_n": round(float(thrust_arr[i]), 3),
                "angular_velocity_body_rad_s": {
                    "pitch": round(float(pitch_rate_arr[i]), 5),
                    "yaw": round(float(yaw_rate_arr[i]), 5),
                    "roll": round(float(roll_rate_arr[i]), 5),
                },
                "aerodynamic_moment_coeff": {
                    "pitch": round(float(pitch_mc_arr[i]), 5),
                    "yaw": round(float(yaw_mc_arr[i]), 5),
                    "note": "dimensionless Cm/Cn as reported by OpenRocket; NOT converted to body-frame Nm here (would require dynamic-pressure * reference-area * reference-length scaling plus a separate body-axis moment-vector reconstruction not attempted in this pass)",
                },
                "thrust_moment_body_nm": [0.0, 0.0, 0.0],  # zero_by_construction (Finding 1: axisymmetric, r_thrust || F_thrust)
                "mass_kg": round(float(mass_arr[i]), 4),
                "cg_body_m": round(float(cg_arr[i]), 5),
                "longitudinal_inertia_kg_m2": round(float(long_inertia_arr[i]), 6),
                "rotational_inertia_kg_m2": round(float(rot_inertia_arr[i]), 6),
                "q_total": round(-_dot(u_T, v_hat), 6) if v_hat else None,
            })

        # Decomposition pass (needs neighbor access -> second loop).
        decomposition = []
        for i in range(len(times)):
            t = times[i]
            u_T = u_T_series[i]
            v_hat = v_hat_series[i]
            q = rows[i]["q_total"]
            du_T_dt = _vec_finite_diff(u_T_series, times, i)
            dv_hat_dt = _vec_finite_diff(v_hat_series, times, i)
            body_rotation_contribution = -_dot(du_T_dt, v_hat)
            velocity_direction_contribution = -_dot(u_T, dv_hat_dt)
            total_q_change = body_rotation_contribution + velocity_direction_contribution

            lo, hi = max(0, i - 1), min(len(times) - 1, i + 1)
            actual_dq_dt = None
            if hi != lo and rows[lo]["q_total"] is not None and rows[hi]["q_total"] is not None:
                dt = times[hi] - times[lo]
                if abs(dt) > 1.0e-12:
                    actual_dq_dt = (rows[hi]["q_total"] - rows[lo]["q_total"]) / dt

            decomposition.append({
                "t": round(t, 4),
                "q_total": q,
                "body_rotation_contribution": round(body_rotation_contribution, 5),
                "velocity_direction_contribution": round(velocity_direction_contribution, 5),
                "total_q_change": round(total_q_change, 5),
                "actual_dq_dt": round(actual_dq_dt, 5) if actual_dq_dt is not None else None,
                "closure_error": round(actual_dq_dt - total_q_change, 5) if actual_dq_dt is not None else None,
            })

        # Integrate contributions over the burn window only (ignition..burnout,
        # or ignition..hit if burnout wasn't detected) using the trapezoid rule.
        window = [
            d for d in decomposition
            if ignition_t is not None and d["t"] >= ignition_t
            and (burnout_t is None or d["t"] <= burnout_t + 0.5)
        ]
        body_integral = 0.0
        vel_integral = 0.0
        for a, b in zip(window, window[1:]):
            dt = b["t"] - a["t"]
            body_integral += 0.5 * (a["body_rotation_contribution"] + b["body_rotation_contribution"]) * dt
            vel_integral += 0.5 * (a["velocity_direction_contribution"] + b["velocity_direction_contribution"]) * dt

        abs_body = abs(body_integral)
        abs_vel = abs(vel_integral)
        if not window or (abs_body < 1e-6 and abs_vel < 1e-6):
            classification = "INSUFFICIENT_TELEMETRY"
        elif abs_body > 2.0 * abs_vel:
            classification = "BODY_ROTATION_DOMINANT"
        elif abs_vel > 2.0 * abs_body:
            classification = "VELOCITY_VECTOR_OVERSHOOT_DOMINANT"
        else:
            classification = "MIXED"

        return {
            "label": label,
            "motor": motor_name,
            "requested_delay_s": delay,
            "apex_t": round(apex_t, 4),
            "ignition_t": round(ignition_t, 4) if ignition_t else None,
            "burnout_t": round(burnout_t, 4) if burnout_t else None,
            "hit_t": round(hit_time, 4) if hit_time else None,
            "burn_window_body_rotation_integral": round(body_integral, 5),
            "burn_window_velocity_direction_integral": round(vel_integral, 5),
            "classification": classification,
            "rows": rows,
            "decomposition": decomposition,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    cases = [
        ("H180W", 9.5, "E8_8_H180W_d9.5"),
        ("H180W", 10.0, "E8_8_H180W_d10.0"),
    ]
    results = []
    for motor, delay, label in cases:
        print(f"Running {label} ...")
        r = run_case(motor, delay, label)
        print(f"  ignition_t={r['ignition_t']} burnout_t={r['burnout_t']} "
              f"body_integral={r['burn_window_body_rotation_integral']} "
              f"vel_integral={r['burn_window_velocity_direction_integral']} "
              f"-> {r['classification']}")
        results.append(r)

    out_path = os.path.join(ARTIFACTS, "q-derivative-decomposition.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
