use nalgebra::Vector3;

use crate::sim_core::physics::atmosphere::Atmo;

/// Compute aerodynamic drag force (inertial frame, opposing velocity).
pub fn drag_force(vel: &Vector3<f64>, atm: &Atmo, cd: f64, area: f64) -> Vector3<f64> {
    let speed = vel.norm();
    if speed > 1e-6 {
        let q_dyn = 0.5 * atm.density * speed * speed;
        let drag_mag = q_dyn * cd * area;
        -vel.normalize() * drag_mag
    } else {
        Vector3::zeros()
    }
}

/// Compute aerodynamic restoring moment from CP-CG offset (body frame).
/// Returns torque vector in body frame.
pub fn restoring_moment(
    vel_body: &Vector3<f64>,
    speed: f64,
    atm: &Atmo,
    area: f64,
    cp_offset: f64,
    cn_alpha: f64,
) -> Vector3<f64> {
    if speed <= 1.0 || cp_offset.abs() <= 1e-6 {
        return Vector3::zeros();
    }

    let q_dyn = 0.5 * atm.density * speed * speed;
    let alpha_y = vel_body.y.atan2(vel_body.z);
    let alpha_z = vel_body.x.atan2(vel_body.z);
    let normal_force = q_dyn * area * cn_alpha;

    Vector3::new(
        -normal_force * alpha_y * cp_offset,
        normal_force * alpha_z * cp_offset,
        0.0,
    )
}

/// Compute aerodynamic damping torque (body frame, proportional to angular rate).
pub fn damping_moment(omega: &Vector3<f64>, speed: f64, atm: &Atmo, area: f64) -> Vector3<f64> {
    if speed <= 1.0 {
        return Vector3::zeros();
    }
    let q_dyn = 0.5 * atm.density * speed * speed;
    let damp = q_dyn * area * 0.5;
    -omega * damp
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::physics::atmosphere;

    #[test]
    fn drag_opposes_velocity() {
        let vel = Vector3::new(0.0, 0.0, 300.0);
        let atm = atmosphere::isa(0.0);
        let f = drag_force(&vel, &atm, 0.3, 0.01);
        assert!(f.z < 0.0, "Drag should oppose upward velocity");
    }

    #[test]
    fn no_drag_at_rest() {
        let vel = Vector3::zeros();
        let atm = atmosphere::isa(0.0);
        let f = drag_force(&vel, &atm, 0.3, 0.01);
        assert!(f.norm() < 1e-10);
    }

    #[test]
    fn restoring_moment_zero_at_zero_aoa() {
        let vel_body = Vector3::new(0.0, 0.0, 300.0); // aligned with body Z
        let atm = atmosphere::isa(0.0);
        let m = restoring_moment(&vel_body, 300.0, &atm, 0.01, 0.3, 2.0);
        assert!(m.norm() < 1e-6, "No restoring moment at zero AoA");
    }
}
