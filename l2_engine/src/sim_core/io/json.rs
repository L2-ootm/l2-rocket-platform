use std::io::{self, Write};

use crate::sim_core::dynamics::state::State;
use crate::sim_core::vehicle::Mission;

/// Summary statistics computed from a flight trajectory.
#[derive(Debug, Clone)]
pub struct FlightSummary {
    pub apogee_m: f64,
    pub apogee_time: f64,
    pub max_speed: f64,
    pub max_mach: f64,
    pub max_accel: f64,
    pub max_accel_g: f64,
    pub flight_time: f64,
    pub impact_speed: f64,
    pub apogee_east_m: f64,
    pub apogee_north_m: f64,
    pub stage_landings: Vec<StageLanding>,
    pub total_prop_mass_kg: f64,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct StageLanding {
    pub stage_idx: usize,
    pub touchdown_time_s: f64,
    pub east_m: f64,
    pub north_m: f64,
    pub distance_m: f64,
    pub vz_ms: f64,
    pub vxy_ms: f64,
    pub total_speed_ms: f64,
}

impl FlightSummary {
    pub fn from_trajectory(trajectory: &[State], mission: &Mission) -> Self {
        Self::from_trajectory_with_wind(trajectory, mission)
    }

    /// Compute summary from trajectory data with air-relative speed for
    /// velocity/Mach fields when wind is present.
    pub fn from_trajectory_with_wind(
        trajectory: &[State],
        mission: &Mission,
    ) -> Self {
        let apogee_state = trajectory
            .iter()
            .max_by(|a, b| a.pos.z.partial_cmp(&b.pos.z).unwrap())
            .unwrap();

        let mut max_speed = 0.0_f64;
        let mut max_mach = 0.0_f64;
        
        let get_wind = |alt: f64| -> nalgebra::Vector3<f64> {
            if let Some(wp) = &mission.wind_profile {
                let (e, n) = wp.wind_vector_at(alt);
                nalgebra::Vector3::new(e, n, 0.0)
            } else {
                mission.wind_velocity_mps
            }
        };

        for state in trajectory.iter() {
            let wind = get_wind(state.pos.z.max(0.0));
            let air_speed = (state.vel - wind).norm();
            max_speed = max_speed.max(air_speed);
            
            let sound_speed = mission.atmosphere_at(state.pos.z.max(0.0)).sound_speed;
            max_mach = max_mach.max(air_speed / sound_speed);
        }

        let max_accel = trajectory
            .windows(2)
            .map(|w| {
                let dt = w[1].time - w[0].time;
                if dt > 0.0 {
                    (w[1].vel - w[0].vel).norm() / dt
                } else {
                    0.0
                }
            })
            .fold(0.0_f64, f64::max);

        let last = trajectory.last().unwrap();

        FlightSummary {
            apogee_m: apogee_state.pos.z,
            apogee_time: apogee_state.time,
            max_speed,
            max_mach,
            max_accel,
            max_accel_g: max_accel / 9.80665,
            flight_time: last.time,
            impact_speed: last.vel.norm(),
            apogee_east_m: apogee_state.pos.x,
            apogee_north_m: apogee_state.pos.y,
            stage_landings: vec![], // Populated separately for multi-stage sims
            total_prop_mass_kg: 0.0, // Computed and set separately
        }
    }
}

/// Write flight summary as JSON to a writer.
pub fn write_summary<W: Write>(
    writer: &mut W,
    mission: &Mission,
    summary: &FlightSummary,
) -> io::Result<()> {
    let document = serde_json::json!({
        "mission": {
            "name": mission.name,
            "stages": mission.stages.len(),
        },
        "performance": {
            "apogee_m": summary.apogee_m,
            "apogee_time_s": summary.apogee_time,
            "apogee_east_m": summary.apogee_east_m,
            "apogee_north_m": summary.apogee_north_m,
            "max_speed_ms": summary.max_speed,
            "max_mach": summary.max_mach,
            "max_accel_ms2": summary.max_accel,
            "max_accel_g": summary.max_accel_g,
            "flight_time_s": summary.flight_time,
            "impact_speed_ms": summary.impact_speed,
            "total_prop_mass_kg": summary.total_prop_mass_kg,
        },
        "stage_landings": summary.stage_landings,
    });
    serde_json::to_writer_pretty(&mut *writer, &document).map_err(io::Error::other)?;
    writeln!(writer)
}

/// Write flight summary JSON to a file.
pub fn write_summary_file(
    path: &str,
    mission: &Mission,
    summary: &FlightSummary,
) -> io::Result<()> {
    let mut file = std::fs::File::create(path)?;
    write_summary(&mut file, mission, summary)
}

#[cfg(test)]
mod tests {
    use super::*;
    use nalgebra::{UnitQuaternion, Vector3};

    fn empty_mission() -> Mission {
        Mission {
            name: "Test".into(),
            stages: vec![],
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
        }
    }

    fn simple_trajectory() -> Vec<State> {
        vec![
            State {
                time: 0.0,
                pos: Vector3::zeros(),
                vel: Vector3::new(0.0, 0.0, 100.0),
                quat: UnitQuaternion::identity(),
                omega: Vector3::zeros(),
                mass: 100.0,
                stage_idx: 0,
                stage_activated_at: 0.0,
                stage_depleted_at: None,
                parachute_deployed: false,
            },
            State {
                time: 10.0,
                pos: Vector3::new(0.0, 0.0, 5000.0),
                vel: Vector3::new(0.0, 0.0, 0.0),
                quat: UnitQuaternion::identity(),
                omega: Vector3::zeros(),
                mass: 80.0,
                stage_idx: 0,
                stage_activated_at: 0.0,
                stage_depleted_at: None,
                parachute_deployed: false,
            },
            State {
                time: 20.0,
                pos: Vector3::zeros(),
                vel: Vector3::new(0.0, 0.0, -50.0),
                quat: UnitQuaternion::identity(),
                omega: Vector3::zeros(),
                mass: 80.0,
                stage_idx: 0,
                stage_activated_at: 0.0,
                stage_depleted_at: None,
                parachute_deployed: false,
            },
        ]
    }

    #[test]
    fn summary_computes_apogee() {
        let traj = simple_trajectory();
        let s = FlightSummary::from_trajectory(&traj, &empty_mission());
        assert!((s.apogee_m - 5000.0).abs() < 0.1);
        assert!((s.apogee_time - 10.0).abs() < 0.1);
    }

    #[test]
    fn summary_uses_air_relative_speed_when_wind_is_present() {
        let traj = vec![State {
            time: 0.0,
            pos: Vector3::zeros(),
            vel: Vector3::zeros(),
            quat: UnitQuaternion::identity(),
            omega: Vector3::zeros(),
            mass: 10.0,
            stage_idx: 0,
            stage_activated_at: 0.0,
            stage_depleted_at: None,
            parachute_deployed: false,
        }];

        let wind_mission = Mission {
            name: "Wind".into(),
            stages: vec![],
            wind_velocity_mps: Vector3::new(2.0, 0.0, 0.0),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
        };
        let summary = FlightSummary::from_trajectory_with_wind(&traj, &wind_mission);

        assert_eq!(summary.max_speed, 2.0);
        assert!(summary.max_mach > 0.0);
    }

    #[test]
    fn json_output_is_valid() {
        let traj = simple_trajectory();
        let mission = Mission {
            name: "Test".into(),
            stages: vec![],
            wind_velocity_mps: nalgebra::Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
        };
        let summary = FlightSummary::from_trajectory(&traj, &mission);

        let mut buf = Vec::new();
        write_summary(&mut buf, &mission, &summary).unwrap();
        let json = String::from_utf8(buf).unwrap();
        assert!(json.contains("\"mission\""));
        assert!(json.contains("\"apogee_m\""));
        assert!(json.contains("\"Test\""));
    }
}
