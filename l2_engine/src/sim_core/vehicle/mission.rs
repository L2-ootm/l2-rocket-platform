use nalgebra::Vector3;

use super::stage::{Stage, StageBuilder};

#[derive(Debug, Clone)]
pub struct LaunchGuide {
    pub length_m: f64,
    pub direction: Vector3<f64>,
}

// ---------------------------------------------------------------------------
// Mission: ordered sequence of stages
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct Mission {
    pub name: String,
    pub stages: Vec<Stage>,
    /// Inertial-frame wind velocity in m/s. Aerodynamics use vehicle velocity
    /// relative to this air mass; zero preserves the original still-air model.
    pub wind_velocity_mps: Vector3<f64>,
    pub wind_profile: Option<crate::sim_core::wind::WindProfile>,
    pub launch_guide: Option<LaunchGuide>,
    pub relative_humidity: f64,
    pub base_temperature_k: f64,
    pub base_pressure_pa: f64,
    pub launch_altitude_m: f64,
}

impl Mission {
    /// Total wet mass of all stages combined.
    pub fn total_mass(&self) -> f64 {
        self.stages.iter().map(|s| s.total_mass()).sum()
    }

    /// Total ideal delta-v (each stage computed with upper stages as payload).
    pub fn total_delta_v(&self) -> f64 {
        let mut dv = 0.0;
        for i in 0..self.stages.len() {
            let payload: f64 = self.stages[i + 1..].iter().map(|s| s.total_mass()).sum();
            dv += self.stages[i].delta_v(payload);
        }
        dv
    }

    /// Get the currently active stage.
    pub fn active_stage(&self, idx: usize) -> Option<&Stage> {
        self.stages.get(idx)
    }
}

// ---------------------------------------------------------------------------
// Mission builder
// ---------------------------------------------------------------------------

pub struct MissionBuilder {
    name: String,
    stages: Vec<Stage>,
    wind_velocity_mps: Vector3<f64>,
    wind_profile: Option<crate::sim_core::wind::WindProfile>,
    launch_guide: Option<LaunchGuide>,
    relative_humidity: f64,
    base_temperature_k: f64,
    base_pressure_pa: f64,
    launch_altitude_m: f64,
}

impl MissionBuilder {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
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

    pub fn stage(mut self, stage: Stage) -> Self {
        self.stages.push(stage);
        self
    }

    pub fn wind_velocity_mps(mut self, wind_velocity_mps: Vector3<f64>) -> Self {
        self.wind_velocity_mps = wind_velocity_mps;
        self
    }

    pub fn wind_profile(mut self, profile: crate::sim_core::wind::WindProfile) -> Self {
        self.wind_profile = Some(profile);
        self
    }

    pub fn launch_guide(mut self, length_m: f64, direction: Vector3<f64>) -> Self {
        if length_m > 0.0 && direction.norm() > 1e-9 {
            self.launch_guide = Some(LaunchGuide {
                length_m,
                direction: direction.normalize(),
            });
        }
        self
    }

    pub fn relative_humidity(mut self, relative_humidity: f64) -> Self {
        self.relative_humidity = relative_humidity.clamp(0.0, 1.0);
        self
    }

    pub fn atmosphere(
        mut self,
        base_temperature_k: f64,
        base_pressure_pa: f64,
        launch_altitude_m: f64,
    ) -> Self {
        self.base_temperature_k = base_temperature_k;
        self.base_pressure_pa = base_pressure_pa;
        self.launch_altitude_m = launch_altitude_m;
        self
    }

    pub fn build(self) -> Mission {
        Mission {
            name: self.name,
            stages: self.stages,
            wind_velocity_mps: self.wind_velocity_mps,
            wind_profile: self.wind_profile,
            launch_guide: self.launch_guide,
            relative_humidity: self.relative_humidity,
            base_temperature_k: self.base_temperature_k,
            base_pressure_pa: self.base_pressure_pa,
            launch_altitude_m: self.launch_altitude_m,
        }
    }
}

impl Mission {
    pub fn atmosphere_at(
        &self,
        altitude_agl_m: f64,
    ) -> crate::sim_core::physics::atmosphere::Atmo {
        crate::sim_core::physics::atmosphere::isa_from_launch_conditions(
            altitude_agl_m,
            self.launch_altitude_m,
            self.base_temperature_k,
            self.base_pressure_pa,
            self.relative_humidity,
        )
    }
}

// ---------------------------------------------------------------------------
// Preset missions
// ---------------------------------------------------------------------------

pub mod presets {
    use super::*;

    /// 2-stage sounding rocket ("Pathfinder").
    pub fn pathfinder() -> Mission {
        Mission {
            name: "Pathfinder".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![
                StageBuilder::new("S1-Booster")
                    .dry_mass(40.0)
                    .propellant_mass(25.0)
                    .thrust(5000.0)
                    .isp(220.0)
                    .cd(0.35)
                    .area(0.02)
                    .inertia(Vector3::new(20.0, 20.0, 2.0))
                    .nozzle_offset(1.5)
                    .cp_offset(0.4)
                    .tvc_max(0.1)
                    .build(),
                StageBuilder::new("S2-Sustainer")
                    .dry_mass(8.0)
                    .propellant_mass(6.0)
                    .thrust(1200.0)
                    .isp(250.0)
                    .cd(0.28)
                    .area(0.008)
                    .inertia(Vector3::new(2.0, 2.0, 0.2))
                    .nozzle_offset(0.6)
                    .cp_offset(0.25)
                    .tvc_max(0.08)
                    .build(),
            ],
        }
    }
}
