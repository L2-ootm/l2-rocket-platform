use std::io::{self, Write};

use crate::sim_core::dynamics::state::State;

/// Write trajectory data to CSV format.
///
/// Columns: time, pos_x, pos_y, pos_z, vel_x, vel_y, vel_z,
///          quat_w, quat_x, quat_y, quat_z, omega_x, omega_y, omega_z,
///          mass, stage_idx, pitch_deg, alpha_deg
pub fn write_trajectory<W: Write>(writer: &mut W, trajectory: &[State]) -> io::Result<()> {
    writeln!(
        writer,
        "time,pos_x,pos_y,pos_z,vel_x,vel_y,vel_z,\
         quat_w,quat_x,quat_y,quat_z,omega_x,omega_y,omega_z,\
         mass,stage_idx,pitch_deg,alpha_deg"
    )?;

    for s in trajectory {
        let q = s.quat.quaternion();
        writeln!(
            writer,
            "{:.4},{:.4},{:.4},{:.4},{:.4},{:.4},{:.4},\
             {:.6},{:.6},{:.6},{:.6},{:.6},{:.6},{:.6},\
             {:.4},{},{:.2},{:.2}",
            s.time,
            s.pos.x,
            s.pos.y,
            s.pos.z,
            s.vel.x,
            s.vel.y,
            s.vel.z,
            q.w,
            q.i,
            q.j,
            q.k,
            s.omega.x,
            s.omega.y,
            s.omega.z,
            s.mass,
            s.stage_idx,
            s.pitch().to_degrees(),
            s.alpha().to_degrees(),
        )?;
    }

    Ok(())
}

/// Write trajectory to a CSV file at the given path.
pub fn write_trajectory_file(path: &str, trajectory: &[State]) -> io::Result<()> {
    let mut file = std::fs::File::create(path)?;
    write_trajectory(&mut file, trajectory)
}

#[cfg(test)]
mod tests {
    use super::*;
    use nalgebra::{UnitQuaternion, Vector3};

    #[test]
    fn csv_output_has_header_and_rows() {
        let traj = vec![
            State {
                time: 0.0,
                pos: Vector3::zeros(),
                vel: Vector3::zeros(),
                quat: UnitQuaternion::identity(),
                omega: Vector3::zeros(),
                mass: 100.0,
                stage_idx: 0,
                stage_activated_at: 0.0,
                stage_depleted_at: None,
                parachute_deployed: false,
            },
            State {
                time: 0.005,
                pos: Vector3::new(0.0, 0.0, 1.0),
                vel: Vector3::new(0.0, 0.0, 50.0),
                quat: UnitQuaternion::identity(),
                omega: Vector3::zeros(),
                mass: 99.5,
                stage_idx: 0,
                stage_activated_at: 0.0,
                stage_depleted_at: None,
                parachute_deployed: false,
            },
        ];

        let mut buf = Vec::new();
        write_trajectory(&mut buf, &traj).unwrap();
        let output = String::from_utf8(buf).unwrap();
        let lines: Vec<&str> = output.lines().collect();

        assert!(lines[0].starts_with("time,"));
        assert_eq!(lines.len(), 3); // header + 2 data rows
        assert!(lines[1].starts_with("0.0000,"));
    }
}
