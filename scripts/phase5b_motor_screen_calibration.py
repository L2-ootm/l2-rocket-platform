#!/usr/bin/env python3
"""Phase 5B Stage 2 screen calibration (mission section 4).

Before the analytic motor-window screen (scripts/phase5b_motor_window_screen.py)
is trusted to filter the sustainer population, it must replay against known
ground truth:

  POSITIVE CONTROL: the recovered eight-forward-fin booster / H180W branch
  (contact-relative ignition near the recovered legal region) must be
  admitted (PROMISING or MARGINAL, never REJECT_*) at or near its known real
  legal ignition time.

  NEGATIVE CONTROLS: E8 aft-fin H180W/J350W-family early powered failures
  (from artifacts/autoevo/phase4b-focused-powered-experiment.json -- real
  OpenRocket powered reruns, opposing_impulse_fraction 0.002-0.194, all
  touchdown 58-61 m/s, never legal) must be rejected at the SAME ignition
  times the ground truth was measured at, and a motor whose burn cannot fit
  in the available window must be structurally rejected.

Recall is prioritized over rejection count (section 4): a false rejection on
the positive control is far worse than a false admission on a negative
control, because it can silently delete the only legal basin found so far.
"""
import json
import math
import os
import sys

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from osifog_sweep import init_or
from scripts.phase5a_coupled_evaluator import FROZEN_BOOSTER_PARAMS, REFERENCE_SUSTAINER_PARAMS
from scripts.phase5b_motor_window_screen import (
    extract_branch_trace, load_motor_curve, evaluate_window, IMPULSE_FRACTION_THRESHOLD,
)
from scripts.flip_diagnosis import E8_8
from scripts.tail_mass_matrix import MASS_GAP

ARTIFACTS = "artifacts/autoevo/phase5b"
os.makedirs(ARTIFACTS, exist_ok=True)

BOOSTER_BRANCH = 1
REJECT_LABELS = {
    "REJECT_LOW_OPPOSITION", "REJECT_ADVERSE_DOMINANT", "REJECT_NO_THRUST_IN_WINDOW",
    "REJECT_WINDOW_BEFORE_TRACE", "REJECT_IGNITION_OUTSIDE_TRACE",
    "REJECT_BURN_LONGER_THAN_AVAILABLE_WINDOW",
}
ADMIT_LABELS = {"PROMISING", "MARGINAL"}

# Ground truth reference: real OpenRocket powered reruns from
# phase4b-focused-powered-experiment.json (E8_baseline / best_controlled_tail_mass_CaseB).
NEGATIVE_CONTROL_RUNS = [
    {"topology": "E8_baseline", "motor": "H73J", "delay_s": 9.295, "ground_truth_opp_frac": 0.1562},
    {"topology": "E8_baseline", "motor": "H73J", "delay_s": 9.795, "ground_truth_opp_frac": 0.0842},
    {"topology": "E8_baseline", "motor": "H73J", "delay_s": 10.295, "ground_truth_opp_frac": 0.0279},
    {"topology": "E8_baseline", "motor": "H180W", "delay_s": 9.297, "ground_truth_opp_frac": 0.1038},
    {"topology": "E8_baseline", "motor": "H180W", "delay_s": 9.797, "ground_truth_opp_frac": 0.0428},
    {"topology": "E8_baseline", "motor": "H180W", "delay_s": 10.297, "ground_truth_opp_frac": 0.0022},
    {"topology": "best_controlled_tail_mass_CaseB", "motor": "H73J", "delay_s": 9.276, "ground_truth_opp_frac": 0.1941},
    {"topology": "best_controlled_tail_mass_CaseB", "motor": "H73J", "delay_s": 9.776, "ground_truth_opp_frac": 0.1283},
    {"topology": "best_controlled_tail_mass_CaseB", "motor": "H180W", "delay_s": 9.263, "ground_truth_opp_frac": 0.1180},
    {"topology": "best_controlled_tail_mass_CaseB", "motor": "H180W", "delay_s": 9.763, "ground_truth_opp_frac": 0.0605},
    {"topology": "best_controlled_tail_mass_CaseB", "motor": "H180W", "delay_s": 10.263, "ground_truth_opp_frac": 0.0125},
]
TOPOLOGY_PARAMS = {
    "E8_baseline": {"s1_aft_ballast_kg": 0.0},
    "best_controlled_tail_mass_CaseB": {"s1_aft_ballast_kg": MASS_GAP},
}

# Known real legal region (phase5a booster-delay-basin.json): 29.860-29.865s
# band plus an isolated legal point at 29.8665s (0.5ms grid).
KNOWN_LEGAL_DELAY_BAND = (29.860, 29.865)
KNOWN_LEGAL_ISOLATED_POINT = 29.8665
POSITIVE_CONTROL_TOLERANCE_S = 0.01


def positive_control():
    """Recall test: does the screen admit the booster/H180W branch near its
    known legal region?"""
    params = dict(FROZEN_BOOSTER_PARAMS)
    params.update(REFERENCE_SUSTAINER_PARAMS)
    trace_data = extract_branch_trace(params, branch=BOOSTER_BRANCH)
    curve = load_motor_curve("H180W")
    contact_t = trace_data["contact_t"]

    # Evaluate a dense local grid across and around the known legal band,
    # at 1ms resolution -- matches the basin's own characterization grid.
    lo, hi = KNOWN_LEGAL_DELAY_BAND
    grid = []
    t = lo - 0.005
    while t <= hi + 0.005 + 1e-9:
        grid.append(round(t, 4))
        t += 0.001
    grid.append(KNOWN_LEGAL_ISOLATED_POINT)

    windows = [evaluate_window(trace_data, curve, ig_t) for ig_t in grid]
    admitted = [w for w in windows if w.get("classification") in ADMIT_LABELS]
    rejected = [w for w in windows if w.get("classification") not in ADMIT_LABELS]

    return {
        "trace_apex_t": trace_data["apex_t"],
        "trace_unpowered_contact_t": contact_t,
        "grid_evaluated_s": grid,
        "n_admitted": len(admitted),
        "n_rejected": len(rejected),
        "recall_pass": len(admitted) > 0,
        "windows": windows,
    }


def negative_controls():
    results = []
    trace_cache = {}
    for run in NEGATIVE_CONTROL_RUNS:
        topo = run["topology"]
        if topo not in trace_cache:
            params = dict(E8_8)
            params.update(TOPOLOGY_PARAMS[topo])
            params["s1_retro_delay"] = 200.0
            trace_cache[topo] = extract_branch_trace(params, branch=BOOSTER_BRANCH)
        trace_data = trace_cache[topo]
        curve = load_motor_curve(run["motor"])
        w = evaluate_window(trace_data, curve, run["delay_s"])
        w["topology"] = topo
        w["ground_truth_opposing_impulse_fraction"] = run["ground_truth_opp_frac"]
        w["ground_truth_below_threshold"] = run["ground_truth_opp_frac"] < IMPULSE_FRACTION_THRESHOLD
        w["screen_rejected"] = w.get("classification") in REJECT_LABELS
        w["agrees_with_ground_truth"] = w["screen_rejected"] == w["ground_truth_below_threshold"]
        results.append(w)
    return results


def motor_cannot_fit_window():
    """Structural check: a motor whose burn duration exceeds the available
    contact-relative window must be rejected by construction."""
    params = dict(FROZEN_BOOSTER_PARAMS)
    params.update(REFERENCE_SUSTAINER_PARAMS)
    trace_data = extract_branch_trace(params, branch=BOOSTER_BRANCH)
    long_burn_curve = load_motor_curve("J420R")
    # Force an artificially short available window by evaluating at a time
    # so close to contact that even a mid-length motor cannot fit -- this is
    # a structural/robustness check on evaluate_window, not a real candidate.
    contact_t = trace_data["contact_t"]
    ignition_t = contact_t - 0.05  # 50ms before contact; J420R burns ~1s+
    w = evaluate_window(trace_data, long_burn_curve, ignition_t)
    return {
        "motor": "J420R",
        "burn_duration_s": long_burn_curve["burn_duration_s"],
        "ignition_time_s": ignition_t,
        "available_window_s": contact_t - ignition_t,
        "result": w,
        "correctly_flagged": w.get("classification") not in ADMIT_LABELS,
    }


def main():
    init_or()
    print("Running positive control (booster/H180W recall)...", file=sys.stderr)
    pos = positive_control()
    print(f"  n_admitted={pos['n_admitted']} n_rejected={pos['n_rejected']} recall_pass={pos['recall_pass']}",
          file=sys.stderr)

    print("Running negative controls (E8/CaseB known powered failures)...", file=sys.stderr)
    neg = negative_controls()
    n_agree = sum(1 for r in neg if r["agrees_with_ground_truth"])
    false_admission = [r for r in neg if r["ground_truth_below_threshold"] and not r["screen_rejected"]]
    false_rejection_positive_side = [
        r for r in neg if not r["ground_truth_below_threshold"] and r["screen_rejected"]
    ]
    print(f"  {n_agree}/{len(neg)} agree with ground truth", file=sys.stderr)

    print("Running motor-cannot-fit-window structural check...", file=sys.stderr)
    fit_check = motor_cannot_fit_window()
    print(f"  correctly_flagged={fit_check['correctly_flagged']}", file=sys.stderr)

    out = {
        "positive_control_booster_h180w": pos,
        "positive_control_recall": pos["recall_pass"],
        "negative_controls": neg,
        "negative_control_rejection": n_agree / len(neg) if neg else None,
        "false_admission_count": len(false_admission),
        "false_admission_detail": false_admission,
        "false_rejection_count_on_negative_set": len(false_rejection_positive_side),
        "motor_cannot_fit_window_check": fit_check,
        "overall_calibration_pass": (
            pos["recall_pass"] and len(false_admission) == 0 and fit_check["correctly_flagged"]
        ),
    }
    out_path = os.path.join(ARTIFACTS, "motor-screen-calibration.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(f"\nWrote {out_path}", file=sys.stderr)
    print(json.dumps({
        "positive_control_recall": out["positive_control_recall"],
        "negative_control_rejection": out["negative_control_rejection"],
        "false_admission_count": out["false_admission_count"],
        "overall_calibration_pass": out["overall_calibration_pass"],
    }, indent=2))


if __name__ == "__main__":
    main()
