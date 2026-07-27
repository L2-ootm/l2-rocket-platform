import pytest

from scripts.osifog_seed_certification import (
    upper_failure_bound_zero_failures,
)


def test_zero_failure_bound_matches_exact_binomial_formula():
    assert upper_failure_bound_zero_failures(300) == pytest.approx(
        1.0 - 0.05 ** (1.0 / 300)
    )
    assert upper_failure_bound_zero_failures(300) < 0.01


def test_zero_failure_bound_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        upper_failure_bound_zero_failures(0)
    with pytest.raises(ValueError):
        upper_failure_bound_zero_failures(10, confidence=1.0)
