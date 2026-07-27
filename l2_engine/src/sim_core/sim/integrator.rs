use nalgebra::UnitQuaternion;

use crate::sim_core::dynamics;
use crate::sim_core::dynamics::state::{GncCommand, State};
use crate::sim_core::vehicle::Mission;

// ---------------------------------------------------------------------------
// 6DOF RK4 integrator with constant GNC command over the step
// ---------------------------------------------------------------------------

/// Single RK4 step with constant GNC command over the step.
pub fn rk4_step(state: &State, mission: &Mission, cmd: &GncCommand, dt: f64) -> State {
    let k1 = dynamics::derivatives(state, mission, cmd);
    let k2 = dynamics::derivatives(&state.apply(&k1, dt * 0.5), mission, cmd);
    let k3 = dynamics::derivatives(&state.apply(&k2, dt * 0.5), mission, cmd);
    let k4 = dynamics::derivatives(&state.apply(&k3, dt), mission, cmd);

    let new_quat_raw = state.quat.quaternion()
        + (k1.dquat + k2.dquat * 2.0 + k3.dquat * 2.0 + k4.dquat) * (dt / 6.0);

    State {
        time: state.time + dt,
        pos: state.pos + (k1.dpos + 2.0 * k2.dpos + 2.0 * k3.dpos + k4.dpos) * (dt / 6.0),
        vel: state.vel + (k1.dvel + 2.0 * k2.dvel + 2.0 * k3.dvel + k4.dvel) * (dt / 6.0),
        quat: UnitQuaternion::new_normalize(new_quat_raw),
        omega: state.omega
            + (k1.domega + 2.0 * k2.domega + 2.0 * k3.domega + k4.domega) * (dt / 6.0),
        mass: (state.mass + (k1.dmass + 2.0 * k2.dmass + 2.0 * k3.dmass + k4.dmass) * (dt / 6.0))
            .max(0.0),
        stage_idx: state.stage_idx,
        stage_activated_at: state.stage_activated_at,
        stage_depleted_at: state.stage_depleted_at,
        parachute_deployed: state.parachute_deployed,
    }
}
