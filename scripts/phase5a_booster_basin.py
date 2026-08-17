#!/usr/bin/env python3
"""Phase 5A booster delay-basin characterization (mission section 4).

Runs the recovered eight-forward-fin booster candidate
(artifacts/autoevo/historical-3p5135-candidate.json ::
complete_parameters_powered_rerun, only s1_retro_delay varies) across a
0.5 ms grid from 29.857 to 29.869 s, records the full required field set per
sample, and supports fresh-process repeatability and save/close/reopen/rerun
checks for any sub-5 m/s sample.

Modes:
  sweep            -- run the full 0.5ms grid in one process, write
                       artifacts/autoevo/phase5a/booster-delay-basin.json
  repeat <delay>    -- fresh-process single-delay rerun (prints JSON to stdout)
  reopen <ork_path> -- reopen a saved .ork and rerun its stored simulation
                        (prints JSON to stdout)
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
    _get_anti_tumble_listener, _retro_burn_diagnostic, _finite_difference,
    save_simulated_ork, run_sim,
)
from scripts.descent_gates import apex_time_from_apogee_events

ARTIFACTS = "artifacts/autoevo/phase5a"
os.makedirs(ARTIFACTS, exist_ok=True)

with open("artifacts/autoevo/historical-3p5135-candidate.json", encoding="utf-8") as f:
    _CAND = json.load(f)
BASE_PARAMS = dict(_CAND["complete_parameters_powered_rerun"])

BOOSTER_BRANCH = 1  # corrected mapping: branch 1 = s1 = Booster


def _state_at(t_arr, px_arr, py_arr, alt_arr, vz_arr, theta_arr, phi_arr, n, index):
    t = float(t_arr[index])
    vz = float(vz_arr[index])
    vx = _finite_difference(px_arr, t_arr, index)
    vy = _finite_difference(py_arr, t_arr, index)
    theta = float(theta_arr[index])
    phi = float(phi_arr[index])
    cos_th = math.cos(theta)
    nose_x = cos_th * math.sin(phi)
    nose_y = cos_th * math.cos(phi)
    nose_z = math.sin(theta)
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    q = None
    if speed > 1.0e-9:
        q = -(nose_x * vx + nose_y * vy + nose_z * vz) / speed
    return {
        "time_s": t,
        "altitude_m": float(alt_arr[index]),
        "velocity_enu": {"vx": vx, "vy": vy, "vz": vz},
        "body_axis_enu": {"x": nose_x, "y": nose_y, "z": nose_z},
        "q": q,
    }


def run_candidate(params, save_path=None):
    """Run one candidate, return the full required field set.

    Touchdown speed/position reuse osifog_sweep.run_sim()'s own
    stage_landings extraction (linear-interpolated exactly at the GROUND_HIT
    event time from TYPE_VELOCITY_XY/TYPE_VELOCITY_Z) -- the same authoritative
    path phase4c's numbers came from. An earlier version of this script
    hand-rolled its own extraction using the last raw sample before impact
    and finite-differenced horizontal velocity from position; OpenRocket does
    not sample exactly at GROUND_HIT, so that undercounted/overcounted by up
    to several m/s versus the interpolated value. Do not reintroduce that.
    """
    ork_xml = generate_ork(params)
    m = run_sim(ork_xml, seed=SIM_SEED)

    landing = next(
        (s for s in m.get("stage_landings", []) if int(s.get("branch", -1)) == BOOSTER_BRANCH),
        None,
    )
    burn_diag = next(
        (d for d in m.get("retro_burn_diagnostics", []) if int(d.get("branch", -1)) == BOOSTER_BRANCH),
        {},
    )

    # Supplemental raw-sample extraction (ignition state, burnout time) --
    # not touchdown speed/position, which come from run_sim above.
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

        br = data.getBranch(BOOSTER_BRANCH)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)

        apogee_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.APOGEE
        )
        apex_t = apex_time_from_apogee_events(apogee_events, t_arr, alt_arr)

        hit_index = None
        for i in range(1, n):
            if float(alt_arr[i]) <= 0.0 and float(alt_arr[i - 1]) > 0.0:
                hit_index = i
                break

        ignition_index = None
        for i in range(n):
            if float(t_arr[i]) > apex_t and float(thrust_arr[i]) > 1.0:
                ignition_index = i
                break

        burnout_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.BURNOUT
        )
        ignition_t = float(t_arr[ignition_index]) if ignition_index is not None else None
        burnout_t = None
        for bt in burnout_events:
            if ignition_t is None or bt >= ignition_t - 1.0e-6:
                burnout_t = bt
                break

        ignition_state = (
            _state_at(t_arr, px_arr, py_arr, alt_arr, vz_arr, theta_arr, phi_arr, n, ignition_index)
            if ignition_index is not None else None
        )

        ground_contact_bracket = None
        if hit_index is not None:
            ground_contact_bracket = [
                {"time_s": float(t_arr[hit_index - 1]), "altitude_m": float(alt_arr[hit_index - 1])},
                {"time_s": float(t_arr[hit_index]), "altitude_m": float(alt_arr[hit_index])},
            ]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    hit_time = landing["time_s"] if landing else None
    touchdown_vz = landing["vz_ms"] if landing else None
    touchdown_vxy = landing["vxy_ms"] if landing else None
    touchdown_total = (
        math.sqrt(touchdown_vz ** 2 + touchdown_vxy ** 2)
        if landing else None
    )

    result = {
        "delay_s": params["s1_retro_delay"],
        "ignition_time_s": ignition_t,
        "ignition_altitude_m": ignition_state["altitude_m"] if ignition_state else None,
        "velocity_at_ignition_enu": ignition_state["velocity_enu"] if ignition_state else None,
        "body_axis_at_ignition_enu": ignition_state["body_axis_enu"] if ignition_state else None,
        "q_at_ignition": ignition_state["q"] if ignition_state else None,
        "ground_contact_bracket": ground_contact_bracket,
        "ground_contact_time_s": hit_time,
        "burnout_time_s": burnout_t,
        "burn_remaining_at_contact_s": (
            round(burnout_t - hit_time, 6) if (burnout_t is not None and hit_time is not None) else None
        ),
        "touchdown_vz_mps": touchdown_vz,
        "touchdown_vxy_mps": touchdown_vxy,
        "touchdown_total_mps": touchdown_total,
        "retro_fraction_opposing": burn_diag.get("fraction_opposing_velocity"),
        "retro_fraction_vertical": burn_diag.get("fraction_vertical_braking"),
        "retro_sample_count": burn_diag.get("sample_count"),
    }
    if save_path:
        save_simulated_ork(ork_xml, save_path, seed=SIM_SEED)
        result["saved_path"] = save_path
    return result


def sweep():
    init_or()
    rows = []
    delay = 29.857
    end = 29.869 + 1e-9
    while delay <= end:
        d = round(delay, 4)
        p = dict(BASE_PARAMS)
        p["s1_retro_delay"] = d
        print(f"delay={d} ...", file=sys.stderr)
        row = run_candidate(p)
        rows.append(row)
        print(f"  touchdown_total={row['touchdown_total_mps']:.4f}", file=sys.stderr)
        delay += 0.0005

    legal = [r for r in rows if r["touchdown_total_mps"] < 5.0]

    # in-process repeatability check for every legal (sub-5) sample
    for r in legal:
        p = dict(BASE_PARAMS)
        p["s1_retro_delay"] = r["delay_s"]
        rerun = run_candidate(p)
        r["repeatability"] = {
            "same_process_rerun_total_mps": rerun["touchdown_total_mps"],
            "bit_identical": rerun["touchdown_total_mps"] == r["touchdown_total_mps"],
        }

    out = {
        "candidate": "historical-3p5135-booster-branch (eight-forward-fin booster, H180W retro)",
        "grid": {"start_s": 29.857, "end_s": 29.869, "step_s": 0.0005},
        "rows": rows,
        "legal_rows": legal,
        "legal_delays_s": [r["delay_s"] for r in legal],
    }
    with open(os.path.join(ARTIFACTS, "booster-delay-basin.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"\nWrote {ARTIFACTS}/booster-delay-basin.json -- {len(legal)}/{len(rows)} legal", file=sys.stderr)
    print(json.dumps({"legal_delays_s": out["legal_delays_s"]}))


def repeat_single(delay_s):
    init_or()
    p = dict(BASE_PARAMS)
    p["s1_retro_delay"] = float(delay_s)
    row = run_candidate(p)
    print(json.dumps(row, indent=2, sort_keys=True, default=str))


def reopen_touchdown(ork_path):
    """Reopen a saved .ork and rerun its stored simulation, return touchdown dict.

    Uses the same GROUND_HIT-interpolated TYPE_VELOCITY_Z/TYPE_VELOCITY_XY
    extraction as osifog_sweep.run_sim() (see run_candidate's docstring above
    for why the naive last-raw-sample approach undercounts/overcounts).
    """
    init_or()
    doc = _load_ork_doc(ork_path)
    sim = doc.getSimulations().get(0)
    sim.getOptions().setRandomSeed(SIM_SEED)
    _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
    sim.simulate(_get_anti_tumble_listener())
    data = sim.getSimulatedData()
    fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
    FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
    br = data.getBranch(BOOSTER_BRANCH)
    n = int(br.getLength())
    t_arr = br.get(fdt.TYPE_TIME)
    vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
    vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)

    hit_time = None
    for ev in br.getEvents():
        if ev.getType() == FlightEvent.Type.GROUND_HIT:
            hit_time = float(ev.getTime())
            break
    idx = 1
    for i in range(1, n):
        if float(t_arr[i]) >= hit_time:
            idx = i
            break
    t1, t2 = float(t_arr[idx - 1]), float(t_arr[idx])
    dt = t2 - t1
    if dt > 0 and t2 >= hit_time >= t1:
        f = (hit_time - t1) / dt
        vz = float(vz_arr[idx - 1]) + f * (float(vz_arr[idx]) - float(vz_arr[idx - 1]))
        vxy = float(vxy_arr[idx - 1]) + f * (float(vxy_arr[idx]) - float(vxy_arr[idx - 1]))
    else:
        vz = float(vz_arr[idx])
        vxy = float(vxy_arr[idx])
    total = math.sqrt(vz ** 2 + vxy ** 2)
    return {"ork_path": ork_path, "ground_contact_time_s": hit_time,
            "touchdown_total_mps": total}


def reopen_single(ork_path):
    print(json.dumps(reopen_touchdown(ork_path), indent=2))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    if mode == "sweep":
        sweep()
    elif mode == "repeat":
        repeat_single(sys.argv[2])
    elif mode == "reopen":
        reopen_single(sys.argv[2])
    else:
        raise SystemExit(f"unknown mode {mode}")
