use nalgebra::Vector3;

use crate::sim_core::dynamics::state::{EARTH_RADIUS, G0};

/// Inverse-square gravity acceleration (inertial frame, ENU).
/// Returns gravitational acceleration vector for a given altitude.
pub fn gravity_accel(altitude: f64) -> Vector3<f64> {
    let alt = altitude.max(0.0);
    let g = G0 * (EARTH_RADIUS / (EARTH_RADIUS + alt)).powi(2);
    Vector3::new(0.0, 0.0, -g)
}

/// Gravitational force on a body at given altitude (inertial frame, ENU).
pub fn gravity_force(altitude: f64, mass: f64) -> Vector3<f64> {
    gravity_accel(altitude) * mass
}

// ---------------------------------------------------------------------------
// J2 perturbation (ECI frame — used by orbital propagator)
// ---------------------------------------------------------------------------

pub const MU_EARTH: f64 = 3.986_004_418e14; // m^3/s^2
pub const R_EARTH_ECI: f64 = 6_378_137.0; // equatorial radius, m
pub const J2_EARTH: f64 = 1.082_63e-3;

/// J2 gravitational acceleration in ECI frame.
/// `pos` is the position vector in ECI coordinates (m).
pub fn gravity_j2_eci(pos: &Vector3<f64>) -> Vector3<f64> {
    let r = pos.norm();
    if r < 1.0 {
        return Vector3::zeros();
    }
    let r2 = r * r;
    let z2 = pos.z * pos.z;

    let mu_over_r3 = MU_EARTH / (r2 * r);
    let j2_coeff = 1.5 * J2_EARTH * R_EARTH_ECI * R_EARTH_ECI / r2;

    let xy_factor = mu_over_r3 * (1.0 + j2_coeff * (1.0 - 5.0 * z2 / r2));
    let z_factor = mu_over_r3 * (1.0 + j2_coeff * (3.0 - 5.0 * z2 / r2));

    Vector3::new(-xy_factor * pos.x, -xy_factor * pos.y, -z_factor * pos.z)
}

// Simplified: point-mass gravity in ECI (no J2)
pub fn gravity_pointmass_eci(pos: &Vector3<f64>) -> Vector3<f64> {
    let r = pos.norm();
    if r < 1.0 {
        return Vector3::zeros();
    }
    -MU_EARTH / (r * r * r) * pos
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sea_level_gravity() {
        let g = gravity_accel(0.0);
        assert!((g.z + G0).abs() < 1e-6);
    }

    #[test]
    fn gravity_decreases_with_altitude() {
        let g0 = gravity_accel(0.0).z.abs();
        let g100k = gravity_accel(100_000.0).z.abs();
        assert!(g100k < g0);
    }

    #[test]
    fn j2_reduces_to_pointmass_at_equator() {
        let pos = Vector3::new(R_EARTH_ECI + 400_000.0, 0.0, 0.0);
        let a_j2 = gravity_j2_eci(&pos);
        let a_pm = gravity_pointmass_eci(&pos);
        // J2 correction is small (~0.1% at LEO)
        let diff = (a_j2 - a_pm).norm() / a_pm.norm();
        assert!(
            diff < 0.01,
            "J2 correction should be <1% at LEO, got {:.4}%",
            diff * 100.0
        );
    }
}
