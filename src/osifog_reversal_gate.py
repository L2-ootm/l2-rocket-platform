#!/usr/bin/env python3
"""Bounded, resumable OpenRocket gate for propellant-driven CG reversal."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import osifog_engine_search as search


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


def _s0_tail_window(metrics: dict) -> float:
    return _tail_window(metrics, "s0")


def _ascent_margin(metrics: dict) -> float:
    values = []
    for item in metrics.get("ascent_stability_segments", []):
        value = item.get("min_calibers")
        if value is not None:
            values.append(float(value))
    return min(values, default=-1.0e9)


def _select_tail_seed(result: dict) -> dict:
    valid = [
        item for item in (
            result.get("openrocket_results", []) + result.get("records", [])
        ) if "metrics" in item
    ]
    if not valid:
        raise RuntimeError("seed result has no completed OpenRocket records")
    return max(valid, key=lambda item: (
        _s0_tail_window(item["metrics"]),
        _ascent_margin(item["metrics"]),
        float(item["metrics"].get("apogee_m", 0.0)),
    ))["parameters"]


def _axial_variants(base: dict) -> list[dict]:
    probe = dict(base)
    search._repair_podset_derived_geometry(probe)
    nose = float(probe["s0_pod_nose_length"])
    lower = -nose
    upper = (
        float(probe["s0_core_length"])
        - nose
        - float(probe["s0_pod_length"])
    )
    axial_values = [lower + (upper - lower) * index / 5.0 for index in range(6)]
    nose_values = sorted({
        float(base.get("nose_mass_kg", 0.04)), 0.04, 0.20, 0.50, 1.00, 2.00,
    })
    result = []
    for axial in axial_values:
        for nose_mass in nose_values:
            item = dict(base)
            item["s0_pod_axial_offset_m"] = axial
            item["nose_mass_kg"] = nose_mass
            search._repair_podset_derived_geometry(item)
            if not search._podset_geometry_violations(item):
                result.append(item)
    return result


def _fin_variants(base: dict) -> list[dict]:
    """Increase aft CP authority around the best long-tail phenotype."""
    roots = sorted({float(base.get("s0_core_fin_root", 0.03)), 0.06, 0.10, 0.16, 0.24})
    pod_roots = sorted({float(base.get("s0_pod_fin_root", 0.08)), 0.14, 0.22})
    nose_masses = sorted({float(base.get("nose_mass_kg", 0.04)), 0.04, 0.20, 0.50})
    result = []
    for root in roots:
        for pod_root in pod_roots:
            for nose_mass in nose_masses:
                item = dict(base)
                item["s0_core_fin_root"] = root
                item["s0_fin_root"] = root
                item["s0_pod_fin_root"] = pod_root
                item["nose_mass_kg"] = nose_mass
                search._repair_podset_derived_geometry(item)
                if not search._podset_geometry_violations(item):
                    result.append(item)
    return result


def _span_variants(base: dict) -> list[dict]:
    """Add aft normal-force authority without extending the root forward."""
    heights = sorted({float(base.get("s0_core_fin_height", 0.30)), 0.40, 0.55, 0.70, 0.85, 1.00})
    counts = sorted({int(base.get("s0_core_fin_count", 3)), 4, 6})
    nose_masses = sorted({float(base.get("nose_mass_kg", 0.04)), 0.04, 0.20})
    result = []
    for height in heights:
        for count in counts:
            for nose_mass in nose_masses:
                item = dict(base)
                item["s0_core_fin_height"] = height
                item["s0_fin_height"] = height
                item["s0_core_fin_count"] = count
                item["s0_fin_count"] = count
                item["nose_mass_kg"] = nose_mass
                search._repair_podset_derived_geometry(item)
                if not search._podset_geometry_violations(item):
                    result.append(item)
    return result


def _motor_variants(base: dict) -> list[dict]:
    """Increase forward consumable mass while retaining the dry tail basin."""
    index_by_designation = {
        row[1]: index for index, row in enumerate(search.MOTOR_DATABASE)
    }
    sustainer_designations = (
        "J420R", "K550W", "K700W", "K1050W", "K510", "L1000", "L1150",
    )
    sustainer_motors = [
        index_by_designation[name] for name in sustainer_designations
        if name in index_by_designation
    ]
    nose_masses = sorted({float(base.get("nose_mass_kg", 0.04)), 0.04, 0.20})
    result = []
    for motor in sustainer_motors:
        for nose_mass in nose_masses:
            item = dict(base)
            item["s0_main"] = motor
            item["nose_mass_kg"] = nose_mass
            search._repair_podset_derived_geometry(item)
            # Keep the pod body at the aft-most supported station. This is
            # where the axial sweep maximized both margin and tail duration.
            item["s0_pod_axial_offset_m"] = (
                float(item["s0_core_length"])
                - float(item["s0_pod_nose_length"])
                - float(item["s0_pod_length"])
            )
            search._repair_podset_derived_geometry(item)
            if not search._podset_geometry_violations(item):
                result.append(item)
    return result


def _delayed_variants(base: dict) -> list[dict]:
    """Keep the complete lower 3+1 stage attached through ascent.

    Only the normal axial stage joint is delayed.  POD nodes remain permanent
    children of their host stages.  The two cages are staggered by 60 degrees,
    and the shared geometry repair expands the sustainer cage when necessary
    to prove conservative exhaust-plume clearance through the lower stage.
    """
    base_height = float(base.get("s1_core_fin_height", 0.35))
    heights = sorted({base_height, 0.50, 0.70, 0.90, 1.10})
    # Sweep structural authority at one near-apogee delay before spending
    # authority calls refining timing around a structurally viable point.
    delays = (14.0, 12.0, 16.0, 10.0, 18.0)
    result = []
    for delay in delays:
        for height in heights:
            item = dict(base)
            item["nose_mass_kg"] = 0.04
            item["s1_core_fin_count"] = 3
            item["s1_fin_count"] = 3
            item["s1_core_fin_height"] = height
            item["s1_fin_height"] = height
            item["s1_core_fin_angle_offset_deg"] = 0.0
            item["s1_separation_delay"] = delay
            item["s0_pod_angle_offset_deg"] = 60.0
            item["s1_pod_angle_offset_deg"] = 0.0
            search._repair_podset_derived_geometry(item)
            if not search._podset_geometry_violations(item):
                result.append(item)
    return result


def _delayed_chord_variants(base: dict) -> list[dict]:
    """Second structural slice around the span optimum measured by v2."""
    result = []
    for height in (0.60, 0.70, 0.80, 0.90):
        for root in (0.25, 0.40, 0.55, 0.70):
            item = dict(base)
            item["nose_mass_kg"] = 0.04
            item["s1_core_fin_count"] = 3
            item["s1_fin_count"] = 3
            item["s1_core_fin_height"] = height
            item["s1_fin_height"] = height
            item["s1_core_fin_root"] = root
            item["s1_fin_root"] = root
            item["s1_core_fin_angle_offset_deg"] = 0.0
            item["s1_separation_delay"] = 14.0
            item["s0_pod_angle_offset_deg"] = 60.0
            item["s1_pod_angle_offset_deg"] = 0.0
            search._repair_podset_derived_geometry(item)
            if not search._podset_geometry_violations(item):
                result.append(item)
    return result


def _delayed_trim_variants(base: dict) -> list[dict]:
    """Move ascent authority aft by trimming destabilizing mid-stack fins."""
    result = []
    for upper_height in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30):
        for lower_height in (0.65, 0.80, 0.95):
            item = dict(base)
            item["nose_mass_kg"] = 0.04
            item["s0_core_fin_height"] = upper_height
            item["s0_fin_height"] = upper_height
            if upper_height == 0.0:
                item["s0_core_fin_count"] = 0
                item["s0_fin_count"] = 0
            item["s1_core_fin_count"] = 3
            item["s1_fin_count"] = 3
            item["s1_core_fin_height"] = lower_height
            item["s1_fin_height"] = lower_height
            item["s1_core_fin_root"] = 0.25
            item["s1_fin_root"] = 0.25
            item["s1_core_fin_angle_offset_deg"] = 0.0
            item["s1_separation_delay"] = 14.0
            item["s0_pod_angle_offset_deg"] = 60.0
            item["s1_pod_angle_offset_deg"] = 0.0
            upper_gap = max(
                0.010,
                upper_height + 0.008,
                float(item.get("s0_grid_fin_height", 0.0)) + 0.008,
                float(item.get("s0_pod_fin_height", 0.0)) + 0.008,
            )
            item["s0_pod_radial_offset"] = (
                float(item["s0_core_radius"])
                + float(item["s0_pod_radius"])
                + upper_gap
            )
            search._repair_podset_derived_geometry(item)
            if not search._podset_geometry_violations(item):
                result.append(item)
    return result


def _separates_before_apogee(metrics: dict, tolerance_s: float = 0.0) -> bool:
    """OSIFOG hard rule R-002: genuine stage separation precedes apogee."""
    branches = metrics.get("branch_event_times", [])
    if not branches:
        return False
    primary = branches[0]
    apogees = [float(value) for value in primary.get("APOGEE", [])]
    separations = [float(value) for value in primary.get("STAGE_SEPARATION", [])]
    return bool(apogees and separations and min(separations) + tolerance_s < min(apogees))


def _record_passes(record: dict, delayed: bool = False) -> bool:
    if (
        record.get("ascent_margin_cal", -1.0e9) < 1.5
        or record.get("s0_tail_window_s", 0.0) < 5.0
        or float(record.get("metrics", {}).get("mach", 1.0e9)) >= 0.95
    ):
        return False
    if not delayed:
        return True
    return (
        record.get("s1_tail_window_s", 0.0) >= 5.0
        and record.get("separates_before_apogee", False)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=(
            "axial", "fin", "span", "motor", "delayed", "delayed_chord",
            "delayed_trim",
        ),
        default="axial",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = args.output / "checkpoint.json"
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint_path.exists() else {"version": 1, "records": []}
    )
    completed = {item["candidate_id"] for item in checkpoint["records"]}
    seed_payload = json.loads(args.seed_result.read_text(encoding="utf-8"))
    base = _select_tail_seed(seed_payload)
    variants = {
        "axial": _axial_variants,
        "fin": _fin_variants,
        "span": _span_variants,
        "motor": _motor_variants,
        "delayed": _delayed_variants,
        "delayed_chord": _delayed_chord_variants,
        "delayed_trim": _delayed_trim_variants,
    }[args.mode](base)
    delayed_gate = args.mode.startswith("delayed")
    for index, parameters in enumerate(variants):
        candidate_id = search._candidate_id(parameters)
        if candidate_id in completed:
            continue
        health = {
            "status": "running", "phase": "recovery_gate",
            "completed": len(checkpoint["records"]), "total": len(variants),
            "candidate": index, "updated_at": _now(),
        }
        search._atomic_json(args.output / "health.json", health)
        try:
            metrics = search._isolated_recovery_gate_evaluator(parameters)
            record = {
                "candidate_id": candidate_id, "parameters": parameters,
                "metrics": metrics, "ascent_margin_cal": _ascent_margin(metrics),
                "s0_tail_window_s": _s0_tail_window(metrics),
                "s1_tail_window_s": _tail_window(metrics, "s1"),
                "separates_before_apogee": _separates_before_apogee(metrics),
            }
        except Exception as exc:
            record = {
                "candidate_id": candidate_id, "parameters": parameters,
                "error": f"{type(exc).__name__}: {exc}",
            }
        checkpoint["records"].append(record)
        search._atomic_json(checkpoint_path, checkpoint)
        current_passes = [
            item for item in checkpoint["records"]
            if _record_passes(item, delayed=delayed_gate)
        ]
        # Three independent points are enough to prove a basin exists; stop
        # the topology gate and hand the basin to the campaign optimizer.
        if len(current_passes) >= 3:
            break
    passes = [
        item for item in checkpoint["records"]
        if _record_passes(item, delayed=delayed_gate)
    ]
    result = {
        **checkpoint,
        "gate_passed": bool(passes),
        "passes": sorted(
            passes,
            key=lambda item: (
                item["s0_tail_window_s"], item["ascent_margin_cal"],
                float(item["metrics"].get("apogee_m", 0.0)),
            ),
            reverse=True,
        ),
    }
    search._atomic_json(args.output / "result.json", result)
    search._atomic_json(args.output / "health.json", {
        "status": "complete", "phase": "finished", "gate_passed": bool(passes),
        "completed": len(checkpoint["records"]), "total": len(variants),
        "updated_at": _now(),
    })
    print(json.dumps({
        "gate_passed": bool(passes), "passes": len(passes),
        "evaluated": len(checkpoint["records"]), "output": str(args.output),
    }, indent=2))
    return 0 if passes else 2


if __name__ == "__main__":
    raise SystemExit(main())
