"""Reusable OSIFOG Level 3 mission adapter over the OpenRocket authority.

This module contains mission policy and orchestration only.  Geometry,
OpenRocket execution and official scoring remain in :mod:`osifog_sweep`.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping
from functools import lru_cache

import osifog_sweep as sweep
from mission_evolution import EvolutionEngine, bisect_transition


MISSION_FILE = Path(__file__).resolve().parent / "missions" / "osifog_l3_precision.json"


@lru_cache(maxsize=1)
def load_mission_contract() -> dict[str, Any]:
    """Load the single source of truth for rules, scoring and search space."""
    return json.loads(MISSION_FILE.read_text(encoding="utf-8"))


def _motor_index_for_designation(designation: str) -> int:
    for index, motor in enumerate(sweep.MOTOR_DATABASE):
        if motor[1] == designation:
            return index
    raise ValueError(f"mission references unknown motor {designation!r}")


def falcon_submission_candidate() -> dict[str, Any]:
    """Return the current genuine-staging Falcon optimization seed."""
    return {
        "s0_main": 37,              # 3 x AeroTech 949J150-P
        "s1_main": 18,              # 3 x AeroTech J360
        "s0_retro": 19,             # central K550W
        "s1_retro": 19,
        "main_cluster_count": 3,
        "s0_body_rad": 0.074,
        "s1_body_rad": 0.074,
        "s0_body_len": 0.70,
        "s1_body_len": 0.75,
        "s1_separation_delay": 0.0,
        "s0_retro_delay": 54.30968241003204,
        "s1_retro_delay": 65.28052643047579,
        "nose_mass_kg": 1.72,
        "nose_ballast_pos_m": 0.45,
        "s0_mid_ballast_kg": 0.0,
        "s1_mid_ballast_kg": 0.0,
        "s0_aft_ballast_kg": 0.0,
        "s1_aft_ballast_kg": 2.725,
        "s1_aft_ballast_pos_m": 0.084,
        "s1_aft_ballast_rod_radius_m": 0.014,
        "s1_aft_ballast_attachment": "central_bonded",
        "s0_fin_count": 4,
        "s0_fin_root": 0.20,
        "s0_fin_height": 0.25,
        "s0_fin_sweep": 10.0,
        "s1_fin_count": 4,
        "s1_fin_root": 0.24,
        "s1_fin_height": 0.38,
        "s1_fin_sweep": 0.05,
        "s1_grid_fin_count": 4,
        "s1_grid_fin_root": 0.10,
        "s1_grid_fin_height": 0.08,
        "s1_grid_fin_position_m": 0.03,
        "launch_azimuth": 34.0,
        "launch_angle_deg": 3.85,
        "wind_levels": sweep.parse_wind_csv(sweep.WIND_CSV),
    }


def falcon_850k_candidate() -> dict[str, Any]:
    """Backward-compatible name for the current submission candidate."""
    return falcon_submission_candidate()


def evaluate_candidate(params: Mapping[str, Any]) -> dict[str, Any]:
    """Run the OpenRocket authority and attach hard-gate and score results."""
    sweep.init_or()
    candidate = dict(params)
    metrics = sweep.run_sim(sweep.generate_ork(candidate))
    legal, violations = sweep.validate_official_constraints(metrics, candidate)
    metrics["legal"] = legal
    metrics["violations"] = violations
    metrics["official_score"] = score_from_mission_contract(metrics, candidate)["score"]
    return metrics


def score_from_mission_contract(
    metrics: Mapping[str, Any], params: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate the mission JSON scoring table and expose every term."""
    contract = load_mission_contract()
    stages = list(metrics.get("stage_landings", []))
    propellant_mass = float(metrics.get("m_prop_kg_actual", 0.0))
    if propellant_mass <= 0.01:
        multiplier = int(params.get("main_cluster_count", 1))
        propellant_mass = (
            multiplier * sweep.propellant_kg(params["s0_main"])
            + sweep.propellant_kg(params["s0_retro"])
            + multiplier * sweep.propellant_kg(params["s1_main"])
            + sweep.propellant_kg(params["s1_retro"])
        )

    values: dict[str, float | list[float]] = {
        "apogee_m": float(metrics.get("apogee_m", 0.0)),
        "apogee_east_m": float(metrics.get("apogee_east_m", 0.0)),
        "apogee_north_m": float(metrics.get("apogee_north_m", 0.0)),
        "stage_landing_east_m": [float(item["east_m"]) for item in stages],
        "stage_landing_north_m": [float(item["north_m"]) for item in stages],
        "stage_landing_total_speed_ms": [
            float(item["total_speed"]) for item in stages
        ],
        "total_prop_mass_kg": propellant_mass,
    }
    score = float(contract["scoring"]["base_score"])
    term_values: dict[str, float] = {}
    for term in contract["scoring"]["terms"]:
        penalty_sum = 0.0
        for metric_name, reference in zip(term["metrics"], term["reference"]):
            value = values[metric_name]
            if isinstance(value, list):
                if not value:
                    raise ValueError(f"scoring metric {metric_name!r} has no stage values")
                if term.get("aggregate") != "mean_over_stages":
                    raise ValueError(f"per-stage metric {metric_name!r} needs an aggregate")
                scalar = sum(value) / len(value)
            else:
                scalar = value
            penalty_sum += (scalar - float(reference)) ** int(term["power"])
        contribution = float(term["coefficient"]) * penalty_sum
        term_values[term["name"]] = contribution
        score += contribution

    legal, violations = sweep.validate_official_constraints(
        dict(metrics), dict(params)
    )
    return {
        "score": score if legal else -1_000_000.0,
        "raw_score": score,
        "is_legal": legal,
        "violations": violations,
        "terms": term_values,
        "total_prop_mass_kg": propellant_mass,
    }


def _raw_openrocket_evaluator(params: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one already-compiled candidate with OpenRocket authority."""
    candidate = dict(params)
    geometry_violations = sweep.validate_candidate_geometry(candidate)
    if geometry_violations:
        return {
            "status": "PHYSICAL_GEOMETRY_REJECTED",
            "geometry_violations": geometry_violations,
            "stage_landings": [],
            "mach": float("inf"),
            "min_static_margin": float("-inf"),
            "apogee_m": float("-inf"),
        }
    return sweep.run_sim(sweep.generate_ork(candidate))


def _ascent_gate(metrics: Mapping[str, Any]) -> bool:
    return (
        not metrics.get("geometry_violations")
        and float(metrics.get("mach", float("inf"))) < sweep.MAX_MACH
        and float(metrics.get("min_static_margin", float("-inf"))) >= sweep.MIN_STATIC_MARGIN
        and math.isfinite(float(metrics.get("apogee_m", float("nan"))))
    )


def _ascent_objective(metrics: Mapping[str, Any], params: Mapping[str, Any]) -> float:
    """Official-score-shaped objective before landing delays are calibrated."""
    altitude_error = float(metrics["apogee_m"]) - sweep.TARGET_APOGEE
    propellant_mass = float(metrics.get("m_prop_kg_actual", 0.0))
    if propellant_mass <= 0.01:
        propellant_mass = sum(
            sweep.propellant_kg(params[key])
            for key in ("s0_main", "s0_retro", "s1_main", "s1_retro")
        )
    propellant_penalty = 7500.0 * propellant_mass
    return 3000.0 * altitude_error**2 + propellant_penalty


def _delay_point_is_usable(
    metrics: Mapping[str, Any], stage_index: int, direct_time_limit: float
) -> bool:
    landings = metrics.get("stage_landings", [])
    if len(landings) <= stage_index:
        return False
    landing = landings[stage_index]
    return (
        float(landing["time_s"]) < direct_time_limit
        and float(landing["vz_ms"]) <= 0.0
        and float(metrics.get("min_static_margin", float("-inf")))
        >= sweep.MIN_STATIC_MARGIN
        and float(metrics.get("mach", float("inf"))) < sweep.MAX_MACH
    )


def adaptive_delay_search(
    engine: EvolutionEngine,
    params: Mapping[str, Any],
    stage_index: int,
    low: float,
    high: float,
    direct_time_limit: float,
    rounds: int = 3,
    samples_per_round: int = 9,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    """Search a discontinuous retro-ignition boundary without assuming monotonic speed.

    Unlike the retired one-motor-at-a-time bisection, this keeps every other
    motor active and repeatedly refines every observed direct/non-direct branch
    boundary plus the best physically usable landing.
    """
    if stage_index not in (0, 1) or not low < high:
        raise ValueError("invalid stage or delay interval")
    key = f"s{stage_index}_retro_delay"
    intervals = [(float(low), float(high))]
    best: tuple[float, Mapping[str, Any]] | None = None

    for _ in range(rounds):
        evaluated: list[tuple[float, Mapping[str, Any], bool]] = []
        for start, end in intervals:
            for index in range(samples_per_round):
                fraction = index / (samples_per_round - 1)
                delay = start + (end - start) * fraction
                candidate = dict(params)
                candidate[key] = delay
                metrics = engine.evaluate(candidate)
                direct = (
                    len(metrics.get("stage_landings", [])) > stage_index
                    and float(metrics["stage_landings"][stage_index]["time_s"])
                    < direct_time_limit
                )
                evaluated.append((delay, metrics, direct))
                ignition_precedes_touchdown = (
                    delay
                    < float(metrics["stage_landings"][stage_index]["time_s"])
                    - 1.0e-6
                ) if len(metrics.get("stage_landings", [])) > stage_index else False
                if (
                    ignition_precedes_touchdown
                    and _delay_point_is_usable(metrics, stage_index, direct_time_limit)
                ):
                    if best is None or float(
                        metrics["stage_landings"][stage_index]["total_speed"]
                    ) < float(best[1]["stage_landings"][stage_index]["total_speed"]):
                        best = (delay, metrics)

        unique = sorted({item[0]: item for item in evaluated}.values(), key=lambda item: item[0])
        next_intervals: list[tuple[float, float]] = []
        for first, second in zip(unique, unique[1:]):
            if first[2] != second[2]:
                next_intervals.append((first[0], second[0]))
        if best is not None:
            delays = [item[0] for item in unique]
            nearest = min(range(len(delays)), key=lambda index: abs(delays[index] - best[0]))
            left = delays[max(0, nearest - 1)]
            right = delays[min(len(delays) - 1, nearest + 1)]
            if right > left:
                next_intervals.append((left, right))
        if not next_intervals:
            break
        intervals = next_intervals

    if best is None:
        raise RuntimeError(f"no usable direct landing found for stage {stage_index}")
    winner = dict(params)
    winner[key] = best[0]
    return winner, best[1]


def _physical_ascent_candidates(base: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Bounded topology-preserving candidates for the physical Falcon repair."""
    space = load_mission_contract()["evolution"]["physical_repair_space"]
    candidates: list[dict[str, Any]] = []
    for designation in space["s0_retro_designations"]:
        retro = _motor_index_for_designation(designation)
        for nose_mass in space["nose_ballast_mass_kg"]:
            for root in space["s1_fin_root_m"]:
                for height in space["s1_fin_height_m"]:
                    candidate = dict(base)
                    candidate.update(
                        s0_retro=retro,
                        nose_mass_kg=float(nose_mass),
                        s1_fin_root=root,
                        s1_fin_height=height,
                        s0_retro_delay=200.0,
                        s1_retro_delay=200.0,
                    )
                    candidates.append(candidate)
    return candidates


def optimize_physical_falcon(
    initial: Mapping[str, Any] | None = None,
    beam_width: int = 4,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Evolve a collision-free Falcon and calibrate both retro motors in OR.

    Returns ``(params, metrics, diagnostics)``.  Every simulation is memoized;
    geometry is rejected before JVM work, and final legality is decided only by
    the same OpenRocket hard gates used for submission.
    """
    sweep.init_or()
    base = dict(initial or falcon_submission_candidate())
    engine = EvolutionEngine(_raw_openrocket_evaluator)
    search_space = load_mission_contract()["evolution"]["physical_repair_space"]
    delay_search = search_space["delay_search"]

    ascent_ranked = []
    for candidate in _physical_ascent_candidates(base):
        metrics = engine.evaluate(candidate)
        if _ascent_gate(metrics):
            ascent_ranked.append(
                (_ascent_objective(metrics, candidate), candidate, metrics)
            )
    ascent_ranked.sort(key=lambda item: item[0])
    if not ascent_ranked:
        raise RuntimeError("physical ascent beam produced no legal candidate")

    # Preserve at least the best ascent candidate for each retro motor.  A
    # purely global beam collapses onto the altitude-perfect K550 family and
    # discards K700 before its materially better landing controllability can be
    # measured.
    selected = []
    seen_signatures = set()
    for retro in sorted({item[1]["s0_retro"] for item in ascent_ranked}):
        item = next(entry for entry in ascent_ranked if entry[1]["s0_retro"] == retro)
        signature = (
            item[1]["s0_retro"],
            item[1]["nose_mass_kg"],
            item[1]["s1_fin_root"],
            item[1]["s1_fin_height"],
        )
        selected.append(item)
        seen_signatures.add(signature)
    for item in ascent_ranked:
        signature = (
            item[1]["s0_retro"],
            item[1]["nose_mass_kg"],
            item[1]["s1_fin_root"],
            item[1]["s1_fin_height"],
        )
        if signature not in seen_signatures:
            selected.append(item)
            seen_signatures.add(signature)
        if len(selected) >= beam_width:
            break

    finalists = []
    for _, candidate, ascent_metrics in selected[:beam_width]:
        ground_times = [
            float(item["time_s"]) for item in ascent_metrics["stage_landings"][:2]
        ]
        tuned = dict(candidate)
        # Coordinate descent keeps both motors present.  The untouched delay is
        # initialized just beyond ground impact, not removed from the vehicle.
        tuned["s0_retro_delay"] = ground_times[0] + 0.25
        tuned["s1_retro_delay"] = ground_times[1] + 0.25

        # Separation timing couples both landing branches.  Calibrate both
        # motors for every separation candidate before ranking it; selecting
        # on one stage alone can make the other branch unrecoverable.
        phase_winners = []
        for separation_delay in search_space["s1_separation_delay_s"]:
            free_phase = dict(
                candidate,
                s1_separation_delay=float(separation_delay),
                s0_retro_delay=200.0,
                s1_retro_delay=200.0,
            )
            free_phase_metrics = engine.evaluate(free_phase)
            phase_ground_times = [
                float(item["time_s"])
                for item in free_phase_metrics.get("stage_landings", [])[:2]
            ]
            if len(phase_ground_times) != 2:
                continue
            calibrated = dict(
                free_phase,
                s0_retro_delay=phase_ground_times[0] + 0.25,
                s1_retro_delay=phase_ground_times[1] + 0.25,
            )
            try:
                for _ in range(2):
                    for stage_index in (0, 1):
                        calibrated, _ = adaptive_delay_search(
                            engine,
                            calibrated,
                            stage_index,
                            phase_ground_times[stage_index]
                            - float(delay_search["window_before_ground_s"]),
                            phase_ground_times[stage_index]
                            + float(delay_search["window_after_ground_s"]),
                            direct_time_limit=phase_ground_times[stage_index] + 3.0,
                            rounds=int(delay_search["rounds"]),
                            samples_per_round=int(delay_search["samples_per_round"]),
                        )
            except RuntimeError:
                continue
            calibrated_metrics = engine.evaluate(calibrated)
            calibrated_scoring = score_from_mission_contract(
                calibrated_metrics, calibrated
            )
            speeds = [
                float(item["total_speed"])
                for item in calibrated_metrics.get("stage_landings", [])[:2]
            ]
            if len(speeds) != 2:
                continue
            landing_excess = sum(max(0.0, speed - 5.0) ** 2 for speed in speeds)
            phase_rank = (
                1 if calibrated_scoring["is_legal"] else 0,
                calibrated_scoring["score"]
                if calibrated_scoring["is_legal"]
                else -landing_excess,
                calibrated_scoring["raw_score"],
            )
            phase_winners.append(
                (phase_rank, calibrated, calibrated_metrics)
            )
        if not phase_winners:
            raise RuntimeError("joint separation/delay calibration had no candidate")
        phase_winners.sort(key=lambda item: item[0], reverse=True)
        _, tuned, _ = phase_winners[0]
        metrics = engine.evaluate(tuned)
        legal, violations = sweep.validate_official_constraints(metrics, tuned)
        scoring = score_from_mission_contract(metrics, tuned)
        metrics = dict(metrics)
        metrics.update(legal=legal, violations=violations, official_score=scoring["score"])
        landing_excess = sum(
            max(0.0, float(item["total_speed"]) - 5.0) ** 2
            for item in metrics.get("stage_landings", [])
        )
        rank = (
            1 if legal else 0,
            scoring["score"] if legal else -landing_excess,
            scoring["raw_score"],
        )
        finalists.append((rank, tuned, metrics, scoring))

    finalists.sort(key=lambda item: item[0], reverse=True)
    _, params, metrics, winning_scoring = finalists[0]
    diagnostics = {
        "evaluations": engine.evaluation_count,
        "ascent_candidates": len(ascent_ranked),
        "finalists": len(finalists),
        "best_score": winning_scoring["score"],
        "finalist_summaries": [
            {
                "s0_retro": item[1]["s0_retro"],
                "nose_mass_kg": item[1]["nose_mass_kg"],
                "fin_root": item[1]["s1_fin_root"],
                "fin_height": item[1]["s1_fin_height"],
                "apogee_m": item[2].get("apogee_m"),
                "landing_speeds": [
                    landing["total_speed"]
                    for landing in item[2].get("stage_landings", [])
                ],
                "legal": item[2]["legal"],
                "score": item[3]["score"],
                "raw_score": item[3]["raw_score"],
            }
            for item in finalists
        ],
    }
    return dict(params), dict(metrics), diagnostics


def calibrate_genuine_landing(
    initial: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Calibrate both delayed central motors after genuine ascent staging."""

    sweep.init_or()
    engine = EvolutionEngine(_raw_openrocket_evaluator)
    candidate = dict(initial or falcon_submission_candidate())
    candidate.update(
        s0_retro_delay=200.0,
        s1_retro_delay=200.0,
        s0_retro_ignition_event="launch",
        s1_retro_ignition_event="launch",
    )
    free_metrics = engine.evaluate(candidate)
    ground_times = [
        float(item["time_s"])
        for item in free_metrics.get("stage_landings", [])[:2]
    ]
    if len(ground_times) != 2:
        raise RuntimeError("genuine-staging seed did not produce two free impacts")

    tuned = dict(
        candidate,
        s0_retro_delay=ground_times[0] + 0.25,
        s1_retro_delay=ground_times[1] + 0.25,
    )
    delay_search = load_mission_contract()["evolution"]["physical_repair_space"][
        "delay_search"
    ]
    for _ in range(3):
        for stage_index in (0, 1):
            tuned, _ = adaptive_delay_search(
                engine,
                tuned,
                stage_index,
                ground_times[stage_index]
                - float(delay_search["window_before_ground_s"]),
                ground_times[stage_index]
                + float(delay_search["window_after_ground_s"]),
                direct_time_limit=ground_times[stage_index] + 3.0,
                rounds=int(delay_search["rounds"]),
                samples_per_round=int(delay_search["samples_per_round"]),
            )
    metrics = engine.evaluate(tuned)
    legal, violations = sweep.validate_official_constraints(metrics, tuned)
    scoring = score_from_mission_contract(metrics, tuned)
    metrics = dict(
        metrics,
        legal=legal,
        violations=violations,
        official_score=scoring["score"],
    )
    return tuned, metrics, {
        "evaluations": engine.evaluation_count,
        "free_impact_times_s": ground_times,
        "score": scoring["score"],
        "raw_score": scoring["raw_score"],
    }


def _inclusive_float_range(start: float, end: float, step: float) -> list[float]:
    if step <= 0 or end < start:
        raise ValueError("invalid floating search range")
    count = int(math.floor((end - start) / step + 1.0e-9))
    values = [start + index * step for index in range(count + 1)]
    if not math.isclose(values[-1], end, abs_tol=1.0e-9):
        values.append(end)
    return values


def polish_trajectory_from_contract(
    initial: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Optimize launch trajectory against the data-driven score table.

    Delay offsets are transported from each candidate's own no-retro impact
    times, then recalibrated at the OpenRocket discontinuity.  This lets the
    polishing phase trade apogee displacement against touchdown drift without
    hardcoded launch directions or stale ignition timing.
    """
    sweep.init_or()
    contract = load_mission_contract()
    space = contract["evolution"]["physical_repair_space"]
    polish = space["trajectory_polish"]
    delay_search = space["delay_search"]
    engine = EvolutionEngine(_raw_openrocket_evaluator)
    base = dict(initial)

    free_base = dict(base, s0_retro_delay=200.0, s1_retro_delay=200.0)
    free_metrics = engine.evaluate(free_base)
    free_times = [float(item["time_s"]) for item in free_metrics["stage_landings"][:2]]
    ignition_leads = [
        free_times[index] - float(base[f"s{index}_retro_delay"])
        for index in (0, 1)
    ]

    azimuths = _inclusive_float_range(
        float(polish["azimuth_range_deg"][0]),
        float(polish["azimuth_range_deg"][1]),
        float(polish["azimuth_step_deg"]),
    )
    # 360 degrees duplicates zero exactly.
    azimuths = [value for value in azimuths if value < 360.0 - 1.0e-9]
    angles = _inclusive_float_range(
        float(polish["angle_from_vertical_range_deg"][0]),
        float(polish["angle_from_vertical_range_deg"][1]),
        float(polish["angle_step_deg"]),
    )

    coarse = []
    base_metrics = engine.evaluate(base)
    base_scoring = score_from_mission_contract(base_metrics, base)
    coarse.append(
        (
            (1 if base_scoring["is_legal"] else 0, base_scoring["score"], base_scoring["raw_score"]),
            base,
            base_metrics,
            free_times,
        )
    )
    for azimuth in azimuths:
        for angle in angles:
            free_candidate = dict(
                base,
                launch_azimuth=azimuth,
                launch_angle_deg=angle,
                s0_retro_delay=200.0,
                s1_retro_delay=200.0,
            )
            candidate_free_metrics = engine.evaluate(free_candidate)
            if not _ascent_gate(candidate_free_metrics):
                continue
            candidate_times = [
                float(item["time_s"])
                for item in candidate_free_metrics["stage_landings"][:2]
            ]
            candidate = dict(free_candidate)
            for stage_index in (0, 1):
                candidate[f"s{stage_index}_retro_delay"] = (
                    candidate_times[stage_index] - ignition_leads[stage_index]
                )
            metrics = engine.evaluate(candidate)
            scoring = score_from_mission_contract(metrics, candidate)
            excess = sum(
                max(0.0, float(item["total_speed"]) - 5.0) ** 2
                for item in metrics.get("stage_landings", [])
            )
            rank = (
                1 if scoring["is_legal"] else 0,
                scoring["score"] if scoring["is_legal"] else -excess,
                scoring["raw_score"],
            )
            coarse.append((rank, candidate, metrics, candidate_times))
    if not coarse:
        raise RuntimeError("trajectory polishing produced no ascent-safe candidates")
    coarse.sort(key=lambda item: item[0], reverse=True)

    finalists = []
    for _, candidate, _, ground_times in coarse[: int(polish["beam_width"])]:
        tuned = dict(candidate)
        try:
            for _ in range(int(polish["coordinate_rounds"])):
                for stage_index in (0, 1):
                    tuned, _ = adaptive_delay_search(
                        engine,
                        tuned,
                        stage_index,
                        ground_times[stage_index]
                        - float(delay_search["window_before_ground_s"]),
                        ground_times[stage_index]
                        + float(delay_search["window_after_ground_s"]),
                        direct_time_limit=ground_times[stage_index] + 3.0,
                        rounds=int(delay_search["rounds"]),
                        samples_per_round=int(delay_search["samples_per_round"]),
                    )
        except RuntimeError:
            continue
        metrics = engine.evaluate(tuned)
        scoring = score_from_mission_contract(metrics, tuned)
        finalists.append((scoring["score"], tuned, metrics, scoring))
    if not finalists:
        return base, dict(base_metrics, official_score=base_scoring["score"]), {
            "evaluations": engine.evaluation_count,
            "coarse_candidates": len(coarse),
            "finalists": 0,
            "score_terms": base_scoring["terms"],
            "fallback": "no polished candidate preserved both direct landings",
        }
    finalists.sort(key=lambda item: item[0], reverse=True)
    score, params, metrics, scoring = finalists[0]
    return dict(params), dict(metrics, official_score=score), {
        "evaluations": engine.evaluation_count,
        "coarse_candidates": len(coarse),
        "finalists": len(finalists),
        "score_terms": scoring["terms"],
    }


def calibrate_touchdown_delay(
    params: Mapping[str, Any],
    stage_index: int,
    low: float,
    high: float,
    direct_time_limit: float,
    iterations: int = 24,
) -> tuple[float, Mapping[str, Any]]:
    """Calibrate one retro delay at its direct/relaunch transition."""
    if stage_index not in (0, 1):
        raise ValueError("stage_index must be 0 or 1")
    key = f"s{stage_index}_retro_delay"
    other = f"s{1 - stage_index}_retro_delay"

    def run(delay: float) -> Mapping[str, Any]:
        candidate = dict(params)
        candidate[key] = delay
        candidate[other] = 200.0
        metrics = sweep.run_sim(sweep.generate_ork(candidate))
        return metrics["stage_landings"][stage_index]

    return bisect_transition(
        run,
        low,
        high,
        is_direct=lambda landing: float(landing["time_s"]) < direct_time_limit,
        objective=lambda landing: float(landing["total_speed"]),
        iterations=iterations,
    )


def _lerp(branch, data_type, index: int, fraction: float) -> float:
    values = branch.get(data_type)
    return float(values[index - 1]) + fraction * (
        float(values[index]) - float(values[index - 1])
    )


def inspect_saved_submission(path: str | os.PathLike[str], params: Mapping[str, Any]) -> dict[str, Any]:
    """Read saved flight data without rerunning and compute official score."""
    import jpype

    sweep.init_or()
    doc = sweep._load_ork_doc(os.path.abspath(path))
    simulations = doc.getSimulations()
    if int(simulations.size()) != 1:
        raise ValueError(f"expected one simulation, found {simulations.size()}")
    sim = simulations.get(0)
    data = sim.getSimulatedData()
    if data is None:
        raise ValueError("submission has no saved flight data")

    fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
    flight_event = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
    branch0 = data.getBranch(0)
    altitude = branch0.get(fdt.TYPE_ALTITUDE)
    apex_index = max(
        range(int(branch0.getLength())), key=lambda index: float(altitude[index])
    )
    metrics: dict[str, Any] = {
        "apogee_m": float(data.getMaxAltitude()),
        "mach": float(data.getMaxMachNumber()),
        "flight_time_s": float(data.getFlightTime()),
        "status": str(sim.getStatus()),
        "seed": sweep.SIM_SEED,
        "apogee_east_m": float(branch0.get(fdt.TYPE_POSITION_X)[apex_index]),
        "apogee_north_m": float(branch0.get(fdt.TYPE_POSITION_Y)[apex_index]),
    }
    event_times: dict[str, list[float]] = {}
    branch_event_times: list[dict[str, list[float]]] = []
    for branch_index in range(int(data.getBranchCount())):
        branch_events: dict[str, list[float]] = {}
        for event in data.getBranch(branch_index).getEvents():
            event_name = str(event.getType().name())
            values = event_times.setdefault(event_name, [])
            branch_values = branch_events.setdefault(event_name, [])
            event_time = float(event.getTime())
            if not any(abs(existing - event_time) < 1.0e-9 for existing in values):
                values.append(event_time)
            if not any(
                abs(existing - event_time) < 1.0e-9 for existing in branch_values
            ):
                branch_values.append(event_time)
        for values in branch_events.values():
            values.sort()
        branch_event_times.append(branch_events)
    for values in event_times.values():
        values.sort()
    metrics["event_times"] = event_times
    metrics["branch_event_times"] = branch_event_times

    initial_mass = float(branch0.get(fdt.TYPE_MASS)[0])
    final_mass = 0.0
    ascent_stability: list[dict[str, float | int]] = []
    retro_burn_diagnostics: list[dict[str, Any]] = []
    landings: list[dict[str, float | int]] = []
    for branch_index in range(int(data.getBranchCount())):
        branch = data.getBranch(branch_index)
        branch_name = str(branch.getName())
        normalized_name = branch_name.strip().lower()
        stage_key = (
            "s0" if "sustainer" in normalized_name
            else "s1" if "booster" in normalized_name
            else None
        )
        burn_diagnostic = sweep._retro_burn_diagnostic(
            branch.get(fdt.TYPE_TIME),
            branch.get(fdt.TYPE_POSITION_X),
            branch.get(fdt.TYPE_POSITION_Y),
            branch.get(fdt.TYPE_VELOCITY_Z),
            branch.get(fdt.TYPE_ORIENTATION_THETA),
            branch.get(fdt.TYPE_ORIENTATION_PHI),
            branch.get(fdt.TYPE_THRUST_FORCE),
            (
                branch_event_times[branch_index].get("APOGEE", [None])[0]
                if branch_event_times[branch_index].get("APOGEE")
                else None
            ),
        )
        burn_diagnostic.update(
            branch=branch_index,
            branch_name=branch_name,
            stage_key=stage_key,
        )
        retro_burn_diagnostics.append(burn_diagnostic)
        hit_time = next(
            (
                float(event.getTime())
                for event in branch.getEvents()
                if event.getType() == flight_event.Type.GROUND_HIT
            ),
            None,
        )
        if hit_time is None:
            continue
        times = branch.get(fdt.TYPE_TIME)
        altitude_values = branch.get(fdt.TYPE_ALTITUDE)
        vertical_values = branch.get(fdt.TYPE_VELOCITY_Z)
        stability_values = branch.get(fdt.TYPE_STABILITY)
        finite_ascent_stability = sweep._minimum_initial_ascent_stability(
            altitude_values,
            vertical_values,
            stability_values,
            times,
            (
                min(event_times.get("STAGE_SEPARATION", []))
                if branch_index > 0 and event_times.get("STAGE_SEPARATION")
                else None
            ),
        )
        if finite_ascent_stability is not None:
            ascent_stability.append(
                {"branch": branch_index, "min_calibers": finite_ascent_stability}
            )
        index = next(
            index
            for index in range(1, int(branch.getLength()))
            if float(times[index]) >= hit_time
        )
        t1, t2 = float(times[index - 1]), float(times[index])
        fraction = (hit_time - t1) / (t2 - t1) if t2 > t1 else 1.0
        east = _lerp(branch, fdt.TYPE_POSITION_X, index, fraction)
        north = _lerp(branch, fdt.TYPE_POSITION_Y, index, fraction)
        vertical = _lerp(branch, fdt.TYPE_VELOCITY_Z, index, fraction)
        horizontal = _lerp(branch, fdt.TYPE_VELOCITY_XY, index, fraction)
        mass = _lerp(branch, fdt.TYPE_MASS, index, fraction)
        theta = math.degrees(float(branch.get(fdt.TYPE_ORIENTATION_THETA)[index]))
        phi = math.degrees(float(branch.get(fdt.TYPE_ORIENTATION_PHI)[index]))
        aoa = math.degrees(float(branch.get(fdt.TYPE_AOA)[index]))
        final_mass += mass
        landings.append(
            {
                "branch": branch_index,
                "branch_name": branch_name,
                "stage_key": stage_key,
                "time_s": hit_time,
                "east_m": east,
                "north_m": north,
                "dist_m": math.hypot(east, north),
                "vz_ms": vertical,
                "vxy_ms": horizontal,
                "total_speed": math.hypot(vertical, horizontal),
                "mass_kg": mass,
                "orientation_theta_deg": theta,
                "orientation_phi_deg": phi,
                "aoa_deg": aoa,
            }
        )

    metrics["stage_landings"] = landings
    metrics["retro_burn_diagnostics"] = retro_burn_diagnostics
    metrics["ascent_static_margins"] = ascent_stability
    metrics["min_static_margin"] = min(
        (item["min_calibers"] for item in ascent_stability),
        default=float("-inf"),
    )
    metrics["m_prop_kg_actual"] = max(0.0, initial_mass - final_mass)
    if len(landings) >= 2:
        for index, landing in enumerate(landings[:2]):
            metrics[f"s{index}_landing_speed"] = landing["total_speed"]
            metrics[f"s{index}_east_m"] = landing["east_m"]
            metrics[f"s{index}_north_m"] = landing["north_m"]

    legal, violations = sweep.validate_official_constraints(metrics, dict(params))
    # This is the saved-submission authority path.  The mission-contract
    # scorer intentionally retains the historical optimization gate, so using
    # it here can reject a current legal package for superseded constraints.
    scoring = sweep.score_official(metrics, dict(params))
    extensions = sim.getSimulationExtensions()
    scripts = [
        str(extensions.get(index).getScript())
        for index in range(int(extensions.size()))
        if hasattr(extensions.get(index), "getScript")
    ]
    return {
        **metrics,
        "simulation_count": int(simulations.size()),
        "branch_count": int(data.getBranchCount()),
        "extension_count": int(extensions.size()),
        "anti_tumble_present": any("TUMBLE" in script for script in scripts),
        "legal": legal,
        "violations": violations,
        "official_score": scoring["score"],
        "score_terms": scoring,
    }


def export_openearth_csvs(
    path: str | os.PathLike[str], output_dir: str | os.PathLike[str]
) -> list[Path]:
    """Export each saved flight-data branch in the four-column OpenEarth format."""
    import jpype

    sweep.init_or()
    doc = sweep._load_ork_doc(os.path.abspath(path))
    sim = doc.getSimulations().get(0)
    data = sim.getSimulatedData()
    if data is None:
        raise ValueError("submission has no saved flight data")

    fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
    flight_event = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
    columns = (
        fdt.TYPE_TIME,
        fdt.TYPE_ALTITUDE,
        fdt.TYPE_POSITION_X,
        fdt.TYPE_POSITION_Y,
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for branch_index in range(int(data.getBranchCount())):
        branch = data.getBranch(branch_index)
        values = [branch.get(column) for column in columns]
        hit_time = next(
            (
                float(event.getTime())
                for event in branch.getEvents()
                if event.getType() == flight_event.Type.GROUND_HIT
            ),
            None,
        )
        output = destination / f"osifog_850k_stage_{branch_index + 1}.csv"
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ("Time", "Altitude", "Position East of launch", "Position North of launch")
            )
            end = int(branch.getLength())
            if hit_time is not None:
                end = next(
                    (index for index in range(end) if float(values[0][index]) > hit_time),
                    end,
                )
            for index in range(end):
                writer.writerow(f"{float(series[index]):.9f}" for series in values)
            if (
                hit_time is not None
                and end < int(branch.getLength())
                and (end == 0 or abs(float(values[0][end - 1]) - hit_time) > 1e-9)
            ):
                previous = max(0, end - 1)
                t1, t2 = float(values[0][previous]), float(values[0][end])
                fraction = (hit_time - t1) / (t2 - t1) if t2 > t1 else 1.0
                writer.writerow(
                    f"{(float(series[previous]) + fraction * (float(series[end]) - float(series[previous]))):.9f}"
                    for series in values
                )
        outputs.append(output)
    return outputs


def save_verified_submission(
    params: Mapping[str, Any],
    output: str | os.PathLike[str],
    min_score: float = 600_000.0,
    attempts: int = 12,
) -> dict[str, Any]:
    """Save until the stored simulation itself passes every gate and score."""
    candidate = dict(params)
    xml = sweep.generate_ork(candidate)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    last: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        sweep.save_simulated_ork(xml, str(output_path))
        last = inspect_saved_submission(output_path, candidate)
        last["save_attempt"] = attempt
        if (
            last["legal"]
            and last["anti_tumble_present"]
            and last["official_score"] >= min_score
        ):
            return last
    raise RuntimeError(f"no verified submission after {attempts} attempts; last={last}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="designs/osifog_level3/osifog_850k_falcon.ork",
        help="submission .ork path",
    )
    parser.add_argument("--min-score", type=float, default=600_000.0)
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="re-evolve ascent geometry and recalibrate both retro delays before saving",
    )
    parser.add_argument(
        "--beam-width",
        type=int,
        default=4,
        help="number of ascent families retained by --optimize",
    )
    parser.add_argument(
        "--calibrate-landing",
        action="store_true",
        help="measure free impacts and calibrate both central retro motors",
    )
    parser.add_argument(
        "--openearth-dir",
        help="optionally export one OpenEarth CSV per saved flight-data branch",
    )
    parser.add_argument("--report", help="optionally write the verified JSON report")
    args = parser.parse_args()
    params = falcon_850k_candidate()
    optimization = None
    if args.optimize:
        params, _, optimization = optimize_physical_falcon(
            params, beam_width=args.beam_width
        )
    if args.calibrate_landing:
        params, _, landing_diagnostics = calibrate_genuine_landing(params)
        optimization = {
            **(optimization or {}),
            "landing_calibration": landing_diagnostics,
        }
    report = save_verified_submission(
        params, args.output, min_score=args.min_score
    )
    report["parameters"] = {
        key: value for key, value in params.items() if key != "wind_levels"
    }
    if optimization is not None:
        report["optimization"] = optimization
    if args.openearth_dir:
        report["openearth_csvs"] = [
            str(path) for path in export_openearth_csvs(args.output, args.openearth_dir)
        ]
    serialized = json.dumps(report, indent=2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
