import json
import math
import random
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

import osifog_engine_search as search


def _wind_file(tmp_path: Path) -> Path:
    path = tmp_path / "wind.csv"
    path.write_text(
        "altitude,speed,direction,stddev\n0,1,0,0\n1000,2,90,0\n",
        encoding="utf-8",
    )
    return path


def test_parameters_to_ast_encodes_three_plus_one_roles():
    p = {
        "s0_main": 34, "s0_retro": 34, "s1_main": 34, "s1_retro": 34,
        "s0_body_len": 1.0, "s1_body_len": 1.0,
        "s0_body_rad": 0.30, "s1_body_rad": 0.30,
        "s0_retro_delay": 40.0, "s1_retro_delay": 20.0,
        "s0_fin_count": 4, "s1_fin_count": 4,
        "s0_fin_sweep": 20.0, "s1_fin_sweep": 20.0,
        "s0_fin_root": 0.2, "s1_fin_root": 0.2,
        "s0_fin_height": 0.1, "s1_fin_height": 0.1,
        "s0_fin_thickness_m": 0.003, "s1_fin_thickness_m": 0.003,
        "s0_fin_material": "fiberglass", "s1_fin_material": "fiberglass",
        "nose_mass_kg": 1.0, "s1_aft_ballast_kg": 0.2,
        "s1_separation_delay": 0.75,
    }
    ast = search.parameters_to_ast(p)
    core_mounts = [node for node in ast if node.node_type == "MOTOR_MOUNT"]
    pods = [node for node in ast if node.node_type == "POD"]
    assert len(core_mounts) == 2
    assert len(pods) == 2
    assert all(mount.params["role"] == "retro" for mount in core_mounts)
    assert all(mount.params["multiplicity"] == 1 for mount in core_mounts)
    for pod in pods:
        assert pod.params["instance_count"] == 3
        child_mounts = [
            child for child in pod.params["children"] if child["type"] == "MOTOR_MOUNT"
        ]
        assert len(child_mounts) == 1
        assert child_mounts[0]["params"]["role"] == "main"
        assert child_mounts[0]["params"]["multiplicity"] == 1
    assert pods[0].params["children"][-2]["params"]["delay"] == 0.0
    assert pods[1].params["children"][-2]["params"]["delay"] == 0.75


def test_engine_samples_only_openrocket_supported_materials_and_native_delays():
    parameters = search._sample_valid_parameters(
        random.Random(16000), [(0.0, 1.0, 0.0, 0.0)]
    )

    assert parameters["s0_fin_material"] in {
        "legal_balsa",
        "cardboard",
        "fiberglass",
    }
    assert 5.5 <= parameters["launch_angle_deg"] <= 8.5
    assert 20.0 <= parameters["s0_retro_delay"] <= 120.0
    assert 10.0 <= parameters["s1_retro_delay"] <= 80.0
    assert search._podset_geometry_violations(parameters) == []
    assert parameters["s0_pod_radial_offset"] > (
        parameters["s0_core_radius"] + parameters["s0_pod_radius"]
    )
    search.parameters_to_ast(parameters)


def test_aft_core_fin_does_not_expand_cage_when_axially_clear_of_pods():
    parameters = search._sample_valid_parameters(
        random.Random(16009), [(0.0, 1.0, 0.0, 0.0)]
    )
    parameters.update(
        s0_core_length=1.10,
        s0_core_fin_root=0.20,
        s0_core_fin_height=0.20,
        s0_pod_axial_offset_m=-parameters["s0_pod_nose_length"],
        s0_pod_radial_offset=(
            parameters["s0_core_radius"] + parameters["s0_pod_radius"] + 0.14
        ),
        s0_pod_fin_height=0.0,
        s1_separation_delay=0.0,
    )

    assert not search._core_fin_axially_overlaps_pod(parameters, "s0")
    search._repair_podset_derived_geometry(parameters)

    clearance = (
        parameters["s0_pod_radial_offset"]
        - parameters["s0_core_radius"]
        - parameters["s0_pod_radius"]
    )
    assert clearance == pytest.approx(0.14)
    assert search._podset_geometry_violations(parameters) == []


def test_core_fin_still_requires_radial_clearance_when_axially_overlapping_pod():
    parameters = search._sample_valid_parameters(
        random.Random(16010), [(0.0, 1.0, 0.0, 0.0)]
    )
    parameters.update(
        s0_core_fin_root=0.20,
        s0_core_fin_height=0.20,
        s0_pod_axial_offset_m=(
            parameters["s0_core_length"]
            - parameters["s0_pod_nose_length"]
            - parameters["s0_pod_length"]
        ),
        s0_pod_radial_offset=(
            parameters["s0_core_radius"] + parameters["s0_pod_radius"] + 0.02
        ),
        s0_pod_fin_height=0.0,
    )

    assert search._core_fin_axially_overlaps_pod(parameters, "s0")
    violations = search._podset_geometry_violations(parameters)

    assert any("discrete core fin intersects" in item for item in violations)


def test_three_core_fins_can_pass_between_three_axially_overlapping_pods():
    parameters = search._sample_valid_parameters(
        random.Random(16011), [(0.0, 1.0, 0.0, 0.0)]
    )
    parameters.update(
        s0_core_fin_count=3,
        s0_core_fin_angle_offset_deg=60.0,
        s0_pod_angle_offset_deg=0.0,
        s0_core_fin_root=0.20,
        s0_core_fin_height=0.20,
        s0_pod_axial_offset_m=(
            parameters["s0_core_length"]
            - parameters["s0_pod_nose_length"]
            - parameters["s0_pod_length"]
        ),
        s0_pod_radial_offset=(
            parameters["s0_core_radius"] + parameters["s0_pod_radius"] + 0.02
        ),
        s0_pod_fin_height=0.0,
        s1_separation_delay=0.0,
    )

    assert search._core_fin_axially_overlaps_pod(parameters, "s0")
    assert not search._core_fin_intersects_pod(parameters, "s0")
    search._repair_podset_derived_geometry(parameters)

    clearance = (
        parameters["s0_pod_radial_offset"]
        - parameters["s0_core_radius"]
        - parameters["s0_pod_radius"]
    )
    assert clearance == pytest.approx(0.02)


def test_podset_compiler_and_manifest_share_genuine_stage_ignition_contract():
    parameters = search._sample_valid_parameters(
        random.Random(16001), [(0.0, 1.0, 0.0, 0.0)]
    )
    ork_xml = search.osifog_podset.generate_podset_ork(parameters)
    manifest = search.build_scenario_manifest(
        "OFFICIAL_FULL_MISSION", "podset-contract", parameters, ork_xml
    )

    search.validate_scenario_manifest(manifest, authority_scoring=True)
    assert manifest["ignition_events"]["s0_main"] == "burnout"
    assert manifest["ignition_events"]["s1_main"] == "launch"
    assert manifest["motors_plugged"] is True
    assert manifest["centering_rings_per_stage"] == {"s0": 2, "s1": 2}
    assert manifest["nose_ballast_shell_bonded"] is True

    broken_xml = ork_xml.replace(
        "<ignitionevent>burnout</ignitionevent>",
        "<ignitionevent>launch</ignitionevent>",
        1,
    )
    broken = search.build_scenario_manifest(
        "OFFICIAL_FULL_MISSION", "broken", parameters, broken_xml
    )
    with pytest.raises(ValueError, match="ignition contract mismatch"):
        search.validate_scenario_manifest(broken, authority_scoring=True)


def test_native_ballast_is_three_real_off_axis_rods_and_remains_ast_symmetric():
    parameters = search._sample_valid_parameters(
        random.Random(16002), [(0.0, 1.0, 0.0, 0.0)]
    )
    parameters.update(
        s1_aft_ballast_kg=1.0,
        s1_ballast_kg=1.0,
        s1_aft_ballast_pos_m=0.08,
        s1_aft_ballast_rod_radius_m=0.014,
        s1_aft_ballast_attachment="central_bonded",
    )
    search._repair_podset_derived_geometry(parameters)

    assert search._podset_geometry_violations(parameters) == []
    root = ET.fromstring(search.osifog_podset.generate_podset_ork(parameters))
    rods = [
        item
        for item in root.findall(".//bulkhead")
        if "Booster Core Ballast Rod" in (item.findtext("name") or "")
    ]
    assert len(rods) == 3
    assert all(float(item.findtext("radialposition")) > 0.0 for item in rods)
    assert sum(
        float(item.find("material").attrib["density"])
        * math.pi
        * float(item.findtext("outerradius")) ** 2
        * float(item.findtext("length"))
        for item in rods
    ) == pytest.approx(1.0, rel=1e-6)

    ballast_nodes = [
        node for node in search.parameters_to_ast(parameters)
        if node.node_type == "BALLAST" and node.params["mass"] == 1.0
    ]
    assert ballast_nodes[0].params["instance_count"] == 3
    assert ballast_nodes[0].params["radial_offset_m"] > 0.0


def test_sustainer_aft_ballast_and_forward_fins_have_openrocket_rust_parity():
    parameters = search._sample_valid_parameters(
        random.Random(16005), [(0.0, 1.0, 0.0, 0.0)]
    )
    parameters.update(
        s0_aft_ballast_kg=0.25,
        s0_aft_ballast_pos_m=0.08,
        s0_aft_ballast_rod_radius_m=0.014,
        s0_aft_ballast_attachment="central_bonded",
        s0_grid_fin_count=4,
        s0_grid_fin_root=0.06,
        s0_grid_fin_height=0.04,
        s0_grid_fin_position_m=0.12,
        s0_grid_fin_sweep=10.0,
        s0_grid_fin_thickness_m=0.003,
        s0_grid_fin_material="fiberglass",
    )
    search._repair_podset_derived_geometry(parameters)

    assert search._podset_geometry_violations(parameters) == []
    root = ET.fromstring(search.osifog_podset.generate_podset_ork(parameters))
    forward = [
        item for item in root.findall(".//freeformfinset")
        if item.findtext("name") == "Sustainer Forward Fins"
    ]
    rods = [
        item for item in root.findall(".//bulkhead")
        if "Sustainer Core Ballast Rod" in (item.findtext("name") or "")
    ]
    assert len(forward) == 1
    assert forward[0].findtext("fincount") == "4"
    assert float(forward[0].findtext("position")) == pytest.approx(0.12)
    assert len(rods) == 3

    ast = search.parameters_to_ast(parameters)
    forward_nodes = [
        node for node in ast
        if node.node_type == "FIN_SET"
        and node.params.get("position_from_top_m") == pytest.approx(0.12)
    ]
    ballast_nodes = [
        node for node in ast
        if node.node_type == "BALLAST"
        and node.params.get("mass") == pytest.approx(0.25)
        and node.params.get("instance_count") == 3
    ]
    assert len(forward_nodes) == 1
    assert len(ballast_nodes) == 1
    assert ballast_nodes[0].params["radial_offset_m"] > 0.0


def test_sampler_does_not_emit_uncompiled_mid_ballast_genes():
    parameters = search._sample_valid_parameters(
        random.Random(16006), [(0.0, 1.0, 0.0, 0.0)]
    )
    assert "s0_mid_ballast_kg" not in parameters
    assert "s1_mid_ballast_kg" not in parameters


def test_delay_candidates_fail_closed_when_branch_has_no_apogee(monkeypatch):
    monkeypatch.setattr(search, "_central_burn_time", lambda *args: 2.0)
    metrics = {
        "descent_alignment_diagnostics": [{"branch": 0, "tail_first_windows": []}],
        "stage_landings": [{"branch": 0, "time_s": 20.0}],
        "branch_event_times": [{"APOGEE": []}],
    }
    assert search._delay_candidates(metrics, {}, 0) == []


def test_delay_candidates_prioritize_vertical_and_total_alignment(monkeypatch):
    monkeypatch.setattr(search, "_central_burn_time", lambda *args: 2.0)
    metrics = {
        "descent_alignment_diagnostics": [{
            "branch": 0,
            "tail_first_windows": [],
            "alignment_candidates": [],
            "alignment_trace": [{
                "time_s": 10.0,
                "alignment_q": 0.9,
                "vertical_alignment_q": 0.8,
            }],
        }],
        "stage_landings": [{"branch": 0, "time_s": 20.0}],
        "branch_event_times": [{"APOGEE": [5.0]}],
    }

    candidates = search._delay_candidates(metrics, {}, 0, limit=2)

    assert candidates == [10.0, 9.5]


def test_rust_ast_carries_openrocket_ring_and_pylon_mass_equivalents():
    parameters = search._sample_valid_parameters(
        random.Random(16004), [(0.0, 1.0, 0.0, 0.0)]
    )
    ast = search.parameters_to_ast(parameters)
    structural = [
        node for node in ast
        if node.node_type == "BALLAST"
        and node.params.get("material") in {"aluminum", "fiberglass"}
    ]

    # Per stage: two centering rings plus the evolved pylon stations.
    expected_structural = 4 + sum(
        parameters[f"{prefix}_pylon_station_count"] for prefix in ("s0", "s1")
    )
    assert len(structural) == expected_structural
    assert sum(node.params["mass"] for node in structural) > 0.0
    pylons = [node for node in structural if node.params["instance_count"] == 3]
    assert len(pylons) == expected_structural - 4
    assert all(node.params["radial_offset_m"] > 0.0 for node in pylons)


def test_pylon_genes_are_buildable_and_have_openrocket_rust_mass_parity():
    parameters = search._sample_valid_parameters(
        random.Random(16007), [(0.0, 1.0, 0.0, 0.0)]
    )
    for prefix in ("s0", "s1"):
        gap = (
            parameters[f"{prefix}_pod_radial_offset"]
            - parameters[f"{prefix}_core_radius"]
            - parameters[f"{prefix}_pod_radius"]
        )
        assert gap / parameters[f"{prefix}_pylon_chord_m"] <= 12.0 + 1e-9
        assert gap / parameters[f"{prefix}_pylon_thickness_m"] <= 120.0 + 1e-9
        assert parameters[f"{prefix}_pylon_station_count"] >= 2

    root = ET.fromstring(search.osifog_podset.generate_podset_ork(parameters))
    pylon_fins = [
        node for node in root.findall(".//freeformfinset")
        if "Pylon Station" in (node.findtext("name") or "")
    ]
    assert len(pylon_fins) == 3 * sum(
        parameters[f"{prefix}_pylon_station_count"] for prefix in ("s0", "s1")
    )
    ast_pylon_mass = sum(
        node.params["mass"] for node in search.parameters_to_ast(parameters)
        if node.node_type == "BALLAST"
        and node.params.get("material") == "aluminum"
        and node.params.get("instance_count") == 3
    )
    expected = 0.0
    for prefix in ("s0", "s1"):
        gap = (
            parameters[f"{prefix}_pod_radial_offset"]
            - parameters[f"{prefix}_core_radius"]
            - parameters[f"{prefix}_pod_radius"]
        )
        expected += (
            2700.0 * gap * parameters[f"{prefix}_pylon_chord_m"]
            * parameters[f"{prefix}_pylon_thickness_m"]
            * 3.0 * parameters[f"{prefix}_pylon_station_count"]
        )
    assert ast_pylon_mass == pytest.approx(expected)


def test_pod_axial_gene_has_openrocket_rust_and_pylon_station_parity():
    parameters = search._sample_valid_parameters(
        random.Random(16008), [(0.0, 1.0, 0.0, 0.0)]
    )
    root = ET.fromstring(search.osifog_podset.generate_podset_ork(parameters))
    podsets = root.findall(".//podset")
    ast_pods = [
        node for node in search.parameters_to_ast(parameters)
        if node.node_type == "POD"
    ]
    assert len(podsets) == len(ast_pods) == 2
    for prefix, podset, ast_pod in zip(("s0", "s1"), podsets, ast_pods):
        position = podset.find("position")
        expected_top = parameters[f"{prefix}_pod_axial_offset_m"]
        assert position is not None
        assert position.attrib["type"] == "top"
        assert float(position.text) == pytest.approx(expected_top, abs=1e-6)
        assert ast_pod.params["axial_offset_m"] == pytest.approx(expected_top)

        body_top = expected_top + parameters[f"{prefix}_pod_nose_length"]
        body_bottom = body_top + parameters[f"{prefix}_pod_length"]
        stations = search.osifog_podset.pylon_stations_m(
            parameters[f"{prefix}_core_length"],
            parameters[f"{prefix}_pod_length"],
            parameters[f"{prefix}_pylon_station_count"],
            expected_top,
            parameters[f"{prefix}_pod_nose_length"],
            parameters[f"{prefix}_pylon_chord_m"],
        )
        assert all(body_top <= station < body_bottom for station in stations)


def test_isolated_authority_timeout_is_classified(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise search.subprocess.TimeoutExpired("worker", 12.0)

    monkeypatch.setattr(search.subprocess, "run", timeout)
    monkeypatch.setenv("OSIFOG_AUTHORITY_TIMEOUT_S", "12")
    with pytest.raises(TimeoutError, match="12s wall-clock"):
        search._isolated_openrocket_evaluator({"candidate": "pathological"})


def test_campaign_state_seeds_are_ranked_by_ascent_and_apogee(tmp_path):
    payload = {
        "records": [
            {"ascent_admissible": False, "apogee_m": 2999.0, "parameters": {"id": 1}},
            {"ascent_admissible": True, "apogee_m": 2500.0, "parameters": {"id": 2}},
            {"ascent_admissible": True, "apogee_m": 2900.0, "parameters": {"id": 3}},
        ]
    }
    ordered = search._seed_parameter_candidates(payload, tmp_path / "state.json")
    assert [item["id"] for item in ordered] == [3, 2, 1]


def test_podset_height_gate_includes_radial_assembly_nose_and_body():
    parameters = search._sample_valid_parameters(
        random.Random(16003), [(0.0, 1.0, 0.0, 0.0)]
    )
    parameters["s0_pod_length"] = 4.0
    violations = search._podset_geometry_violations(parameters)
    assert any("height" in violation.lower() for violation in violations)


def test_authority_convergence_requires_bounded_independent_fine_step_results():
    def run(dt, apogee=3000.0, speed=2.0, score=820_000.0):
        return {
            "timestep_s": dt,
            "metrics": {
                "apogee_m": apogee,
                "mach": 0.80,
                "stage_landings": [
                    {"stage_key": "s0", "total_speed": speed, "east_m": 1.0, "north_m": 2.0},
                    {"stage_key": "s1", "total_speed": speed, "east_m": 2.0, "north_m": 1.0},
                ],
            },
            "official": {"raw_score": score, "is_legal": True},
        }

    assert search._authority_convergence(run(0.005), run(0.001))["converged"] is True
    failed = search._authority_convergence(
        run(0.005), run(0.001, apogee=3001.0, speed=2.5, score=810_000.0)
    )
    assert failed["converged"] is False
    assert failed["deltas"]["landing_speed_ms"] == pytest.approx(0.5)


def test_delay_candidates_target_burnout_near_impact_inside_tail_window():
    metrics = {
        "descent_alignment_diagnostics": [
            {
                "branch": 0,
                "tail_first_windows": [
                    {"start_time_s": 20.0, "end_time_s": 50.0}
                ],
            }
        ],
        "stage_landings": [{"branch": 0, "time_s": 50.0}],
        "branch_event_times": [
            {
                "APOGEE": [20.0],
                "IGNITION": [0.0, 200.0],
                "BURNOUT": [2.0, 203.0],
            }
        ],
    }

    candidates = search._delay_candidates(metrics, {}, 0)

    assert 47.0 in candidates
    assert all(20.0 < delay < 50.0 for delay in candidates)


def test_delay_candidates_force_bounded_authority_trials_without_tail_window():
    metrics = {
        "descent_alignment_diagnostics": [
            {"branch": 0, "tail_first_windows": [], "alignment_candidates": []}
        ],
        "stage_landings": [{"branch": 0, "time_s": 50.0}],
        "branch_event_times": [
            {"APOGEE": [20.0], "IGNITION": [0.0, 200.0], "BURNOUT": [2.0, 203.0]}
        ],
    }

    candidates = search._delay_candidates(metrics, {}, 0)

    assert set(candidates) == {46.0, 46.5, 47.0, 47.5, 48.0}


def test_stage_polish_rank_is_total_and_orders_landing_speed():
    metrics = {
        "stage_landings": [{"branch": 0, "total_speed": 4.0, "dist_m": 12.0}],
        "retro_burn_diagnostics": [{
            "branch": 0,
            "retro_braking_verified": True,
            "fraction_opposing_velocity": 0.9,
        }],
    }
    assert search._stage_polish_rank(metrics, 0) == (0, 0, 4.0, -0.9, 12.0)


def test_landing_opportunity_requires_a_usable_full_motor_burn(monkeypatch):
    monkeypatch.setattr(
        search,
        "_load_motor_curve",
        lambda _motor_index: {
            "designation": "TEST",
            "source": "synthetic",
            "points": [(0.0, 100.0), (2.0, 100.0)],
            "burn_duration_s": 2.0,
            "propellant_mass_kg": 1.0,
            "loaded_mass_kg": 2.0,
        },
    )
    trace = [
        {
            "time_s": time,
            "altitude_m": (10.0 - time) * 20.0,
            "speed_ms": 20.0,
            "vertical_speed_ms": -20.0,
            "horizontal_speed_ms": 0.0,
            "theta_deg": 90.0,
            "alignment_q": 1.0,
            "vertical_alignment_q": 1.0,
        }
        for time in (8.0, 9.0, 10.0)
    ]
    metrics = {
        "descent_alignment_diagnostics": [
            {"branch": 0, "stage_key": "s0", "alignment_trace": trace}
        ],
        "branch_event_times": [{"STAGE_SEPARATION": [2.0]}],
        "event_times": {"STAGE_SEPARATION": [2.0]},
        "stage_landings": [
            {
                "branch": 0,
                "time_s": 10.0,
                "total_speed": 20.0,
                "mass_kg": 10.0,
            }
        ],
    }

    opportunity = search._landing_opportunity(
        metrics, {"s0_retro": 7}, branch=0, ignition_time_s=8.0
    )

    assert opportunity["usable"] is True
    assert opportunity["usable_tail_first_duration_s"] == pytest.approx(2.0)
    assert opportunity["opposing_impulse_ns"] == pytest.approx(200.0)
    assert opportunity["available_delta_v_ms"] > 20.0

    trace[1]["alignment_q"] = trace[1]["vertical_alignment_q"] = -1.0
    trace[2]["alignment_q"] = trace[2]["vertical_alignment_q"] = -1.0
    rejected = search._landing_opportunity(
        metrics, {"s0_retro": 7}, branch=0, ignition_time_s=8.0
    )
    assert rejected["usable"] is False
    assert "tail-first window shorter than motor burn" in rejected["rejection_reasons"]


def test_irregular_thrust_curve_integrates_to_hand_calculated_impulse(monkeypatch):
    monkeypatch.setattr(
        search,
        "_load_motor_curve",
        lambda _motor_index: {
            "designation": "IRREGULAR",
            "source": "synthetic",
            "points": [(0.0, 0.0), (1.0, 10.0), (3.0, 0.0)],
            "burn_duration_s": 3.0,
            "propellant_mass_kg": 0.5,
            "loaded_mass_kg": 1.0,
        },
    )
    trace = [
        {
            "time_s": time,
            "altitude_m": 100.0 - 10.0 * (time - 8.0),
            "speed_ms": 5.5,
            "vertical_speed_ms": -5.5,
            "horizontal_speed_ms": 0.0,
            "theta_deg": math.degrees(math.asin(0.8)),
            "alignment_q": 0.8,
            "vertical_alignment_q": 0.8,
        }
        for time in (8.0, 9.0, 11.0)
    ]
    metrics = {
        "descent_alignment_diagnostics": [
            {"branch": 0, "stage_key": "s0", "alignment_trace": trace}
        ],
        "branch_event_times": [{"STAGE_SEPARATION": [2.0]}],
        "event_times": {"STAGE_SEPARATION": [2.0]},
        "stage_landings": [
            {
                "branch": 0,
                "time_s": 11.0,
                "total_speed": 5.5,
                "mass_kg": 10.0,
            }
        ],
    }

    opportunity = search._landing_opportunity(
        metrics, {"s0_retro": 7}, branch=0, ignition_time_s=8.0
    )

    assert opportunity["opposing_impulse_ns"] == pytest.approx(12.0)
    assert opportunity["vertical_braking_impulse_ns"] == pytest.approx(12.0)
    assert opportunity["mean_burn_weighted_q"] == pytest.approx(0.8)
    assert opportunity["motor_burn_duration_s"] == pytest.approx(3.0)


def test_eng_curve_is_loaded_with_explicit_si_units():
    motor = search._load_motor_curve(7)

    assert motor["designation"] == "H180W"
    assert motor["time_unit"] == "s"
    assert motor["thrust_unit"] == "N"
    assert motor["mass_unit"] == "kg"
    assert motor["burn_duration_s"] == pytest.approx(1.313)
    assert motor["points"][0] == (0.0, 0.0)
    assert search._curve_impulse(motor["points"]) > 200.0


def test_landing_opportunity_rejects_invalid_event_order(monkeypatch):
    monkeypatch.setattr(
        search,
        "_load_motor_curve",
        lambda _motor_index: {
            "designation": "TEST",
            "source": "synthetic",
            "points": [(0.0, 10.0), (1.0, 10.0)],
            "burn_duration_s": 1.0,
            "propellant_mass_kg": 0.1,
            "loaded_mass_kg": 0.2,
        },
    )
    trace = [
        {
            "time_s": time,
            "altitude_m": 20.0,
            "speed_ms": 10.0,
            "vertical_speed_ms": -10.0,
            "horizontal_speed_ms": 0.0,
            "theta_deg": 90.0,
            "alignment_q": 1.0,
            "vertical_alignment_q": 1.0,
        }
        for time in (4.0, 5.0, 6.0)
    ]
    metrics = {
        "descent_alignment_diagnostics": [
            {"branch": 0, "stage_key": "s0", "alignment_trace": trace}
        ],
        "branch_event_times": [{"STAGE_SEPARATION": [5.0]}],
        "stage_landings": [
            {"branch": 0, "time_s": 6.0, "total_speed": 10.0, "mass_kg": 1.0}
        ],
    }

    before_separation = search._landing_opportunity(
        metrics, {"s0_retro": 7}, branch=0, ignition_time_s=4.0
    )
    after_impact = search._landing_opportunity(
        metrics, {"s0_retro": 7}, branch=0, ignition_time_s=6.0
    )

    assert before_separation["rejection_reasons"] == [
        "candidate ignition is at or before separation"
    ]
    assert after_impact["rejection_reasons"] == [
        "candidate ignition is at or after impact"
    ]


def test_branch_identity_fails_closed_instead_of_using_branch_order():
    with pytest.raises(ValueError, match="deterministic stage identity"):
        search._stage_key_for_branch(
            {"branch_identities": [{"branch": 0, "stage_key": None}]}, 0
        )


def test_all_scenario_types_build_explicit_fail_closed_manifests():
    parameters = search._sample_valid_parameters(
        random.Random(16004), [(0.0, 1.0, 0.0, 0.0)]
    )

    for scenario_type in search.SCENARIO_TYPES:
        scenario_parameters = dict(parameters)
        if scenario_type in {"STAGE_FREE_DESCENT_DIAGNOSTIC", "DEBUG_ONLY"}:
            scenario_parameters.update(
                s0_retro_delay=0.0,
                s1_retro_delay=0.0,
                s0_retro_ignition_event="never",
                s1_retro_ignition_event="never",
            )
        ork_xml = search.osifog_podset.generate_podset_ork(scenario_parameters)
        manifest = search.build_scenario_manifest(
            scenario_type, "scenario-contract-test", scenario_parameters, ork_xml
        )
        search.validate_scenario_manifest(manifest)
        assert manifest["scenario_type"] == scenario_type
        assert manifest["anti_tumble_serialized_valid"] is True

    diagnostic_parameters = dict(
        parameters,
        s0_retro_delay=0.0,
        s1_retro_delay=0.0,
        s0_retro_ignition_event="never",
        s1_retro_ignition_event="never",
    )
    diagnostic = search.build_scenario_manifest(
        "STAGE_FREE_DESCENT_DIAGNOSTIC",
        "diagnostic",
        diagnostic_parameters,
        search.osifog_podset.generate_podset_ork(diagnostic_parameters),
    )
    with pytest.raises(ValueError, match="cannot be scored"):
        search.validate_scenario_manifest(
            diagnostic, authority_scoring=True
        )


def test_authority_calibration_uses_robust_medians(tmp_path):
    payload = {
        "rust_candidates": [
            {
                "rust": {
                    "apogee_m": 1000.0,
                    "mach": 0.5,
                    "min_static_margin": 2.0,
                }
            }
            for _ in range(9)
        ],
        "openrocket_results": [
            {
                "index": index,
                "metrics": {
                    "apogee_m": 2000.0 if index < 8 else 100000.0,
                    "mach": 0.6 if index < 8 else 10.0,
                    "min_static_margin": 1.6 if index < 8 else 20.0,
                },
            }
            for index in range(9)
        ],
    }
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    calibration = search.load_authority_calibration(result_path)

    assert calibration["sample_count"] == 9
    assert calibration["apogee_delta"] == 2.0
    assert calibration["mach_delta"] == 1.2
    assert calibration["margin_delta"] == 0.8


def test_authority_calibration_composes_with_factor_already_applied(tmp_path):
    payload = {
        "authority_calibration": {
            "apogee_delta": 0.8,
            "mach_delta": 1.1,
            "margin_delta": 0.75,
        },
        "rust_candidates": [
            {
                "rust": {
                    "apogee_m": 1000.0,
                    "mach": 0.5,
                    "min_static_margin": 2.0,
                }
            }
            for _ in range(8)
        ],
        "openrocket_results": [
            {
                "index": index,
                "metrics": {
                    "apogee_m": 1250.0,
                    "mach": 0.6,
                    "min_static_margin": 1.6,
                },
            }
            for index in range(8)
        ],
    }
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    calibration = search.load_authority_calibration(result_path)

    assert calibration["apogee_delta"] == 1.0
    assert calibration["mach_delta"] == 1.32
    assert calibration["margin_delta"] == pytest.approx(0.6)


def test_ascent_scoring_projects_out_descent_only_terms():
    scoring = search._ascent_scoring_table()

    names = {term["name"] for term in scoring["terms"]}
    assert names == {"apogee_altitude", "apogee_horizontal", "propellant_used"}


def test_rust_environment_carries_full_mission_atmosphere_and_wind_profile():
    parameters = {
        "launch_angle_deg": 6.0,
        "launch_azimuth": 34.0,
        "wind_levels": [
            (2.0, 3.0, 215.0, 0.4),
            (1000.0, 12.0, 205.0, 0.7),
        ],
    }

    environment = search._rust_environment(parameters)

    assert environment["base_temperature_k"] == 303.25
    assert environment["base_pressure_pa"] == 100000.0
    assert environment["launch_altitude_m"] == 3.0
    assert environment["wind_levels"][1] == {
        "altitude_m": 1000.0,
        "speed_ms": 12.0,
        "direction_deg": 205.0,
        "std_dev_ms": 0.7,
    }


def test_breeding_preserves_physical_three_plus_one_contract():
    wind = [(0.0, 1.0, 0.0, 0.0)]
    rng = random.Random(16000)
    parents = [
        search._sample_valid_parameters(rng, wind)
        for _ in range(4)
    ]

    child = search._breed_valid_parameters(rng, parents, wind)

    assert child["main_cluster_count"] == 3
    assert search._podset_geometry_violations(child) == []
    search.parameters_to_ast(child)


def test_stratification_keeps_leaders_and_structural_diversity():
    ranked = []
    for index in range(12):
        params = {
            "s0_grid_fin_count": 0 if index < 8 else index,
            "s1_grid_fin_count": 0,
            "s0_main": 1,
            "s1_main": 2,
            "s0_retro": index if index >= 8 else 3,
            "s1_retro": 4,
            "nose_mass_kg": 5.0 if index < 8 else 0.5,
            "s0_aft_ballast_kg": 0.5 if index >= 8 else 0.0,
            "s0_grid_fin_root": 0.1,
            "s0_grid_fin_height": 0.1,
        }
        ranked.append((params, SimpleNamespace(score=100 - index)))

    selected = search._stratify_candidates(ranked, 6)

    assert [item[1].score for item in selected[:2]] == [100, 99]
    assert len({item[0]["s0_grid_fin_count"] for item in selected}) > 1
    assert any(item[0]["nose_mass_kg"] == 0.5 for item in selected)


def test_search_ranks_rust_then_openrocket_and_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "_sample_valid_parameters", lambda rng, wind: {"candidate": rng.random(), "wind_levels": wind})
    monkeypatch.setattr(search, "parameters_to_ast", lambda p: [p])

    def rust_evaluator(population):
        return [
            SimpleNamespace(
                status="success",
                score=float(index),
                rust_apogee_m=3000.0,
                rust_mach=0.8,
                rust_min_static_margin=2.0,
            )
            for index, _ in enumerate(population)
        ]

    calls = []

    def authority(p):
        calls.append(p["candidate"])
        score = p["candidate"] * 1000.0
        return {"apogee_m": 3000.0}, {"score": score, "is_legal": True, "violations": []}

    config = search.SearchConfig(
        rust_budget=5,
        finalist_budget=2,
        seed=7,
        output_dir=tmp_path / "out",
        wind_csv=_wind_file(tmp_path),
    )
    result = search.run_search(config, rust_evaluator=rust_evaluator, openrocket_evaluator=authority)

    assert len(calls) == 2
    assert len(result["openrocket_results"]) == 2
    assert result["best"]["official"]["score"] == max(item["official"]["score"] for item in result["openrocket_results"])
    checkpoint = json.loads((config.output_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["best"]["official"]["is_legal"] is True


def test_openrocket_failure_is_checkpointed(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "_sample_valid_parameters", lambda rng, wind: {"candidate": 1, "wind_levels": wind})
    monkeypatch.setattr(search, "parameters_to_ast", lambda p: [p])
    rust_result = SimpleNamespace(
        status="success", score=1.0, rust_apogee_m=0.0, rust_mach=0.0,
        rust_min_static_margin=0.0,
    )
    config = search.SearchConfig(
        rust_budget=1, finalist_budget=1, output_dir=tmp_path / "out",
        wind_csv=_wind_file(tmp_path),
    )
    result = search.run_search(
        config,
        rust_evaluator=lambda _: [rust_result],
        openrocket_evaluator=lambda _: (_ for _ in ()).throw(RuntimeError("authority failed")),
    )
    assert "authority failed" in result["openrocket_results"][0]["error"]
    assert result["best"] is None


def test_autopilot_feeds_each_authority_result_into_next_cycle(
    tmp_path, monkeypatch
):
    seen = []

    def fake_run_search(config):
        seen.append(config)
        cycle = len(seen)
        config.output_dir.mkdir(parents=True, exist_ok=True)
        (config.output_dir / "result.json").write_text("{}", encoding="utf-8")
        return {
            "best": {
                "official": {
                    "is_legal": True,
                    "score": 840_000.0 + cycle * 10_000.0,
                }
            },
            "authority_calibration": None,
        }

    monkeypatch.setattr(search, "run_search", fake_run_search)
    root = tmp_path / "autopilot"
    result = search.run_autopilot(
        search.SearchConfig(output_dir=root, resume=False),
        cycles=3,
        target_score=860_000.0,
    )

    assert len(seen) == 2
    assert seen[0].calibration_result is None
    assert seen[1].calibration_result == root / "cycle-001" / "result.json"
    assert result["best"]["official"]["score"] == 860_000.0
    summary = json.loads((root / "autopilot.json").read_text(encoding="utf-8"))
    assert summary["goal_reached"] is True


def test_autopilot_retries_with_new_seed_and_clears_alert(tmp_path, monkeypatch):
    seen = []

    def flaky_run_search(config):
        seen.append(config.seed)
        if len(seen) == 1:
            raise RuntimeError("transient evaluator failure")
        config.output_dir.mkdir(parents=True, exist_ok=True)
        (config.output_dir / "result.json").write_text("{}", encoding="utf-8")
        return {"best": None, "authority_calibration": None}

    monkeypatch.setattr(search, "run_search", flaky_run_search)
    root = tmp_path / "autopilot-retry"
    search.run_autopilot(
        search.SearchConfig(output_dir=root, seed=7, resume=False),
        cycles=1,
    )

    assert seen == [7, 1_000_010]
    alert = json.loads((root / "alert.json").read_text(encoding="utf-8"))
    health = json.loads((root / "health.json").read_text(encoding="utf-8"))
    assert alert["status"] == "clear"
    assert health["status"] == "running"


def test_resume_deduplicates_by_candidate_identity_not_rank(tmp_path, monkeypatch):
    candidates = iter([{"candidate": 1}, {"candidate": 2}])
    monkeypatch.setattr(
        search, "_sample_valid_parameters", lambda rng, wind: next(candidates)
    )
    monkeypatch.setattr(search, "parameters_to_ast", lambda p: [p])
    output = tmp_path / "identity-resume"
    output.mkdir()
    prior = {
        "index": 99,
        "candidate_id": search._candidate_id({"candidate": 2}),
        "parameters": {"candidate": 2},
        "official": {"is_legal": False, "score": -1_000_000.0},
    }
    (output / "checkpoint.json").write_text(
        json.dumps({"openrocket_results": [prior]}), encoding="utf-8"
    )
    rust_results = [
        SimpleNamespace(status="success", score=2.0, rust_apogee_m=0.0, rust_mach=0.0, rust_min_static_margin=0.0),
        SimpleNamespace(status="success", score=1.0, rust_apogee_m=0.0, rust_mach=0.0, rust_min_static_margin=0.0),
    ]
    calls = []
    search.run_search(
        search.SearchConfig(
            rust_budget=2, finalist_budget=2, output_dir=output,
            wind_csv=_wind_file(tmp_path), resume=True,
        ),
        rust_evaluator=lambda _: rust_results,
        openrocket_evaluator=lambda p: (
            calls.append(p["candidate"]) or ({}, {"is_legal": False, "score": -1_000_000.0})
        ),
    )
    assert calls == [1]


def test_campaign_rejects_second_live_owner(tmp_path):
    root = tmp_path / "lease"
    root.mkdir()
    (root / "campaign.lease.json").write_text(
        json.dumps({"pid": search.os.getpid(), "token": "other"}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="already owned"):
        with search._campaign_lease(root):
            pass


def test_campaign_recovers_stale_lease(tmp_path):
    root = tmp_path / "stale-lease"
    root.mkdir()
    (root / "campaign.lease.json").write_text(
        json.dumps({"pid": -1, "token": "dead"}), encoding="utf-8"
    )
    with search._campaign_lease(root):
        assert (root / "campaign.lease.json").exists()
    assert not (root / "campaign.lease.json").exists()
    assert list(root.glob("campaign.lease.stale-*.json"))


def test_campaign_is_idempotent_after_certified_goal(tmp_path, monkeypatch):
    calls = []

    def fake_run_search(config):
        calls.append(config.seed)
        config.output_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "best": {
                "candidate_id": "winner",
                "parameters": {"candidate": 7},
                "official": {"is_legal": True, "score": 810_000.0},
            },
            "openrocket_results": [],
            "persisted_authority": {
                "path": str(config.output_dir / "best-authority.ork"),
                "official": {"is_legal": True, "score": 810_000.0},
                "certified": True,
                "deterministic": True,
                "replay_count": 5,
            },
        }
        (config.output_dir / "result.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        return result

    monkeypatch.setattr(search, "run_search", fake_run_search)
    campaign = search.CampaignConfig(
        search=search.SearchConfig(
            rust_budget=10, finalist_budget=2,
            output_dir=tmp_path / "campaign", wind_csv=_wind_file(tmp_path),
        ),
        max_shards=3,
        target_score=800_001.0,
    )
    first = search.run_campaign(campaign)
    second = search.run_campaign(campaign)
    assert first["status"] == second["status"] == "goal_reached"
    assert first["certified_score"] == 810_000.0
    assert len(calls) == 1
    assert (campaign.search.output_dir / "champion.json").exists()
