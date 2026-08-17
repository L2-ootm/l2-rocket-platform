"""Campaign M -- reopen the late-separation branch against the CORRECT score formula.

Why: mining every recorded OpenRocket run in this repo against the rule-PDF
formula shows the 2026-07-24 D-family (s1_separation_delay = 44.0 s) reaching
769,589 at seed 16000 -- 167,036 above Candidate K -- purely by collapsing the
mean-touchdown-position term from 218,553 to 85,629.  D was abandoned because
its legal delay window is sub-millisecond and it fails the dt ladder, not
because the architecture is wrong.

Phase 1 (this file): retros parked at 1100 s, sweep separation delay only, and
measure the position term directly.  Freefall probes cost one simulation each.

Usage: venv/Scripts/python.exe -X utf8 scripts/campaign_m_late_separation.py
"""
import copy, json, math, os, sys, time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO); os.chdir(_REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork
from scripts.osifog_direct_driver import BASE as DRIVER_BASE

PARKED = 1100.0
SEED = 16000
OUT = "OSIFOG/experiments-2026-07-26/m1_separation_freefall.json"

K_PARAMS = json.load(open("designs/osifog_visuals/candidate_K_celestial_datum_v7.json",
                          encoding="utf-8"))


def base():
    p = copy.deepcopy(DRIVER_BASE)
    p.update(K_PARAMS)
    p.pop("livery", None)
    p.pop("livery_decals", None)
    return p


def land(m, key):
    for s in m.get("stage_landings", []):
        if s["stage_key"] == key:
            return s
    return None


def probe(sep, launch_angle=None, azimuth=None):
    p = base()
    p["s1_separation_delay"] = sep
    p["s0_retro_delay"] = PARKED
    p["s1_retro_delay"] = PARKED
    if launch_angle is not None:
        p["launch_angle_deg"] = launch_angle
    if azimuth is not None:
        p["launch_azimuth"] = azimuth
    m = run_sim(generate_ork(p), seed=SEED)
    s0, s1 = land(m, "s0"), land(m, "s1")
    if s0 is None or s1 is None or s1["total_speed"] == 0.0:
        return None
    mE = (s0["east_m"] + s1["east_m"]) / 2.0
    mN = (s0["north_m"] + s1["north_m"]) / 2.0
    segs = {s["segment"]: s["min_calibers"]
            for s in m.get("ascent_stability_segments", [])}
    pen_alt = 3000.0 * (m["apogee_m"] - 3000.0) ** 2
    pen_ah = 16.0 * (m["apogee_east_m"] ** 2 + m["apogee_north_m"] ** 2)
    pen_pos = 2.0 * (mE ** 2 + mN ** 2)
    pen_prop = 7500.0 * m.get("m_prop_kg_actual", 0.0)
    # Ceiling: everything except the touchdown-velocity term, which retuning sets.
    ceiling = 900000.0 - pen_alt - pen_ah - pen_pos - pen_prop
    return dict(sep=sep, launch_angle=p["launch_angle_deg"], azimuth=p["launch_azimuth"],
                apogee=m["apogee_m"], mach=m["mach"],
                margin=segs.get("full_stack", float("nan")),
                apoE=m["apogee_east_m"], apoN=m["apogee_north_m"],
                s0_t=s0["time_s"], s0_v=s0["total_speed"], s0_m=s0["mass_kg"],
                s0_E=s0["east_m"], s0_N=s0["north_m"],
                s1_t=s1["time_s"], s1_v=s1["total_speed"], s1_m=s1["mass_kg"],
                s1_E=s1["east_m"], s1_N=s1["north_m"],
                mE=mE, mN=mN, mr=math.hypot(mE, mN),
                pen_alt=pen_alt, pen_ah=pen_ah, pen_pos=pen_pos, pen_prop=pen_prop,
                ceiling=ceiling)


def main():
    init_or()
    rows = []
    t0 = time.time()
    print(f"{'sep':>6} {'apo':>8} {'mach':>6} {'SM':>5} {'apoR':>6} | "
          f"{'s0 t':>6} {'s0 vT':>6} {'s0 r':>6} | {'s1 t':>6} {'s1 vT':>6} {'s1 r':>6} | "
          f"{'meanR':>6} {'posPen':>8} {'CEILING':>8}", flush=True)
    for sep in [x / 2.0 for x in range(46, 101)]:      # 23.0 .. 50.0 step 0.5
        try:
            r = probe(sep)
        except Exception as exc:
            print(f"{sep:6.1f}  FAIL {str(exc)[:70]}", flush=True); continue
        if r is None:
            print(f"{sep:6.1f}  no clean separation", flush=True); continue
        rows.append(r)
        print(f"{r['sep']:6.1f} {r['apogee']:8.2f} {r['mach']:6.4f} {r['margin']:5.2f} "
              f"{math.hypot(r['apoE'],r['apoN']):6.1f} | "
              f"{r['s0_t']:6.1f} {r['s0_v']:6.1f} {math.hypot(r['s0_E'],r['s0_N']):6.1f} | "
              f"{r['s1_t']:6.1f} {r['s1_v']:6.1f} {math.hypot(r['s1_E'],r['s1_N']):6.1f} | "
              f"{r['mr']:6.1f} {r['pen_pos']:8.0f} {r['ceiling']:8.0f}", flush=True)
    rows.sort(key=lambda r: -r["ceiling"])
    print(f"\n[{time.time()-t0:.0f}s]  best ceilings:")
    for r in rows[:10]:
        print(f"  sep={r['sep']:5.1f}  ceiling={r['ceiling']:8.0f}  posPen={r['pen_pos']:8.0f} "
              f"meanR={r['mr']:6.1f}  apo={r['apogee']:8.2f}  s0vT={r['s0_v']:6.1f} s1vT={r['s1_v']:6.1f}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rows, open(OUT, "w"), indent=1)
    print("wrote", OUT)


main()
