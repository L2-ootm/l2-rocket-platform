#!/usr/bin/env python3
"""Build a normalized replay corpus from existing campaign artifacts.

Mission section 5 asks for a `replay-corpus.parquet` with a rich per-
candidate schema (candidate_id, parent_id, generation, geometry_digest,
q_metrics, ...). That schema was never populated by any prior phase: phases
1-3a wrote ad hoc, phase-specific summary JSON (no candidate-level rows, no
parquet dependency in this repo), and Phase 4A's own driver
(`scripts/phase4a_direct_search.py`) discarded every candidate's raw
parameters after scoring it -- only small aggregate summaries survive on
disk. Fabricating the full schema retroactively from data that was never
recorded would misrepresent what is actually known.

This script instead does the honest, bounded thing: it normalizes every
existing JSON artifact under `artifacts/phase*/` into one row per file (phase,
source path, best-effort extracted outcome/metrics, raw content preserved for
anything not extracted), and appends fully-schemed rows for the new data this
session actually generated at candidate granularity (flip diagnosis + strake
batch). JSON is used instead of parquet: the repo has no pandas/pyarrow
dependency, corpus size is in the hundreds of rows, and a new dependency for
that scale is not justified (anti-bloat).
"""
import glob
import json
import os

ARTIFACTS_ROOT = "artifacts"
OUT_DIR = "artifacts/autoevo"
os.makedirs(OUT_DIR, exist_ok=True)


def _infer_outcome(data):
    if isinstance(data, dict):
        for key in ("legal_branch_found", "legal_branch", "legal"):
            if key in data:
                return "legal" if data[key] else "illegal_or_incomplete"
        status = data.get("status") or data.get("dominant_failure")
        if status:
            return str(status)
    return None


def load_historical_rows():
    rows = []
    for path in sorted(glob.glob(os.path.join(ARTIFACTS_ROOT, "phase*", "*.json"))):
        phase = os.path.basename(os.path.dirname(path))
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            rows.append({
                "phase": phase, "source_file": path, "row_type": "historical_summary",
                "load_error": str(exc),
            })
            continue
        rows.append({
            "phase": phase,
            "source_file": path,
            "row_type": "historical_summary",
            "candidate_id": None,
            "generation": None,
            "engine_version": "pre-autoevo",
            "authority_result": _infer_outcome(data),
            "raw": data,
        })
    return rows


def load_session_rows():
    rows = []
    flip_path = os.path.join(OUT_DIR, "flip-diagnosis-summary.json")
    if os.path.exists(flip_path):
        with open(flip_path, "r", encoding="utf-8") as f:
            flip_results = json.load(f)
        for r in flip_results:
            rows.append({
                "phase": "autoevo-2026-07-20",
                "source_file": flip_path,
                "row_type": "flip_diagnosis",
                "candidate_id": r.get("label"),
                "family": "E8",
                "engine_version": "autoevo-1",
                "physical_valid": True,
                "q_metrics": {
                    "q_mean_pre_ignition": r.get("q_mean_pre_ignition"),
                    "q_mean_post_ignition": r.get("q_mean_post_ignition"),
                    "q_flip_delay_after_ignition_s": r.get("q_flip_delay_after_ignition_s"),
                    "burn_direction_cosine_mean": r.get("burn_direction_cosine_mean"),
                },
                "moment_metrics": {"thrust_line_moment": r.get("thrust_line_moment")},
                "authority_result": "diagnostic_only",
                "failure_class": "physics_limited_candidate" if r.get("q_flip_delay_after_ignition_s") else None,
                "compute_cost": {"openrocket_runs": 1},
            })

    strake_path = os.path.join(OUT_DIR, "strake-batch-results.json")
    if os.path.exists(strake_path):
        with open(strake_path, "r", encoding="utf-8") as f:
            strake_results = json.load(f)
        for r in strake_results:
            legal = any(pp.get("legal_branch") for pp in r.get("powered_probes", []))
            rows.append({
                "phase": "autoevo-2026-07-20",
                "source_file": strake_path,
                "row_type": "strake_batch",
                "candidate_id": r.get("label"),
                "family": "C" if "ST" in r.get("label", "") else "E8",
                "engine_version": "autoevo-1",
                "physical_valid": "error" not in r,
                "ascent_margin_cal": r.get("min_margin_cal"),
                "max_mach": r.get("mach"),
                "staging_legal": r.get("staging_legal"),
                "descent_admitted": r.get("admitted"),
                "descent_admission_reasons": r.get("admission_reasons"),
                "q_metrics": r.get("descent_metrics"),
                "powered_probes": r.get("powered_probes"),
                "authority_result": "legal" if legal else (
                    "descent_rejected" if r.get("admitted") is False else
                    "powered_early_stopped" if r.get("powered_probes") else "ascent_illegal"
                ),
                "failure_class": None if legal else (
                    "ascent_illegal_margin" if r.get("min_margin_cal") is not None and r.get("min_margin_cal") < 1.5
                    else "descent_rejected" if r.get("admitted") is False
                    else "powered_flip_early_stopped"
                ),
                "compute_cost": {
                    "openrocket_runs": 1 + len(r.get("powered_probes", [])),
                },
            })
    return rows


def main():
    historical = load_historical_rows()
    session = load_session_rows()
    corpus = historical + session

    with open(os.path.join(OUT_DIR, "replay-corpus.json"), "w") as f:
        json.dump(corpus, f, indent=2, sort_keys=True, default=str)

    by_phase = {}
    by_outcome = {}
    for row in corpus:
        by_phase[row["phase"]] = by_phase.get(row["phase"], 0) + 1
        outcome = row.get("authority_result") or "unclassified"
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

    summary = {
        "total_rows": len(corpus),
        "historical_rows": len(historical),
        "session_rows": len(session),
        "rows_by_phase": by_phase,
        "rows_by_outcome": by_outcome,
        "known_gaps": [
            "Phases 1-4A never persisted per-candidate raw parameters or "
            "geometry digests -- only aggregate phase summaries survive. "
            "candidate_id/parent_id/generation/geometry_digest are null for "
            "every historical row; they cannot be reconstructed after the "
            "fact without rerunning the original (undocumented) parameter "
            "sweeps.",
            "No engine-version manifest existed before this session "
            "(engine_version='pre-autoevo' is a placeholder marking "
            "everything before 2026-07-20's autoevo work, not a real "
            "version identifier).",
            "Format is JSON, not parquet: no pandas/pyarrow dependency "
            "exists in this repo and corpus size (hundreds of rows) does "
            "not justify adding one.",
        ],
    }
    with open(os.path.join(OUT_DIR, "replay-corpus-summary.json"), "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
