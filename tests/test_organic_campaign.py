import json
from types import SimpleNamespace

import organic_campaign as campaign


def _run(out_dir, mission_path, **overrides):
    args = campaign.parse_args([
        "--mission", str(mission_path),
        "--out", str(out_dir),
        "--population", "8",
        "--elite-count", "2",
        "--generations-per-cycle", "1",
        "--max-cycles", "1",
        "--execution-profile", "super-speed",
        "--calibrate-every", "0",
        "--validate-openrocket", "0",
        "--seed", "17",
    ])
    for key, value in overrides.items():
        setattr(args, key, value)
    return campaign.run_campaign(args)


def test_campaign_writes_monitoring_files_and_terminal_state(tmp_path):
    mission = tmp_path / "mission.json"
    mission.write_text(json.dumps({
        "target_apogee": 3000.0,
        "scoring": {
            "base_score": 900000.0,
            "terms": [
                {"name": "apogee_altitude", "metrics": ["apogee_m"], "reference": [3000.0], "power": 2, "coefficient": -3000.0},
            ],
        },
        "constraints": {"min_stages": 1, "max_stages": 1, "min_static_margin": 1.5},
    }), encoding="utf-8")

    out = tmp_path / "campaign"
    rc = _run(out, mission)
    assert rc == 0

    for name in (
        "campaign-manifest.json", "campaign-progress.json", "campaign-state.json",
        "health.json", "best-candidate.json", "alert.json", "events.jsonl",
        "organic_elite.json",
    ):
        assert (out / name).exists(), f"missing {name}"

    state = json.loads((out / "campaign-state.json").read_text())
    assert state["status"] in campaign.TERMINAL_STATES

    progress = json.loads((out / "campaign-progress.json").read_text())
    assert progress["cycles_completed"] == 1
    assert progress["cumulative_generations"] == 1

    best = json.loads((out / "best-candidate.json").read_text())
    assert "population_stats" in best
    assert best["population_stats"]["population_size"] == 8
    assert "official_score_breakdown" in (best["best"] or {})

    # no lease left dangling after a clean exit
    assert not (out / "campaign.lease.json").exists()


def test_campaign_resumes_cumulative_progress_across_restarts(tmp_path):
    mission = tmp_path / "mission.json"
    mission.write_text(json.dumps({
        "target_apogee": 3000.0,
        "scoring": {"base_score": 900000.0, "terms": []},
        "constraints": {"min_stages": 1, "max_stages": 1, "min_static_margin": 1.5},
    }), encoding="utf-8")

    out = tmp_path / "campaign"
    # --max-cycles is a cumulative lifetime budget: a single `run_campaign`
    # call loops continuously until that budget (or the goal) is hit, so to
    # exercise "restart resumes instead of resetting" the first invocation
    # must exhaust its own (smaller) budget and exit on its own, then a
    # second invocation with a raised budget continues from where it left
    # off -- exactly the shape a watchdog-driven budget increase would take.
    _run(out, mission, max_cycles=1)
    first_progress = json.loads((out / "campaign-progress.json").read_text())
    assert first_progress["cycles_completed"] == 1
    assert first_progress["cumulative_generations"] == 1

    events_before = (out / "events.jsonl").read_text().count("resumed_from_checkpoint")
    assert events_before == 0

    # Simulate a restart against the SAME --out: must resume, not reset.
    _run(out, mission, max_cycles=2)
    second_progress = json.loads((out / "campaign-progress.json").read_text())
    assert second_progress["cycles_completed"] == 2
    assert second_progress["cumulative_generations"] == 2

    events_after = (out / "events.jsonl").read_text().count("resumed_from_checkpoint")
    assert events_after == 1


def _stagnation_args(**overrides):
    defaults = {"stagnation_cycles": 3, "stagnation_score_ratio": 0.9}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_stagnation_guard_counts_consecutive_stagnant_cycles_then_fires():
    progress = {"last_legality_rate": 0.0, "last_score_ratio": 0.95}
    args = _stagnation_args()

    assert campaign._apply_stagnation_guard(progress, args) is False
    assert progress["stagnant_cycles"] == 1
    assert campaign._apply_stagnation_guard(progress, args) is False
    assert progress["stagnant_cycles"] == 2
    # Third consecutive stagnant cycle hits the threshold (3) -- fires and
    # resets the counter so a fresh streak has to accumulate again.
    assert campaign._apply_stagnation_guard(progress, args) is True
    assert progress["stagnant_cycles"] == 0


def test_stagnation_guard_resets_on_any_non_stagnant_cycle():
    progress = {"last_legality_rate": 0.0, "last_score_ratio": 0.95}
    args = _stagnation_args()

    campaign._apply_stagnation_guard(progress, args)
    campaign._apply_stagnation_guard(progress, args)
    assert progress["stagnant_cycles"] == 2

    # Population diversified (median pulled away from max) -- not stagnant.
    progress["last_score_ratio"] = 0.5
    assert campaign._apply_stagnation_guard(progress, args) is False
    assert progress["stagnant_cycles"] == 0


def test_stagnation_guard_ignores_populations_with_any_legal_candidate():
    progress = {"last_legality_rate": 0.02, "last_score_ratio": 0.99}
    args = _stagnation_args()

    for _ in range(5):
        assert campaign._apply_stagnation_guard(progress, args) is False
    assert progress["stagnant_cycles"] == 0


def test_stagnation_guard_disabled_when_stagnation_cycles_is_zero():
    progress = {"last_legality_rate": 0.0, "last_score_ratio": 1.0}
    args = _stagnation_args(stagnation_cycles=0)

    for _ in range(10):
        assert campaign._apply_stagnation_guard(progress, args) is False
