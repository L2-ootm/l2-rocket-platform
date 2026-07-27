import json
import random
from pathlib import Path

import pytest

import osifog_engine_search as search
import osifog_legal_stage_campaign as campaign


def _metrics(separation=4.0, apogee=20.0):
    return {
        "mach": 0.8,
        "min_static_margin": 1.8,
        "event_times": {
            "STAGE_SEPARATION": [separation],
            "APOGEE": [apogee],
        },
        "ascent_stability_segments": [
            {"segment": "full_stack", "min_calibers": 1.8},
            {"segment": "sustainer", "min_calibers": 1.7},
            {"segment": "booster", "min_calibers": 2.0},
        ],
        "descent_alignment_diagnostics": [
            {"stage_key": "s0", "tail_first_windows": [{"duration_s": 4.0}]},
            {"stage_key": "s1", "tail_first_windows": [{"duration_s": 8.0}]},
        ],
        "stage_landings": [{"branch": 0}, {"branch": 1}],
    }


def test_history_excludes_quarantined_and_post_apogee_records(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(search, "_podset_geometry_violations", lambda _: [])
    good_dir = tmp_path / "osifog_recovery_gate_v1"
    bad_dir = tmp_path / "osifog_recovery_gate_v2"
    quarantined_dir = tmp_path / "osifog_recovery_gate_v3"
    for directory in (good_dir, bad_dir, quarantined_dir):
        directory.mkdir()
    (quarantined_dir / "QUARANTINED.md").write_text("no", encoding="utf-8")
    for directory, metrics in (
        (good_dir, _metrics()),
        (bad_dir, _metrics(separation=21.0)),
        (quarantined_dir, _metrics()),
    ):
        (directory / "result.json").write_text(json.dumps({
            "records": [{"candidate_id": directory.name, "parameters": {"x": 1}, "metrics": metrics}]
        }), encoding="utf-8")

    records = campaign.load_history(tmp_path)

    assert [item["candidate_id"] for item in records] == [good_dir.name]


def test_stagewise_proposal_is_buildable_and_keeps_pods_permanent():
    wind = [(0.0, 1.0, 0.0, 0.0)]
    first = search._sample_valid_parameters(random.Random(16001), wind)
    second = search._sample_valid_parameters(random.Random(16002), wind)

    proposal = campaign.stagewise_proposal(
        random.Random(16003), wind, [first, second], [first, second]
    )

    assert proposal["s0_retro_delay"] == 200.0
    assert proposal["s1_retro_delay"] == 200.0
    assert 0.0 <= proposal["s1_separation_delay"] <= 1.0
    assert search._podset_geometry_violations(proposal) == []
    pods = [node for node in search.parameters_to_ast(proposal) if node.node_type == "POD"]
    assert len(pods) == 2
    assert all("separation_delay" not in pod.params for pod in pods)
    assert proposal["s0_fin_height"] == proposal["s0_core_fin_height"]


def test_aero_stability_mutation_uses_lever_arm_instead_of_heavy_ballast():
    parameters = {
        "nose_mass_kg": 3.0, "nose_length_m": 0.9,
        "s0_core_length": 0.6, "s0_core_fin_count": 3,
        "s0_core_fin_height": 0.05, "s0_core_fin_root": 0.08,
    }

    campaign._apply_sustainer_aero_stability_mutation(parameters, random.Random(17001))

    assert parameters["nose_mass_kg"] <= 1.4
    assert parameters["nose_length_m"] >= 1.2
    assert parameters["s0_core_length"] >= 0.75
    assert parameters["s0_core_fin_height"] >= 0.12
    assert parameters["s0_core_fin_root"] >= 0.12


def test_aero_stability_mutation_builds_narrow_interleaved_cage():
    wind = [(0.0, 1.0, 0.0, 0.0)]
    parameters = search._sample_valid_parameters(random.Random(17002), wind)

    campaign._apply_sustainer_aero_stability_mutation(
        parameters, random.Random(17003)
    )
    search._repair_podset_derived_geometry(parameters)

    gap = (
        parameters["s0_pod_radial_offset"]
        - parameters["s0_core_radius"]
        - parameters["s0_pod_radius"]
    )
    assert parameters["s0_core_fin_count"] == 3
    assert parameters["s0_core_fin_angle_offset_deg"] == pytest.approx(60.0)
    assert parameters["s0_grid_fin_count"] == 0
    assert parameters["s1_separation_delay"] == 0.0
    assert gap == pytest.approx(0.008)
    assert not search._core_fin_intersects_pod(parameters, "s0")
    assert search._podset_geometry_violations(parameters) == []


def test_stagewise_crossover_preserves_discrete_recovery_module():
    wind = [(0.0, 1.0, 0.0, 0.0)]
    stable = search._sample_valid_parameters(random.Random(18001), wind)
    recovery = search._sample_valid_parameters(random.Random(18002), wind)
    recovery["s0_retro"] = 19
    recovery["nose_mass_kg"] = 0.7
    recovery["s1_retro"] = 18
    recovery["s1_aft_ballast_kg"] = 1.0

    proposal = campaign.stagewise_proposal(
        random.Random(18003), wind, [stable], [stable],
        sustainer_recovery_parents=[recovery],
        booster_recovery_parents=[recovery],
    )

    assert proposal["s0_retro"] == 19
    assert proposal["nose_mass_kg"] == 0.8
    assert proposal["s1_retro"] == 18
    assert proposal["s1_aft_ballast_kg"] == 1.0


def test_parent_rankings_keep_ascent_and_recovery_phenotypes_separate():
    stable = {
        "metrics": {**_metrics(), "apogee_m": 2950.0},
        "landing_opportunities": [{
            "stage_key": "s0", "available_delta_v_ms": 20.0,
            "required_delta_v_ms": 100.0, "motor_burn_duration_s": 2.0,
            "usable_tail_first_duration_s": 2.0,
            "fraction_burn_opposing_total_velocity": 0.7,
        }],
    }
    recovery = {
        "metrics": {**_metrics(), "apogee_m": 2100.0},
        "landing_opportunities": [{
            "stage_key": "s0", "available_delta_v_ms": 220.0,
            "required_delta_v_ms": 100.0, "motor_burn_duration_s": 2.0,
            "usable_tail_first_duration_s": 2.0,
            "fraction_burn_opposing_total_velocity": 0.8,
        }],
    }
    stable["metrics"]["ascent_stability_segments"][1]["min_calibers"] = 2.0
    recovery["metrics"]["ascent_stability_segments"][1]["min_calibers"] = 0.2

    assert campaign._sustainer_ascent_quality(stable) > campaign._sustainer_ascent_quality(recovery)
    assert campaign._stage_recovery_quality(recovery, "s0") > campaign._stage_recovery_quality(stable, "s0")
    unmeasured = {"metrics": _metrics(), "landing_opportunities": []}
    assert campaign._stage_recovery_quality(stable, "s0") > campaign._stage_recovery_quality(unmeasured, "s0")


def test_recovery_parent_pool_is_measured_and_motor_stratified():
    records = []
    for motor in (14, 15, 19):
        for index in range(4):
            records.append({
                "parameters": {"s0_retro": motor, "marker": f"{motor}-{index}"},
                "metrics": _metrics(),
                "landing_opportunities": [{
                    "stage_key": "s0",
                    "available_delta_v_ms": 150.0 + motor,
                    "required_delta_v_ms": 100.0,
                    "motor_burn_duration_s": 2.0,
                    "usable_tail_first_duration_s": 2.0,
                    "fraction_burn_opposing_total_velocity": 0.9,
                    "fraction_burn_opposing_vertical_velocity": 0.9,
                }],
            })
    records.append({
        "parameters": {"s0_retro": 8, "marker": "unmeasured"},
        "metrics": _metrics(), "landing_opportunities": [],
    })

    parents = campaign._recovery_parent_parameters(records, "s0", 6)

    assert {item["s0_retro"] for item in parents} == {14, 15, 19}
    assert all(item["marker"] != "unmeasured" for item in parents)


def test_rust_selection_reserves_morphology_exploration_quota():
    ranked = []
    for index in range(20):
        parameters = {
            "nose_mass_kg": 0.8 + index * 0.4,
            "s0_core_fin_height": 0.05 + index * 0.08,
            "s0_core_length": 0.5 + index * 0.15,
            "s0_main": index,
            "s0_retro": index,
        }
        ranked.append(((str(index), parameters), [20.0 - index]))

    selected = campaign._select_rust_inputs(ranked, 10)

    assert [item[0][0] for item in selected[:8]] == [str(i) for i in range(8)]
    assert any(int(item[0][0]) >= 8 for item in selected[8:])


def test_authority_selection_reserves_high_lever_arm_calls():
    survivors = []
    for index in range(12):
        parameters = {
            "s0_main": index, "s0_retro": index,
            "s0_core_fin_count": 4, "s0_core_fin_height": 0.05,
            "s0_core_length": 0.6, "nose_mass_kg": 1.0,
            "s0_grid_fin_count": 0,
        }
        prediction = [2.0, 2.0, 2.0, 2.0, 3000.0, 0.8]
        survivors.append((((f"small-{index}", parameters), prediction), None))
    for index in range(4):
        parameters = {
            "s0_main": 20 + index, "s0_retro": 20 + index,
            "s0_core_fin_count": 6, "s0_core_fin_height": 0.20 + index * 0.02,
            "s0_core_length": 1.0, "nose_mass_kg": 1.0,
            "s0_grid_fin_count": 0,
        }
        prediction = [0.5, 2.0, 2.0, 2.0, 2500.0, 0.8]
        survivors.append((((f"aero-{index}", parameters), prediction), None))

    selected = campaign._diverse_authority_selection(survivors, 8)

    assert len(selected) == 8
    assert sum(item[0][0][0].startswith("aero-") for item in selected) == 2


def test_authority_record_fails_closed_on_any_ascent_segment(monkeypatch):
    metrics = _metrics()
    metrics["ascent_stability_segments"][1]["min_calibers"] = 1.49
    monkeypatch.setattr(search, "_ascent_admissible", lambda *_: (True, []))
    monkeypatch.setattr(search, "_delay_candidates", lambda *args, **kwargs: [10.0])
    monkeypatch.setattr(search, "_landing_opportunity", lambda *args, **kwargs: {"usable": True})
    monkeypatch.setattr(search, "_candidate_id", lambda _: "candidate")

    record = campaign._authority_record({}, metrics)

    assert not record["ascent_admissible"]
    assert not record["recovery_basin_pass"]


def test_acquisition_prefers_joint_feasibility_over_single_metric_extreme():
    balanced = campaign._acquisition([1.8, 1.6, 2.0, 2.0, 2900.0, 0.8])
    no_tail = campaign._acquisition([4.0, 0.0, 4.0, 4.0, 3000.0, 0.7])

    assert balanced > no_tail


def test_acquisition_uses_motor_aware_delta_v_and_alignment_when_available():
    feasible = campaign._acquisition([
        1.8, 1.6, 2.0, 2.0, 2900.0, 0.8,
        10.0, 0.9, 1.2, 20.0, 0.9, 1.2,
    ])
    insufficient_delta_v = campaign._acquisition([
        1.8, 1.6, 2.0, 2.0, 2900.0, 0.8,
        -90.0, 0.9, 1.2, 20.0, 0.9, 1.2,
    ])

    assert feasible > insufficient_delta_v


def test_acquisition_rejects_sideways_braking_even_with_total_alignment():
    vertical = campaign._acquisition([
        1.8, 1.6, 2.0, 2.0, 2900.0, 0.8,
        10.0, 0.9, 0.9, 1.2,
        20.0, 0.9, 0.9, 1.2,
    ])
    sideways = campaign._acquisition([
        1.8, 1.6, 2.0, 2.0, 2900.0, 0.8,
        10.0, 0.9, 0.2, 1.2,
        20.0, 0.9, 0.9, 1.2,
    ])

    assert vertical > sideways


def test_acquisition_rejects_low_apogee_drag_shortcut():
    near_target = campaign._acquisition([1.8, 1.6, 2.0, 2.0, 2900.0, 0.8])
    low_apogee = campaign._acquisition([1.8, 1.6, 2.0, 2.0, 600.0, 0.4])

    assert near_target > low_apogee
