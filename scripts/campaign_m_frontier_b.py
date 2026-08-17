"""Campaign M phase 4 -- the actual frontier: how low can the Sustainer's arrest
speed go as a function of separation time?

Late separation collapses the mean-touchdown-position penalty (200,071 -> 18,362)
but leaves the Sustainer only a few seconds between release and its retro burn,
so it arrests dirty (horizontal residual). This measures the achievable floor per
separation with a sub-millisecond search, so the trade can be made on numbers.

Booster branch is parked; the two branches are physically independent after
separation.

Usage: venv/Scripts/python.exe -X utf8 scripts/campaign_m_frontier.py
"""
import copy, json, math, os, sys, time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO); os.chdir(_REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork
from scripts.osifog_direct_driver import BASE as DRIVER_BASE
from rocket_forge import MOTOR_DATABASE

G = 9.80665
PARKED = 1100.0
SEED = 16000
CATALOG = json.load(open("OSIFOG/experiments-2026-07-25/or_motor_catalog.json",
                         encoding="utf-8"))
_BY_DIGEST = {m["digest"]: m for m in CATALOG}
K = json.load(open("designs/osifog_visuals/candidate_K_celestial_datum_v7.json",
                   encoding="utf-8"))
M1 = {r["sep"]: r for r in json.load(
    open("OSIFOG/experiments-2026-07-26/m1_separation_freefall.json", encoding="utf-8"))}
OUT = "OSIFOG/experiments-2026-07-26/m4b_sustainer_frontier.json"


def base(sep):
    p = copy.deepcopy(DRIVER_BASE); p.update(K)
    p.pop("livery", None); p.pop("livery_decals", None)
    p["s1_separation_delay"] = sep
    p["s1_retro_delay"] = PARKED
    return p


def land(m, k):
    for s in m.get("stage_landings", []):
        if s["stage_key"] == k:
            return s


def probe(p, stage="s0"):
    try:
        ld = land(run_sim(generate_ork(p), seed=SEED), stage)
        if not ld:
            return 1e9, None
        return ld["total_speed"], ld
    except Exception:
        return 1e9, None


def descend(p0, key, centre, log):
    """6-round grid descent: 500 ms -> 0.16 ms."""
    best = (None, 1e9, None)
    span, step = 2.5, 0.5
    for rnd in range(6):
        d = centre - span
        while d <= centre + span + 1e-9:
            p = dict(p0); p[key] = round(d, 7)
            v, ld = probe(p)
            if v < best[1]:
                best = (round(d, 7), v, ld)
            d += step
        log(f"      step={step*1000:8.3f}ms  best={best[1]:8.3f} m/s @ {best[0]}")
        if best[0] is None or best[1] > 60.0:
            return best
        centre = best[0]; span, step = step * 1.2, step / 5.0
    return best


def main():
    seps = [37.5, 40.5, 43.0, 45.0, 47.0, 32.0, 36.0, 35.0, 33.0, 31.0]
    init_or()
    logf = open(OUT.replace(".json", ".log"), "w", encoding="utf-8")
    def log(m):
        print(m, flush=True); logf.write(m + "\n"); logf.flush()
    out = []
    t0 = time.time()
    for sep in seps:
        p = base(sep)
        p["s0_retro_delay"] = PARKED
        ff = run_sim(generate_ork(p), seed=SEED)
        s0 = land(ff, "s0")
        thrust = _BY_DIGEST[MOTOR_DATABASE[p["s0_retro"]][5]]["avg_thrust_n"]
        r = thrust / (s0["mass_kg"] * G)
        centre = s0["time_s"] - (s0["total_speed"] ** 2 / (2 * G)) * math.log(r / (r - 1)) / s0["total_speed"]
        pp = M1.get(sep, {}).get("pen_pos")
        log(f"  sep={sep:5.1f}  ff vT={s0['total_speed']:6.1f} t={s0['time_s']:6.2f} "
            f"m={s0['mass_kg']:6.3f}  predicted ignition {centre:.3f}  posPen={pp}")
        delay, v, ld = descend(p, "s0_retro_delay", centre, log)
        row = dict(sep=sep, pen_pos=pp, s0_delay=delay, s0_speed=v,
                   s0_vz=(ld or {}).get("vz_ms"), s0_vxy=(ld or {}).get("vxy_ms"),
                   s0_theta=(ld or {}).get("orientation_theta_deg"))
        out.append(row)
        log(f"    => sep={sep:5.1f} FLOOR {v:7.3f} m/s  (vz={row['s0_vz']} vxy={row['s0_vxy']} "
            f"theta={row['s0_theta']})  {'LEGAL' if v < 5.0 else 'illegal'}")
        json.dump(out, open(OUT, "w"), indent=1)
    log(f"\n[{time.time()-t0:.0f}s] FRONTIER")
    log(f"{'sep':>6} {'posPen':>8} {'s0 floor':>9} {'vz':>7} {'vxy':>7} {'theta':>7}")
    for r in out:
        log(f"{r['sep']:6.1f} {(r['pen_pos'] or 0):8.0f} {r['s0_speed']:9.3f} "
            f"{(r['s0_vz'] or 0):7.2f} {(r['s0_vxy'] or 0):7.2f} {(r['s0_theta'] or 0):7.1f}")
    log("wrote " + OUT)


main()
