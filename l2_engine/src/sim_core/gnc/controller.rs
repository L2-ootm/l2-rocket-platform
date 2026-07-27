use crate::sim_core::dynamics::state::{GncCommand, State};
use crate::sim_core::vehicle::Mission;

/// Trait for flight controllers.
///
/// Implement this to create custom GNC controllers that can be
/// plugged into the simulation loop.
pub trait Controller {
    /// Compute gimbal commands from current state and mission.
    fn control(&mut self, state: &State, mission: &Mission, dt: f64) -> GncCommand;

    /// Reset controller internal state (e.g., PID integrators).
    fn reset(&mut self) {}

    /// Human-readable name for logging/display.
    fn name(&self) -> &str {
        "unnamed"
    }
}
