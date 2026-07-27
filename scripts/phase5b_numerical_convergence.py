#!/usr/bin/env python3
"""Phase 5B booster numerical-convergence audit (mission section 1).

For the recovered eight-forward-fin booster candidate
(artifacts/autoevo/historical-3p5135-candidate.json ::
complete_parameters_powered_rerun), sweep 9 representative s1_retro_delay
values across 5 diagnostic simulation timesteps (0.05/0.02/0.01/0.005/0.001s)
to check whether the millisecond-scale legal/illegal classification found at
the official 0.05s timestep survives finer integration.

Writes:
  artifacts/autoevo/phase5b/booster-numerical-convergence.json
  artifacts/autoevo/phase5b/booster-contact-mode-map.json

Does NOT alter the official authority timestep (0.05s, unchanged in
osifog_sweep.py's default). Diagnostic-only.
"""
import json
import math
import os
import sys
import tempfile

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, _retro_burn_diagnostic, _finite_difference,
    run_sim,
)
from scripts.descent_gates import apex_time_from_apogee_events

ARTIFACTS = "artifacts/autoevo/phase5b"
os.makedirs(ARTIFACTS, exist_ok=True)

with open("artifacts/autoevo/historical-3p5135-candidate.json", encoding="utf-8") as f:
    _CAND = json.load(f)
BASE_PARAMS = dict(_CAND["complete_parameters_powered_rerun"])

BOOSTER_BRANCH = 1  # corrected mapping: branch 1 = s1 = Booster

DELAYS_S = [29.8600, 29.8605, 29.8610, 29.8625, 29.8640, 29.8645, 29.8650, 29.8660, 29.8665]
TIMESTEPS_S = [0.05, 0.02, 0.01, 0.005, 0.001]

OFFICIAL_TIMESTEP_S = 0.05


def _classify_contact_mode(t_arr, alt_arr, ignition_t, hit_time, burnout_t):
    """Classify the contact mode using altitude behavior between ignition and contact."""
    if ignition_t is None or hit_time is None:
        return "UNKNOWN"
    idxs = [i for i in range(len(t_arr)) if ignition_t <= float(t_arr[i]) <= hit_time]
    if not idxs:
        return "UNKNOWN"
    alts = [float(alt_arr[i]) for i in idxs]
    ts = [float(t_arr[i]) for i in idxs]
    max_alt = max(alts)
    max_alt_idx = alts.index(max_alt)
    alt_at_ignition = alts[0]
    burning_at_contact = burnout_t is not None and burnout_t >= hit_time - 1e-6

    # Ascend-then-recontact: altitude climbs measurably above its value at
    # ignition before falling back to contact.
    if max_alt > alt_at_ignition + 0.5 and ts[max_alt_idx] < ts[-1]:
        return "ASCEND_THEN_RECONTACT"
    if not burning_at_contact:
        return "POST_BURN_COAST_CONTACT"
    # Near-hover: vertical speed magnitude stays small for a meaningful
    # fraction of the post-ignition window (rough proxy via altitude slope).
    if len(alts) > 2:
        span = max(alts) - min(alts)
        duration = ts[-1] - ts[0]
        if duration > 1e-6 and span / duration < 1.0:
            return "NEAR_HOVER_CONTACT"
    return "DIRECT_DESCENT_CONTACT"


def run_one(delay_s, timestep_s):
    p = dict(BASE_PARAMS)
    p["s1_retro_delay"] = float(delay_s)
    p["timestep_s"] = float(timestep_s)

    ork_xml = generate_ork(p)
    m = run_sim(ork_xml, seed=SIM_SEED)
    landing = next(
        (s for s in m.get("stage_landings", []) if int(s.get("branch", -1)) == BOOSTER_BRANCH),
        None,
    )

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
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)

        apogee_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.APOGEE
        )
        apex_t = apex_time_from_apogee_events(apogee_events, t_arr, alt_arr)

        ignition_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.IGNITION
        )
        ignition_event_t = next((t for t in ignition_events if t > apex_t - 1e-6), None)

        first_nonzero_thrust_index = None
        for i in range(n):
            if float(t_arr[i]) > apex_t and float(thrust_arr[i]) > 1.0:
                first_nonzero_thrust_index = i
                break
        first_nonzero_thrust_t = (
            float(t_arr[first_nonzero_thrust_index]) if first_nonzero_thrust_index is not None else None
        )

        burnout_events = sorted(
            float(ev.getTime()) for ev in br.getEvents()
            if ev.getType() == FlightEvent.Type.BURNOUT
        )
        burnout_t = None
        for bt in burnout_events:
            if first_nonzero_thrust_t is None or bt >= first_nonzero_thrust_t - 1.0e-6:
                burnout_t = bt
                break

        hit_index = None
        for i in range(1, n):
            if float(alt_arr[i]) <= 0.0 and float(alt_arr[i - 1]) > 0.0:
                hit_index = i
                break

        ground_contact_bracket = None
        if hit_index is not None:
            ground_contact_bracket = [
                {"time_s": float(t_arr[hit_index - 1]), "altitude_m": float(alt_arr[hit_index - 1])},
                {"time_s": float(t_arr[hit_index]), "altitude_m": float(alt_arr[hit_index])},
            ]

        min_alt_before_contact = None
        max_alt_after_ignition = None
        if first_nonzero_thrust_t is not None and hit_index is not None:
            window_idxs = [i for i in range(n) if first_nonzero_thrust_t <= float(t_arr[i]) <= float(t_arr[hit_index])]
            if window_idxs:
                min_alt_before_contact = min(float(alt_arr[i]) for i in window_idxs)
                max_alt_after_ignition = max(float(alt_arr[i]) for i in window_idxs)

        hit_time_event = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time_event = float(ev.getTime())
                break

        contact_mode = _classify_contact_mode(
            t_arr, alt_arr, first_nonzero_thrust_t, hit_time_event, burnout_t
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    hit_time = landing["time_s"] if landing else hit_time_event
    touchdown_vz = landing["vz_ms"] if landing else None
    touchdown_vxy = landing["vxy_ms"] if landing else None
    touchdown_total = (
        math.sqrt(touchdown_vz ** 2 + touchdown_vxy ** 2) if landing else None
    )
    motor_burning_at_contact = (
        burnout_t is not None and hit_time is not None and burnout_t >= hit_time - 1e-6
    )

    return {
        "configured_ignition_delay_s": delay_s,
        "ignition_event_time_s": ignition_event_t,
        "first_nonzero_thrust_sample_time_s": first_nonzero_thrust_t,
        "simulation_timestep_s": timestep_s,
        "integrator_settings": {"simulator": "RK4Simulator", "calculator": "BarrowmanCalculator"},
        "first_ground_contact_time_s": hit_time,
        "ground_contact_event_state": {"time_s": hit_time_event},
        "ground_contact_interpolation_bracket": ground_contact_bracket,
        "minimum_altitude_before_contact_m": min_alt_before_contact,
        "maximum_altitude_after_ignition_before_contact_m": max_alt_after_ignition,
        "burnout_time_s": burnout_t,
        "motor_burning_at_contact": motor_burning_at_contact,
        "touchdown_vz_mps": touchdown_vz,
        "touchdown_vxy_mps": touchdown_vxy,
        "touchdown_total_mps": touchdown_total,
        "contact_mode": contact_mode,
        "legal": touchdown_total is not None and touchdown_total < 5.0,
    }


def run_audit():
    init_or()
    rows = []
    total = len(DELAYS_S) * len(TIMESTEPS_S)
    count = 0
    for delay in DELAYS_S:
        for ts in TIMESTEPS_S:
            count += 1
            print(f"[{count}/{total}] delay={delay} timestep={ts} ...", file=sys.stderr)
            row = run_one(delay, ts)
            print(
                f"  touchdown={row['touchdown_total_mps']} legal={row['legal']} "
                f"mode={row['contact_mode']}",
                file=sys.stderr,
            )
            rows.append(row)

    # repeatability: same-process rerun at the official timestep for every delay
    for delay in DELAYS_S:
        official = next(r for r in rows if r["configured_ignition_delay_s"] == delay
                         and r["simulation_timestep_s"] == OFFICIAL_TIMESTEP_S)
        rerun = run_one(delay, OFFICIAL_TIMESTEP_S)
        official["repeatability"] = {
            "same_process_rerun_touchdown_total_mps": rerun["touchdown_total_mps"],
            "bit_identical": rerun["touchdown_total_mps"] == official["touchdown_total_mps"],
        }
    for r in rows:
        r.setdefault("repeatability", None)

    # Convergence classification per delay: does legal/illegal status agree
    # across all 5 timesteps?
    convergence = {}
    for delay in DELAYS_S:
        delay_rows = [r for r in rows if r["configured_ignition_delay_s"] == delay]
        legal_flags = {r["simulation_timestep_s"]: r["legal"] for r in delay_rows}
        modes = {r["simulation_timestep_s"]: r["contact_mode"] for r in delay_rows}
        touchdowns = {r["simulation_timestep_s"]: r["touchdown_total_mps"] for r in delay_rows}
        agree = len(set(legal_flags.values())) == 1
        convergence[str(delay)] = {
            "legal_by_timestep": legal_flags,
            "contact_mode_by_timestep": modes,
            "touchdown_total_mps_by_timestep": touchdowns,
            "classification_agrees_across_timesteps": agree,
        }

    out = {
        "candidate": "historical-3p5135-booster-branch (eight-forward-fin booster, H180W retro)",
        "delays_s": DELAYS_S,
        "timesteps_s": TIMESTEPS_S,
        "official_timestep_s": OFFICIAL_TIMESTEP_S,
        "rows": rows,
        "convergence_by_delay": convergence,
    }
    with open(os.path.join(ARTIFACTS, "booster-numerical-convergence.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)

    contact_mode_map = {
        str(delay): {str(ts): r["contact_mode"] for ts, r in
                     ((rr["simulation_timestep_s"], rr) for rr in rows if rr["configured_ignition_delay_s"] == delay)}
        for delay in DELAYS_S
    }
    with open(os.path.join(ARTIFACTS, "booster-contact-mode-map.json"), "w", encoding="utf-8") as f:
        json.dump(contact_mode_map, f, indent=2, sort_keys=True, default=str)

    n_agree = sum(1 for v in convergence.values() if v["classification_agrees_across_timesteps"])
    print(f"\nWrote {ARTIFACTS}/booster-numerical-convergence.json", file=sys.stderr)
    print(f"Wrote {ARTIFACTS}/booster-contact-mode-map.json", file=sys.stderr)
    print(f"{n_agree}/{len(DELAYS_S)} delays classification-agree across all 5 timesteps", file=sys.stderr)
    print(json.dumps({"n_agree": n_agree, "n_total": len(DELAYS_S)}))


if __name__ == "__main__":
    run_audit()
