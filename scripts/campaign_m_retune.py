"""Campaign M phase 2 -- retune both retro delays at late separation and MEASURE
the contiguous legal window, which is what disqualified the 2026-07-24 D-family
(sub-millisecond sustainer window, failed the dt ladder).

Separation happens after apogee, so apogee / Mach / ascent margin / propellant
are invariant across s1_separation_delay -- only the two delays move.

Usage: venv/Scripts/python.exe -X utf8 scripts/campaign_m_retune.py [sep ...]
"""
import copy, json, math, os, sys, time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO); os.chdir(_REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork, score_official
from scripts.osifog_direct_driver import BASE as DRIVER_BASE
from rocket_forge import MOTOR_DATABASE

G = 9.80665
PARKED = 1100.0
SEED = 16000
CATALOG = json.load(open("OSIFOG/experiments-2026-07-25/or_motor_catalog.json",
                         encoding="utf-8"))
_BY_DIGEST = {m["digest"]: m for m in CATALOG}
K_PARAMS = json.load(open("designs/osifog_visuals/candidate_K_celestial_datum_v7.json",
                          encoding="utf-8"))
OUT = "OSIFOG/experiments-2026-07-26/m2_retune.json"


def base(sep):
    p = copy.deepcopy(DRIVER_BASE)
    p.update(K_PARAMS)
    p.pop("livery", None); p.pop("livery_decals", None)
    p["s1_separation_delay"] = sep
    return p


def land(m, key):
    for s in m.get("stage_landings", []):
        if s["stage_key"] == key:
            return s
    return None


def speed(p, stage):
    try:
        ld = land(run_sim(generate_ork(p), seed=SEED), stage)
        return ld["total_speed"] if ld else 1e9
    except Exception:
        return 1e9


def predict_ignition(t_ground_ff, v_t, mass, thrust):
    r = thrust / (mass * G)
    if r <= 1.02:
        return None
    return t_ground_ff - (v_t ** 2 / (2.0 * G)) * math.log(r / (r - 1.0)) / v_t


def search_stage(p0, stage, centre, log):
    key = f"{stage}_retro_delay"
    best = (None, 1e9)
    span, step = 2.5, 0.5
    for _ in range(4):
        d = centre - span
        while d <= centre + span + 1e-9:
            p = dict(p0); p[key] = round(d, 6)
            v = speed(p, stage)
            if v < best[1]:
                best = (round(d, 6), v)
            d += step
        log(f"      {stage} step={step:<7.4f} best={best[1]:8.3f} @ {best[0]}")
        if best[0] is None or best[1] > 40.0:
            return best
        centre = best[0]; span, step = step * 1.2, step / 5.0
    return best


def map_window(p0, stage, centre, step, reach=60):
    """Contiguous <5 m/s interval around a legal centre, in seconds."""
    key = f"{stage}_retro_delay"
    lo = hi = centre
    for direction in (-1, 1):
        d = centre
        for _ in range(reach):
            d += direction * step
            p = dict(p0); p[key] = round(d, 6)
            if speed(p, stage) >= 5.0:
                break
            lo, hi = min(lo, d), max(hi, d)
    return round(hi - lo, 6), round(lo, 6), round(hi, 6)


def run_sep(sep, log):
    p = base(sep)
    p["s0_retro_delay"] = PARKED; p["s1_retro_delay"] = PARKED
    ff = run_sim(generate_ork(p), seed=SEED)
    s0, s1 = land(ff, "s0"), land(ff, "s1")
    if s0 is None or s1 is None or s1["total_speed"] == 0.0:
        log(f"  sep={sep}: no clean separation"); return None
    log(f"  sep={sep}: apo={ff['apogee_m']:.2f} mach={ff['mach']:.4f} "
        f"| s0 {s0['mass_kg']:.3f}kg {s0['total_speed']:.1f}m/s t={s0['time_s']:.2f} "
        f"| s1 {s1['mass_kg']:.3f}kg {s1['total_speed']:.1f}m/s t={s1['time_s']:.2f}")
    res = {"sep": sep, "apogee": ff["apogee_m"], "mach": ff["mach"]}
    for stage, ld in (("s0", s0), ("s1", s1)):
        thrust = _BY_DIGEST[MOTOR_DATABASE[p[f"{stage}_retro"]][5]]["avg_thrust_n"]
        c = predict_ignition(ld["time_s"], ld["total_speed"], ld["mass_kg"], thrust)
        if c is None:
            log(f"      {stage}: T/W<=1"); return None
        probe = dict(p)
        probe[f"{'s1' if stage=='s0' else 's0'}_retro_delay"] = PARKED
        delay, v = search_stage(probe, stage, c, log)
        res[f"{stage}_delay"], res[f"{stage}_speed"] = delay, v
        if v >= 5.0:
            log(f"      {stage}: floor {v:.2f} m/s -- NOT LANDABLE")
            res[f"{stage}_window"] = 0.0
            return res
        w, lo, hi = map_window(probe, stage, delay, step=0.002)
        res[f"{stage}_window"], res[f"{stage}_lo"], res[f"{stage}_hi"] = w, lo, hi
        res[f"{stage}_centre"] = round((lo + hi) / 2.0, 6)
        log(f"      {stage}: {v:.2f} m/s  window {w*1000:.0f} ms  [{lo}, {hi}]  centre {res[f'{stage}_centre']}")
    # score at the joint window centres -- the robust operating point, not the minimum
    q = base(sep)
    q["s0_retro_delay"] = res["s0_centre"]; q["s1_retro_delay"] = res["s1_centre"]
    m = run_sim(generate_ork(q), seed=SEED)
    sc = score_official(m, q)
    res["joint"] = {k: sc[k] for k in ("raw_score", "is_legal", "violations", "mean_V",
                                       "mean_E", "mean_N", "apogee_m",
                                       "pen_pos", "pen_vel", "pen_alt",
                                       "pen_ah" if "pen_ah" in sc else "apogee_horiz_pen",
                                       "prop_pen")}
    res["joint"]["s0_v"] = m["s0_landing_speed"]; res["joint"]["s1_v"] = m["s1_landing_speed"]
    log(f"    >> JOINT at window centres: score={sc['raw_score']:.0f} legal={sc['is_legal']} "
        f"v=({m['s0_landing_speed']:.3f},{m['s1_landing_speed']:.3f}) "
        f"meanR={math.hypot(sc['mean_E'],sc['mean_N']):.1f}")
    return res


def main():
    seps = [float(a) for a in sys.argv[1:]] or [47.0, 46.5, 46.0, 45.0, 43.0]
    init_or()
    logf = open(OUT.replace(".json", ".log"), "w", encoding="utf-8")
    def log(m):
        print(m, flush=True); logf.write(m + "\n"); logf.flush()
    out = []
    t0 = time.time()
    for sep in seps:
        try:
            r = run_sep(sep, log)
        except Exception as exc:
            log(f"  sep={sep} FAIL {str(exc)[:120]}"); continue
        if r: out.append(r)
        json.dump(out, open(OUT, "w"), indent=1)
    log(f"\n[{time.time()-t0:.0f}s] summary")
    for r in sorted(out, key=lambda r: -(r.get("joint", {}).get("raw_score") or -9e9)):
        j = r.get("joint", {})
        log(f"  sep={r['sep']:5.1f} s0w={r.get('s0_window',0)*1000:6.0f}ms "
            f"s1w={r.get('s1_window',0)*1000:6.0f}ms score={j.get('raw_score',0):9.0f} "
            f"legal={j.get('is_legal')}")
    log("wrote " + OUT)


main()
