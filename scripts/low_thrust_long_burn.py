#!/usr/bin/env python3
"""Low-thrust, long-burn motor test (corrective-loop directive section 5).

Compares H73J (~188 Ns over ~2.953s, low mean thrust) against H180W
(~234 Ns over ~1.313s, higher mean thrust) as the active retro/braking
motor on the E8_8 topology, at the same ignition delay, ranked on the full
metric set the directive specifies -- not on total impulse alone.
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
from motor_data import load_motor_by_index, burn_duration, total_impulse
from scripts.descent_gates import apex_time_from_apogee_events, q_components
from scripts.flip_diagnosis import E8_8, find_motor_index, _min_speed_after_ignition

ARTIFACTS = "artifacts/autoevo"
os.makedirs(ARTIFACTS, exist_ok=True)


def _nose_axis(theta, phi):
    cos_th = math.cos(theta)
    return (cos_th * math.sin(phi), cos_th * math.cos(phi), math.sin(theta))


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def run_case(motor_name, delay):
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
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)

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

        trace = []
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t:
                continue
            if hit_time is not None and t > hit_time:
                break
            vx = _finite_difference(px_arr, t_arr, i)
            vy = _finite_difference(py_arr, t_arr, i)
            vz = float(vz_arr[i])
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            q_total, q_h, q_v = q_components(theta, phi, vx, vy, vz, speed)
            u_T = _nose_axis(theta, phi)
            trace.append({
                "t": t, "vx": vx, "vy": vy, "vz": vz, "speed": speed,
                "q_total": q_total, "q_horizontal": q_h,
                "thrust_n": float(thrust_arr[i]), "u_T": u_T,
            })

        ignition_t = next((r["t"] for r in trace if r["thrust_n"] > 1.0), None)
        burnout_t = None
        if ignition_t is not None:
            after = [r for r in trace if r["t"] > ignition_t]
            for r in after:
                if r["thrust_n"] <= 1.0:
                    burnout_t = r["t"]
                    break

        burn = [r for r in trace if r["thrust_n"] > 1.0]
        i_total = i_opp = i_opp_h = 0.0
        for a, b in zip(burn, burn[1:]):
            dt = b["t"] - a["t"]
            if dt <= 0:
                continue
            thrust = 0.5 * (a["thrust_n"] + b["thrust_n"])
            q = 0.5 * ((a["q_total"] or 0.0) + (b["q_total"] or 0.0))
            qh = 0.5 * ((a["q_horizontal"] or 0.0) + (b["q_horizontal"] or 0.0))
            i_total += thrust * dt
            if q > 0:
                i_opp += thrust * dt * q
            if qh > 0:
                i_opp_h += thrust * dt * qh
        i_opp_over_i_total = (i_opp / i_total) if i_total > 0 else None
        i_opp_h_over_i_total = (i_opp_h / i_total) if i_total > 0 else None

        # Initial deceleration: d(speed)/dt over the first ~0.15s of the burn.
        initial_decel = None
        if burn:
            t0 = burn[0]["t"]
            window = [r for r in burn if r["t"] <= t0 + 0.15]
            if len(window) >= 2:
                initial_decel = (window[-1]["speed"] - window[0]["speed"]) / (window[-1]["t"] - window[0]["t"])

        # Body-rotation vs velocity-direction contribution, integrated over
        # the burn window (same decomposition as
        # scripts/q_derivative_decomposition.py).
        body_integral = vel_integral = 0.0
        for i in range(1, len(trace) - 1):
            r = trace[i]
            if not (ignition_t is not None and r["t"] >= ignition_t
                    and (burnout_t is None or r["t"] <= burnout_t + 0.5)):
                continue
            lo, hi = trace[i - 1], trace[i + 1]
            dt = hi["t"] - lo["t"]
            if dt <= 1.0e-9:
                continue
            speed_hi = hi["speed"]
            speed_lo = lo["speed"]
            v_hat_hi = (hi["vx"] / speed_hi, hi["vy"] / speed_hi, hi["vz"] / speed_hi) if speed_hi > 1e-6 else (0, 0, 0)
            v_hat_lo = (lo["vx"] / speed_lo, lo["vy"] / speed_lo, lo["vz"] / speed_lo) if speed_lo > 1e-6 else (0, 0, 0)
            speed_mid = r["speed"]
            v_hat_mid = (r["vx"] / speed_mid, r["vy"] / speed_mid, r["vz"] / speed_mid) if speed_mid > 1e-6 else (0, 0, 0)
            du_T_dt = tuple((hi["u_T"][k] - lo["u_T"][k]) / dt for k in range(3))
            dv_hat_dt = tuple((v_hat_hi[k] - v_hat_lo[k]) / dt for k in range(3))
            body_contrib = -_dot(du_T_dt, v_hat_mid)
            vel_contrib = -_dot(r["u_T"], dv_hat_dt)
            # Trapezoid-ish accumulation using the local dt/2 (2-sided).
            step = (hi["t"] - r["t"] + (r["t"] - lo["t"])) / 2.0
            body_integral += body_contrib * step
            vel_integral += vel_contrib * step

        min_speed_pt = _min_speed_after_ignition(
            [{"t": r["t"], "speed_ms": r["speed"], "q": r["q_total"]} for r in trace],
            ignition_t, hit_time,
        )

        touchdown_speed = None
        if hit_time and n > 1:
            idx = min(range(n), key=lambda i: abs(float(t_arr[i]) - hit_time))
            touchdown_speed = math.sqrt(float(vxy_arr[idx]) ** 2 + float(vz_arr[idx]) ** 2)

        peak_thrust = max((r["thrust_n"] for r in trace), default=0.0)

        return {
            "ignition_t": round(ignition_t, 3) if ignition_t else None,
            "burnout_t": round(burnout_t, 3) if burnout_t else None,
            "measured_peak_thrust_n": round(peak_thrust, 2),
            "initial_deceleration_mps2": round(initial_decel, 3) if initial_decel is not None else None,
            "body_rotation_contribution_integral": round(body_integral, 4),
            "velocity_direction_contribution_integral": round(vel_integral, 4),
            "opposing_impulse_fraction": round(i_opp_over_i_total, 4) if i_opp_over_i_total is not None else None,
            "horizontal_opposing_impulse_fraction": round(i_opp_h_over_i_total, 4) if i_opp_h_over_i_total is not None else None,
            "minimum_pre_contact_speed_ms": min_speed_pt["speed_ms"] if min_speed_pt else None,
            "touchdown_speed_ms": round(touchdown_speed, 3) if touchdown_speed is not None else None,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    delay = 9.5  # apex_t(E8_8, K550W-inert baseline) + ~1s; matches Finding 3/5 methodology
    results = {}
    for motor in ("H73J", "H180W"):
        idx = find_motor_index(motor)
        m = load_motor_by_index(idx)
        print(f"=== {motor} @ delay={delay}s ===")
        print(f"  static: mean_thrust={m.total_impulse_ns / m.burn_duration_s:.1f}N "
              f"burn_duration={m.burn_duration_s}s total_impulse={m.total_impulse_ns}Ns")
        measured = run_case(motor, delay)
        print(f"  measured: {measured}")
        results[motor] = {
            "static_mean_thrust_n": round(m.total_impulse_ns / m.burn_duration_s, 2),
            "static_burn_duration_s": m.burn_duration_s,
            "static_total_impulse_ns": m.total_impulse_ns,
            "static_loaded_mass_kg": m.loaded_mass_kg,
            "delay_s": delay,
            **measured,
        }
        print()

    h73j, h180w = results["H73J"], results["H180W"]
    ranking = {
        "opposing_impulse_fraction_winner": (
            "H73J" if (h73j["opposing_impulse_fraction"] or 0) > (h180w["opposing_impulse_fraction"] or 0) else "H180W"
        ),
        "touchdown_speed_winner": (
            "H73J" if (h73j["touchdown_speed_ms"] or 1e9) < (h180w["touchdown_speed_ms"] or 1e9) else "H180W"
        ),
        "min_pre_contact_speed_winner": (
            "H73J" if (h73j["minimum_pre_contact_speed_ms"] or 1e9) < (h180w["minimum_pre_contact_speed_ms"] or 1e9) else "H180W"
        ),
        "either_legal_branch_lt_5ms": (
            (h73j["touchdown_speed_ms"] or 1e9) < 5.0 or (h180w["touchdown_speed_ms"] or 1e9) < 5.0
        ),
    }
    out = {"delay_s": delay, "results": results, "ranking": ranking}
    out_path = os.path.join(ARTIFACTS, "low-thrust-long-burn-results.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"Wrote {out_path}")
    print(f"Ranking: {ranking}")


if __name__ == "__main__":
    main()
