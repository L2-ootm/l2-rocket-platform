#!/usr/bin/env python3
"""Algorithm-owned Candidate J search around the immutable Candidate I family.

The campaign keeps the legal internal octaweb topology fixed:

* Sustainer: one central retro motor and no ascent motor.
* Booster: three K700W ascent motors around one central retro motor.
* Two structural octaweb rings per stage and one interstage coupler.
* No pods and no passive recovery devices.

Rust screens the generated AST population. OpenRocket then performs sequential
authority free-descent and powered timing searches. Candidate I is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from motor_data import load_motor_by_index
from organic_loop import run_rust_evaluator
from osifog_sweep import (
    MOTOR_DATABASE,
    generate_ork,
    init_or,
    parse_wind_csv,
    run_sim,
    score_official,
    validate_compiled_centering_rings,
    validate_compiled_interstage_coupler,
    validate_compiled_nose_ballast_attachment,
    validate_upper_stage_ignition_after_separation,
)
from rocket_ast import ASTNode


CANDIDATE_I = REPO / "designs/osifog_submission/candidate_I.json"
CANDIDATE_I_ORK = REPO / "designs/osifog_submission/candidate_I.ork"
EXPECTED_I_HASHES = {
    CANDIDATE_I.name: "44441616D5774A4630918374FBEEB61EDBD0415CB7803F1125323C5F583F906E",
    CANDIDATE_I_ORK.name: "74B54EE7AF06E81AFFAE722625398C89CE3F226D1913BB7F4F1C4CBBF7B57172",
}
DEFAULT_OUT = (
    REPO
    / "OSIFOG/experiments-2026-07-25/candidate_j_organic_campaign"
)
WIND = parse_wind_csv("OSIFOG/OpenWind_File.csv")
OFFICIAL_SEED = 16000
CAMPAIGN_SCHEMA = 4

# Motor indices are stable repository data, not guessed OpenRocket labels.
S0_FAMILIES = {
    20: [6.0, 6.8, 7.4, 7.8, 8.4],  # K700W control family
    22: [5.0, 6.0, 7.0, 8.0],       # K510 long burn
    19: [3.5, 4.5, 5.5, 6.5],       # K550W
    37: [0.8, 1.5, 2.2, 3.0, 4.0],  # 949J150-P long burn
    36: [0.0, 0.6, 1.2, 2.0],       # 644J94-P long burn
    16: [0.5, 1.2, 2.0, 3.0],       # J510W
}
S1_RETROS = [5, 6, 7, 8, 9, 10, 11, 12, 13]
AST_PROXY_RADIUS_M = 0.085


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().upper()


def verify_candidate_i() -> dict:
    actual = {
        CANDIDATE_I.name: sha256(CANDIDATE_I),
        CANDIDATE_I_ORK.name: sha256(CANDIDATE_I_ORK),
    }
    if actual != EXPECTED_I_HASHES:
        raise RuntimeError(f"Candidate I hash drift: {actual}")
    return actual


def atomic_json(path: Path | str, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temp, path)


def motor_name(index: int) -> str:
    return str(MOTOR_DATABASE[index][1])


def motor_impulse(index: int) -> float:
    motor = load_motor_by_index(index)
    points = list(zip(motor.time_points_s, motor.thrust_points_n))
    return sum(
        (f0 + f1) * 0.5 * (t1 - t0)
        for (t0, f0), (t1, f1) in zip(points, points[1:])
    )


def slim_metrics(metrics: dict) -> dict:
    omit = {"descent_alignment_diagnostics", "retro_burn_diagnostics"}
    value = {key: item for key, item in metrics.items() if key not in omit}
    value["alignment_summary"] = [
        {
            "stage_key": item.get("stage_key"),
            "best_alignment_q": item.get("best_alignment_q"),
            "best_sample": item.get("best_sample"),
            "tail_first_windows": item.get("tail_first_windows", []),
        }
        for item in metrics.get("descent_alignment_diagnostics", [])
    ]
    return value


def material_legality(xml: str, params: dict) -> tuple[bool, list[str]]:
    """Fail-closed competition material and physical-package gate."""
    violations: list[str] = []
    densities = [float(x) for x in re.findall(r'density="([0-9.]+)"', xml)]
    if not densities:
        violations.append("no serialized material densities")
    for density in densities:
        if not 170.0 - 1e-9 <= density <= 11340.0 + 1e-9:
            violations.append(f"material density {density} kg/m3 outside rule band")
    lowered = xml.lower()
    for tag in (
        "<overridemass>",
        "<overridecg>",
        "<overridesubcomponents>",
        "<overridecd>",
    ):
        if tag in lowered:
            violations.append(f"forbidden override tag {tag}")
    if params.get("s0_main") is not None:
        violations.append("sustainer ascent motor is not null")
    if params.get("s1_main") != 20 or params.get("main_cluster_count") != 3:
        violations.append("booster is not the locked three-K700 octaweb")
    if not params.get("octaweb_rings"):
        violations.append("octaweb rings disabled")
    if not params.get("interstage_coupler"):
        violations.append("interstage coupler disabled")
    dimensions = {
        key: float(params[key])
        for key in (
            "octaweb_ring_width_m",
            "interstage_coupler_length_m",
            "interstage_coupler_wall_m",
            "interstage_coupler_sustainer_overlap_m",
        )
    }
    for key, value in dimensions.items():
        if value < 0.001 - 1e-12:
            violations.append(f"{key}={value} m is below the 1 mm rule")
    violations.extend(validate_compiled_centering_rings(xml))
    violations.extend(validate_compiled_interstage_coupler(xml, required=True))
    violations.extend(validate_upper_stage_ignition_after_separation(xml))
    violations.extend(
        validate_compiled_nose_ballast_attachment(
            xml, params.get("nose_mass_kg")
        )
    )
    return not violations, violations


def candidate_i_params() -> dict:
    params = json.loads(CANDIDATE_I.read_text(encoding="utf-8"))
    params["main_cluster_count"] = 3
    params["wind_levels"] = WIND
    return params


def generate_population(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    base = candidate_i_params()
    population: list[dict] = []
    seen: set[str] = set()

    # Always retain the immutable-family control in the generated population.
    control = dict(base)
    control["s0_retro_delay"] = 1100.0
    control["s1_retro_delay"] = 1100.0
    control["_id"] = "control_I"
    population.append(control)
    seen.add("control_I")

    # Deterministic high-information ridge around Candidate I: isolate the
    # Booster retro impulse from aero changes and let physical Sustainer
    # ballast compensate its ascent-mass delta. Random generation continues
    # below, but this ridge is never left to sampling luck.
    for ballast_total, s1_retro, separation in [
        (7.8, 5, 23.10),
        (7.8, 6, 23.10),
        (7.8, 7, 23.10),
        (7.8, 9, 23.10),
        (7.8, 10, 23.10),
        (7.8, 11, 23.10),
        (8.0, 7, 23.15),
        (8.0, 9, 23.15),
        (8.2, 7, 23.20),
        (8.2, 9, 23.20),
        (8.4, 7, 23.25),
        (8.4, 9, 23.25),
    ]:
        p = dict(base)
        candidate_id = (
            f"ridge_s0K700W_b{ballast_total:.2f}_"
            f"s1{motor_name(s1_retro)}_sep{separation:.2f}"
        )
        p.update(
            {
                "_id": candidate_id,
                "s0_retro": 20,
                "s1_retro": s1_retro,
                "s0_mid_ballast_kg": max(0.0, ballast_total - 1.0),
                "s0_aft_ballast_kg": min(1.0, ballast_total),
                "s0_fin_count": 0,
                "s0_grid_fin_count": 0,
                "s1_separation_delay": separation,
                "s0_retro_delay": 1100.0,
                "s1_retro_delay": 1100.0,
            }
        )
        population.append(p)
        seen.add(candidate_id)

    aero_choices = [
        (0, 0.0, 0.0, 0, 0.0, 0.0),
        (0, 0.0, 0.0, 3, 0.08, 0.06),
        (0, 0.0, 0.0, 4, 0.12, 0.08),
        (3, 0.08, 0.06, 3, 0.10, 0.08),
        (4, 0.10, 0.08, 4, 0.12, 0.10),
    ]
    while len(population) < count:
        s0_retro = rng.choice(list(S0_FAMILIES))
        ballast_total = rng.choice(S0_FAMILIES[s0_retro])
        aft = min(ballast_total, rng.choice([0.0, 0.25, 0.5, 1.0]))
        mid = max(0.0, ballast_total - aft)
        s1_retro = rng.choice(S1_RETROS)
        aft_count, aft_root, aft_height, grid_count, grid_root, grid_height = (
            rng.choice(aero_choices)
        )
        separation = rng.choice([23.05, 23.10, 23.15, 23.25, 23.40])
        candidate_id = (
            f"s0{motor_name(s0_retro)}_b{ballast_total:.2f}_"
            f"a{aft_count}-{aft_root:.2f}-{aft_height:.2f}_"
            f"g{grid_count}-{grid_root:.2f}-{grid_height:.2f}_"
            f"s1{motor_name(s1_retro)}_sep{separation:.2f}"
        )
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        p = dict(base)
        p.update(
            {
                "_id": candidate_id,
                "s0_retro": s0_retro,
                "s1_retro": s1_retro,
                "s0_mid_ballast_kg": mid,
                "s0_aft_ballast_kg": aft,
                "s0_fin_count": aft_count,
                "s0_fin_root": aft_root or 0.08,
                "s0_fin_height": aft_height or 0.06,
                "s0_fin_material": "legal_balsa",
                "s0_grid_fin_count": grid_count,
                "s0_grid_fin_root": grid_root or 0.08,
                "s0_grid_fin_height": grid_height or 0.06,
                "s0_grid_fin_material": "legal_balsa",
                "s0_grid_fin_position_m": 0.05,
                "s1_separation_delay": separation,
                # Delays are disabled for the authority free-descent phase.
                "s0_retro_delay": 1100.0,
                "s1_retro_delay": 1100.0,
            }
        )
        population.append(p)
    return population


def _mount(
    role: str,
    index: int,
    *,
    multiplicity: int = 1,
    radial_offset_m: float = 0.0,
    ignition: str = "launch",
    ignition_delay: float = 1100.0,
) -> ASTNode:
    return ASTNode(
        "MOTOR_MOUNT",
        role=role,
        motor_index=index,
        motor_designation=motor_name(index),
        multiplicity=multiplicity,
        radial_offset_m=radial_offset_m,
        instance_angle_step_deg=120.0 if multiplicity == 3 else 0.0,
        host_inner_radius_m=AST_PROXY_RADIUS_M - 0.002,
        ignition=ignition,
        ignition_delay=ignition_delay,
        mount_material_density=700.0,
    )


def parameters_to_internal_ast(params: dict) -> list[ASTNode]:
    """Project a legal Falcon parameter genome into the native organic AST."""
    A = ASTNode
    nodes = [
        A("STAGE", name="Sustainer", recovery="retro_only"),
        A(
            "NOSE_CONE",
            shape="ogive",
            length=params["nose_length_m"],
            material="aluminum",
            thickness=0.002,
        ),
        A(
            "BODY_TUBE",
            length=params["s0_body_len"],
            radius=AST_PROXY_RADIUS_M,
            material="fiberglass",
            thickness=0.002,
        ),
        A(
            "PAYLOAD",
            mass=params.get("nose_mass_kg", 0.05),
        ),
    ]
    if params.get("s0_mid_ballast_kg", 0.0) > 0.0:
        nodes.append(
            A(
                "BALLAST",
                mass=params["s0_mid_ballast_kg"],
                material="steel",
                axial_offset_m=params["s0_body_len"] * 0.5,
            )
        )
    if params.get("s0_aft_ballast_kg", 0.0) > 0.0:
        nodes.append(
            A(
                "BALLAST",
                mass=params["s0_aft_ballast_kg"],
                material="steel",
                position="aft",
            )
        )
    if params.get("s0_fin_count", 0) > 0:
        nodes.append(
            A(
                "FIN_SET",
                count=params["s0_fin_count"],
                root=params["s0_fin_root"],
                height=params["s0_fin_height"],
                material="balsa",
            )
        )
    if params.get("s0_grid_fin_count", 0) > 0:
        nodes.append(
            A(
                "FIN_SET",
                role="forward_flap",
                count=params["s0_grid_fin_count"],
                root=params["s0_grid_fin_root"],
                height=params["s0_grid_fin_height"],
                material="balsa",
                position_from_top_m=params.get("s0_grid_fin_position_m", 0.05),
            )
        )
    nodes.extend(
        [
            _mount(
                "retro",
                params["s0_retro"],
                ignition="launch",
                # The authority freefall pass disables retro motors, but the
                # Rust proxy must not inherit that 1100 s sentinel: doing so
                # forces every 6-DoF proxy flight to integrate to the engine's
                # 1200 s ceiling. A representative late-flight delay keeps
                # the bulk screen physically meaningful and bounded.
                ignition_delay=46.0,
            ),
            A("CLOSE_BODY"),
            A("STAGE", name="Booster", recovery="retro_only"),
            A(
                "BODY_TUBE",
                length=params["s1_body_len"],
                radius=AST_PROXY_RADIUS_M,
                material="fiberglass",
                thickness=0.002,
            ),
            A(
                "FIN_SET",
                count=params["s1_fin_count"],
                root=params["s1_fin_root"],
                height=params["s1_fin_height"],
                material="balsa",
            ),
            A(
                "FIN_SET",
                role="forward_flap",
                count=params["s1_grid_fin_count"],
                root=params["s1_grid_fin_root"],
                height=params["s1_grid_fin_height"],
                material="balsa",
                position_from_top_m=0.05,
            ),
            # The 53.5 mm radius is the tight, collision-free K700/I-class
            # internal cage center distance. Authority uses the exact compiler.
            _mount(
                "main",
                20,
                multiplicity=3,
                radial_offset_m=0.0535,
                ignition="automatic",
                ignition_delay=0.0,
            ),
            _mount(
                "retro",
                params["s1_retro"],
                ignition="launch",
                ignition_delay=78.0,
            ),
            A("CLOSE_BODY"),
        ]
    )
    return nodes


def parameters_to_ascent_ast(params: dict) -> list[ASTNode]:
    """Fuse the attached stack into a one-stage AST for the ascent-only proxy.

    Candidate I does not separate at Booster burnout; the complete stack
    coasts to apogee and separates at 23.1 s. The generic Rust multistage AST
    otherwise assumes that every upper stage owns an ascent motor and would
    drop the Booster at burnout. This projection preserves the real attached
    mass, length, fins, three-K700 thrust cluster, and both inert wet retro
    motors until apogee. The exact two-stage branches remain OpenRocket-only.
    """
    A = ASTNode
    total_body_length = params["s0_body_len"] + params["s1_body_len"]
    s0_retro = load_motor_by_index(params["s0_retro"])
    s1_retro = load_motor_by_index(params["s1_retro"])
    nodes = [
        A("STAGE", name="Attached Candidate J Stack", recovery="retro_only"),
        A(
            "NOSE_CONE",
            shape="ogive",
            length=params["nose_length_m"],
            material="aluminum",
            thickness=0.002,
        ),
        A(
            "BODY_TUBE",
            length=total_body_length,
            radius=AST_PROXY_RADIUS_M,
            material="fiberglass",
            thickness=0.002,
        ),
        A("PAYLOAD", mass=params.get("nose_mass_kg", 0.05)),
        A(
            "BALLAST",
            mass=s0_retro.dry_mass_kg + s0_retro.propellant_mass_kg,
            material="steel",
            axial_offset_m=params["s0_body_len"] * 0.75,
        ),
        A(
            "BALLAST",
            mass=s1_retro.dry_mass_kg + s1_retro.propellant_mass_kg,
            material="steel",
            axial_offset_m=params["s0_body_len"] + params["s1_body_len"] * 0.75,
        ),
    ]
    if params.get("s0_mid_ballast_kg", 0.0) > 0.0:
        nodes.append(
            A(
                "BALLAST",
                mass=params["s0_mid_ballast_kg"],
                material="steel",
                axial_offset_m=params["s0_body_len"] * 0.5,
            )
        )
    if params.get("s0_aft_ballast_kg", 0.0) > 0.0:
        nodes.append(
            A(
                "BALLAST",
                mass=params["s0_aft_ballast_kg"],
                material="steel",
                axial_offset_m=max(0.05, params["s0_body_len"] - 0.05),
            )
        )
    if params.get("s0_fin_count", 0) > 0:
        nodes.append(
            A(
                "FIN_SET",
                count=params["s0_fin_count"],
                root=params["s0_fin_root"],
                height=params["s0_fin_height"],
                material="balsa",
                position_from_top_m=max(
                    0.0, params["s0_body_len"] - params["s0_fin_root"]
                ),
            )
        )
    if params.get("s0_grid_fin_count", 0) > 0:
        nodes.append(
            A(
                "FIN_SET",
                role="forward_flap",
                count=params["s0_grid_fin_count"],
                root=params["s0_grid_fin_root"],
                height=params["s0_grid_fin_height"],
                material="balsa",
                position_from_top_m=params.get("s0_grid_fin_position_m", 0.05),
            )
        )
    nodes.extend(
        [
            A(
                "FIN_SET",
                count=params["s1_fin_count"],
                root=params["s1_fin_root"],
                height=params["s1_fin_height"],
                material="balsa",
            ),
            A(
                "FIN_SET",
                role="forward_flap",
                count=params["s1_grid_fin_count"],
                root=params["s1_grid_fin_root"],
                height=params["s1_grid_fin_height"],
                material="balsa",
                position_from_top_m=params["s0_body_len"] + 0.05,
            ),
            _mount(
                "main",
                20,
                multiplicity=3,
                radial_offset_m=0.0535,
                ignition="automatic",
                ignition_delay=0.0,
            ),
            A("CLOSE_BODY"),
        ]
    )
    return nodes


def rust_screen(population: list[dict]) -> tuple[list[dict], list]:
    candidates = [
        {
            "id": params["_id"],
            "ast": [node.to_dict() for node in parameters_to_ascent_ast(params)],
        }
        for params in population
    ]
    results = run_rust_evaluator(
        candidates,
        3000.0,
        # Bulk ranking is an ascent screen: retro motors remain as wet inert
        # mass but do not contaminate the apogee with a late landing burn.
        # OpenRocket remains the powered-descent authority below.
        physics_mode="openrocket",
        constraints={"max_mach": 0.99, "simulation_phase": "ascent"},
        execution_profile="authority-heavy",
    )
    by_id = {item.id: item for item in results}
    baseline = by_id.get("control_I")
    baseline_apogee = (
        baseline.apogee_m
        if baseline and baseline.status == "success" and baseline.apogee_m > 1.0
        else 1750.983015850169
    )

    ranked = []
    for params in population:
        result = by_id.get(params["_id"])
        if result is None or result.status != "success":
            continue
        corrected_apogee = result.apogee_m * 2997.564627 / baseline_apogee
        s0_motor = load_motor_by_index(params["s0_retro"])
        s1_motor = load_motor_by_index(params["s1_retro"])
        s0_mass_proxy = (
            1.094
            + params["s0_mid_ballast_kg"]
            + params["s0_aft_ballast_kg"]
            + s0_motor.dry_mass_kg
            + s0_motor.propellant_mass_kg
        )
        s1_mass_proxy = (
            3.258 + s1_motor.dry_mass_kg + s1_motor.propellant_mass_kg
        )
        s0_speed_proxy = 178.795 * math.sqrt(max(s0_mass_proxy, 0.1) / 11.93)
        s1_speed_proxy = 52.619 * math.sqrt(max(s1_mass_proxy, 0.1) / 3.724)
        ratios = {
            "s0": motor_impulse(params["s0_retro"])
            / max(
                1.0,
                s0_mass_proxy * s0_speed_proxy
                + 0.25
                * s0_mass_proxy
                * 9.80665
                * s0_motor.burn_duration_s,
            ),
            "s1": motor_impulse(params["s1_retro"])
            / max(
                1.0,
                s1_mass_proxy * s1_speed_proxy
                + 0.25
                * s1_mass_proxy
                * 9.80665
                * s1_motor.burn_duration_s,
            ),
        }
        rank = (
            abs(corrected_apogee - 3000.0) / 120.0
            + 3.0 * abs(math.log(max(ratios["s0"], 1e-6) / 1.0))
            + 1.5 * abs(math.log(max(ratios["s1"], 1e-6) / 1.7))
            + max(0.0, result.mach - 0.95) * 100.0
        )
        ranked.append(
            {
                "id": params["_id"],
                "rank": rank,
                "corrected_apogee_m": corrected_apogee,
                "impulse_ratios": ratios,
                "rust": result.__dict__,
                "params": params,
            }
        )
    ranked.sort(key=lambda item: item["rank"])
    return ranked, results


def generate_window_target_population() -> list[dict]:
    """Generate the high-information, long-burn dual-retro authority grid.

    This grid keeps Candidate I's aerodynamic geometry and Sustainer topology
    unchanged. It replaces the two high-thrust retros with longer-burn motors,
    then varies only physical ballast, separation timing, and launch attitude.
    The 96.4 g Booster ballast point exactly restores the I211W -> I161W loaded
    motor mass delta; it is a bonded physical component, never a mass override.
    """
    base = candidate_i_params()
    population = []
    for s0_retro, ballast_total in (
        (22, 7.6),
        (22, 8.0),
        (22, 8.4),
    ):
        for booster_ballast in (0.0, 0.0964):
            for separation in (23.05, 23.15, 23.25):
                for launch_angle in (1.4, 1.6, 1.8):
                    p = dict(base)
                    candidate_id = (
                        f"window_mid_s0{motor_name(s0_retro)}_"
                        f"b{ballast_total:.2f}_"
                        f"s1{motor_name(9)}_bb{booster_ballast:.4f}_"
                        f"sep{separation:.2f}_tilt{launch_angle:.1f}"
                    )
                    p.update(
                        {
                            "_id": candidate_id,
                            "s0_retro": s0_retro,
                            "s1_retro": 9,
                            "s0_mid_ballast_kg": ballast_total,
                            "s0_aft_ballast_kg": 0.0,
                            "s0_fin_count": 0,
                            "s0_grid_fin_count": 0,
                            "s1_mid_ballast_kg": booster_ballast,
                            "s1_mid_ballast_attachment": "airframe_bonded",
                            "s1_mid_ballast_rod_radius_m": 0.006,
                            "s1_separation_delay": separation,
                            "launch_angle_deg": launch_angle,
                            "launch_azimuth": 35.0,
                            "s0_retro_delay": 1100.0,
                            "s1_retro_delay": 1100.0,
                        }
                    )
                    population.append({"id": candidate_id, "params": p})
    return population


def select_diverse(ranked: list[dict], count: int) -> list[dict]:
    selected = []
    family_counts: dict[tuple, int] = {}
    for item in ranked:
        p = item["params"]
        family = (
            p["s0_retro"],
            p["s1_retro"],
            p["s0_fin_count"],
            p["s0_grid_fin_count"],
        )
        if family_counts.get(family, 0) >= 1:
            continue
        selected.append(item)
        family_counts[family] = family_counts.get(family, 0) + 1
        if len(selected) >= count:
            break
    return selected


def authority_freefall(selected: list[dict], state: dict) -> list[dict]:
    init_or()
    rows = state.setdefault("freefall", {})
    for number, item in enumerate(selected, 1):
        candidate_id = item["id"]
        if candidate_id in rows:
            continue
        p = dict(item["params"])
        p.pop("_id", None)
        try:
            xml = generate_ork(p)
            legal, violations = material_legality(xml, p)
            if not legal:
                rows[candidate_id] = {
                    "status": "rejected",
                    "violations": violations,
                }
            else:
                metrics = run_sim(
                    xml,
                    seed=OFFICIAL_SEED,
                    wind_seed=OFFICIAL_SEED,
                )
                rows[candidate_id] = {
                    "status": "success",
                    "params": p,
                    "metrics": slim_metrics(metrics),
                    "material_gate": {
                        "legal": True,
                        "custom_materials_allowed_by_primary_rule": True,
                    },
                }
        except Exception as exc:
            rows[candidate_id] = {"status": "failed", "reason": str(exc)}
        print(
            f"[freefall {number}/{len(selected)}] {candidate_id}: "
            f"{rows[candidate_id]['status']}",
            flush=True,
        )
        atomic_json(state["_path"], state)
    return rank_freefall(rows)


def rank_freefall(rows: dict) -> list[dict]:
    ranked = []
    for candidate_id, row in rows.items():
        if row.get("status") != "success":
            continue
        metrics = row["metrics"]
        landings = {
            item["stage_key"]: item for item in metrics.get("stage_landings", [])
        }
        if set(landings) != {"s0", "s1"}:
            continue
        p = row["params"]
        ratios = {}
        sensitivities = {}
        for stage, target in (("s0", 1.0), ("s1", 1.7)):
            index = p[f"{stage}_retro"]
            motor = load_motor_by_index(index)
            landing = landings[stage]
            mass = float(landing["mass_kg"])
            speed = float(landing["total_speed"])
            requirement = (
                mass * speed
                + 0.25 * mass * 9.80665 * motor.burn_duration_s
            )
            ratios[stage] = motor_impulse(index) / max(requirement, 1.0)
            sensitivities[stage] = (
                motor_impulse(index) / motor.burn_duration_s / max(mass, 0.1)
            )
        minimum_margin = float(metrics.get("min_static_margin", -999.0))
        apogee = float(metrics.get("apogee_m", 0.0))
        mach = float(metrics.get("mach", 99.0))
        score = (
            abs(apogee - 3000.0) / 80.0
            + 4.0 * abs(math.log(max(ratios["s0"], 1e-6) / 1.0))
            + 2.0 * abs(math.log(max(ratios["s1"], 1e-6) / 1.7))
            + 0.002 * sensitivities["s0"]
            + 0.004 * sensitivities["s1"]
            + max(0.0, 0.5 - minimum_margin) * 20.0
            + max(0.0, mach - 0.95) * 200.0
        )
        ranked.append(
            {
                "id": candidate_id,
                "rank": score,
                "apogee_m": apogee,
                "mach": mach,
                "min_static_margin": minimum_margin,
                "impulse_ratios": ratios,
                "thrust_sensitivities": sensitivities,
                "landings": landings,
                "params": p,
            }
        )
    ranked.sort(key=lambda item: item["rank"])
    return ranked


def evaluate_powered(
    state: dict,
    candidate: dict,
    s0_delay: float,
    s1_delay: float,
) -> dict:
    key = f"{candidate['id']}|s0={s0_delay:.4f}|s1={s1_delay:.4f}"
    rows = state.setdefault("powered", {})
    if key in rows:
        return rows[key]
    p = dict(candidate["params"])
    p["s0_retro_delay"] = round(s0_delay, 4)
    p["s1_retro_delay"] = round(s1_delay, 4)
    try:
        xml = generate_ork(p)
        legal, violations = material_legality(xml, p)
        if not legal:
            row = {"status": "rejected", "violations": violations}
        else:
            metrics = run_sim(
                xml,
                seed=OFFICIAL_SEED,
                wind_seed=OFFICIAL_SEED,
            )
            landing = {
                item["stage_key"]: item
                for item in metrics.get("stage_landings", [])
            }
            row = {
                "status": "success",
                "s0_delay": s0_delay,
                "s1_delay": s1_delay,
                "speeds": {
                    stage: landing.get(stage, {}).get("total_speed")
                    for stage in ("s0", "s1")
                },
                "metrics": slim_metrics(metrics),
            }
    except Exception as exc:
        row = {"status": "failed", "reason": str(exc)}
    rows[key] = row
    atomic_json(state["_path"], state)
    return row


def powered_search(ranked: list[dict], state: dict, count: int) -> list[dict]:
    init_or()
    summaries = []
    offsets = [-3.0, -2.0, -1.25, -0.75, -0.35, 0.0, 0.35, 0.75, 1.25, 2.0, 3.0]
    for number, candidate in enumerate(ranked[:count], 1):
        land = candidate["landings"]
        s0_motor = load_motor_by_index(candidate["params"]["s0_retro"])
        s1_motor = load_motor_by_index(candidate["params"]["s1_retro"])
        centers = {
            "s0": float(land["s0"]["time_s"]) - 0.36 * s0_motor.burn_duration_s,
            "s1": float(land["s1"]["time_s"]) - 0.264 * s1_motor.burn_duration_s,
        }
        stage_best = {}
        for stage in ("s0", "s1"):
            trials = []
            for offset in offsets:
                delays = dict(centers)
                delays[stage] += offset
                row = evaluate_powered(
                    state, candidate, delays["s0"], delays["s1"]
                )
                speed = row.get("speeds", {}).get(stage)
                if speed is not None:
                    trials.append((float(speed), delays[stage], row))
            if not trials:
                continue
            best = min(trials, key=lambda item: item[0])
            # The algorithm refines only a demonstrated basin.
            if best[0] < 25.0:
                center = best[1]
                for step in range(-5, 6):
                    delay = center + step * 0.1
                    delays = dict(centers)
                    delays[stage] = delay
                    row = evaluate_powered(
                        state, candidate, delays["s0"], delays["s1"]
                    )
                    speed = row.get("speeds", {}).get(stage)
                    if speed is not None:
                        trials.append((float(speed), delay, row))
                best = min(trials, key=lambda item: item[0])
            # Once a real basin has been demonstrated, let the algorithm
            # descend from centiseconds to the 5 ms authority scale. This is
            # intentionally conditional: no fine polishing is spent trying
            # to rescue a family whose coarse best is still a hard impact.
            if best[0] < 50.0:
                for resolution in (0.02, 0.005):
                    center = best[1]
                    for step in range(-5, 6):
                        delay = center + step * resolution
                        delays = dict(centers)
                        delays[stage] = delay
                        row = evaluate_powered(
                            state, candidate, delays["s0"], delays["s1"]
                        )
                        speed = row.get("speeds", {}).get(stage)
                        if speed is not None:
                            trials.append((float(speed), delay, row))
                    best = min(trials, key=lambda item: item[0])
            stage_best[stage] = {
                "speed": best[0],
                "delay": best[1],
            }
        if set(stage_best) == {"s0", "s1"}:
            combined = evaluate_powered(
                state,
                candidate,
                stage_best["s0"]["delay"],
                stage_best["s1"]["delay"],
            )
            summaries.append(
                {
                    "id": candidate["id"],
                    "stage_best": stage_best,
                    "combined": combined,
                    "params": candidate["params"],
                }
            )
        print(
            f"[powered {number}/{min(count, len(ranked))}] "
            f"{candidate['id']} {stage_best}",
            flush=True,
        )
    summaries.sort(
        key=lambda item: max(
            value["speed"] for value in item["stage_best"].values()
        )
    )
    state["powered_summary"] = summaries
    atomic_json(state["_path"], state)
    return summaries


def write_report(state: dict) -> None:
    path = Path(state["_path"]).with_name("REPORT.md")
    rust_ranked = state.get("rust_ranked", [])
    freefall_ranked = state.get("freefall_ranked", [])
    powered = state.get("powered_summary", [])
    lines = [
        "# Candidate J Organic Robustness Campaign",
        "",
        f"- Candidate I immutable hashes verified: `{state['candidate_i_hashes']}`",
        f"- Generated AST population: {state.get('population_count', 0)}",
        f"- Rust-successful candidates: {len(rust_ranked)}",
        f"- OpenRocket freefall authorities: {len(state.get('freefall', {}))}",
        f"- Powered candidates searched: {len(powered)}",
        "- Architecture: internal octaweb, no pods, retro-only Sustainer, "
        "three-K700 Booster, rings and coupler mandatory.",
        "- Material rule: custom/unregistered materials are explicitly permitted "
        "when density is 0.17-11.34 g/cm3.",
        "",
        "## Best powered results",
        "",
        "| candidate | s0 best (m/s) | s1 best (m/s) | combined legal |",
        "|---|---:|---:|---|",
    ]
    for item in powered[:10]:
        combined = item["combined"]
        speeds = combined.get("speeds", {})
        legal = (
            speeds.get("s0") is not None
            and speeds.get("s1") is not None
            and speeds["s0"] < 5.0
            and speeds["s1"] < 5.0
        )
        lines.append(
            f"| {item['id']} | {item['stage_best']['s0']['speed']:.3f} | "
            f"{item['stage_best']['s1']['speed']:.3f} | {legal} |"
        )
    if not powered:
        lines.append("| none yet | - | - | - |")
    lines.extend(
        [
            "",
            "A package is not promoted from this campaign until both stages have "
            "a measured continuous timing window, pass factorized wind/integrator "
            "seeds, pass the timestep ladder, and pass the complete package gate.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("screen", "targeted", "powered", "all"),
        default="all",
    )
    parser.add_argument("--population", type=int, default=120)
    parser.add_argument("--authority", type=int, default=16)
    parser.add_argument("--powered-candidates", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.output_dir / "campaign.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {}
    if state.get("schema") != CAMPAIGN_SCHEMA:
        state = {}
    state["schema"] = CAMPAIGN_SCHEMA
    state["_path"] = str(state_path)
    state["candidate_i_hashes"] = verify_candidate_i()

    population = generate_population(args.population, args.seed)
    state["population_count"] = len(population)
    if not state.get("rust_ranked"):
        ranked, raw = rust_screen(population)
        state["rust_ranked"] = ranked
        state["rust_result_count"] = len(raw)
        atomic_json(state_path, state)
    selected = select_diverse(state["rust_ranked"], args.authority)

    if args.phase in ("screen", "all"):
        state["freefall_ranked"] = authority_freefall(selected, state)
        atomic_json(state_path, state)
    elif args.phase == "targeted":
        targeted = generate_window_target_population()
        state["targeted_population_count"] = len(targeted)
        state["freefall_ranked"] = authority_freefall(targeted, state)
        atomic_json(state_path, state)
    else:
        state["freefall_ranked"] = rank_freefall(state.get("freefall", {}))

    if args.phase in ("powered", "all"):
        if not state["freefall_ranked"]:
            raise RuntimeError("no OpenRocket freefall candidates available")
        powered_search(
            state["freefall_ranked"],
            state,
            args.powered_candidates,
        )

    write_report(state)
    atomic_json(state_path, state)
    print(f"saved {state_path}")
    print(f"saved {args.output_dir / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
