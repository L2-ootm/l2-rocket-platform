"""Campaign M phase 7 -- turn a finalized m5_*.json into a submission params file.

Takes Candidate K's parameter set (geometry, materials, motors, launch site,
wind, livery) and overrides ONLY the three flight-timing values Campaign M
retuned: s1_separation_delay, s0_retro_delay, s1_retro_delay.

Nothing structural changes, so every geometry / material / physical-legality
gate that Candidate K already passes still holds by construction.  The
celestial-datum v7 livery block and its decal declarations are carried across
verbatim.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/campaign_m_build_candidate.py \
      OSIFOG/experiments-2026-07-26/m5_sep300.json candidate_M_maxscore
"""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = sys.argv[1]
NAME = sys.argv[2]
DEST_DIR = sys.argv[3] if len(sys.argv) > 3 else "designs/osifog_visuals"

K_PATH = "designs/osifog_visuals/candidate_K_celestial_datum_v7.json"
K = json.load(open(K_PATH, encoding="utf-8"))
res = json.load(open(SRC, encoding="utf-8"))

if res.get("abort"):
    raise SystemExit("source run aborted on %s (floor %.3f)"
                     % (res["abort"], res.get("floor", float("nan"))))

params = dict(K)
params["s1_separation_delay"] = res["sep"]
params["s0_retro_delay"] = res["s0_delay"]
params["s1_retro_delay"] = res["s1_delay"]

out_path = os.path.join(DEST_DIR, NAME + ".json")
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(params, fh, indent=1)
    fh.write("\n")

v = res["verified"]
print("wrote %s" % out_path)
print("  separation      %.4f s   (Candidate K: %.4f s)"
      % (params["s1_separation_delay"], K["s1_separation_delay"]))
print("  s0 retro delay  %.7f s  (K: %.7f)  window %.1f ms"
      % (params["s0_retro_delay"], K["s0_retro_delay"], res["s0_window_ms"]))
print("  s1 retro delay  %.7f s  (K: %.7f)  window %.1f ms"
      % (params["s1_retro_delay"], K["s1_retro_delay"], res["s1_window_ms"]))
print("  seed-16000 score %.1f   legal=%s   mean touchdown speed %.3f m/s"
      % (v["raw_score"], v["is_legal"], v["mean_V"]))
print("  decomposition: alt=%.0f apoH=%.0f pos=%.0f vel=%.0f prop=%.0f"
      % (v["apogee_alt_pen"], v["apogee_horiz_pen"], v["touch_pos_pen"],
         v["touch_vel_pen"], v["prop_pen"]))
changed = sorted(k for k in set(params) | set(K) if params.get(k) != K.get(k))
print("  keys changed vs Candidate K: %s" % ", ".join(changed))
