#!/usr/bin/env python3
"""Authority-guided discovery of a legal post-apogee retro attitude basin.

One OpenRocket JVM is used per worker process. The supervisor never evaluates
physics; it owns leases, restarts, stagnation policy, and the transition into
the official score campaign after a persisted two-stage recovery pass.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import random
import socket
import subprocess
import sys
import time

import numpy as np

import osifog_engine_search as search
import osifog_sweep


TERMINAL_WORKER_STATES = {"recovery_pass", "blocked", "budget_exhausted"}
TERMINAL_SUPERVISOR_STATES = {"goal_reached", "blocked", "budget_exhausted"}
FEATURE_KEYS = (
    "nose_mass_kg", "nose_length_m", "nose_ballast_pos_m",
    "s0_main", "s0_retro", "s1_main", "s1_retro",
    "s0_aft_ballast_kg", "s0_aft_ballast_pos_m",
    "s1_aft_ballast_kg", "s1_aft_ballast_pos_m",
    "s0_core_length", "s0_core_radius", "s0_pod_length", "s0_pod_radius",
    "s1_core_length", "s1_core_radius", "s1_pod_length", "s1_pod_radius",
    "s0_pod_axial_offset_m", "s1_pod_axial_offset_m",
    "s0_core_fin_count", "s0_core_fin_root", "s0_core_fin_height",
    "s0_core_fin_sweep", "s0_core_fin_thickness_m",
    "s1_core_fin_count", "s1_core_fin_root", "s1_core_fin_height",
    "s1_core_fin_sweep", "s1_core_fin_thickness_m",
    "s0_grid_fin_count", "s0_grid_fin_root", "s0_grid_fin_height",
    "s0_grid_fin_position_m", "s0_grid_fin_sweep",
    "s1_grid_fin_count", "s1_grid_fin_root", "s1_grid_fin_height",
    "s1_grid_fin_position_m", "s1_grid_fin_sweep",
    "s0_pod_fin_count", "s0_pod_fin_root", "s0_pod_fin_height",
    "s1_pod_fin_count", "s1_pod_fin_root", "s1_pod_fin_height",
    "s0_pod_radial_offset", "s1_pod_radial_offset",
    "s0_pylon_chord_m", "s0_pylon_thickness_m", "s0_pylon_station_count",
    "s1_pylon_chord_m", "s1_pylon_thickness_m", "s1_pylon_station_count",
    "s0_aero_interference_factor", "s1_aero_interference_factor",
    "s1_separation_delay",
    "launch_angle_deg", "launch_azimuth",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _atomic_json(path: Path, payload: dict) -> None:
    search._atomic_json(path, payload)


def _append_event(root: Path, event: str, **details) -> None:
    root.mkdir(parents=True, exist_ok=True)
    record = {"at": _now(), "event": event, **details}
    with (root / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _pid_alive(pid: int) -> bool:
    return search._pid_is_alive(int(pid))


def _terminate_tree(pid: int) -> None:
    if not _pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def _source_manifest() -> dict:
    paths = (
        Path(__file__), Path("osifog_engine_search.py"), Path("osifog_podset.py"),
        Path("osifog_sweep.py"), Path("rocket_ast.py"),
        Path("missions/osifog_l3_precision.json"),
        Path("l2_engine/target/release/ast_eval.exe"),
        Path("lib/OpenRocket-24.12.jar"),
    )
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths if path.exists()
    }


def _manifest_digest(manifest: dict) -> str:
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _verify_sources(expected: dict) -> None:
    actual = _source_manifest()
    if actual != expected:
        changed = sorted(set(actual) | set(expected))
        changed = [key for key in changed if actual.get(key) != expected.get(key)]
        raise RuntimeError("source drift: " + ", ".join(changed))


@contextmanager
def _exclusive_lease(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{socket.gethostname()}:{os.getpid()}:{time.time_ns()}"
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "token": token, "at": _now()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            break
        except FileExistsError:
            incumbent = _read_json(path)
            if _pid_alive(int(incumbent.get("pid", -1))):
                raise RuntimeError(f"live lease owner {incumbent.get('pid')}: {path}")
            os.replace(path, path.with_name(path.name + f".stale-{time.time_ns()}"))
    try:
        yield
    finally:
        current = _read_json(path)
        if current.get("token") == token:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


@dataclass(frozen=True)
class WorkerConfig:
    root: Path
    worker_id: int
    seed: int
    generations: int = 12
    proposals: int = 128
    authority_batch: int = 24
    elite_count: int = 10
    powered_exploration_per_generation: int = 2
    seed_results: tuple[str, ...] = (
        "designs/osifog_recovery_gate_v9/result.json",
        "designs/osifog_recovery_gate_v10/checkpoint.json",
    )


def _feature_vector(parameters: dict, rust_result=None) -> list[float]:
    values = [float(parameters.get(key, 0.0) or 0.0) for key in FEATURE_KEYS]
    values.extend([
        float(getattr(rust_result, "rust_apogee_m", 0.0) or 0.0),
        float(getattr(rust_result, "rust_mach", 0.0) or 0.0),
        float(getattr(rust_result, "rust_min_static_margin", 0.0) or 0.0),
        float(getattr(rust_result, "rust_total_prop_mass_kg", 0.0) or 0.0),
    ])
    return values


def _ridge_predictions(history: list[dict], proposals: list[dict]) -> list[float] | None:
    training = [item for item in history if item.get("features")]
    if len(training) < 24:
        return None
    x = np.asarray([item["features"] for item in training], dtype=float)
    y = np.asarray([item["attitude_score"] for item in training], dtype=float)
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1.0e-9] = 1.0
    z = (x - mean) / scale
    design = np.column_stack((np.ones(len(z)), z, z * z))
    ridge = np.eye(design.shape[1]) * 1.0e-3
    ridge[0, 0] = 0.0
    try:
        weights = np.linalg.solve(design.T @ design + ridge, design.T @ y)
    except np.linalg.LinAlgError:
        return None
    q = np.asarray([item["features"] for item in proposals], dtype=float)
    qz = (q - mean) / scale
    qdesign = np.column_stack((np.ones(len(qz)), qz, qz * qz))
    return [float(value) for value in qdesign @ weights]


def _load_seed_parameters(config: WorkerConfig, wind_levels: list) -> list[dict]:
    result = []
    seen = set()
    for raw_path in config.seed_results:
        payload = _read_json(Path(raw_path))
        records = list(payload.get("openrocket_results", []))
        if isinstance(payload.get("parameters"), dict):
            records.append({"parameters": payload["parameters"]})
        for record in records:
            parameters = record.get("parameters")
            if not isinstance(parameters, dict):
                continue
            restored = dict(parameters, wind_levels=wind_levels)
            try:
                search._repair_podset_derived_geometry(restored)
                if search._podset_geometry_violations(restored):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            identity = search._candidate_id(restored)
            if identity not in seen:
                seen.add(identity)
                result.append(restored)
    return result


def _descent_branch_summary(metrics: dict, parameters: dict) -> dict:
    """Score every independently landing branch, never just branch zero."""
    by_stage = {}
    all_opportunities = []
    for diagnostic in metrics.get("descent_alignment_diagnostics", []):
        branch = int(diagnostic.get("branch", -1))
        if branch < 0:
            continue
        stage_key = search._stage_key_for_branch(metrics, branch)
        trace = diagnostic.get("alignment_trace", [])
        final_time = float(trace[-1]["time_s"]) if trace else 0.0
        first_time = float(trace[0]["time_s"]) if trace else final_time
        duration = max(1.0e-9, final_time - first_time)
        near = [item for item in trace if float(item["time_s"]) >= final_time - 15.0]
        early = [
            item for item in trace
            if (float(item["time_s"]) - first_time) / duration <= 0.40
        ]
        late = [
            item for item in trace
            if (float(item["time_s"]) - first_time) / duration >= 0.50
        ]
        tail_samples = [item for item in trace if float(item["alignment_q"]) >= 0.5]
        acquisition_fraction = (
            (float(tail_samples[0]["time_s"]) - first_time) / duration
            if tail_samples else math.inf
        )
        late_tail_fraction = (
            sum(float(item["alignment_q"]) >= 0.5 for item in late) / len(late)
            if late else 0.0
        )
        early_broadside_fraction = (
            sum(abs(float(item["alignment_q"])) < 0.5 for item in early) / len(early)
            if early else 0.0
        )
        terminal_pool = trace[max(0, int(len(trace) * 0.9)):] if trace else []
        terminal_speed = (
            sum(float(item["speed_ms"]) for item in terminal_pool) / len(terminal_pool)
            if terminal_pool else math.inf
        )
        peak_speed = max((float(item["speed_ms"]) for item in trace), default=math.inf)
        passive_transition = bool(
            acquisition_fraction <= 0.65 and late_tail_fraction >= 0.50
        )
        opportunities = [
            search._landing_opportunity(metrics, parameters, branch, delay)
            for delay in search._delay_candidates(metrics, parameters, branch)
        ]
        usable = [item for item in opportunities if item.get("usable")]
        windows = diagnostic.get("tail_first_windows", [])
        by_stage[stage_key] = {
            "branch": branch,
            "best_alignment_q": float(diagnostic.get("best_alignment_q", -1.0)),
            "near_impact_alignment_q": max(
                (float(item["alignment_q"]) for item in near), default=-1.0
            ),
            "max_tail_window_s": max(
                (float(item.get("duration_s", 0.0)) for item in windows), default=0.0
            ),
            "usable_opportunities": len(usable),
            "opportunities": opportunities,
            "tail_acquisition_fraction": acquisition_fraction,
            "late_tail_fraction": late_tail_fraction,
            "early_broadside_fraction": early_broadside_fraction,
            "terminal_speed_ms": terminal_speed,
            "peak_to_terminal_reduction_ms": max(0.0, peak_speed - terminal_speed),
            "passive_transition": passive_transition,
        }
        all_opportunities.extend(opportunities)
    # Missing branch evidence is a hard failure, not an optimistic average.
    for stage_key in ("s0", "s1"):
        by_stage.setdefault(stage_key, {
            "branch": None, "best_alignment_q": -1.0,
            "near_impact_alignment_q": -1.0, "max_tail_window_s": 0.0,
            "usable_opportunities": 0, "opportunities": [],
            "tail_acquisition_fraction": math.inf, "late_tail_fraction": 0.0,
            "early_broadside_fraction": 0.0, "terminal_speed_ms": math.inf,
            "peak_to_terminal_reduction_ms": 0.0, "passive_transition": False,
        })
    stages = [by_stage["s0"], by_stage["s1"]]
    return {
        "stage_attitude": by_stage,
        "best_alignment_q": min(item["best_alignment_q"] for item in stages),
        "near_impact_alignment_q": min(
            item["near_impact_alignment_q"] for item in stages
        ),
        "max_tail_window_s": min(item["max_tail_window_s"] for item in stages),
        "usable_stages": sum(bool(item["usable_opportunities"]) for item in stages),
        "passive_transition_stages": sum(item["passive_transition"] for item in stages),
        "worst_late_tail_fraction": min(item["late_tail_fraction"] for item in stages),
        "worst_terminal_speed_ms": max(item["terminal_speed_ms"] for item in stages),
        "usable_opportunities": sum(item["usable_opportunities"] for item in stages),
        "opportunities": all_opportunities,
    }


def _authority_summary(parameters: dict) -> tuple[dict, dict]:
    free = dict(
        parameters,
        s0_retro_delay=0.0, s1_retro_delay=0.0,
        s0_retro_ignition_event="never", s1_retro_ignition_event="never",
    )
    metrics = search._run_authority(
        free, "STAGE_FREE_DESCENT_DIAGNOSTIC", search._candidate_id(parameters)
    )
    admissible, violations = search._ascent_admissible(metrics, free)
    descent = _descent_branch_summary(metrics, free)
    margin = float(metrics.get("min_static_margin", -99.0))
    mach = float(metrics.get("mach", 99.0))
    apogee = float(metrics.get("apogee_m", 0.0))
    stability_progress = max(0.0, min(1.0, margin / osifog_sweep.MIN_STATIC_MARGIN))
    mach_progress = max(0.0, min(1.0, (1.10 - mach) / 0.15))
    apogee_error = abs(apogee - 3000.0)
    # This scalar trains the surrogate. Parent survival is constraint-ranked
    # separately below. The quadratic term mirrors the official precision
    # pressure and prevents attitude from buying away kilometres of error.
    score = (
        2_000_000.0 * float(admissible)
        - 150.0 * apogee_error * apogee_error
        + 100_000.0 * stability_progress + 50_000.0 * mach_progress
        + 180_000.0 * ((descent["best_alignment_q"] + 1.0) / 2.0)
        + 220_000.0 * ((descent["near_impact_alignment_q"] + 1.0) / 2.0)
        + 150_000.0 * min(1.0, descent["max_tail_window_s"] / 2.0)
        + 150_000.0 * descent["passive_transition_stages"]
        + 100_000.0 * descent["worst_late_tail_fraction"]
        - 1_000.0 * min(200.0, descent["worst_terminal_speed_ms"])
        + 250_000.0 * descent["usable_stages"]
    )
    summary = {
        "candidate_id": search._candidate_id(parameters),
        "parameters": parameters,
        "attitude_score": score,
        "ascent_admissible": admissible,
        "violations": violations,
        "apogee_m": apogee,
        "mach": mach,
        "min_static_margin": margin,
        "apogee_error_m": apogee_error,
        **descent,
    }
    return summary, metrics


def _campaign_phase(records: list[dict]) -> str:
    legal = [item for item in records if item.get("ascent_admissible")]
    if not legal or min(item.get("apogee_error_m", math.inf) for item in legal) > 500.0:
        return "ascent_corridor"
    if not any(
        item.get("usable_stages", 0) == 2
        and item.get("passive_transition_stages", 0) == 2
        for item in legal
    ):
        return "dual_attitude"
    return "powered_recovery"


def _selection_key(record: dict, phase: str) -> tuple:
    admissible = int(bool(record.get("ascent_admissible")))
    error = min(1.0e9, float(record.get("apogee_error_m", math.inf)))
    terminal_speed = min(
        1.0e9, float(record.get("worst_terminal_speed_ms", math.inf))
    )
    if phase == "ascent_corridor":
        return (admissible, -error, record.get("min_static_margin", -99.0))
    in_corridor = int(error <= 500.0)
    if phase == "dual_attitude":
        return (
            admissible, in_corridor,
            int(record.get("passive_transition_stages", 0)),
            int(record.get("usable_stages", 0)),
            -terminal_speed,
            record.get("near_impact_alignment_q", -1.0),
            record.get("max_tail_window_s", 0.0), -error,
        )
    powered = int(bool(record.get("powered_validation", {}).get("passed")))
    return (
        powered, admissible, in_corridor, int(record.get("usable_stages", 0)),
        record.get("near_impact_alignment_q", -1.0), -error,
    )


def _recovery_pass(metrics: dict) -> tuple[bool, dict]:
    landings = {
        item.get("stage_key"): item
        for item in metrics.get("stage_landings", [])
        if item.get("stage_key") in {"s0", "s1"}
    }
    diagnostics = {
        item.get("stage_key"): item
        for item in metrics.get("retro_burn_diagnostics", [])
        if item.get("stage_key") in {"s0", "s1"}
    }
    speeds = {
        stage: float(landings.get(stage, {}).get("total_speed", math.inf))
        for stage in ("s0", "s1")
    }
    braking = {
        stage: bool(diagnostics.get(stage, {}).get("retro_braking_verified", False))
        for stage in ("s0", "s1")
    }
    passed = all(speeds[stage] < 5.0 and braking[stage] for stage in ("s0", "s1"))
    return passed, {"landing_speeds_ms": speeds, "braking_verified": braking}


def _select_authority_batch(
    proposals: list[tuple[dict, object]], history: list[dict], count: int, rng: random.Random
) -> list[tuple[dict, object]]:
    rows = [
        {"parameters": p, "rust": result, "features": _feature_vector(p, result)}
        for p, result in proposals
    ]
    predictions = _ridge_predictions(history, rows)
    for index, row in enumerate(rows):
        row["prediction"] = (
            predictions[index]
            if predictions is not None
            else float(getattr(row["rust"], "score", -1.0e30))
        )
    selected = []
    selected_ids = set()

    def take(sequence, limit):
        for row in sequence:
            identity = search._candidate_id(row["parameters"])
            if identity in selected_ids:
                continue
            selected.append((row["parameters"], row["rust"]))
            selected_ids.add(identity)
            if len(selected) >= limit:
                break

    leader_limit = max(1, count // 3)
    take(sorted(rows, key=lambda row: row["prediction"], reverse=True), leader_limit)
    target = sorted(
        rows,
        key=lambda row: (
            abs(float(getattr(row["rust"], "rust_apogee_m", 0.0)) - 3000.0),
            max(0.0, float(getattr(row["rust"], "rust_mach", 99.0)) - 0.95),
            max(0.0, 1.5 - float(getattr(row["rust"], "rust_min_static_margin", -99.0))),
        ),
    )
    take(target, max(leader_limit + 1, count * 2 // 3))
    recovery = sorted(
        rows,
        key=lambda row: (
            float(row["parameters"].get("nose_mass_kg", 99.0)),
            abs(float(getattr(row["rust"], "rust_min_static_margin", 0.0)) - 1.7),
            int(row["parameters"].get("s0_grid_fin_count", 0)),
            -float(row["parameters"].get("s0_core_fin_height", 0.0))
            * float(row["parameters"].get("s0_core_fin_root", 0.0)),
        ),
    )
    take(recovery, max(count * 5 // 6, leader_limit + 1))
    shuffled = list(rows)
    rng.shuffle(shuffled)
    take(shuffled, count)
    return selected[:count]


def _worker_state(config: WorkerConfig, manifest: dict) -> dict:
    return {
        "schema": 1,
        "status": "running",
        "worker_id": config.worker_id,
        "config": {**asdict(config), "root": str(config.root)},
        "sources": manifest,
        "source_digest": _manifest_digest(manifest),
        "generation": 0,
        "records": [],
        "parents": [],
        "best_score": None,
        "best_alignment_q": -1.0,
        "no_improve_generations": 0,
        "diversity_injections": 0,
        "error_counts": {},
    }


def _write_worker_health(worker_root: Path, status: str, phase: str, **details) -> None:
    _atomic_json(worker_root / "health.json", {
        "status": status, "phase": phase, "pid": os.getpid(),
        "updated_at": _now(), **details,
    })


def _check_worker_sources(state: dict, state_path: Path, worker_root: Path) -> bool:
    try:
        _verify_sources(state["sources"])
        return True
    except RuntimeError as exc:
        reason = str(exc)
        state.update(status="blocked", blocked_reason=reason, finished_at=_now())
        _atomic_json(state_path, state)
        _write_worker_health(worker_root, "blocked", "source_drift", reason=reason)
        return False


def run_worker(config: WorkerConfig) -> int:
    worker_root = config.root / "workers" / f"worker-{config.worker_id:02d}"
    worker_root.mkdir(parents=True, exist_ok=True)
    state_path = worker_root / "state.json"
    manifest = _source_manifest()
    with _exclusive_lease(worker_root / "worker.lease.json"):
        state = _read_json(state_path)
        if not state:
            state = _worker_state(config, manifest)
            rng = random.Random(config.seed)
        else:
            if not _check_worker_sources(state, state_path, worker_root):
                return 2
            rng = random.Random()
            rng.setstate(pickle.loads(base64.b64decode(state["rng_state"])))
            if state.get("status") in TERMINAL_WORKER_STATES:
                return 0
        wind_levels = osifog_sweep.parse_wind_csv(search.SearchConfig().wind_csv)
        seed_parameters = _load_seed_parameters(config, wind_levels)
        completed_ids = {item["candidate_id"] for item in state["records"]}
        start_generation = int(state.get("generation", 0))
        for generation in range(start_generation, config.generations):
            if not _check_worker_sources(state, state_path, worker_root):
                return 2
            parent_parameters = [item["parameters"] for item in state.get("parents", [])]
            if not parent_parameters:
                parent_parameters = seed_parameters
            inject = int(state.get("no_improve_generations", 0)) >= 3
            proposals = []
            proposal_ids = set()
            attempts = 0
            while len(proposals) < config.proposals and attempts < config.proposals * 20:
                attempts += 1
                if parent_parameters and rng.random() < (0.35 if inject else 0.75):
                    parameters = search._breed_valid_parameters(rng, parent_parameters, wind_levels)
                else:
                    parameters = search._sample_valid_parameters(rng, wind_levels)
                identity = search._candidate_id(parameters)
                if identity in proposal_ids or identity in completed_ids:
                    continue
                proposal_ids.add(identity)
                proposals.append(parameters)
            if len(proposals) < config.authority_batch:
                raise RuntimeError("proposal generator exhausted unique physical candidates")
            rust_results = search._default_rust_evaluator(
                [search.parameters_to_ast(item) for item in proposals],
                proposals,
                None,
                execution_profile="super-speed",
                simulation_phase="full",
            )
            rust_pairs = [
                (parameters, result)
                for parameters, result in zip(proposals, rust_results)
                if not str(result.reason).startswith(("parse", "motor_oversized"))
            ]
            batch = _select_authority_batch(
                rust_pairs, state["records"], config.authority_batch, rng
            )
            generation_records = []
            powered_used = 0
            for index, (parameters, rust_result) in enumerate(batch):
                if not _check_worker_sources(state, state_path, worker_root):
                    return 2
                identity = search._candidate_id(parameters)
                _atomic_json(worker_root / "health.json", {
                    "status": "running", "phase": "openrocket_free_descent",
                    "pid": os.getpid(), "updated_at": _now(),
                    "generation": generation + 1, "generations": config.generations,
                    "candidate": index + 1, "batch": len(batch),
                    "evaluated": len(state["records"]),
                    "best_score": state.get("best_score"),
                    "best_alignment_q": state.get("best_alignment_q", -1.0),
                })
                try:
                    record, _free_metrics = _authority_summary(parameters)
                    record["features"] = _feature_vector(parameters, rust_result)
                    record["rust"] = {
                        "status": rust_result.status,
                        "reason": rust_result.reason,
                        "score": rust_result.score,
                        "apogee_m": rust_result.rust_apogee_m,
                        "mach": rust_result.rust_mach,
                        "min_static_margin": rust_result.rust_min_static_margin,
                    }
                    exploratory_powered = bool(
                        record["ascent_admissible"]
                        and record["apogee_error_m"] <= 750.0
                        and record.get("passive_transition_stages", 0) >= 1
                        and powered_used < config.powered_exploration_per_generation
                    )
                    if record["ascent_admissible"] and (
                        record.get("usable_stages", 0) == 2 or exploratory_powered
                    ):
                        powered_used += 1
                        powered_metrics, official, tuned = search._default_openrocket_evaluator(parameters)
                        passed, recovery = _recovery_pass(powered_metrics)
                        record["powered_validation"] = {
                            "passed": passed, "recovery": recovery,
                            "official": official, "parameters": tuned,
                            "powered_stage_trials": powered_metrics.get("powered_stage_trials", []),
                        }
                        if passed:
                            payload = {
                                "schema": 1, "status": "recovery_pass",
                                "worker_id": config.worker_id,
                                "candidate_id": identity, "parameters": tuned,
                                "metrics": powered_metrics, "official": official,
                                "openrocket_results": [{
                                    "index": 0, "candidate_id": identity,
                                    "parameters": tuned, "metrics": powered_metrics,
                                    "official": official,
                                }],
                            }
                            _atomic_json(config.root / "recovery-pass.json", payload)
                            state["status"] = "recovery_pass"
                            state["recovery_pass"] = recovery
                            state["records"].append(record)
                            _atomic_json(state_path, state)
                            _append_event(worker_root, "recovery_pass", **recovery)
                            _write_worker_health(
                                worker_root, "complete", "recovery_pass", **recovery
                            )
                            return 0
                except Exception as exc:
                    fingerprint = f"{type(exc).__name__}:{exc}"
                    counts = Counter(state.get("error_counts", {}))
                    counts[fingerprint] += 1
                    state["error_counts"] = dict(counts)
                    record = {
                        "candidate_id": identity, "parameters": parameters,
                        "error_type": type(exc).__name__, "error": str(exc),
                        "attitude_score": -1.0e30,
                    }
                    if counts[fingerprint] >= 2:
                        state["records"].append(record)
                        state["status"] = "blocked"
                        state["blocked_reason"] = "repeated_deterministic_error"
                        _atomic_json(state_path, state)
                        _atomic_json(worker_root / "alert.json", {
                            "status": "operator_attention_required", "at": _now(),
                            "reason": state["blocked_reason"], "fingerprint": fingerprint,
                        })
                        _write_worker_health(
                            worker_root, "blocked", state["blocked_reason"],
                            fingerprint=fingerprint,
                        )
                        return 2
                state["records"].append(record)
                generation_records.append(record)
                completed_ids.add(identity)
                state["rng_state"] = base64.b64encode(
                    pickle.dumps(rng.getstate())
                ).decode("ascii")
                _atomic_json(state_path, state)

            valid = [item for item in state["records"] if not item.get("error")]
            phase = _campaign_phase(valid)
            valid.sort(key=lambda item: _selection_key(item, phase), reverse=True)
            q_ranked = sorted(
                valid, key=lambda item: item.get("near_impact_alignment_q", -1.0),
                reverse=True,
            )
            parents = []
            parent_ids = set()
            for item in valid[:config.elite_count] + q_ranked[:4]:
                if item["candidate_id"] not in parent_ids:
                    parents.append(item)
                    parent_ids.add(item["candidate_id"])
            best = valid[0] if valid else None
            previous_score = state.get("best_score")
            previous_q = float(state.get("best_alignment_q", -1.0))
            best_key = _selection_key(best, phase) if best else ()
            previous_phase = state.get("campaign_phase")
            previous_key = tuple(state.get("best_phase_key", ()))
            improved = bool(best) and (
                phase != previous_phase or not previous_key or best_key > previous_key
            )
            if improved:
                state["best_score"] = float(best["attitude_score"])
                state["best_phase_key"] = list(best_key)
                state["campaign_phase"] = phase
                state["best_alignment_q"] = max(
                    previous_q, float(best.get("near_impact_alignment_q", -1.0))
                )
                state["no_improve_generations"] = 0
            else:
                state["no_improve_generations"] = int(
                    state.get("no_improve_generations", 0)
                ) + 1
            if state["no_improve_generations"] == 3:
                state["diversity_injections"] = int(
                    state.get("diversity_injections", 0)
                ) + 1
                _append_event(worker_root, "stagnation_diversity_injection",
                              generation=generation + 1)
            if (
                state["no_improve_generations"] >= 6
                and int(state.get("diversity_injections", 0)) >= 1
            ):
                state["status"] = "blocked"
                state["blocked_reason"] = "authority_evolution_stagnated"
                _atomic_json(state_path, state)
                _atomic_json(worker_root / "alert.json", {
                    "status": "operator_attention_required", "at": _now(),
                    "reason": state["blocked_reason"],
                    "best_score": state.get("best_score"),
                    "best_alignment_q": state.get("best_alignment_q"),
                })
                _write_worker_health(
                    worker_root, "blocked", state["blocked_reason"],
                    best_score=state.get("best_score"),
                    best_alignment_q=state.get("best_alignment_q"),
                )
                return 2
            state["parents"] = parents
            state["generation"] = generation + 1
            state["rng_state"] = base64.b64encode(
                pickle.dumps(rng.getstate())
            ).decode("ascii")
            _atomic_json(state_path, state)
            _append_event(
                worker_root, "generation_completed", generation=generation + 1,
                campaign_phase=phase,
                best_score=state.get("best_score"),
                best_alignment_q=state.get("best_alignment_q"),
                no_improve_generations=state["no_improve_generations"],
            )
        state["status"] = "budget_exhausted"
        _atomic_json(state_path, state)
        _write_worker_health(
            worker_root, "complete", "budget_exhausted",
            evaluated=len(state["records"]),
            best_score=state.get("best_score"),
            best_alignment_q=state.get("best_alignment_q"),
        )
        return 0


def _spawn(command: list[str], root: Path, label: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    stdout = (root / f"{label}.stdout.log").open("a", encoding="utf-8")
    stderr = (root / f"{label}.stderr.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, *command],
        cwd=Path(__file__).resolve().parent,
        stdout=stdout, stderr=stderr,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout.close()
    stderr.close()
    return process.pid


def _worker_command(root: Path, worker_id: int, args) -> list[str]:
    return [
        str(Path(__file__).name), "--mode", "worker", "--root", str(root),
        "--worker-id", str(worker_id), "--workers", str(args.workers),
        "--generations", str(args.generations), "--proposals", str(args.proposals),
        "--authority-batch", str(args.authority_batch), "--seed", str(args.seed),
    ]


def _official_command(root: Path) -> list[str]:
    recovery = root / "recovery-pass.json"
    return [
        "osifog_engine_search.py", "--rust-budget", "20000",
        "--rust-generations", "5", "--finalists", "48",
        "--campaign-shards", "24", "--target-score", "800001",
        "--max-rust-budget", "200000", "--max-finalists", "128",
        "--calibrate-from", str(recovery), "--seed-from", str(recovery),
        "--output", str(root / "official-800k"),
    ]


def _write_supervisor_health(root: Path, state: dict, **details) -> None:
    _atomic_json(root / "health.json", {
        "status": state["status"], "phase": state.get("phase"),
        "pid": os.getpid(), "updated_at": _now(),
        "reason": state.get("reason"), **details,
    })


def _terminal_worker_outcome(worker_states: list[dict]) -> tuple[str, str]:
    statuses = [item.get("status") for item in worker_states]
    if statuses and all(status == "budget_exhausted" for status in statuses):
        return "budget_exhausted", "attitude_search_budget_exhausted"
    return "blocked", "attitude_workers_blocked"


def run_supervisor(args) -> int:
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "supervisor-state.json"
    with _exclusive_lease(root / "supervisor.lease.json"):
        state = _read_json(state_path) or {
            "schema": 1, "status": "running", "phase": "attitude_discovery",
            "created_at": _now(), "worker_restarts": {}, "campaign_restarts": 0,
            "sources": _source_manifest(),
        }
        if state.get("status") in TERMINAL_SUPERVISOR_STATES:
            return 0
        while True:
            try:
                _verify_sources(state["sources"])
            except RuntimeError as exc:
                state.update(status="blocked", reason=str(exc), finished_at=_now())
                _atomic_json(state_path, state)
                _write_supervisor_health(root, state)
                _atomic_json(root / "alert.json", {
                    "status": "operator_attention_required", "at": _now(),
                    "reason": state["reason"],
                })
                return 2
            recovery_path = root / "recovery-pass.json"
            if recovery_path.exists() and state.get("phase") != "official_campaign":
                for worker_id in range(args.workers):
                    health = _read_json(
                        root / "workers" / f"worker-{worker_id:02d}" / "health.json"
                    )
                    _terminate_tree(int(health.get("pid", -1)))
                pid = _spawn(_official_command(root), root, "official-campaign")
                state.update(phase="official_campaign", campaign_pid=pid)
                _append_event(root, "official_campaign_started", pid=pid)

            if state.get("phase") == "official_campaign":
                campaign_root = root / "official-800k"
                campaign_state = _read_json(campaign_root / "campaign-state.json")
                status = campaign_state.get("status")
                pid = int(state.get("campaign_pid", -1))
                if status == "goal_reached":
                    state.update(status="goal_reached", finished_at=_now())
                    _atomic_json(state_path, state)
                    _write_supervisor_health(root, state, campaign=campaign_state)
                    return 0
                if status in {"blocked", "budget_exhausted"}:
                    state.update(status=status, finished_at=_now(), campaign=campaign_state)
                    _atomic_json(state_path, state)
                    _write_supervisor_health(root, state, campaign=campaign_state)
                    return 2 if status == "blocked" else 0
                if not _pid_alive(pid):
                    state["campaign_restarts"] = int(state.get("campaign_restarts", 0)) + 1
                    if state["campaign_restarts"] > 2:
                        state.update(status="blocked", reason="campaign_restart_budget_exhausted")
                        _atomic_json(state_path, state)
                        _write_supervisor_health(root, state)
                        return 2
                    state["campaign_pid"] = _spawn(
                        _official_command(root), root, "official-campaign"
                    )
                _atomic_json(root / "health.json", {
                    "status": "running", "phase": "official_campaign",
                    "pid": os.getpid(), "updated_at": _now(),
                    "campaign_pid": state.get("campaign_pid"),
                    "campaign_status": status,
                    "best_legal_score": campaign_state.get("best_legal_score"),
                    "certified_score": campaign_state.get("certified_score"),
                })
            else:
                terminal = []
                snapshots = []
                worker_states = []
                for worker_id in range(args.workers):
                    worker_root = root / "workers" / f"worker-{worker_id:02d}"
                    worker_state = _read_json(worker_root / "state.json")
                    health = _read_json(worker_root / "health.json")
                    status = worker_state.get("status", "starting")
                    worker_states.append(worker_state)
                    terminal.append(status in TERMINAL_WORKER_STATES)
                    pid = int(health.get("pid", -1))
                    stale = False
                    if health.get("updated_at"):
                        try:
                            updated = datetime.fromisoformat(health["updated_at"])
                            stale = (datetime.now(timezone.utc) - updated).total_seconds() > args.stale_seconds
                        except ValueError:
                            stale = True
                    if stale and _pid_alive(pid):
                        _terminate_tree(pid)
                    if status not in TERMINAL_WORKER_STATES and not _pid_alive(pid):
                        key = str(worker_id)
                        previously_started = bool(worker_state or health)
                        restarts = int(state["worker_restarts"].get(key, 0))
                        if previously_started:
                            restarts += 1
                            state["worker_restarts"][key] = restarts
                        if restarts > 2:
                            worker_state.update(
                                status="blocked", blocked_reason="restart_budget_exhausted"
                            )
                            _atomic_json(worker_root / "state.json", worker_state)
                            terminal[-1] = True
                        else:
                            pid = _spawn(
                                _worker_command(root, worker_id, args),
                                worker_root, "worker",
                            )
                    snapshots.append({
                        "worker_id": worker_id, "pid": pid, "status": status,
                        "generation": worker_state.get("generation", 0),
                        "evaluated": len(worker_state.get("records", [])),
                        "best_alignment_q": worker_state.get("best_alignment_q", -1.0),
                        "best_score": worker_state.get("best_score"),
                    })
                if terminal and all(terminal):
                    status, reason = _terminal_worker_outcome(worker_states)
                    state.update(status=status, reason=reason, finished_at=_now())
                    _atomic_json(state_path, state)
                    _write_supervisor_health(root, state, workers=snapshots)
                    if status == "blocked":
                        _atomic_json(root / "alert.json", {
                            "status": "operator_attention_required", "at": _now(),
                            "reason": state["reason"], "workers": snapshots,
                        })
                    return 2 if status == "blocked" else 0
                _atomic_json(root / "health.json", {
                    "status": "running", "phase": "attitude_discovery",
                    "pid": os.getpid(), "updated_at": _now(), "workers": snapshots,
                })
            _atomic_json(state_path, state)
            time.sleep(args.interval)


def ensure_supervisor(args) -> int:
    root = Path(args.root)
    state = _read_json(root / "supervisor-state.json")
    if state.get("status") in TERMINAL_SUPERVISOR_STATES:
        return 0
    lease = _read_json(root / "supervisor.lease.json")
    if _pid_alive(int(lease.get("pid", -1))):
        return 0
    command = [
        str(Path(__file__).name), "--mode", "supervisor", "--root", str(root),
        "--workers", str(args.workers), "--generations", str(args.generations),
        "--proposals", str(args.proposals), "--authority-batch", str(args.authority_batch),
        "--seed", str(args.seed), "--interval", str(args.interval),
        "--stale-seconds", str(args.stale_seconds),
    ]
    _spawn(command, root, "supervisor")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("worker", "supervisor", "ensure"), default="supervisor")
    parser.add_argument("--root", type=Path, default=Path("designs/osifog_attitude_campaign_v1"))
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--generations", type=int, default=12)
    parser.add_argument("--proposals", type=int, default=128)
    parser.add_argument("--authority-batch", type=int, default=24)
    parser.add_argument("--seed", type=int, default=260722)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--stale-seconds", type=float, default=900.0)
    args = parser.parse_args(argv)
    if args.mode == "worker":
        return run_worker(WorkerConfig(
            root=args.root, worker_id=args.worker_id,
            seed=args.seed + args.worker_id * 104729,
            generations=args.generations, proposals=args.proposals,
            authority_batch=args.authority_batch,
        ))
    if args.mode == "ensure":
        return ensure_supervisor(args)
    return run_supervisor(args)


if __name__ == "__main__":
    raise SystemExit(main())
