import pytest

from mission_evolution import EvolutionEngine, Phase, bisect_transition


def test_engine_runs_goal_preserving_phases_and_memoizes_candidates():
    calls = []

    def evaluate(params):
        calls.append(params["x"])
        return {"error": abs(params["x"] - 3), "legal": params["x"] >= 0}

    engine = EvolutionEngine(evaluate)
    phases = [
        Phase(
            "coarse",
            candidates=lambda current: ({"x": value} for value in (-1, 1, 3, 5)),
            gate=lambda metrics, params: metrics["legal"],
            objective=lambda metrics, params: metrics["error"],
        ),
        Phase(
            "confirm",
            candidates=lambda current: (current, {"x": 4}),
            gate=lambda metrics, params: metrics["legal"],
            objective=lambda metrics, params: metrics["error"],
        ),
    ]

    winner, history = engine.run({"x": 0}, phases)

    assert winner.params["x"] == 3
    assert [item.params["x"] for item in history] == [3, 3]
    assert calls.count(3) == 1
    assert engine.evaluation_count == 5


def test_transition_bisection_keeps_best_direct_touchdown():
    def evaluate(delay):
        if delay < 2.0:
            return {"direct": False, "speed": 40.0}
        return {"direct": True, "speed": 2.0 + 10.0 * (delay - 2.0)}

    delay, metrics = bisect_transition(
        evaluate,
        1.0,
        3.0,
        is_direct=lambda result: result["direct"],
        objective=lambda result: result["speed"],
        iterations=24,
    )

    assert delay == pytest.approx(2.0, abs=1.0e-6)
    assert metrics["speed"] == pytest.approx(2.0, abs=1.0e-5)


def test_transition_bisection_rejects_invalid_bracket():
    with pytest.raises(ValueError, match="low endpoint"):
        bisect_transition(
            lambda value: {"direct": True, "speed": value},
            1.0,
            2.0,
            is_direct=lambda result: result["direct"],
            objective=lambda result: result["speed"],
        )
