"""Unit tests for pure-Python helpers in scripts/flip_diagnosis.py. No
OpenRocket JVM needed -- these only exercise post-processing logic on
synthetic trace data.
"""
from scripts.flip_diagnosis import _min_speed_after_ignition


def _pt(t, speed_ms, q, thrust_n=0.0):
    return {"t": t, "speed_ms": speed_ms, "q": q, "thrust_n": thrust_n}


def test_min_speed_excludes_ground_hit_zeroed_sample():
    # Regression: OpenRocket zeroes velocity (q recorded as None) at and
    # after GROUND_HIT. An unfiltered min() over speed_ms always picks that
    # zeroed sample (speed_ms=0.0) instead of the genuine in-flight minimum.
    trace = [
        _pt(9.5, 5.0, 0.9),
        _pt(9.6, 3.2, 0.5),   # genuine in-flight minimum
        _pt(9.7, 8.0, -0.3),
        _pt(19.5, 0.0, None),  # post ground-hit, zeroed
    ]
    result = _min_speed_after_ignition(trace, ignition_t=9.0, hit_time=19.5)
    assert result["t"] == 9.6
    assert result["speed_ms"] == 3.2


def test_min_speed_excludes_samples_before_ignition():
    trace = [
        _pt(1.0, 0.5, 0.9),  # pre-ignition, would be the min if not excluded
        _pt(9.6, 3.2, 0.5),
    ]
    result = _min_speed_after_ignition(trace, ignition_t=9.0, hit_time=None)
    assert result["t"] == 9.6


def test_min_speed_returns_none_if_nothing_qualifies():
    trace = [_pt(19.5, 0.0, None)]
    result = _min_speed_after_ignition(trace, ignition_t=9.0, hit_time=19.5)
    assert result is None
