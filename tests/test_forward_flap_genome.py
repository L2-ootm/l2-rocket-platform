"""Regression coverage for the forward-flap / retro-motor genome extension.

Context: OSIFOG bans passive recovery devices and requires passive (no
active control) tail-first descent for a retro-propulsive landing. A prior
side experiment (designs/osifog_level3/starship_best_genome.json) showed a
nose-mounted "forward flap" FIN_SET forces exactly that attitude, but the
mechanism was never wired into rocket_ast.py/organic_loop.py's actual
generator/mutator -- these tests lock in that wiring so it can't silently
regress (e.g. sanitize_ast_for_openrocket's default parachute fallback, or a
mutation re-rolling a retro motor outside its legal designation pool).
"""
import random
import xml.etree.ElementTree as ET

import pytest

from rocket_ast import ASTCompiler, MOTOR_DATABASE, create_random_ast
from organic_loop import OrganicCandidate, mutate_ast, official_score_breakdown

RETRO_POOL = ["H73J", "H128W", "H180W", "K550W"]


def _generate(seed, **kwargs):
    random.seed(seed)
    return create_random_ast(min_stages=2, max_stages=2, **kwargs)


@pytest.mark.parametrize("seed", range(10))
def test_no_recovery_devices_suppresses_parachute(seed):
    ast = _generate(seed, no_recovery_devices=True)
    assert not any(n.node_type == "PARACHUTE" for n in ast)
    # sanitize_ast_for_openrocket's own fallback chute must also stay
    # suppressed -- it triggers off STAGE.recovery, not just absence of a
    # PARACHUTE node in the pre-sanitize AST.
    xml = ASTCompiler().compile(ast, name="Parachute Suppression Test")
    assert "<parachute>" not in xml


@pytest.mark.parametrize("seed", range(10))
def test_forward_flap_generation_and_bounds(seed):
    ast = _generate(seed, forward_flap_probability=1.0)
    flaps = [n for n in ast if n.node_type == "FIN_SET" and n.params.get("role") == "forward_flap"]
    assert flaps, "forward_flap_probability=1.0 must produce at least one forward flap per stage"
    for flap in flaps:
        assert 0.0 <= flap.params["position_from_top_m"] <= 0.15
        assert 0.04 <= flap.params["root"] <= 0.20
        assert 0.03 <= flap.params["height"] <= 0.15


def test_forward_flap_compiles_with_top_position_tag():
    ast = _generate(1, forward_flap_probability=1.0)
    xml = ASTCompiler().compile(ast, name="Flap XML Test")
    assert "Forward Flap" in xml
    root = ET.fromstring(xml)
    flap_finsets = [
        el for el in root.iter("freeformfinset")
        if el.findtext("name") == "Forward Flap"
    ]
    assert flap_finsets
    for finset in flap_finsets:
        position = finset.find("position")
        assert position.get("type") == "top"
        assert float(position.text) <= 0.15


def _stage_motor_groups(ast):
    """Split MOTOR_MOUNT nodes by their owning STAGE (a 2-stage AST has one
    main+retro pair per stage -- comparing across stages gives meaningless
    radii)."""
    groups = []
    current = None
    for node in ast:
        if node.node_type == "STAGE":
            if current is not None:
                groups.append(current)
            current = []
        elif node.node_type == "MOTOR_MOUNT" and current is not None:
            current.append(node)
    if current is not None:
        groups.append(current)
    return groups


@pytest.mark.parametrize("seed", range(10))
def test_retro_motor_generation_pool_and_clearance(seed):
    ast = _generate(seed, retro_motor_pool=RETRO_POOL, retro_motor_probability=1.0)
    retros = [n for n in ast if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "retro"]
    assert retros, "retro_motor_probability=1.0 must produce a retro motor mount per stage"
    for retro in retros:
        assert retro.params["motor_designation"] in RETRO_POOL
        assert retro.params["ignition"] == "burnout"
    # Radial non-collision, per stage: a stage's retro offset must clear both
    # its own radius and that *same stage's* centerline main motor radius.
    for motors in _stage_motor_groups(ast):
        stage_retros = [m for m in motors if m.params.get("role") == "retro"]
        stage_mains = [m for m in motors if m.params.get("role") != "retro"]
        if not stage_retros or not stage_mains:
            continue
        main_r = MOTOR_DATABASE[stage_mains[0].params["motor_index"]][2] / 2.0
        for retro in stage_retros:
            retro_r = MOTOR_DATABASE[retro.params["motor_index"]][2] / 2.0
            assert retro.params["radial_offset_m"] >= main_r + retro_r


def test_retro_and_flap_survive_repeated_mutation():
    random.seed(11)
    ast = create_random_ast(
        min_stages=2,
        max_stages=2,
        no_recovery_devices=True,
        forward_flap_probability=1.0,
        retro_motor_pool=RETRO_POOL,
        retro_motor_probability=1.0,
    )
    for _ in range(25):
        ast = mutate_ast(ast, rate=0.6, retro_motor_pool=RETRO_POOL)

    assert not any(n.node_type == "PARACHUTE" for n in ast)
    retros = [n for n in ast if n.node_type == "MOTOR_MOUNT" and n.params.get("role") == "retro"]
    for retro in retros:
        assert retro.params["motor_designation"] in RETRO_POOL, (
            "mutation must never re-roll a retro motor outside its legal pool"
        )


OSIFOG_SCORING = {
    "base_score": 900000.0,
    "terms": [
        {"name": "apogee_altitude", "metrics": ["apogee_m"], "reference": [3000.0], "power": 2, "coefficient": -3000.0},
        {"name": "apogee_horizontal", "metrics": ["apogee_east_m", "apogee_north_m"], "reference": [0.0, 0.0], "power": 2, "coefficient": -16.0},
        {"name": "touchdown_position", "metrics": ["stage_landing_east_m", "stage_landing_north_m"], "reference": [0.0, 0.0], "power": 2, "coefficient": -2.0, "aggregate": "mean_over_stages"},
        {"name": "touchdown_speed", "metrics": ["stage_landing_total_speed_ms"], "reference": [0.0], "power": 2, "coefficient": -500.0, "aggregate": "mean_over_stages"},
        {"name": "propellant_used", "metrics": ["total_prop_mass_kg"], "reference": [0.0], "power": 1, "coefficient": -7500.0},
    ],
}


def test_official_score_breakdown_matches_hand_computed_formula():
    candidate = OrganicCandidate(
        ast=[], score=0.0, raw_score=0.0, status="success", reason="ok",
        rust_apogee_m=3000.0, rust_apogee_east_m=0.0, rust_apogee_north_m=0.0,
        rust_total_prop_mass_kg=0.5,
        rust_stage_landings=[{"east_m": 1.0, "north_m": 2.0, "total_speed_ms": 3.0}],
    )
    breakdown = official_score_breakdown(candidate, OSIFOG_SCORING)
    expected = 900000 - 2 * (1.0**2 + 2.0**2) - 500 * (3.0**2) - 7500 * 0.5
    assert breakdown["complete"] is True
    assert breakdown["computed_score"] == pytest.approx(expected)


def test_official_score_breakdown_two_stage_mean_and_incomplete_flag():
    candidate = OrganicCandidate(
        ast=[], score=0.0, raw_score=0.0, status="success", reason="ok",
        rust_apogee_m=2990.0, rust_apogee_east_m=1.0, rust_apogee_north_m=-1.0,
        rust_total_prop_mass_kg=1.2,
        rust_stage_landings=[
            {"east_m": 2.0, "north_m": 0.0, "total_speed_ms": 4.0},
            {"east_m": 0.0, "north_m": 2.0, "total_speed_ms": 2.0},
        ],
    )
    breakdown = official_score_breakdown(candidate, OSIFOG_SCORING)
    touchdown_speed_term = next(t for t in breakdown["terms"] if t["name"] == "touchdown_speed")
    assert touchdown_speed_term["components"][0]["value"] == pytest.approx(3.0)  # mean(4,2)
    assert breakdown["complete"] is True

    # No landings at all -- must report incomplete, not fabricate a score.
    empty = OrganicCandidate(
        ast=[], score=0.0, raw_score=0.0, status="failed", reason="incomplete",
        rust_apogee_m=500.0, rust_stage_landings=[],
    )
    incomplete_breakdown = official_score_breakdown(empty, OSIFOG_SCORING)
    assert incomplete_breakdown["complete"] is False
    assert incomplete_breakdown["computed_score"] is None
