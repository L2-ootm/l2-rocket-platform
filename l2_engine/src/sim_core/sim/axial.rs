//! Allocation-free axial flight scorer for high-throughput population screening.
//!
//! This model preserves the production motor curves, mass flow, atmospheric
//! model, gravity law, Mach-dependent drag and staging timeline, but constrains
//! translation to the launch axis.  It intentionally does **not** model
//! attitude/TVC, angle of attack, weathercocking, horizontal wind, aerodynamic
//! normal force, or recovery descent.  Results are therefore a calibrated proxy
//! and never an authority result; promoted designs still require 6-DOF and
//! OpenRocket validation.

use crate::sim_core::dynamics::state::{EARTH_RADIUS, G0};
use crate::sim_core::io::json::FlightSummary;
use crate::sim_core::vehicle::{Mission, Stage};

/// Controls the axial screening integration.
#[derive(Debug, Clone, Copy)]
pub struct AxialConfig {
    /// Nominal fixed step. Steps are shortened only to land on discrete motor
    /// and staging events, avoiding cadence-dependent impulse loss.
    pub dt: f64,
    pub max_time: f64,
    /// Altitude that proves the vehicle has left the pad.
    pub liftoff_altitude_m: f64,
    /// Extra time after the first motor's nominal end before declaring failure.
    pub no_liftoff_grace_s: f64,
}

impl Default for AxialConfig {
    fn default() -> Self {
        Self {
            dt: 0.05,
            max_time: 600.0,
            liftoff_altitude_m: 1.0,
            no_liftoff_grace_s: 0.5,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AxialError {
    EmptyMission,
    InvalidConfig,
    InvalidStage,
    NoLiftoff,
    Diverged,
    TimeLimit,
}

/// Simulate a mission along its launch axis and stop on the first descending
/// sample after apogee. Memory consumption is O(number of stages), with no
/// trajectory or command allocation.
pub fn simulate_axial(
    mission: &Mission,
    config: &AxialConfig,
) -> Result<FlightSummary, AxialError> {
    if mission.stages.is_empty() {
        return Err(AxialError::EmptyMission);
    }
    if !config.dt.is_finite()
        || config.dt <= 0.0
        || !config.max_time.is_finite()
        || config.max_time <= 0.0
    {
        return Err(AxialError::InvalidConfig);
    }
    if mission.stages.iter().any(|s| {
        !s.dry_mass.is_finite()
            || !s.propellant_mass().is_finite()
            || !s.motors.first().map(|m| m.isp).unwrap_or(0.0).is_finite()
            || !s.area.is_finite()
            || s.dry_mass < 0.0
            || s.propellant_mass() < 0.0
            || s.motors.first().map(|m| m.isp).unwrap_or(0.0) <= 0.0
            || s.area < 0.0
    }) {
        return Err(AxialError::InvalidStage);
    }

    let mut t = 0.0_f64;
    let mut altitude = 0.0_f64;
    let mut velocity = 0.0_f64;
    let mut mass = mission.total_mass();
    let mut stage_idx = 0_usize;
    let mut stage_activated_at = 0.0_f64;
    let mut depleted_at: Option<f64> = None;
    let mut remaining_propellant = mission.stages[0].propellant_mass();
    let mut consumed_propellant = 0.0_f64;
    let mut launched = false;

    let first = &mission.stages[0];
    let first_motor_end = first.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0) + nominal_burn_duration(first);
    let no_liftoff_deadline = first_motor_end + config.no_liftoff_grace_s.max(0.0);

    let mut apogee = 0.0_f64;
    let mut apogee_time = 0.0_f64;
    let mut max_speed = 0.0_f64;
    let mut max_mach = 0.0_f64;
    let mut max_accel = 0.0_f64;

    while t < config.max_time {
        let stage = &mission.stages[stage_idx];

        // Separation is a zero-duration event and must precede force sampling.
        if let Some(burnout) = depleted_at {
            if stage_idx + 1 < mission.stages.len()
                && t + 1e-10 >= burnout + stage.separation_coast.max(0.0)
            {
                mass = (mass - stage.dry_mass).max(0.0);
                stage_idx += 1;
                stage_activated_at = t;
                depleted_at = None;
                remaining_propellant = mission.stages[stage_idx].propellant_mass();
                continue;
            }
        }

        let stage = &mission.stages[stage_idx];
        let mut dt = config.dt.min(config.max_time - t);
        dt = clip_to_next_event(
            dt,
            t,
            stage,
            stage_activated_at,
            depleted_at,
            stage_idx + 1 < mission.stages.len(),
        );
        if dt <= 1e-10 {
            // Floating-point coincidence with a curve knot: move by a tiny,
            // bounded amount rather than spin forever.
            dt = config.dt.min(config.max_time - t).min(1e-7);
        }

        // Explicit midpoint (RK2) gives materially lower coarse-step bias than
        // Euler while requiring only two allocation-free force evaluations.
        let a0 = axial_acceleration(
            mission,
            stage,
            t,
            altitude,
            velocity,
            mass,
            remaining_propellant,
            stage_activated_at,
        );
        let thrust0 = active_thrust(stage, t, stage_activated_at, remaining_propellant);
        let flow0 = mass_flow(stage, thrust0);
        let mid_mass = (mass - 0.5 * dt * flow0).max(mass - remaining_propellant);
        let mid_prop = (remaining_propellant - 0.5 * dt * flow0).max(0.0);
        let mid_alt = (altitude + 0.5 * dt * velocity).max(0.0);
        let mid_vel = velocity + 0.5 * dt * a0;
        let mid_t = t + 0.5 * dt;
        let amid = axial_acceleration(
            mission,
            stage,
            mid_t,
            mid_alt,
            mid_vel,
            mid_mass,
            mid_prop,
            stage_activated_at,
        );
        let thrust_mid = active_thrust(stage, mid_t, stage_activated_at, mid_prop);
        let consumed = (dt * mass_flow(stage, thrust_mid)).min(remaining_propellant);

        let previous_altitude = altitude;
        let previous_velocity = velocity;
        altitude += dt * mid_vel;
        velocity += dt * amid;
        remaining_propellant -= consumed;
        consumed_propellant += consumed;
        mass -= consumed;
        t += dt;

        // The pad supplies the normal force and prevents pre-ignition freefall.
        if !launched && altitude <= 0.0 {
            altitude = 0.0;
            velocity = velocity.max(0.0);
        }

        if !t.is_finite() || !altitude.is_finite() || !velocity.is_finite() || !mass.is_finite() {
            return Err(AxialError::Diverged);
        }

        if remaining_propellant <= stage.propellant_depletion_tolerance_kg()
            || motor_finished(stage, t, stage_activated_at)
        {
            // Real curves define burnout even if numerical quadrature leaves a
            // trace residue. Constant-thrust stages deplete by mass flow.
            consumed_propellant += remaining_propellant.max(0.0);
            remaining_propellant = 0.0;
            mass = stage.dry_mass
                + mission.stages[stage_idx + 1..]
                    .iter()
                    .map(Stage::total_mass)
                    .sum::<f64>();
            depleted_at.get_or_insert(t);
        }

        if altitude >= config.liftoff_altitude_m.max(0.0) {
            launched = true;
        } else if !launched && t >= no_liftoff_deadline {
            return Err(AxialError::NoLiftoff);
        }

        if altitude > apogee {
            apogee = altitude;
            apogee_time = t;
        }
        let axial_air_speed = (velocity - mission.wind_velocity_mps.z).abs();
        let atm = mission.atmosphere_at(altitude.max(0.0));
        max_speed = max_speed.max(axial_air_speed);
        if atm.sound_speed > 0.0 {
            max_mach = max_mach.max(axial_air_speed / atm.sound_speed);
        }
        max_accel = max_accel.max((velocity - previous_velocity).abs() / dt);

        if launched && previous_velocity > 0.0 && velocity <= 0.0 {
            // Linear interpolation removes most dt-sized apogee quantization.
            let fraction = previous_velocity / (previous_velocity - velocity);
            let crossing_time = t - dt + dt * fraction;
            let crossing_alt = previous_altitude
                + previous_velocity * dt * fraction
                + 0.5 * amid * (dt * fraction).powi(2);
            if crossing_alt > apogee {
                apogee = crossing_alt;
                apogee_time = crossing_time;
            }
            return Ok(FlightSummary {
                apogee_m: apogee,
                apogee_time,
                max_speed,
                max_mach,
                max_accel,
                max_accel_g: max_accel / G0,
                flight_time: crossing_time,
                impact_speed: 0.0,
                apogee_east_m: 0.0,
                apogee_north_m: 0.0,
                stage_landings: vec![],
                total_prop_mass_kg: consumed_propellant.min(
                    mission
                        .stages
                        .iter()
                        .map(|stage| stage.propellant_mass())
                        .sum::<f64>(),
                ),
            });
        }
    }

    if !launched {
        Err(AxialError::NoLiftoff)
    } else {
        Err(AxialError::TimeLimit)
    }
}

#[inline]
fn nominal_burn_duration(stage: &Stage) -> f64 {
    stage
        .motors.first().and_then(|m| m.thrust_curve.last())
        .map(|point| point.0.max(0.0))
        .unwrap_or_else(|| stage.burn_time())
}

#[inline]
fn motor_finished(stage: &Stage, t: f64, activated_at: f64) -> bool {
    !stage.motors.first().map_or(true, |m| m.thrust_curve.is_empty())
        && t + 1e-10 >= activated_at + stage.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0) + nominal_burn_duration(stage)
}

#[inline]
fn active_thrust(stage: &Stage, t: f64, activated_at: f64, propellant: f64) -> f64 {
    let motor_time = t - activated_at - stage.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0);
    if propellant <= stage.propellant_depletion_tolerance_kg() || motor_time < 0.0 {
        0.0
    } else if stage.motors.first().map_or(true, |m| m.thrust_curve.is_empty()) && motor_time > stage.burn_time() {
        0.0
    } else {
        stage.thrust_at(motor_time).max(0.0)
    }
}

#[inline]
fn mass_flow(stage: &Stage, thrust: f64) -> f64 {
    if thrust > 0.0 && stage.motors.first().map(|m| m.isp).unwrap_or(0.0) > 0.0 {
        thrust / (stage.motors.first().map(|m| m.isp).unwrap_or(0.0) * G0)
    } else {
        0.0
    }
}

#[inline]
fn axial_acceleration(
    mission: &Mission,
    stage: &Stage,
    t: f64,
    altitude: f64,
    velocity: f64,
    mass: f64,
    propellant: f64,
    activated_at: f64,
) -> f64 {
    let atm = mission.atmosphere_at(altitude.max(0.0));
    let relative_velocity = velocity - mission.wind_velocity_mps.z;
    let speed = relative_velocity.abs();
    let mach = if atm.sound_speed > 0.0 {
        speed / atm.sound_speed
    } else {
        0.0
    };
    let cd = stage.cd_at_conditions(mach, speed, atm.kinematic_viscosity);
    let drag = 0.5 * atm.density * speed * speed * cd * stage.area;
    let signed_drag = -relative_velocity.signum() * drag;
    let gravity = G0 * (EARTH_RADIUS / (EARTH_RADIUS + altitude.max(0.0))).powi(2);
    (active_thrust(stage, t, activated_at, propellant) + signed_drag) / mass.max(1e-9) - gravity
}

fn clip_to_next_event(
    dt: f64,
    t: f64,
    stage: &Stage,
    activated_at: f64,
    depleted_at: Option<f64>,
    has_upper_stage: bool,
) -> f64 {
    let mut result = dt;
    let ignition = activated_at + stage.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0);
    if ignition > t + 1e-10 {
        result = result.min(ignition - t);
    }
    if !stage.motors.first().map_or(true, |m| m.thrust_curve.is_empty()) {
        for &(curve_t, _) in &stage.motors.first().unwrap().thrust_curve {
            let event = ignition + curve_t;
            if event > t + 1e-10 {
                result = result.min(event - t);
                break;
            }
        }
    } else {
        let burnout = ignition + stage.burn_time();
        if burnout > t + 1e-10 {
            result = result.min(burnout - t);
        }
    }
    if has_upper_stage {
        if let Some(burnout) = depleted_at {
            let separation = burnout + stage.separation_coast.max(0.0);
            if separation > t + 1e-10 {
                result = result.min(separation - t);
            }
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::vehicle::{MissionBuilder, StageBuilder};

    #[test]
    fn real_curve_launches_and_stops_at_apogee() {
        let stage = StageBuilder::new("curve")
            .dry_mass(8.0)
            .propellant_mass(2.0)
            .thrust(1200.0)
            .isp(220.0)
            .cd(0.35)
            .area(0.008)
            .thrust_curve(vec![(0.0, 0.0), (0.1, 1200.0), (3.5, 1200.0), (3.6, 0.0)])
            .build();
        let mission = MissionBuilder::new("axial-test").stage(stage).build();
        let summary = simulate_axial(&mission, &AxialConfig::default()).unwrap();
        assert!(summary.apogee_m > 100.0);
        assert!(summary.max_mach > 0.0);
        assert_eq!(summary.impact_speed, 0.0);
        assert!((summary.flight_time - summary.apogee_time).abs() < 0.1);
    }

    #[test]
    fn underpowered_motor_is_rejected() {
        let stage = StageBuilder::new("weak")
            .dry_mass(100.0)
            .propellant_mass(1.0)
            .thrust(10.0)
            .isp(200.0)
            .build();
        let mission = MissionBuilder::new("no-liftoff").stage(stage).build();
        assert!(matches!(
            simulate_axial(&mission, &AxialConfig::default()),
            Err(AxialError::NoLiftoff)
        ));
    }

    #[test]
    fn delayed_second_stage_contributes_energy() {
        let booster = StageBuilder::new("booster")
            .dry_mass(4.0)
            .propellant_mass(1.0)
            .thrust(1000.0)
            .isp(210.0)
            .cd(0.3)
            .area(0.006)
            .separation_coast(0.4)
            .build();
        let sustainer = StageBuilder::new("sustainer")
            .dry_mass(3.0)
            .propellant_mass(1.0)
            .thrust(500.0)
            .isp(230.0)
            .cd(0.3)
            .area(0.006)
            .ignition_delay(0.5)
            .build();
        let two = MissionBuilder::new("two")
            .stage(booster.clone())
            .stage(sustainer)
            .build();
        let one = MissionBuilder::new("one").stage(booster).build();
        let config = AxialConfig::default();
        let two_summary = simulate_axial(&two, &config).unwrap();
        let one_summary = simulate_axial(&one, &config).unwrap();
        assert!(two_summary.apogee_m > one_summary.apogee_m);
    }
}
