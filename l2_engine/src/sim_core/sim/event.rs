use crate::sim_core::dynamics::state::State;
use crate::sim_core::vehicle::Mission;

const EVENT_TIME_EPSILON: f64 = 1.0e-9;

/// Distance in seconds to the next event whose time is known before stepping.
/// Continuous crossings (launch-guide clearance, apogee and landing) remain
/// post-step detectors because their timestamps require bracketing.
pub fn distance_to_next_scheduled_event(state: &State, mission: &Mission) -> f64 {
    let Some(stage) = mission.active_stage(state.stage_idx) else {
        return f64::INFINITY;
    };

    let ignition_time = state.stage_activated_at + stage.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0);
    let burn_duration = stage
        .motors.first().and_then(|m| m.thrust_curve.last())
        .map_or_else(|| stage.burn_time(), |point| point.0);
    let burnout_time = ignition_time + burn_duration;

    let mut event_times = vec![ignition_time, burnout_time];
    if let Some(depleted_at) = state.stage_depleted_at {
        let separation_time = depleted_at + stage.separation_coast;
        event_times.push(separation_time);
        if state.stage_idx + 1 == mission.stages.len() {
            if let Some(delay) = stage.parachute_delay {
                event_times.push(separation_time + delay);
            }
        }
    }

    event_times
        .into_iter()
        .map(|event_time| event_time - state.time)
        .filter(|distance| *distance > EVENT_TIME_EPSILON)
        .min_by(f64::total_cmp)
        .unwrap_or(f64::INFINITY)
}

/// First-order distance to the continuous apogee crossing. This mirrors the
/// event-distance estimate OpenRocket feeds to its stepper; the passive
/// detector remains responsible for confirming the actual sign crossing.
pub fn distance_to_predicted_apogee(state: &State, vertical_acceleration: f64) -> f64 {
    if state.pos.z > 100.0 && state.vel.z > 0.0 && vertical_acceleration < -1.0e-12 {
        state.vel.z / -vertical_acceleration
    } else {
        f64::INFINITY
    }
}

// ---------------------------------------------------------------------------
// Simulation events
// ---------------------------------------------------------------------------

/// Kinds of simulation events.
#[derive(Debug, Clone, PartialEq)]
pub enum EventKind {
    Launch,
    Burnout { stage: usize },
    Staging { from: usize, to: usize },
    Apogee,
    Landing,
    Custom(String),
}

/// A discrete event that occurred during simulation.
#[derive(Debug, Clone)]
pub struct SimEvent {
    pub time: f64,
    pub kind: EventKind,
    pub state: State,
}

/// Trait for passive event detectors.
/// Implementations inspect consecutive states and report events.
pub trait EventDetector {
    fn check(&mut self, prev: &State, current: &State) -> Option<EventKind>;
}

/// Detects apogee (altitude going from increasing to decreasing).
pub struct ApogeeDetector;

impl EventDetector for ApogeeDetector {
    fn check(&mut self, prev: &State, current: &State) -> Option<EventKind> {
        if prev.vel.z > 0.0 && current.vel.z <= 0.0 && current.pos.z > 100.0 {
            Some(EventKind::Apogee)
        } else {
            None
        }
    }
}

/// Detects when altitude crosses a threshold (ascending or descending).
pub struct AltitudeDetector {
    pub altitude: f64,
    pub ascending: bool,
    fired: bool,
}

impl AltitudeDetector {
    pub fn new(altitude: f64, ascending: bool) -> Self {
        Self {
            altitude,
            ascending,
            fired: false,
        }
    }
}

impl EventDetector for AltitudeDetector {
    fn check(&mut self, prev: &State, current: &State) -> Option<EventKind> {
        if self.fired {
            return None;
        }
        let crossed = if self.ascending {
            prev.pos.z < self.altitude && current.pos.z >= self.altitude
        } else {
            prev.pos.z > self.altitude && current.pos.z <= self.altitude
        };
        if crossed {
            self.fired = true;
            Some(EventKind::Custom(format!(
                "Altitude {:.0}m ({})",
                self.altitude,
                if self.ascending {
                    "ascending"
                } else {
                    "descending"
                }
            )))
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::vehicle::{MissionBuilder, StageBuilder};
    use nalgebra::{UnitQuaternion, Vector3};

    fn make_state(alt: f64, vz: f64) -> State {
        State {
            time: 0.0,
            pos: Vector3::new(0.0, 0.0, alt),
            vel: Vector3::new(0.0, 0.0, vz),
            quat: UnitQuaternion::identity(),
            omega: Vector3::zeros(),
            mass: 100.0,
            stage_idx: 0,
            stage_activated_at: 0.0,
            stage_depleted_at: None,
            parachute_deployed: false,
        }
    }

    #[test]
    fn apogee_detected() {
        let mut det = ApogeeDetector;
        let prev = make_state(5000.0, 10.0);
        let curr = make_state(5005.0, -1.0);
        assert_eq!(det.check(&prev, &curr), Some(EventKind::Apogee));
    }

    #[test]
    fn altitude_detector_ascending() {
        let mut det = AltitudeDetector::new(1000.0, true);
        let prev = make_state(900.0, 100.0);
        let curr = make_state(1050.0, 100.0);
        assert!(det.check(&prev, &curr).is_some());
        // Should not fire again
        assert!(det.check(&prev, &curr).is_none());
    }

    #[test]
    fn scheduled_events_follow_ignition_burnout_and_separation() {
        let mission = MissionBuilder::new("events")
            .stage(
                StageBuilder::new("stage")
                    .ignition_delay(0.3)
                    .thrust_curve(vec![(0.0, 10.0), (1.03, 0.0)])
                    .separation_coast(0.5)
                    .build(),
            )
            .build();
        let mut state = make_state(0.0, 0.0);

        assert!((distance_to_next_scheduled_event(&state, &mission) - 0.3).abs() < 1e-12);
        state.time = 0.3;
        assert!((distance_to_next_scheduled_event(&state, &mission) - 1.03).abs() < 1e-12);
        state.time = 1.33;
        state.stage_depleted_at = Some(1.33);
        assert!((distance_to_next_scheduled_event(&state, &mission) - 0.5).abs() < 1e-12);
    }

    #[test]
    fn predicted_apogee_uses_vertical_rate_and_acceleration() {
        let state = make_state(500.0, 9.80665);
        assert!((distance_to_predicted_apogee(&state, -9.80665) - 1.0).abs() < 1e-12);
        assert!(distance_to_predicted_apogee(&make_state(50.0, 10.0), -9.80665).is_infinite());
        assert!(distance_to_predicted_apogee(&make_state(500.0, -1.0), -9.80665).is_infinite());
    }
}
