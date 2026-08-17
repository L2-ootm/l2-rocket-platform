#!/usr/bin/env python3
"""Resumable stage-wise surrogate search for a legal OSIFOG recovery basin."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.feature_extraction import DictVectorizer

import osifog_engine_search as search


HISTORY_GLOBS = (
    "osifog_800k_campaign_v2/shards/*/result.json",
    "osifog_recovery_gate_v*/result.json",
    "osifog_cg_reversal_gate_v*/result.json",
    "osifog_cg_reversal_gate_v*/checkpoint.json",
    "osifog_reversal_*_gate_v*/result.json",
    "osifog_reversal_*_gate_v*/checkpoint.json",
    "osifog_legal_stage_campaign_v*/checkpoint.json",
    "osifog_legal_stage_smoke_v*/result.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail_window(metrics: dict, stage_key: str) -> float:
    for item in metrics.get("descent_alignment_diagnostics", []):
        if item.get("stage_key") == stage_key:
            return max(
                (float(window.get("duration_s", 0.0)) for window in item.get("tail_first_windows", [])),
                default=0.0,
            )
    return 0.0


def _segment_margin(metrics: dict, segment: str) -> float:
    values = [
        float(item["min_calibers"])
        for item in metrics.get("ascent_stability_segments", [])
        if item.get("segment") == segment and item.get("min_calibers") is not None
    ]
    return min(values, default=-99.0)


def _minimum_ascent_margin(metrics: dict) -> float:
    values = [
        float(item["min_calibers"])
        for item in metrics.get("ascent_stability_segments", [])
        if item.get("min_calibers") is not None
    ]
    return min(values, default=float(metrics.get("min_static_margin", -99.0)))


def _genuine_staging(metrics: dict) -> bool:
    events = metrics.get("event_times", {})
    apogees = [float(value) for value in events.get("APOGEE", [])]
    separations = [float(value) for value in events.get("STAGE_SEPARATION", [])]
    return bool(apogees and separations and min(separations) < min(apogees))


def _records_in(value, source: Path):
    if isinstance(value, dict):
        if isinstance(value.get("parameters"), dict) and isinstance(value.get("metrics"), dict):
            yield source, value
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                yield from _records_in(nested, source)
    elif isinstance(value, list):
        for nested in value:
            yield from _records_in(nested, source)


def load_history(
    designs: Path, campaign_records: list[dict] | None = None,
    *, current_geometry_only: bool = True,
) -> list[dict]:
    """Load current, legal-event-order authority records exactly once."""
    unique = {}
    for pattern in HISTORY_GLOBS:
        for path in designs.glob(pattern):
            if (path.parent / "QUARANTINED.md").exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for record_source, record in _records_in(payload, path):
                parameters = record["parameters"]
                metrics = record["metrics"]
                if not _genuine_staging(metrics):
                    continue
                if not math.isfinite(float(metrics.get("mach", math.inf))):
                    continue
                if current_geometry_only:
                    try:
                        if search._podset_geometry_violations(parameters):
                            continue
                    except (KeyError, TypeError, ValueError):
                        continue
                candidate_id = record.get("candidate_id") or search._candidate_id(parameters)
                unique[candidate_id] = {
                    "candidate_id": candidate_id,
                    "source": str(record_source),
                    "parameters": parameters,
                    "metrics": metrics,
                    "landing_opportunities": record.get("landing_opportunities", []),
                }
    for record in campaign_records or []:
        if isinstance(record.get("metrics"), dict) and _genuine_staging(record["metrics"]):
            unique[record["candidate_id"]] = record
    return list(unique.values())


def _features(parameters: dict) -> dict:
    result = {}
    for key, value in parameters.items():
        if key == "wind_levels" or not (
            key.startswith("s0_") or key.startswith("s1_")
            or key in {"nose_mass_kg", "nose_length_m", "nose_ballast_pos_m"}
        ):
            continue
        if isinstance(value, bool):
            result[key] = int(value)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            result[key] = float(value)
        elif isinstance(value, str):
            result[key] = value
    return result


def _targets(metrics: dict) -> list[float]:
    return [
        _segment_margin(metrics, "sustainer"),
        math.log1p(_tail_window(metrics, "s0")),
        _segment_margin(metrics, "booster"),
        math.log1p(_tail_window(metrics, "s1")),
        float(metrics.get("apogee_m", 0.0)),
        float(metrics.get("mach", 99.0)),
    ]


def _opportunity_targets(record: dict) -> list[float] | None:
    by_stage = {
        item.get("stage_key"): item
        for item in record.get("landing_opportunities", [])
        if item.get("stage_key") in {"s0", "s1"}
    }
    if set(by_stage) != {"s0", "s1"}:
        return None
    result = []
    for stage_key in ("s0", "s1"):
        item = by_stage[stage_key]
        available = float(item.get("available_delta_v_ms") or 0.0)
        required = float(item.get("required_delta_v_ms") or 0.0)
        burn = max(1.0e-6, float(item.get("motor_burn_duration_s") or 0.0))
        duration = float(item.get("usable_tail_first_duration_s") or 0.0)
        result.extend([
            available - required,
            float(item.get("fraction_burn_opposing_total_velocity") or 0.0),
            float(item.get("fraction_burn_opposing_vertical_velocity") or 0.0),
            duration / burn,
        ])
    return result


def _stage_quality(record: dict, stage_key: str) -> float:
    segment = "sustainer" if stage_key == "s0" else "booster"
    return min(
        _segment_margin(record["metrics"], segment) / 1.5,
        _tail_window(record["metrics"], stage_key) / 3.0,
    )


def _sustainer_ascent_quality(record: dict) -> float:
    """Rank parents for the ascent phenotype, independently of recovery."""
    apogee = float(record.get("metrics", {}).get("apogee_m", 0.0))
    corridor = max(-1.0, 1.0 - abs(apogee - 3000.0) / 1000.0)
    return min(_segment_margin(record["metrics"], "sustainer") / 1.5, corridor)


def _stage_recovery_quality(record: dict, stage_key: str) -> float:
    """Rank braking phenotypes without discarding them for poor ascent stability."""
    opportunity = next(
        (
            item for item in record.get("landing_opportunities", [])
            if item.get("stage_key") == stage_key
        ),
        None,
    )
    if opportunity is None:
        # Passive alignment without a measured motor opportunity is useful
        # morphology evidence, but it is not a recovery-module parent.
        return -100.0 + min(_tail_window(record["metrics"], stage_key), 100.0) / 1000.0
    gap = float(opportunity.get("available_delta_v_ms") or 0.0) - float(
        opportunity.get("required_delta_v_ms") or 0.0
    )
    burn = max(1.0e-6, float(opportunity.get("motor_burn_duration_s") or 0.0))
    opposing = float(opportunity.get("fraction_burn_opposing_total_velocity") or 0.0)
    vertical = float(opportunity.get("fraction_burn_opposing_vertical_velocity") or 0.0)
    duration = float(opportunity.get("usable_tail_first_duration_s") or 0.0) / burn
    # Gap is scarce only until a useful reserve exists. Saturation prevents a
    # huge, unstable motor from erasing lower-impulse motors that are already
    # sufficient and compatible with stable morphology.
    gap_score = max(-2.0, min(1.0, gap / 50.0))
    return gap_score + min(opposing, vertical) + min(duration, 2.0)


def _recovery_parent_parameters(
    records: list[dict], stage_key: str, count: int,
) -> list[dict]:
    """Build a measured, motor-stratified recovery donor pool."""
    motor_key = f"{stage_key}_retro"
    groups: dict[int, list[dict]] = {}
    for record in records:
        if not any(
            item.get("stage_key") == stage_key
            for item in record.get("landing_opportunities", [])
        ):
            continue
        motor = int(record["parameters"][motor_key])
        groups.setdefault(motor, []).append(record)
    for group in groups.values():
        group.sort(
            key=lambda item: _stage_recovery_quality(item, stage_key),
            reverse=True,
        )
    groups = {
        motor: group for motor, group in groups.items()
        if _stage_recovery_quality(group[0], stage_key) > 0.0
    }
    selected = []
    while groups and len(selected) < count:
        for motor in sorted(
            groups,
            key=lambda key: _stage_recovery_quality(groups[key][0], stage_key),
            reverse=True,
        ):
            group = groups[motor]
            selected.append(group.pop(0)["parameters"])
            if len(selected) >= count:
                break
            if not group:
                groups.pop(motor, None)
        groups = {motor: group for motor, group in groups.items() if group}
    if not selected:
        raise RuntimeError(f"no measured recovery parents for {stage_key}")
    return selected


def _is_discrete_gene(key: str, value) -> bool:
    return (
        isinstance(value, (str, bool, int))
        or key.endswith("_count")
        or key.endswith("_main")
        or key.endswith("_retro")
        or "material" in key
        or "attachment" in key
        or "shape" in key
    )


def _inherit_stage(child: dict, left: dict, right: dict, prefix: str, rng: random.Random) -> None:
    keys = set(key for key in child if key.startswith(prefix))
    keys.update(key for key in left if key.startswith(prefix))
    keys.update(key for key in right if key.startswith(prefix))
    for key in keys:
        values = [parent[key] for parent in (left, right) if key in parent]
        if not values:
            continue
        value = rng.choice(values)
        if (
            len(values) == 2 and not _is_discrete_gene(key, value)
            and all(isinstance(item, (int, float)) for item in values)
            and rng.random() < 0.45
        ):
            alpha = rng.uniform(0.0, 1.0)
            value = float(values[0]) + alpha * (float(values[1]) - float(values[0]))
        child[key] = value


def _apply_sustainer_aero_stability_mutation(child: dict, rng: random.Random) -> None:
    """Create a low-ballast, long-lever-arm stability phenotype."""
    child["nose_mass_kg"] = min(
        max(0.8, float(child.get("nose_mass_kg", 1.0))), rng.uniform(1.0, 1.4)
    )
    child["nose_length_m"] = max(
        float(child.get("nose_length_m", 1.0)), rng.uniform(1.2, 1.8)
    )
    child["s0_core_length"] = rng.uniform(0.75, 1.15)
    child["s0_core_fin_count"] = 3
    child["s0_core_fin_angle_offset_deg"] = (
        float(child.get("s0_pod_angle_offset_deg", 0.0)) + 60.0
    ) % 120.0
    child["s0_core_fin_height"] = rng.uniform(0.12, 0.24)
    child["s0_core_fin_root"] = rng.uniform(0.12, 0.28)
    child["s0_grid_fin_count"] = 0
    child["s1_separation_delay"] = 0.0
    cage_keys = (
        "s0_core_radius", "s0_pod_radius", "s0_pod_length",
        "s0_pod_nose_length",
    )
    if all(key in child for key in cage_keys):
        # Put the three pods at the aft end and the three core fins in the
        # alternating 60-degree sectors. This is a real interleaved cage, not
        # the former solid-disk approximation that pushed every pod outward.
        child["s0_pod_fin_count"] = 0
        child["s0_pod_fin_height"] = 0.0
        child["s0_pod_axial_offset_m"] = (
            child["s0_core_length"]
            - float(child["s0_pod_nose_length"])
            - float(child["s0_pod_length"])
        )
        child["s0_pod_radial_offset"] = (
            float(child["s0_core_radius"])
            + float(child["s0_pod_radius"])
            + 0.008
        )


def _apply_sustainer_frontier_bridge_mutation(
    child: dict, rng: random.Random,
) -> None:
    """Build a fixed-surface bridge between ascent and recovery phenotypes.

    The aft fins retain a positive ascent margin. Smaller forward canards
    supply the opposing aerodynamic moment absent after apogee. Both sets are
    permanent physical components, not deployment or a recovery device.
    """
    child["nose_mass_kg"] = rng.uniform(0.8, 1.15)
    child["nose_length_m"] = max(
        float(child.get("nose_length_m", 1.0)), rng.uniform(1.15, 1.65)
    )
    child["s0_core_length"] = rng.uniform(1.00, 1.35)
    child["s0_core_fin_count"] = 3
    child["s0_core_fin_angle_offset_deg"] = (
        float(child.get("s0_pod_angle_offset_deg", 0.0)) + 60.0
    ) % 120.0
    child["s0_core_fin_height"] = rng.uniform(0.10, 0.19)
    child["s0_core_fin_root"] = rng.uniform(0.16, 0.32)
    child["s0_grid_fin_count"] = rng.choice((3, 4))
    child["s0_grid_fin_height"] = rng.uniform(0.04, 0.12)
    child["s0_grid_fin_root"] = rng.uniform(0.06, 0.16)
    child["s0_grid_fin_position_m"] = rng.uniform(0.02, 0.18)
    child["s0_grid_fin_sweep"] = rng.uniform(0.0, 20.0)
    child["s1_separation_delay"] = 0.0
    if all(
        key in child
        for key in (
            "s0_core_length", "s0_pod_length", "s0_pod_nose_length",
        )
    ):
        child["s0_pod_fin_count"] = 0
        child["s0_pod_fin_height"] = 0.0
        child["s0_pod_axial_offset_m"] = (
            float(child["s0_core_length"])
            - float(child["s0_pod_nose_length"])
            - float(child["s0_pod_length"])
        )

def stagewise_proposal(
    rng: random.Random, wind_levels: list, sustainer_parents: list[dict],
    booster_parents: list[dict], max_attempts: int = 80,
    donor_pool: list[dict] | None = None,
    sustainer_recovery_parents: list[dict] | None = None,
    booster_recovery_parents: list[dict] | None = None,
) -> dict:
    """Recombine complete stage genomes, then apply bounded genetic injection."""
    for _ in range(max_attempts):
        child = dict(rng.choice(donor_pool)) if donor_pool else search._sample_valid_parameters(rng, wind_levels)
        # Deliberately cross an ascent phenotype with a recovery phenotype.
        # A single scalar parent ranking previously erased the rare positive
        # braking-gap candidates because their first versions were unstable.
        s0_left = rng.choice(sustainer_parents)
        s0_right = rng.choice(sustainer_recovery_parents or sustainer_parents)
        s1_left = rng.choice(booster_parents)
        s1_right = rng.choice(booster_recovery_parents or booster_parents)
        _inherit_stage(child, s0_left, s0_right, "s0_", rng)
        _inherit_stage(child, s1_left, s1_right, "s1_", rng)
        for key in ("nose_mass_kg", "nose_length_m", "nose_ballast_pos_m"):
            values = [item[key] for item in (s0_left, s0_right) if key in item]
            if values:
                if len(values) == 2 and rng.random() < 0.60:
                    alpha = rng.uniform(0.0, 1.0)
                    child[key] = float(values[0]) + alpha * (float(values[1]) - float(values[0]))
                else:
                    child[key] = rng.choice(values)
        # Small random genetic injection prevents the surrogate from merely
        # replaying its own training hull.
        donor = rng.choice(donor_pool) if donor_pool else search._sample_valid_parameters(rng, wind_levels)
        for key, value in donor.items():
            if key != "wind_levels" and rng.random() < 0.06:
                child[key] = value
        morphology_roll = rng.random()
        if morphology_roll < 0.30:
            _apply_sustainer_frontier_bridge_mutation(child, rng)
        elif morphology_roll < 0.50:
            _apply_sustainer_aero_stability_mutation(child, rng)
        if sustainer_recovery_parents:
            # Motor choice is discrete; interpolation cannot discover the
            # stable-parent geometry with the recovery-parent braking motor.
            # Preserve that recovery module as a coherent building block.
            child["s0_retro"] = s0_right["s0_retro"]
            child["s0_aft_ballast_kg"] = s0_right.get("s0_aft_ballast_kg", 0.0)
            child["nose_mass_kg"] = max(
                0.8,
                min(float(child["nose_mass_kg"]), float(s0_right.get("nose_mass_kg", 99.0))),
            )
        if booster_recovery_parents:
            child["s1_retro"] = s1_right["s1_retro"]
            child["s1_aft_ballast_kg"] = s1_right.get("s1_aft_ballast_kg", 0.0)
        child.pop("s0_mid_ballast_kg", None)
        child.pop("s1_mid_ballast_kg", None)
        child["wind_levels"] = wind_levels
        child["main_cluster_count"] = 3
        child["s0_retro_delay"] = 200.0
        child["s1_retro_delay"] = 200.0
        child["s1_separation_delay"] = min(
            1.0, max(0.0, float(child.get("s1_separation_delay", 0.0)))
        )
        child["s1_ballast_kg"] = float(child.get("s1_aft_ballast_kg", 0.0))
        search._repair_podset_derived_geometry(child)
        # These legacy aliases are still model features. Keep them equal to
        # the native AST genes so the surrogate cannot learn from inert drift.
        for suffix in ("fin_count", "fin_sweep", "fin_root", "fin_height", "fin_thickness_m", "fin_material"):
            child[f"s0_{suffix}"] = child[f"s0_core_{suffix}"]
            child[f"s1_{suffix}"] = child[f"s1_core_{suffix}"]
        if not search._podset_geometry_violations(child):
            return child
    raise RuntimeError("stage-wise crossover could not produce buildable geometry")


def _acquisition(prediction) -> float:
    s0_margin, log_s0_tail, s1_margin, log_s1_tail, apogee, mach = prediction[:6]
    joint_terms = [
        s0_margin / 1.5,
        math.expm1(max(0.0, log_s0_tail)) / 3.0,
        s1_margin / 1.5,
        math.expm1(max(0.0, log_s1_tail)) / 3.0,
        max(-1.0, 1.0 - abs(apogee - 3000.0) / 1000.0),
    ]
    if len(prediction) >= 14:
        (
            s0_gap, s0_q, s0_vq, s0_duration,
            s1_gap, s1_q, s1_vq, s1_duration,
        ) = prediction[6:14]
        joint_terms.extend([
            1.0 + s0_gap / 100.0,
            s0_q / 0.70,
            s0_vq / 0.70,
            s0_duration,
            1.0 + s1_gap / 100.0,
            s1_q / 0.70,
            s1_vq / 0.70,
            s1_duration,
        ])
    elif len(prediction) >= 12:
        s0_gap, s0_q, s0_duration, s1_gap, s1_q, s1_duration = prediction[6:12]
        joint_terms.extend([
            1.0 + s0_gap / 100.0,
            s0_q / 0.70,
            s0_duration,
            1.0 + s1_gap / 100.0,
            s1_q / 0.70,
            s1_duration,
        ])
    joint = min(joint_terms)
    if mach >= 0.95:
        joint -= 4.0 * (mach - 0.95)
    return float(joint)


def _record_joint(record: dict) -> float:
    if "metrics" not in record:
        return -99.0
    terms = [
        float(record.get("minimum_ascent_margin_cal", -99.0)) / 1.5,
        max(
            -1.0,
            1.0 - abs(float(record["metrics"].get("apogee_m", 0.0)) - 3000.0) / 1000.0,
        ),
    ]
    opportunities = {
        item.get("stage_key"): item
        for item in record.get("landing_opportunities", [])
        if item.get("stage_key") in {"s0", "s1"}
    }
    if set(opportunities) != {"s0", "s1"}:
        return -99.0
    for stage_key in ("s0", "s1"):
        item = opportunities[stage_key]
        gap = float(item.get("available_delta_v_ms") or 0.0) - float(
            item.get("required_delta_v_ms") or 0.0
        )
        burn = max(1.0e-6, float(item.get("motor_burn_duration_s") or 0.0))
        terms.extend([
            1.0 + gap / 100.0,
            float(item.get("fraction_burn_opposing_total_velocity") or 0.0) / 0.70,
            float(item.get("fraction_burn_opposing_vertical_velocity") or 0.0) / 0.70,
            float(item.get("usable_tail_first_duration_s") or 0.0) / burn,
        ])
    return min(terms)


def _diverse_authority_selection(rust_survivors: list, limit: int) -> list:
    """Prevent one surrogate mode from consuming every authority call."""
    selected = []
    selected_ids = set()
    signatures = set()
    exploit_limit = max(1, int(limit * 0.75))
    for item in rust_survivors:
        parameters = item[0][0][1]
        signature = (
            int(parameters["s0_main"]), int(parameters["s0_retro"]),
            int(parameters.get("s0_core_fin_count", 0)),
            round(float(parameters.get("s0_core_fin_height", 0.0)), 2),
            round(float(parameters.get("nose_mass_kg", 0.0)) * 4.0) / 4.0,
            int(parameters.get("s0_grid_fin_count", 0)),
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        selected.append(item)
        selected_ids.add(item[0][0][0])
        if len(selected) >= exploit_limit:
            break
    # Reserve authority calls for the low-ballast/aft-aero mechanism. Without
    # this boundary quota the surrogate repeatedly filled every call with
    # small-fin braking phenotypes and never learned the intended morphology.
    exploration = sorted(
        rust_survivors,
        key=lambda item: (
            -abs(float(item[0][0][1].get("s0_core_fin_height", 0.0)) - 0.18),
            -abs(float(item[0][0][1].get("s0_core_length", 0.0)) - 0.90),
            -abs(float(item[0][0][1].get("nose_mass_kg", 0.0)) - 1.20),
            _acquisition(item[0][1]),
        ),
        reverse=True,
    )
    exploration_signatures = set()
    for item in exploration:
        candidate_id = item[0][0][0]
        parameters = item[0][0][1]
        fin_height = float(parameters.get("s0_core_fin_height", 0.0))
        signature = (
            int(parameters.get("s0_retro", -1)),
            int(parameters.get("s1_retro", -1)),
            round(float(parameters.get("nose_mass_kg", 0.0)) * 5.0),
        )
        if (
            candidate_id in selected_ids
            or not 0.12 <= fin_height <= 0.24
            or signature in exploration_signatures
        ):
            continue
        exploration_signatures.add(signature)
        selected.append(item)
        selected_ids.add(candidate_id)
        if len(selected) >= limit:
            return selected
    for item in rust_survivors:
        if item[0][0][0] not in selected_ids:
            selected.append(item)
            selected_ids.add(item[0][0][0])
            if len(selected) >= limit:
                break
    return selected


def _select_rust_inputs(ranked: list, limit: int) -> list:
    """Keep an explicit morphology-learning quota ahead of the Rust screen."""
    if len(ranked) <= limit:
        return ranked
    exploit_limit = max(1, int(limit * 0.80))
    selected = list(ranked[:exploit_limit])
    selected_ids = {item[0][0] for item in selected}
    signatures = set()
    for item in ranked[exploit_limit:]:
        candidate_id, parameters = item[0]
        signature = (
            round(float(parameters.get("nose_mass_kg", 0.0)) / 0.4),
            round(float(parameters.get("s0_core_fin_height", 0.0)) / 0.08),
            round(float(parameters.get("s0_core_length", 0.0)) / 0.15),
            int(parameters.get("s0_main", -1)),
            int(parameters.get("s0_retro", -1)),
        )
        if candidate_id in selected_ids or signature in signatures:
            continue
        signatures.add(signature)
        selected.append(item)
        selected_ids.add(candidate_id)
        if len(selected) >= limit:
            return selected
    for item in ranked:
        if item[0][0] not in selected_ids:
            selected.append(item)
            selected_ids.add(item[0][0])
            if len(selected) >= limit:
                break
    return selected


def _authority_record(parameters: dict, metrics: dict) -> dict:
    ascent_ok, ascent_violations = search._ascent_admissible(metrics, parameters)
    minimum_margin = _minimum_ascent_margin(metrics)
    if minimum_margin < 1.5:
        ascent_ok = False
        ascent_violations = [
            *ascent_violations,
            f"ascent segment margin {minimum_margin:.3f} cal is below 1.5",
        ]
    if not _genuine_staging(metrics):
        ascent_ok = False
        ascent_violations = [*ascent_violations, "separation is not before apogee"]
    opportunities = []
    for branch in (0, 1):
        trials = [
            search._landing_opportunity(metrics, parameters, branch, delay)
            for delay in search._delay_candidates(metrics, parameters, branch, limit=18)
        ]
        opportunities.append(max(
            trials,
            key=lambda item: (
                bool(item.get("usable")),
                float(item.get("available_delta_v_ms", 0.0))
                - float(item.get("required_delta_v_ms", 1.0e9)),
                float(item.get("fraction_burn_opposing_total_velocity", 0.0)),
            ),
            default={"usable": False, "rejection_reasons": ["no ignition candidates"]},
        ))
    return {
        "candidate_id": search._candidate_id(parameters),
        "parameters": parameters,
        "metrics": metrics,
        "ascent_admissible": ascent_ok,
        "ascent_violations": ascent_violations,
        "minimum_ascent_margin_cal": minimum_margin,
        "s0_tail_window_s": _tail_window(metrics, "s0"),
        "s1_tail_window_s": _tail_window(metrics, "s1"),
        "landing_opportunities": opportunities,
        "recovery_basin_pass": ascent_ok and len(opportunities) == 2
        and all(bool(item.get("usable")) for item in opportunities),
    }


def _source_digest() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return search._canonical_digest({
        str(path): search._sha256_file(path)
        for path in (
            Path(__file__),
            Path(__file__).with_name("osifog_engine_search.py"),
            Path(__file__).with_name("osifog_podset.py"),
            repo_root / "missions" / "osifog_l3_precision.json",
        )
    })


def run(args) -> dict:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output / "checkpoint.json"
    source_digest = _source_digest()
    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if state.get("source_digest") != source_digest:
            raise RuntimeError("campaign source drift; use a new output directory")
    else:
        state = {
            "version": 1, "source_digest": source_digest,
            "seed": args.seed, "completed_cycles": 0, "records": [],
            "cycle_summaries": [],
        }
    wind_levels = search.osifog_sweep.parse_wind_csv(args.wind_csv)
    for cycle in range(int(state["completed_cycles"]), args.cycles):
        search._write_health(output, "running", "history_and_surrogate", cycle=cycle + 1)
        history = load_history(args.designs, state["records"])
        parent_history = load_history(
            args.designs, state["records"], current_geometry_only=False
        )
        if len(history) < 16:
            raise RuntimeError(f"only {len(history)} current-geometry authority records; need at least 16")
        vectorizer = DictVectorizer(sparse=False)
        matrix = vectorizer.fit_transform([_features(item["parameters"]) for item in history])
        targets = [_targets(item["metrics"]) for item in history]
        workers = max(1, math.floor((os.cpu_count() or 2) * 0.70))
        model = ExtraTreesRegressor(
            n_estimators=args.trees,
            min_samples_leaf=(1 if len(history) < 80 else 2),
            max_features=0.80, random_state=args.seed + cycle, n_jobs=workers,
        )
        model.fit(matrix, targets)
        opportunity_history = [
            item for item in history if _opportunity_targets(item) is not None
        ]
        opportunity_model = None
        if len(opportunity_history) >= 12:
            opportunity_model = ExtraTreesRegressor(
                n_estimators=args.trees, min_samples_leaf=1,
                max_features=0.90, random_state=args.seed + cycle + 700001,
                n_jobs=workers,
            )
            opportunity_model.fit(
                vectorizer.transform([
                    _features(item["parameters"]) for item in opportunity_history
                ]),
                [_opportunity_targets(item) for item in opportunity_history],
            )
        sustainer_parents = [
            item["parameters"] for item in sorted(
                parent_history, key=_sustainer_ascent_quality, reverse=True
            )[:48]
        ]
        sustainer_recovery_parents = _recovery_parent_parameters(
            parent_history, "s0", 48
        )
        booster_parents = [
            item["parameters"] for item in sorted(
                parent_history, key=lambda item: _stage_quality(item, "s1"), reverse=True
            )[:32]
        ]
        booster_recovery_parents = _recovery_parent_parameters(
            parent_history, "s1", 32
        )
        rng = random.Random(args.seed + cycle * 100003)
        donor_pool = [
            search._sample_valid_parameters(rng, wind_levels) for _ in range(96)
        ]
        known_ids = {item["candidate_id"] for item in history}
        proposals = {}
        while len(proposals) < args.proposals:
            proposal = stagewise_proposal(
                rng, wind_levels, sustainer_parents, booster_parents,
                donor_pool=donor_pool,
                sustainer_recovery_parents=sustainer_recovery_parents,
                booster_recovery_parents=booster_recovery_parents,
            )
            candidate_id = search._candidate_id(proposal)
            if candidate_id not in known_ids:
                proposals[candidate_id] = proposal
        proposal_items = list(proposals.items())
        prediction_matrix = vectorizer.transform([
            _features(parameters) for _, parameters in proposal_items
        ])
        tree_predictions = np.stack([
            estimator.predict(prediction_matrix) for estimator in model.estimators_
        ])
        prediction_mean = tree_predictions.mean(axis=0)
        prediction_std = tree_predictions.std(axis=0)
        # Conservative confidence-adjusted estimates keep extrapolative
        # false positives from consuming sequential OpenRocket calls.
        predictions = prediction_mean.copy()
        predictions[:, 0] -= 0.50 * prediction_std[:, 0]
        predictions[:, 1] -= 0.25 * prediction_std[:, 1]
        predictions[:, 2] -= 0.50 * prediction_std[:, 2]
        predictions[:, 3] -= 0.25 * prediction_std[:, 3]
        predictions[:, 5] += 0.50 * prediction_std[:, 5]
        if opportunity_model is not None:
            opportunity_tree_predictions = np.stack([
                estimator.predict(prediction_matrix)
                for estimator in opportunity_model.estimators_
            ])
            opportunity_predictions = opportunity_tree_predictions.mean(axis=0)
            opportunity_std = opportunity_tree_predictions.std(axis=0)
            opportunity_predictions[:, 0] -= 0.50 * opportunity_std[:, 0]
            opportunity_predictions[:, 1:4] -= 0.25 * opportunity_std[:, 1:4]
            opportunity_predictions[:, 4] -= 0.50 * opportunity_std[:, 4]
            opportunity_predictions[:, 5:8] -= 0.25 * opportunity_std[:, 5:8]
            predictions = np.concatenate(
                (predictions, opportunity_predictions), axis=1
            )
        ranked = sorted(
            zip(proposal_items, predictions),
            key=lambda item: _acquisition(item[1]), reverse=True,
        )
        rust_inputs = _select_rust_inputs(ranked, args.rust_finalists)
        rust_parameters = [item[0][1] for item in rust_inputs]
        search._write_health(
            output, "running", "rust_screen", cycle=cycle + 1,
            proposals=len(proposals), finalists=len(rust_parameters),
        )
        rust_results = search._default_rust_evaluator(
            [search.parameters_to_ast(item) for item in rust_parameters],
            rust_parameters, execution_profile="super-speed", simulation_phase="ascent",
        )
        rust_survivors = []
        for ranked_item, result in zip(rust_inputs, rust_results):
            if result.status == "success":
                rust_survivors.append((ranked_item, result))
        rust_survivors.sort(
            key=lambda item: (_acquisition(item[0][1]), item[1].score), reverse=True
        )
        authority_inputs = _diverse_authority_selection(
            rust_survivors, args.authority_finalists
        )
        if not authority_inputs:
            raise RuntimeError("Rust screen produced no ascent-legal surrogate finalists")
        search._write_health(
            output, "running", "openrocket_authority", cycle=cycle + 1,
            finalists=len(authority_inputs), completed=0,
        )
        cycle_records = []
        failures = 0
        for index, ((candidate_pair, prediction), rust_result) in enumerate(authority_inputs):
            candidate_id, parameters = candidate_pair
            try:
                metrics = search._isolated_recovery_gate_evaluator(parameters)
                record = _authority_record(parameters, metrics)
                record["predicted"] = list(map(float, prediction))
                record["rust"] = {
                    "score": rust_result.score,
                    "apogee_m": rust_result.rust_apogee_m,
                    "mach": rust_result.rust_mach,
                    "min_static_margin": rust_result.rust_min_static_margin,
                }
            except Exception as exc:
                failures += 1
                record = {
                    "candidate_id": candidate_id, "parameters": parameters,
                    "error": f"{type(exc).__name__}: {exc}",
                    "recovery_basin_pass": False,
                }
            state["records"].append(record)
            cycle_records.append(record)
            search._atomic_json(checkpoint_path, state)
            search._write_health(
                output, "running", "openrocket_authority", cycle=cycle + 1,
                finalists=len(authority_inputs), completed=index + 1, failures=failures,
            )
            if sum(bool(item.get("recovery_basin_pass")) for item in cycle_records) >= 2:
                break
        valid = [item for item in cycle_records if "metrics" in item]
        best_joint = max((_record_joint(item) for item in valid), default=-99.0)
        summary = {
            "cycle": cycle + 1, "history_records": len(history),
            "parent_records": len(parent_history),
            "opportunity_training_records": len(opportunity_history),
            "proposals": len(proposals), "rust_survivors": len(rust_survivors),
            "authority_completed": len(cycle_records), "authority_failures": failures,
            "best_joint_proxy": best_joint,
            "basin_passes": sum(bool(item.get("recovery_basin_pass")) for item in cycle_records),
            "completed_at": _now(),
        }
        state["cycle_summaries"].append(summary)
        state["completed_cycles"] = cycle + 1
        search._atomic_json(checkpoint_path, state)
        if summary["basin_passes"]:
            break
        recent = state["cycle_summaries"][-3:]
        if len(recent) == 3 and max(item["best_joint_proxy"] for item in recent) - min(
            item["best_joint_proxy"] for item in recent
        ) < 0.02:
            state["stop_reason"] = "three_cycle_joint_gate_stagnation"
            break
        if failures > len(cycle_records) / 2:
            state["stop_reason"] = "authority_failure_rate_exceeded_50_percent"
            break
    passes = [item for item in state["records"] if item.get("recovery_basin_pass")]
    state["gate_passed"] = bool(passes)
    state["passes"] = passes
    state.setdefault("stop_reason", "recovery_basin_found" if passes else "cycle_budget_exhausted")
    search._atomic_json(output / "result.json", state)
    search._atomic_json(checkpoint_path, state)
    search._write_health(
        output, "complete", "finished", gate_passed=bool(passes),
        completed_cycles=state["completed_cycles"], stop_reason=state["stop_reason"],
    )
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--designs", type=Path, default=Path("designs"))
    parser.add_argument("--wind-csv", type=Path, default=Path("OSIFOG/OpenWind_File.csv"))
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--proposals", type=int, default=3000)
    parser.add_argument("--rust-finalists", type=int, default=500)
    parser.add_argument("--authority-finalists", type=int, default=12)
    parser.add_argument("--trees", type=int, default=160)
    parser.add_argument("--seed", type=int, default=16000)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({
        "gate_passed": result["gate_passed"],
        "cycles": result["completed_cycles"],
        "records": len(result["records"]),
        "stop_reason": result["stop_reason"],
    }, indent=2))
    return 0 if result["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
