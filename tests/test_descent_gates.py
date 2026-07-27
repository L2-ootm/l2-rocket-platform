"""Unit tests for the descent-only ranking and powered admission/early-stop
gates (scripts/descent_gates.py). All synthetic -- no OpenRocket JVM needed.
"""
import math

import pytest

from scripts.descent_gates import (
    q_components, descent_profile, impulse_opposition_fraction,
    passive_descent_admission, PoweredEarlyStop, apex_time_from_apogee_events,
)


def test_apex_time_uses_apogee_event_not_argmax_altitude():
    # Regression for the 2026-07-20 diagnostic-integrity bug: a retro burn
    # firing near the natural apogee can add enough net-upward impulse to
    # create a second, HIGHER altitude peak later in the flight (measured
    # case: true apogee 247.45 m @ t=6.4645s; post-burn second peak 289.81 m
    # @ t=10.33s). argmax(altitude) picks the second peak; the true apex
    # must come from OpenRocket's own APOGEE event instead.
    t_arr = [6.0145, 6.4645, 6.9645, 10.2685, 10.33]
    alt_arr = [246.373, 247.451, 246.13, 289.786, 289.811]
    apogee_events = [6.4645375892102075]
    apex_t = apex_time_from_apogee_events(apogee_events, t_arr, alt_arr)
    assert apex_t == pytest.approx(6.4645375892102075)
    assert apex_t != t_arr[max(range(len(alt_arr)), key=lambda i: alt_arr[i])]


def test_apex_time_falls_back_to_argmax_altitude_without_apogee_event():
    t_arr = [0.0, 1.0, 2.0]
    alt_arr = [10.0, 30.0, 20.0]
    apex_t = apex_time_from_apogee_events([], t_arr, alt_arr)
    assert apex_t == pytest.approx(1.0)


def test_apex_time_uses_earliest_apogee_event_if_multiple():
    apex_t = apex_time_from_apogee_events([12.0, 6.5], t_arr=None, alt_arr=None)
    assert apex_t == pytest.approx(6.5)


def test_descent_profile_apex_hint_must_come_from_free_run_not_powered_branch():
    # Regression for a second apex-detection failure mode found alongside the
    # first: if ignition happens close enough to (or before) the natural
    # apogee, the burn's own added impulse can delay the POWERED branch's
    # single true APOGEE event past burnout entirely (measured case: burn
    # window 8.736-10.431s, but that branch's only APOGEE event fires at
    # 15.13s). Recomputing apex_t from the powered branch's own event -- even
    # via apex_time_from_apogee_events -- would still discard the whole burn
    # window. The free-descent run's own (unpowered, uncorrupted) apex_t must
    # be reused instead. This test asserts that using the corrupted powered-
    # branch apex silently drops burn samples, while using the free-run apex
    # recovers them -- documenting why callers must pass apex_t_hint.
    t_arr = [8.60, 8.70, 8.7467, 8.90, 10.0, 10.431, 12.0, 15.128]
    thrust_arr = [0.0, 0.0, 218.762, 667.505, 559.0, 0.0, 0.0, 0.0]
    free_run_apex_t = 8.436  # from the unpowered baseline, before this burn
    corrupted_powered_apex_t = 15.128  # this branch's own (delayed) APOGEE event

    def count_burn_samples_seen(apex_t):
        return sum(1 for t, th in zip(t_arr, thrust_arr) if t >= apex_t and th > 1.0)

    assert count_burn_samples_seen(corrupted_powered_apex_t) == 0
    assert count_burn_samples_seen(free_run_apex_t) == 3


def test_q_components_tail_first_straight_down():
    # theta=+90deg (nose straight up), falling straight down (vz<0):
    # nose opposes velocity -> q_total should be +1.
    theta = math.radians(90.0)
    phi = 0.0
    q_total, q_h, q_v = q_components(theta, phi, vx=0.0, vy=0.0, vz=-10.0, speed=10.0)
    assert q_total == pytest.approx(1.0, abs=1e-6)


def test_q_components_nose_first_straight_down():
    # theta=-90deg (nose straight down), falling straight down: nose aligned
    # with velocity -> q_total should be -1.
    theta = math.radians(-90.0)
    phi = 0.0
    q_total, q_h, q_v = q_components(theta, phi, vx=0.0, vy=0.0, vz=-10.0, speed=10.0)
    assert q_total == pytest.approx(-1.0, abs=1e-6)


def test_q_components_zero_speed_returns_none():
    assert q_components(0.0, 0.0, 0.0, 0.0, 0.0, 0.0) == (None, None, None)


def _make_free_fall_samples(n=20, dt=0.05, start_t=8.5):
    """Synthetic tail-first free descent: constant theta=90deg, no thrust."""
    samples = []
    for i in range(n):
        t = start_t + i * dt
        samples.append({
            "t": t, "q_total": 1.0, "q_horizontal": 0.0, "q_vertical": 1.0,
            "angular_rate_norm": 0.0, "speed_ms": 10.0, "horizontal_speed_ms": 0.0,
            "thrust_n": 0.0,
        })
    return samples


def test_impulse_opposition_fraction_pure_braking():
    samples = _make_free_fall_samples()
    for s in samples:
        s["thrust_n"] = 100.0  # thrust present, q_total=1.0 (fully opposing)
    frac, meta = impulse_opposition_fraction(samples)
    assert frac == pytest.approx(1.0, abs=1e-6)


def test_impulse_opposition_fraction_pure_acceleration():
    samples = _make_free_fall_samples()
    for s in samples:
        s["thrust_n"] = 100.0
        s["q_total"] = -1.0  # thrust co-aligned with velocity (accelerating)
    frac, meta = impulse_opposition_fraction(samples)
    assert frac == pytest.approx(0.0, abs=1e-6)


def test_impulse_opposition_fraction_no_burn_returns_none():
    samples = _make_free_fall_samples()  # thrust_n = 0.0 throughout
    frac, meta = impulse_opposition_fraction(samples)
    assert frac is None


def test_passive_descent_admission_rejects_short_window():
    # Only 0.5s of positive-q window, but motor needs 2s to burn.
    samples = _make_free_fall_samples(n=10, dt=0.05)  # 0.5s span
    admitted, reasons, metrics = passive_descent_admission(samples, motor_burn_duration_s=2.0)
    assert not admitted
    assert any("braking window" in r for r in reasons)


def test_passive_descent_admission_accepts_good_window():
    samples = _make_free_fall_samples(n=100, dt=0.05)  # 5s span, all q=1.0
    for s in samples:
        s["q_horizontal"] = 0.5
        s["horizontal_speed_ms"] = 5.0
    admitted, reasons, metrics = passive_descent_admission(samples, motor_burn_duration_s=2.0)
    assert admitted
    assert reasons == []


def test_passive_descent_admission_rejects_high_horizontal_speed():
    samples = _make_free_fall_samples(n=100, dt=0.05)
    for s in samples:
        s["q_horizontal"] = 0.5
        s["horizontal_speed_ms"] = 50.0
    admitted, reasons, metrics = passive_descent_admission(samples, motor_burn_duration_s=2.0)
    assert not admitted
    assert any("horizontal speed too high" in r for r in reasons)


def test_passive_descent_admission_rejects_adverse_mean_q():
    samples = _make_free_fall_samples(n=100, dt=0.05)
    for s in samples:
        s["q_total"] = -0.9
        s["q_horizontal"] = 0.1
    admitted, reasons, metrics = passive_descent_admission(samples, motor_burn_duration_s=2.0)
    assert not admitted
    assert any("mean q_total" in r for r in reasons)


def test_powered_early_stop_suspends_on_low_impulse_fraction():
    watchdog = PoweredEarlyStop()
    watchdog.record("cand1", "J350W", 10.0, i_opp_over_i_total=0.06,
                     powered_speed_ms=57.0, free_descent_speed_ms=14.0,
                     burn_mean_q=-0.9, flip_detected=True)
    suspend, reason = watchdog.should_suspend("J350W")
    assert suspend
    assert "I_opp/I_total" in reason


def test_powered_early_stop_suspends_on_repeated_worse_than_free_descent():
    watchdog = PoweredEarlyStop()
    watchdog.record("cand1", "H180W", 9.0, i_opp_over_i_total=0.4,
                     powered_speed_ms=40.0, free_descent_speed_ms=14.0,
                     burn_mean_q=0.1, flip_detected=False)
    watchdog.record("cand1", "H180W", 12.0, i_opp_over_i_total=0.4,
                     powered_speed_ms=45.0, free_descent_speed_ms=14.0,
                     burn_mean_q=0.1, flip_detected=False)
    suspend, reason = watchdog.should_suspend("H180W")
    assert suspend
    assert "worse than free descent" in reason


def test_powered_early_stop_suspends_on_repeated_flip():
    watchdog = PoweredEarlyStop()
    watchdog.record("cand1", "J350W", 9.5, i_opp_over_i_total=0.4,
                     powered_speed_ms=10.0, free_descent_speed_ms=14.0,
                     burn_mean_q=0.5, flip_detected=True)
    watchdog.record("cand1", "J350W", 15.0, i_opp_over_i_total=0.4,
                     powered_speed_ms=10.0, free_descent_speed_ms=14.0,
                     burn_mean_q=0.5, flip_detected=True)
    suspend, reason = watchdog.should_suspend("J350W")
    assert suspend
    assert "distinct ignition windows" in reason


def test_powered_early_stop_does_not_suspend_good_candidate():
    watchdog = PoweredEarlyStop()
    watchdog.record("cand1", "H180W", 9.5, i_opp_over_i_total=0.8,
                     powered_speed_ms=4.0, free_descent_speed_ms=14.0,
                     burn_mean_q=0.9, flip_detected=False)
    suspend, reason = watchdog.should_suspend("H180W")
    assert not suspend


def test_powered_early_stop_is_per_motor():
    watchdog = PoweredEarlyStop()
    watchdog.record("cand1", "J350W", 10.0, i_opp_over_i_total=0.06,
                     powered_speed_ms=57.0, free_descent_speed_ms=14.0,
                     burn_mean_q=-0.9, flip_detected=True)
    # A different motor on the same candidate has no data yet -> not suspended.
    suspend, reason = watchdog.should_suspend("H180W")
    assert not suspend
