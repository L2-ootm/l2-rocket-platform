use super::guidance::guidance_pitch;
use super::pid::Pid;
use crate::sim_core::dynamics::state::{GncCommand, State};
use crate::sim_core::vehicle::Mission;

// ---------------------------------------------------------------------------
// TVC Controller: guidance + PID control combined
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct TvcController {
    pub pitch_pid: Pid,
    pub yaw_pid: Pid,
}

impl TvcController {
    pub fn new() -> Self {
        Self {
            // Tuned for typical sounding rocket (Ixx~5, nozzle_offset~1m)
            pitch_pid: Pid::new(2.0, 0.1, 0.5),
            yaw_pid: Pid::new(2.0, 0.1, 0.5),
        }
    }

    /// Compute GNC command from current state and mission.
    pub fn update(&mut self, state: &State, mission: &Mission, dt: f64) -> GncCommand {
        let desired_pitch = guidance_pitch(state, mission);
        let current_pitch = state.pitch();
        let pitch_error = desired_pitch - current_pitch;

        // Yaw: keep zero (no lateral steering for now)
        let body_z_inertial = state.body_z();
        let yaw_error = -body_z_inertial
            .x
            .atan2((body_z_inertial.y.powi(2) + body_z_inertial.z.powi(2)).sqrt());

        let gy = self.pitch_pid.update(pitch_error, dt);
        let gz = self.yaw_pid.update(yaw_error, dt);

        GncCommand {
            gimbal_y: gy,
            gimbal_z: gz,
        }
    }

    pub fn reset(&mut self) {
        self.pitch_pid.reset();
        self.yaw_pid.reset();
    }
}

impl Default for TvcController {
    fn default() -> Self {
        Self::new()
    }
}

impl super::Controller for TvcController {
    fn control(&mut self, state: &State, mission: &Mission, dt: f64) -> GncCommand {
        self.update(state, mission, dt)
    }

    fn reset(&mut self) {
        TvcController::reset(self);
    }

    fn name(&self) -> &str {
        "TvcController"
    }
}

/// Backward-compatible type alias.
pub type GncSystem = TvcController;
