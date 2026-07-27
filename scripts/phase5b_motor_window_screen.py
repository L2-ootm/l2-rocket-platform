#!/usr/bin/env python3
"""Phase 5B Stage 2: motor-aware contact-relative window screen (mission section 3).

For an ascent-legal Stage 1 sustainer candidate, extracts the full unpowered
descent trace (branch 0 = Sustainer) from natural apex to natural ground
contact, then evaluates REAL motor thrust curves (queried from OpenRocket's
own bundled motor database, the same source `scripts/extract_motors.py`
uses) against every physically plausible contact-relative ignition window.

This is an ANALYTIC estimate, not a re-simulation: it assumes the powered
trajectory's attitude/velocity direction (q_total/q_vertical/q_horizontal)
tracks the unpowered trace up to the point evaluated, and estimates opposing
impulse and delta-v from that assumption. It is explicitly NOT a substitute
for Stage 3 (real powered OpenRocket validation) -- it exists to avoid
spending expensive real powered runs on windows/motors that cannot possibly
work (thrust too short, direction adverse, mass too high), per section 3's
"Do not use peak q_total as the screen" directive: this module ranks on
impulse-weighted, full-window quantities instead of a single peak sample.
"""
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, _finite_difference,
)
from scripts.descent_gates import apex_time_from_apogee_events, q_components

ARTIFACTS = "artifacts/autoevo/phase5b"
os.makedirs(ARTIFACTS, exist_ok=True)

MOTOR_DB = REPO_ROOT / "openrocket/core/src/main/resources/datafiles/thrustcurves/initial_motors.db"

SUSTAINER_BRANCH = 0  # corrected mapping (phase5a section 0.2): branch 0 = s0 = Sustainer

# Real motor designations (bundled DB `designation` column), per mission
# section 9's minimum screen list. J420R is the "one additional physically
# fitting motor chosen to bracket thrust/duration" -- shorter/harder-hitting
# than H180W/J360, bracketing the low-thrust-long-burn H73J on the other end.
SCREEN_MOTORS = ["H73J", "H180W", "J360", "J420R"]

IMPULSE_FRACTION_THRESHOLD = 0.25  # matches PoweredEarlyStop, for consistency


def load_motor_curve(designation):
    """Query OpenRocket's bundled motor DB for (t_arr, f_arr, prop_kg, total_kg)."""
    conn = sqlite3.connect(str(MOTOR_DB))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, propellant_weight, total_weight FROM motors WHERE designation = ?",
            (designation,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"motor designation not found in bundled DB: {designation}")
        motor_id, prop_g, total_g = row
        cur.execute(
            "SELECT id FROM thrust_curves WHERE motor_id = ? ORDER BY total_impulse DESC, id ASC LIMIT 1",
            (motor_id,),
        )
        curve_row = cur.fetchone()
        if not curve_row:
            raise ValueError(f"no thrust_curves row for {designation}")
        curve_id = curve_row[0]
        cur.execute(
            "SELECT time_seconds, force_newtons FROM thrust_data WHERE curve_id = ? ORDER BY time_seconds",
            (curve_id,),
        )
        points = cur.fetchall()
        if not points:
            raise ValueError(f"no thrust_data rows for {designation}")
    finally:
        conn.close()
    t_arr = [float(t) for t, _ in points]
    f_arr = [float(f) for _, f in points]
    burn_duration_s = t_arr[-1]
    total_impulse_ns = 0.0
    for (t0, f0), (t1, f1) in zip(points, points[1:]):
        total_impulse_ns += 0.5 * (f0 + f1) * (t1 - t0)
    return {
        "designation": designation,
        "t_arr": t_arr,
        "f_arr": f_arr,
        "burn_duration_s": burn_duration_s,
        "total_impulse_ns": total_impulse_ns,
        "propellant_kg": prop_g / 1000.0,
        "total_kg": total_g / 1000.0,
    }


def _thrust_at(curve, tau):
    """Linear-interpolated thrust at time-since-ignition tau; 0 outside [0, burn_duration]."""
    t_arr, f_arr = curve["t_arr"], curve["f_arr"]
    if tau <= t_arr[0]:
        return f_arr[0] if tau >= 0 else 0.0
    if tau >= t_arr[-1]:
        return 0.0
    for i in range(1, len(t_arr)):
        if t_arr[i] >= tau:
            t0, t1 = t_arr[i - 1], t_arr[i]
            f0, f1 = f_arr[i - 1], f_arr[i]
            frac = (tau - t0) / (t1 - t0) if t1 > t0 else 0.0
            return f0 + frac * (f1 - f0)
    return 0.0


def extract_branch_trace(params, branch=SUSTAINER_BRANCH):
    """Run one Stage-1 (unpowered, both retros disabled) sim; return the full
    trace for the requested branch (0=Sustainer, 1=Booster; corrected mapping,
    phase5a section 0.2) from natural apex to natural ground contact, plus the
    contact time and mass array for delta-v estimation.
    """
    p = dict(params)
    p["s1_retro_delay"] = 200.0
    p["s0_retro_delay"] = 200.0
    ork_xml = generate_ork(p)

    import tempfile
    fd, path = tempfile.mkstemp(suffix=".ork")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        sim.simulate(_get_anti_tumble_listener())
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

        br = data.getBranch(branch)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        mass_arr = br.get(fdt.TYPE_MASS)

        apogee_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.APOGEE
        )
        apex_t = apex_time_from_apogee_events(apogee_events, t_arr, alt_arr)

        contact_t = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                contact_t = float(ev.getTime())
                break
        if contact_t is None:
            for i in range(1, n):
                if float(alt_arr[i]) <= 0.0 and float(alt_arr[i - 1]) > 0.0:
                    contact_t = float(t_arr[i])
                    break

        trace = []
        prev_theta = prev_phi = prev_t = None
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t:
                continue
            if contact_t is not None and t > contact_t:
                break
            vz = float(vz_arr[i])
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            vx = _finite_difference(px_arr, t_arr, i)
            vy = _finite_difference(py_arr, t_arr, i)
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            q_total, q_h, q_v = q_components(theta, phi, vx, vy, vz, speed)
            rate = 0.0
            if prev_theta is not None and prev_t is not None and t > prev_t:
                dt = t - prev_t
                rate = math.hypot((theta - prev_theta) / dt, (phi - prev_phi) / dt)
            prev_theta, prev_phi, prev_t = theta, phi, t
            trace.append({
                "time_s": t,
                "altitude_m": float(alt_arr[i]),
                "velocity_enu_mps": {"vx": vx, "vy": vy, "vz": vz},
                "vertical_speed_mps": vz,
                "horizontal_speed_mps": math.hypot(vx, vy),
                "q_total": q_total,
                "q_vertical": q_v,
                "q_horizontal": q_h,
                "angular_rate_norm": rate,
                "mass_kg": float(mass_arr[i]),
            })
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    return {"apex_t": apex_t, "contact_t": contact_t, "trace": trace}


def _interp_trace(trace, t):
    """Linearly interpolate trace fields at time t; None if outside range."""
    if not trace:
        return None
    if t <= trace[0]["time_s"]:
        return trace[0]
    if t >= trace[-1]["time_s"]:
        return trace[-1]
    for a, b in zip(trace, trace[1:]):
        if a["time_s"] <= t <= b["time_s"]:
            dt = b["time_s"] - a["time_s"]
            f = (t - a["time_s"]) / dt if dt > 0 else 0.0
            out = {}
            for k in ("q_total", "q_vertical", "q_horizontal", "vertical_speed_mps",
                      "horizontal_speed_mps", "angular_rate_norm", "mass_kg", "altitude_m"):
                av, bv = a.get(k), b.get(k)
                out[k] = (av + f * (bv - av)) if (av is not None and bv is not None) else av
            out["time_s"] = t
            return out
    return trace[-1]


def evaluate_window(trace_data, motor_curve, ignition_time_s):
    """Analytic prediction for one (motor, ignition_time) contact-relative window."""
    trace = trace_data["trace"]
    contact_t = trace_data["contact_t"]
    burn_duration_s = motor_curve["burn_duration_s"]
    window_end_s = ignition_time_s + burn_duration_s

    if ignition_time_s < trace[0]["time_s"] or ignition_time_s > trace[-1]["time_s"]:
        return {
            "motor": motor_curve["designation"], "ignition_seed_absolute_s": ignition_time_s,
            "classification": "REJECT_IGNITION_OUTSIDE_TRACE",
        }

    n_samples = 40
    dtau = burn_duration_s / n_samples
    i_total = 0.0
    i_opp = 0.0
    i_opp_vertical = 0.0
    i_opp_horizontal = 0.0
    i_adverse = 0.0
    mass_at_ignition = _interp_trace(trace, ignition_time_s)["mass_kg"]

    prev_t = ignition_time_s
    prev_thrust = _thrust_at(motor_curve, 0.0)
    prev_state = _interp_trace(trace, min(ignition_time_s, trace[-1]["time_s"]))
    for k in range(1, n_samples + 1):
        tau = k * dtau
        t = ignition_time_s + tau
        thrust = _thrust_at(motor_curve, tau)
        state = _interp_trace(trace, min(t, trace[-1]["time_s"]))
        dt = t - prev_t
        if dt > 0:
            avg_thrust = 0.5 * (prev_thrust + thrust)
            q = 0.5 * ((prev_state["q_total"] or 0.0) + (state["q_total"] or 0.0))
            qv = 0.5 * ((prev_state["q_vertical"] or 0.0) + (state["q_vertical"] or 0.0))
            qh = 0.5 * ((prev_state["q_horizontal"] or 0.0) + (state["q_horizontal"] or 0.0))
            impulse = avg_thrust * dt
            i_total += impulse
            if q > 0:
                i_opp += impulse * q
            else:
                i_adverse += impulse * abs(q)
            if qv > 0:
                i_opp_vertical += impulse * qv
            if qh > 0:
                i_opp_horizontal += impulse * qh
        prev_t, prev_thrust, prev_state = t, thrust, state

    opposing_fraction = (i_opp / i_total) if i_total > 0 else None

    end_state = _interp_trace(trace, min(window_end_s, trace[-1]["time_s"]))
    delta_v_from_opposition = (i_opp / mass_at_ignition) if mass_at_ignition else 0.0
    predicted_contact_vz = None
    predicted_contact_vxy = None
    if contact_t is not None:
        contact_state = _interp_trace(trace, min(contact_t, trace[-1]["time_s"]))
        vz0 = contact_state["vertical_speed_mps"]
        vxy0 = contact_state["horizontal_speed_mps"]
        # crude uniform split of the estimated braking delta-v between
        # vertical/horizontal by their opposing-impulse share -- Stage 3 must
        # verify with a real powered rerun, this is an ANALYTIC screen only.
        v_share = i_opp_vertical / i_opp if i_opp > 0 else 0.5
        predicted_contact_vz = max(0.0, abs(vz0) - delta_v_from_opposition * v_share)
        predicted_contact_vxy = max(0.0, vxy0 - delta_v_from_opposition * (1 - v_share))

    required_vertical_delta_v = abs(end_state["vertical_speed_mps"]) if end_state else None
    required_horizontal_delta_v = end_state["horizontal_speed_mps"] if end_state else None

    burnout_before_contact = (
        contact_t is not None and window_end_s <= contact_t
    )

    if opposing_fraction is None:
        classification = "REJECT_NO_THRUST_IN_WINDOW"
    elif window_end_s - trace[0]["time_s"] < 0:
        classification = "REJECT_WINDOW_BEFORE_TRACE"
    elif opposing_fraction < IMPULSE_FRACTION_THRESHOLD:
        classification = "REJECT_LOW_OPPOSITION"
    elif i_adverse > i_opp:
        classification = "REJECT_ADVERSE_DOMINANT"
    elif predicted_contact_vz is not None and predicted_contact_vxy is not None and \
            math.hypot(predicted_contact_vz, predicted_contact_vxy) < 5.0:
        classification = "PROMISING"
    else:
        classification = "MARGINAL"

    return {
        "motor": motor_curve["designation"],
        "burn_duration_s": burn_duration_s,
        "total_impulse_ns": motor_curve["total_impulse_ns"],
        "window_start_s": ignition_time_s,
        "window_end_s": window_end_s,
        "ignition_seed_absolute_s": ignition_time_s,
        "ignition_seed_relative_to_contact_s": (
            ignition_time_s - contact_t if contact_t is not None else None
        ),
        "predicted_opposing_total_impulse_ns": i_opp,
        "predicted_opposing_vertical_impulse_ns": i_opp_vertical,
        "predicted_opposing_horizontal_impulse_ns": i_opp_horizontal,
        "predicted_adverse_impulse_ns": i_adverse,
        "opposing_impulse_fraction": opposing_fraction,
        "required_vertical_delta_v": required_vertical_delta_v,
        "required_horizontal_delta_v": required_horizontal_delta_v,
        "predicted_burnout_state": "BEFORE_CONTACT" if burnout_before_contact else "STILL_BURNING_AT_CONTACT",
        "predicted_contact_speed": (
            math.hypot(predicted_contact_vz, predicted_contact_vxy)
            if predicted_contact_vz is not None else None
        ),
        "uncertainty": "ANALYTIC_ESTIMATE_ASSUMES_PASSIVE_TRAJECTORY_UNTIL_CUTOFF_NOT_A_POWERED_RERUN",
        "classification": classification,
    }


def screen_candidate(params, label, window_step_s=0.1, branch=SUSTAINER_BRANCH, motors=None):
    """Full Stage 2 screen for one candidate: extract trace, sweep motors x windows."""
    trace_data = extract_branch_trace(params, branch=branch)
    trace = trace_data["trace"]
    contact_t = trace_data["contact_t"]
    if not trace or contact_t is None:
        return {"label": label, "status": "NO_TRACE"}

    results = []
    for motor_name in (motors or SCREEN_MOTORS):
        curve = load_motor_curve(motor_name)
        # Sweep ignition times so the burn's tail lands at or before contact,
        # from as-early-as-apex up to the latest point the full burn still
        # fits before contact (contact-relative, per section 9 -- not
        # apex-relative).
        latest_ignition = contact_t - curve["burn_duration_s"]
        earliest_ignition = trace[0]["time_s"]
        if latest_ignition < earliest_ignition:
            results.append({
                "motor": motor_name, "classification": "REJECT_BURN_LONGER_THAN_AVAILABLE_WINDOW",
                "burn_duration_s": curve["burn_duration_s"],
                "available_window_s": contact_t - earliest_ignition,
            })
            continue
        ignition_times = []
        t = latest_ignition
        while t >= earliest_ignition:
            ignition_times.append(round(t, 4))
            t -= window_step_s
        for ig_t in ignition_times:
            results.append(evaluate_window(trace_data, curve, ig_t))

    promising = [r for r in results if r.get("classification") == "PROMISING"]
    marginal = [r for r in results if r.get("classification") == "MARGINAL"]
    return {
        "label": label,
        "status": "SCREENED",
        "apex_t": trace_data["apex_t"],
        "unpowered_contact_t": contact_t,
        "n_windows_evaluated": len(results),
        "n_promising": len(promising),
        "n_marginal": len(marginal),
        "best_promising": max(promising, key=lambda r: r["opposing_impulse_fraction"], default=None),
        "windows": results,
    }


if __name__ == "__main__":
    init_or()
    candidates = json.loads(sys.argv[1]) if len(sys.argv) > 1 else [{}]
    from scripts.phase5a_coupled_evaluator import build_params
    out = []
    for i, overrides in enumerate(candidates):
        label = overrides.pop("_label", f"cand_{i}")
        print(f"Screening {label} ...", file=sys.stderr)
        params = build_params(overrides)
        r = screen_candidate(params, label)
        out.append(r)
        print(f"  status={r.get('status')} promising={r.get('n_promising')} marginal={r.get('n_marginal')}",
              file=sys.stderr)
    with open(os.path.join(ARTIFACTS, "sustainer-motor-window-screen.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(json.dumps(
        [{"label": r["label"], "status": r.get("status"), "n_promising": r.get("n_promising")} for r in out],
        indent=2,
    ))
