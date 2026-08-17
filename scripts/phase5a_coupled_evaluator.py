#!/usr/bin/env python3
"""Coupled two-branch candidate evaluator (mission section 5), Stage 1 slice.

Evaluates a sustainer mutation as part of the COMPLETE two-stage vehicle,
with the booster phenotype frozen exactly as recovered
(artifacts/autoevo/historical-3p5135-candidate.json, s1_* fields -- section 2).

Stage 1 scope only (section 9): physical compiler -> full-stack unpowered
ascent -> genuine separation before apogee -> phase-resolved ascent legality
-> unpowered booster and sustainer branch descent metrics. Motor screening,
powered validation, and booster delay recalibration are Stage 2-4 and are
NOT run here (section 9 explicitly scopes Stage 1 to free-descent).
"""
import json
import math
import os
import sys

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, validate_candidate_geometry, run_sim,
    MIN_STATIC_MARGIN, MAX_MACH,
)

ARTIFACTS = "artifacts/autoevo/phase5a"
os.makedirs(ARTIFACTS, exist_ok=True)

with open("artifacts/autoevo/historical-3p5135-candidate.json", encoding="utf-8") as f:
    _CAND = json.load(f)
_REF = _CAND["complete_parameters_powered_rerun"]

# Frozen booster phenotype (section 2). Also freezes shared environment
# (wind, launch geometry) so only sustainer (s0_*/nose_*) fields vary.
FROZEN_KEYS = [
    "main_cluster_count", "launch_angle_deg", "launch_azimuth", "wind_levels",
    "s1_aft_ballast_attachment", "s1_aft_ballast_kg", "s1_aft_ballast_pos_m",
    "s1_aft_ballast_rod_radius_m", "s1_body_len", "s1_body_rad",
    "s1_fin_count", "s1_fin_height", "s1_fin_material", "s1_fin_root",
    "s1_fin_sweep", "s1_fin_thickness_m", "s1_grid_fin_count",
    "s1_grid_fin_height", "s1_grid_fin_material", "s1_grid_fin_position_m",
    "s1_grid_fin_root", "s1_grid_fin_sweep", "s1_grid_fin_thickness_m",
    "s1_main", "s1_mid_ballast_kg", "s1_retro", "s1_separation_delay",
]
FROZEN_BOOSTER_PARAMS = {k: _REF[k] for k in FROZEN_KEYS}

# Reference sustainer geometry (Family S0-A local-refinement baseline).
REFERENCE_SUSTAINER_PARAMS = {
    k: v for k, v in _REF.items()
    if k not in FROZEN_KEYS and k not in ("s1_retro_delay", "s0_retro_delay")
}

STAGE1_MIN_MARGIN = 1.5
STAGE1_PREFERRED_MARGIN = 1.55


def build_params(sustainer_overrides):
    p = dict(FROZEN_BOOSTER_PARAMS)
    p.update(REFERENCE_SUSTAINER_PARAMS)
    p.update(sustainer_overrides)
    # Both retros disabled for Stage 1 (unpowered free-descent screening).
    p["s1_retro_delay"] = 200.0
    p["s0_retro_delay"] = 200.0
    return p


def evaluate_stage1(sustainer_overrides, label):
    params = build_params(sustainer_overrides)
    violations = validate_candidate_geometry(params)
    if violations:
        return {"label": label, "status": "REJECTED_GEOMETRY", "violations": violations}

    ork_xml = generate_ork(params)
    m = run_sim(ork_xml, seed=SIM_SEED)

    if "ABORTED" in m.get("status", "").upper():
        return {"label": label, "status": "REJECTED_SIM_ABORTED", "detail": m.get("status")}

    mach = m.get("mach", 999.0)
    segments = m.get("ascent_stability_segments", [])
    full_stack_margin = next(
        (s["min_calibers"] for s in segments if s.get("segment") == "full_stack"), None
    )
    sustainer_margin = next(
        (s["min_calibers"] for s in segments if s.get("segment") == "sustainer"), None
    )
    min_margin = min([x for x in (full_stack_margin, sustainer_margin) if x is not None], default=None)

    event_times = m.get("event_times", {})
    separations = event_times.get("STAGE_SEPARATION", [])
    apogees = event_times.get("APOGEE", [])
    separation_before_apogee = bool(separations and apogees and min(separations) < min(apogees))

    ascent_legal = (
        mach < MAX_MACH
        and min_margin is not None and min_margin >= STAGE1_MIN_MARGIN
        and separation_before_apogee
    )

    result = {
        "label": label,
        "sustainer_overrides": sustainer_overrides,
        "mach": mach,
        "full_stack_min_margin_cal": full_stack_margin,
        "sustainer_min_margin_cal": sustainer_margin,
        "separation_before_apogee": separation_before_apogee,
        "separation_time_s": min(separations) if separations else None,
        "first_apogee_time_s": min(apogees) if apogees else None,
        "ascent_legal": ascent_legal,
        "preferred_margin": (min_margin is not None and min_margin >= STAGE1_PREFERRED_MARGIN),
    }
    if not ascent_legal:
        result["status"] = "REJECTED_ASCENT_ILLEGAL"
        return result

    # Unpowered free-descent metrics for both branches, straight from run_sim's
    # own stage_landings / descent_alignment_diagnostics -- no re-simulation.
    stage_landings = {int(s["branch"]): s for s in m.get("stage_landings", [])}
    alignment = {
        int(d["branch"]): d for d in m.get("descent_alignment_diagnostics", [])
    }
    booster_landing = next((s for k, s in stage_landings.items()
                             if s.get("stage_key") == "s1"), None)
    sustainer_landing = next((s for k, s in stage_landings.items()
                               if s.get("stage_key") == "s0"), None)
    booster_align = next((a for k, a in alignment.items()
                           if a.get("stage_key") == "s1"), None)
    sustainer_align = next((a for k, a in alignment.items()
                             if a.get("stage_key") == "s0"), None)

    def _touchdown_total(landing):
        if not landing:
            return None
        return math.sqrt(float(landing["vz_ms"]) ** 2 + float(landing["vxy_ms"]) ** 2)

    result.update({
        "booster_unpowered_touchdown_mps": _touchdown_total(booster_landing),
        "sustainer_unpowered_touchdown_mps": _touchdown_total(sustainer_landing),
        "booster_best_alignment_q": booster_align.get("best_alignment_q") if booster_align else None,
        "sustainer_best_alignment_q": sustainer_align.get("best_alignment_q") if sustainer_align else None,
        "sustainer_alignment_sample_count": sustainer_align.get("sample_count") if sustainer_align else None,
    })
    result["status"] = "ASCENT_LEGAL_STAGE1"
    return result


if __name__ == "__main__":
    init_or()
    families = json.loads(sys.argv[1]) if len(sys.argv) > 1 else [{}]
    results = []
    for i, overrides in enumerate(families):
        label = overrides.pop("_label", f"cand_{i}")
        print(f"Evaluating {label} ...", file=sys.stderr)
        r = evaluate_stage1(overrides, label)
        results.append(r)
        print(f"  status={r['status']}", file=sys.stderr)
    with open(os.path.join(ARTIFACTS, "sustainer-family-results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=str)
    print(json.dumps([{"label": r["label"], "status": r["status"]} for r in results], indent=2))
