"""Empirical Sustainer/Booster retro search against real OpenRocket.

The analytic model in `retro_motor_selector.py` found the root cause (retro
impulse matching) and picked the Booster motor that works, but it is not
accurate enough to rank fine choices -- it would have excluded the very I49N
configuration that measured a 100 ms window and 11/12 seeds.  So generate
candidates from the model, but rank them by what OpenRocket actually does.

Per candidate configuration:
  1. one freefall probe (both retro delays parked at 1100 s) yields apogee,
     Mach, full-stack margin, and each stage's landing mass, terminal speed and
     unpowered ground-contact time;
  2. hard gates on apogee / Mach / ascent margin drop the config early;
  3. each stage's ignition delay is then searched independently -- the two
     branches are physically independent after separation, so one stage's
     delay never moves the other's result;
  4. survivors get their contiguous <5 m/s window mapped.

Runs in-process on a single JVM: ~2 s per simulation.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/candidate_k_search.py [out.json]
"""
import copy
import json
import math
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork, parse_wind_csv, score_official
from rocket_forge import MOTOR_DATABASE

G = 9.80665
SEED = 16000
PARKED = 1100.0
WIND = parse_wind_csv("OSIFOG/OpenWind_File.csv")
CATALOG = json.load(open("OSIFOG/experiments-2026-07-25/or_motor_catalog.json",
                        encoding="utf-8"))
_BY_DIGEST = {m["digest"]: m for m in CATALOG}

# Gates.  Apogee is deliberately loose here: it is trimmed later with fin area
# and ballast, and a config that lands both stages is worth keeping.
APOGEE_RANGE = (2850.0, 3150.0)
MACH_MAX = 0.945
MARGIN_MIN = 0.15

BASE = {
    "s1_main": 20, "main_cluster_count": 3,
    "s1_separation_event": "launch", "s1_separation_delay": 23.10,
    "s0_main": None,
    "s0_body_len": 0.7, "s1_body_len": 1.0,
    "s0_body_rad": 0.082, "s1_body_rad": 0.082,
    "s1_fin_count": 4, "s1_fin_root": 0.12, "s1_fin_height": 0.10,
    "s1_fin_material": "legal_balsa",
    "s1_grid_fin_count": 4, "s1_grid_fin_root": 0.08, "s1_grid_fin_height": 0.06,
    "s1_grid_fin_material": "legal_balsa",
    "s0_fin_count": 0, "s0_grid_fin_count": 0,
    "s0_aft_ballast_kg": 1.0, "s0_aft_ballast_attachment": "airframe_bonded",
    "s0_aft_ballast_rod_radius_m": 0.025875,
    "s1_mid_ballast_attachment": "central_bonded",
    "s1_mid_ballast_rod_radius_m": 0.025,
    "s1_mid_ballast_pos_m": 0.12,
    "nose_length_m": 0.12, "nose_mass_kg": 0.05,
    "nose_ballast_attachment": "nose_shell_bonded",
    "nose_ballast_material": "aluminum",
    "launch_azimuth": 35.0, "launch_angle_deg": 1.6,
    "plugged_motors": True,
    "octaweb_rings": True, "octaweb_ring_width_m": 0.003,
    "interstage_coupler": True, "interstage_coupler_length_m": 0.05,
    "interstage_coupler_wall_m": 0.001,
    "interstage_coupler_sustainer_overlap_m": 0.025,
    "wind_levels": WIND,
}


def motor_perf(idx):
    mo = _BY_DIGEST.get(MOTOR_DATABASE[idx][5])
    if mo is None:
        raise KeyError(f"motor index {idx} ({MOTOR_DATABASE[idx][1]}) not in catalog")
    return mo["avg_thrust_n"], mo["total_impulse_ns"], mo["burn_time_s"]


def predict_ignition(t_ground_ff, v_t, mass, thrust):
    """Ignition time whose drag-included arrest lands at ground level."""
    r = thrust / (mass * G)
    if r <= 1.02:
        return None
    stop_dist = (v_t ** 2 / (2.0 * G)) * math.log(r / (r - 1.0))
    return t_ground_ff - stop_dist / v_t


def landing(metrics, stage_key):
    for s in metrics.get("stage_landings", []):
        if s["stage_key"] == stage_key:
            return s
    return None


def evaluate(params, seed=SEED):
    return run_sim(generate_ork(params), seed=seed)


def search_stage(base_params, stage, centre, log):
    """Grid-descend on one stage's retro delay; returns (best_delay, best_speed)."""
    key = f"{stage}_retro_delay"
    best = (None, 1e9)
    span, step = 2.5, 0.5
    for _ in range(4):
        lo, hi = centre - span, centre + span
        d = lo
        while d <= hi + 1e-9:
            p = dict(base_params)
            p[key] = round(d, 5)
            try:
                m = evaluate(p)
                ld = landing(m, stage)
                v = ld["total_speed"] if ld else 1e9
            except Exception:
                v = 1e9
            if v < best[1]:
                best = (round(d, 5), v)
            d += step
        log(f"      {stage} step={step:<6.3f} best={best[1]:8.3f} m/s @ {best[0]}")
        if best[0] is None or best[1] > 40.0:
            return best
        centre = best[0]
        span, step = step * 1.2, step / 5.0
    return best


def map_window(base_params, stage, centre, step=0.005, reach=40):
    """Measure the contiguous <5 m/s interval around a legal centre."""
    key = f"{stage}_retro_delay"
    lo = hi = centre
    for direction in (-1, 1):
        d = centre
        for _ in range(reach):
            d += direction * step
            p = dict(base_params)
            p[key] = round(d, 5)
            try:
                ld = landing(evaluate(p), stage)
                v = ld["total_speed"] if ld else 1e9
            except Exception:
                v = 1e9
            if v >= 5.0:
                break
            lo, hi = min(lo, d), max(hi, d)
    return round(hi - lo, 5), round(lo, 5), round(hi, 5)


def run_config(name, overrides, log):
    p = copy.deepcopy(BASE)
    p.update(overrides)
    p["s0_retro_delay"] = PARKED
    p["s1_retro_delay"] = PARKED
    try:
        ff = evaluate(p)
    except Exception as exc:
        log(f"  {name}: GEN/SIM FAIL {str(exc)[:90]}")
        return None
    segs = {s["segment"]: s["min_calibers"]
            for s in ff.get("ascent_stability_segments", [])}
    margin = segs.get("full_stack", float("nan"))
    apo, mach = ff.get("apogee_m", 0.0), ff.get("mach", 9.9)
    s0, s1 = landing(ff, "s0"), landing(ff, "s1")
    if s0 is None or s1 is None or s1["total_speed"] == 0.0:
        log(f"  {name}: no clean separation (apo={apo:.0f})")
        return None
    log(f"  {name}: apo={apo:7.1f} mach={mach:.3f} margin={margin:5.2f} "
        f"| s0 {s0['mass_kg']:6.3f}kg {s0['total_speed']:6.1f}m/s "
        f"| s1 {s1['mass_kg']:6.3f}kg {s1['total_speed']:5.1f}m/s")
    if not (APOGEE_RANGE[0] <= apo <= APOGEE_RANGE[1]):
        log("      rejected: apogee"); return None
    if mach > MACH_MAX:
        log("      rejected: Mach"); return None
    if not (margin > MARGIN_MIN):
        log("      rejected: ascent margin"); return None

    result = {"name": name, "overrides": overrides, "apogee_m": apo,
              "mach": mach, "margin": margin,
              "s0_mass": s0["mass_kg"], "s0_vt": s0["total_speed"],
              "s1_mass": s1["mass_kg"], "s1_vt": s1["total_speed"]}
    for stage, ld in (("s0", s0), ("s1", s1)):
        thrust = motor_perf(p[f"{stage}_retro"])[0]
        centre = predict_ignition(ld["time_s"], ld["total_speed"],
                                  ld["mass_kg"], thrust)
        if centre is None:
            log(f"      {stage}: T/W <= 1, cannot arrest"); return None
        probe = dict(p)
        probe[f"{'s1' if stage == 's0' else 's0'}_retro_delay"] = PARKED
        delay, speed = search_stage(probe, stage, centre, log)
        result[f"{stage}_delay"] = delay
        result[f"{stage}_speed"] = speed
        if speed >= 5.0:
            log(f"      {stage}: floor {speed:.2f} m/s -- not landable")
            result[f"{stage}_window"] = 0.0
            return result
        w, wlo, whi = map_window(probe, stage, delay)
        result[f"{stage}_window"] = w
        result[f"{stage}_window_lo"] = wlo
        result[f"{stage}_window_hi"] = whi
        log(f"      {stage}: {speed:.2f} m/s, window {w*1000:.0f} ms "
            f"[{wlo}, {whi}]")
    return result


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else \
        "OSIFOG/experiments-2026-07-25/k_arch/candidate_k_search.json"
    init_or()
    logf = open(out_path.replace(".json", ".log"), "w", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        logf.write(msg + "\n")
        logf.flush()

    # Sustainer retro candidates are 54-75 mm (its central mount is alone in an
    # 82 mm body); Booster retro candidates must be 38 mm (boxed in by the three
    # 54 mm K700W mains).
    configs = []
    for xfer, s0m, s1m in [
        (0.0, 43, 38), (0.0, 43, 39), (0.0, 20, 38),
        (1.5, 43, 38), (1.5, 43, 39), (1.5, 41, 38),
        (3.0, 43, 39), (3.0, 41, 39), (3.0, 41, 37),
        (4.0, 41, 37), (4.0, 40, 37), (4.5, 41, 37),
    ]:
        if MOTOR_DATABASE[s0m][2] > 0.0755 or MOTOR_DATABASE[s1m][2] > 0.0385:
            continue
        name = "x%03.1f_%s_%s" % (xfer, MOTOR_DATABASE[s0m][1],
                                  MOTOR_DATABASE[s1m][1])
        configs.append((name, {
            "s0_mid_ballast_kg": round(7.8 - xfer, 3),
            "s1_mid_ballast_kg": round(xfer, 3),
            "s0_retro": s0m, "s1_retro": s1m,
        }))

    log(f"evaluating {len(configs)} configurations")
    results = []
    for name, ov in configs:
        r = run_config(name, ov, log)
        if r:
            results.append(r)
            json.dump(results, open(out_path, "w"), indent=1, default=str)
    both = [r for r in results
            if r.get("s0_window", 0) > 0 and r.get("s1_window", 0) > 0]
    both.sort(key=lambda r: -min(r["s0_window"], r["s1_window"]))
    log("\n=== dual-legal configurations, ranked by worst window ===")
    for r in both:
        log("%-28s worst=%5.0f ms  s0=%5.0f ms (%.2f m/s)  s1=%5.0f ms (%.2f m/s)"
            % (r["name"], min(r["s0_window"], r["s1_window"]) * 1000,
               r["s0_window"] * 1000, r["s0_speed"],
               r["s1_window"] * 1000, r["s1_speed"]))
    if not both:
        log("  (none)")
    json.dump(results, open(out_path, "w"), indent=1, default=str)
    log(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
