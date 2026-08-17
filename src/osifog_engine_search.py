"""Unattended two-authority optimizer for the OSIFOG Level 3 Falcon.

Rust screens ascent/topology cheaply through the organic AST contract.
OpenRocket remains authoritative for retro descent, hard gates, and score.
"""

from __future__ import annotations

import argparse
import base64
from bisect import bisect_left
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
import os
import pickle
import random
import socket
import subprocess
import statistics
import sys
import time
from functools import lru_cache
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ckg_memory import ContinuousKnowledgeGraph
from organic_loop import (
    OrganicCandidate,
    OrganicLoopConfig,
    evaluate_rust_population,
    extract_topological_signature,
    rust_available_motor_indices,
)
from rocket_ast import ASTNode
from rocket_forge import MOTOR_DATABASE
import osifog_sweep
import osifog_podset


REPO_ROOT = Path(__file__).resolve().parents[1]
MISSION_PATH = REPO_ROOT / "missions" / "osifog_l3_precision.json"
SCENARIO_TYPES = {
    "OFFICIAL_FULL_MISSION",
    "EXPOSED_SUSTAINER_ASCENT",
    "STAGE_FREE_DESCENT_DIAGNOSTIC",
    "POWERED_STAGE_LANDING_VALIDATION",
    "DELAY_ROBUSTNESS",
    "DEBUG_ONLY",
}
EXPECTED_MAIN_IGNITION = {"s0_main": "burnout", "s1_main": "launch"}
AUTHORITY_TIMESTEP_LADDER_S = (0.05, 0.02, 0.01, 0.005, 0.001)
AUTHORITY_CONVERGENCE_LIMITS = {
    "apogee_m": 0.25,
    "mach": 0.002,
    "landing_speed_ms": 0.10,
    "landing_position_m": 0.50,
    "score": 500.0,
}


def _distance_to_radial_segment(
    radius: float, angle_deg: float, inner: float, outer: float,
    segment_angle_deg: float,
) -> float:
    """Plan-view distance from a plume axis to one finite radial fin."""
    delta = math.radians(angle_deg - segment_angle_deg)
    projection = radius * math.cos(delta)
    if inner <= projection <= outer:
        return abs(radius * math.sin(delta))
    endpoint = inner if projection < inner else outer
    return math.sqrt(max(
        0.0,
        radius * radius + endpoint * endpoint
        - 2.0 * radius * endpoint * math.cos(delta),
    ))


def _interstage_plume_violations(parameters: dict) -> list[str]:
    """Check all upper exhaust columns through the attached lower cage."""
    if float(parameters.get("s1_separation_delay", 0.0)) <= 1.0e-9:
        return []
    upper_radius = float(parameters["s0_pod_radial_offset"])
    upper_base = float(parameters.get("s0_pod_angle_offset_deg", 0.0))
    lower_radius = float(parameters["s1_pod_radial_offset"])
    lower_base = float(parameters.get("s1_pod_angle_offset_deg", 0.0))
    upper_body_bottom = (
        float(parameters.get("s0_pod_axial_offset_m", 0.0))
        + float(parameters.get("s0_pod_nose_length", 0.0))
        + float(parameters["s0_pod_length"])
    )
    plume_distance = (
        max(0.0, float(parameters["s0_core_length"]) - upper_body_bottom)
        + float(parameters["s1_core_length"])
    )
    motor_radius = float(MOTOR_DATABASE[int(parameters["s0_main"])][2]) / 2.0
    plume_radius = motor_radius + math.tan(math.radians(12.0)) * plume_distance
    required = plume_radius + 0.001
    violations = []
    if upper_radius - float(parameters["s1_core_radius"]) < required - 1.0e-9:
        violations.append("interstage sustainer plume intersects the booster core")

    upper_angles = [upper_base + 120.0 * index for index in range(3)]
    lower_angles = [lower_base + 120.0 * index for index in range(3)]
    lower_pod_radius = float(parameters["s1_pod_radius"])
    pod_hit = False
    for upper_angle in upper_angles:
        for lower_angle in lower_angles:
            delta = math.radians(upper_angle - lower_angle)
            distance = math.sqrt(max(
                0.0,
                upper_radius * upper_radius + lower_radius * lower_radius
                - 2.0 * upper_radius * lower_radius * math.cos(delta),
            ))
            if distance < lower_pod_radius + required - 1.0e-9:
                pod_hit = True
                break
        if pod_hit:
            break
    if pod_hit:
        violations.append(
            "interstage sustainer plume intersects a booster pod; stagger or "
            "expand the cages before delayed separation"
        )

    fin_count = int(parameters.get("s1_core_fin_count", 0))
    if fin_count > 0:
        fin_inner = float(parameters["s1_core_radius"])
        fin_outer = fin_inner + float(parameters.get("s1_core_fin_height", 0.0))
        fin_base = float(parameters.get("s1_core_fin_angle_offset_deg", 0.0))
        half_thickness = float(parameters.get("s1_core_fin_thickness_m", 0.003)) / 2.0
        fin_hit = False
        for upper_angle in upper_angles:
            for index in range(fin_count):
                if _distance_to_radial_segment(
                    upper_radius, upper_angle, fin_inner, fin_outer,
                    fin_base + 360.0 * index / fin_count,
                ) < required + half_thickness - 1.0e-9:
                    fin_hit = True
                    break
            if fin_hit:
                break
        if fin_hit:
            violations.append(
                "interstage sustainer plume intersects a discrete booster core fin"
            )
    return violations


def _finset_axially_overlaps_pod(
    parameters: dict, prefix: str, finset: str
) -> bool:
    """Return whether one core-mounted fin set shares an axial station with a pod."""
    core_length = float(parameters[f"{prefix}_core_length"])
    fin_root = float(parameters.get(f"{prefix}_{finset}_root", 0.0))
    default_top = max(0.0, core_length - fin_root) if finset == "core_fin" else 0.0
    fin_top = float(
        parameters.get(f"{prefix}_{finset}_position_m", default_top)
    )
    fin_bottom = fin_top + fin_root
    pod_top = float(parameters.get(f"{prefix}_pod_axial_offset_m", 0.0))
    pod_bottom = (
        pod_top
        + float(parameters.get(f"{prefix}_pod_nose_length", 0.0))
        + float(parameters[f"{prefix}_pod_length"])
    )
    return fin_top < pod_bottom - 1.0e-9 and pod_top < fin_bottom - 1.0e-9


def _finset_intersects_pod(
    parameters: dict, prefix: str, finset: str
) -> bool:
    """Check finite, discrete core-mounted fins against all pod tubes in 3D."""
    count = int(parameters.get(f"{prefix}_{finset}_count", 0))
    height = float(parameters.get(f"{prefix}_{finset}_height", 0.0))
    if (
        count <= 0
        or height <= 0.0
        or not _finset_axially_overlaps_pod(parameters, prefix, finset)
    ):
        return False
    pod_radius = float(parameters[f"{prefix}_pod_radius"])
    pod_offset = float(parameters[f"{prefix}_pod_radial_offset"])
    pod_base = float(parameters.get(f"{prefix}_pod_angle_offset_deg", 0.0))
    fin_base = float(
        parameters.get(f"{prefix}_{finset}_angle_offset_deg", 0.0)
    )
    inner = float(parameters[f"{prefix}_core_radius"])
    outer = inner + height
    required = pod_radius + float(
        parameters.get(f"{prefix}_{finset}_thickness_m", 0.003)
    ) / 2.0 + 0.001
    for pod_index in range(3):
        pod_angle = pod_base + 120.0 * pod_index
        for fin_index in range(count):
            fin_angle = fin_base + 360.0 * fin_index / count
            if _distance_to_radial_segment(
                pod_offset, pod_angle, inner, outer, fin_angle
            ) < required - 1.0e-9:
                return True
    return False


def _core_fin_axially_overlaps_pod(parameters: dict, prefix: str) -> bool:
    return _finset_axially_overlaps_pod(parameters, prefix, "core_fin")


def _core_fin_intersects_pod(parameters: dict, prefix: str) -> bool:
    return _finset_intersects_pod(parameters, prefix, "core_fin")


def _podset_geometry_violations(parameters: dict) -> list[str]:
    violations = []
    for prefix in ("s0", "s1"):
        violations.extend(osifog_podset.podset_buildability_violations(parameters, prefix))
        required = (
            float(parameters[f"{prefix}_core_radius"])
            + float(parameters[f"{prefix}_pod_radius"])
        )
        offset = float(parameters[f"{prefix}_pod_radial_offset"])
        clearance = offset - required
        if clearance < 0.001 - 1e-9:
            violations.append(
                f"{prefix}: pod/core radial clearance {clearance:.4f}m is below 1mm"
            )
        core_fin_height = float(parameters.get(f"{prefix}_core_fin_height", 0.0))
        if _core_fin_intersects_pod(parameters, prefix):
            violations.append(
                f"{prefix}: a discrete core fin intersects the pod envelope "
                f"(height {core_fin_height:.4f}m, clearance {clearance:.4f}m)"
            )
        grid_fin_height = float(parameters.get(f"{prefix}_grid_fin_height", 0.0))
        if _finset_intersects_pod(parameters, prefix, "grid_fin"):
            violations.append(
                f"{prefix}: a discrete forward fin intersects the pod envelope "
                f"(height {grid_fin_height:.4f}m, clearance {clearance:.4f}m)"
            )
        grid_fin_end = (
            float(parameters.get(f"{prefix}_grid_fin_position_m", 0.0))
            + float(parameters.get(f"{prefix}_grid_fin_root", 0.0))
        )
        if int(parameters.get(f"{prefix}_grid_fin_count", 0)) > 0 and grid_fin_end > float(parameters[f"{prefix}_core_length"]) + 1e-9:
            violations.append(
                f"{prefix}: forward fins end at {grid_fin_end:.4f}m beyond the core"
            )
        pod_top = float(parameters.get(f"{prefix}_pod_axial_offset_m", 0.0))
        pod_nose = float(parameters.get(f"{prefix}_pod_nose_length", 0.0))
        pod_body_top = pod_top + pod_nose
        pod_body_bottom = pod_body_top + float(parameters[f"{prefix}_pod_length"])
        core_length = float(parameters[f"{prefix}_core_length"])
        core_fin_root = float(parameters.get(f"{prefix}_core_fin_root", 0.0))
        if int(parameters.get(f"{prefix}_core_fin_count", 0)) > 0 and core_fin_root > core_length + 1.0e-9:
            violations.append(
                f"{prefix}: core fin root {core_fin_root:.4f}m exceeds the "
                f"{core_length:.4f}m body attachment length"
            )
        pod_fin_root = float(parameters.get(f"{prefix}_pod_fin_root", 0.0))
        if int(parameters.get(f"{prefix}_pod_fin_count", 0)) > 0 and pod_fin_root > float(parameters[f"{prefix}_pod_length"]) + 1.0e-9:
            violations.append(
                f"{prefix}: pod fin root {pod_fin_root:.4f}m exceeds the pod body"
            )
        if pod_body_top < -1.0e-9 or pod_body_bottom > core_length + 1.0e-9:
            violations.append(
                f"{prefix}: pod cylindrical body [{pod_body_top:.4f}, "
                f"{pod_body_bottom:.4f}]m is not fully supported by the core"
            )
        try:
            osifog_podset.pylon_stations_m(
                core_length,
                float(parameters[f"{prefix}_pod_length"]),
                int(parameters.get(f"{prefix}_pylon_station_count", 2)),
                pod_top,
                pod_nose,
                float(parameters.get(f"{prefix}_pylon_chord_m", 0.025)),
            )
        except ValueError as exc:
            violations.append(f"{prefix}: {exc}")
        nozzle_forward_m = max(0.0, core_length - pod_body_bottom)
        motor_radius = float(MOTOR_DATABASE[int(parameters[f"{prefix}_main"])][2]) / 2.0
        plume_radius_at_core_tail = motor_radius + math.tan(math.radians(12.0)) * nozzle_forward_m
        plume_axis_clearance = offset - float(parameters[f"{prefix}_core_radius"])
        if plume_axis_clearance < plume_radius_at_core_tail + 0.001 - 1.0e-9:
            violations.append(
                f"{prefix}: pod exhaust plume clearance {plume_axis_clearance:.4f}m "
                f"is below conservative envelope {plume_radius_at_core_tail + 0.001:.4f}m"
            )
    violations.extend(_interstage_plume_violations(parameters))
    if abs(
        float(parameters["s0_core_radius"])
        - float(parameters["s1_core_radius"])
    ) > 1.0e-9:
        violations.append("PodSet core radii differ without a physical transition")
    height = osifog_podset.podset_total_height_m(parameters)
    if height > osifog_sweep.MAX_HEIGHT_M + 1.0e-9:
        violations.append(
            f"PodSet rocket height {height:.3f} m exceeds "
            f"{osifog_sweep.MAX_HEIGHT_M:.1f} m"
        )
    if not violations:
        try:
            osifog_podset.generate_podset_ork(parameters)
        except (KeyError, TypeError, ValueError) as exc:
            violations.append(str(exc))
    return violations


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candidate_id(parameters: dict) -> str:
    """Stable physical identity used by checkpoints and authority caching."""
    return _canonical_digest(parameters)


def build_scenario_manifest(
    scenario_type: str, candidate_id: str, parameters: dict, ork_xml: str
) -> dict:
    if scenario_type not in SCENARIO_TYPES:
        raise ValueError(f"missing or invalid scenario type: {scenario_type!r}")
    import xml.etree.ElementTree as ET

    root = ET.fromstring(ork_xml)
    stage_nodes = root.findall(".//rocket/subcomponents/stage")
    stages = [
        {
            "name": stage.findtext("name"),
            "id": stage.findtext("id"),
        }
        for stage in stage_nodes
    ]
    compiled_motors = {}
    stage_key_by_name = {"Sustainer": "s0", "Booster": "s1"}
    for stage in stage_nodes:
        stage_name = stage.findtext("name")
        stage_key = stage_key_by_name.get(stage_name)
        if stage_key is None:
            continue
        for mount in stage.findall(".//innertube"):
            mount_name = mount.findtext("name") or ""
            motor_mount = mount.find("motormount")
            if motor_mount is None:
                continue
            if "Pod Motor Mount" in mount_name or "Main Motor Mount" in mount_name:
                role = "main"
            elif "Central Retro Mount" in mount_name or "Retro" in mount_name:
                role = "retro"
            else:
                continue
            compiled_motors[f"{stage_key}_{role}"] = {
                "stage_name": stage_name,
                "mount_name": mount_name,
                "ignition_event": motor_mount.findtext("ignitionevent"),
                "ignition_delay_s": float(
                    motor_mount.findtext("ignitiondelay") or 0.0
                ),
                "designation": motor_mount.findtext("motor/designation"),
            }
    anti_tumble = osifog_sweep.inspect_anti_tumble_xml(ork_xml)
    stage_ring_counts = {
        stage_key_by_name.get(stage.findtext("name")): len(
            stage.findall(".//centeringring")
        )
        for stage in stage_nodes
        if stage_key_by_name.get(stage.findtext("name")) is not None
    }
    motor_delays = [
        (node.text or "").strip().lower()
        for node in root.findall(".//motormount/motor/delay")
    ]
    nose = root.find(".//rocket/subcomponents/stage/subcomponents/nosecone")
    nose_shell_bonded = False
    if nose is not None:
        ballast = nose.find("./subcomponents/bulkhead")
        if ballast is not None:
            try:
                position = float(ballast.findtext("position"))
                expected_radius = (
                    osifog_podset._haack_radius(
                        position,
                        float(nose.findtext("length")),
                        float(nose.findtext("aftradius")),
                    )
                    - float(nose.findtext("thickness"))
                )
                actual_radius = float(ballast.findtext("outerradius"))
                nose_shell_bonded = math.isclose(
                    actual_radius,
                    expected_radius,
                    rel_tol=0.0,
                    abs_tol=2.0e-6,
                )
            except (TypeError, ValueError):
                nose_shell_bonded = False
    diagnostic = scenario_type in {
        "STAGE_FREE_DESCENT_DIAGNOSTIC",
        "DEBUG_ONLY",
    }
    active_motors = []
    disabled_motors = []
    for stage_key in ("s0", "s1"):
        for role in ("main", "retro"):
            item = {
                "stage_key": stage_key,
                "role": role,
                "designation": MOTOR_DATABASE[int(parameters[f"{stage_key}_{role}"])][1],
            }
            compiled = compiled_motors.get(f"{stage_key}_{role}", {})
            if compiled.get("ignition_event") == "never":
                disabled_motors.append(item)
            else:
                active_motors.append(item)
    return {
        "scenario_type": scenario_type,
        "candidate_id": candidate_id,
        "mission_manifest_digest": _sha256_file(MISSION_PATH),
        "wind_file_digest": _sha256_file(Path(osifog_sweep.WIND_CSV)),
        "anti_tumble_script_digest": osifog_sweep.ANTI_TUMBLE_SCRIPT_DIGEST,
        "anti_tumble_serialized_valid": anti_tumble["valid"],
        "motors_plugged": bool(motor_delays) and all(
            delay == "none" for delay in motor_delays
        ),
        "centering_rings_per_stage": stage_ring_counts,
        "nose_ballast_shell_bonded": nose_shell_bonded,
        "openrocket_jar_digest": _sha256_file(Path("lib/OpenRocket-24.12.jar")),
        "stage_ids": stages,
        "branch_mapping": {"Sustainer": "s0", "Booster": "s1"},
        "active_motors": active_motors,
        "disabled_motors": disabled_motors,
        "ignition_events": {
            "s0_main": compiled_motors.get("s0_main", {}).get("ignition_event"),
            "s1_main": compiled_motors.get("s1_main", {}).get("ignition_event"),
            "s0_retro_delay_s": parameters["s0_retro_delay"],
            "s1_retro_delay_s": parameters["s1_retro_delay"],
        },
        "compiled_motors": compiled_motors,
        "separation_events": {
            "s1": "burnout",
            "delay_s": parameters.get("s1_separation_delay", 0.0),
        },
        "environment": {
            "latitude": osifog_sweep.LAUNCH_LAT,
            "longitude": osifog_sweep.LAUNCH_LON,
            "altitude_m": osifog_sweep.LAUNCH_ALT,
            "temperature_k": osifog_sweep.TEMP_K,
            "pressure_pa": osifog_sweep.PRESSURE_PA,
            "wind_altitude_reference": "AGL",
        },
        "launch_guide": {
            "length_m": osifog_sweep.LAUNCH_ROD_M,
            "angle_deg": parameters.get("launch_angle_deg"),
            "azimuth_deg": parameters.get("launch_azimuth"),
        },
        "diagnostic_only": diagnostic,
    }


def validate_scenario_manifest(manifest: dict, *, authority_scoring: bool = False) -> None:
    scenario_type = manifest.get("scenario_type")
    if scenario_type not in SCENARIO_TYPES:
        raise ValueError("scenario manifest has no valid scenario_type")
    if not manifest.get("anti_tumble_serialized_valid"):
        raise ValueError("scenario lacks the exact serialized anti-tumble extension")
    if not manifest.get("mission_manifest_digest") or not manifest.get("wind_file_digest"):
        raise ValueError("scenario mission/wind digest is missing")
    if set(manifest.get("branch_mapping", {}).values()) != {"s0", "s1"}:
        raise ValueError("scenario branch identity is ambiguous")
    if authority_scoring and manifest.get("diagnostic_only"):
        raise ValueError("diagnostic scenario cannot be scored as authority")
    if scenario_type == "OFFICIAL_FULL_MISSION":
        if len(manifest.get("stage_ids", [])) < 2:
            raise ValueError("full mission is missing a stage")
        if len(manifest.get("active_motors", [])) < 4:
            raise ValueError("full mission is missing a motor role")
        if not manifest.get("motors_plugged"):
            raise ValueError("full mission contains a non-plugged motor")
        if manifest.get("centering_rings_per_stage") != {"s0": 2, "s1": 2}:
            raise ValueError(
                "full mission requires exactly two centering rings per stage"
            )
        if not manifest.get("nose_ballast_shell_bonded"):
            raise ValueError("full mission nose ballast is not shell-bonded")
    compiled_motors = manifest.get("compiled_motors", {})
    if set(compiled_motors) != {"s0_main", "s0_retro", "s1_main", "s1_retro"}:
        raise ValueError("compiled OpenRocket artifact is missing a motor role")
    actual_main_ignition = {
        key: compiled_motors.get(key, {}).get("ignition_event")
        for key in EXPECTED_MAIN_IGNITION
    }
    if actual_main_ignition != EXPECTED_MAIN_IGNITION:
        raise ValueError(
            "compiled main-motor ignition contract mismatch: "
            f"expected {EXPECTED_MAIN_IGNITION}, got {actual_main_ignition}"
        )
    for stage_key in ("s0", "s1"):
        compiled_delay = float(
            compiled_motors[f"{stage_key}_retro"]["ignition_delay_s"]
        )
        declared_delay = float(
            manifest["ignition_events"][f"{stage_key}_retro_delay_s"]
        )
        if abs(compiled_delay - declared_delay) > 1.0e-6:
            raise ValueError(
                f"compiled {stage_key} retro delay {compiled_delay} does not "
                f"match manifest {declared_delay}"
            )
    expected_retro_event = (
        "never" if scenario_type in {"STAGE_FREE_DESCENT_DIAGNOSTIC", "DEBUG_ONLY"}
        else "launch"
    )
    if scenario_type in {"OFFICIAL_FULL_MISSION", "STAGE_FREE_DESCENT_DIAGNOSTIC", "DEBUG_ONLY"}:
        for stage_key in ("s0", "s1"):
            actual = compiled_motors[f"{stage_key}_retro"]["ignition_event"]
            if actual != expected_retro_event:
                raise ValueError(
                    f"compiled {stage_key} retro event {actual!r} does not match "
                    f"scenario requirement {expected_retro_event!r}"
                )


@dataclass
class SearchConfig:
    rust_budget: int = 5000
    rust_generations: int = 5
    finalist_budget: int = 48
    seed: int = 16000
    output_dir: Path = Path("designs/osifog_engine_search")
    wind_csv: Path = Path("OSIFOG/OpenWind_File.csv")
    resume: bool = True
    calibration_result: Path | None = None
    seed_parameters: Path | None = None


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    last_error = None
    for attempt in range(5):
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{time.time_ns()}.{attempt}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return
        except OSError as exc:
            last_error = exc
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            time.sleep(0.05 * (attempt + 1))
    raise last_error


def _write_health(output_dir: Path, status: str, phase: str, **details) -> None:
    _atomic_json(
        Path(output_dir) / "health.json",
        {
            "status": status,
            "phase": phase,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            **details,
        },
    )


def _motor_indices(allowed: set[str]) -> list[int]:
    rust_indices = set(rust_available_motor_indices())
    result = [
        index
        for index, motor in enumerate(MOTOR_DATABASE)
        if index in rust_indices and motor[1] in allowed
    ]
    if not result:
        raise RuntimeError(f"no Rust .eng motors match {sorted(allowed)}")
    return result


def _mission_search_space() -> dict:
    mission = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    return mission["evolution"]["physical_repair_space"]


def _choice_in_range(rng: random.Random, values, fallback):
    values = list(values or ())
    return rng.choice(values) if values else fallback


def _fitting_radius(main_index: int, retro_index: int, preferred: list[float]) -> float:
    for radius in sorted(float(value) for value in preferred):
        try:
            osifog_sweep._falcon_cluster_geometry(
                main_index, retro_index, radius
            )
        except ValueError:
            continue
        return radius
    for millimeters in range(65, 111):
        radius = millimeters / 1000.0
        try:
            osifog_sweep._falcon_cluster_geometry(
                main_index, retro_index, radius
            )
        except ValueError:
            continue
        return radius
    raise ValueError("no legal 3+1 airframe radius fits the selected motors")


def _sample_parameters(rng: random.Random, wind_levels: list) -> dict:
    mission = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    space = _mission_search_space()
    mains = _motor_indices(set(mission["motor_pool"]["allowed_designations"]))
    s0_retros = _motor_indices(set(space["s0_retro_designations"]))
    s1_retros = _motor_indices(set(space["s1_retro_designations"]))
    s0_main, s1_main = rng.choice(mains), rng.choice(mains)
    s0_retro, s1_retro = rng.choice(s0_retros), rng.choice(s1_retros)
    radius_options = space["s0_body_radius_m"]
    s0_radius = _fitting_radius(s0_main, s0_retro, radius_options)
    s1_radius = _fitting_radius(s1_main, s1_retro, radius_options)
    fin_materials = tuple(space["s0_fin_material"])
    trajectory = space["trajectory_polish"]
    p = {
        "s0_main": s0_main,
        "s0_retro": s0_retro,
        "s1_main": s1_main,
        "s1_retro": s1_retro,
        "main_cluster_count": 3,
        "s0_body_rad": s0_radius,
        "s1_body_rad": s1_radius,
        "s0_body_len": osifog_sweep._body_len(s0_main, s0_retro, 0.12, 3),
        "s1_body_len": osifog_sweep._body_len(s1_main, s1_retro, 0.12, 3),
        # Mission-clock ignition candidates are optimized natively. The OR
        # authority pass still creates its own 200s free-descent baseline
        # before polishing these values against observed tail-first windows.
        "s0_retro_delay": rng.uniform(20.0, 120.0),
        "s1_retro_delay": rng.uniform(10.0, 80.0),
        "s0_retro_ignition_event": "launch",
        "s1_retro_ignition_event": "launch",
        "nose_mass_kg": rng.uniform(0.2, 0.8),
        "nose_length_m": rng.uniform(1.0, 2.0),
        "nose_ballast_pos_m": rng.uniform(0.05, 0.50),
        "s0_aft_ballast_kg": rng.uniform(0.0, 0.80),
        "s1_aft_ballast_kg": rng.uniform(0.0, 0.80),
        "s0_aft_ballast_pos_m": rng.uniform(0.02, 0.50),
        "s1_aft_ballast_pos_m": rng.uniform(0.02, 0.80),
        "s0_pod_angle_offset_deg": rng.uniform(0.0, 120.0),
        "s1_pod_angle_offset_deg": rng.uniform(0.0, 120.0),
        "s0_core_fin_angle_offset_deg": rng.uniform(0.0, 120.0),
        "s1_core_fin_angle_offset_deg": rng.uniform(0.0, 120.0),
        "s0_core_fin_root": rng.uniform(0.18, 0.45),
        "s1_core_fin_root": rng.uniform(0.20, 0.50),
        "s0_core_fin_height": rng.uniform(0.08, 0.25),
        "s1_core_fin_height": rng.uniform(0.10, 0.28),
        "s0_fin_root": rng.uniform(0.32, 0.40),
        "s1_fin_root": rng.uniform(0.35, 0.45),
        "s0_fin_height": rng.uniform(0.18, 0.24),
        "s1_fin_height": rng.uniform(0.20, 0.26),
        "s0_pod_radius": 0.025,
        "s1_pod_radius": 0.025,
        "s0_pod_length": rng.uniform(0.15, 0.50),
        "s1_pod_length": rng.uniform(0.15, 0.50),
        "s0_pod_fin_count": rng.choice((0, 3, 3, 4)),
        "s1_pod_fin_count": rng.choice((0, 3, 3, 4)),
        "s0_pod_fin_root": rng.uniform(0.04, 0.20),
        "s1_pod_fin_root": rng.uniform(0.04, 0.20),
        "s0_pod_fin_height": rng.uniform(0.02, 0.10),
        "s1_pod_fin_height": rng.uniform(0.02, 0.10),
        "s0_aft_ballast_rod_radius_m": _choice_in_range(
            rng, space["s1_aft_ballast_rod_radius_m"], 0.014
        ),
        "s1_aft_ballast_rod_radius_m": _choice_in_range(
            rng, space["s1_aft_ballast_rod_radius_m"], 0.014
        ),
        "s0_aft_ballast_attachment": _choice_in_range(
            rng, space["s1_aft_ballast_attachment"], "central_bonded"
        ),
        "s1_aft_ballast_attachment": _choice_in_range(
            rng, space["s1_aft_ballast_attachment"], "central_bonded"
        ),
        "s0_fin_material": rng.choice(fin_materials),
        "s1_fin_material": rng.choice(fin_materials),
        "s0_fin_thickness_m": _choice_in_range(
            rng, space["s0_fin_thickness_m"], 0.001
        ),
        "s1_fin_thickness_m": _choice_in_range(
            rng, space["s0_fin_thickness_m"], 0.001
        ),
        "s0_grid_fin_count": rng.choice((0, 3, 3, 3, 3)),
        "s1_grid_fin_count": rng.choice((0, 3, 3, 3, 3)),
        "s0_grid_fin_root": rng.uniform(0.10, 0.35),
        "s1_grid_fin_root": rng.uniform(0.10, 0.35),
        "s0_grid_fin_height": rng.uniform(0.08, 0.35),
        "s1_grid_fin_height": rng.uniform(0.08, 0.35),
        "s0_grid_fin_position_m": rng.uniform(
            *space["forward_fin_position_range_m"]
        ),
        "s1_grid_fin_position_m": rng.uniform(
            *space["forward_fin_position_range_m"]
        ),
        "s0_grid_fin_sweep": rng.uniform(0.0, 30.0),  # Wide sweep range
        "s1_grid_fin_sweep": rng.uniform(0.0, 30.0),
        "s0_grid_fin_thickness_m": _choice_in_range(
            rng, space["s0_fin_thickness_m"], 0.001
        ),
        "s1_grid_fin_thickness_m": _choice_in_range(
            rng, space["s0_fin_thickness_m"], 0.001
        ),
        "s0_grid_fin_material": rng.choice(fin_materials),
        "s1_grid_fin_material": rng.choice(fin_materials),
        # Three forward fins can occupy the 60-degree gaps between the three
        # side pods instead of forcing the cage outward as a solid drag disk.
        "s0_grid_fin_angle_offset_deg": 60.0,
        "s1_grid_fin_angle_offset_deg": 0.0,
        "s0_fin_count": rng.choice((3, 4, 5)),
        "s1_fin_count": rng.choice((3, 4, 5)),
        "s0_fin_sweep": rng.uniform(0.0, 35.0),
        "s1_fin_sweep": rng.uniform(0.0, 40.0),
        "s0_fin_root": rng.uniform(0.18, 0.38),
        "s1_fin_root": rng.uniform(0.20, 0.40),
        "s0_fin_height": rng.uniform(0.08, 0.22),
        "s1_fin_height": rng.uniform(0.10, 0.24),
        "s1_separation_delay": 0.0,
        "launch_angle_deg": rng.uniform(
            *trajectory["angle_from_vertical_range_deg"]
        ),
        "launch_azimuth": rng.uniform(*trajectory["azimuth_range_deg"]),
        "wind_levels": wind_levels,
    }
    # Native external 3+1 geometry. Keep the historical body/fin keys above
    # during migration because authority diagnostics still consume them, but
    # both Rust and OpenRocket now compile these physical PodSet dimensions.
    for prefix, main_index, retro_index in (
        ("s0", s0_main, s0_retro),
        ("s1", s1_main, s1_retro),
    ):
        main = MOTOR_DATABASE[main_index]
        retro = MOTOR_DATABASE[retro_index]
        pod_radius = max(0.016, float(main[2]) / 2.0 + 0.006)
        core_radius = max(0.02, float(retro[2]) / 2.0 + 0.006)
        pod_length = float(main[3]) + 0.05
        pod_nose_length = max(0.12, pod_radius * 3.0)
        core_length = max(
            0.4, float(retro[3]) + 0.05,
            pod_nose_length + pod_length + rng.uniform(0.04, 0.30),
        )
        if float(p[f"{prefix}_aft_ballast_kg"]) > 0.0:
            rod_radius = float(p[f"{prefix}_aft_ballast_rod_radius_m"])
            core_radius = max(
                core_radius,
                osifog_podset.minimum_core_radius_for_ballast(
                    retro_index, rod_radius
                ),
            )
            rod_length = osifog_podset.ballast_rod_length_m(
                float(p[f"{prefix}_aft_ballast_kg"]), rod_radius
            )
            core_length = max(
                core_length,
                float(p[f"{prefix}_aft_ballast_pos_m"]) + rod_length + 0.03,
            )
        pod_fin_height = rng.uniform(0.0, pod_radius)
        # Honor the mission's physical aft-fin gene. The previous radius*2
        # clamp collapsed every 0.5..1.5 m sustainer choice to ~0.13 m,
        # eliminating the only passive-stability lever available to low-nose
        # recovery designs. The pod cage expands below to preserve clearance.
        core_fin_height = float(p[f"{prefix}_fin_height"])
        grid_fin_height = (
            float(p[f"{prefix}_grid_fin_height"])
            if int(p[f"{prefix}_grid_fin_count"]) > 0
            else 0.0
        )
        pod_gap = max(pod_fin_height, core_fin_height, grid_fin_height) + 0.008
        pylon_span_to_chord = rng.choice((6.0, 8.0, 10.0, 12.0))
        pylon_span_to_thickness = rng.choice((60.0, 80.0, 100.0, 120.0))
        core_length = max(
            core_length,
            float(p[f"{prefix}_grid_fin_position_m"])
            + float(p[f"{prefix}_grid_fin_root"])
            + 0.01,
        )
        p.update(
            {
                f"{prefix}_core_radius": core_radius,
                f"{prefix}_core_length": core_length,
                f"{prefix}_pod_radius": pod_radius,
                f"{prefix}_pod_length": pod_length,
                f"{prefix}_pod_nose_length": pod_nose_length,
                f"{prefix}_pod_axial_offset_m": rng.uniform(
                    0.0, max(0.01, core_length - pod_nose_length - pod_length)
                ),
                f"{prefix}_pod_angle_offset_deg": 60.0 if prefix == "s1" else 0.0,
                f"{prefix}_pod_radial_offset": core_radius + pod_radius + pod_gap,
                f"{prefix}_core_fin_count": int(p[f"{prefix}_fin_count"]),
                f"{prefix}_core_fin_sweep": float(p[f"{prefix}_fin_sweep"]),
                f"{prefix}_core_fin_root": min(float(p[f"{prefix}_fin_root"]), core_length * 0.95),
                f"{prefix}_core_fin_height": core_fin_height,
                f"{prefix}_core_fin_thickness_m": float(
                    p[f"{prefix}_fin_thickness_m"]
                ),
                f"{prefix}_core_fin_material": p[f"{prefix}_fin_material"],
                f"{prefix}_pod_fin_count": int(p.get(f"{prefix}_pod_fin_count", 3)) if pod_fin_height > 0.0 else 0,
                f"{prefix}_pod_fin_sweep": rng.uniform(0.0, 20.0),
                f"{prefix}_pod_fin_root": max(0.03, pod_length * 0.15),
                f"{prefix}_pod_fin_height": pod_fin_height,
                f"{prefix}_pod_fin_thickness_m": 0.003,
                f"{prefix}_pod_fin_material": "fiberglass",
                f"{prefix}_pod_nose_shape": "ogive",
                f"{prefix}_aero_interference_factor": 1.15,
                f"{prefix}_pylon_chord_m": max(0.025, pod_gap / pylon_span_to_chord),
                f"{prefix}_pylon_thickness_m": max(0.003, pod_gap / pylon_span_to_thickness),
                f"{prefix}_pylon_station_count": rng.choice((2, 3)),
            }
        )
    _repair_podset_derived_geometry(p)
    return p


def _repair_podset_derived_geometry(p: dict) -> None:
    """Re-derive motor fit and the continuous core after genetic crossover."""
    gaps = {}
    for prefix in ("s0", "s1"):
        gap_terms = [
            float(p[f"{prefix}_pod_radial_offset"])
            - float(p[f"{prefix}_core_radius"])
            - float(p[f"{prefix}_pod_radius"]),
            float(p.get(f"{prefix}_pod_fin_height", 0.0)) + 0.008,
        ]
        if _finset_intersects_pod(p, prefix, "grid_fin"):
            # Forward fins are real fixed surfaces on the central core. A
            # crossover can inherit them independently of the donor cage
            # radius, so repair only a real discrete 3D intersection rather
            # than treating the whole fin set as a solid 360-degree disk.
            gap_terms.append(
                float(p.get(f"{prefix}_grid_fin_height", 0.0)) + 0.008
            )
        if _core_fin_intersects_pod(p, prefix):
            gap_terms.append(float(p.get(f"{prefix}_core_fin_height", 0.0)) + 0.008)
        gaps[prefix] = max(gap_terms)
        main = MOTOR_DATABASE[int(p[f"{prefix}_main"])]
        retro = MOTOR_DATABASE[int(p[f"{prefix}_retro"])]
        p[f"{prefix}_pod_radius"] = max(
            float(p[f"{prefix}_pod_radius"]), float(main[2]) / 2.0 + 0.006
        )
        p[f"{prefix}_core_radius"] = max(
            float(p[f"{prefix}_core_radius"]), float(retro[2]) / 2.0 + 0.006
        )
        p[f"{prefix}_pod_length"] = max(
            float(p[f"{prefix}_pod_length"]), float(main[3]) + 0.05
        )
        p[f"{prefix}_core_length"] = max(
            float(p[f"{prefix}_core_length"]),
            float(retro[3]) + 0.05,
            float(p[f"{prefix}_pod_length"]) + 0.04,
        )
        pod_nose = float(p.get(
            f"{prefix}_pod_nose_length",
            max(0.12, float(p[f"{prefix}_pod_radius"]) * 3.0),
        ))
        p[f"{prefix}_pod_nose_length"] = pod_nose
        lower = 0.02
        upper = max(lower, float(p[f"{prefix}_core_length"]) - pod_nose - float(
            p[f"{prefix}_pod_length"]
        ))
        p[f"{prefix}_pod_axial_offset_m"] = min(
            upper,
            max(lower, float(p.get(f"{prefix}_pod_axial_offset_m", upper))),
        )
    for prefix in ("s0", "s1"):
        if float(p.get(f"{prefix}_aft_ballast_kg", 0.0)) > 0.0:
            rod_radius = float(p[f"{prefix}_aft_ballast_rod_radius_m"])
            p[f"{prefix}_core_radius"] = max(
                float(p[f"{prefix}_core_radius"]),
                osifog_podset.minimum_core_radius_for_ballast(
                    int(p[f"{prefix}_retro"]), rod_radius
                ),
            )
            p[f"{prefix}_core_length"] = max(
                float(p[f"{prefix}_core_length"]),
                float(p[f"{prefix}_aft_ballast_pos_m"])
                + osifog_podset.ballast_rod_length_m(
                    float(p[f"{prefix}_aft_ballast_kg"]), rod_radius
                )
                + 0.03,
            )
        p[f"{prefix}_core_length"] = max(
            float(p[f"{prefix}_core_length"]),
            float(p.get(f"{prefix}_grid_fin_position_m", 0.0))
            + float(p.get(f"{prefix}_grid_fin_root", 0.0))
            + 0.01,
        )
    common_core_radius = max(p["s0_core_radius"], p["s1_core_radius"])
    for prefix in ("s0", "s1"):
        p[f"{prefix}_core_radius"] = common_core_radius
        p[f"{prefix}_pod_radial_offset"] = (
            common_core_radius + p[f"{prefix}_pod_radius"] + gaps[prefix]
        )
        # Crossover can combine a wider cage with a thinner donor pylon.
        # Repair section dimensions so offspring remain buildable, while
        # preserving larger inherited sections as real mass.
        p[f"{prefix}_pylon_chord_m"] = max(
            float(p.get(f"{prefix}_pylon_chord_m", 0.025)), gaps[prefix] / 12.0
        )
        p[f"{prefix}_pylon_thickness_m"] = max(
            float(p.get(f"{prefix}_pylon_thickness_m", 0.003)), gaps[prefix] / 120.0
        )
        p[f"{prefix}_pylon_station_count"] = max(
            2, int(p.get(f"{prefix}_pylon_station_count", 2))
        )
    if float(p.get("s1_separation_delay", 0.0)) > 1.0e-9:
        p.setdefault("s0_pod_angle_offset_deg", 0.0)
        p.setdefault("s1_pod_angle_offset_deg", 60.0)
        angle_delta = abs((
            float(p["s0_pod_angle_offset_deg"])
            - float(p["s1_pod_angle_offset_deg"])
        ) % 120.0)
        angle_delta = min(angle_delta, 120.0 - angle_delta)
        if angle_delta < 1.0e-6:
            # A delayed axial separation means the upper pod motors burn
            # through the complete lower cage.  Coincident pod azimuths are
            # never a useful phenotype, so repair legacy/crossover genomes to
            # the maximum 60-degree inter-cage stagger.
            p["s0_pod_angle_offset_deg"] = (
                float(p["s1_pod_angle_offset_deg"]) + 60.0
            ) % 120.0
        s0_body_bottom = (
            float(p["s0_pod_axial_offset_m"])
            + float(p["s0_pod_nose_length"])
            + float(p["s0_pod_length"])
        )
        plume_distance = (
            max(0.0, float(p["s0_core_length"]) - s0_body_bottom)
            + float(p["s1_core_length"])
        )
        motor_radius = float(MOTOR_DATABASE[int(p["s0_main"])][2]) / 2.0
        plume_radius = motor_radius + math.tan(math.radians(12.0)) * plume_distance
        required_s0_radius = float(p["s1_core_radius"]) + plume_radius + 0.001
        p["s0_pod_radial_offset"] = max(
            float(p["s0_pod_radial_offset"]), required_s0_radius
        )
        # Clear the actual finite lower pods and fins.  Incrementing here is
        # deterministic and conservative, but avoids the former false model
        # that treated three thin fins as a solid 360-degree drag disk.
        for _ in range(1000):
            if not _interstage_plume_violations(p):
                break
            p["s0_pod_radial_offset"] += 0.005
        s0_gap = (
            float(p["s0_pod_radial_offset"])
            - float(p["s0_core_radius"])
            - float(p["s0_pod_radius"])
        )
        p["s0_pylon_chord_m"] = max(
            float(p.get("s0_pylon_chord_m", 0.025)), s0_gap / 12.0
        )
        p["s0_pylon_thickness_m"] = max(
            float(p.get("s0_pylon_thickness_m", 0.003)), s0_gap / 120.0
        )


def _sample_valid_parameters(
    rng: random.Random, wind_levels: list, max_attempts: int = 250
) -> dict:
    """Sample until the shared physical cage/attachment compiler accepts it."""
    last_violations = []
    for _ in range(max_attempts):
        try:
            parameters = _sample_parameters(rng, wind_levels)
        except ValueError as exc:
            last_violations = [str(exc)]
            continue
        last_violations = _podset_geometry_violations(parameters)
        if not last_violations:
            return parameters
    raise RuntimeError(
        "engine could not generate a collision-free candidate after "
        f"{max_attempts} attempts: {'; '.join(last_violations)}"
    )


def _breed_valid_parameters(
    rng: random.Random,
    parents: list[dict],
    wind_levels: list,
    max_attempts: int = 100,
) -> dict:
    """Crossover successful physical candidates with random genetic injection."""
    if not parents:
        return _sample_valid_parameters(rng, wind_levels)
    for _ in range(max_attempts):
        left = rng.choice(parents)
        right = rng.choice(parents)
        donor = _sample_valid_parameters(rng, wind_levels)
        child = {
            key: (left if rng.random() < 0.5 else right).get(key, donor.get(key))
            for key in donor
        }
        for key in donor:
            if key != "wind_levels" and rng.random() < 0.12:
                child[key] = donor[key]
        child["wind_levels"] = wind_levels
        child["main_cluster_count"] = 3
        child["s1_ballast_kg"] = child["s1_aft_ballast_kg"]
        _repair_podset_derived_geometry(child)
        try:
            for stage in ("s0", "s1"):
                child[f"{stage}_body_rad"] = _fitting_radius(
                    child[f"{stage}_main"],
                    child[f"{stage}_retro"],
                    _mission_search_space()["s0_body_radius_m"],
                )
                child[f"{stage}_body_len"] = osifog_sweep._body_len(
                    child[f"{stage}_main"],
                    child[f"{stage}_retro"],
                    0.12,
                    3,
                )
        except ValueError:
            continue
        if not _podset_geometry_violations(child):
            return child
    return _sample_valid_parameters(rng, wind_levels)


def _seed_parameter_candidates(payload: dict, source_path: Path) -> list[dict]:
    """Load ordered phenotypes from result files or durable campaign states."""
    candidates = []
    if isinstance(payload.get("parameters"), dict):
        candidates.append(payload["parameters"])
    if isinstance(payload.get("best"), dict) and isinstance(
        payload["best"].get("parameters"), dict
    ):
        candidates.append(payload["best"]["parameters"])
    candidates.extend(
        item["parameters"]
        for item in payload.get("openrocket_results", [])
        if isinstance(item, dict) and isinstance(item.get("parameters"), dict)
    )
    records = [
        item for item in payload.get("records", [])
        if isinstance(item, dict) and isinstance(item.get("parameters"), dict)
    ]
    records.sort(key=lambda item: (
        not bool(item.get("ascent_admissible", False)),
        3000.0 * (float(item.get("apogee_m", 0.0)) - 3000.0) ** 2,
        -int(item.get("usable_stages", 0)),
        -float(item.get("max_tail_window_s", 0.0)),
    ))
    candidates.extend(item["parameters"] for item in records[:32])
    for nested in payload.get("seed_sources", []):
        nested_path = Path(nested)
        if not nested_path.is_absolute():
            nested_path = source_path.parent / nested_path
        nested_payload = json.loads(nested_path.read_text(encoding="utf-8"))
        candidates.extend(_seed_parameter_candidates(nested_payload, nested_path))
    return candidates


def parameters_to_ast(p: dict) -> list[ASTNode]:
    """Project the physical external 3+1 PodSet into the native Rust AST."""
    stages: list[ASTNode] = []
    rust_material = {
        "legal_balsa": "balsa",
        "cardboard": "cardboard",
        "fiberglass": "fiberglass",
    }
    for stage_key, name, include_nose in (
        ("s0", "Sustainer", True),
        ("s1", "Booster", False),
    ):
        main_motor = MOTOR_DATABASE[p[f"{stage_key}_main"]]
        retro_motor = MOTOR_DATABASE[p[f"{stage_key}_retro"]]
        core_radius = p.get(
            f"{stage_key}_core_radius",
            max(0.02, retro_motor[2] / 2.0 + 0.006),
        )
        pod_radius = p.get(
            f"{stage_key}_pod_radius",
            max(0.016, main_motor[2] / 2.0 + 0.006),
        )
        core_length = p.get(
            f"{stage_key}_core_length", p.get(f"{stage_key}_body_len", 0.8)
        )
        pod_length = p.get(
            f"{stage_key}_pod_length",
            max(main_motor[3] + 0.05, core_length * 0.8),
        )
        pod_fin_height = p.get(
            f"{stage_key}_pod_fin_height",
            min(p.get(f"{stage_key}_fin_height", 0.0), pod_radius * 2.0),
        )
        pod_offset = p.get(
            f"{stage_key}_pod_radial_offset",
            core_radius + pod_radius + max(0.008, pod_fin_height + 0.008),
        )
        core_fin_count = p.get(
            f"{stage_key}_core_fin_count", p.get(f"{stage_key}_fin_count", 4)
        )
        core_fin_root = p.get(
            f"{stage_key}_core_fin_root", p.get(f"{stage_key}_fin_root", 0.12)
        )
        core_fin_height = p.get(
            f"{stage_key}_core_fin_height", p.get(f"{stage_key}_fin_height", 0.06)
        )
        if include_nose:
            stages.extend(
                [
                    ASTNode("STAGE", name=name, recovery="retro_only"),
                    ASTNode(
                        "NOSE_CONE",
                        shape="haack",
                        length=p.get(
                            "nose_length_m",
                            max(0.25, core_radius * 8.0),
                        ),
                        material="fiberglass",
                    ),
                ]
            )
        else:
            stages.append(ASTNode("STAGE", name=name, recovery="retro_only"))
        stages.extend(
            [
                ASTNode(
                    "BODY_TUBE",
                    length=core_length,
                    radius=core_radius,
                    thickness=0.002,
                    material="cardboard",
                ),
                ASTNode(
                    "MOTOR_MOUNT",
                    motor_index=p[f"{stage_key}_retro"],
                    motor_designation=retro_motor[1],
                    role="retro",
                    multiplicity=1,
                    ignition="launch",
                    ignition_delay=p[f"{stage_key}_retro_delay"],
                    mount_length_m=core_length,
                    mount_material_density=700.0,
                ),
                ASTNode(
                    "FIN_SET",
                    count=core_fin_count,
                    sweep=p.get(f"{stage_key}_core_fin_sweep", p.get(f"{stage_key}_fin_sweep", 15.0)),
                    root=core_fin_root,
                    height=core_fin_height,
                    thickness=p.get(f"{stage_key}_core_fin_thickness_m", p.get(f"{stage_key}_fin_thickness_m", 0.003)),
                    material=rust_material.get(p.get(f"{stage_key}_core_fin_material", p.get(f"{stage_key}_fin_material", "fiberglass")), "fiberglass"),
                    cross_section="airfoil",
                ),
            ]
        )
        grid_count = int(p.get(f"{stage_key}_grid_fin_count", 0))
        if grid_count > 0:
            stages.append(
                ASTNode(
                    "FIN_SET",
                    count=grid_count,
                    sweep=p[f"{stage_key}_grid_fin_sweep"],
                    root=p[f"{stage_key}_grid_fin_root"],
                    height=p[f"{stage_key}_grid_fin_height"],
                    thickness=p[f"{stage_key}_grid_fin_thickness_m"],
                    material=rust_material[
                        p[f"{stage_key}_grid_fin_material"]
                    ],
                    cross_section="airfoil",
                    position_from_top_m=p[
                        f"{stage_key}_grid_fin_position_m"
                    ],
                )
            )
        if stage_key == "s0" and p["nose_mass_kg"] > 0:
            stages.append(
                ASTNode(
                    "BALLAST",
                    mass=p["nose_mass_kg"],
                    material="steel",
                    axial_offset_m=float(
                        p.get("nose_ballast_pos_m", p.get("nose_length_m", 0.5) * 0.75)
                    ),
                )
            )
        native_support_keys = (
            f"{stage_key}_core_radius",
            f"{stage_key}_core_length",
            f"{stage_key}_aft_ballast_pos_m",
            f"{stage_key}_aft_ballast_rod_radius_m",
            f"{stage_key}_aft_ballast_attachment",
        )
        ballast_layout = (
            osifog_podset.stage_support_layout(p, stage_key)["ballast"]
            if all(key in p for key in native_support_keys)
            else None
        )
        if ballast_layout is not None:
            ballast_params = {
                "mass": ballast_layout["mass_kg"],
                "material": "steel",
                "axial_offset_m": ballast_layout["axial_center_m"],
                "radial_offset_m": ballast_layout["center_radius_m"],
                "instance_count": ballast_layout["count"],
                "angle_offset_deg": 0.0,
            }
            stages.append(
                ASTNode(
                    "BALLAST",
                    **ballast_params,
                )
            )
        elif float(p.get(f"{stage_key}_aft_ballast_kg", 0.0)) > 0.0:
            # Compatibility for legacy callers that predate the native cage
            # geometry. Campaign candidates always take the physical layout
            # path above.
            stages.append(
                ASTNode(
                    "BALLAST",
                    mass=float(p[f"{stage_key}_aft_ballast_kg"]),
                    material="steel",
                    position="aft",
                )
            )
        # OpenRocket models the two centering rings and six pod pylons as
        # physical components.  Carry their equivalent masses and stations
        # into the proxy so optimization cannot exploit mass that disappears
        # only in Rust.  Radial pylon instances also contribute to transverse
        # inertia through the native BALLAST instance expansion.
        if all(
            f"{stage_key}_{suffix}" in p
            for suffix in (
                "core_radius", "core_length", "pod_radius",
                "pod_length", "pod_radial_offset",
            )
        ):
            for support_mass in osifog_podset.podset_structural_point_masses(
                p, stage_key
            ):
                stages.append(ASTNode("BALLAST", **support_mass))
        stages.append(ASTNode("CLOSE_BODY"))
        pod_children = [
            ASTNode(
                "NOSE_CONE",
                shape=p.get(f"{stage_key}_pod_nose_shape", "ogive"),
                length=p.get(f"{stage_key}_pod_nose_length", max(0.12, pod_radius * 3.0)),
                aft_radius=pod_radius,
                material="fiberglass",
            ),
            ASTNode(
                "BODY_TUBE",
                length=pod_length,
                radius=pod_radius,
                thickness=0.002,
                material="fiberglass",
            ),
        ]
        pod_fin_count = int(p.get(f"{stage_key}_pod_fin_count", 0))
        if pod_fin_count > 0:
            pod_children.append(
                ASTNode(
                    "FIN_SET",
                    count=pod_fin_count,
                    sweep=p.get(f"{stage_key}_pod_fin_sweep", 0.0),
                    root=p.get(f"{stage_key}_pod_fin_root", pod_length * 0.15),
                    height=pod_fin_height,
                    thickness=p.get(f"{stage_key}_pod_fin_thickness_m", 0.003),
                    material=rust_material.get(p.get(f"{stage_key}_pod_fin_material", "fiberglass"), "fiberglass"),
                    cross_section="airfoil",
                )
            )
        pod_children.extend(
            [
                ASTNode(
                    "MOTOR_MOUNT",
                    motor_index=p[f"{stage_key}_main"],
                    motor_designation=main_motor[1],
                    role="main",
                    multiplicity=1,
                    ignition="automatic",
                    # Rust consumes this as the primary motor mount's
                    # ejection/separation coast. OpenRocket emits the same
                    # booster value as <separationdelay>.
                    delay=(p.get("s1_separation_delay", 0.0) if stage_key == "s1" else 0.0),
                ),
                ASTNode("CLOSE_BODY"),
            ]
        )
        stages.append(
            ASTNode(
                "POD",
                name=f"{name} Ascent Pods",
                instance_count=3,
                radial_offset_m=pod_offset,
                angle_offset_deg=float(
                    p.get(f"{stage_key}_pod_angle_offset_deg", 0.0)
                ),
                axial_offset_m=float(
                    p.get(f"{stage_key}_pod_axial_offset_m", 0.0)
                ),
                aero_interference_factor=p.get(
                    f"{stage_key}_aero_interference_factor", 1.15
                ),
                children=[node.to_dict() for node in pod_children],
            )
        )
    return stages


class _NeutralCkg:
    def __init__(self, calibrations: dict | None = None):
        self.calibrations = calibrations or {}

    @staticmethod
    def subgraph_items(_ast):
        return []

    @staticmethod
    def acceptance_multiplier_for_items(_items):
        return 1.0


def _finite_positive_ratio(authority_value, rust_value) -> float | None:
    try:
        authority = float(authority_value)
        proxy = float(rust_value)
    except (TypeError, ValueError):
        return None
    ratio = authority / proxy if proxy > 0.0 else math.nan
    return ratio if math.isfinite(ratio) and ratio > 0.0 else None


def _robust_quantile(values: list[float], quantile: float) -> float:
    """Quantile after a MAD fence, protecting gates without obeying outliers."""
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    fence = max(4.0 * mad, 0.05 * abs(median))
    filtered = sorted(value for value in values if abs(value - median) <= fence)
    if not filtered:
        filtered = sorted(values)
    index = min(len(filtered) - 1, max(0, math.ceil(quantile * len(filtered)) - 1))
    return filtered[index]


def load_authority_calibration(result_path: Path | None) -> dict | None:
    """Learn robust global Rust->OR correction from a prior authority batch."""
    if result_path is None:
        return None
    payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
    applied = payload.get("authority_calibration") or {}
    applied_apogee = float(applied.get("apogee_delta", 1.0))
    applied_mach = float(applied.get("mach_delta", 1.0))
    applied_margin = float(applied.get("margin_delta", 1.0))
    rust_candidates = payload.get("rust_candidates", [])
    apogee_ratios: list[float] = []
    mach_ratios: list[float] = []
    margin_ratios: list[float] = []
    for record in payload.get("openrocket_results", []):
        metrics = record.get("metrics")
        index = record.get("index")
        if not isinstance(metrics, dict) or not isinstance(index, int):
            continue
        if index < 0 or index >= len(rust_candidates):
            continue
        rust = rust_candidates[index].get("rust", {})
        apogee_ratio = _finite_positive_ratio(
            metrics.get("apogee_m"), rust.get("apogee_m")
        )
        mach_ratio = _finite_positive_ratio(metrics.get("mach"), rust.get("mach"))
        margin_ratio = _finite_positive_ratio(
            metrics.get("min_static_margin"), rust.get("min_static_margin")
        )
        if apogee_ratio is not None:
            apogee_ratios.append(apogee_ratio)
        if mach_ratio is not None:
            mach_ratios.append(mach_ratio)
        if margin_ratio is not None:
            margin_ratios.append(margin_ratio)
    if min(len(apogee_ratios), len(mach_ratios), len(margin_ratios)) < 8:
        import sys
        print("WARNING: Insufficient paired samples (< 8). Falling back to neutral calibration.", file=sys.stderr)
        return {
            "source": str(result_path),
            "sample_count": min(len(apogee_ratios), len(mach_ratios), len(margin_ratios)),
            "apogee_delta": applied_apogee * 1.0,
            "mach_delta": applied_mach * 1.05,
            "mach_median": 1.0,
            "mach_mean": 1.0,
            "margin_delta": applied_margin * 1.0,
            "margin_median": 1.0,
            "margin_mean": 1.0,
            "apogee_mad": 0.0,
        }
    return {
        "source": str(result_path),
        "sample_count": min(
            len(apogee_ratios), len(mach_ratios), len(margin_ratios)
        ),
        "apogee_delta": applied_apogee * statistics.median(apogee_ratios),
        # Hard gates use conservative tails. Central tendency remains in the
        # report so drift is visible without letting a median under-protect OR.
        "mach_delta": applied_mach * _robust_quantile(mach_ratios, 0.90),
        "mach_median": statistics.median(mach_ratios),
        "mach_mean": statistics.fmean(mach_ratios),
        "margin_delta": max(0.2, applied_margin * _robust_quantile(margin_ratios, 0.50)),
        "margin_median": statistics.median(margin_ratios),
        "margin_mean": statistics.fmean(margin_ratios),
        "apogee_mad": statistics.median(
            abs(value - statistics.median(apogee_ratios))
            for value in apogee_ratios
        ),
        "mach_mad": statistics.median(
            abs(value - statistics.median(mach_ratios))
            for value in mach_ratios
        ),
        "margin_mad": statistics.median(
            abs(value - statistics.median(margin_ratios))
            for value in margin_ratios
        ),
        "update_ratio": {
            "apogee": statistics.median(apogee_ratios),
            "mach_gate": _robust_quantile(mach_ratios, 0.90),
            "margin_gate": _robust_quantile(margin_ratios, 0.10),
        },
    }


def _ascent_scoring_table() -> dict:
    """Project the official data table onto metrics available at ascent apogee."""
    scoring = json.loads(MISSION_PATH.read_text(encoding="utf-8"))["scoring"]
    terms = [
        term
        for term in scoring.get("terms", [])
        if not any(
            str(metric).startswith("stage_landing_")
            for metric in term.get("metrics", [])
        )
    ]
    return {**scoring, "terms": terms}


def _rust_environment(parameters: dict) -> dict:
    mission = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    atmosphere = mission["atmosphere"]
    surface = parameters["wind_levels"][0]
    return {
        "launch_rod_length_m": osifog_sweep.LAUNCH_ROD_M,
        "launch_rod_angle_rad": math.radians(parameters["launch_angle_deg"]),
        "launch_rod_direction_rad": math.radians(parameters["launch_azimuth"]),
        "wind_speed_mps": float(surface[1]),
        "wind_direction_rad": math.radians(float(surface[2])),
        "relative_humidity": 0.82,
        "base_temperature_k": float(atmosphere["base_temperature_k"]),
        "base_pressure_pa": float(atmosphere["base_pressure_pa"]),
        "launch_altitude_m": float(atmosphere["launch_altitude_m"]),
        "wind_levels": [
            {
                "altitude_m": float(level[0]),
                "speed_ms": float(level[1]),
                "direction_deg": float(level[2]),
                "std_dev_ms": float(level[3]),
            }
            for level in parameters["wind_levels"]
        ],
    }


def _full_scoring_table() -> dict:
    return json.loads(MISSION_PATH.read_text(encoding="utf-8"))["scoring"]


def _default_rust_evaluator(
    population: list[list[ASTNode]],
    parameters: list[dict],
    calibration: dict | None = None,
    execution_profile: str = "balanced",
    simulation_phase: str = "ascent",
):
    calibrations = {}
    if calibration is not None:
        correction = {
            "avg_apogee_delta": calibration["apogee_delta"],
            "avg_mach_delta": calibration["mach_delta"],
            "avg_margin_delta": calibration["margin_delta"],
        }
        calibrations = {
            extract_topological_signature(ast): correction for ast in population
        }
    config = OrganicLoopConfig(
        population=len(population),
        elite_count=len(population),
        target_apogee_m=3000.0,
        physics_mode="openrocket",
        # Rust is the high-throughput full-mission proxy; OpenRocket remains
        # the authority pass for promoted candidates.
        execution_profile=execution_profile,
        objectives=[{"metric": "apogee_m", "kind": "target", "target": 3000.0}],
        constraints={
            "max_mach": 0.95,
            "max_height_m": 4.0,
            "min_static_margin": 1.5,
            **(
                {"simulation_phase": "ascent"}
                if simulation_phase == "ascent"
                else {}
            ),
            "scoring": (
                _ascent_scoring_table()
                if simulation_phase == "ascent"
                else _full_scoring_table()
            ),
            "wind_csv_path": str(
                Path(osifog_sweep.WIND_CSV).resolve()
            ),
        },
    )
    return evaluate_rust_population(
        population,
        _NeutralCkg(calibrations),
        config,
        candidate_environments=[
            _rust_environment(item) for item in parameters
        ],
    )


def _evolve_rust_candidates(
    config: SearchConfig,
    rng: random.Random,
    wind_levels: list,
    calibration: dict | None,
) -> list[tuple[dict, object]]:
    generations = max(1, min(config.rust_generations, config.rust_budget))
    base_size, remainder = divmod(config.rust_budget, generations)
    generation_sizes = [
        base_size + (1 if index < remainder else 0)
        for index in range(generations)
    ]
    checkpoint = Path(config.output_dir) / "rust-evolution.json"
    source_digest = _canonical_digest({
        str(path): _sha256_file(path)
        for path in (Path(__file__), Path(__file__).with_name("osifog_podset.py"), MISSION_PATH)
    })
    archive: list[tuple[dict, object]] = []
    start_generation = 0
    if config.resume and checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("source_digest") != source_digest:
            raise RuntimeError(
                "Rust evolution checkpoint source drift; use a new output directory"
            )
        if saved.get("generation_sizes") != generation_sizes:
            raise RuntimeError(
                "Rust evolution checkpoint budget drift; use a new output directory"
            )
        start_generation = int(saved.get("completed_generations", 0))
        rng.setstate(pickle.loads(base64.b64decode(saved["rng_state"])))
        for item in saved.get("archive", []):
            restored = dict(item["parameters"], wind_levels=wind_levels)
            archive.append((
                restored,
                OrganicCandidate(ast=parameters_to_ast(restored), **item["result"]),
            ))
    seed_parameters = []
    if config.seed_parameters is not None:
        seed_path = Path(config.seed_parameters)
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        candidates = _seed_parameter_candidates(payload, seed_path)
        for candidate in candidates:
            restored = dict(candidate, wind_levels=wind_levels)
            _repair_podset_derived_geometry(restored)
            if not _podset_geometry_violations(restored):
                seed_parameters.append(restored)
    parameters = []
    if start_generation == 0:
        parameters = [dict(item) for item in seed_parameters[:generation_sizes[0]]]
        while len(parameters) < generation_sizes[0]:
            parameters.append(
                _breed_valid_parameters(rng, seed_parameters, wind_levels)
                if seed_parameters and rng.random() < 0.65
                else _sample_valid_parameters(rng, wind_levels)
            )
    for generation in range(start_generation, len(generation_sizes)):
        size = generation_sizes[generation]
        _write_health(
            config.output_dir,
            "running",
            "rust_generation",
            generation=generation + 1,
            generations=len(generation_sizes),
            generation_size=size,
            evaluated=len(archive),
        )
        if generation > 0:
            successful = [
                (p, result)
                for p, result in archive
                if result.status == "success"
            ]
            successful.sort(key=lambda item: item[1].score, reverse=True)
            parent_count = max(8, min(len(successful), size // 5))
            parents = [item[0] for item in successful[:parent_count]]
            parameters = [
                _breed_valid_parameters(rng, parents, wind_levels)
                for _ in range(size)
            ]
        asts = [parameters_to_ast(item) for item in parameters]
        results = _default_rust_evaluator(
            asts,
            parameters,
            calibration,
            execution_profile="super-speed",
            simulation_phase="full",
        )
        archive.extend(zip(parameters, results))
        failure_reasons = Counter(
            result.reason for result in results if result.status != "success"
        )
        ranked_archive = sorted(
            (
                (p, result) for p, result in archive
                if result.status == "success"
            ),
            key=lambda item: item[1].score,
            reverse=True,
        )
        archive = _stratify_candidates(
            ranked_archive, max(256, config.finalist_budget * 8)
        )
        _atomic_json(checkpoint, {
            "schema": 1,
            "source_digest": source_digest,
            "generation_sizes": generation_sizes,
            "completed_generations": generation + 1,
            "last_generation_failure_reasons": dict(failure_reasons.most_common()),
            "rng_state": base64.b64encode(pickle.dumps(rng.getstate())).decode("ascii"),
            "archive": [
                {
                    "parameters": {k: v for k, v in p.items() if k != "wind_levels"},
                    "result": {
                        key: value
                        for key, value in asdict(result).items()
                        if key != "ast"
                    },
                }
                for p, result in archive
            ],
        })
        successful_scores = [
            result.score for result in results if result.status == "success"
        ]
        _write_health(
            config.output_dir,
            "running",
            "rust_generation_complete",
            generation=generation + 1,
            generations=len(generation_sizes),
            evaluated=len(archive),
            generation_successes=len(successful_scores),
            generation_best_score=(max(successful_scores) if successful_scores else None),
        )
    return archive


def _promote_rust_candidates(
    ranked: list[tuple[dict, object]],
    calibration: dict | None,
    count: int,
) -> list[tuple[dict, object]]:
    """Recheck the leading fast-screen candidates at medium Rust fidelity."""
    selected = ranked[:count]
    if not selected:
        return []
    parameters = [item[0] for item in selected]
    asts = [parameters_to_ast(item) for item in parameters]
    results = _default_rust_evaluator(
        asts,
        parameters,
        calibration,
        execution_profile="balanced",
        simulation_phase="full",
    )
    promoted = [
        (parameters[index], result)
        for index, result in enumerate(results)
        if result.status == "success"
    ]
    promoted.sort(key=lambda item: item[1].score, reverse=True)
    return promoted


def _stratify_candidates(
    ranked: list[tuple[dict, object]],
    count: int,
) -> list[tuple[dict, object]]:
    """Keep score leaders while reserving slots for structural diversity."""
    if len(ranked) <= count:
        return ranked
    # Keep score leaders, but recovery topology is a separate basin from
    # ascent score. Two thirds of the authority budget is deliberately
    # reserved for distinct passive-flip mass/aero layouts.
    leader_count = max(1, count // 3)
    selected = list(ranked[:leader_count])
    selected_ids = {id(item[0]) for item in selected}
    buckets: dict[tuple, list[tuple[dict, object]]] = {}
    for item in ranked[leader_count:]:
        p = item[0]
        signature = tuple(
            value
            for stage in ("s0", "s1")
            for value in (
                int(p.get(f"{stage}_grid_fin_count", 0)),
                round(
                    int(p.get(f"{stage}_grid_fin_count", 0))
                    * float(p.get(f"{stage}_grid_fin_root", 0.0))
                    * float(p.get(f"{stage}_grid_fin_height", 0.0)),
                    3,
                ),
                round(
                    int(p.get(f"{stage}_core_fin_count", 0))
                    * float(p.get(f"{stage}_core_fin_root", 0.0))
                    * float(p.get(f"{stage}_core_fin_height", 0.0)),
                    3,
                ),
            )
        ) + (
            round(float(p.get("nose_mass_kg", 0.0)) * 2.0) / 2.0,
            round(float(p.get("s0_aft_ballast_kg", 0.0)) * 4.0) / 4.0,
            int(p["s0_retro"]),
            int(p["s1_retro"]),
        )
        buckets.setdefault(signature, []).append(item)
    while len(selected) < count and buckets:
        ordered_signatures = sorted(
            buckets,
            key=lambda signature: (
                signature[6],
                0 if signature[0] == 0 else 1,
                0 if signature[3] == 0 else 1,
                -signature[1],
                -signature[4],
            ),
        )
        for signature in ordered_signatures:
            bucket = buckets[signature]
            while bucket and id(bucket[0][0]) in selected_ids:
                bucket.pop(0)
            if bucket:
                item = bucket.pop(0)
                selected.append(item)
                selected_ids.add(id(item[0]))
                if len(selected) >= count:
                    break
            if not bucket:
                buckets.pop(signature, None)
    return selected


def _audit_physical_finalists(finalists: list[tuple[dict, object]]) -> None:
    """Fail a cycle before OR if any finalist violates the shared build model."""
    for index, (parameters, _result) in enumerate(finalists):
        violations = _podset_geometry_violations(parameters)
        if violations:
            raise RuntimeError(
                f"physical audit failed for finalist {index}: "
                + "; ".join(violations)
            )
        parameters_to_ast(parameters)
        osifog_podset.generate_podset_ork(parameters)


def _authority_stage_elites(
    records: list[dict],
    branch: int,
    count: int = 3,
) -> list[dict]:
    ranked = []
    for record in records:
        metrics = record.get("metrics")
        if not isinstance(metrics, dict):
            continue
        if float(metrics.get("mach", 99.0)) >= osifog_sweep.MAX_MACH:
            continue
        # Segment-aware stability filter: full_stack >= 0.5, sustainer >= 1.5 cal
        stability_segments = metrics.get("ascent_stability_segments", [])
        if stability_segments:
            seg_map = {s["segment"]: s["min_calibers"] for s in stability_segments}
            if seg_map.get("full_stack", float("-inf")) < osifog_sweep.MIN_FULL_STACK_MARGIN:
                continue
            if seg_map.get("sustainer", float("-inf")) < osifog_sweep.MIN_STATIC_MARGIN:
                continue
        elif float(metrics.get("min_static_margin", -99.0)) < osifog_sweep.MIN_STATIC_MARGIN:
            continue
        landing = next(
            (
                item
                for item in metrics.get("stage_landings", [])
                if int(item.get("branch", -1)) == branch
            ),
            None,
        )
        if landing is None:
            continue
        alignment = next(
            (
                item
                for item in metrics.get("descent_alignment_diagnostics", [])
                if int(item.get("branch", -1)) == branch
            ),
            {},
        )
        ranked.append(
            (
                0 if alignment.get("tail_first_windows") else 1,
                float(landing.get("total_speed", 1.0e9)),
                float(landing.get("dist_m", 1.0e9)),
                3000.0 * (float(metrics.get("apogee_m", 0.0)) - 3000.0) ** 2,
                record,
            )
        )
    ranked.sort(key=lambda item: item[:-1])
    return [item[-1] for item in ranked[:count]]


def _authority_recombinations(records: list[dict], limit: int = 9) -> list[dict]:
    """Combine independently strong stage descents, then require full recheck."""
    stage0 = _authority_stage_elites(records, 0)
    stage1 = _authority_stage_elites(records, 1)
    baselines = sorted(
        (record for record in records if isinstance(record.get("metrics"), dict)),
        key=lambda record: 3000.0 * (
            float(record["metrics"].get("apogee_m", 0.0)) - 3000.0
        ) ** 2,
    )
    if not stage0 or not stage1 or not baselines:
        return []
    results = []
    seen = set()
    for left in stage0:
        for right in stage1:
            parameters = dict(baselines[0]["parameters"])
            parameters.update(
                {
                    key: value
                    for key, value in left["parameters"].items()
                    if key.startswith("s0_") or key.startswith("nose_")
                }
            )
            parameters.update(
                {
                    key: value
                    for key, value in right["parameters"].items()
                    if key.startswith("s1_")
                }
            )
            identity = json.dumps(parameters, sort_keys=True)
            if identity in seen:
                continue
            seen.add(identity)
            if _podset_geometry_violations(parameters):
                continue
            parameters_to_ast(parameters)
            osifog_podset.generate_podset_ork(parameters)
            results.append(parameters)
            if len(results) >= limit:
                return results
    return results


def _ascent_admissible(metrics: dict, parameters: dict) -> tuple[bool, list[str]]:
    violations = _podset_geometry_violations(parameters)
    if float(metrics.get("mach", 99.0)) >= osifog_sweep.MAX_MACH:
        violations.append("supersonic")
    # Segment-aware stability: full_stack >= 0.5 cal during boost, sustainer >= 1.5 cal after staging
    stability_segments = metrics.get("ascent_stability_segments", [])
    if stability_segments:
        seg_map = {s["segment"]: s["min_calibers"] for s in stability_segments}
        full_stack_margin = seg_map.get("full_stack", float("-inf"))
        sustainer_margin = seg_map.get("sustainer", float("-inf"))
        if full_stack_margin < osifog_sweep.MIN_FULL_STACK_MARGIN:
            violations.append(
                f"full-stack stability {full_stack_margin:.3f} cal < {osifog_sweep.MIN_FULL_STACK_MARGIN:.1f} cal"
            )
        if sustainer_margin < osifog_sweep.MIN_STATIC_MARGIN:
            violations.append(
                f"sustainer stability {sustainer_margin:.3f} cal < {osifog_sweep.MIN_STATIC_MARGIN:.1f} cal"
            )
    else:
        if float(metrics.get("min_static_margin", -99.0)) < osifog_sweep.MIN_STATIC_MARGIN:
            violations.append("ascent stability")
    if len(metrics.get("stage_landings", [])) != 2:
        violations.append("missing free-impact branch")
    events = metrics.get("event_times", {})
    separations = events.get("STAGE_SEPARATION", [])
    apogees = events.get("APOGEE", [])
    if not separations or (apogees and min(separations) >= min(apogees)):
        violations.append("non-genuine staging")
    return not violations, violations



def _central_burn_time(metrics: dict, branch: int, delay_s: float = 200.0) -> float:
    events = metrics["branch_event_times"][branch]
    burnouts = [
        float(value)
        for value in events.get("BURNOUT", [])
        if float(value) > delay_s + 1.0e-6
    ]
    return min(burnouts) - delay_s if burnouts else 2.0


def _stage_key_for_branch(metrics: dict, branch: int) -> str:
    matches = [
        item.get("stage_key")
        for item in metrics.get("branch_identities", [])
        if int(item.get("branch", -1)) == branch
    ]
    if len(matches) != 1 or matches[0] not in {"s0", "s1"}:
        raise ValueError(f"branch {branch} has no deterministic stage identity")
    return str(matches[0])


@lru_cache(maxsize=None)
def _load_motor_curve(motor_index: int) -> dict:
    """Load one OpenRocket-sourced ENG curve in seconds, newtons, and kg."""
    designation = MOTOR_DATABASE[motor_index][1]
    path = REPO_ROOT / "l2_engine" / "motors" / f"{designation}.eng"
    points: list[tuple[float, float]] = []
    header = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        fields = line.split()
        if header is None:
            if len(fields) < 7 or fields[0] != designation:
                raise ValueError(f"invalid ENG header: {path}")
            header = fields
            continue
        if len(fields) != 2:
            raise ValueError(f"invalid ENG thrust point: {path}: {line}")
        points.append((float(fields[0]), float(fields[1])))
    if len(points) < 2:
        raise ValueError(f"motor curve has insufficient points: {path}")
    if any(
        not math.isfinite(value) or value < 0.0
        for point in points
        for value in point
    ):
        raise ValueError(f"motor curve contains non-finite or negative data: {path}")
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise ValueError(f"motor curve times are not strictly increasing: {path}")
    propellant_mass_kg = float(header[4])
    loaded_mass_kg = float(header[5])
    if not 0.0 < propellant_mass_kg <= loaded_mass_kg:
        raise ValueError(f"invalid ENG motor masses: {path}")
    return {
        "designation": designation,
        "source": str(path),
        "time_unit": "s",
        "thrust_unit": "N",
        "mass_unit": "kg",
        "points": points,
        "burn_duration_s": points[-1][0],
        "propellant_mass_kg": propellant_mass_kg,
        "loaded_mass_kg": loaded_mass_kg,
    }


def _interpolate_trace(trace: list[dict], time_s: float, field: str) -> float:
    if time_s <= float(trace[0]["time_s"]):
        return float(trace[0][field])
    if time_s >= float(trace[-1]["time_s"]):
        return float(trace[-1][field])
    index = bisect_left(trace, time_s, key=lambda item: float(item["time_s"]))
    right = trace[index]
    left = trace[index - 1]
    span = float(right["time_s"]) - float(left["time_s"])
    fraction = (time_s - float(left["time_s"])) / span if span else 1.0
    return float(left[field]) + fraction * (
        float(right[field]) - float(left[field])
    )


def _interpolate_curve(curve: list[tuple[float, float]], elapsed_s: float) -> float:
    if elapsed_s <= curve[0][0]:
        return curve[0][1]
    if elapsed_s >= curve[-1][0]:
        return curve[-1][1]
    index = bisect_left(curve, elapsed_s, key=lambda item: item[0])
    right_t, right_thrust = curve[index]
    left_t, left_thrust = curve[index - 1]
    fraction = (elapsed_s - left_t) / (right_t - left_t)
    return left_thrust + fraction * (right_thrust - left_thrust)


def _curve_impulse(
    curve: list[tuple[float, float]], end_time_s: float | None = None
) -> float:
    """Integrate a piecewise-linear thrust curve with irregular time spacing."""
    end = curve[-1][0] if end_time_s is None else max(
        curve[0][0], min(float(end_time_s), curve[-1][0])
    )
    times = {curve[0][0], end}
    times.update(time for time, _ in curve if curve[0][0] < time < end)
    ordered = sorted(times)
    return sum(
        0.5
        * (
            _interpolate_curve(curve, left)
            + _interpolate_curve(curve, right)
        )
        * (right - left)
        for left, right in zip(ordered, ordered[1:])
    )


def _landing_opportunity(
    metrics: dict,
    parameters: dict,
    branch: int,
    ignition_time_s: float,
) -> dict:
    """Screen one free-fall branch against a complete real motor burn."""
    diagnostic = next(
        item
        for item in metrics["descent_alignment_diagnostics"]
        if int(item["branch"]) == branch
    )
    trace = diagnostic.get("alignment_trace", [])
    landing = next(
        item
        for item in metrics["stage_landings"]
        if int(item["branch"]) == branch
    )
    stage_key = diagnostic.get("stage_key")
    if stage_key not in {"s0", "s1"}:
        return {
            "branch": branch,
            "usable": False,
            "rejection_reasons": ["missing deterministic branch-to-stage identity"],
        }
    motor_key = f"{stage_key}_retro"
    if motor_key not in parameters:
        return {
            "branch": branch,
            "stage_key": stage_key,
            "usable": False,
            "rejection_reasons": ["missing landing motor identity"],
        }
    motor_index = int(parameters[motor_key])
    motor = _load_motor_curve(motor_index)
    curve = motor["points"]
    burn_duration = float(motor["burn_duration_s"])
    burnout = ignition_time_s + burn_duration
    impact = float(landing["time_s"])
    mass = float(landing["mass_kg"])
    required_dv = max(0.0, float(landing["total_speed"]) - 4.5)
    result = {
        "branch": branch,
        "stage_key": stage_key,
        "motor": motor["designation"],
        "motor_curve_source": motor["source"],
        "motor_curve_units": {"time": "s", "thrust": "N", "mass": "kg"},
        "free_impact_time_s": impact,
        "free_impact_speed_ms": float(landing["total_speed"]),
        "candidate_ignition_time_s": ignition_time_s,
        "motor_burn_duration_s": burn_duration,
        "burnout_time_s": burnout,
        "stage_mass_kg": mass,
        "required_delta_v_ms": required_dv,
        "mass_model": "impulse-proportional propellant depletion",
    }
    missing = []
    if not trace:
        missing.append("missing alignment trace")
    if not math.isfinite(mass) or mass <= 0.0:
        missing.append("missing or invalid stage mass")
    branch_events = metrics.get("branch_event_times", [])
    events = branch_events[branch] if branch < len(branch_events) else {}
    separations = events.get("STAGE_SEPARATION", [])
    if not separations:
        separations = metrics.get("event_times", {}).get("STAGE_SEPARATION", [])
    if not separations:
        missing.append("missing separation event")
    if missing:
        return {**result, "usable": False, "rejection_reasons": missing}

    integration_end = min(burnout, impact)
    if ignition_time_s >= impact:
        return {
            **result,
            "usable": False,
            "rejection_reasons": ["candidate ignition is at or after impact"],
        }
    if ignition_time_s <= min(float(value) for value in separations):
        return {
            **result,
            "usable": False,
            "rejection_reasons": ["candidate ignition is at or before separation"],
        }
    times = {ignition_time_s, integration_end}
    times.update(
        float(sample["time_s"])
        for sample in trace
        if ignition_time_s < float(sample["time_s"]) < integration_end
    )
    times.update(
        ignition_time_s + elapsed
        for elapsed, _thrust in curve
        if ignition_time_s < ignition_time_s + elapsed < integration_end
    )
    ordered = sorted(times)
    opposing_impulse = 0.0
    vertical_impulse = 0.0
    total_impulse = 0.0
    opposing_delta_v = 0.0
    vertical_delta_v = 0.0
    usable_duration = 0.0
    weighted_q = 0.0
    minimum_q = 1.0
    total_curve_impulse = _curve_impulse(curve)

    def stage_mass_at(time_s: float) -> float:
        consumed_fraction = (
            _curve_impulse(curve, time_s - ignition_time_s)
            / total_curve_impulse
            if total_curve_impulse > 0.0
            else 0.0
        )
        return mass - float(motor["propellant_mass_kg"]) * consumed_fraction

    for left, right in zip(ordered, ordered[1:]):
        midpoint = (left + right) / 2.0
        dt = right - left
        evaluations = []
        for time_s in (left, midpoint, right):
            thrust = _interpolate_curve(curve, time_s - ignition_time_s)
            q = _interpolate_trace(trace, time_s, "alignment_q")
            vertical_q = _interpolate_trace(
                trace, time_s, "vertical_alignment_q"
            )
            evaluations.append(
                (
                    thrust,
                    q,
                    vertical_q,
                    stage_mass_at(time_s),
                )
            )
        left_values, mid_values, right_values = evaluations
        impulse = (
            left_values[0] + 4.0 * mid_values[0] + right_values[0]
        ) * dt / 6.0
        opposed = (
            max(left_values[1], 0.0) * left_values[0]
            + 4.0 * max(mid_values[1], 0.0) * mid_values[0]
            + max(right_values[1], 0.0) * right_values[0]
        ) * dt / 6.0
        vertical = (
            max(left_values[2], 0.0) * left_values[0]
            + 4.0 * max(mid_values[2], 0.0) * mid_values[0]
            + max(right_values[2], 0.0) * right_values[0]
        ) * dt / 6.0
        total_impulse += impulse
        opposing_impulse += opposed
        vertical_impulse += vertical
        weighted_q += (
            left_values[1] * left_values[0]
            + 4.0 * mid_values[1] * mid_values[0]
            + right_values[1] * right_values[0]
        ) * dt / 6.0
        opposing_delta_v += (
            max(left_values[1], 0.0) * left_values[0] / left_values[3]
            + 4.0
            * max(mid_values[1], 0.0)
            * mid_values[0]
            / mid_values[3]
            + max(right_values[1], 0.0) * right_values[0] / right_values[3]
        ) * dt / 6.0
        vertical_delta_v += (
            max(left_values[2], 0.0) * left_values[0] / left_values[3]
            + 4.0
            * max(mid_values[2], 0.0)
            * mid_values[0]
            / mid_values[3]
            + max(right_values[2], 0.0) * right_values[0] / right_values[3]
        ) * dt / 6.0
        minimum_q = min(
            minimum_q,
            left_values[1],
            mid_values[1],
            right_values[1],
        )
        if min(value[1] for value in evaluations) >= 0.5:
            usable_duration += dt

    available_dv = opposing_delta_v
    fraction_opposing = opposing_impulse / total_impulse if total_impulse else 0.0
    fraction_vertical = vertical_impulse / total_impulse if total_impulse else 0.0
    reasons = []
    if usable_duration + 0.05 < burn_duration:
        reasons.append("tail-first window shorter than motor burn")
    if burnout > impact + 0.30:
        reasons.append("motor cannot burn out by contact allowance")
    if available_dv < required_dv * 1.05:
        reasons.append("opposing delta-v below required margin")
    if fraction_opposing < 0.70:
        reasons.append("poor burn-weighted total-velocity alignment")
    if fraction_vertical < 0.70:
        reasons.append("poor burn-weighted vertical alignment")
    result.update(
        {
            "ignition_altitude_m": _interpolate_trace(
                trace, ignition_time_s, "altitude_m"
            ),
            "burnout_altitude_m": _interpolate_trace(
                trace, min(burnout, impact), "altitude_m"
            ),
            "usable_tail_first_duration_s": usable_duration,
            "mean_burn_weighted_q": (
                weighted_q / total_impulse if total_impulse else -1.0
            ),
            "minimum_burn_q": minimum_q,
            "opposing_impulse_ns": opposing_impulse,
            "vertical_braking_impulse_ns": vertical_impulse,
            "available_delta_v_ms": available_dv,
            "available_vertical_delta_v_ms": vertical_delta_v,
            "predicted_touchdown_speed_ms": max(
                0.0, float(landing["total_speed"]) - available_dv
            ),
            "fraction_burn_opposing_total_velocity": fraction_opposing,
            "fraction_burn_opposing_vertical_velocity": fraction_vertical,
            "predicted_burnout_to_impact_coast_s": impact - burnout,
            "terminal_theta_deg": float(trace[-1]["theta_deg"]),
            "terminal_horizontal_speed_ms": float(
                trace[-1]["horizontal_speed_ms"]
            ),
            "usable": not reasons,
            "rejection_reasons": reasons,
        }
    )
    return result


def _delay_candidates(
    metrics: dict,
    parameters: dict,
    branch: int,
    limit: int = 25,
) -> list[float]:
    diagnostic = next(
        item
        for item in metrics["descent_alignment_diagnostics"]
        if int(item["branch"]) == branch
    )
    windows = diagnostic.get("tail_first_windows", [])
    landing = next(
        item for item in metrics["stage_landings"] if int(item["branch"]) == branch
    )
    impact_time = float(landing["time_s"])
    burn_time = _central_burn_time(metrics, branch)
    ideal = impact_time - burn_time
    apogee_events = metrics["branch_event_times"][branch].get("APOGEE", [])
    if not apogee_events:
        # A malformed/non-flying branch is useful negative evidence for
        # evolution, but it has no physically meaningful ignition interval.
        return []
    apogee = min(float(value) for value in apogee_events)
    # The analytical screen prioritizes trials; it is not an authority veto.
    # Always retain a small impact/burn-time stencil so promoted candidates
    # receive real powered OpenRocket evidence even when the coarse attitude
    # trace contains no threshold-crossing window.
    values = {
        round(min(impact_time - 0.001, max(apogee + 0.001, ideal + offset)), 6)
        for offset in (-1.0, -0.5, 0.0, 0.5, 1.0)
    }
    vertical_priority = {}
    for window in windows:
        start = max(float(window["start_time_s"]), apogee + 0.001)
        end = min(float(window["end_time_s"]), impact_time - 0.001)
        if end < start:
            continue
        best_q = float(window.get("best_alignment_q", 0.5))
        anchors = (
            start,
            (start + end) / 2.0,
            end,
            min(end, max(start, ideal)),
            min(end, max(start, ideal - 0.5)),
            min(end, max(start, ideal + 0.5)),
        )
        for a in anchors:
            rv = round(a, 6)
            values.add(rv)
            vertical_priority[rv] = max(vertical_priority.get(rv, -1.0), best_q)
    # Tumble phase is materially narrower than the coarse window envelope.
    # OpenRocket retains the strongest q samples so the authority search can
    # ignite at (or shortly before) the actual favorable attitude.
    for sample in diagnostic.get("alignment_candidates", []):
        sample_time = float(sample["time_s"])
        if sample_time <= apogee or sample_time >= impact_time:
            continue
        for lead in (0.0, 0.25 * burn_time, 0.50 * burn_time):
            values.add(round(max(apogee + 0.001, sample_time - lead), 6))
    # Total-velocity alignment can be excellent while the motor points mostly
    # sideways. Preserve ignition anchors whose complete stored attitude trace
    # opposes both total and vertical velocity; these are the actual landing
    # windows, not merely low-speed vector matches.
    for sample in diagnostic.get("alignment_trace", []):
        q_opp = float(sample.get("alignment_q", -1.0))
        vq_opp = float(sample.get("vertical_alignment_q", -1.0))
        if min(q_opp, vq_opp) < 0.5:
            continue
        sample_time = float(sample["time_s"])
        if sample_time <= apogee or sample_time >= impact_time:
            continue
        for lead in (0.0, 0.25 * burn_time, 0.50 * burn_time):
            value = round(max(apogee + 0.001, sample_time - lead), 6)
            values.add(value)
            vertical_priority[value] = max(
                vertical_priority.get(value, -1.0),
                min(q_opp, vq_opp),
            )
    prioritized = sorted(
        vertical_priority,
        key=lambda value: (-vertical_priority[value], abs(value - ideal)),
    )
    remainder = sorted(
        values - set(vertical_priority), key=lambda value: abs(value - ideal)
    )
    return (prioritized + remainder)[:limit]


def _stage_polish_rank(metrics: dict, branch: int) -> tuple:
    landing = next(
        item for item in metrics.get("stage_landings", [])
        if int(item.get("branch", -1)) == branch
    )
    diagnostic = next(
        item for item in metrics.get("retro_burn_diagnostics", [])
        if int(item.get("branch", -1)) == branch
    )
    fraction = float(diagnostic.get("fraction_opposing_velocity", 0.0))
    speed = float(landing["total_speed"])
    return (
        0 if diagnostic.get("retro_braking_verified", False) else 1,
        0 if speed < 5.0 else 1,
        speed,
        -fraction,
        float(landing["dist_m"]),
    )


def _powered_trial_summary(
    metrics: dict, stage_key: str, branch: int, delay: float
) -> dict:
    landing = next(
        (
            item for item in metrics.get("stage_landings", [])
            if int(item.get("branch", -1)) == branch
        ),
        None,
    )
    diagnostic = next(
        (
            item for item in metrics.get("retro_burn_diagnostics", [])
            if int(item.get("branch", -1)) == branch
        ),
        None,
    )
    return {
        "stage_key": stage_key,
        "branch": branch,
        "delay_s": delay,
        "landing": landing,
        "retro_burn_diagnostic": diagnostic,
        "scenario_type": metrics.get("scenario_manifest", {}).get("scenario_type"),
    }


def _run_authority(
    parameters: dict,
    scenario_type: str,
    candidate_id: str = "unassigned",
) -> dict:
    ork_xml = osifog_podset.generate_podset_ork(parameters)
    manifest = build_scenario_manifest(
        scenario_type, candidate_id, parameters, ork_xml
    )
    validate_scenario_manifest(manifest)
    osifog_sweep.init_or()
    metrics = osifog_sweep.run_sim(
        ork_xml,
        seed=osifog_sweep.SIM_SEED,
    )
    metrics["scenario_manifest"] = manifest
    return metrics


def _default_openrocket_evaluator(parameters: dict) -> tuple[dict, dict, dict]:
    free_parameters = dict(
        parameters,
        s0_retro_delay=0.0,
        s1_retro_delay=0.0,
        s0_retro_ignition_event="never",
        s1_retro_ignition_event="never",
    )
    free_metrics = _run_authority(
        free_parameters, "STAGE_FREE_DESCENT_DIAGNOSTIC"
    )
    admissible, violations = _ascent_admissible(free_metrics, free_parameters)
    if not admissible:
        free_metrics["powered_stage_trials"] = []
        free_metrics["authority_gate"] = "ascent_rejected"
        score = osifog_sweep.score_official(free_metrics, free_parameters)
        score.update(is_legal=False, violations=violations)
        return free_metrics, score, free_parameters

    tuned = dict(free_parameters)
    free_metrics["landing_opportunities"] = []
    powered_trial_evidence = []
    for branch in (0, 1):
        stage_key = _stage_key_for_branch(free_metrics, branch)
        candidates = _delay_candidates(free_metrics, tuned, branch)
        if not candidates:
            score = osifog_sweep.score_official(free_metrics, tuned)
            score.update(
                is_legal=False,
                violations=[f"branch {branch} has no tail-first ignition window"],
            )
            return free_metrics, score, tuned

        opportunities = [
            _landing_opportunity(free_metrics, tuned, branch, delay)
            for delay in candidates
        ]
        free_metrics["landing_opportunities"].extend(opportunities)
        usable = [item for item in opportunities if item["usable"]]
        ranked = usable if usable else opportunities
        if not usable:
            free_metrics.setdefault("heuristic_override_branches", []).append(branch)
        ranked.sort(
            key=lambda item: (
                float(item.get("predicted_touchdown_speed_ms", math.inf)),
                -float(item.get("opposing_impulse_ns", 0.0)),
                -float(item.get("mean_burn_weighted_q", -1.0)),
                abs(float(item.get("predicted_burnout_to_impact_coast_s", math.inf))),
            )
        )
        candidates = [
            float(item["candidate_ignition_time_s"])
            for item in ranked[: min(5, len(ranked))]
        ]

        stage_results = []
        for delay in candidates:
            trial = dict(
                tuned,
                **{
                    f"{stage_key}_retro_delay": delay,
                    f"{stage_key}_retro_ignition_event": "launch",
                    f"{'s1' if stage_key == 's0' else 's0'}_retro_delay": 0.0,
                    f"{'s1' if stage_key == 's0' else 's0'}_retro_ignition_event": "never",
                },
            )
            metrics = _run_authority(
                trial, "POWERED_STAGE_LANDING_VALIDATION"
            )
            powered_trial_evidence.append(
                _powered_trial_summary(metrics, stage_key, branch, delay)
            )
            stage_results.append(
                (_stage_polish_rank(metrics, branch), delay, metrics)
            )
        stage_results.sort(key=lambda item: item[0])
        best_rank, best_delay, _best_metrics = stage_results[0]

        # Once the real motor curve gets within striking distance, refine the
        # discontinuous ground-contact boundary hierarchically.  This is where
        # millisecond changes can separate a legal landing from a crash, while
        # avoiding a dense sweep for obviously unsuitable designs.
        if float(best_rank[2]) < 15.0:
            for step, radius in ((0.01, 0.10), (0.001, 0.012)):
                refined = []
                count = int(round(radius / step))
                for offset in range(-count, count + 1):
                    delay = round(best_delay + offset * step, 6)
                    trial = dict(
                        tuned,
                        **{
                            f"{stage_key}_retro_delay": delay,
                            f"{stage_key}_retro_ignition_event": "launch",
                            f"{'s1' if stage_key == 's0' else 's0'}_retro_delay": 0.0,
                            f"{'s1' if stage_key == 's0' else 's0'}_retro_ignition_event": "never",
                        },
                    )
                    metrics = _run_authority(
                        trial, "POWERED_STAGE_LANDING_VALIDATION"
                    )
                    powered_trial_evidence.append(
                        _powered_trial_summary(metrics, stage_key, branch, delay)
                    )
                    refined.append(
                        (_stage_polish_rank(metrics, branch), delay, metrics)
                    )
                refined.sort(key=lambda item: item[0])
                best_rank, best_delay, _best_metrics = refined[0]
        tuned[f"{stage_key}_retro_delay"] = best_delay
        tuned[f"{stage_key}_retro_ignition_event"] = "launch"

    metrics = _run_authority(tuned, "OFFICIAL_FULL_MISSION")
    metrics["powered_stage_trials"] = powered_trial_evidence
    metrics["free_descent_screen"] = {
        "stage_landings": free_metrics.get("stage_landings", []),
        "landing_opportunities": free_metrics.get("landing_opportunities", []),
        "heuristic_override_branches": free_metrics.get(
            "heuristic_override_branches", []
        ),
    }
    metrics["authority_gate"] = "powered_trials_completed"
    legal, violations = osifog_sweep.validate_official_constraints(metrics, tuned)
    score = osifog_sweep.score_official(metrics, tuned)
    score["is_legal"] = legal
    score["violations"] = violations
    return metrics, score, tuned


def _landing_by_stage(metrics: dict) -> dict[str, dict]:
    result = {}
    for landing in metrics.get("stage_landings", []):
        stage_key = landing.get("stage_key")
        if stage_key in ("s0", "s1"):
            result[stage_key] = landing
    return result


def _authority_convergence(coarse: dict, fine: dict) -> dict:
    coarse_landings = _landing_by_stage(coarse["metrics"])
    fine_landings = _landing_by_stage(fine["metrics"])
    if set(coarse_landings) != {"s0", "s1"} or set(fine_landings) != {"s0", "s1"}:
        return {"converged": False, "reason": "missing deterministic stage landing identity"}
    speed_delta = max(
        abs(
            float(coarse_landings[key]["total_speed"])
            - float(fine_landings[key]["total_speed"])
        )
        for key in ("s0", "s1")
    )
    position_delta = max(
        math.hypot(
            float(coarse_landings[key]["east_m"])
            - float(fine_landings[key]["east_m"]),
            float(coarse_landings[key]["north_m"])
            - float(fine_landings[key]["north_m"]),
        )
        for key in ("s0", "s1")
    )
    deltas = {
        "apogee_m": abs(
            float(coarse["metrics"]["apogee_m"])
            - float(fine["metrics"]["apogee_m"])
        ),
        "mach": abs(
            float(coarse["metrics"]["mach"])
            - float(fine["metrics"]["mach"])
        ),
        "landing_speed_ms": speed_delta,
        "landing_position_m": position_delta,
        "score": abs(
            float(coarse["official"]["raw_score"])
            - float(fine["official"]["raw_score"])
        ),
    }
    return {
        "converged": all(
            deltas[name] <= AUTHORITY_CONVERGENCE_LIMITS[name]
            for name in AUTHORITY_CONVERGENCE_LIMITS
        ),
        "deltas": deltas,
        "limits": AUTHORITY_CONVERGENCE_LIMITS,
        "compared_timesteps_s": [coarse["timestep_s"], fine["timestep_s"]],
    }


def _certify_authority_candidate(parameters: dict, output_dir: Path) -> dict:
    """Fresh-run numerical convergence plus repeated saved-artifact inspection."""
    from osifog_precision import inspect_saved_submission

    candidate_id = _candidate_id(parameters)
    fresh_runs = []
    for timestep_s in AUTHORITY_TIMESTEP_LADDER_S:
        trial = dict(parameters, timestep_s=timestep_s)
        metrics = _run_authority(
            trial,
            "OFFICIAL_FULL_MISSION",
            candidate_id=candidate_id,
        )
        official = osifog_sweep.score_official(metrics, trial)
        fresh_runs.append(
            {
                "timestep_s": timestep_s,
                "metrics": metrics,
                "official": official,
            }
        )
    convergence = _authority_convergence(fresh_runs[-2], fresh_runs[-1])
    fine_parameters = dict(parameters, timestep_s=AUTHORITY_TIMESTEP_LADDER_S[-1])
    authority_path = output_dir / "best-authority.ork"
    osifog_sweep.save_simulated_ork(
        osifog_podset.generate_podset_ork(fine_parameters),
        str(authority_path),
    )
    replay_metrics = [
        inspect_saved_submission(authority_path, fine_parameters)
        for _ in range(5)
    ]
    replay_scores = [
        osifog_sweep.score_official(metrics, fine_parameters)
        for metrics in replay_metrics
    ]
    replay_digests = [_canonical_digest(metrics) for metrics in replay_metrics]
    deterministic = len(set(replay_digests)) == 1
    all_fresh_legal = all(
        run["official"].get("is_legal", False) for run in fresh_runs
    )
    persisted_legal = all(score.get("is_legal", False) for score in replay_scores)
    return {
        "path": str(authority_path),
        "metrics": replay_metrics[0],
        "official": replay_scores[0],
        "replay_count": len(replay_metrics),
        "replay_digests": replay_digests,
        "deterministic": deterministic,
        "fresh_run_count": len(fresh_runs),
        "fresh_runs": [
            {
                "timestep_s": run["timestep_s"],
                "official": run["official"],
                "metrics": {
                    "apogee_m": run["metrics"].get("apogee_m"),
                    "mach": run["metrics"].get("mach"),
                    "min_static_margin": run["metrics"].get("min_static_margin"),
                    "stage_landings": run["metrics"].get("stage_landings"),
                    "m_prop_kg_actual": run["metrics"].get("m_prop_kg_actual"),
                },
            }
            for run in fresh_runs
        ],
        "numerical_convergence": convergence,
        "certified": (
            deterministic
            and all_fresh_legal
            and persisted_legal
            and convergence.get("converged", False)
        ),
    }


def _isolated_openrocket_evaluator(parameters: dict):
    """Run one authority candidate behind a killable wall-clock boundary."""
    timeout_s = float(os.environ.get("OSIFOG_AUTHORITY_TIMEOUT_S", "120"))
    worker = Path(__file__).with_name("osifog_authority_worker.py")
    worker_python = getattr(sys, "_base_executable", sys.executable)
    worker_env = os.environ.copy()
    site_packages = str(Path(sys.prefix) / "Lib" / "site-packages")
    src_dir = str(Path(__file__).resolve().parent)
    repo_root_str = str(REPO_ROOT)
    worker_env["PYTHONPATH"] = os.pathsep.join(
        item for item in (src_dir, repo_root_str, site_packages, worker_env.get("PYTHONPATH", "")) if item
    )
    try:
        completed = subprocess.run(
            [worker_python, str(worker)],
            input=json.dumps({"mode": "official", "parameters": parameters}, allow_nan=False),
            text=True,
            capture_output=True,
            timeout=timeout_s,
            cwd=REPO_ROOT,
            env=worker_env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"OpenRocket authority candidate exceeded {timeout_s:.0f}s wall-clock limit"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-2000:]
        raise RuntimeError(
            f"isolated OpenRocket authority worker exited {completed.returncode}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError(
            "isolated OpenRocket authority worker returned invalid JSON: "
            + completed.stdout[-1000:]
        ) from exc
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("error", "isolated authority evaluation failed"))
    return payload["metrics"], payload["official"], payload["parameters"]


def _isolated_recovery_gate_evaluator(parameters: dict) -> dict:
    """Authority ascent/free-descent diagnostic with the same isolation."""
    timeout_s = float(os.environ.get("OSIFOG_AUTHORITY_TIMEOUT_S", "120"))
    worker = Path(__file__).with_name("osifog_authority_worker.py")
    worker_python = getattr(sys, "_base_executable", sys.executable)
    worker_env = os.environ.copy()
    site_packages = str(Path(sys.prefix) / "Lib" / "site-packages")
    src_dir = str(Path(__file__).resolve().parent)
    repo_root_str = str(REPO_ROOT)
    worker_env["PYTHONPATH"] = os.pathsep.join(
        item for item in (src_dir, repo_root_str, site_packages, worker_env.get("PYTHONPATH", "")) if item
    )
    try:
        completed = subprocess.run(
            [worker_python, str(worker)],
            input=json.dumps({"mode": "recovery_gate", "parameters": parameters}, allow_nan=False),
            text=True, capture_output=True, timeout=timeout_s,
            cwd=REPO_ROOT, env=worker_env, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"OpenRocket recovery gate exceeded {timeout_s:.0f}s wall-clock limit"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[-2000:])
    payload = json.loads(completed.stdout)
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("error", "recovery gate failed"))
    return payload["metrics"]


def run_search(
    config: SearchConfig,
    *,
    rust_evaluator: Callable | None = None,
    openrocket_evaluator: Callable | None = None,
) -> dict:
    output_dir = Path(config.output_dir)
    _write_health(output_dir, "running", "rust_population")
    checkpoint = output_dir / "checkpoint.json"
    wind_levels = osifog_sweep.parse_wind_csv(config.wind_csv)
    rng = random.Random(config.seed)
    calibration = load_authority_calibration(config.calibration_result)
    if rust_evaluator is not None:
        parameters = [
            _sample_valid_parameters(rng, wind_levels)
            for _ in range(config.rust_budget)
        ]
        asts = [parameters_to_ast(item) for item in parameters]
        rust_pairs = list(zip(parameters, rust_evaluator(asts)))
    else:
        rust_pairs = _evolve_rust_candidates(
            config, rng, wind_levels, calibration
        )
    ranked = sorted(
        (
            (parameters, result)
            for parameters, result in rust_pairs
            if result.status == "success"
        ),
        key=lambda item: item[1].score,
        reverse=True,
    )
    if not ranked:
        raise RuntimeError(
            "Rust screening produced zero successful physical candidates; "
            "inspect rust-evolution.json failure reasons"
        )
    if rust_evaluator is None:
        promotion_count = max(
            config.finalist_budget * 4, config.finalist_budget
        )
        ranked = _promote_rust_candidates(
            _stratify_candidates(ranked, promotion_count),
            calibration,
            promotion_count,
        )
        ranked = _stratify_candidates(ranked, config.finalist_budget)
        _audit_physical_finalists(ranked[: config.finalist_budget])
    prior_results = []
    if config.resume and checkpoint.exists():
        try:
            prior_results = json.loads(checkpoint.read_text(encoding="utf-8")).get(
                "openrocket_results", []
            )
        except (OSError, ValueError):
            prior_results = []
    state = {
        "version": 2,
        "config": {
            **asdict(config),
            "output_dir": str(config.output_dir),
            "wind_csv": str(config.wind_csv),
            "calibration_result": (
                str(config.calibration_result)
                if config.calibration_result is not None
                else None
            ),
            "seed_parameters": (
                str(config.seed_parameters)
                if config.seed_parameters is not None
                else None
            ),
        },
        "authority_calibration": calibration,
        "rust_candidates": [
            {
                "candidate_id": _candidate_id(p),
                "parameters": p,
                "rust": {
                    "score": result.score,
                    "apogee_m": result.rust_apogee_m,
                    "mach": result.rust_mach,
                    "min_static_margin": result.rust_min_static_margin,
                    "stage_landings": getattr(result, "rust_stage_landings", None),
                    "total_prop_mass_kg": getattr(
                        result, "rust_total_prop_mass_kg", 0.0
                    ),
                },
            }
            for p, result in ranked[: config.finalist_budget]
        ],
        "openrocket_results": prior_results,
    }
    _atomic_json(checkpoint, state)
    _write_health(
        output_dir,
        "running",
        "openrocket_authority",
        finalists=len(ranked[: config.finalist_budget]),
        completed=len(prior_results),
    )

    authority = openrocket_evaluator or _isolated_openrocket_evaluator
    completed_ids = {
        item.get("candidate_id") or _candidate_id(item["parameters"])
        for item in prior_results
        if isinstance(item.get("parameters"), dict)
    }
    for index, (p, rust_result) in enumerate(ranked[: config.finalist_budget]):
        candidate_id = _candidate_id(p)
        if config.resume and candidate_id in completed_ids:
            continue
        try:
            evaluation = authority(p)
            if len(evaluation) == 3:
                metrics, official, tuned_parameters = evaluation
            else:
                metrics, official = evaluation
                tuned_parameters = p
            record = {
                "index": index,
                "candidate_id": candidate_id,
                "parameters": tuned_parameters,
                "rust_score": rust_result.score,
                "metrics": metrics,
                "official": official,
            }
        except Exception as exc:
            record = {
                "index": index,
                "candidate_id": candidate_id,
                "parameters": p,
                "error": str(exc),
            }
        state["openrocket_results"].append(record)
        _atomic_json(checkpoint, state)
        _write_health(
            output_dir,
            "running",
            "openrocket_authority",
            finalists=len(ranked[: config.finalist_budget]),
            completed=len(state["openrocket_results"]),
            last_error=record.get("error"),
        )

    if openrocket_evaluator is None:
        recombinations = _authority_recombinations(state["openrocket_results"])
        for parameters in recombinations:
            index = len(state["openrocket_results"])
            candidate_id = _candidate_id(parameters)
            if candidate_id in {
                item.get("candidate_id") or _candidate_id(item["parameters"])
                for item in state["openrocket_results"]
                if isinstance(item.get("parameters"), dict)
            }:
                continue
            try:
                metrics, official, tuned_parameters = authority(parameters)
                record = {
                    "index": index,
                    "candidate_id": candidate_id,
                    "parameters": tuned_parameters,
                    "metrics": metrics,
                    "official": official,
                    "source": "stage_authority_recombination",
                }
            except Exception as exc:
                record = {
                    "index": index,
                    "candidate_id": candidate_id,
                    "parameters": parameters,
                    "error": str(exc),
                    "source": "stage_authority_recombination",
                }
            state["openrocket_results"].append(record)
            _atomic_json(checkpoint, state)

    successful = [r for r in state["openrocket_results"] if "official" in r]
    successful.sort(
        key=lambda record: (
            1 if record["official"].get("is_legal", False) else 0,
            (
                record["official"]["score"]
                if record["official"].get("is_legal", False)
                else record["official"].get("raw_score", -1.0e30)
            ),
        ),
        reverse=True,
    )
    state["best"] = successful[0] if successful else None
    if (
        openrocket_evaluator is None
        and state["best"] is not None
        and state["best"]["official"].get("is_legal", False)
    ):
        best_parameters = state["best"]["parameters"]
        state["persisted_authority"] = _certify_authority_candidate(
            best_parameters, output_dir
        )
    _atomic_json(output_dir / "result.json", state)
    _atomic_json(checkpoint, state)
    _write_health(
        output_dir,
        "complete",
        "finished",
        legal=bool(state["best"] and state["best"]["official"].get("is_legal")),
        best_score=(
            state["best"]["official"].get("score") if state["best"] else None
        ),
    )
    return state


def run_autopilot(
    config: SearchConfig,
    *,
    cycles: int,
    target_score: float = 850_000.0,
) -> dict:
    """Run closed-loop Rust->OR generations until authority reaches the goal."""
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    root = Path(config.output_dir)
    calibration_source = config.calibration_result
    history = []
    consecutive_failures = 0
    for cycle in range(1, cycles + 1):
        cycle_dir = root if cycles == 1 else root / f"cycle-{cycle:03d}"
        attempt = 0
        while True:
            attempt += 1
            cycle_config = SearchConfig(
                rust_budget=config.rust_budget,
                rust_generations=config.rust_generations,
                finalist_budget=config.finalist_budget,
                seed=config.seed + cycle - 1 + (attempt - 1) * 1_000_003,
                output_dir=cycle_dir,
                wind_csv=config.wind_csv,
                resume=config.resume,
                calibration_result=calibration_source,
                seed_parameters=config.seed_parameters,
            )
            _write_health(
                root,
                "running",
                "autopilot_cycle",
                cycle=cycle,
                attempt=attempt,
                consecutive_failures=consecutive_failures,
            )
            try:
                result = run_search(cycle_config)
                consecutive_failures = 0
                break
            except Exception as exc:
                consecutive_failures += 1
                alert = {
                    "status": "retrying" if consecutive_failures < 3 else "failed",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "cycle": cycle,
                    "attempt": attempt,
                    "consecutive_failures": consecutive_failures,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "next_action": (
                        "retry_with_new_seed"
                        if consecutive_failures < 3
                        else "operator_attention_required"
                    ),
                }
                _atomic_json(root / "alert.json", alert)
                _write_health(root, "degraded", "autopilot_retry", alert=alert)
                if consecutive_failures >= 3:
                    raise RuntimeError(
                        "autopilot stopped after three consecutive failures; "
                        f"see {root / 'alert.json'}"
                    ) from exc
        best = result.get("best")
        score = (
            float(best["official"]["score"])
            if best and best.get("official", {}).get("is_legal", False)
            else None
        )
        history.append(
            {
                "cycle": cycle,
                "result": str(cycle_dir / "result.json"),
                "legal_score": score,
                "calibration": result.get("authority_calibration"),
            }
        )
        _atomic_json(
            root / "autopilot.json",
            {
                "target_score": target_score,
                "cycles_requested": cycles,
                "cycles_completed": cycle,
                "history": history,
                "goal_reached": score is not None and score >= target_score,
            },
        )
        _atomic_json(
            root / "alert.json",
            {
                "status": "clear",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "cycle": cycle,
                "message": "latest cycle completed successfully",
            },
        )
        if score is not None and score >= target_score:
            return result
        calibration_source = cycle_dir / "result.json"
    return result


@dataclass(frozen=True)
class CampaignConfig:
    search: SearchConfig
    max_shards: int = 24
    target_score: float = 800_001.0
    bootstrap: Path | None = None
    max_rust_budget: int = 100_000
    max_finalists: int = 96


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # Python's os.kill(pid, 0) is not a harmless existence probe on
        # Windows; it can terminate the target (including the caller itself).
        # Query the process handle without changing process state.
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, int(pid)
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def _campaign_lease(root: Path):
    """Exclusive, crash-recoverable campaign lease."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "campaign.lease.json"
    token = _canonical_digest(
        {"pid": os.getpid(), "host": socket.gethostname(), "time_ns": time.time_ns()}
    )
    lease = {
        "token": token,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(lease, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            break
        except FileExistsError:
            try:
                incumbent = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                incumbent = {}
            if _pid_is_alive(int(incumbent.get("pid", -1))):
                raise RuntimeError(
                    f"campaign already owned by live PID {incumbent['pid']}: {path}"
                )
            stale = root / f"campaign.lease.stale-{time.time_ns()}.json"
            try:
                os.replace(path, stale)
            except FileNotFoundError:
                pass
    try:
        yield lease
    finally:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("token") == token:
                path.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass


def _append_campaign_event(root: Path, event: str, **details) -> None:
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **details,
    }
    root.mkdir(parents=True, exist_ok=True)
    with (root / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _campaign_manifest(config: CampaignConfig) -> dict:
    search = asdict(config.search)
    for key in ("output_dir", "wind_csv", "calibration_result", "seed_parameters"):
        if search.get(key) is not None:
            search[key] = str(search[key])
    source_paths = [
        MISSION_PATH,
        Path(config.search.wind_csv),
        Path(__file__),
        Path(__file__).with_name("osifog_sweep.py"),
        Path(__file__).with_name("osifog_podset.py"),
        Path(__file__).with_name("osifog_precision.py"),
        Path(__file__).with_name("osifog_campaign_watchdog.py"),
        Path(__file__).with_name("rocket_ast.py"),
        REPO_ROOT / "lib" / "OpenRocket-24.12.jar",
        REPO_ROOT / "l2_engine" / "target" / "release" / ("ast_eval.exe" if os.name == "nt" else "ast_eval"),
    ]
    sources = {
        str(path): _sha256_file(path)
        for path in source_paths
        if path.exists() and path.is_file()
    }
    body = {
        "schema": 1,
        "target_score": config.target_score,
        "max_shards": config.max_shards,
        "max_rust_budget": config.max_rust_budget,
        "max_finalists": config.max_finalists,
        "bootstrap": str(config.bootstrap) if config.bootstrap else None,
        "search": search,
        "sources": sources,
    }
    return {**body, "campaign_id": _canonical_digest(body)}


def _verify_campaign_sources(manifest: dict) -> None:
    drift = []
    for raw_path, expected in manifest.get("sources", {}).items():
        path = Path(raw_path)
        actual = _sha256_file(path) if path.exists() and path.is_file() else None
        if actual != expected:
            drift.append(str(path))
    if drift:
        raise RuntimeError(
            "campaign source drift detected: " + ", ".join(sorted(drift))
        )


def _repeated_authority_error(result: dict, threshold: int = 2) -> str | None:
    counts = Counter(
        str(item["error"])
        for item in result.get("openrocket_results", [])
        if item.get("error")
    )
    repeated = [error for error, count in counts.items() if count >= threshold]
    return sorted(repeated)[0] if repeated else None


def _result_progress(result: dict) -> tuple[float | None, dict | None]:
    best = result.get("best")
    official = best.get("official", {}) if best else {}
    score = float(official["score"]) if official.get("is_legal", False) else None
    persisted = result.get("persisted_authority")
    return score, persisted


def _violation_histogram(result: dict) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in result.get("openrocket_results", []):
        for violation in record.get("official", {}).get("violations", []):
            counts[str(violation)] += 1
        if record.get("error"):
            counts["authority_error"] += 1
    return dict(counts.most_common())


def _bootstrap_finished(root: Path) -> bool:
    try:
        status = json.loads((root / "autopilot.json").read_text(encoding="utf-8"))
        return int(status.get("cycles_completed", 0)) >= int(
            status.get("cycles_requested", 1)
        )
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return (root / "result.json").exists()


def _bootstrap_results(root: Path) -> list[Path]:
    direct = root / "result.json"
    results = sorted(root.glob("cycle-*/result.json"))
    if direct.exists():
        results.append(direct)
    return results


def run_campaign(config: CampaignConfig) -> dict:
    """Run an idempotent, authority-certified, self-calibrating long campaign."""
    if config.max_shards < 1:
        raise ValueError("max_shards must be >= 1")
    root = Path(config.search.output_dir)
    manifest = _campaign_manifest(config)
    manifest_path = root / "campaign.json"
    state_path = root / "campaign-state.json"

    with _campaign_lease(root):
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("campaign_id") != manifest["campaign_id"]:
                raise RuntimeError(
                    "campaign configuration/source hashes changed; use a new output directory"
                )
        else:
            _atomic_json(manifest_path, manifest)
            _append_campaign_event(root, "campaign_created", campaign_id=manifest["campaign_id"])

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            state = {
                "schema": 1,
                "campaign_id": manifest["campaign_id"],
                "status": "running",
                "target_score": config.target_score,
                "next_shard": 1,
                "rust_budget": config.search.rust_budget,
                "finalist_budget": config.search.finalist_budget,
                "best_legal_score": None,
                "certified_score": None,
                "history": [],
                "failures": [],
            }

        calibration = config.search.calibration_result
        imported = {item["result"] for item in state["history"]}
        if config.bootstrap is not None:
            bootstrap = Path(config.bootstrap)
            while not _bootstrap_finished(bootstrap):
                _write_health(root, "running", "waiting_for_bootstrap", bootstrap=str(bootstrap))
                time.sleep(15)
            for result_path in _bootstrap_results(bootstrap):
                if str(result_path) in imported:
                    continue
                result = json.loads(result_path.read_text(encoding="utf-8"))
                score, persisted = _result_progress(result)
                state["history"].append({
                    "kind": "bootstrap",
                    "result": str(result_path),
                    "legal_score": score,
                    "violations": _violation_histogram(result),
                })
                if score is not None:
                    state["best_legal_score"] = max(score, state["best_legal_score"] or score)
                if persisted and persisted.get("certified"):
                    certified = persisted.get("official", {}).get("score")
                    if certified is not None:
                        state["certified_score"] = max(
                            float(certified), state["certified_score"] or float(certified)
                        )
                calibration = result_path
                imported.add(str(result_path))
                _append_campaign_event(root, "bootstrap_imported", result=str(result_path), score=score)
            _atomic_json(state_path, state)

        recent_scores = [
            item["legal_score"] for item in state["history"]
            if item.get("legal_score") is not None
        ]
        for shard in range(int(state.get("next_shard", 1)), config.max_shards + 1):
            if (state.get("certified_score") or -1.0e30) >= config.target_score:
                break
            shard_dir = root / "shards" / f"shard-{shard:03d}"
            result_path = shard_dir / "result.json"
            attempt = 0
            while True:
                attempt += 1
                _write_health(
                    root, "running", "campaign_shard", shard=shard, attempt=attempt,
                    rust_budget=state["rust_budget"], finalists=state["finalist_budget"],
                    best_legal_score=state.get("best_legal_score"),
                )
                try:
                    _verify_campaign_sources(manifest)
                    if result_path.exists():
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                    else:
                        result = run_search(SearchConfig(
                            rust_budget=int(state["rust_budget"]),
                            rust_generations=config.search.rust_generations,
                            finalist_budget=int(state["finalist_budget"]),
                            seed=config.search.seed + shard * 104_729 + (attempt - 1) * 1_000_003,
                            output_dir=shard_dir,
                            wind_csv=config.search.wind_csv,
                            resume=True,
                            calibration_result=calibration,
                            seed_parameters=config.search.seed_parameters,
                        ))
                    repeated_error = _repeated_authority_error(result)
                    if repeated_error is not None:
                        raise RuntimeError(
                            "repeated deterministic authority error: " + repeated_error
                        )
                    break
                except Exception as exc:
                    failure = {
                        "shard": shard, "attempt": attempt,
                        "error_type": type(exc).__name__, "error": str(exc),
                    }
                    state["failures"].append(failure)
                    _atomic_json(state_path, state)
                    _append_campaign_event(root, "shard_failed", **failure)
                    fingerprint = (failure["error_type"], failure["error"])
                    repeats = sum(
                        1 for item in state["failures"]
                        if (item.get("error_type"), item.get("error")) == fingerprint
                    )
                    if repeats >= 2:
                        state["status"] = "blocked"
                        state["blocked_reason"] = failure
                        _atomic_json(state_path, state)
                        _atomic_json(root / "campaign-alert.json", {
                            "status": "operator_attention_required",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "reason": "repeated_deterministic_failure",
                            **failure,
                        })
                        _write_health(
                            root, "blocked", "repeated_deterministic_failure", **failure
                        )
                        _append_campaign_event(
                            root, "campaign_blocked", reason="repeated_deterministic_failure",
                            **failure,
                        )
                        return state
                    if attempt >= 3:
                        result = None
                        break
            if result is None:
                state["rust_budget"] = min(
                    config.max_rust_budget, int(state["rust_budget"] * 1.25)
                )
                state["finalist_budget"] = min(
                    config.max_finalists, int(state["finalist_budget"]) + 8
                )
                state["next_shard"] = shard + 1
                _atomic_json(state_path, state)
                continue

            score, persisted = _result_progress(result)
            violations = _violation_histogram(result)
            state["history"].append({
                "kind": "shard", "shard": shard, "result": str(result_path),
                "legal_score": score, "violations": violations,
                "rust_budget": state["rust_budget"],
                "finalist_budget": state["finalist_budget"],
            })
            if score is not None:
                state["best_legal_score"] = max(score, state["best_legal_score"] or score)
                recent_scores.append(score)
            if persisted and persisted.get("certified"):
                certified = persisted.get("official", {}).get("score")
                if certified is not None:
                    state["certified_score"] = max(
                        float(certified), state["certified_score"] or float(certified)
                    )
                    if float(certified) >= config.target_score:
                        _atomic_json(root / "champion.json", {
                            "campaign_id": manifest["campaign_id"],
                            "candidate_id": result["best"].get("candidate_id"),
                            "parameters": result["best"]["parameters"],
                            "persisted_authority": persisted,
                            "source_result": str(result_path),
                        })
            calibration = result_path
            # Stagnation or an authority batch with no legal design buys more
            # diversity and authority coverage; official constraints stay fixed.
            stalled = len(recent_scores) < 2 or (
                len(recent_scores) >= 3
                and max(recent_scores[-3:]) <= max(recent_scores[:-3] or [-1.0e30])
            )
            if score is None or stalled:
                state["rust_budget"] = min(
                    config.max_rust_budget, max(int(state["rust_budget"] * 1.25), int(state["rust_budget"]) + 1)
                )
                state["finalist_budget"] = min(
                    config.max_finalists, int(state["finalist_budget"]) + 8
                )
            state["next_shard"] = shard + 1
            state["status"] = (
                "goal_reached"
                if (state.get("certified_score") or -1.0e30) >= config.target_score
                else "running"
            )
            _atomic_json(state_path, state)
            _append_campaign_event(
                root, "shard_completed", shard=shard, legal_score=score,
                certified_score=state.get("certified_score"), violations=violations,
            )

        if state.get("status") != "goal_reached":
            state["status"] = "budget_exhausted"
        _atomic_json(state_path, state)
        _write_health(
            root, "complete", state["status"],
            best_legal_score=state.get("best_legal_score"),
            certified_score=state.get("certified_score"),
        )
        _append_campaign_event(root, "campaign_finished", status=state["status"])
        return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-budget", type=int, default=5000)
    parser.add_argument("--rust-generations", type=int, default=5)
    parser.add_argument("--finalists", type=int, default=48)
    parser.add_argument("--seed", type=int, default=16000)
    parser.add_argument("--output", type=Path, default=Path("designs/osifog_engine_search"))
    parser.add_argument("--wind-csv", type=Path, default=Path("OSIFOG/OpenWind_File.csv"))
    parser.add_argument(
        "--calibrate-from",
        type=Path,
        help="prior result.json used to calibrate Rust apogee/Mach against OR",
    )
    parser.add_argument(
        "--seed-from",
        type=Path,
        help="result/recovery JSON whose physical parameters seed generation zero",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="closed-loop Rust/OR cycles; each completed OR batch calibrates the next",
    )
    parser.add_argument("--target-score", type=float, default=850_000.0)
    parser.add_argument(
        "--campaign-shards", type=int, default=0,
        help="run the durable long-campaign supervisor for this many shards",
    )
    parser.add_argument(
        "--bootstrap", type=Path,
        help="adopt a completed/in-flight autopilot directory before starting shards",
    )
    parser.add_argument("--max-rust-budget", type=int, default=100_000)
    parser.add_argument("--max-finalists", type=int, default=96)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args(argv)
    search_config = SearchConfig(
        rust_budget=args.rust_budget,
        rust_generations=args.rust_generations,
        finalist_budget=args.finalists,
        seed=args.seed,
        output_dir=args.output,
        wind_csv=args.wind_csv,
        resume=not args.no_resume,
        calibration_result=args.calibrate_from,
        seed_parameters=args.seed_from,
    )
    if args.campaign_shards:
        result = run_campaign(CampaignConfig(
            search=search_config,
            max_shards=args.campaign_shards,
            target_score=args.target_score,
            bootstrap=args.bootstrap,
            max_rust_budget=args.max_rust_budget,
            max_finalists=args.max_finalists,
        ))
    else:
        result = run_autopilot(
            search_config, cycles=args.cycles, target_score=args.target_score,
        )
    best = result.get("best")
    print(json.dumps({
        "best": best and best.get("official"),
        "campaign_status": result.get("status"),
        "certified_score": result.get("certified_score"),
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
