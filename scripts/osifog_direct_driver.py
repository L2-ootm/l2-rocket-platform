"""Direct real-OpenRocket design-search driver for OSIFOG Level 3.

READ `OSIFOG/ADVISOR_PLAYBOOK.md` FIRST — it holds the verified physics laws,
the architecture plan of record (PHOENIX), and the exact next steps this
driver is meant to execute. This file is the modular batch runner around
`osifog_sweep.generate_ork` (parameterized 2-stage falcon .ork generator) and
`osifog_sweep.run_sim` (the real-OR oracle: per-stage GROUND_HIT landings,
apogee E/N, margins, events, official score).

Design choices are DATA, not code: every batch is a JSON file mapping
tag -> parameter overrides applied on top of BASE below. Special keys:
  "_seeds": [ints]            — seeds to run each config under (default [16000])
  "_base": {overrides}        — shared overrides applied before each tagged
                                config (keeps fine grids compact)
  "_s0_flaps_on_nose_m": x    — post-edit: relocate 'Sustainer Forward Grid
                                Fins' onto the nosecone at x m from the tip
                                (OR 24.12 supports fin sets on nose cones)
  "s0_main": null             — retro-only sustainer (no ascent cluster);
                                requires s0_aft_ballast_attachment
                                "airframe_bonded" + exact bridging rod radius,
                                see playbook §2.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/osifog_direct_driver.py batch.json
Output: one compact row per run + <batch>_out.json with full metrics.
Run 2-3 instances in parallel (one JVM each) for throughput.
"""
import sys, os, json, copy
import re as _re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import (init_or, run_sim, generate_ork, parse_wind_csv,
                          score_official)

WIND = parse_wind_csv("OSIFOG/OpenWind_File.csv")

# Base family: modified-839k hot-staged ascent geometry. Override freely.
BASE = {
    "s0_main": 37, "s0_retro": 19,
    "s1_main": 18, "s1_retro": 19,
    "main_cluster_count": 3,
    "s0_body_len": 0.70, "s0_body_rad": 0.074,
    "s1_body_len": 0.75, "s1_body_rad": 0.074,
    "nose_length_m": 0.74,
    "s0_retro_delay": 1100.0,
    "s1_retro_delay": 1100.0,
    "s0_fin_count": 4, "s0_fin_sweep": 10.0, "s0_fin_root": 0.20, "s0_fin_height": 0.25,
    "s1_fin_count": 4, "s1_fin_sweep": 10.0, "s1_fin_root": 0.20, "s1_fin_height": 0.25,
    "nose_mass_kg": 1.70,
    "s0_mid_ballast_kg": 0.0, "s0_aft_ballast_kg": 0.0,
    "s1_mid_ballast_kg": 0.0, "s1_aft_ballast_kg": 0.55,
    "s1_grid_fin_count": 4, "s1_grid_fin_root": 0.10, "s1_grid_fin_height": 0.08,
    "s0_grid_fin_count": 0,
    "launch_azimuth": 270.0, "launch_angle_deg": 0.0,
    "s1_separation_delay": 0.0,
    "wind_levels": WIND,
}


def move_flaps_to_nose(xml, offset_from_nose_tip_m):
    """Relocate 'Sustainer Forward Grid Fins' into the nosecone at the given
    axial offset from the nose tip (multiplies the flap moment arm)."""
    m = _re.search(
        r"(<freeformfinset>\s*<name>Sustainer Forward Grid Fins</name>.*?</freeformfinset>)",
        xml, _re.S)
    if not m:
        raise ValueError("sustainer grid fin block not found")
    block = m.group(1)
    xml = xml.replace(block, "")
    block = _re.sub(
        r"<position type=\"top\">[0-9.eE+-]+</position>",
        f'<position type="top">{offset_from_nose_tip_m:.6f}</position>', block)
    block = _re.sub(
        r"<axialoffset method=\"top\">[0-9.eE+-]+</axialoffset>",
        f'<axialoffset method="top">{offset_from_nose_tip_m:.6f}</axialoffset>', block)
    nose = _re.search(r"(<nosecone>.*?)(</subcomponents>\s*</nosecone>)", xml, _re.S)
    if not nose:
        raise ValueError("nosecone subcomponents not found")
    return xml.replace(nose.group(0), nose.group(1) + block + "\n" + nose.group(2))


def summarize(tag, m, seed, parameters):
    segs = {s["segment"]: round(s["min_calibers"], 2)
            for s in m.get("ascent_stability_segments", [])}
    lands = m.get("stage_landings", [])
    lstr = " | ".join(
        "%s t=%.1f v=%.1f th=%+.0f E=%.0f N=%.0f" % (
            l["branch_name"][:4], l["time_s"], l["total_speed"],
            l["orientation_theta_deg"], l["east_m"], l["north_m"])
        for l in lands)
    score = ""
    if len(lands) >= 2:
        try:
            s = score_official(m, parameters)
            # The direct campaign prints the raw formula value; packaging adds
            # XML-only checks that are unavailable from metrics alone.
            score = " SCORE=%.0f" % s["raw_score"]
        except Exception:
            pass
    print("%-26s s%-6d apo=%7.1f mach=%.2f E/N=%6.1f/%6.1f prop=%.2f m=%s  %s%s" % (
        tag, seed, m.get("apogee_m", 0), m.get("mach", 0),
        m.get("apogee_east_m", 0), m.get("apogee_north_m", 0),
        m.get("m_prop_kg_actual", 0), segs, lstr or "NO-LAND", score), flush=True)


def main():
    batch_path = sys.argv[1]
    batch = json.load(open(batch_path))
    seeds = batch.pop("_seeds", [16000])
    common_overrides = batch.pop("_base", {})
    init_or()
    out = {}
    for tag, overrides in batch.items():
        p = copy.deepcopy(BASE)
        p.update(common_overrides)
        p.update(overrides)
        nose_flap_offset = p.pop("_s0_flaps_on_nose_m", None)
        try:
            xml = generate_ork(p)
            if nose_flap_offset is not None:
                xml = move_flaps_to_nose(xml, nose_flap_offset)
        except Exception as e:
            print("%-26s GEN-FAIL %s" % (tag, e), flush=True)
            out[tag] = {"gen_error": str(e)}
            continue
        for seed in seeds:
            try:
                m = run_sim(xml, seed=seed)
            except Exception as e:
                print("%-26s s%d SIM-FAIL %s" % (tag, seed, e), flush=True)
                out[f"{tag}@{seed}"] = {"sim_error": str(e)}
                continue
            summarize(tag, m, seed, p)
            slim = {k: v for k, v in m.items()
                    if k not in ("descent_alignment_diagnostics", "retro_burn_diagnostics")}
            slim["_align"] = [
                {"branch": d.get("branch"), "best_q": d.get("best_alignment_q"),
                 "best_t": (d.get("best_sample") or {}).get("time_s"),
                 "best_alt": (d.get("best_sample") or {}).get("altitude_m")}
                for d in m.get("descent_alignment_diagnostics", [])]
            out[f"{tag}@{seed}"] = slim
    dst = batch_path.replace(".json", "_out.json")
    json.dump(out, open(dst, "w"), indent=1, default=str)
    print("saved", dst, flush=True)


if __name__ == "__main__":
    main()
