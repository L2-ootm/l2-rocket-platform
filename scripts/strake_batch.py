#!/usr/bin/env python3
"""Strake-inclusive exploration batch under the live-insert gates.

Runs Family C (strakes/keels), strake+aft-fin hybrids, and the E8 baseline
through: (1) a free-descent-only OpenRocket run, ranked purely on
q_total/q_horizontal/q_vertical/angular-rate/horizontal-speed (never static
margin/CP/fin-area -- see scripts/descent_gates.py); (2) the hard passive-
descent admission gate; (3) for admitted candidates only, a short powered
probe guarded by PoweredEarlyStop.
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
)
from motor_data import burn_duration
from scripts.descent_gates import (
    descent_profile, impulse_opposition_fraction, passive_descent_admission,
    PoweredEarlyStop, apex_time_from_apogee_events,
)

ARTIFACTS = "artifacts/autoevo"
os.makedirs(ARTIFACTS, exist_ok=True)

BASE = {
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

SEEDS = [
    {"label": "E8_baseline", "s1_fin_count": 8, "s1_strake_count": 0},
    {"label": "ST3_triangular", "s1_fin_count": 0, "s1_strake_count": 3,
     "s1_strake_planform": "triangular", "s1_strake_length_m": 0.85,
     "s1_strake_span_m": 0.035, "s1_strake_position_m": 0.05},
    {"label": "ST4_triangular", "s1_fin_count": 0, "s1_strake_count": 4,
     "s1_strake_planform": "triangular", "s1_strake_length_m": 0.85,
     "s1_strake_span_m": 0.035, "s1_strake_position_m": 0.05},
    {"label": "ST3_tapered", "s1_fin_count": 0, "s1_strake_count": 3,
     "s1_strake_planform": "tapered", "s1_strake_length_m": 0.85,
     "s1_strake_span_m": 0.04, "s1_strake_position_m": 0.05},
    {"label": "ST4_clipped_delta", "s1_fin_count": 0, "s1_strake_count": 4,
     "s1_strake_planform": "clipped_delta", "s1_strake_length_m": 0.85,
     "s1_strake_span_m": 0.03, "s1_strake_position_m": 0.05},
    {"label": "ST4_hybrid_small_fin", "s1_fin_count": 4, "s1_fin_height": 0.30,
     "s1_fin_root": 0.15, "s1_strake_count": 4, "s1_strake_planform": "tapered",
     "s1_strake_length_m": 0.85, "s1_strake_span_m": 0.035, "s1_strake_position_m": 0.05},
    {"label": "ST4_hybrid_full_e8", "s1_fin_count": 8, "s1_strake_count": 4,
     "s1_strake_planform": "triangular", "s1_strake_length_m": 0.85,
     "s1_strake_span_m": 0.03, "s1_strake_position_m": 0.05},
]


def find_motor_index(name):
    for i, m in enumerate(MOTOR_DATABASE):
        if m[1] == name:
            return i
    raise ValueError(f"motor {name} not in MOTOR_DATABASE")


def run_ork(params, apex_t_hint=None):
    """Simulate one candidate.

    ``apex_t_hint``: the natural (unpowered) apex time to use for the
    descent-window filter, normally the already-known free-descent run's own
    apex_t. Pass this for every POWERED probe of a candidate. Recomputing
    apex_t from a powered branch's own APOGEE event is not just vulnerable to
    the "second, higher altitude peak" bug (fixed via
    ``apex_time_from_apogee_events``) -- it has a second, distinct failure
    mode: if ignition happens close enough to (or before) the natural apogee,
    the burn's added impulse can delay the vehicle's OWN single true apogee
    event past burnout entirely (measured case: burn 8.736-10.431s, but the
    branch's only APOGEE event fires at 15.13s), which again makes
    ``descent_profile``'s "if t < apex_t" filter discard the whole burn
    window. The free-descent apex_t is a property of the unpowered airframe
    trajectory up to that point and does not change based on what a retro
    motor does afterward, so it is always the correct reference for judging
    a powered probe of the same candidate. Leave unset only for the free
    probe itself, where there is no earlier known-good apex to reuse.
    """
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

        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        stab0 = br0.get(fdt.TYPE_STABILITY)
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        apogee = float(data.getMaxAltitude())
        min_margin = float('inf')
        for i in range(n0):
            s = float(stab0[i])
            if 0 < float(alt0[i]) < apogee * 0.95 and 0 < s < min_margin:
                min_margin = s

        branch_events = []
        for bi in range(int(data.getBranchCount())):
            br = data.getBranch(bi)
            bev = {}
            for ev in br.getEvents():
                bev.setdefault(str(ev.getType().name()), []).append(round(float(ev.getTime()), 4))
            branch_events.append(bev)
        sep_times = branch_events[1].get("STAGE_SEPARATION", [])
        sep_t = min(sep_times) if sep_times else None
        apogee_times = branch_events[0].get("APOGEE", [])
        first_apogee = min(apogee_times) if apogee_times else float('inf')
        staging_legal = sep_t is not None and sep_t < first_apogee

        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)
        mach = float(data.getMaxMachNumber())

        own_apex_t = apex_time_from_apogee_events(
            branch_events[1].get("APOGEE", []), t_arr, alt_arr,
        )
        apex_t = apex_t_hint if apex_t_hint is not None else own_apex_t
        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        samples = descent_profile(t_arr, px_arr, py_arr, vz_arr, theta_arr, phi_arr,
                                   thrust_arr, apex_t, hit_time)

        final_speed = None
        if hit_time and n > 1:
            idx = min(range(n), key=lambda i: abs(float(t_arr[i]) - hit_time))
            vxy_h = float(vxy_arr[idx])
            vz_h = float(vz_arr[idx])
            final_speed = math.sqrt(vxy_h ** 2 + vz_h ** 2)

        return {
            "mach": mach,
            "min_margin_cal": round(min_margin, 3) if min_margin != float('inf') else None,
            "staging_legal": staging_legal,
            "apex_t": round(apex_t, 3),
            "own_apex_t": round(own_apex_t, 3),
            "used_apex_t_hint": apex_t_hint is not None,
            "hit_t": round(hit_time, 3) if hit_time else None,
            "final_speed_ms": round(final_speed, 3) if final_speed else None,
            "samples": samples,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    motor_name = "J350W"
    motor_idx = find_motor_index(motor_name)
    burn_s = burn_duration(motor_idx)
    print(f"Using {motor_name}, burn duration {burn_s:.2f}s\n")

    results = []
    for seed in SEEDS:
        label = seed["label"]
        p = dict(BASE)
        p.update({k: v for k, v in seed.items() if k != "label"})
        print(f"=== {label}: free-descent probe ===")
        try:
            free = run_ork(p)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({"label": label, "error": str(exc)})
            continue

        admitted, reasons, metrics = passive_descent_admission(free["samples"], burn_s)
        print(f"  legal={free['staging_legal']} mach={free['mach']:.3f} margin={free['min_margin_cal']}")
        print(f"  descent metrics: {metrics}")
        print(f"  ADMITTED={admitted} reasons={reasons}")

        entry = {
            "label": label,
            "staging_legal": free["staging_legal"],
            "mach": round(free["mach"], 4),
            "min_margin_cal": free["min_margin_cal"],
            "free_descent_final_speed_ms": free["final_speed_ms"],
            "descent_metrics": metrics,
            "admitted": admitted,
            "admission_reasons": reasons,
            "powered_probes": [],
        }

        if admitted:
            watchdog = PoweredEarlyStop()
            apex_t = free["apex_t"]
            for delay in [apex_t + 0.3, apex_t + 1.0, apex_t + 2.0]:
                suspend, why = watchdog.should_suspend(motor_name)
                if suspend:
                    print(f"  early-stop before delay={delay:.2f}: {why}")
                    break
                pp = dict(p)
                pp["s1_retro"] = motor_idx
                pp["s1_retro_delay"] = round(delay, 3)
                print(f"  powered probe delay={delay:.2f} ...")
                try:
                    powered = run_ork(pp, apex_t_hint=apex_t)
                except Exception as exc:
                    print(f"    ERROR: {exc}")
                    continue
                burn_samples = [s for s in powered["samples"] if s["thrust_n"] > 1.0]
                burn_mean_q = (
                    sum(s["q_total"] for s in burn_samples if s["q_total"] is not None)
                    / max(1, len([s for s in burn_samples if s["q_total"] is not None]))
                ) if burn_samples else None
                i_opp_frac, i_meta = impulse_opposition_fraction(powered["samples"])
                flip = burn_mean_q is not None and burn_mean_q < 0
                watchdog.record(label, motor_name, delay, i_opp_frac,
                                 powered["final_speed_ms"], free["final_speed_ms"],
                                 burn_mean_q, flip)
                entry["powered_probes"].append({
                    "delay_s": round(delay, 3),
                    "final_speed_ms": powered["final_speed_ms"],
                    "burn_mean_q": round(burn_mean_q, 4) if burn_mean_q is not None else None,
                    "i_opp_over_i_total": round(i_opp_frac, 4) if i_opp_frac is not None else None,
                    "flip_detected": flip,
                    "legal_branch": powered["final_speed_ms"] is not None and powered["final_speed_ms"] < 5.0,
                })
                print(f"    speed={powered['final_speed_ms']} burn_q={burn_mean_q} I_opp/I_total={i_opp_frac}")

        results.append(entry)
        print()

    with open(os.path.join(ARTIFACTS, "strake-batch-results.json"), "w") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)

    legal = [r for r in results for pp in r.get("powered_probes", []) if pp.get("legal_branch")]
    print("=" * 60)
    print(f"LEGAL BRANCH FOUND: {bool(legal)}")
    print(f"Wrote {ARTIFACTS}/strake-batch-results.json")


if __name__ == "__main__":
    main()
