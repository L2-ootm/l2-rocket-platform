"""Seed x timestep robustness matrix for an OSIFOG Level 3 candidate.

Playbook sec 6 item 1: the submission is a single deterministic run, but a
candidate whose sub-5 m/s landing exists only at one seed or only at
dt = 0.05 s is NOT physically robust and must be reported as such.
sec 3.5 records that historically 100% of sub-5 m/s landings reverted to
30-41 m/s at dt <= 0.01 s, so this check is the real gate.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/osifog_robustness.py <params.json> [out.json]
"""
import sys, os, json, copy

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import osifog_sweep as S
from osifog_direct_driver import BASE

SEEDS = [16000, 7, 12345]
TIMESTEPS = [0.05, 0.02, 0.01]


def main():
    params_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    p0 = copy.deepcopy(BASE)
    p0.update(json.load(open(params_path)))

    S.init_or()
    table = {}
    print("%-7s %-6s | %9s %9s | %8s %8s | %8s %8s" % (
        "dt", "seed", "apogee", "drift", "SUS v", "SUS th", "BOO v", "BOO th"))
    for dt in TIMESTEPS:
        for seed in SEEDS:
            p = dict(p0); p["timestep_s"] = dt
            try:
                m = S.run_sim(S.generate_ork(p), seed=seed)
            except Exception as e:
                print("%-7.3f %-6d | ERROR %s" % (dt, seed, str(e)[:80]))
                table["%s|%s" % (dt, seed)] = {"error": str(e)}
                continue
            L = {l["branch_name"][:4]: l for l in m.get("stage_landings", [])}
            s, b = L.get("Sust"), L.get("Boos")
            drift = (m["apogee_east_m"] ** 2 + m["apogee_north_m"] ** 2) ** 0.5
            print("%-7.3f %-6d | %9.2f %9.2f | %8.3f %+8.0f | %8.3f %+8.0f" % (
                dt, seed, m["apogee_m"], drift,
                s["total_speed"] if s else -1, s["orientation_theta_deg"] if s else 0,
                b["total_speed"] if b else -1, b["orientation_theta_deg"] if b else 0))
            table["%s|%s" % (dt, seed)] = {
                "apogee_m": m["apogee_m"], "drift_m": drift, "mach": m["mach"],
                "sustainer_v": s["total_speed"] if s else None,
                "sustainer_theta": s["orientation_theta_deg"] if s else None,
                "booster_v": b["total_speed"] if b else None,
                "booster_theta": b["orientation_theta_deg"] if b else None,
                "score": S.score_official(m, p)["raw_score"],
            }
    ok = [v for v in table.values() if "error" not in v
          and (v.get("sustainer_v") or 99) < 5 and (v.get("booster_v") or 99) < 5]
    print("\n%d of %d cells land BOTH stages under 5 m/s" % (len(ok), len(table)))
    if out_path:
        json.dump(table, open(out_path, "w"), indent=1)
        print("saved", out_path)


if __name__ == "__main__":
    main()
