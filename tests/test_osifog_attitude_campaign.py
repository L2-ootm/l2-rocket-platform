import math
import random
from types import SimpleNamespace

import osifog_attitude_campaign as campaign


def test_recovery_pass_requires_both_fast_landings_and_verified_braking():
    metrics = {
        "stage_landings": [
            {"stage_key": "s0", "total_speed": 4.0},
            {"stage_key": "s1", "total_speed": 4.5},
        ],
        "retro_burn_diagnostics": [
            {"stage_key": "s0", "retro_braking_verified": True},
            {"stage_key": "s1", "retro_braking_verified": True},
        ],
    }
    passed, evidence = campaign._recovery_pass(metrics)
    assert passed is True
    assert evidence["landing_speeds_ms"] == {"s0": 4.0, "s1": 4.5}

    metrics["retro_burn_diagnostics"][1]["retro_braking_verified"] = False
    assert campaign._recovery_pass(metrics)[0] is False


def test_ridge_surrogate_returns_finite_predictions_after_authority_warmup():
    width = len(campaign.FEATURE_KEYS) + 4
    history = []
    for index in range(24):
        features = [0.0] * width
        features[0] = index / 10.0
        history.append({"features": features, "attitude_score": 3.0 * index + 7.0})
    proposals = []
    for value in (0.25, 0.75):
        features = [0.0] * width
        features[0] = value
        proposals.append({"features": features})

    predictions = campaign._ridge_predictions(history, proposals)

    assert len(predictions) == 2
    assert all(math.isfinite(value) for value in predictions)
    assert predictions[1] > predictions[0]


def test_authority_selection_reserves_low_nose_recovery_candidates():
    proposals = []
    for index in range(12):
        parameters = {
            key: 0.0 for key in campaign.FEATURE_KEYS
        }
        parameters.update(
            nose_mass_kg=5.0 if index < 8 else 0.08,
            launch_azimuth=float(index),
            s0_core_fin_height=0.5,
            s0_core_fin_root=0.1,
            s0_retro=1,
            s1_retro=1,
            wind_levels=[(0.0, 1.0, 0.0, 0.0)],
        )
        rust = SimpleNamespace(
            score=1000.0 - index,
            rust_apogee_m=3000.0,
            rust_mach=0.8,
            rust_min_static_margin=1.7,
            rust_total_prop_mass_kg=1.0,
        )
        proposals.append((parameters, rust))

    selected = campaign._select_authority_batch(
        proposals, [], 6, random.Random(1)
    )

    assert len(selected) == 6
    assert any(parameters["nose_mass_kg"] == 0.08 for parameters, _ in selected)


def test_terminal_worker_outcome_distinguishes_exhaustion_from_failure():
    assert campaign._terminal_worker_outcome([
        {"status": "budget_exhausted"},
        {"status": "budget_exhausted"},
    ]) == ("budget_exhausted", "attitude_search_budget_exhausted")

    assert campaign._terminal_worker_outcome([
        {"status": "budget_exhausted"},
        {"status": "blocked"},
    ]) == ("blocked", "attitude_workers_blocked")


def test_descent_summary_requires_passive_transition_for_both_stages(monkeypatch):
    monkeypatch.setattr(campaign.search, "_delay_candidates", lambda *args: [10.0])
    monkeypatch.setattr(
        campaign.search, "_landing_opportunity",
        lambda metrics, parameters, branch, delay: {"usable": branch == 0},
    )
    trace = lambda qs, speed: [
        {
            "time_s": float(i), "alignment_q": q, "speed_ms": speed + i,
            "altitude_m": 100.0 - 20.0 * i, "vertical_speed_ms": -speed,
            "horizontal_speed_ms": 1.0, "theta_deg": 0.0,
            "vertical_alignment_q": q,
        }
        for i, q in enumerate(qs)
    ]
    metrics = {
        "branch_identities": [
            {"branch": 0, "stage_key": "s0"},
            {"branch": 1, "stage_key": "s1"},
        ],
        "descent_alignment_diagnostics": [
            {
                "branch": 0, "best_alignment_q": 0.95,
                "alignment_trace": trace([0.0, 0.2, 0.7, 0.8, 0.9], 20.0),
                "tail_first_windows": [{"duration_s": 2.0}],
            },
            {
                "branch": 1, "best_alignment_q": 0.90,
                "alignment_trace": trace([0.0, 0.1, 0.2, 0.3, 0.4], 25.0),
                "tail_first_windows": [],
            },
        ],
    }

    result = campaign._descent_branch_summary(metrics, {})

    assert result["passive_transition_stages"] == 1
    assert result["usable_stages"] == 1
    assert result["best_alignment_q"] == 0.90
    assert result["stage_attitude"]["s0"]["early_broadside_fraction"] > 0.0
    assert result["stage_attitude"]["s1"]["passive_transition"] is False


def test_ascent_corridor_phase_ranks_apogee_before_attitude():
    close = {
        "ascent_admissible": True, "apogee_error_m": 100.0,
        "near_impact_alignment_q": -0.9, "min_static_margin": 1.5,
    }
    aligned_but_low = {
        "ascent_admissible": True, "apogee_error_m": 1800.0,
        "near_impact_alignment_q": 1.0, "min_static_margin": 3.0,
    }
    assert campaign._selection_key(close, "ascent_corridor") > campaign._selection_key(
        aligned_but_low, "ascent_corridor"
    )
