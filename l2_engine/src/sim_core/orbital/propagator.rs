use nalgebra::Vector3;

use crate::sim_core::physics::gravity::{gravity_j2_eci, gravity_pointmass_eci};

/// Simplified 3DOF orbital state (no attitude).
#[derive(Debug, Clone)]
pub struct OrbitalState {
    pub time: f64,
    pub pos: Vector3<f64>, // m, ECI
    pub vel: Vector3<f64>, // m/s, ECI
}

impl OrbitalState {
    pub fn altitude(&self) -> f64 {
        self.pos.norm() - crate::sim_core::physics::gravity::R_EARTH_ECI
    }

    pub fn speed(&self) -> f64 {
        self.vel.norm()
    }
}

/// RK4 step for orbital propagation.
fn rk4_orbital_step(
    state: &OrbitalState,
    dt: f64,
    accel_fn: &dyn Fn(&Vector3<f64>) -> Vector3<f64>,
) -> OrbitalState {
    let deriv = |pos: &Vector3<f64>, vel: &Vector3<f64>| -> (Vector3<f64>, Vector3<f64>) {
        (vel.clone(), accel_fn(pos))
    };

    let (k1_dr, k1_dv) = deriv(&state.pos, &state.vel);
    let (k2_dr, k2_dv) = deriv(
        &(state.pos + k1_dr * dt * 0.5),
        &(state.vel + k1_dv * dt * 0.5),
    );
    let (k3_dr, k3_dv) = deriv(
        &(state.pos + k2_dr * dt * 0.5),
        &(state.vel + k2_dv * dt * 0.5),
    );
    let (k4_dr, k4_dv) = deriv(&(state.pos + k3_dr * dt), &(state.vel + k3_dv * dt));

    OrbitalState {
        time: state.time + dt,
        pos: state.pos + (k1_dr + 2.0 * k2_dr + 2.0 * k3_dr + k4_dr) * (dt / 6.0),
        vel: state.vel + (k1_dv + 2.0 * k2_dv + 2.0 * k3_dv + k4_dv) * (dt / 6.0),
    }
}

/// Propagate an orbit with optional J2 perturbation.
///
/// Returns trajectory sampled at `dt` intervals for `duration` seconds.
pub fn propagate_orbit(
    initial: &OrbitalState,
    dt: f64,
    duration: f64,
    use_j2: bool,
) -> Vec<OrbitalState> {
    let accel_fn: Box<dyn Fn(&Vector3<f64>) -> Vector3<f64>> = if use_j2 {
        Box::new(|pos: &Vector3<f64>| gravity_j2_eci(pos))
    } else {
        Box::new(|pos: &Vector3<f64>| gravity_pointmass_eci(pos))
    };

    let n_steps = (duration / dt) as usize;
    let mut trajectory = Vec::with_capacity(n_steps + 1);
    let mut state = initial.clone();
    trajectory.push(state.clone());

    for _ in 0..n_steps {
        state = rk4_orbital_step(&state, dt, &*accel_fn);
        trajectory.push(state.clone());
    }

    trajectory
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::physics::gravity::{MU_EARTH, R_EARTH_ECI};

    #[test]
    fn circular_orbit_stays_circular() {
        let r = R_EARTH_ECI + 400_000.0;
        let v = (MU_EARTH / r).sqrt();
        let initial = OrbitalState {
            time: 0.0,
            pos: Vector3::new(r, 0.0, 0.0),
            vel: Vector3::new(0.0, v, 0.0),
        };

        // Propagate one orbit (~92 min)
        let period = 2.0 * std::f64::consts::PI * (r.powi(3) / MU_EARTH).sqrt();
        let traj = propagate_orbit(&initial, 1.0, period, false);
        let last = traj.last().unwrap();

        // Should return close to starting position (RK4 with dt=1s has ~1e-4 relative error)
        let pos_error = (last.pos - initial.pos).norm();
        let orbit_circumference = 2.0 * std::f64::consts::PI * r;
        let relative_error = pos_error / orbit_circumference;
        assert!(
            relative_error < 2e-4,
            "Relative position error after one orbit: {:.2e} ({:.0}m / {:.0}m)",
            relative_error,
            pos_error,
            orbit_circumference
        );
    }

    #[test]
    fn j2_causes_raan_drift() {
        let r = R_EARTH_ECI + 400_000.0;
        let v = (MU_EARTH / r).sqrt();
        // ISS-like inclination
        let inc = 51.6_f64.to_radians();
        let initial = OrbitalState {
            time: 0.0,
            pos: Vector3::new(r, 0.0, 0.0),
            vel: Vector3::new(0.0, v * inc.cos(), v * inc.sin()),
        };

        let period = 2.0 * std::f64::consts::PI * (r.powi(3) / MU_EARTH).sqrt();
        let traj_no_j2 = propagate_orbit(&initial, 1.0, period, false);
        let traj_j2 = propagate_orbit(&initial, 1.0, period, true);

        let pos_no_j2 = traj_no_j2.last().unwrap().pos;
        let pos_j2 = traj_j2.last().unwrap().pos;

        // J2 should cause noticeable difference after one orbit
        let diff = (pos_j2 - pos_no_j2).norm();
        assert!(
            diff > 10.0,
            "J2 should cause measurable position difference, got {:.1} m",
            diff
        );
    }
}
