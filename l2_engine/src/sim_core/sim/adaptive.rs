//! OpenRocket-compatible adaptive time-step selection proof of concept.
//!
//! This module intentionally does not alter the production runner yet. It
//! isolates the selection policy so event scheduling and six-DOF integration
//! can adopt it behind a physics-mode switch without changing RK4 itself.

#[derive(Debug, Clone, Copy)]
pub struct AdaptiveStepInputs {
    pub user_dt: f64,
    pub distance_to_event: f64,
    pub maximum_angle_step: f64,
    pub lateral_pitch_rate: f64,
    pub roll_rate: f64,
    pub roll_acceleration: f64,
    pub lateral_angular_acceleration: f64,
    pub on_launch_rod: bool,
    pub launch_rod_length: f64,
    pub speed: f64,
    pub previous_dt: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepLimit {
    User,
    Event,
    PitchAngle,
    RollAngle,
    RollRateChange,
    PitchYawRateChange,
    LaunchRodDistance,
    Growth,
}

#[derive(Debug, Clone, Copy)]
pub struct SelectedStep {
    pub dt: f64,
    pub limit: StepLimit,
}

const MIN_TIME_STEP: f64 = 1.0e-5;
const MAX_ROLL_STEP_ANGLE: f64 = 2.0 * 28.32_f64.to_radians();
const MAX_ROLL_RATE_CHANGE: f64 = 2.0_f64.to_radians();
const MAX_PITCH_YAW_CHANGE: f64 = 4.0_f64.to_radians();

fn positive_ratio(numerator: f64, denominator: f64) -> f64 {
    if denominator.abs() > 1e-15 {
        (numerator / denominator).abs()
    } else {
        f64::INFINITY
    }
}

/// Select a step using the eight constraints in OpenRocket 24.12's
/// `RK4SimulationStepper.step()`.
pub fn select_time_step(input: AdaptiveStepInputs) -> SelectedStep {
    let user_dt = input.user_dt.max(MIN_TIME_STEP);
    let mut candidates = [
        (user_dt, StepLimit::User),
        (input.distance_to_event.max(0.0), StepLimit::Event),
        (
            positive_ratio(input.maximum_angle_step, input.lateral_pitch_rate),
            StepLimit::PitchAngle,
        ),
        (
            positive_ratio(MAX_ROLL_STEP_ANGLE, input.roll_rate),
            StepLimit::RollAngle,
        ),
        (
            positive_ratio(MAX_ROLL_RATE_CHANGE, input.roll_acceleration),
            StepLimit::RollRateChange,
        ),
        (
            positive_ratio(MAX_PITCH_YAW_CHANGE, input.lateral_angular_acceleration),
            StepLimit::PitchYawRateChange,
        ),
        (f64::INFINITY, StepLimit::LaunchRodDistance),
        (1.5 * input.previous_dt, StepLimit::Growth),
    ];

    if input.on_launch_rod {
        candidates[0].0 /= 5.0;
        candidates[6].0 = positive_ratio(input.launch_rod_length / 10.0, input.speed);
    }

    let (mut dt, mut limit) = candidates
        .into_iter()
        .min_by(|a, b| a.0.total_cmp(&b.0))
        .expect("eight timestep candidates");

    let minimum_dt = user_dt / 20.0;
    if (input.distance_to_event - dt).abs() < minimum_dt {
        dt = input.distance_to_event;
        limit = StepLimit::Event;
    }
    if dt < minimum_dt {
        dt = minimum_dt;
    }

    SelectedStep { dt, limit }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unconstrained_inputs() -> AdaptiveStepInputs {
        AdaptiveStepInputs {
            user_dt: 0.05,
            distance_to_event: f64::INFINITY,
            maximum_angle_step: 3.0_f64.to_radians(),
            lateral_pitch_rate: 0.0,
            roll_rate: 0.0,
            roll_acceleration: 0.0,
            lateral_angular_acceleration: 0.0,
            on_launch_rod: false,
            launch_rod_length: 2.0,
            speed: 0.0,
            previous_dt: f64::INFINITY,
        }
    }

    #[test]
    fn selector_exercises_all_eight_openrocket_constraints() {
        let base = unconstrained_inputs();
        assert_eq!(select_time_step(base).limit, StepLimit::User);

        let mut input = base;
        input.distance_to_event = 0.02;
        assert_eq!(select_time_step(input).limit, StepLimit::Event);

        input = base;
        input.lateral_pitch_rate = 2.0;
        assert_eq!(select_time_step(input).limit, StepLimit::PitchAngle);

        input = base;
        input.roll_rate = 40.0;
        assert_eq!(select_time_step(input).limit, StepLimit::RollAngle);

        input = base;
        input.roll_acceleration = 4.0;
        assert_eq!(select_time_step(input).limit, StepLimit::RollRateChange);

        input = base;
        input.lateral_angular_acceleration = 8.0;
        assert_eq!(select_time_step(input).limit, StepLimit::PitchYawRateChange);

        input = base;
        input.on_launch_rod = true;
        input.speed = 80.0;
        assert_eq!(select_time_step(input).limit, StepLimit::LaunchRodDistance);

        input = base;
        input.previous_dt = 0.01;
        assert_eq!(select_time_step(input).limit, StepLimit::Growth);
    }

    #[derive(Clone, Copy)]
    struct BallisticState {
        t: f64,
        z: f64,
        v: f64,
    }

    fn acceleration(t: f64, burnout: f64) -> f64 {
        if t <= burnout { 20.0 } else { -9.80665 }
    }

    fn rk4_ballistic(s: BallisticState, dt: f64, burnout: f64) -> BallisticState {
        let a1 = acceleration(s.t, burnout);
        let v2 = s.v + a1 * dt * 0.5;
        let a2 = acceleration(s.t + dt * 0.5, burnout);
        let v3 = s.v + a2 * dt * 0.5;
        let a3 = acceleration(s.t + dt * 0.5, burnout);
        let v4 = s.v + a3 * dt;
        let a4 = acceleration(s.t + dt, burnout);
        BallisticState {
            t: s.t + dt,
            z: s.z + (s.v + 2.0 * v2 + 2.0 * v3 + v4) * dt / 6.0,
            v: s.v + (a1 + 2.0 * a2 + 2.0 * a3 + a4) * dt / 6.0,
        }
    }

    fn run(adaptive: bool) -> (f64, Vec<(f64, f64, StepLimit)>) {
        let burnout: f64 = 1.03;
        let user_dt = 0.05;
        let mut previous_dt = user_dt;
        let mut state = BallisticState {
            t: 0.0,
            z: 0.0,
            v: 0.0,
        };
        let mut history = Vec::new();
        while state.v >= 0.0 || state.t <= burnout {
            let next_event = if state.t < burnout {
                burnout - state.t
            } else {
                (state.v / 9.80665).max(0.0)
            };
            let selected = if adaptive {
                select_time_step(AdaptiveStepInputs {
                    user_dt,
                    distance_to_event: next_event,
                    maximum_angle_step: 3.0_f64.to_radians(),
                    lateral_pitch_rate: 0.0,
                    roll_rate: 0.0,
                    roll_acceleration: 0.0,
                    lateral_angular_acceleration: 0.0,
                    on_launch_rod: false,
                    launch_rod_length: 0.0,
                    speed: state.v.abs(),
                    previous_dt,
                })
            } else {
                SelectedStep {
                    dt: user_dt,
                    limit: StepLimit::User,
                }
            };
            history.push((state.t, selected.dt, selected.limit));
            previous_dt = selected.dt;
            state = rk4_ballistic(state, selected.dt, burnout);
            if history.len() > 10_000 {
                panic!("ballistic proof did not terminate");
            }
        }
        (state.z, history)
    }

    #[test]
    fn adaptive_selector_reduces_dt_at_burnout_and_apogee() {
        let (_apogee, history) = run(true);
        assert!(history.iter().any(|(t, dt, limit)| {
            (*t - 1.0).abs() < 1e-12 && (*dt - 0.03).abs() < 1e-12 && *limit == StepLimit::Event
        }));
        assert!(
            history
                .iter()
                .any(|(_, dt, limit)| *dt < 0.01 && *limit == StepLimit::Event)
        );
    }

    #[test]
    fn adaptive_ballistic_apogee_is_closer_than_fixed_step() {
        let burnout: f64 = 1.03;
        let burnout_velocity: f64 = 20.0 * burnout;
        let expected = 0.5 * 20.0 * burnout.powi(2) + burnout_velocity.powi(2) / (2.0 * 9.80665);
        let (adaptive, _) = run(true);
        let (fixed, _) = run(false);
        println!(
            "expected={expected:.9} adaptive={adaptive:.9} fixed={fixed:.9} adaptive_error={:.9} fixed_error={:.9}",
            (adaptive - expected).abs(),
            (fixed - expected).abs()
        );
        assert!((adaptive - expected).abs() < (fixed - expected).abs());
    }
}
