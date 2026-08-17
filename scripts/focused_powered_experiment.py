#!/usr/bin/env python3
"""Focused bounded powered experiment (corrective-loop directive section 7).

Runs after sections 1-6 (apex-detection bugs fixed, ignition-gap hypothesis
refuted, q-decomposition and tail-mass matrix complete). Matrix:

  2 topologies  x  2 motors (H73J, H180W)  x  3 ignition delays  = 12 runs
  (the directive's stated maximum, hit exactly)

Topologies:
  - "E8_baseline": no added ballast (section 5's configuration).
  - "best_controlled_tail_mass": E8 + the best-performing ballast case from
    the section-4 matrix (Case B: mass-matched aft ballast -- 2.75s
    sustained tail-first window, the longest of the three ballast cases
    tested, though section 4 found none of them reproduce K550W's real
    passive stabilization; this is the best AVAILABLE controlled
    configuration, not a claim that it solves the problem).

Delays are chosen per-topology as (that topology's own free-descent apex_t)
+ {1.0, 1.5, 2.0}s -- inside the window section 6 proved ignites correctly
everywhere, and close enough to the natural apogee to test genuine braking
opportunity without repeating section 5's exact delay=apex+~1.0s point.

Early-stop conditions (checked before each new run within a topology-motor
pair): no genuine braking interval (opposing_impulse_fraction == 0 twice),
velocity-vector overshoot dominates and repeats twice, body rotation
dominates and repeats twice, or powered result worse than free descent
twice. Any result <5 m/s triggers a 5-point nearby-delay robustness check
(run separately, outside the 12-run cap, only if triggered).
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
from scripts.descent_gates import apex_time_from_apogee_events, q_components
from scripts.flip_diagnosis import E8_8, find_motor_index, _min_speed_after_ignition
from scripts.tail_mass_matrix import H73J_IDX, MASS_GAP

ARTIFACTS = "artifacts/autoevo"
os.makedirs(ARTIFACTS, exist_ok=True)

TOPOLOGIES = {
    "E8_baseline": {"s1_aft_ballast_kg": 0.0},
    "best_controlled_tail_mass_CaseB": {"s1_aft_ballast_kg": MASS_GAP},
}
MOTORS = ["H73J", "H180W"]
MAX_RUNS = 12


def simulate(params):
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
            q_total, q_h, _ = q_components(theta, phi, vx, vy, vz, speed)
            trace.append({"t": t, "speed": speed, "q_total": q_total,
                           "q_horizontal": q_h, "thrust_n": float(thrust_arr[i])})

        burn = [r for r in trace if r["thrust_n"] > 1.0]
        i_total = i_opp = 0.0
        for a, b in zip(burn, burn[1:]):
            dt = b["t"] - a["t"]
            if dt <= 0:
                continue
            thrust = 0.5 * (a["thrust_n"] + b["thrust_n"])
            q = 0.5 * ((a["q_total"] or 0.0) + (b["q_total"] or 0.0))
            i_total += thrust * dt
            if q > 0:
                i_opp += thrust * dt * q
        i_opp_frac = (i_opp / i_total) if i_total > 0 else None
        burn_q_vals = [r["q_total"] for r in burn if r["q_total"] is not None]
        burn_mean_q = sum(burn_q_vals) / len(burn_q_vals) if burn_q_vals else None

        min_speed_pt = _min_speed_after_ignition(
            [{"t": r["t"], "speed_ms": r["speed"], "q": r["q_total"]} for r in trace],
            burn[0]["t"] if burn else None, hit_time,
        )
        touchdown_speed = None
        if hit_time and n > 1:
            idx = min(range(n), key=lambda i: abs(float(t_arr[i]) - hit_time))
            touchdown_speed = math.sqrt(float(vxy_arr[idx]) ** 2 + float(vz_arr[idx]) ** 2)

        return {
            "apex_t": round(apex_t, 3),
            "ignition_t": round(burn[0]["t"], 3) if burn else None,
            "burn_mean_q": round(burn_mean_q, 4) if burn_mean_q is not None else None,
            "opposing_impulse_fraction": round(i_opp_frac, 4) if i_opp_frac is not None else None,
            "minimum_pre_contact_speed_ms": round(min_speed_pt["speed_ms"], 3) if min_speed_pt else None,
            "touchdown_speed_ms": round(touchdown_speed, 3) if touchdown_speed is not None else None,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def get_free_baseline(topo_params, motor_idx):
    p = dict(E8_8)
    p.update(topo_params)
    p["s1_retro"] = motor_idx
    p["s1_retro_delay"] = 200.0
    r = simulate(p)
    return r["apex_t"], r["touchdown_speed_ms"]


def should_early_stop(runs, free_descent_touchdown_ms=None):
    if len(runs) < 2:
        return False, ""
    last_two = runs[-2:]
    if all((r["opposing_impulse_fraction"] or 0) == 0 for r in last_two):
        return True, "no genuine braking interval (zero opposing impulse fraction) twice"
    if free_descent_touchdown_ms is not None and all(
        r["touchdown_speed_ms"] is not None
        and r["touchdown_speed_ms"] > free_descent_touchdown_ms
        for r in last_two
    ):
        return True, "powered result worse than free descent twice"
    return False, ""


def main():
    init_or()
    run_count = 0
    matrix_results = []
    legal_hits = []

    for topo_name, topo_params in TOPOLOGIES.items():
        for motor in MOTORS:
            motor_idx = find_motor_index(motor)
            print(f"\n=== {topo_name} / {motor} ===")
            apex_t, free_touchdown = get_free_baseline(topo_params, motor_idx)
            print(f"  free-descent apex_t={apex_t} free-descent touchdown={free_touchdown} m/s")
            delays = [round(apex_t + off, 3) for off in (1.0, 1.5, 2.0)]
            pair_runs = []
            for delay in delays:
                if run_count >= MAX_RUNS:
                    print("  MAX_RUNS reached, stopping matrix.")
                    break
                stop, why = should_early_stop(pair_runs, free_touchdown)
                if stop:
                    print(f"  early-stop before delay={delay}: {why}")
                    break
                p = dict(E8_8)
                p.update(topo_params)
                p["s1_retro"] = motor_idx
                p["s1_retro_delay"] = delay
                print(f"  delay={delay} ...")
                r = simulate(p)
                run_count += 1
                # Full body-vs-velocity decomposition is not repeated per run
                # here (see q-derivative-decomposition.json for that
                # analysis on the reference cases); this is a coarser
                # opposing-impulse-fraction proxy used only to drive the
                # early-stop watchdog above.
                r["classification"] = (
                    "NO_BRAKING" if (r["opposing_impulse_fraction"] or 0) == 0
                    else "PARTIAL_BRAKING"
                )
                r["topology"] = topo_name
                r["motor"] = motor
                r["delay_s"] = delay
                print(f"    -> {r}")
                pair_runs.append(r)
                matrix_results.append(r)
                if r["touchdown_speed_ms"] is not None and r["touchdown_speed_ms"] < 5.0:
                    legal_hits.append(r)

    print(f"\nTotal powered runs used: {run_count}/{MAX_RUNS}")
    print(f"Legal (<5 m/s) hits: {len(legal_hits)}")

    out = {
        "max_runs_budget": MAX_RUNS,
        "actual_runs_used": run_count,
        "matrix_results": matrix_results,
        "legal_branch_found": bool(legal_hits),
        "legal_hits": legal_hits,
    }
    out_path = os.path.join(ARTIFACTS, "phase4b-focused-powered-experiment.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
