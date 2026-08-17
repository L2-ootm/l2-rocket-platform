"""Small, physics-agnostic primitives for staged mission evolution.

The module deliberately knows nothing about rockets or OpenRocket.  A mission
adapter supplies an evaluator, hard gate, phase candidate generators and
objectives.  This keeps complex missions composable without creating another
simulation engine or optimization framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


Params = Mapping[str, Any]
Metrics = Mapping[str, Any]
Evaluator = Callable[[Params], Metrics]
Gate = Callable[[Metrics, Params], bool]
Objective = Callable[[Metrics, Params], float]
CandidateGenerator = Callable[[Params], Iterable[Params]]


def _freeze(value: Any) -> Any:
    """Convert nested JSON-like parameters into a deterministic cache key."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, float):
        return round(value, 12)
    return value


@dataclass(frozen=True)
class EvaluatedCandidate:
    params: Params
    metrics: Metrics
    objective: float


@dataclass(frozen=True)
class Phase:
    """One bounded evolutionary step over candidates derived from the winner."""

    name: str
    candidates: CandidateGenerator
    objective: Objective
    gate: Gate = lambda metrics, params: True


class EvolutionEngine:
    """Memoized evaluator with goal-preserving, phase-by-phase selection."""

    def __init__(self, evaluator: Evaluator):
        self._evaluator = evaluator
        self._cache: dict[Any, Metrics] = {}

    def evaluate(self, params: Params) -> Metrics:
        key = _freeze(params)
        if key not in self._cache:
            self._cache[key] = self._evaluator(dict(params))
        return self._cache[key]

    @property
    def evaluation_count(self) -> int:
        return len(self._cache)

    def run(self, initial: Params, phases: Iterable[Phase]) -> tuple[EvaluatedCandidate, list[EvaluatedCandidate]]:
        current_params: Params = dict(initial)
        history: list[EvaluatedCandidate] = []

        for phase in phases:
            legal: list[EvaluatedCandidate] = []
            for params in phase.candidates(current_params):
                normalized = dict(params)
                metrics = self.evaluate(normalized)
                if phase.gate(metrics, normalized):
                    legal.append(
                        EvaluatedCandidate(
                            params=normalized,
                            metrics=metrics,
                            objective=float(phase.objective(metrics, normalized)),
                        )
                    )
            if not legal:
                raise RuntimeError(f"phase {phase.name!r} produced no legal candidates")
            winner = min(legal, key=lambda item: item.objective)
            history.append(winner)
            current_params = winner.params

        if not history:
            metrics = self.evaluate(current_params)
            history.append(EvaluatedCandidate(current_params, metrics, 0.0))
        return history[-1], history


def bisect_transition(
    evaluate: Callable[[float], Metrics],
    low: float,
    high: float,
    is_direct: Callable[[Metrics], bool],
    objective: Callable[[Metrics], float],
    iterations: int = 24,
) -> tuple[float, Metrics]:
    """Find the best point at a discontinuous direct/relaunch boundary.

    ``low`` must be on the non-direct side and ``high`` on the direct side.
    Every evaluated direct result competes by the supplied objective, allowing
    touchdown speed to be minimized even when the response is discontinuous.
    """
    low_metrics = evaluate(low)
    high_metrics = evaluate(high)
    if is_direct(low_metrics):
        raise ValueError("low endpoint must be on the non-direct side")
    if not is_direct(high_metrics):
        raise ValueError("high endpoint must be on the direct side")

    best_value, best_metrics = high, high_metrics
    for _ in range(iterations):
        value = (low + high) / 2.0
        metrics = evaluate(value)
        if is_direct(metrics):
            high = value
            if objective(metrics) < objective(best_metrics):
                best_value, best_metrics = value, metrics
        else:
            low = value
    return best_value, best_metrics
