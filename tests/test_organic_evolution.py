import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

import pytest

from rocket_ast import ASTNode, MOTOR_DATABASE


def simple_ast(motor_index=12, deploy="apogee"):
    return [
        ASTNode("STAGE", name="Test Sustainer"),
        ASTNode("NOSE_CONE", shape="conical", length=0.3, material="cardboard"),
        ASTNode("BODY_TUBE", length=1.0, radius=0.04, material="cardboard"),
        ASTNode("PARACHUTE", deploy=deploy, diameter=0.5),
        ASTNode("FIN_SET", count=4, sweep=30.0, root=0.12, height=0.06),
        ASTNode("MOTOR_MOUNT", motor_index=motor_index, ignition="automatic"),
        ASTNode("CLOSE_BODY"),
    ]


def test_ckg_penalizes_previously_failed_subgraphs(tmp_path):
    from ckg_memory import ContinuousKnowledgeGraph

    path = tmp_path / "ckg.json"
    ckg = ContinuousKnowledgeGraph(path)
    ast = simple_ast(motor_index=14, deploy="ejection")

    assert ckg.penalty_for(ast) == 0.0

    ckg.record(ast, score=-10.0, status="failed", reason="bad deploy")
    ckg.save()
    later = ContinuousKnowledgeGraph(path)

    assert later.penalty_for(ast) > 0.0
    assert later.acceptance_multiplier(ast) < 1.0


def test_ckg_rewards_success_without_hiding_failures(tmp_path):
    from ckg_memory import ContinuousKnowledgeGraph

    path = tmp_path / "ckg.json"
    ckg = ContinuousKnowledgeGraph(path)
    ast = simple_ast(motor_index=18)

    ckg.record(ast, score=42.0, status="success", reason="clean")
    ckg.record(ast, score=-5.0, status="failed", reason="unstable")
    ckg.save()

    reloaded = ContinuousKnowledgeGraph(path)
    entries = list(reloaded.iter_entries(ast))

    assert entries
    assert any(entry["failures"] == 1 for entry in entries)
    assert any(entry["successes"] == 1 for entry in entries)


def test_ckg_save_replaces_existing_valid_memory(tmp_path):
    from ckg_memory import ContinuousKnowledgeGraph

    path = tmp_path / "ckg.json"
    ckg = ContinuousKnowledgeGraph(path)
    ckg.record(simple_ast(motor_index=10), score=1.0, status="success", reason="first")
    ckg.save()

    ckg.record(simple_ast(motor_index=11), score=2.0, status="failed", reason="second")
    ckg.save()

    assert not list(tmp_path.glob(".ckg.json.*.tmp"))
    reloaded = ContinuousKnowledgeGraph(path)
    assert len(reloaded.entries) >= 2


def test_ckg_save_retries_transient_replace_denial(tmp_path, monkeypatch):
    from pathlib import Path

    from ckg_memory import ContinuousKnowledgeGraph

    original_replace = Path.replace
    calls = {"count": 0}

    def flaky_replace(self, target):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("synthetic lock")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    ckg = ContinuousKnowledgeGraph(tmp_path / "ckg.json")
    ckg.record(simple_ast(motor_index=12), score=3.0, status="success", reason="retry")

    ckg.save()

    assert calls["count"] == 2
    assert ContinuousKnowledgeGraph(tmp_path / "ckg.json").entries


def test_ckg_records_auditable_apogee_and_mach_calibrations(tmp_path):
    from ckg_memory import ContinuousKnowledgeGraph

    ckg = ContinuousKnowledgeGraph(tmp_path / "ckg.json")

    ckg.record_calibration("sig", 0.95, 0.98)
    ckg.record_calibration("sig", 1.05, 1.02)

    entry = ckg.calibrations["sig"]
    assert entry["count"] == 2
    assert entry["avg_apogee_delta"] == 1.0
    assert entry["avg_mach_delta"] == 1.0
    assert entry["min_apogee_delta"] == 0.95
    assert entry["max_mach_delta"] == 1.02
    assert entry["last"] == {"apogee_delta": 1.05, "mach_delta": 1.02}


def test_ckg_authority_memory_is_stage_contextual_not_generic(tmp_path):
    from ckg_memory import ContinuousKnowledgeGraph

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode("MOTOR_MOUNT", motor_index=18, motor_designation=MOTOR_DATABASE[18][1]),
        ASTNode("CLOSE_BODY"),
        ASTNode("STAGE", name="Booster"),
        ASTNode("BODY_TUBE", length=0.8, radius=0.08),
        ASTNode("FIN_SET", count=4, root=0.18, height=0.09),
        ASTNode("MOTOR_MOUNT", motor_index=22, motor_designation=MOTOR_DATABASE[22][1]),
        ASTNode("CLOSE_BODY"),
    ]
    ckg = ContinuousKnowledgeGraph(tmp_path / "ckg.json")

    ckg.record_authority(ast, score=0.0, status="failed", reason="or_authority:min_static_margin")
    labels = [entry["label"] for entry in ckg.entries.values()]

    assert all(not label.startswith("STAGE:") for label in labels)
    assert "CLOSE_BODY:{}" not in labels
    assert any(label.startswith("AUTHORITY_STAGE[0]") for label in labels)
    assert any("motor=" in label and "fin_height=" in label for label in labels)


def test_organic_loop_exports_ranked_elites(tmp_path):
    from organic_loop import OrganicLoopConfig, run_generation

    config = OrganicLoopConfig(
        population=8,
        elite_count=3,
        seed=7,
        output_dir=tmp_path / "elites",
        ckg_path=tmp_path / "ckg.json",
        evaluator="heuristic",
    )

    result = run_generation(config)

    assert len(result.elites) == 3
    assert result.elites[0].score >= result.elites[-1].score
    assert (tmp_path / "elites" / "organic_elite.json").exists()
    assert (tmp_path / "elites" / "organic_G000_I000.ork").exists()
    with zipfile.ZipFile(tmp_path / "elites" / "organic_G000_I000.ork") as archive:
        assert archive.namelist() == ["rocket.ork"]
        assert archive.read("rocket.ork").startswith(b'<?xml version="1.0" encoding="utf-8"?>')

    payload = json.loads((tmp_path / "elites" / "organic_elite.json").read_text())
    assert payload["generated_by"].startswith("organic_loop")
    assert len(payload["elite"]) == 3


def test_json_report_write_replaces_existing_valid_report(tmp_path):
    from organic_loop import write_json_report

    report = tmp_path / "nested" / "organic_elite.json"
    write_json_report(report, {"elite": [{"score": 1.0}]})
    write_json_report(report, {"elite": [{"score": 2.0}], "status": "complete"})

    assert not (tmp_path / "nested" / ".organic_elite.json.tmp").exists()
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "elite": [{"score": 2.0}],
        "status": "complete",
    }


def test_organic_loop_ranks_rust_results_and_records_failures(tmp_path):
    from organic_loop import OrganicLoopConfig, RustEvaluationResult, run_generation

    calls = []

    def fake_evaluator(candidates, _target_apogee_m, physics_mode, objectives, constraints, calibrations):
        assert physics_mode == "openrocket"
        assert objectives is None
        assert constraints is None
        assert isinstance(calibrations, dict)
        calls.append(len(candidates))
        return [
            RustEvaluationResult(
                id=candidate["id"],
                status="success" if idx != 1 else "failed",
                score=10.0 - idx,
                apogee_m=15000.0 + idx,
                mach=1.0,
                min_static_margin=1.5,
                margins=[1.5],
                reason="ok" if idx != 1 else "synthetic failure",
            )
            for idx, candidate in enumerate(candidates)
        ]

    config = OrganicLoopConfig(
        population=4,
        elite_count=2,
        seed=8,
        output_dir=tmp_path / "elites",
        ckg_path=tmp_path / "ckg.json",
        evaluator="rust",
        rust_evaluator=fake_evaluator,
    )

    result = run_generation(config)

    assert calls
    assert result.elites[0].rust_apogee_m == 15000.0
    assert result.elites[0].score >= result.elites[-1].score

    entries = list(result.ckg.entries.values())
    assert any(entry["failures"] > 0 for entry in entries)


def test_organic_loop_passes_osifog_mission_contract_to_rust(tmp_path):
    from organic_loop import OrganicLoopConfig, RustEvaluationResult, run_generation

    mission = json.loads(open("missions/precision_350m.json", encoding="utf-8").read())
    captured = {}

    def fake_evaluator(candidates, target_apogee_m, physics_mode, objectives, constraints, calibrations):
        captured["target_apogee_m"] = target_apogee_m
        captured["physics_mode"] = physics_mode
        captured["objectives"] = objectives
        captured["constraints"] = constraints
        captured["candidate_count"] = len(candidates)
        assert isinstance(calibrations, dict)
        return [
            RustEvaluationResult(
                id=candidate["id"],
                status="success",
                score=100.0 - idx,
                apogee_m=350.0,
                mach=0.2,
                min_static_margin=1.8,
                margins=[1.8],
                reason="ok",
            )
            for idx, candidate in enumerate(candidates)
        ]

    config = OrganicLoopConfig(
        population=3,
        elite_count=1,
        seed=20260719,
        target_apogee_m=350.0,
        mission_path=tmp_path / "precision_350m.json",
        output_dir=tmp_path / "elites",
        ckg_path=tmp_path / "ckg.json",
        evaluator="rust",
        rust_evaluator=fake_evaluator,
        objectives=mission["objectives"],
        constraints=mission["constraints"],
    )

    result = run_generation(config)

    assert result.elites[0].rust_apogee_m == 350.0
    assert captured["target_apogee_m"] == 350.0
    assert captured["physics_mode"] == "openrocket"
    assert captured["objectives"] == mission["objectives"]
    assert captured["constraints"]["min_static_margin"] == 1.5
    assert captured["candidate_count"] == 3


def test_rust_population_preserves_per_candidate_launch_environment(tmp_path):
    from ckg_memory import ContinuousKnowledgeGraph
    from organic_loop import (
        OrganicLoopConfig,
        RustEvaluationResult,
        evaluate_rust_population,
    )

    environment = {
        "launch_rod_length_m": 6.0,
        "launch_rod_angle_rad": 0.1,
        "launch_rod_direction_rad": 0.5,
        "wind_speed_mps": 4.0,
        "wind_direction_rad": 1.2,
        "relative_humidity": 0.82,
    }
    captured = {}

    def fake_evaluator(
        candidates,
        target_apogee_m,
        physics_mode,
        objectives,
        constraints,
        calibrations,
    ):
        captured["environment"] = candidates[0]["environment"]
        return [
            RustEvaluationResult(
                id=candidates[0]["id"],
                status="success",
                score=1.0,
                apogee_m=3000.0,
                mach=0.8,
                min_static_margin=1.6,
                margins=[1.6],
                reason="ok",
            )
        ]

    evaluate_rust_population(
        [simple_ast()],
        ContinuousKnowledgeGraph(tmp_path / "ckg.json"),
        OrganicLoopConfig(
            population=1,
            elite_count=1,
            rust_evaluator=fake_evaluator,
        ),
        candidate_environments=[environment],
    )

    assert captured["environment"] == environment


def test_prepare_ast_for_rust_restricts_to_available_eng_motors():
    from organic_loop import _eng_designations, prepare_ast_for_rust

    ast = simple_ast(motor_index=18)
    ast[2].params["radius"] = 0.01
    ast[2].params["length"] = 0.1
    ast[-2].params["motor_designation"] = "missing-designation"

    prepared = prepare_ast_for_rust(ast)
    body = next(node for node in prepared if node.node_type == "BODY_TUBE")
    motor = next(node for node in prepared if node.node_type == "MOTOR_MOUNT")
    designation = motor.params["motor_designation"]
    motor_diameter = MOTOR_DATABASE[motor.params["motor_index"]][2]
    motor_length = MOTOR_DATABASE[motor.params["motor_index"]][3]

    assert designation in _eng_designations()
    assert body.params["radius"] >= motor_diameter / 2.0 + 0.001 + body.params["thickness"] + 0.002
    assert body.params["length"] >= motor_length + 0.02


def test_normalize_ast_sets_multistage_openrocket_timing_contract():
    # Regression: normalize_stage_ignition_events used to write "burnout"
    # for every non-bottom stage's main motor, on the mistaken assumption
    # that OpenRocket only auto-ignites the bottom stage. Confirmed via a
    # direct l2_engine ast_trace that a "burnout"-tagged upper-stage main
    # motor never ignites at all (self-referential ignition_delay in
    # mission_adapter.rs) -- "automatic" is correct for every stage.
    from organic_loop import normalize_ast
    from rocket_ast import ASTCompiler

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("MOTOR_MOUNT", motor_index=18, ignition="ignitiondelay", ignition_delay=12.0),
        ASTNode("CLOSE_BODY"),
        ASTNode("STAGE", name="Booster"),
        ASTNode("BODY_TUBE", length=0.8, radius=0.06),
        ASTNode("MOTOR_MOUNT", motor_index=22, ignition="burnout"),
        ASTNode("CLOSE_BODY"),
    ]

    normalized = normalize_ast(ast)
    motors = [node for node in normalized if node.node_type == "MOTOR_MOUNT"]

    assert motors[0].params["ignition"] == "automatic"
    assert motors[1].params["ignition"] == "automatic"
    assert "ignition_delay" not in motors[0].params

    xml = ASTCompiler().compile(normalized)

    assert "<ignitionevent>ignitiondelay</ignitionevent>" not in xml
    assert "<ignitionevent>burnout</ignitionevent>" not in xml
    assert "<ignitionevent>automatic</ignitionevent>" in xml
    assert "<separationevent>burnout</separationevent>" in xml
    assert "<windturbulence>0.0</windturbulence>" in xml


def test_normalize_ast_sets_automatic_ignition_for_every_stage_in_four_stage_stack():
    from organic_loop import normalize_ast

    ast = []
    for index in range(4):
        ast.extend(
            [
                ASTNode("STAGE", name=f"Stage {index}"),
                ASTNode("BODY_TUBE", length=1.0, radius=0.05),
                ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
                ASTNode("MOTOR_MOUNT", motor_index=18, ignition="automatic"),
                ASTNode("CLOSE_BODY"),
            ]
        )

    normalized = normalize_ast(ast)
    ignitions = [
        node.params["ignition"]
        for node in normalized
        if node.node_type == "MOTOR_MOUNT"
    ]

    assert ignitions == ["automatic", "automatic", "automatic", "automatic"]


def test_normalize_ast_preserves_retro_motor_ignition_and_delay():
    # Regression: normalize_stage_ignition_events used to overwrite
    # ignition on EVERY MOTOR_MOUNT (role-blind) from stage position alone
    # and unconditionally delete ignition_delay. For a bottom-stage retro
    # (landing/braking) motor this silently turned "ignite near touchdown,
    # tuned by ignition_delay" into "ignite automatic" -- i.e. at launch,
    # simultaneously with the main ascent motor -- and threw away the
    # landing-burn timing the GA was supposed to be searching over.
    # Confirmed live via a real generated octaweb candidate before this fix.
    from organic_loop import normalize_ast

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("MOTOR_MOUNT", role="main", motor_index=18, ignition="burnout"),
        ASTNode("MOTOR_MOUNT", role="retro", motor_index=5, ignition="burnout", ignition_delay=17.5),
        ASTNode("CLOSE_BODY"),
        ASTNode("STAGE", name="Booster"),
        ASTNode("BODY_TUBE", length=0.8, radius=0.06),
        ASTNode("MOTOR_MOUNT", role="main", motor_index=22, ignition="automatic"),
        ASTNode("MOTOR_MOUNT", role="retro", motor_index=6, ignition="burnout", ignition_delay=8.25),
        ASTNode("CLOSE_BODY"),
    ]

    normalized = normalize_ast(ast)
    retro_mounts = [n for n in normalized if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "retro"]
    main_mounts = [n for n in normalized if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "main"]

    assert len(retro_mounts) == 2
    # Retro ignition/delay must survive untouched regardless of which
    # stage (bottom or not) the retro motor belongs to.
    for retro in retro_mounts:
        assert retro.params["ignition"] == "burnout"
    assert retro_mounts[0].params["ignition_delay"] == 17.5
    assert retro_mounts[1].params["ignition_delay"] == 8.25

    # Main motors are always "automatic" regardless of stage position (see
    # test_normalize_ast_sets_multistage_openrocket_timing_contract for why
    # "burnout" on an upper stage's own main motor is wrong, not just
    # differently correct).
    assert main_mounts[0].params["ignition"] == "automatic"
    assert main_mounts[1].params["ignition"] == "automatic"


def test_repair_height_violation_trims_longest_body_tube_below_limit():
    from organic_loop import repair_height_violation

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("NOSE_CONE", length=0.4, shape="ogive"),
        ASTNode("BODY_TUBE", length=1.5, radius=0.05),
        ASTNode("BODY_TUBE", length=2.11005, radius=0.06),
        ASTNode("CLOSE_BODY"),
    ]

    reason = "constraint_violation:max_height_m 4.000005 > 4.000000"
    repaired = repair_height_violation(ast, reason, safety_margin_m=0.03)

    bodies = [n for n in repaired if n.node_type == "BODY_TUBE"]
    # The longest BODY_TUBE (2.11005) must absorb the trim, not the shorter
    # one or the nose -- overshoot (0.000005) + safety margin (0.03).
    assert bodies[0].params["length"] == pytest.approx(1.5)
    assert bodies[1].params["length"] == pytest.approx(2.11005 - 0.000005 - 0.03)


def test_repair_height_violation_falls_back_to_nose_cone_with_no_body_tubes():
    from organic_loop import repair_height_violation

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("NOSE_CONE", length=0.5, shape="ogive"),
        ASTNode("CLOSE_BODY"),
    ]

    reason = "constraint_violation:max_height_m 4.05 > 4.00"
    repaired = repair_height_violation(ast, reason, safety_margin_m=0.03)

    nose = next(n for n in repaired if n.node_type == "NOSE_CONE")
    assert nose.params["length"] == pytest.approx(0.5 - 0.08)


def test_repair_height_violation_respects_minimum_body_tube_length():
    from organic_loop import repair_height_violation

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("BODY_TUBE", length=0.2, radius=0.05),
        ASTNode("CLOSE_BODY"),
    ]

    # Wildly oversized violation -- reduction_needed far exceeds the tube's
    # own length; must clamp to the minimum, not go zero/negative.
    reason = "constraint_violation:max_height_m 9.0 > 4.00"
    repaired = repair_height_violation(ast, reason, safety_margin_m=0.03)

    body = next(n for n in repaired if n.node_type == "BODY_TUBE")
    assert body.params["length"] == pytest.approx(0.15)


def test_repair_height_violation_is_noop_for_other_reasons():
    from organic_loop import repair_height_violation

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("CLOSE_BODY"),
    ]

    for reason in (
        None,
        "",
        "constraint_violation:min_static_margin 1.499999 < 1.500000",
        "simulation_diverged",
    ):
        repaired = repair_height_violation(ast, reason, safety_margin_m=0.03)
        body = next(n for n in repaired if n.node_type == "BODY_TUBE")
        assert body.params["length"] == pytest.approx(1.0)


def test_precision_payload_preserves_forward_stage_topology_when_payload_exists():
    from organic_loop import insert_precision_payload

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("PAYLOAD", mass=1.25),
        ASTNode("MOTOR_MOUNT", motor_index=18, ignition="burnout"),
        ASTNode("CLOSE_BODY"),
        ASTNode("STAGE", name="Booster"),
        ASTNode("BODY_TUBE", length=0.8, radius=0.06),
        ASTNode("MOTOR_MOUNT", motor_index=22),
        ASTNode("CLOSE_BODY"),
    ]

    polished = insert_precision_payload(ast, 2.5)
    payloads = [node for node in polished if node.node_type == "PAYLOAD"]
    first_stage_end = next(idx for idx, node in enumerate(polished) if node.node_type == "CLOSE_BODY")

    assert len(payloads) == 1
    assert payloads[0].params["mass"] == 3.75
    assert any(
        idx < first_stage_end and node.node_type == "PAYLOAD" and node.params["mass"] == 3.75
        for idx, node in enumerate(polished)
    )


def test_openrocket_authority_run_uses_reproducible_seed(monkeypatch):
    from organic_loop import run_openrocket_simulation
    import osifog_sweep

    wind_seeds = []
    monkeypatch.setattr(
        osifog_sweep,
        "_seed_multilevel_wind",
        lambda options, seed: wind_seeds.append((options, seed)),
    )

    class Options:
        seed = None

        def setRandomSeed(self, seed):
            self.seed = seed

    class Simulation:
        def __init__(self):
            self.options = Options()
            self.simulated = False

        def getOptions(self):
            return self.options

        def simulate(self):
            self.simulated = True

    simulation = Simulation()
    run_openrocket_simulation(simulation)

    assert simulation.options.seed == 16000
    assert wind_seeds == [(simulation.options, 16000)]
    assert simulation.simulated


def test_openrocket_validation_rejects_aborted_partial_flight(monkeypatch, tmp_path):
    import organic_loop
    import osifog_sweep

    class Status:
        def name(self):
            return "ABORTED"

    class EventType:
        def name(self):
            return "SIM_ABORT"

    class Event:
        def getType(self):
            return EventType()

        def getData(self):
            return "Stage began to tumble under thrust"

    class Branch:
        def getEvents(self):
            return [Event()]

    class Data:
        def getBranchCount(self):
            return 1

        def getBranch(self, _index):
            return Branch()

        def getMaxAltitude(self):
            return 930.0

        def getMaxMachNumber(self):
            return 0.8

        def getFlightTime(self):
            return 3.6

    class Options:
        def setRandomSeed(self, _seed):
            pass

    class Simulation:
        def getOptions(self):
            return Options()

        def simulate(self):
            pass

        def getStatus(self):
            return Status()

        def getSimulatedData(self):
            return Data()

    class Simulations:
        def get(self, _index):
            return Simulation()

    class Document:
        def getSimulations(self):
            return Simulations()

    class Helper:
        def load_doc(self, _path):
            return Document()

    monkeypatch.setattr(
        organic_loop, "openrocket_static_margins", lambda _doc, _machs: {"phase0_M0.3": 2.0}
    )
    monkeypatch.setattr(
        organic_loop,
        "_extract_warning_summary",
        lambda _source: {"critical": [], "normal": [], "info": []},
    )
    monkeypatch.setattr(
        osifog_sweep,
        "_seed_multilevel_wind",
        lambda _options, _seed: None,
    )

    metrics = organic_loop.validate_openrocket_ork(
        tmp_path / "aborted.ork", Helper(), [0.3]
    )

    assert metrics["status"] == "failed"
    assert metrics["reason"] == "openrocket_simulation_aborted"
    assert metrics["simulation_status"] == "ABORTED"
    assert metrics["abort_reasons"] == ["Stage began to tumble under thrust"]
    assert metrics["apogee_m"] == 930.0


def test_generation_checkpoints_defer_openrocket_validation(monkeypatch, tmp_path):
    import organic_loop
    from organic_loop import OrganicCandidate, OrganicLoopConfig, export_elites

    calls = []
    monkeypatch.setattr(
        organic_loop,
        "validate_openrocket_ork",
        lambda *_args, **_kwargs: calls.append("validated")
        or {
            "status": "success",
            "apogee_m": 1000.0,
            "mach": 1.0,
            "critical_warning_count": 0,
            "min_static_margin": 2.0,
        },
    )
    candidate = OrganicCandidate(
        ast=simple_ast(),
        score=1.0,
        raw_score=1.0,
        status="success",
        reason="ok",
    )
    config = OrganicLoopConfig(
        output_dir=tmp_path,
        evaluator="rust",
        validate_openrocket=1,
        or_helper=object(),
        constraints={},
        phase_machs=[0.3],
    )

    export_elites([candidate], config, validate_openrocket=False)
    assert calls == []

    export_elites([candidate], config, validate_openrocket=True)
    assert calls == ["validated"]


def test_openrocket_viability_uses_authority_constraints():
    from organic_loop import openrocket_metrics_are_viable

    valid = {
        "status": "success",
        "apogee_m": 17000.0,
        "mach": 2.2,
        "warning_count": 0,
        "critical_warning_count": 0,
        "min_static_margin": 2.0,
    }
    constraints = {"max_mach": 3.0, "min_static_margin": 1.5}

    assert openrocket_metrics_are_viable(valid, constraints, target_apogee_m=16000.0)
    assert not openrocket_metrics_are_viable(
        {**valid, "apogee_m": 15999.0}, constraints, target_apogee_m=16000.0
    )
    assert not openrocket_metrics_are_viable({**valid, "mach": 3.01}, constraints)
    assert openrocket_metrics_are_viable({**valid, "warning_count": 1}, constraints)
    assert not openrocket_metrics_are_viable({**valid, "critical_warning_count": 1}, constraints)
    assert not openrocket_metrics_are_viable(
        {**valid, "min_static_margin": 1.49}, constraints
    )


def test_warning_extraction_reads_flight_data_warning_set():
    from organic_loop import _extract_warning_summary

    class WarningSet:
        def getCriticalWarnings(self):
            return ["critical"]

        def getNormalWarnings(self):
            return ["normal"]

        def getInformationalWarnings(self):
            return ["info"]

    class FlightData:
        def getWarningSet(self):
            return WarningSet()

    assert _extract_warning_summary(FlightData()) == {
        "critical": ["critical"],
        "normal": ["normal"],
        "info": ["info"],
    }


def test_normalize_ast_enforces_stage_local_motor_fit_and_recovery_contract():
    from organic_loop import normalize_ast

    top_motor = len(MOTOR_DATABASE) - 1
    booster_motor = max(0, len(MOTOR_DATABASE) - 2)
    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("BODY_TUBE", length=0.1, radius=0.01, thickness=0.002),
        ASTNode("PARACHUTE", deploy="altitude", diameter=0.05),
        ASTNode("MOTOR_MOUNT", motor_index=top_motor, motor_designation="stale"),
        ASTNode("CLOSE_BODY"),
        ASTNode("STAGE", name="Booster"),
        ASTNode("BODY_TUBE", length=0.1, radius=0.01, thickness=0.002),
        ASTNode("MOTOR_MOUNT", motor_index=booster_motor),
        ASTNode("CLOSE_BODY"),
    ]

    normalized = normalize_ast(ast)
    stages = []
    current = []
    for node in normalized:
        if node.node_type == "STAGE" and current:
            stages.append(current)
            current = [node]
        else:
            current.append(node)
    stages.append(current)

    for stage in stages:
        body = next(node for node in stage if node.node_type == "BODY_TUBE")
        motor = next(node for node in stage if node.node_type == "MOTOR_MOUNT")
        motor_diameter = MOTOR_DATABASE[motor.params["motor_index"]][2]
        motor_length = MOTOR_DATABASE[motor.params["motor_index"]][3]
        required_radius = motor_diameter / 2.0 + 0.001 + body.params["thickness"] + 0.002

        assert body.params["radius"] >= required_radius
        assert body.params["length"] >= motor_length + 0.02
        assert motor.params["motor_designation"] == MOTOR_DATABASE[motor.params["motor_index"]][1]

    chutes = [node for node in normalized if node.node_type == "PARACHUTE"]
    assert len(chutes) == 1
    assert chutes[0].params["deploy"] == "apogee"


def test_ast_compiler_sanitizes_problematic_openrocket_contracts():
    from rocket_ast import ASTCompiler

    ast = [
        ASTNode("STAGE", name="Noisy Sustainer"),
        ASTNode("BODY_TUBE", length=0.1, radius=0.01, thickness=0.002),
        ASTNode("PARACHUTE", deploy="altitude", diameter=0.05),
        ASTNode("MOTOR_MOUNT", motor_index=len(MOTOR_DATABASE) - 1, motor_designation="wrong"),
        ASTNode("CLOSE_BODY"),
    ]

    xml = ASTCompiler().compile(ast)

    assert "<deployevent>altitude</deployevent>" not in xml
    assert "<deployevent>apogee</deployevent>" in xml
    assert "<designation>wrong</designation>" not in xml
    assert f"<designation>{MOTOR_DATABASE[-1][1]}</designation>" in xml
    assert "<outerradius>" in xml
    assert "<innertube>" in xml


def test_ast_compiler_emits_stable_unique_component_ids():
    from rocket_ast import ASTCompiler

    component_tags = {
        "stage",
        "nosecone",
        "bodytube",
        "innertube",
        "parachute",
        "masscomponent",
        "freeformfinset",
        "trapezoidfinset",
    }

    first = ET.fromstring(ASTCompiler().compile(simple_ast()))
    second = ET.fromstring(ASTCompiler().compile(simple_ast()))

    def component_ids(root):
        components = [element for element in root.iter() if element.tag in component_tags]
        ids = [element.findtext("id") for element in components]
        assert all(ids)
        assert len(ids) == len(set(ids))
        return ids

    assert component_ids(first) == component_ids(second)
    assert first.find(".//motorconfiguration").attrib["configid"] == second.find(
        ".//motorconfiguration"
    ).attrib["configid"]
    assert first.findtext(".//conditions/configid") == second.findtext(
        ".//conditions/configid"
    )


def test_precision_mission_motor_curve_is_pinned_by_digest():
    from rocket_ast import ASTCompiler

    n2000_index = next(
        index for index, motor in enumerate(MOTOR_DATABASE) if motor[1] == "N2000W"
    )
    xml = ASTCompiler().compile(simple_ast(motor_index=n2000_index))

    digest = MOTOR_DATABASE[n2000_index][5]
    assert digest
    assert f"<digest>{digest}</digest>" in xml


def test_every_motor_in_database_has_a_digest():
    # Regression: 12 of MOTOR_DATABASE's 38 motors (K510, L1000, L1150,
    # L1500T, L2200G, M1939W, M650W, M1297W, 9977M2245-P, N4800T,
    # 20146N5800-P, 40960O8000-P) had digest=None -- confirmed as a real,
    # user-reported OpenRocket load warning ("Multiple motors with
    # designation 'L1150' for manufacturer 'AeroTech' found, one chosen
    # arbitrarily") for the ambiguous ones, and (worse, not directly
    # reported but found while investigating) L1000/L1150's designation
    # strings don't match ANY real OpenRocket catalog entry at all --
    # without a digest, findMotors' description-based fallback path could
    # plausibly resolve to nothing or something L2/Rust never scored.
    # Every entry now has a real digest, queried directly against the
    # OpenRocket 24.12 JVM (Application.getMotorSetDatabase()) and verified
    # end-to-end to load with zero ambiguity/missing-motor warnings.
    missing = [motor[1] for motor in MOTOR_DATABASE if not motor[5]]
    assert not missing, f"motors missing a digest: {missing}"


def test_ast_compiler_does_not_clip_high_power_precision_payload():
    from rocket_ast import ASTCompiler

    ast = simple_ast()
    ast.insert(-2, ASTNode("PAYLOAD", mass=12.5))

    xml = ASTCompiler().compile(ast)

    assert "<mass>12.500000</mass>" in xml


def test_ast_compiler_emits_ballast_masscomponent_and_survives_sanitize():
    from rocket_ast import ASTCompiler, sanitize_ast_for_openrocket

    ast = simple_ast()
    ast.insert(-2, ASTNode("BALLAST", mass=0.2, position="aft"))

    sanitized = sanitize_ast_for_openrocket(ast)
    assert any(node.node_type == "BALLAST" for node in sanitized)

    xml = ASTCompiler().compile(ast)

    assert "<name>Evolved Ballast</name>" in xml
    assert "<mass>0.200000</mass>" in xml
    assert '<position type="bottom">-0.05</position>' in xml


def test_ast_compiler_ballast_absolute_offset_overrides_position_keyword():
    from rocket_ast import ASTCompiler

    ast = simple_ast()
    ast.insert(-2, ASTNode("BALLAST", mass=0.1, position="aft", axial_offset_m=0.37))

    xml = ASTCompiler().compile(ast)

    assert '<position type="absolute">0.370000</position>' in xml


def test_random_ast_generation_preserves_physical_openrocket_contracts():
    import random
    from rocket_ast import create_random_ast

    random.seed(20260719)
    for _ in range(50):
        ast = create_random_ast()
        stages = []
        current = []
        for node in ast:
            if node.node_type == "STAGE" and current:
                stages.append(current)
                current = [node]
            else:
                current.append(node)
        stages.append(current)

        assert stages
        assert any(node.node_type == "NOSE_CONE" for node in stages[0])
        assert any(node.node_type == "PARACHUTE" for node in stages[0])

        for idx, stage in enumerate(stages):
            body = next(node for node in stage if node.node_type == "BODY_TUBE")
            motor = next(node for node in stage if node.node_type == "MOTOR_MOUNT")
            fins = [node for node in stage if node.node_type == "FIN_SET"]
            chutes = [node for node in stage if node.node_type == "PARACHUTE"]
            motor_diameter = MOTOR_DATABASE[motor.params["motor_index"]][2]
            motor_length = MOTOR_DATABASE[motor.params["motor_index"]][3]
            required_radius = motor_diameter / 2.0 + 0.001 + body.params["thickness"] + 0.002

            assert body.params["radius"] >= required_radius
            assert body.params["length"] >= motor_length + 0.02
            assert motor.params["motor_designation"] == MOTOR_DATABASE[motor.params["motor_index"]][1]
            assert fins
            assert all(fin.params["root"] >= body.params["radius"] * 1.2 for fin in fins)
            assert all(fin.params["height"] >= body.params["radius"] * 0.7 for fin in fins)
            assert all(chute.params["deploy"] == "apogee" for chute in chutes)
            if idx > 0:
                assert not chutes


def test_ast_compiler_emits_fin_cross_section_contract():
    from rocket_ast import ASTCompiler

    default_xml = ASTCompiler().compile(simple_ast())
    rounded_xml = ASTCompiler().compile(
        [
            ASTNode("STAGE", name="Rounded Fin Test"),
            ASTNode("BODY_TUBE", length=1.0, radius=0.04),
            ASTNode("FIN_SET", count=4, sweep=30.0, root=0.12, height=0.06, cross_section="rounded"),
            ASTNode("MOTOR_MOUNT", motor_index=12, ignition="automatic"),
            ASTNode("CLOSE_BODY"),
        ]
    )

    assert "<crosssection>airfoil</crosssection>" in default_xml
    assert "<crosssection>rounded</crosssection>" in rounded_xml


def test_ast_compiler_maps_double_wedge_cross_section_to_square_for_openrocket():
    # Regression: OpenRocket's real FinSet.CrossSection enum only has
    # SQUARE/ROUNDED/AIRFOIL (confirmed by reading openrocket/core/src/main/
    # java/.../FinSet.java directly) -- "double-wedge" (one of rocket_forge.
    # FIN_CROSS_SECTIONS' 4 legal choices, and a real richer drag category
    # Rust's own barrowman.rs computes) can never match any OpenRocket enum
    # constant. Writing it literally into <crosssection> produced a genuine
    # Warning.FILE_INVALID_PARAMETER on load (verified against the real
    # OpenRocket 24.12 JVM: "Parametro invalido encontrado, ignorando"),
    # confirmed as a real user-reported OpenRocket load warning. Rust's own
    # test (barrowman.rs::"OpenRocket loads organic double-wedge fins as its
    # square cross-section") already documents that OpenRocket collapses
    # double-wedge to square -- so the compiler now writes that explicitly
    # instead of relying on undocumented silent-fallback parsing.
    from rocket_ast import ASTCompiler

    xml = ASTCompiler().compile(
        [
            ASTNode("STAGE", name="Double Wedge Test"),
            ASTNode("BODY_TUBE", length=1.0, radius=0.04),
            ASTNode("FIN_SET", count=4, sweep=30.0, root=0.12, height=0.06, cross_section="double-wedge"),
            ASTNode("MOTOR_MOUNT", motor_index=12, ignition="automatic"),
            ASTNode("CLOSE_BODY"),
        ]
    )
    assert "<crosssection>square</crosssection>" in xml
    assert "double-wedge" not in xml


def test_organic_loop_repeated_rust_failures_never_block_evaluation(tmp_path):
    """Regression test for a real, empirically-confirmed bug: the CKG's
    acceptance-multiplier prefilter used to hard-reject a candidate (without
    ever running it through Rust) once cumulative failures on its shared
    subgraph features crossed a threshold. In a hard mission with a low
    baseline legality rate, most candidates share low-level features (a
    given motor designation, a given fin count) with the many *unrelated*
    failures that are simply normal at that rate -- a fresh 24-population/
    6-generation OSIFOG run collapsed to zero real Rust evaluations by
    generation 3 purely from this, independent of whether new mutations were
    actually better. Every candidate must always reach real evaluation; the
    CKG may only apply a soft score multiplier (floor 0.05), never a hard
    veto."""
    from organic_loop import OrganicLoopConfig, RustEvaluationResult, run_generation

    calls = []

    def always_fail(candidates, _target_apogee_m, _physics_mode, _objectives, _constraints, _calibrations):
        calls.append(len(candidates))
        return [
            RustEvaluationResult(
                id=candidate["id"],
                status="failed",
                score=-100.0,
                apogee_m=0.0,
                mach=0.0,
                min_static_margin=0.0,
                margins=[],
                reason="synthetic structural failure",
            )
            for candidate in candidates
        ]

    config = OrganicLoopConfig(
        population=3,
        elite_count=1,
        seed=91,
        output_dir=tmp_path / "first",
        ckg_path=tmp_path / "ckg.json",
        evaluator="rust",
        rust_evaluator=always_fail,
    )
    run_generation(config)

    last = None
    for idx in range(18):
        config.output_dir = tmp_path / f"repeat_{idx}"
        last = run_generation(config)
        assert last.elites[0].reason != "ckg_prefilter"

    # Every one of the 19 generations must have reached the evaluator with
    # its full population -- none silently skipped by a CKG veto.
    assert len(calls) == 19
    assert all(count == 3 for count in calls)
    assert last.elites[0].status == "failed"
    assert last.elites[0].reason == "synthetic structural failure"


def test_organic_loop_cli_heuristic_smoke(tmp_path):
    import organic_loop
    script = str(Path(organic_loop.__file__).resolve())
    completed = subprocess.run(
        [
            sys.executable,
            script,
            "--evaluator",
            "heuristic",
            "--population",
            "4",
            "--elite-count",
            "2",
            "--generations",
            "1",
            "--out",
            str(tmp_path / "out"),
            "--ckg",
            str(tmp_path / "ckg.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "wrote 2 elites" in completed.stdout


def test_organic_loop_cli_heuristic_mission_smoke(tmp_path):
    import organic_loop
    script = str(Path(organic_loop.__file__).resolve())
    mission_path = tmp_path / "mission.json"
    mission_path.write_text(
        json.dumps(
            {
                "name": "Tiny precision smoke",
                "objectives": [{"metric": "apogee", "kind": "target", "value": 350.0}],
            }
        )
    )

    completed = subprocess.run(
        [
            sys.executable,
            script,
            "--evaluator",
            "heuristic",
            "--mission",
            str(mission_path),
            "--population",
            "4",
            "--elite-count",
            "2",
            "--generations",
            "1",
            "--out",
            str(tmp_path / "out"),
            "--ckg",
            str(tmp_path / "ckg.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads((tmp_path / "out" / "organic_elite.json").read_text())

    assert "wrote 2 elites" in completed.stdout
    assert payload["mission"] == str(mission_path)
    assert payload["target_apogee_m"] == 350.0


def test_precision_350m_mission_encodes_osifog_style_rules():
    mission = json.loads(open("missions/precision_350m.json", encoding="utf-8").read())

    apogee_objectives = [
        objective
        for objective in mission["objectives"]
        if objective.get("metric") == "apogee"
    ]
    kinds = {objective["kind"] for objective in apogee_objectives}

    assert mission["output"].endswith(".ork")
    assert {"target", "atmost", "atleast"}.issubset(kinds)
    assert any(objective.get("value") == 350 for objective in apogee_objectives)
    assert any(objective.get("value") == 350.5 for objective in apogee_objectives)
    assert any(objective.get("value") == 349.5 for objective in apogee_objectives)
    assert mission["constraints"]["min_static_margin"] >= 1.5
    assert mission["sim"]["windaverage"] == 4.0
    assert mission["sim"]["launchrodlength"] >= 2.0


def test_openrocket_warning_summary_is_serializable():
    from organic_loop import _extract_warning_summary, _merge_warning_summaries

    class FakeList:
        def __init__(self, values):
            self.values = values

        def __iter__(self):
            return iter(self.values)

        def iterator(self):
            return iter(self.values)

    class FakeWarnings:
        def getCriticalWarnings(self):
            return FakeList(["bad geometry"])

        def getNormalWarnings(self):
            return FakeList(["large angle of attack"])

        def getInformationalWarnings(self):
            return FakeList(["info only"])

    class FakeSource:
        def getWarnings(self):
            return FakeWarnings()

    summary = _extract_warning_summary(FakeSource())
    merged = _merge_warning_summaries(summary, summary)

    assert summary == {
        "critical": ["bad geometry"],
        "normal": ["large angle of attack"],
        "info": ["info only"],
    }
    assert merged == summary


def test_load_mission_target_apogee_uses_scale_for_maximize(tmp_path):
    from organic_loop import load_mission_target_apogee

    mission_path = tmp_path / "maximize.json"
    mission_path.write_text(
        json.dumps(
            {
                "objectives": [
                    {"metric": "apogee", "kind": "maximize", "scale": 1000000},
                    {"metric": "mach", "kind": "maximize", "scale": 10},
                ]
            }
        )
    )

    assert load_mission_target_apogee(mission_path) == 1000000.0


def test_load_mission_target_apogee_uses_target_or_value(tmp_path):
    from organic_loop import load_mission_target_apogee

    target_path = tmp_path / "target.json"
    target_path.write_text(
        json.dumps({"objectives": [{"metric": "apogee_m", "kind": "target", "target": 350.0}]})
    )
    atleast_path = tmp_path / "atleast.json"
    atleast_path.write_text(
        json.dumps({"objectives": [{"metric": "max_altitude", "kind": "atleast", "value": 100000.0}]})
    )

    assert load_mission_target_apogee(target_path) == 350.0
    assert load_mission_target_apogee(atleast_path) == 100000.0


def test_run_rust_evaluator_batch_defaults_to_openrocket(monkeypatch, tmp_path):
    import organic_loop
    from organic_loop import run_rust_evaluator

    captured = {}

    def fake_run(cmd, cwd, check, capture_output, text):
        batch_path = cmd[-1]
        captured.update(json.loads(open(batch_path, encoding="utf-8").read()))

        class Completed:
            stdout = json.dumps({"results": []})

        return Completed()

    monkeypatch.setattr(organic_loop, "_AST_EVAL_STREAMS", {})
    monkeypatch.setattr(organic_loop.subprocess, "run", fake_run)
    monkeypatch.setattr(organic_loop, "_ensure_ast_eval_binary", lambda *_: None)

    run_rust_evaluator([{"id": "cand-0", "ast": []}], 1234.0)

    assert captured["physics_mode"] == "openrocket"
    assert captured["target_apogee_m"] == 1234.0
    assert captured["constraints"]["target_apogee_m"] == 1234.0


def test_ast_eval_stream_handles_three_batches_in_one_process():
    import organic_loop
    from pathlib import Path

    engine_dir = Path(organic_loop.__file__).resolve().parents[1] / "l2_engine"
    binary_name = "ast_eval.exe" if organic_loop.os.name == "nt" else "ast_eval"
    binary_path = engine_dir / "target" / "release" / binary_name
    if not binary_path.exists():
        subprocess.run(
            ["cargo", "build", "--quiet", "--release", "--bin", "ast_eval"],
            check=True,
            cwd=engine_dir,
        )
    stream = organic_loop._AstEvalStream(binary_path, engine_dir)
    try:
        pid = stream.pid
        request = {
            "target_apogee_m": 15000.0,
            "physics_mode": "openrocket",
            "execution_profile": "super-speed",
            "objectives": [],
            "constraints": {},
            "phase_machs": [0.3],
            "candidates": [],
            "calibrations": {},
            "divergence_model": None,
        }
        for _ in range(3):
            assert stream.request(request) == {"results": []}
            assert stream.pid == pid
    finally:
        stream.close()


def test_ast_eval_binary_freshness_tracks_rust_sources(tmp_path):
    import organic_loop
    import os

    engine_dir = tmp_path / "engine"
    source_dir = engine_dir / "src"
    source_dir.mkdir(parents=True)
    binary = engine_dir / "target" / "release" / "ast_eval.exe"
    binary.parent.mkdir(parents=True)
    manifest = engine_dir / "Cargo.toml"
    lockfile = engine_dir / "Cargo.lock"
    source = source_dir / "ast.rs"
    for path in (manifest, lockfile, source, binary):
        path.write_text("fixture", encoding="utf-8")

    old = 1_000_000_000
    fresh = 2_000_000_000
    for path in (manifest, lockfile, source):
        os.utime(path, ns=(old, old))
    os.utime(binary, ns=(fresh, fresh))
    assert not organic_loop._ast_eval_binary_is_stale(engine_dir, binary)

    newer = fresh + 1_000_000_000
    os.utime(source, ns=(newer, newer))
    assert organic_loop._ast_eval_binary_is_stale(engine_dir, binary)
    assert organic_loop._ast_eval_binary_is_stale(engine_dir, binary.with_name("missing.exe"))


def test_organic_loop_cli_rust_smoke_when_cargo_available(tmp_path):
    if shutil.which("cargo") is None:
        import pytest

        pytest.skip("cargo is not available")

    import organic_loop
    from pathlib import Path
    script = str(Path(organic_loop.__file__).resolve())
    completed = subprocess.run(
        [
            sys.executable,
            script,
            "--evaluator",
            "rust",
            "--population",
            "2",
            "--elite-count",
            "1",
            "--generations",
            "1",
            "--seed",
            "20260704",
            "--out",
            str(tmp_path / "out"),
            "--ckg",
            str(tmp_path / "ckg.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads((tmp_path / "out" / "organic_elite.json").read_text())

    assert "wrote 1 elites" in completed.stdout
    assert payload["evaluator"] == "rust"
    assert "rust_apogee_m" in payload["elite"][0]


def test_build_environment_defaults_when_mission_has_no_launch_block():
    from organic_loop import _build_environment
    from rocket_ast import OPENROCKET_SIMULATION_DEFAULTS

    env = _build_environment(None, OPENROCKET_SIMULATION_DEFAULTS)

    assert env == OPENROCKET_SIMULATION_DEFAULTS


def test_build_environment_applies_mission_launch_fields():
    from organic_loop import _build_environment
    from rocket_ast import OPENROCKET_SIMULATION_DEFAULTS

    env = _build_environment(
        {"rod_length_m": 6.0, "azimuth_deg": 288.0, "angle_from_vertical_deg": 5.0},
        OPENROCKET_SIMULATION_DEFAULTS,
    )

    assert env["launch_rod_length_m"] == 6.0
    assert abs(env["launch_rod_direction_rad"] - math.radians(288.0)) < 1e-9
    assert abs(env["launch_rod_angle_rad"] - math.radians(5.0)) < 1e-9
    # Unspecified fields fall back to the defaults, not to zero.
    assert env["wind_speed_mps"] == OPENROCKET_SIMULATION_DEFAULTS["wind_speed_mps"]


def test_build_environment_samples_azimuth_within_configured_range():
    from organic_loop import _build_environment
    from rocket_ast import OPENROCKET_SIMULATION_DEFAULTS

    low, high = 258.0, 318.0
    for _ in range(30):
        env = _build_environment(
            {"azimuth_deg": 288.0, "azimuth_range_deg": [low, high]},
            OPENROCKET_SIMULATION_DEFAULTS,
        )
        azimuth_deg = math.degrees(env["launch_rod_direction_rad"])
        assert low - 1e-6 <= azimuth_deg <= high + 1e-6


def test_motor_pool_indices_restricts_to_allowed_designations():
    from rocket_ast import MOTOR_DATABASE, motor_pool_indices

    indices = motor_pool_indices(["J800T", "K550W"])

    assert indices
    designations = {MOTOR_DATABASE[i][1] for i in indices}
    assert designations == {"J800T", "K550W"}


def test_motor_pool_indices_none_when_unrestricted():
    from rocket_ast import motor_pool_indices

    assert motor_pool_indices(None) is None
    assert motor_pool_indices([]) is None


def test_motor_pool_indices_raises_on_unknown_designation():
    from rocket_ast import motor_pool_indices

    with pytest.raises(ValueError):
        motor_pool_indices(["NOT_A_REAL_MOTOR"])


def test_create_random_ast_assigns_varied_fin_materials():
    # Regression: FIN_SET was never given a material at creation (only
    # _sanitize_fin's own validation existed, which only matters if
    # something upstream sets one) and ASTNode.mutate() never touched fin
    # material either -- every fin was permanently stuck at
    # _sanitize_fin's "fiberglass" fallback for the life of every
    # candidate. Same per-part-material-freedom gap as motor mounts/rings
    # (already fixed earlier), extended here to fins per the user's
    # explicit direction that material choice should be free per part.
    import random

    from rocket_ast import MATERIALS, create_random_ast

    seen = set()
    for seed in range(60):
        random.seed(seed)
        ast = create_random_ast(min_stages=1, max_stages=2)
        for node in ast:
            if node.node_type == "FIN_SET":
                material = node.params.get("material")
                assert material in MATERIALS
                seen.add(material)
    assert len(seen) > 1


def test_fin_mutate_varies_material_and_thickness():
    import random

    from rocket_ast import ASTNode, MATERIALS

    seen_materials = set()
    seen_thicknesses = set()
    node = ASTNode("FIN_SET", count=4, sweep=30.0, root=0.12, height=0.08, material="fiberglass", thickness=0.003)
    for seed in range(200):
        random.seed(seed)
        node.mutate(rate=0.9)
        assert node.params["material"] in MATERIALS
        seen_materials.add(node.params["material"])
        seen_thicknesses.add(round(node.params["thickness"], 6))

    assert len(seen_materials) > 1
    assert len(seen_thicknesses) > 1


def test_body_tube_mutate_varies_thickness():
    # Regression: BODY_TUBE's ASTNode.mutate() branch varied length/radius/
    # material but NEVER "thickness" -- and create_random_ast never set an
    # explicit thickness at creation either (before this fix) -- so every
    # candidate this pipeline has ever generated silently fell through to
    # _sanitize_body's fixed 0.002m (2mm) default, with no way for the GA
    # to trade wall thickness for mass. Confirmed as a real user-reported
    # gap: "make the tube thinner... the algorithm must be able to tune
    # this" -- same dead-parameter bug class as the fin-material fix from a
    # prior session (test_fin_mutate_varies_material_and_thickness above).
    import random

    from rocket_ast import ASTNode

    seen_thicknesses = set()
    node = ASTNode("BODY_TUBE", length=1.0, radius=0.05, material="cardboard", thickness=0.002)
    for seed in range(200):
        random.seed(seed)
        node.mutate(rate=0.9)
        assert 0.001 <= node.params["thickness"] <= 0.008
        seen_thicknesses.add(round(node.params["thickness"], 6))

    assert len(seen_thicknesses) > 1


def test_create_random_ast_varies_body_tube_thickness():
    import random

    from rocket_ast import create_random_ast

    seen_thicknesses = set()
    for seed in range(50):
        random.seed(seed)
        ast = create_random_ast(min_stages=1, max_stages=1)
        for node in ast:
            if node.node_type == "BODY_TUBE":
                seen_thicknesses.add(round(node.params["thickness"], 6))

    assert len(seen_thicknesses) > 1


def test_create_random_ast_respects_motor_pool():
    import random
    from rocket_ast import MOTOR_DATABASE, create_random_ast

    random.seed(20260719)
    allowed = ["J800T", "K550W", "K700W", "K1050W", "L1150"]
    for _ in range(25):
        ast = create_random_ast(min_stages=1, max_stages=2, motor_pool=allowed)
        for node in ast:
            if node.node_type == "MOTOR_MOUNT":
                idx = node.params["motor_index"]
                assert MOTOR_DATABASE[idx][1] in allowed


def _two_stage_ast(sustainer_motor_index, booster_motor_index):
    return [
        ASTNode("STAGE", name="Evolved Sustainer"),
        ASTNode("NOSE_CONE", shape="ogive", length=0.3, material="cardboard"),
        ASTNode("BODY_TUBE", length=0.8, radius=0.05, material="cardboard"),
        ASTNode("FIN_SET", count=4, sweep=20.0, root=0.12, height=0.08),
        ASTNode("MOTOR_MOUNT", motor_index=sustainer_motor_index, ignition="automatic"),
        ASTNode("CLOSE_BODY"),
        ASTNode("STAGE", name="Evolved Booster 1"),
        ASTNode("BODY_TUBE", length=0.9, radius=0.05, material="cardboard"),
        ASTNode("FIN_SET", count=4, sweep=20.0, root=0.14, height=0.10),
        ASTNode("MOTOR_MOUNT", motor_index=booster_motor_index, ignition="automatic"),
        ASTNode("CLOSE_BODY"),
    ]


def test_crossover_ast_combines_stages_from_both_parents():
    # Regression: run_generation's reproduction loop had NO crossover at
    # all -- every child came from mutating a single random parent, with
    # no mechanism to combine traits from two different individuals.
    # Confirmed as the actual cause of a live campaign's population
    # freezing at an unchanged min_thrust_to_weight value for 100+
    # generations even after removing the constraints that previously
    # bound first: if "good sustainer" and "good booster" existed in
    # different lineages, pure mutation could never combine them into one
    # individual. This test builds two parents with DIFFERENT motors per
    # stage and confirms crossover actually produces children mixing
    # stages from both parents, not just cloning one parent wholesale.
    import random

    from organic_loop import crossover_ast
    from rocket_ast import MOTOR_DATABASE, _split_stages

    idx_a_sustainer = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "H180W")
    idx_a_booster = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "J350W")
    idx_b_sustainer = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "I161W")
    idx_b_booster = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "K550W")

    parent_a = _two_stage_ast(idx_a_sustainer, idx_a_booster)
    parent_b = _two_stage_ast(idx_b_sustainer, idx_b_booster)

    seen_combinations = set()
    for seed in range(200):
        random.seed(seed)
        child = crossover_ast(parent_a, parent_b)
        stages = _split_stages(child)
        assert len(stages) == 2, "crossover must preserve a 2-stage structure here"
        motors = []
        for stage in stages:
            mount = next(n for n in stage if n.node_type == "MOTOR_MOUNT")
            motors.append(MOTOR_DATABASE[mount.params["motor_index"]][1])
        seen_combinations.add(tuple(motors))

    # All 4 possible stage-origin combinations should appear across enough
    # trials: (A,A), (A,B), (B,A), (B,B) -- proving real mixing happens,
    # not just picking one parent wholesale every time.
    expected = {
        ("H180W", "J350W"),
        ("H180W", "K550W"),
        ("I161W", "J350W"),
        ("I161W", "K550W"),
    }
    assert seen_combinations == expected, f"expected all 4 combinations, got {seen_combinations}"


def test_blend_node_params_interpolates_continuous_numerics_between_parents():
    # _blend_node_params is the node-level primitive crossover_ast relies on.
    # Ported from osifog_legal_stage_campaign.py::_inherit_stage's 45%
    # numeric-interpolation behavior (the more sophisticated of the two
    # pre-existing crossover implementations found in the codebase). This
    # confirms continuous params actually land strictly between the two
    # parent values on at least some trials (true blending, not just a
    # coin-flip pick of one parent's value), while discrete/id-like keys
    # (motor_index) are never interpolated -- fractional motor indices
    # would be meaningless.
    import random

    from organic_loop import _blend_node_params
    from rocket_ast import ASTNode

    node_a = ASTNode("FIN_SET", count=4, sweep=10.0, root=0.10, height=0.05, motor_index=2)
    node_b = ASTNode("FIN_SET", count=4, sweep=30.0, root=0.20, height=0.15, motor_index=9)

    saw_interpolated_sweep = False
    for seed in range(300):
        random.seed(seed)
        child = _blend_node_params(node_a, node_b)
        assert child.params["motor_index"] in (2, 9), "discrete key must never be interpolated"
        sweep = child.params["sweep"]
        assert 10.0 <= sweep <= 30.0
        if sweep not in (10.0, 30.0):
            saw_interpolated_sweep = True

    assert saw_interpolated_sweep, "expected at least one trial to blend a continuous value strictly between parents"


def test_mutate_ast_respects_motor_pool():
    import random
    from organic_loop import mutate_ast
    from rocket_ast import MOTOR_DATABASE

    random.seed(20260719)
    allowed = ["J800T", "K550W"]
    ast = simple_ast(motor_index=next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "J800T"))
    for _ in range(25):
        ast = mutate_ast(ast, rate=0.9, motor_pool=allowed)
        for node in ast:
            if node.node_type == "MOTOR_MOUNT":
                idx = node.params["motor_index"]
                assert MOTOR_DATABASE[idx][1] in allowed


def test_resolve_stage_range_uses_topology_stage_count_when_present():
    from organic_loop import _resolve_stage_range

    mission_data = {
        "constraints": {"min_stages": 2, "max_stages": 3},
        "topology": {"stage_count": 2},
    }
    assert _resolve_stage_range(mission_data) == (2, 2)


def test_resolve_stage_range_falls_back_to_constraints_without_topology():
    from organic_loop import _resolve_stage_range

    mission_data = {"constraints": {"min_stages": 1, "max_stages": 2}}
    assert _resolve_stage_range(mission_data) == (1, 2)


def test_resolve_stage_range_defaults_with_no_mission_data():
    from organic_loop import _resolve_stage_range

    assert _resolve_stage_range({}) == (1, 2)


def test_resolve_stage_range_topology_overrides_a_wider_constraint_range():
    from organic_loop import _resolve_stage_range

    # topology.stage_count is design intent, not just another legality
    # bound -- it must win even when constraints would technically allow
    # more stages than the mission actually wants built.
    mission_data = {
        "constraints": {"min_stages": 1, "max_stages": 4},
        "topology": {"stage_count": 3},
    }
    assert _resolve_stage_range(mission_data) == (3, 3)


def test_structural_mutation_retro_motor_branch_does_not_crash():
    # Regression: _structural_mutation's RETRO_MOTOR branch called
    # _select_motor_index without it ever being imported into organic_loop.py
    # (only the unrelated _motor_index was imported) -- a real, live
    # NameError any time this branch fired with a retro_motor_pool set, with
    # no try/except around the mutate_ast call site in run_generation's
    # reproduction loop. Confirmed as the likely cause of the v7 campaign's
    # watchdog-tracked restarts (restart_count: 2) before this fix.
    import random

    from organic_loop import _structural_mutation
    from rocket_ast import MOTOR_DATABASE

    retro_motor_pool = ["F50T", "F67W", "G71R", "G104T"]
    fired = False
    for seed in range(200):
        random.seed(seed)
        ast = simple_ast()
        _structural_mutation(ast, retro_motor_pool=retro_motor_pool)
        retro_mounts = [
            n for n in ast
            if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "retro"
        ]
        if retro_mounts:
            fired = True
            idx = retro_mounts[0].params["motor_index"]
            assert MOTOR_DATABASE[idx][1] in retro_motor_pool
            break

    assert fired, "RETRO_MOTOR structural mutation never fired across 200 seeds"


def test_sanitize_deduplicates_fin_sets_already_present_in_stale_ast():
    # Regression: _structural_mutation's guard (see the next test) only
    # stops NEW duplicate fin sets from being ADDED going forward -- it does
    # nothing for an AST that already accumulated duplicates before that fix
    # existed, which mutation/crossover then carries forward unchanged
    # (nothing else ever drops an existing node). Confirmed as a real,
    # live-campaign case: a candidate re-exported immediately after the
    # mutation-guard fix went live STILL had 3 overlapping "Evolved Fins" on
    # one stage, all inherited from before the fix. sanitize_ast_for_
    # openrocket runs on every candidate every generation
    # (prepare_ast_for_rust), so it must actively de-duplicate, not just
    # rely on the mutation guard.
    from rocket_ast import ASTNode, sanitize_ast_for_openrocket

    ast = [
        ASTNode("STAGE", name="Duplicated Fins"),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06, material="kraft"),
        ASTNode("FIN_SET", count=5, root=0.20, height=0.30, material="lead"),
        ASTNode("FIN_SET", count=6, root=0.25, height=0.35, material="birch"),
        ASTNode("FIN_SET", role="forward_flap", count=4, root=0.05, height=0.05),
        ASTNode("FIN_SET", role="forward_flap", count=3, root=0.04, height=0.04),
        ASTNode("MOTOR_MOUNT", motor_index=12, ignition="automatic"),
        ASTNode("CLOSE_BODY"),
    ]

    sanitized = sanitize_ast_for_openrocket(ast)
    fin_nodes = [n for n in sanitized if n.node_type == "FIN_SET"]
    main_fins = [n for n in fin_nodes if n.params.get("role") != "forward_flap"]
    forward_flaps = [n for n in fin_nodes if n.params.get("role") == "forward_flap"]

    assert len(main_fins) == 1, f"expected exactly 1 main fin set, got {len(main_fins)}"
    assert len(forward_flaps) == 1, f"expected exactly 1 forward flap, got {len(forward_flaps)}"


def test_structural_mutation_fin_set_branch_does_not_duplicate_existing_fins():
    # Regression: _structural_mutation's FIN_SET branch, unlike its
    # PARACHUTE/FORWARD_FLAP/RETRO_MOTOR sibling branches (all guarded with
    # `not any(...)`), had NO guard against a main fin set already existing
    # -- and every stage always has exactly one from creation, with no
    # mechanism anywhere that ever removes one. Confirmed as a real,
    # live-campaign bug via a user screenshot: 3 overlapping "Evolved Fins"
    # freeformfinsets stacked at the same axial position on one stage,
    # rendering as self-intersecting geometry in OpenRocket. This fires the
    # branch repeatedly (not just once) to prove it doesn't keep stacking
    # duplicates over many mutation events, matching how a long-running
    # campaign actually accumulates them one mutation at a time.
    import random

    from organic_loop import _structural_mutation

    ast = simple_ast()
    fired = 0
    for seed in range(400):
        random.seed(seed)
        before = len(ast)
        _structural_mutation(ast)
        main_fins = [
            n for n in ast
            if n.node_type == "FIN_SET" and n.params.get("role") != "forward_flap"
        ]
        assert len(main_fins) == 1, (
            f"expected exactly 1 main fin set, got {len(main_fins)} after seed {seed}"
        )
        if len(ast) != before:
            fired += 1

    assert fired > 0, "no structural mutation ever fired across 400 seeds"


def test_sanitize_repairs_stale_octaweb_cage_against_final_body_radius():
    # Regression: a main octaweb mount's radial_offset_m/cluster_scale/
    # main_outer_radius_m/retro_sleeve_outer_radius_m are set ONCE at
    # creation (octaweb_motor_mounts) and were never touched again by any
    # mutation operator or by sanitize_ast_for_openrocket's diameter-
    # continuity widening pass -- confirmed as a real, live-campaign bug via
    # a user screenshot: a candidate that survived many generations via
    # elitism kept cage numbers computed for a body radius far smaller than
    # its CURRENT (widened) BODY_TUBE.radius, stranding the 3 main motors
    # far from the central retro motor instead of mutually tangent. This AST
    # deliberately sets an obviously-stale cage (values appropriate to a
    # tiny body) on a mount whose BODY_TUBE is much larger, simulating
    # exactly that drift, and a retro radial_offset_m the mutation jitter
    # could plausibly have left nonzero.
    from rocket_ast import ASTNode, sanitize_ast_for_openrocket, _tighten_octaweb_cage
    from osifog_sweep import _falcon_cluster_geometry

    body_radius = 0.15
    main_idx, retro_idx = 7, 14  # H180W, J350W
    ast = [
        ASTNode("STAGE", name="Stale Cage"),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=body_radius),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode(
            "MOTOR_MOUNT", role="main", motor_index=main_idx, multiplicity=3,
            radial_offset_m=0.342, radial_angle_deg=0.0,
            instance_angle_step_deg=120.0, cluster_configuration="3-ring",
            cluster_scale=999.0, main_outer_radius_m=0.0155,
            retro_sleeve_outer_radius_m=0.30,
        ),
        ASTNode(
            "MOTOR_MOUNT", role="retro", motor_index=retro_idx, multiplicity=1,
            radial_offset_m=0.021, ignition="burnout", ignition_delay=10.0,
        ),
        ASTNode("CLOSE_BODY"),
    ]

    sanitized = sanitize_ast_for_openrocket(ast)
    main = next(
        n for n in sanitized
        if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "main"
    )
    retro = next(
        n for n in sanitized
        if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "retro"
    )

    expected = _tighten_octaweb_cage(
        _falcon_cluster_geometry(main_idx, retro_idx, body_radius)
    )
    assert main.params["radial_offset_m"] == pytest.approx(expected["center_distance_m"])
    assert main.params["cluster_scale"] == pytest.approx(expected["cluster_scale"])
    # Near-tangent + a real safety margin restored (see
    # _tighten_octaweb_cage's own comment), not the stale 0.342/0.021 gap.
    assert main.params["radial_offset_m"] == pytest.approx(
        main.params["main_outer_radius_m"] + main.params["retro_sleeve_outer_radius_m"] + 0.005
    )
    # Retro re-centered inside the sleeve, not left at its stale/jittered offset.
    assert retro.params["radial_offset_m"] == pytest.approx(0.0, abs=1e-9)


def test_structural_mutation_octaweb_convert_replaces_single_mount_with_cluster():
    import random

    from organic_loop import _structural_mutation
    from rocket_ast import OCTAWEB_BODY_RADIUS_RANGE_M

    mission_data = json.loads(
        open("missions/osifog_l3_precision.json", encoding="utf-8").read()
    )
    motor_pool = mission_data["motor_pool"]["allowed_designations"]
    retro_motor_pool = mission_data["motor_pool"]["retro_allowed_designations"]

    converted = False
    for seed in range(200):
        random.seed(seed)
        ast = simple_ast()
        _structural_mutation(ast, motor_pool=motor_pool, retro_motor_pool=retro_motor_pool)
        mounts = [n for n in ast if n.node_type == "MOTOR_MOUNT"]
        octaweb_mains = [
            n for n in mounts
            if n.params.get("multiplicity") == 3 and n.params.get("cluster_configuration") == "3-ring"
        ]
        if octaweb_mains:
            converted = True
            # CLOSE_BODY must stay last -- the converted mounts belong
            # before it, not after (see the stage_end/CLOSE_BODY handling
            # in _structural_mutation's OCTAWEB_CONVERT branch).
            assert ast[-1].node_type == "CLOSE_BODY"
            body_tube = next(n for n in ast if n.node_type == "BODY_TUBE")
            assert OCTAWEB_BODY_RADIUS_RANGE_M[0] <= body_tube.params["radius"] <= OCTAWEB_BODY_RADIUS_RANGE_M[1]
            retro_mounts = [n for n in mounts if n.params.get("role") == "retro"]
            assert len(retro_mounts) == 1
            # Old plain single mount fully replaced, not left dangling
            # alongside the new cluster.
            assert len(mounts) == 2
            break

    assert converted, "OCTAWEB_CONVERT never fired across 200 seeds -- dead mutation branch"


def test_resolve_octaweb_probability_is_one_when_mission_declares_3ring():
    from organic_loop import _resolve_octaweb_probability

    mission_data = {"topology": {"main_cluster": {"configuration": "3-ring", "count": 3}}}
    assert _resolve_octaweb_probability(mission_data) == 1.0


def test_resolve_octaweb_probability_is_zero_without_topology():
    from organic_loop import _resolve_octaweb_probability

    assert _resolve_octaweb_probability({}) == 0.0
    assert _resolve_octaweb_probability({"topology": {"stage_count": 2}}) == 0.0


def test_osifog_l3_precision_mission_declares_octaweb_intent():
    from organic_loop import _resolve_octaweb_probability

    mission_data = json.loads(
        open("missions/osifog_l3_precision.json", encoding="utf-8").read()
    )
    assert _resolve_octaweb_probability(mission_data) == 1.0


def test_osifog_l3_precision_mission_generates_exactly_two_stages():
    import random

    from organic_loop import _resolve_stage_range
    from rocket_ast import create_random_ast

    mission_data = json.loads(
        open("missions/osifog_l3_precision.json", encoding="utf-8").read()
    )
    min_stages, max_stages = _resolve_stage_range(mission_data)
    assert (min_stages, max_stages) == (2, 2)

    random.seed(20260723)
    motor_pool = mission_data["motor_pool"]["allowed_designations"]
    for _ in range(10):
        ast = create_random_ast(min_stages, max_stages, motor_pool=motor_pool)
        stage_count = sum(1 for node in ast if node.node_type == "STAGE")
        assert stage_count == 2


def test_closest_legal_material_for_density_picks_nearest():
    from rocket_ast import MATERIALS, _closest_legal_material_for_density

    assert _closest_legal_material_for_density(11340.0) == "lead"
    assert _closest_legal_material_for_density(170.0) == "balsa"
    # steel=7850 is MATERIALS' closest entry to 8000
    assert _closest_legal_material_for_density(8000.0) == "steel"
    # every possible pick must itself be within the mission-wide legal range
    for probe in (0.0, 500.0, 5000.0, 50000.0, -100.0):
        picked = _closest_legal_material_for_density(probe)
        assert 170.0 <= MATERIALS[picked][2] <= 11340.0


def _tangent_cage(main_outer_radius_m=0.02025, retro_sleeve_outer_radius_m=0.0315):
    return {
        "main_outer_radius_m": main_outer_radius_m,
        "retro_sleeve_outer_radius_m": retro_sleeve_outer_radius_m,
        "center_distance_m": main_outer_radius_m + retro_sleeve_outer_radius_m,
    }


def test_octaweb_ballast_rods_splits_total_mass_across_three_rods():
    from rocket_ast import MATERIALS, octaweb_ballast_rods

    cage = _tangent_cage()
    node = octaweb_ballast_rods(cage, target_mass_kg=1.5, length_m=0.5, main_angle_deg=10.0)

    assert node.node_type == "BALLAST"
    # min(main_outer, retro_sleeve_outer) = main_outer here, and it clears
    # the neighboring main motors on the first try (no shrinking needed)
    # for this particular cage -- see
    # test_octaweb_ballast_rods_shrinks_to_fit_when_default_size_overlaps
    # for a cage where shrinking is required.
    assert node.params["radius"] == pytest.approx(0.02025)
    assert node.params["length"] == pytest.approx(0.5)
    assert node.params["instance_count"] == 3
    # Tangent to the CENTER (retro) motor -- matches the confirmed 839k
    # reference pattern (radialposition - retro_sleeve_outer_radius ==
    # the ballast's own outerradius there), not the main motors' own ring
    # radius (no room there) and not outside the cluster's envelope
    # (passes through the circumscribing rings' own material there).
    assert node.params["radial_offset_m"] == pytest.approx(
        cage["retro_sleeve_outer_radius_m"] + node.params["radius"]
    )
    # main_angle_deg + 30, not +60 -- OpenRocket's native 3-ring
    # clustering has its own +90deg starting rotation (clusterrotation=0
    # renders the first instance at 90deg, not 0deg), so the true gap
    # midpoint is (main_angle_deg + 90) - 60 = main_angle_deg + 30. See
    # ULTRAREVIEW-octaweb-ballast-radialdirection-units.md.
    assert node.params["angle_offset_deg"] == pytest.approx(40.0)  # main(10) + 30

    # Rust divides `mass` by instance_count -- verify that recovers exactly
    # what OpenRocket will independently compute per rod from the chosen
    # material's real density * this rod's own volume (the whole point of
    # solving density from a fixed radius/length instead of the reverse).
    material = node.params["material"]
    density = MATERIALS[material][2]
    rod_volume = math.pi * node.params["radius"] ** 2 * 0.5
    expected_per_rod_mass = density * rod_volume
    assert node.params["mass"] / 3.0 == pytest.approx(expected_per_rod_mass)


def test_octaweb_ballast_rods_uses_dense_material_for_large_mass_request():
    from rocket_ast import octaweb_ballast_rods

    cage = _tangent_cage()
    # A large mass in a small rod volume needs a dense material -- steel or
    # lead, not balsa/cardboard/aluminum.
    node = octaweb_ballast_rods(cage, target_mass_kg=9.0, length_m=0.3)
    assert node.params["material"] in ("steel", "lead")


def test_octaweb_ballast_rods_returns_none_when_no_real_gap_exists():
    from rocket_ast import octaweb_ballast_rods

    # Retro motor barely bigger than the 2mm clearance floor leaves no
    # meaningful gap for a ballast rod.
    cage = _tangent_cage(main_outer_radius_m=0.02025, retro_sleeve_outer_radius_m=0.0035)
    assert octaweb_ballast_rods(cage, target_mass_kg=1.0, length_m=0.3) is None


def test_octaweb_ballast_rods_shrinks_when_default_size_overlaps_mains():
    from rocket_ast import octaweb_ballast_rods, _ballast_clears_main_motors

    # min(main_outer, retro_sleeve_outer) = retro_sleeve_outer = 0.02 here,
    # but that size does NOT clear the neighboring main motors for this
    # main/retro ratio (verified: needs to shrink to ~0.0144).
    cage = _tangent_cage(main_outer_radius_m=0.08, retro_sleeve_outer_radius_m=0.02)
    assert not _ballast_clears_main_motors(0.02, 0.02 + 0.02, 0.08, cage["center_distance_m"])

    node = octaweb_ballast_rods(cage, target_mass_kg=0.5, length_m=0.3)
    assert node is not None
    assert node.params["radius"] < 0.02
    assert _ballast_clears_main_motors(
        node.params["radius"], node.params["radial_offset_m"], 0.08, cage["center_distance_m"]
    )


def test_sanitize_ballast_clamps_shaped_rod_fields():
    from rocket_ast import ASTNode, _sanitize_ballast

    node = ASTNode(
        "BALLAST", mass=1.0, material="steel",
        radius=10.0, length=100.0, instance_count=99, radial_offset_m=-5.0,
        angle_offset_deg=400.0,
    )
    sanitized = _sanitize_ballast(node)
    assert sanitized.params["radius"] <= 0.3
    assert sanitized.params["length"] <= 2.0
    assert sanitized.params["instance_count"] <= 8
    assert sanitized.params["radial_offset_m"] >= 0.0


def test_sanitize_ballast_leaves_plain_lumped_ballast_untouched():
    from rocket_ast import ASTNode, _sanitize_ballast

    node = ASTNode("BALLAST", mass=0.2, position="aft")
    sanitized = _sanitize_ballast(node)
    assert "radius" not in sanitized.params
    assert "length" not in sanitized.params
    assert "instance_count" not in sanitized.params


def test_compiler_emits_three_solid_innertube_rods_for_shaped_ballast():
    from rocket_ast import ASTCompiler, MATERIALS, octaweb_ballast_rods

    cage = {"main_outer_radius_m": 0.02, "center_distance_m": 0.07, "retro_sleeve_outer_radius_m": 0.05}
    ballast = octaweb_ballast_rods(cage, target_mass_kg=1.2, length_m=0.4)
    assert ballast is not None

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=0.09),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode(
            "MOTOR_MOUNT", role="main", motor_index=18, multiplicity=3,
            radial_offset_m=0.07, instance_angle_step_deg=120.0,
            cluster_configuration="3-ring", cluster_scale=2.5,
            main_outer_radius_m=0.02, retro_sleeve_outer_radius_m=0.05,
        ),
        ballast,
        ASTNode("CLOSE_BODY"),
    ]
    xml = ASTCompiler().compile(ast, name="Ballast rod compile test")

    assert xml.count("<innertube>") >= 3
    assert xml.count("Evolved Ballast Rod") == 3
    assert "<masscomponent>" not in xml or "Evolved Ballast" not in xml.split("<masscomponent>")[1]
    density = MATERIALS[ballast.params["material"]][2]
    assert f'density="{density}"' in xml
    # 3 rods 120 degrees apart -> radialdirection values should differ
    import re
    directions = re.findall(r"<radialdirection>([\-0-9.]+)</radialdirection>", xml)
    assert len(set(directions)) >= 3


def test_compiler_writes_ballast_radialdirection_in_degrees_not_radians():
    """Regression for the root cause found via ultrareview: <radialdirection>
    is DEGREES in OpenRocket's .ork format (confirmed empirically against a
    real OpenRocket JVM and independently against the 839k reference's own
    saved ballast rods), not radians -- a prior version of this code called
    math.radians() before writing it, silently shrinking every nonzero
    angular offset to ~1/57th of its intended value."""
    from rocket_ast import ASTCompiler, ASTNode

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=0.09),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode(
            "MOTOR_MOUNT", role="main", motor_index=18, multiplicity=3,
            radial_offset_m=0.07, instance_angle_step_deg=120.0,
            cluster_configuration="3-ring", cluster_scale=2.5,
            main_outer_radius_m=0.02, retro_sleeve_outer_radius_m=0.05,
        ),
        ASTNode(
            "BALLAST", mass=0.3, material="steel", radius=0.01, length=0.3,
            instance_count=3, radial_offset_m=0.06, angle_offset_deg=60.0,
            position="aft",
        ),
        ASTNode("CLOSE_BODY"),
    ]
    xml = ASTCompiler().compile(ast, name="degrees-not-radians regression")

    import re
    directions = [
        float(d) for d in re.findall(r"<radialdirection>([\-0-9.]+)</radialdirection>", xml)
    ]
    # angle_offset_deg=60, 3 instances 120deg apart -> {60, 180, 300}
    # (plus the main cluster mount's own radialdirection, always 0.0 for a
    # clustered mount). If this were still radians-mislabeled-as-degrees,
    # the ballast values would be {1.047, 3.142, 5.236} instead.
    assert {60.0, 180.0, 300.0}.issubset(set(round(d, 3) for d in directions))


def test_compiler_still_emits_masscomponent_for_plain_ballast():
    from rocket_ast import ASTCompiler, ASTNode

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode("MOTOR_MOUNT", role="main", motor_index=18),
        ASTNode("BALLAST", mass=0.2, position="aft"),
        ASTNode("CLOSE_BODY"),
    ]
    xml = ASTCompiler().compile(ast, name="Plain ballast compile test")
    assert "<masscomponent>" in xml
    assert "Evolved Ballast</name>" in xml


def test_create_random_ast_adds_octaweb_ballast_rods_when_repair_space_requests_it():
    import random

    from rocket_ast import create_random_ast

    repair_space = {"nose_ballast_mass_kg": [2.0]}
    found_ballast = False
    for seed in range(1000, 1150):
        random.seed(seed)
        ast = create_random_ast(
            min_stages=1, max_stages=1,
            motor_pool=["I161W", "I357T", "J350W"],
            retro_motor_pool=["H180W", "K550W"],
            retro_motor_probability=1.0,
            octaweb_probability=1.0,
            repair_space=repair_space,
        )
        mounts = [n for n in ast if n.node_type == "MOTOR_MOUNT"]
        is_octaweb = any(
            m.params.get("cluster_configuration") == "3-ring" for m in mounts
        )
        ballast_nodes = [n for n in ast if n.node_type == "BALLAST"]
        if is_octaweb and ballast_nodes:
            found_ballast = True
            main_mount = next(m for m in mounts if m.params.get("role") == "main")
            retro_mount = next(m for m in mounts if m.params.get("role") == "retro")
            ballast = ballast_nodes[0]
            main_outer = main_mount.params["main_outer_radius_m"]
            retro_sleeve_outer = main_mount.params["retro_sleeve_outer_radius_m"]
            # Bounded by the retro motor's own size, not necessarily equal
            # to the main motors' radius (see octaweb_ballast_rods
            # docstring) -- but never bigger.
            assert ballast.params["radius"] <= main_outer + 1e-9
            # Tangent to the CENTER (retro) motor, matching the confirmed
            # 839k reference pattern -- not the same ring radius as the
            # main motors (no room there) and not pushed outside the
            # cluster's envelope (passes through the circumscribing
            # rings' own material there).
            assert ballast.params["radial_offset_m"] == pytest.approx(
                retro_sleeve_outer + ballast.params["radius"]
            )
            break
    assert found_ballast, "expected at least one octaweb+ballast candidate across 150 seeds"


def test_octaweb_ballast_key_matches_real_mission_repair_space():
    """Regression: `create_random_ast`'s octaweb branch and
    `organic_loop.py`'s OCTAWEB_CONVERT structural mutation both used to read
    `repair_space["octaweb_ballast_mass_kg"]`, a key that never existed in
    any mission file -- the real key (`osifog_precision.py`'s legacy pipeline
    and every mission JSON) is `nose_ballast_mass_kg`. Because the key never
    matched, `octaweb_ballast_rods` silently never ran for the live campaign
    (`osifog_l3_precision.json` has `octaweb_probability=1.0`, so this
    affected essentially the entire population), removing the only
    mechanism for tunable CG-shifting mass in an octaweb candidate --
    plausibly why static margin plateaued just under the 1.5 requirement.
    This test loads the REAL mission file (not a code-matching fixture) so
    a future rename on either side breaks loudly instead of silently."""
    import json
    import random
    from pathlib import Path

    from rocket_ast import create_random_ast

    mission = json.loads(
        Path("missions/osifog_l3_precision.json").read_text(encoding="utf-8")
    )
    repair_space = mission["evolution"]["physical_repair_space"]
    assert "nose_ballast_mass_kg" in repair_space, (
        "mission's ballast-mass key renamed -- update create_random_ast/"
        "organic_loop.py's octaweb ballast lookup to match"
    )

    found_ballast = False
    for seed in range(1000, 1150):
        random.seed(seed)
        ast = create_random_ast(
            min_stages=1, max_stages=1,
            motor_pool=["I161W", "I357T", "J350W"],
            retro_motor_pool=["H180W", "K550W"],
            retro_motor_probability=1.0,
            octaweb_probability=1.0,
            repair_space=repair_space,
        )
        mounts = [n for n in ast if n.node_type == "MOTOR_MOUNT"]
        is_octaweb = any(m.params.get("cluster_configuration") == "3-ring" for m in mounts)
        if is_octaweb and any(n.node_type == "BALLAST" for n in ast):
            found_ballast = True
            break
    assert found_ballast, (
        "expected at least one octaweb+ballast candidate using the mission's "
        "OWN repair_space dict across 150 seeds -- the key lookup is not "
        "wired to the real mission data"
    )


def test_create_random_ast_tightens_body_radius_to_octaweb_cage():
    # Regression: body_radius was drawn ONCE from OCTAWEB_BODY_RADIUS_RANGE_M
    # (0.06-0.20m) independently of whatever motor pair octaweb_motor_mounts
    # ends up choosing, and only ever checked for "does the cage fit inside
    # it", never "is this body actually sized for it". A small motor pair
    # (e.g. H180W main / F50T retro, ~35mm tight-tangent cage) landing
    # inside a body drawn near the range's 200mm ceiling left a large,
    # structurally pointless gap between the motors and the body wall --
    # confirmed as a real, user-reported visual issue via re-exported .ork
    # inspection. create_random_ast now tightens every BODY_TUBE down to
    # whichever octaweb stage's cage needs the most room (+ a real margin),
    # rather than leaving the original wide random draw.
    import random

    from rocket_ast import create_random_ast

    found = 0
    for seed in range(300):
        random.seed(seed)
        ast = create_random_ast(
            min_stages=1, max_stages=1,
            motor_pool=["H180W"], retro_motor_pool=["F50T"],
            retro_motor_probability=1.0, octaweb_probability=1.0,
        )
        mounts = [n for n in ast if n.node_type == "MOTOR_MOUNT"]
        if not any(m.params.get("cluster_configuration") == "3-ring" for m in mounts):
            continue
        found += 1
        body = next(n for n in ast if n.node_type == "BODY_TUBE")
        main = next(m for m in mounts if m.params.get("role") == "main")
        required = main.params["radial_offset_m"] + main.params["main_outer_radius_m"]
        # Real margin (wall/attachment/ballast growth), not the old
        # unbounded-up-to-0.20m gap -- 2x the cage's own required radius is
        # a generous ceiling that still proves the fix, without hardcoding
        # the exact tightening formula's constants into the test.
        assert body.params["radius"] <= required * 2.0, (
            f"seed {seed}: body_radius={body.params['radius']:.4f} vs "
            f"cage_required={required:.4f} -- tightening did not apply"
        )
    assert found > 0, "no octaweb candidate generated across 300 seeds"


def _octaweb_stage_ast():
    return [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=0.09),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode(
            "MOTOR_MOUNT", role="main", motor_index=18, multiplicity=3,
            radial_offset_m=0.0695, radial_angle_deg=0.0,
            instance_angle_step_deg=120.0, cluster_configuration="3-ring",
            cluster_scale=2.97, main_outer_radius_m=0.02,
            retro_sleeve_outer_radius_m=0.049,
        ),
        ASTNode(
            "MOTOR_MOUNT", role="retro", motor_index=19, multiplicity=1,
            radial_offset_m=0.0, ignition="burnout", ignition_delay=10.0,
        ),
        ASTNode("CLOSE_BODY"),
    ]


def test_compiled_3ring_mount_has_zero_radialposition_not_double_offset():
    from rocket_ast import ASTCompiler
    import xml.etree.ElementTree as ET

    xml = ASTCompiler().compile(_octaweb_stage_ast(), name="radialposition regression")
    root = ET.fromstring(xml)
    main = next(
        it for it in root.iter("innertube")
        if it.findtext("clusterconfiguration") == "3-ring"
    )
    # This is the exact bug reproduced live: radialposition=0.069517
    # stacked on clusterconfiguration=3-ring/clusterscale=2.973027 shoved
    # every motor instance outside the body tube when opened in real
    # OpenRocket. clusterscale alone must position the ring.
    assert float(main.findtext("radialposition")) == pytest.approx(0.0, abs=1e-9)
    assert main.findtext("clusterconfiguration") == "3-ring"
    # NOT the fixture's own arbitrary hardcoded 2.97 -- sanitize_ast_for_
    # openrocket now unconditionally re-tightens octaweb cage geometry
    # against the compiled candidate's real (motor_index-resolved) motor
    # pair and body radius (see its own "Repair octaweb cage geometry"
    # comment: frozen/stale cage numbers surviving many generations via
    # elitism was a separate, real live-campaign bug found after this test
    # was first written). Compute the CORRECT tangent value the same way
    # the repair pass does, instead of re-hardcoding a second magic number.
    from osifog_sweep import _falcon_cluster_geometry
    from rocket_ast import _tighten_octaweb_cage

    expected_cage = _tighten_octaweb_cage(_falcon_cluster_geometry(18, 19, 0.09))
    assert float(main.findtext("clusterscale")) == pytest.approx(
        expected_cage["cluster_scale"]
    )


def test_compiled_single_mount_still_uses_radialposition_for_real_offset():
    from rocket_ast import ASTCompiler, ASTNode
    import xml.etree.ElementTree as ET

    ast = [
        ASTNode("STAGE", name="Sustainer"),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=0.05),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode("MOTOR_MOUNT", role="main", motor_index=18),
        ASTNode(
            "MOTOR_MOUNT", role="retro", motor_index=19,
            radial_offset_m=0.03, radial_angle_deg=180.0,
        ),
        ASTNode("CLOSE_BODY"),
    ]
    xml = ASTCompiler().compile(ast, name="single-mount offset regression")
    root = ET.fromstring(xml)
    # sanitize_ast_for_openrocket (called inside compile()) recomputes the
    # exact offset from real motor radii for non-overlap -- this test only
    # needs to confirm a "single"-configuration mount's radialposition is
    # still a real, nonzero, motor-radius-driven value (i.e. the 3-ring
    # fix didn't also zero out legitimate single-mount offsets), not match
    # the raw 0.03 this AST happened to request.
    retro = next(
        it for it in root.iter("innertube")
        if it.findtext("name") == "Retro Motor Mount"
    )
    assert retro.findtext("clusterconfiguration") == "single"
    assert float(retro.findtext("radialposition")) > 0.0
    # DEGREES, not radians -- see ULTRAREVIEW-octaweb-ballast-
    # radialdirection-units.md. 180.0 here, not math.pi.
    assert float(retro.findtext("radialdirection")) == pytest.approx(180.0)


def test_compiled_ork_includes_official_anti_tumble_extension():
    from rocket_ast import ASTCompiler
    from osifog_sweep import ANTI_TUMBLE_SCRIPT, normalize_anti_tumble_script

    xml = ASTCompiler().compile(_octaweb_stage_ast(), name="anti-tumble regression")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    extensions = root.findall(".//simulation/extension")
    assert len(extensions) == 1
    script_entry = next(e for e in extensions[0].findall("entry") if e.get("key") == "script")
    assert normalize_anti_tumble_script(script_entry.text) == normalize_anti_tumble_script(ANTI_TUMBLE_SCRIPT)
    enabled_entry = next(e for e in extensions[0].findall("entry") if e.get("key") == "enabled")
    assert enabled_entry.text == "true"


def test_validate_compiled_geometry_is_clean_for_current_octaweb_generator():
    from rocket_ast import ASTCompiler, validate_compiled_geometry

    xml = ASTCompiler().compile(_octaweb_stage_ast(), name="clean geometry check")
    assert validate_compiled_geometry(xml) == []


def test_validate_compiled_geometry_catches_the_reproduced_double_offset_bug():
    from rocket_ast import validate_compiled_geometry

    # Hand-crafted XML reproducing the exact bug found live: a 3-ring
    # innertube with a nonzero radialposition.
    xml = '''<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.10" creator="test"><rocket><subcomponents>
<stage><name>Booster</name><subcomponents>
<bodytube><radius>0.09</radius><subcomponents>
<innertube><name>Octaweb Ascent Motors</name>
<radialposition>0.069517</radialposition>
<outerradius>0.02</outerradius>
<clusterconfiguration>3-ring</clusterconfiguration>
<clusterscale>2.973027</clusterscale>
</innertube>
</subcomponents></bodytube>
</subcomponents></stage>
</subcomponents></rocket>
<simulations><simulation><extension extensionid="info.openrocket.core.simulation.extension.impl.ScriptingExtension">
<entry key="script" type="string">TUMBLE</entry>
</extension></simulation></simulations>
</openrocket>'''
    violations = validate_compiled_geometry(xml)
    assert len(violations) == 1
    assert "radialposition" in violations[0]
    assert "3-ring" in violations[0]


def test_validate_compiled_geometry_catches_missing_anti_tumble():
    from rocket_ast import validate_compiled_geometry

    xml = '''<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.10" creator="test"><rocket><subcomponents>
<stage><name>Sustainer</name><subcomponents></subcomponents></stage>
</subcomponents></rocket>
<simulations><simulation></simulation></simulations>
</openrocket>'''
    violations = validate_compiled_geometry(xml)
    assert any("anti-tumble" in v for v in violations)


def test_validate_compiled_geometry_catches_component_outside_body_tube():
    from rocket_ast import validate_compiled_geometry

    xml = '''<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.10" creator="test"><rocket><subcomponents>
<stage><name>Sustainer</name><subcomponents>
<bodytube><radius>0.05</radius><subcomponents>
<innertube><name>Retro Motor Mount</name>
<radialposition>0.06</radialposition>
<outerradius>0.02</outerradius>
<clusterconfiguration>single</clusterconfiguration>
</innertube>
</subcomponents></bodytube>
</subcomponents></stage>
</subcomponents></rocket>
<simulations><simulation><extension extensionid="info.openrocket.core.simulation.extension.impl.ScriptingExtension">
<entry key="script" type="string">TUMBLE</entry>
</extension></simulation></simulations>
</openrocket>'''
    violations = validate_compiled_geometry(xml)
    assert any("exceeds body tube radius" in v for v in violations)


def _two_stage_octaweb_ast():
    stage = lambda name: [
        ASTNode("STAGE", name=name),
        ASTNode("NOSE_CONE", length=0.3),
        ASTNode("BODY_TUBE", length=1.0, radius=0.09),
        ASTNode("FIN_SET", count=4, root=0.12, height=0.06),
        ASTNode(
            "MOTOR_MOUNT", role="main", motor_index=18, multiplicity=3,
            radial_offset_m=0.0695, radial_angle_deg=0.0,
            instance_angle_step_deg=120.0, cluster_configuration="3-ring",
            cluster_scale=2.97, main_outer_radius_m=0.02,
            retro_sleeve_outer_radius_m=0.049,
        ),
        ASTNode(
            "MOTOR_MOUNT", role="retro", motor_index=19, multiplicity=1,
            radial_offset_m=0.0, ignition="burnout", ignition_delay=10.0,
        ),
        ASTNode("CLOSE_BODY"),
    ]
    return stage("Sustainer") + stage("Booster")


def test_compiled_two_stage_octaweb_has_no_duplicate_component_ids():
    from rocket_ast import ASTCompiler, validate_compiled_geometry
    import xml.etree.ElementTree as ET

    xml = ASTCompiler().compile(_two_stage_octaweb_ast(), name="id collision regression")
    root = ET.fromstring(xml)

    ids_by_label = {}
    for element in root.iter():
        id_node = element.find("id")
        if id_node is None or not (id_node.text or "").strip():
            continue
        name_node = element.find("name")
        label = name_node.text if name_node is not None else element.tag
        ids_by_label.setdefault(id_node.text.strip(), []).append(label)

    duplicated = {cid: labels for cid, labels in ids_by_label.items() if len(labels) > 1}
    assert not duplicated, f"duplicate component ids across stages: {duplicated}"

    # This exact bug reproduced live as identical centering-ring ids
    # between the Sustainer and Booster stage -- confirm both stages'
    # rings actually exist (not just that IDs happen not to collide
    # because they're missing): 2 rings (forward+aft) circumscribing the
    # 3-motor cluster per stage x 2 stages = 4 total (no ring around the
    # center/retro motor -- it's held by tangency to the 3 outer motors
    # alone, per the reference design).
    rings = list(root.iter("centeringring"))
    assert len(rings) == 4
    assert all(r.find("id") is not None and (r.find("id").text or "").strip() for r in rings)

    assert validate_compiled_geometry(xml) == []


def test_octaweb_motor_mounts_tightens_center_distance_to_true_tangency():
    import random

    from rocket_ast import octaweb_motor_mounts

    # Body radius well above the minimum needed for this motor pair --
    # before the fix, center_distance used ALL available body room,
    # leaving a visible gap between the outer motors and the central
    # retro motor instead of true tangency.
    random.seed(42)
    main_pool = ["I161W"]
    retro_pool = ["H180W"]
    mounts = octaweb_motor_mounts(main_pool, retro_pool, body_radius_m=0.15, ignition_bottom=True)
    assert mounts is not None
    main_mount, retro_mount = mounts

    main_outer = main_mount.params["main_outer_radius_m"]
    retro_sleeve_outer = main_mount.params["retro_sleeve_outer_radius_m"]
    center_distance = main_mount.params["radial_offset_m"]

    # Near-tangent + a real safety margin (see _tighten_octaweb_cage's own
    # comment: exact zero-margin tangency was the actual cause of a
    # persistent motor_mount_collision near-miss across a live campaign --
    # a real OpenRocket octaweb reference design confirmed centering rings
    # auto-size to the body/center-tube boundary and are not individually
    # tangent to each motor, so there's no physical reason to sit exactly
    # on the bare-legal minimum). center_distance == main_outer +
    # retro_sleeve_outer + SAFETY_MARGIN_M (0.005), not "however much room
    # the body tube happened to have left over".
    assert center_distance == pytest.approx(main_outer + retro_sleeve_outer + 0.005, abs=1e-9)

    # And it must be STRICTLY less than what the old body-room-driven
    # value would have been for this oversized body radius -- otherwise
    # the fix is a no-op for bodies with slack (the exact case that broke
    # visually).
    body_inner = 0.15 - 0.002
    old_body_driven_center_distance = body_inner - main_outer
    assert center_distance < old_body_driven_center_distance


def test_tighten_octaweb_cage_guarantees_outer_outer_clearance_margin():
    # Regression: the pre-fix tangent formula (center_distance = main_outer
    # + retro_sleeve_outer, zero margin) only guaranteed the retro sleeve
    # was tangent to the main motors -- it never independently verified the
    # 3 main motors clear EACH OTHER (l2_engine's
    # enforce_motor_mount_clearance requires chord=center_distance*sqrt(3)
    # >= 2*main_outer+0.002). For a large-main/small-retro motor pair these
    # two constraints nearly coincide right at the boundary -- confirmed as
    # the actual cause of a live campaign's persistent motor_mount_collision
    # near-miss (every elite, every cycle, for hundreds of generations,
    # typically within 2-3% of the legal threshold). Also per user
    # direction: a real OpenRocket-provided octaweb reference design shows
    # centering rings auto-sizing to the body/center-tube boundary, NOT
    # individually tangent to each clustered motor -- there was never a
    # physical reason to sit exactly on the bare-legal minimum.
    import math

    from osifog_sweep import _falcon_cluster_geometry
    from rocket_ast import _tighten_octaweb_cage

    # L1500T main (largest legal main motor, 98mm) / F50T retro (smallest
    # legal retro motor, 29mm) -- the degenerate large-main/small-retro case.
    cage = _tighten_octaweb_cage(_falcon_cluster_geometry(25, 0, 0.15))
    main_outer = cage["main_outer_radius_m"]
    chord = cage["center_distance_m"] * math.sqrt(3.0)
    needed = 2.0 * main_outer + 0.002
    assert chord - needed >= 0.004, (
        f"outer-outer clearance margin too thin: chord={chord:.6f} needed={needed:.6f}"
    )


def test_sanitize_widens_body_radius_when_octaweb_motor_swap_no_longer_fits():
    # Regression: root-caused as the actual cause of a live campaign's
    # persistent motor_mount_collision failures (74% of v9's population,
    # every elite, every cycle). A mutation/crossover motor swap can change
    # a stage's main motor to something physically BIGGER than what that
    # stage's CURRENT body radius can host as a legal 3+1 cage
    # (_falcon_cluster_geometry raises ValueError in that case). The
    # previous behavior left the STALE cage (radial_offset_m/
    # main_outer_radius_m, sized for whatever smaller motor was there
    # before the swap) untouched, while l2_engine independently derives the
    # REAL (bigger) mount_outer_radius_m from the new motor's actual
    # thrust-curve diameter -- producing a genuine, guaranteed geometric
    # overlap. Confirmed empirically against a real failing v9 elite
    # candidate before this fix: reported dist=0.098871 < needed=0.102000
    # matched L1500T's real 98mm diameter exactly (2*(0.098/2+0.001)+0.002
    # = 0.102000) against a cage cached for a smaller motor.
    #
    # This test constructs the same failure mode directly: a stage whose
    # main motor is swapped to L1500T (98mm, the largest legal main motor)
    # but whose BODY_TUBE radius is still sized for something much smaller
    # -- too small to legally host L1500T's octaweb cage at all.
    import math

    from osifog_sweep import _falcon_cluster_geometry, _min_octaweb_body_radius_m
    from rocket_ast import ASTNode, MOTOR_DATABASE, octaweb_motor_mounts, sanitize_ast_for_openrocket

    l1500t_idx = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "L1500T")
    retro_idx = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "F50T")

    small_radius = 0.05
    # Confirm the premise: this motor pair genuinely does not fit this body
    # radius (the exact ValueError path the fix handles).
    with pytest.raises(ValueError):
        _falcon_cluster_geometry(l1500t_idx, retro_idx, small_radius)

    main_mount, retro_mount = octaweb_motor_mounts(["I161W"], ["F50T"], body_radius_m=small_radius + 0.03, ignition_bottom=True)
    # Simulate a motor-swap mutation/crossover result: main motor becomes
    # L1500T but the body radius (and the mount's own now-stale cage
    # params) were never touched.
    main_mount.params["motor_index"] = l1500t_idx
    main_mount.params["motor_designation"] = "L1500T"

    ast = [
        ASTNode("STAGE", name="Evolved Sustainer"),
        ASTNode("NOSE_CONE", shape="ogive", length=0.3, material="cardboard"),
        ASTNode("BODY_TUBE", length=1.0, radius=small_radius, material="cardboard"),
        ASTNode("FIN_SET", count=4, sweep=20.0, root=0.12, height=0.08),
        main_mount,
        retro_mount,
        ASTNode("CLOSE_BODY"),
    ]

    repaired = sanitize_ast_for_openrocket(ast)
    body_node = next(n for n in repaired if n.node_type == "BODY_TUBE")
    repaired_main = next(
        n for n in repaired
        if n.node_type == "MOTOR_MOUNT" and n.params.get("multiplicity") == 3
    )
    repaired_retro = next(
        n for n in repaired
        if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "retro"
    )

    min_required = _min_octaweb_body_radius_m(l1500t_idx, retro_idx)
    assert body_node.params["radius"] >= min_required, (
        "body radius must be widened to actually fit the swapped-in motor, "
        "not left stale"
    )

    # The repaired cage must be genuinely self-consistent and collision-free
    # against the SAME formula l2_engine's enforce_motor_mount_clearance
    # uses: chord (main-vs-main) and main-vs-retro distance must both clear
    # their required separation.
    main_outer = repaired_main.params["main_outer_radius_m"]
    center_distance = repaired_main.params["radial_offset_m"]
    chord = center_distance * math.sqrt(3.0)
    assert chord >= 2.0 * main_outer + 0.002, "main motors must not collide with each other"
    assert repaired_retro.params["radial_offset_m"] == 0.0, "octaweb retro must stay centered"
    retro_sleeve_outer = repaired_main.params["retro_sleeve_outer_radius_m"]
    assert center_distance >= main_outer + retro_sleeve_outer, "main must not collide with retro sleeve"


def test_octaweb_motor_mounts_assigns_independent_materials_from_legal_pool():
    import random

    from rocket_ast import MOUNT_MATERIAL_CHOICES, octaweb_motor_mounts

    seen_main, seen_retro, seen_ring = set(), set(), set()
    for seed in range(2000, 2100):
        random.seed(seed)
        mounts = octaweb_motor_mounts(["I161W"], ["H180W"], body_radius_m=0.1, ignition_bottom=True)
        if mounts is None:
            continue
        main_mount, retro_mount = mounts
        assert main_mount.params["mount_material"] in MOUNT_MATERIAL_CHOICES
        assert retro_mount.params["mount_material"] in MOUNT_MATERIAL_CHOICES
        assert main_mount.params["ring_material"] in MOUNT_MATERIAL_CHOICES
        seen_main.add(main_mount.params["mount_material"])
        seen_retro.add(retro_mount.params["mount_material"])
        seen_ring.add(main_mount.params["ring_material"])
    # Real freedom, not a fixed default -- confirms the GA can actually
    # explore more than one material across generations.
    assert len(seen_main) > 1
    assert len(seen_retro) > 1
    assert len(seen_ring) > 1


def test_octaweb_motor_mounts_sets_matching_mount_material_density():
    import random

    from rocket_ast import MATERIALS, octaweb_motor_mounts

    random.seed(2000)
    main_mount, retro_mount = octaweb_motor_mounts(["I161W"], ["H180W"], body_radius_m=0.1, ignition_bottom=True)
    assert main_mount.params["mount_material_density"] == pytest.approx(
        MATERIALS[main_mount.params["mount_material"]][2]
    )
    assert retro_mount.params["mount_material_density"] == pytest.approx(
        MATERIALS[retro_mount.params["mount_material"]][2]
    )


def test_compiler_writes_configured_mount_and_ring_materials_not_hardcoded():
    """Regression: _motor_mount_xml/_octaweb_circumscribing_rings_xml used
    to hardcode "kraft"/"fiberglass" respectively, ignoring any material
    param entirely."""
    from rocket_ast import ASTCompiler, MATERIALS

    ast = _octaweb_stage_ast()
    for node in ast:
        if node.node_type == "MOTOR_MOUNT" and node.params.get("role") == "main":
            node.params["mount_material"] = "carbon"
            node.params["ring_material"] = "aluminum"
        elif node.node_type == "MOTOR_MOUNT" and node.params.get("role") == "retro":
            node.params["mount_material"] = "aluminum"
    xml = ASTCompiler().compile(ast, name="material wiring regression")

    carbon_density = MATERIALS["carbon"][2]
    aluminum_density = MATERIALS["aluminum"][2]
    assert f'density="{carbon_density}"' in xml
    assert f'density="{aluminum_density}"' in xml
    assert "kraft" not in xml.lower()


def test_sanitize_motor_falls_back_to_kraft_for_invalid_mount_material():
    from rocket_ast import ASTNode, MATERIALS, _sanitize_motor

    node = ASTNode("MOTOR_MOUNT", motor_index=18, mount_material="not_a_real_material")
    sanitized = _sanitize_motor(node)
    assert sanitized.params["mount_material"] == "kraft"
    assert sanitized.params["mount_material_density"] == pytest.approx(MATERIALS["kraft"][2])


def test_body_widening_rescales_fins_proportionally_not_just_the_body():
    """Regression for a real, reproduced bug: widening a stage's body
    radius to match a bigger sibling stage (the diameter-discontinuity
    fix) left that stage's fins sized relative to the OLD radius,
    dropping as low as a 0.74 root/radius ratio against the 1.2 minimum
    every fin needs for adequate static margin. Found via ultrareview-
    style stress testing (2500 random rockets across 50 seeds), not a
    single lucky/unlucky case."""
    import random

    from rocket_ast import create_random_ast

    violations = 0
    for trial_seed in range(20):
        random.seed(trial_seed)
        for _ in range(20):
            ast = create_random_ast()
            stages, current = [], []
            for node in ast:
                if node.node_type == "STAGE" and current:
                    stages.append(current)
                    current = [node]
                else:
                    current.append(node)
            stages.append(current)
            for stage in stages:
                body = next(n for n in stage if n.node_type == "BODY_TUBE")
                fins = [
                    n for n in stage
                    if n.node_type == "FIN_SET" and n.params.get("role") != "forward_flap"
                ]
                for fin in fins:
                    if fin.params["root"] < body.params["radius"] * 1.2:
                        violations += 1
    assert violations == 0
