"""Passive-descent ranking and powered-admission/early-stop gates.

Mission requirement (live-insert 2026-07-20): ascent metrics (OpenRocket
static margin, scalar CP, fin count/area) must never be used as the primary
descent ranking. Tail-first descent is a high-angle/reverse-flow moment
problem; candidates are ranked and gated purely on measured attitude/moment/
impulse quantities: q_total(t), q_horizontal(t), q_vertical(t), braking
alignment angle, angular-rate norm, horizontal speed, and full-burn opposing
impulse fraction.

Ascent-only gates (legal staging, `min_static_margin`, `max_mach`) stay in
`osifog_sweep.py`'s existing hard-constraint validator and are untouched
here — this module only ever ranks/gates the *descent* phase.
"""
import math

from osifog_sweep import _finite_difference


def apex_time_from_apogee_events(apogee_event_times, t_arr=None, alt_arr=None):
    """Natural (pre-ignition) apex time, from OpenRocket's own APOGEE event.

    Do not use argmax(altitude) for this: a retro burn that fires close
    enough to the natural apogee to still be climbing can add enough net-
    upward impulse to create a second, *higher* altitude peak later in the
    flight (measured case: +21 m, +3.7 s vs. the true apogee). Picking the
    global altitude maximum then silently selects that later peak, and every
    "if t < apex_t" descent-window filter downstream discards the entire
    burn window -- making a motor that ignited exactly on schedule look like
    it never fired. Falls back to argmax(altitude) only when no APOGEE event
    is present in the branch (should not happen for a normal flight).
    """
    if apogee_event_times:
        return min(apogee_event_times)
    apex_idx = max(range(len(alt_arr)), key=lambda i: float(alt_arr[i]))
    return float(t_arr[apex_idx])


def q_components(theta, phi, vx, vy, vz, speed):
    """q_total, q_horizontal, q_vertical from nose-vector/velocity dot products.

    Convention matches `_descent_alignment_diagnostic` in osifog_sweep.py:
    positive q means the nose points opposite the velocity (tail-first).
    """
    if speed <= 1.0e-9:
        return None, None, None
    cos_th = math.cos(theta)
    nose_x = cos_th * math.sin(phi)
    nose_y = cos_th * math.cos(phi)
    nose_z = math.sin(theta)
    q_total = -(nose_x * vx + nose_y * vy + nose_z * vz) / speed
    h_speed = math.hypot(vx, vy)
    q_horizontal = -(nose_x * vx + nose_y * vy) / h_speed if h_speed > 1.0e-9 else 0.0
    q_vertical = -(nose_z * vz) / abs(vz) if abs(vz) > 1.0e-9 else 0.0
    return q_total, q_horizontal, q_vertical


def descent_profile(t_arr, px_arr, py_arr, vz_arr, theta_arr, phi_arr, thrust_arr,
                     apex_t, hit_t=None):
    """Per-sample descent trace: q components, angular-rate norm, thrust, speed."""
    n = min(len(t_arr), len(px_arr), len(py_arr), len(vz_arr), len(theta_arr),
            len(phi_arr), len(thrust_arr))
    samples = []
    prev_theta = prev_phi = prev_t = None
    for i in range(n):
        t = float(t_arr[i])
        if t < apex_t:
            continue
        if hit_t is not None and t > hit_t:
            break
        vz = float(vz_arr[i])
        theta = float(theta_arr[i])
        phi = float(phi_arr[i])
        vx = _finite_difference(px_arr, t_arr, i)
        vy = _finite_difference(py_arr, t_arr, i)
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        q_total, q_h, q_v = q_components(theta, phi, vx, vy, vz, speed)
        rate = 0.0
        if prev_theta is not None and prev_t is not None and t > prev_t:
            dt = t - prev_t
            rate = math.hypot((theta - prev_theta) / dt, (phi - prev_phi) / dt)
        prev_theta, prev_phi, prev_t = theta, phi, t
        samples.append({
            "t": t,
            "q_total": q_total,
            "q_horizontal": q_h,
            "q_vertical": q_v,
            "angular_rate_norm": rate,
            "speed_ms": speed,
            "horizontal_speed_ms": math.hypot(vx, vy),
            "thrust_n": float(thrust_arr[i]),
        })
    return samples


def impulse_opposition_fraction(samples):
    """I_opp / I_total over samples with thrust > 1 N.

    I_total is the thrust-time impulse actually delivered during the burn.
    I_opp is the portion of that impulse whose direction opposed the
    velocity vector (the only portion that is genuine braking). Using an
    impulse-weighted fraction rather than a sample-count fraction avoids
    over- or under-crediting a burn whose thrust profile is not flat.
    """
    burn = [s for s in samples if s["thrust_n"] > 1.0]
    if len(burn) < 2:
        return None, {"sample_count": len(burn)}
    i_total = 0.0
    i_opp = 0.0
    for a, b in zip(burn, burn[1:]):
        dt = b["t"] - a["t"]
        if dt <= 0:
            continue
        thrust = 0.5 * (a["thrust_n"] + b["thrust_n"])
        q = 0.5 * ((a["q_total"] or 0.0) + (b["q_total"] or 0.0))
        i_total += thrust * dt
        if q > 0:
            i_opp += thrust * dt * q
    if i_total <= 0:
        return None, {"sample_count": len(burn)}
    return i_opp / i_total, {"sample_count": len(burn), "i_total_ns": i_total, "i_opp_ns": i_opp}


def passive_descent_admission(samples, motor_burn_duration_s):
    """Hard gate (live-insert section 3): may this candidate see a powered sweep?

    Requires all of:
      - a sustained post-apex q_total > 0.3 window at least as long as the
        motor's burn duration (a burn that can't fit inside the window can't
        possibly brake throughout it);
      - positive full-window mean q_total and q_horizontal;
      - bounded horizontal speed (>30 m/s makes <5 m/s touchdown implausible
        regardless of vertical braking);
      - bounded mean angular-rate norm.

    Returns (admitted: bool, reasons: list[str], metrics: dict). `reasons` is
    empty iff admitted.
    """
    if not samples:
        return False, ["no descent samples"], {}

    q_vals = [s["q_total"] for s in samples if s["q_total"] is not None]
    qh_vals = [s["q_horizontal"] for s in samples if s["q_horizontal"] is not None]
    rate_vals = [s["angular_rate_norm"] for s in samples]
    hspeed_vals = [s["horizontal_speed_ms"] for s in samples]

    mean_q = sum(q_vals) / len(q_vals) if q_vals else -1.0
    mean_qh = sum(qh_vals) / len(qh_vals) if qh_vals else -1.0
    mean_rate = sum(rate_vals) / len(rate_vals) if rate_vals else 999.0
    max_hspeed = max(hspeed_vals) if hspeed_vals else 999.0

    best_run = 0.0
    run_start = None
    for s in samples:
        if s["q_total"] is not None and s["q_total"] > 0.3:
            if run_start is None:
                run_start = s["t"]
            best_run = max(best_run, s["t"] - run_start)
        else:
            run_start = None

    metrics = {
        "mean_q_total": mean_q,
        "mean_q_horizontal": mean_qh,
        "mean_angular_rate_norm": mean_rate,
        "max_horizontal_speed_ms": max_hspeed,
        "sustained_positive_q_window_s": best_run,
    }

    reasons = []
    if best_run < motor_burn_duration_s:
        reasons.append(
            f"no sustained braking window >= motor burn duration "
            f"({best_run:.2f}s available vs {motor_burn_duration_s:.2f}s needed)"
        )
    if mean_q <= 0:
        reasons.append(f"full-window mean q_total is not positive ({mean_q:.3f})")
    if mean_qh <= 0:
        reasons.append(f"full-window mean q_horizontal is not positive ({mean_qh:.3f})")
    if max_hspeed > 30.0:
        reasons.append(
            f"horizontal speed too high for <5 m/s touchdown to be plausible "
            f"({max_hspeed:.1f} m/s)"
        )
    if mean_rate > 3.0:
        reasons.append(f"angular rate too high ({mean_rate:.2f} rad/s mean norm)")

    return (len(reasons) == 0), reasons, metrics


class PoweredEarlyStop:
    """Watchdog: suspend a candidate/motor pair after 1-3 diagnostic powered runs.

    Thresholds are the mission's initial watchdog values, not tuned
    constants -- they are recorded per run (`self.runs`) so they can be
    recalibrated later from observed false positive/negative rates against
    full OpenRocket authority results.
    """

    IMPULSE_FRACTION_THRESHOLD = 0.25
    MAX_DIAGNOSTIC_RUNS = 3

    def __init__(self):
        self.runs = []

    def record(self, label, motor, delay_s, i_opp_over_i_total, powered_speed_ms,
               free_descent_speed_ms, burn_mean_q, flip_detected):
        self.runs.append({
            "label": label,
            "motor": motor,
            "delay_s": delay_s,
            "i_opp_over_i_total": i_opp_over_i_total,
            "powered_speed_ms": powered_speed_ms,
            "free_descent_speed_ms": free_descent_speed_ms,
            "burn_mean_q": burn_mean_q,
            "flip_detected": flip_detected,
        })

    def should_suspend(self, motor):
        runs = [r for r in self.runs if r["motor"] == motor]
        if not runs:
            return False, "no data yet"
        latest = runs[-1]
        if latest["i_opp_over_i_total"] is not None and \
                latest["i_opp_over_i_total"] < self.IMPULSE_FRACTION_THRESHOLD:
            return True, (
                f"I_opp/I_total={latest['i_opp_over_i_total']:.3f} < "
                f"{self.IMPULSE_FRACTION_THRESHOLD} threshold"
            )
        worse = [
            r for r in runs
            if r["powered_speed_ms"] is not None and r["free_descent_speed_ms"] is not None
            and r["powered_speed_ms"] > r["free_descent_speed_ms"]
        ]
        if len(worse) >= 2:
            return True, f"powered speed worse than free descent in {len(worse)}/{len(runs)} runs"
        flips = [r for r in runs if r["flip_detected"]]
        if len(flips) >= 2:
            return True, f"same motor-on flip observed in {len(flips)} distinct ignition windows"
        if len(runs) >= self.MAX_DIAGNOSTIC_RUNS:
            adverse = [r for r in runs if r["burn_mean_q"] is not None and r["burn_mean_q"] < 0]
            if len(adverse) >= 2:
                return True, "q_total adverse through most of burn across diagnostic runs"
            return True, f"reached MAX_DIAGNOSTIC_RUNS ({self.MAX_DIAGNOSTIC_RUNS}) with no braking basin found"
        return False, "no local braking basin ruled out yet"
