"""Bounded Gate 4 search for one powered, legal sustainer landing branch.

This is deliberately not a full-vehicle optimizer.  It uses complete
OpenRocket flights to inherit a legal separation state, filters free-fall
branches with the motor-aware screen, and validates only a small diverse set
of powered sustainer delays.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import osifog_engine_search as search
import osifog_sweep as sweep


SEED = 16000
FAMILY_LIMIT = 4
STRUCTURE_LIMIT = 20
MOTOR_WINDOW_LIMIT = 5
POWERED_FINALIST_LIMIT = 3


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _seed_parameters() -> list[dict]:
    records = []
    for path in (
        Path("designs/osifog_autonomous_hour/sustainer-q-probe.json"),
        Path("designs/osifog_autonomous_hour/sustainer-structure-probe.json"),
    ):
        for record in json.loads(path.read_text(encoding="utf-8")):
            parameters = record.get("parameters")
            if parameters:
                records.append(copy.deepcopy(parameters))
    # Preserve diversity rather than taking adjacent rankings from one probe.
    indices = (0, 1, 4, 8, 12, 20, 30, 40)
    return [records[index % len(records)] for index in indices]


def _family_candidate(
    family: str, seed: dict, index: int, rng: random.Random
) -> dict:
    p = copy.deepcopy(seed)
    p["s0_retro_delay"] = 200.0
    p["s1_retro_delay"] = 200.0
    p["s1_separation_delay"] = min(
        0.75, max(0.0, float(p.get("s1_separation_delay", 0.0)))
    )
    p["s0_grid_fin_material"] = rng.choice(
        ("legal_balsa", "cardboard", "fiberglass")
    )
    p["s0_grid_fin_thickness_m"] = rng.choice((0.001, 0.002, 0.003))

    if family == "forward_area":
        p.update(
            s0_grid_fin_count=rng.choice((3, 4, 6, 8)),
            s0_grid_fin_height=rng.uniform(0.08, 0.30),
            s0_grid_fin_root=rng.uniform(0.06, 0.20),
            s0_grid_fin_sweep=rng.uniform(0.0, 35.0),
            s0_grid_fin_position_m=rng.uniform(0.005, 0.18),
            s0_fin_count=rng.choice((4, 6, 8)),
            s0_fin_height=rng.uniform(0.35, 0.85),
            s0_fin_root=rng.uniform(0.12, 0.24),
        )
    elif family == "aft_mass_forward_area":
        p.update(
            s0_grid_fin_count=rng.choice((4, 6, 8)),
            s0_grid_fin_height=rng.uniform(0.12, 0.32),
            s0_grid_fin_root=rng.uniform(0.08, 0.20),
            s0_grid_fin_sweep=rng.uniform(0.0, 25.0),
            s0_grid_fin_position_m=rng.uniform(0.005, 0.12),
            s0_aft_ballast_kg=rng.uniform(0.8, 2.8),
            s0_aft_ballast_pos_m=rng.uniform(
                0.65 * float(p["s0_body_len"]),
                0.88 * float(p["s0_body_len"]),
            ),
            s0_aft_ballast_attachment="central_bonded",
            s0_fin_count=rng.choice((4, 6)),
            s0_fin_height=rng.uniform(0.45, 0.90),
        )
    elif family == "long_body_forward_drag":
        body_length = rng.uniform(0.90, 1.35)
        p.update(
            s0_body_len=body_length,
            s0_grid_fin_count=rng.choice((4, 6, 8)),
            s0_grid_fin_height=rng.uniform(0.10, 0.28),
            s0_grid_fin_root=rng.uniform(0.08, 0.18),
            s0_grid_fin_sweep=rng.uniform(10.0, 45.0),
            s0_grid_fin_position_m=rng.uniform(0.005, 0.15),
            s0_mid_ballast_kg=rng.uniform(0.0, 0.8),
            s0_mid_ballast_pos_m=rng.uniform(0.35, 0.60) * body_length,
            s0_fin_count=rng.choice((4, 6)),
            s0_fin_height=rng.uniform(0.40, 0.80),
            s0_fin_root=rng.uniform(0.14, 0.24),
        )
    elif family == "split_aero_area":
        p.update(
            s0_grid_fin_count=rng.choice((4, 6, 8)),
            s0_grid_fin_height=rng.uniform(0.10, 0.26),
            s0_grid_fin_root=rng.uniform(0.10, 0.22),
            s0_grid_fin_sweep=rng.uniform(0.0, 30.0),
            s0_grid_fin_position_m=rng.uniform(0.005, 0.20),
            s0_fin_count=rng.choice((6, 8)),
            s0_fin_height=rng.uniform(0.55, 0.95),
            s0_fin_root=rng.uniform(0.16, 0.26),
            s0_fin_sweep=rng.uniform(0.0, 40.0),
            s0_mid_ballast_kg=rng.uniform(0.0, 1.0),
        )
    else:
        raise ValueError(f"unknown family {family}")

    # Explore the actual local retro pool without tying physics to a motor name.
    p["s0_retro"] = rng.choice((0, 1, 2, 3, 4, 5, 6, 7, 8))
    p["candidate_id"] = f"{family}-{index:03d}"
    return p


def _screen_opportunities(metrics: dict, parameters: dict) -> list[dict]:
    delays = search._delay_candidates(
        metrics, parameters, branch=0, limit=20
    )
    opportunities = [
        search._landing_opportunity(metrics, parameters, 0, delay)
        for delay in delays
    ]
    opportunities.sort(
        key=lambda item: (
            not bool(item.get("usable")),
            -float(item.get("fraction_burn_opposing_vertical_velocity", 0.0)),
            -float(item.get("available_vertical_delta_v_ms", 0.0)),
            float(item.get("predicted_touchdown_speed_ms", math.inf)),
            abs(float(item.get("predicted_burnout_to_impact_coast_s", math.inf))),
        )
    )
    return opportunities[:MOTOR_WINDOW_LIMIT]


def _trace_value(metrics: dict, branch: int, time_s: float, field: str):
    diagnostic = next(
        item
        for item in metrics.get("descent_alignment_diagnostics", [])
        if int(item.get("branch", -1)) == branch
    )
    trace = diagnostic.get("alignment_trace", [])
    if not trace:
        return None
    return search._interpolate_trace(trace, time_s, field)


def _powered_comparison(predicted: dict, authority: dict) -> dict:
    branch = int(predicted["branch"])
    requested_ignition = float(predicted["candidate_ignition_time_s"])
    events = authority["branch_event_times"][branch]
    impact = next(
        item for item in authority["stage_landings"]
        if int(item["branch"]) == branch
    )
    ignitions = [
        float(value)
        for value in events.get("IGNITION", [])
        if float(value) > requested_ignition - 0.05
        and float(value) < float(impact["time_s"])
    ]
    ignition = min(ignitions, key=lambda value: abs(value - requested_ignition))
    burnouts = [
        float(value)
        for value in events.get("BURNOUT", [])
        if ignition < float(value)
    ]
    burnout = min(burnouts) if burnouts else None
    touchdown_speed = float(impact["total_speed"])
    screen_positive = bool(predicted["usable"])
    authority_positive = touchdown_speed < 5.0
    classification = (
        "true_positive" if screen_positive and authority_positive
        else "false_positive" if screen_positive
        else "false_negative" if authority_positive
        else "true_negative"
    )
    return {
        "predicted": {
            "ignition_altitude_m": predicted.get("ignition_altitude_m"),
            "burnout_altitude_m": predicted.get("burnout_altitude_m"),
            "opposing_impulse_ns": predicted.get("opposing_impulse_ns"),
            "vertical_impulse_ns": predicted.get("vertical_braking_impulse_ns"),
            "touchdown_speed_ms": predicted.get("predicted_touchdown_speed_ms"),
        },
        "authority": {
            "ignition_time_s": ignition,
            "burnout_time_s": burnout,
            "ignition_altitude_m": _trace_value(
                authority, branch, ignition, "altitude_m"
            ),
            "burnout_altitude_m": (
                _trace_value(authority, branch, burnout, "altitude_m")
                if burnout is not None else None
            ),
            "touchdown_total_speed_ms": touchdown_speed,
            "touchdown_vertical_speed_ms": float(impact["vz_ms"]),
            "touchdown_horizontal_speed_ms": float(impact["vxy_ms"]),
            "contact_time_s": float(impact["time_s"]),
        },
        "delta": {
            "ignition_altitude_m": (
                _trace_value(authority, branch, ignition, "altitude_m")
                - float(predicted["ignition_altitude_m"])
            ),
            "burnout_altitude_m": (
                _trace_value(authority, branch, burnout, "altitude_m")
                - float(predicted["burnout_altitude_m"])
                if burnout is not None else None
            ),
            "touchdown_speed_ms": touchdown_speed
            - float(predicted.get("predicted_touchdown_speed_ms", 0.0)),
        },
        "classification": classification,
    }


def _powered_run(parameters: dict, delay_s: float) -> dict:
    trial = copy.deepcopy(parameters)
    trial["s0_retro_delay"] = round(float(delay_s), 6)
    trial["s1_retro_delay"] = 200.0
    metrics = search._run_authority(
        trial,
        "POWERED_STAGE_LANDING_VALIDATION",
        trial.get("candidate_id", "gate4-powered"),
    )
    landing = next(
        item for item in metrics.get("stage_landings", [])
        if item.get("stage_key") == "s0"
    )
    return {"delay_s": trial["s0_retro_delay"], "parameters": trial, "metrics": metrics, "landing": landing}


def _refine(parameters: dict, initial_runs: list[dict]) -> list[dict]:
    runs = list(initial_runs)
    best = min(runs, key=lambda item: float(item["landing"]["total_speed"]))
    if float(best["landing"]["total_speed"]) >= 15.0:
        return runs
    center = float(best["delay_s"])
    tested = {round(float(item["delay_s"]), 6) for item in runs}
    for offset in range(-5, 6):
        delay = round(center + 0.01 * offset, 6)
        if delay not in tested:
            runs.append(_powered_run(parameters, delay))
            tested.add(delay)
    best = min(runs, key=lambda item: float(item["landing"]["total_speed"]))
    neighbors = [
        item for item in runs
        if abs(float(item["delay_s"]) - float(best["delay_s"])) <= 0.02 + 1e-9
    ]
    if min(float(item["landing"]["total_speed"]) for item in neighbors) >= 8.0:
        return runs
    center = float(best["delay_s"])
    for offset in range(-5, 6):
        delay = round(center + 0.001 * offset, 6)
        if delay not in tested:
            runs.append(_powered_run(parameters, delay))
            tested.add(delay)
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(
            "designs/osifog_autonomous_hour/gate4-sustainer-search"
        ),
    )
    parser.add_argument("--structures-per-family", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.structures_per_family <= STRUCTURE_LIMIT:
        raise SystemExit("structures-per-family exceeds Gate 4 budget")

    output = args.out
    output.mkdir(parents=True, exist_ok=True)
    command = " ".join(shlex.quote(arg) for arg in sys.argv)
    families = (
        "forward_area",
        "aft_mass_forward_area",
        "long_body_forward_drag",
        "split_aero_area",
    )
    manifest = {
        "status": "RUNNING",
        "seed": SEED,
        "command": command,
        "families": list(families),
        "budgets": {
            "family_limit": FAMILY_LIMIT,
            "structures_per_family": args.structures_per_family,
            "motor_windows_per_structure": MOTOR_WINDOW_LIMIT,
            "powered_finalists_per_family": POWERED_FINALIST_LIMIT,
        },
        "authority": "OpenRocket 24.12",
        "screen_role": "rejection filter only",
    }
    _write_json(output / "search-manifest.json", manifest)

    sweep.init_or()
    seeds = _seed_parameters()
    screen_table = []
    powered_table = []
    best_legal = None
    for family_index, family in enumerate(families):
        rng = random.Random(SEED + family_index * 1000)
        family_survivors = []
        valid_count = 0
        attempts = 0
        while valid_count < args.structures_per_family and attempts < 100:
            attempts += 1
            p = _family_candidate(
                family, seeds[valid_count % len(seeds)], valid_count, rng
            )
            geometry_violations = sweep.validate_candidate_geometry(p)
            if geometry_violations:
                screen_table.append(
                    {
                        "candidate_id": p["candidate_id"],
                        "family": family,
                        "stage": "physical_prefilter",
                        "status": "REJECTED",
                        "reasons": geometry_violations,
                    }
                )
                continue
            valid_count += 1
            try:
                free = search._run_authority(
                    p,
                    "STAGE_FREE_DESCENT_DIAGNOSTIC",
                    p["candidate_id"],
                )
            except Exception as exc:
                screen_table.append(
                    {
                        "candidate_id": p["candidate_id"],
                        "family": family,
                        "stage": "free_descent",
                        "status": "ERROR",
                        "reasons": [str(exc)],
                    }
                )
                continue
            admissible, violations = search._ascent_admissible(free, p)
            opportunities = (
                _screen_opportunities(free, p) if admissible else []
            )
            usable = [item for item in opportunities if item.get("usable")]
            row = {
                "candidate_id": p["candidate_id"],
                "family": family,
                "stage": "motor_window_screen",
                "status": "SURVIVED" if usable else "REJECTED",
                "ascent_admissible": admissible,
                "violations": violations,
                "apogee_m": free.get("apogee_m"),
                "mach": free.get("mach"),
                "min_static_margin": free.get("min_static_margin"),
                "free_touchdown": next(
                    (
                        item for item in free.get("stage_landings", [])
                        if item.get("stage_key") == "s0"
                    ),
                    None,
                ),
                "best_opportunity": opportunities[0] if opportunities else None,
            }
            screen_table.append(row)
            if usable:
                family_survivors.append(
                    {
                        "parameters": p,
                        "free_metrics": free,
                        "opportunities": usable,
                    }
                )
            _write_json(output / "compact-screen-table.json", screen_table)

        family_survivors.sort(
            key=lambda item: (
                -float(
                    item["opportunities"][0][
                        "fraction_burn_opposing_vertical_velocity"
                    ]
                ),
                float(
                    item["opportunities"][0]["predicted_touchdown_speed_ms"]
                ),
            )
        )
        finalists = family_survivors[:POWERED_FINALIST_LIMIT]
        for finalist in finalists:
            p = finalist["parameters"]
            predicted_by_delay = {
                round(float(item["candidate_ignition_time_s"]), 6): item
                for item in finalist["opportunities"]
            }
            initial_runs = []
            for opportunity in finalist["opportunities"][:POWERED_FINALIST_LIMIT]:
                run = _powered_run(
                    p, float(opportunity["candidate_ignition_time_s"])
                )
                initial_runs.append(run)
                comparison = _powered_comparison(opportunity, run["metrics"])
                powered_table.append(
                    {
                        "candidate_id": p["candidate_id"],
                        "family": family,
                        "delay_s": run["delay_s"],
                        **comparison,
                    }
                )
            refined = _refine(p, initial_runs)
            for run in refined:
                if round(float(run["delay_s"]), 6) in predicted_by_delay:
                    continue
                # Use the nearest free-fall prediction for refinement deltas.
                predicted = min(
                    finalist["opportunities"],
                    key=lambda item: abs(
                        float(item["candidate_ignition_time_s"])
                        - float(run["delay_s"])
                    ),
                )
                comparison = _powered_comparison(predicted, run["metrics"])
                powered_table.append(
                    {
                        "candidate_id": p["candidate_id"],
                        "family": family,
                        "delay_s": run["delay_s"],
                        "refined_from_nearest_prediction": True,
                        **comparison,
                    }
                )
            best = min(
                refined, key=lambda item: float(item["landing"]["total_speed"])
            )
            if float(best["landing"]["total_speed"]) < 5.0:
                selected = float(best["delay_s"])
                robustness = []
                for offset in (-0.020, -0.010, 0.0, 0.010, 0.020):
                    delay = round(selected + offset, 6)
                    run = next(
                        (
                            item for item in refined
                            if abs(float(item["delay_s"]) - delay) < 1e-9
                        ),
                        None,
                    ) or _powered_run(p, delay)
                    landing = run["landing"]
                    robustness.append(
                        {
                            "delay_s": delay,
                            "touchdown_total_speed_ms": landing["total_speed"],
                            "touchdown_vertical_speed_ms": landing["vz_ms"],
                            "touchdown_horizontal_speed_ms": landing["vxy_ms"],
                            "legal": float(landing["total_speed"]) < 5.0,
                            "status": run["metrics"].get("status"),
                        }
                    )
                best_legal = {
                    "status": "LEGAL BRANCH",
                    "candidate_id": p["candidate_id"],
                    "family": family,
                    "selected_delay_s": selected,
                    "parameters": best["parameters"],
                    "landing": best["landing"],
                    "metrics": best["metrics"],
                    "robustness": robustness,
                }
                ork_path = output / "best-legal-branch.ork"
                sweep.save_simulated_ork(
                    sweep.generate_ork(best["parameters"]), ork_path
                )
                best_legal["ork_path"] = str(ork_path)
                best_legal["ork_sha256"] = hashlib.sha256(
                    ork_path.read_bytes()
                ).hexdigest()
                _write_json(output / "best-legal-branch.json", best_legal)
                break
        _write_json(output / "powered-openrocket-comparisons.json", powered_table)
        if best_legal is not None:
            break

    classifications = {}
    for row in powered_table:
        classification = row["classification"]
        classifications[classification] = classifications.get(classification, 0) + 1
    _write_json(
        output / "false-positive-false-negative-table.json",
        {
            "counts": classifications,
            "comparisons": powered_table,
        },
    )
    manifest["status"] = "LEGAL BRANCH" if best_legal else "NO LEGAL BRANCH"
    manifest["tested_free_structures"] = sum(
        row.get("stage") == "motor_window_screen" for row in screen_table
    )
    manifest["powered_authority_runs"] = len(powered_table)
    manifest["result_path"] = (
        str(output / "best-legal-branch.json")
        if best_legal else str(output / "best-failure-evidence.json")
    )
    if best_legal is None:
        failures = [
            row for row in screen_table
            if row.get("stage") == "motor_window_screen"
        ]
        _write_json(
            output / "best-failure-evidence.json",
            {
                "status": "NO LEGAL BRANCH",
                "screened_structures": len(failures),
                "dominant_failure": "No powered sustainer touchdown below 5 m/s",
                "best_screen_survivors": [
                    row for row in failures if row.get("status") == "SURVIVED"
                ][:10],
            },
        )
    _write_json(output / "search-manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "output": str(output)}))
    return 0 if best_legal else 2


if __name__ == "__main__":
    raise SystemExit(main())
