use nalgebra::{UnitQuaternion, Vector3};

use super::integrator::rk4_step;
use super::{
    adaptive::{AdaptiveStepInputs, SelectedStep, StepLimit, select_time_step},
    event::{distance_to_next_scheduled_event, distance_to_predicted_apogee},
};
use crate::PhysicsMode;
use crate::sim_core::dynamics::sixdof::derivatives;
use crate::sim_core::dynamics::state::{GncCommand, SimConfig, State};
use crate::sim_core::gnc::Controller;
use crate::sim_core::gnc::TvcController;
use crate::sim_core::io::json::{FlightSummary, StageLanding};
use crate::sim_core::vehicle::Mission;

// ---------------------------------------------------------------------------
// Stage separation logic
// ---------------------------------------------------------------------------

/// Separate after the ascent motors finish, without waiting for delayed
/// recovery motors. The detached branch keeps its dry mass and every gram of
/// propellant still aboard so its independently timed retro burn remains live.
fn check_staging(state: &mut State, mission: &Mission) -> Option<State> {
    if state.stage_idx >= mission.stages.len() {
        return None;
    }
    let stage = &mission.stages[state.stage_idx];
    let upper_mass: f64 = mission.stages[state.stage_idx + 1..]
        .iter()
        .map(|s| s.total_mass())
        .sum();
    let t_since_activation = state.time - state.stage_activated_at;
    let total_propellant_depleted = stage.remaining_propellant_at(t_since_activation)
        <= stage.propellant_depletion_tolerance_kg();
    let separation_ready = if state.stage_idx + 1 < mission.stages.len() {
        stage.ascent_burn_complete(t_since_activation)
    } else {
        total_propellant_depleted
    };

    if separation_ready {
        let depleted_at = *state.stage_depleted_at.get_or_insert(state.time);
        if state.stage_idx + 1 < mission.stages.len() {
            if state.time - depleted_at >= stage.separation_coast {
                let detached_mass = stage.dry_mass
                    + stage.remaining_propellant_at(t_since_activation);
                // Prepare dropped stage
                let mut dropped = state.clone();
                dropped.mass = detached_mass;
                dropped.stage_idx = 0;
                
                // Drop the entire branch, including unburned retro propellant.
                state.mass = upper_mass;
                state.stage_idx += 1;
                state.stage_depleted_at = None;
                state.stage_activated_at = state.time;
                return Some(dropped);
            }
        } else {
            if total_propellant_depleted {
                state.mass = stage.dry_mass + upper_mass;
            }
            if let Some(delay) = stage.parachute_delay {
                if state.time - depleted_at >= stage.separation_coast + delay {
                    state.parachute_deployed = true;
                }
            }
        }
    }
    None
}

fn apply_launch_guide_constraint(state: &mut State, mission: &Mission) {
    let Some(guide) = mission.launch_guide.as_ref() else {
        return;
    };
    let along = state.pos.dot(&guide.direction);
    if along >= guide.length_m {
        return;
    }

    let clamped_along = along.max(0.0);
    let speed_along = state.vel.dot(&guide.direction).max(0.0);
    state.pos = guide.direction * clamped_along;
    state.vel = guide.direction * speed_along;
    state.omega = Vector3::zeros();
    state.quat = UnitQuaternion::rotation_between(&Vector3::z(), &guide.direction)
        .unwrap_or_else(UnitQuaternion::identity);
}

fn no_liftoff_deadline(mission: &Mission) -> Option<f64> {
    let first = mission.stages.first()?;
    let burn_s = first
        .motors.first().and_then(|m| m.thrust_curve.last())
        .map(|point| point.0)
        .unwrap_or_else(|| first.burn_time());
    Some(first.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0) + burn_s + 0.5)
}

/// Fast-screen coast stepping: retain the configured fine cadence through
/// every burn, ignition boundary and the last second before contact, while
/// skipping empty 25ms coast samples that carry no control/event information.
fn fast_unpowered_descent_step(state: &State, mission: &Mission, base_dt: f64) -> f64 {
    if base_dt < 0.024 || state.vel.z >= 0.0 || state.pos.z <= 0.0 {
        return base_dt;
    }
    let Some(stage) = mission.active_stage(state.stage_idx) else {
        return base_dt;
    };
    let elapsed = state.time - state.stage_activated_at;
    if stage.thrust_at(elapsed) > 0.0 {
        return base_dt;
    }
    let next_ignition = stage
        .motors
        .iter()
        .map(|motor| motor.ignition_delay - elapsed)
        .filter(|distance| *distance > 0.0)
        .fold(f64::INFINITY, f64::min);
    let time_to_ground = state.pos.z / (-state.vel.z).max(0.1);
    if next_ignition > 0.5 && time_to_ground > 1.0 {
        0.25
    } else {
        base_dt
            .min(next_ignition.max(0.001))
            .min(time_to_ground.max(0.001))
    }
}

// ---------------------------------------------------------------------------
// Full mission simulation
// ---------------------------------------------------------------------------

/// Simulate a complete multi-stage mission with a custom controller.
/// Returns trajectory and the GNC commands at each step.
pub fn simulate_with(
    mission: &Mission,
    config: &SimConfig,
    controller: &mut dyn Controller,
) -> (Vec<State>, Vec<GncCommand>) {
    simulate_loop(mission, config, controller, PhysicsMode::HyperReal)
}

/// Mode-aware production entry point. OpenRocket compatibility uses its
/// adaptive timestep policy; HyperReal deliberately delegates to the legacy
/// fixed-step behavior.
pub fn simulate_with_mode(
    mission: &Mission,
    config: &SimConfig,
    controller: &mut dyn Controller,
    mode: PhysicsMode,
) -> (Vec<State>, Vec<GncCommand>) {
    simulate_loop(mission, config, controller, mode)
}

/// Run the same 6-DOF dynamics while retaining only scalar flight extrema.
///
/// This is the production scoring path for throughput-oriented profiles.  It
/// deliberately leaves `simulate_with_mode` unchanged for trace/authority
/// parity and uses O(1) memory instead of cloning every state and command.
pub fn simulate_summary_with_mode(
    mission: &Mission,
    config: &SimConfig,
    controller: &mut dyn Controller,
    mode: PhysicsMode,
    stop_at_apogee: bool,
) -> Result<FlightSummary, String> {
    simulate_summary_with_mode_gated(
        mission,
        config,
        controller,
        mode,
        stop_at_apogee,
        None,
    )
}

/// Population-screen variant that aborts immediately when a hard Mach gate
/// is crossed. Rejected designs are not "completed" because their result is
/// already immutable; viable designs still propagate every branch to ground.
pub fn simulate_summary_with_mode_gated(
    mission: &Mission,
    config: &SimConfig,
    controller: &mut dyn Controller,
    mode: PhysicsMode,
    stop_at_apogee: bool,
    max_mach_gate: Option<f64>,
) -> Result<FlightSummary, String> {
    let mut state = State {
        time: 0.0,
        pos: Vector3::zeros(),
        vel: Vector3::zeros(),
        quat: UnitQuaternion::identity(),
        omega: Vector3::zeros(),
        mass: mission.total_mass(),
        stage_idx: 0,
        stage_activated_at: 0.0,
        stage_depleted_at: None,
        parachute_deployed: false,
    };
    let _first_stage = mission
        .stages
        .first()
        .ok_or_else(|| "mission_has_no_stages".to_string())?;
    let mut launched = false;
    let no_liftoff_deadline = no_liftoff_deadline(mission).expect("first stage exists");
    let mut previous_dt = config.dt;
    let mut previous_cmd = GncCommand::default();
    let mut apogee_m = 0.0_f64;
    let mut apogee_time = 0.0_f64;
    let mut apogee_east_m = 0.0_f64;
    let mut apogee_north_m = 0.0_f64;
    let mut max_speed = 0.0_f64;
    let mut max_mach = 0.0_f64;
    let mut max_accel = 0.0_f64;
    let mut impact_speed = 0.0_f64;
    let mut dropped_stages = Vec::new();
    let mut final_landing = None;
    let loaded_propellant_kg = mission
        .stages
        .iter()
        .map(|stage| stage.propellant_mass())
        .sum::<f64>();
    let mut consumed_propellant_kg = 0.0_f64;

    while state.time < config.max_time {
        let selected =
            select_runner_step(&state, mission, config, mode, previous_dt, &previous_cmd);
        let dt = fast_unpowered_descent_step(&state, mission, selected.dt)
            .min(config.max_time - state.time);
        let previous_state = state.clone();
        let previous_vel = state.vel;
        let cmd = controller.control(&state, mission, dt);

        state = rk4_step(&state, mission, &cmd, dt);
        consumed_propellant_kg += (previous_state.mass - state.mass).max(0.0);
        apply_launch_guide_constraint(&mut state, mission);
        let mass_before_staging = state.mass;
        let stage_before_staging = state.stage_idx;
        if let Some(dropped) = check_staging(&mut state, mission) {
            // Integration can leave milligram-scale residue at an exact burn
            // boundary. Count only that residue here; the detached branch's
            // real remaining propellant stays aboard and is consumed later.
            consumed_propellant_kg +=
                (mass_before_staging - state.mass - dropped.mass).max(0.0);
            dropped_stages.push((stage_before_staging, dropped));
        } else {
            consumed_propellant_kg += (mass_before_staging - state.mass).max(0.0);
        }

        if !state.time.is_finite()
            || !state.pos.iter().all(|value| value.is_finite())
            || !state.vel.iter().all(|value| value.is_finite())
        {
            return Err("simulation_diverged".to_string());
        }

        if state.pos.z > apogee_m {
            apogee_m = state.pos.z;
            apogee_time = state.time;
            apogee_east_m = state.pos.x;
            apogee_north_m = state.pos.y;
        }
        let wind_vel = if let Some(wp) = &mission.wind_profile {
            let (e, n) = wp.wind_vector_at(state.pos.z.max(0.0));
            Vector3::new(e, n, 0.0)
        } else {
            mission.wind_velocity_mps
        };
        let air_speed = (state.vel - wind_vel).norm();
        max_speed = max_speed.max(air_speed);
        let sound_speed = mission.atmosphere_at(state.pos.z.max(0.0)).sound_speed;
        if sound_speed > 0.0 {
            max_mach = max_mach.max(air_speed / sound_speed);
            if max_mach_gate.is_some_and(|gate| max_mach > gate) {
                return Err(format!(
                    "constraint_violation:max_mach {:.6} > {:.6}",
                    max_mach,
                    max_mach_gate.unwrap()
                ));
            }
        }
        if dt > 0.0 {
            max_accel = max_accel.max((state.vel - previous_vel).norm() / dt);
        }

        if state.pos.z > 1.0 {
            launched = true;
        } else if !launched && state.time >= no_liftoff_deadline {
            return Err("no_liftoff".to_string());
        }

        if stop_at_apogee && launched && state.vel.z <= 0.0 {
            impact_speed = state.vel.norm();
            break;
        }
        if launched && state.pos.z <= 0.0 {
            let landing = interpolate_touchdown(&previous_state, &state, state.stage_idx);
            state.time = landing.touchdown_time_s;
            state.pos.x = landing.east_m;
            state.pos.y = landing.north_m;
            state.pos.z = 0.0;
            impact_speed = landing.total_speed_ms;
            final_landing = Some(landing);
            break;
        }

        previous_dt = dt;
        previous_cmd = cmd;
    }

    if !launched {
        return Err("no_liftoff".to_string());
    }

    let mut stage_landings = Vec::new();
    if let Some(landing) = final_landing {
        stage_landings.push(landing);
    }

    // An ascent-only scorer has all the information it needs at first apogee.
    // Do not spend most of the batch budget propagating detached branches to
    // touchdown; OpenRocket owns that branch/descent authority pass.
    if stop_at_apogee {
        dropped_stages.clear();
    }
    for (d_stage_idx, mut d_state) in dropped_stages {
        let mut branch_mission = mission.clone();
        branch_mission.name = format!("{} detached stage {}", mission.name, d_stage_idx);
        branch_mission.stages = vec![mission.stages[d_stage_idx].clone()];
        let mut dropped_landing = None;
        while d_state.pos.z > 0.0 && d_state.time < config.max_time {
            let d_dt = fast_unpowered_descent_step(&d_state, &branch_mission, config.dt)
                .min(config.max_time - d_state.time);
            let previous_state = d_state.clone();
            let cmd = GncCommand::default();
            d_state = rk4_step(&d_state, &branch_mission, &cmd, d_dt);
            if branch_mission.stages[0]
                .remaining_propellant_at(d_state.time - d_state.stage_activated_at)
                <= branch_mission.stages[0].propellant_depletion_tolerance_kg()
            {
                d_state.mass = branch_mission.stages[0].dry_mass;
            }
            consumed_propellant_kg += (previous_state.mass - d_state.mass).max(0.0);
            // Manually deploy parachute based on time since separation (d_state.stage_activated_at is unchanged)
            if let Some(delay) = mission.stages[d_stage_idx].parachute_delay {
                // Time since it depleted propellant
                if let Some(depleted_at) = d_state.stage_depleted_at {
                    if d_state.time - depleted_at >= mission.stages[d_stage_idx].separation_coast + delay {
                        d_state.parachute_deployed = true;
                    }
                }
            }
            if !d_state.time.is_finite() || !d_state.pos.iter().all(|v| v.is_finite()) || !d_state.vel.iter().all(|v| v.is_finite()) {
                return Err(format!("dropped_stage_{d_stage_idx}_simulation_diverged"));
            }
            if d_state.pos.z <= 0.0 {
                dropped_landing = Some(interpolate_touchdown(
                    &previous_state,
                    &d_state,
                    d_stage_idx,
                ));
                break;
            }
        }
        if let Some(landing) = dropped_landing {
            stage_landings.push(landing);
        }
    }
    stage_landings.sort_by_key(|landing| landing.stage_idx);

    // The official term is propellant consumed, not merely propellant loaded.
    // Keep the historical field name for compatibility with mission tables.
    let total_prop_mass_kg = consumed_propellant_kg.clamp(0.0, loaded_propellant_kg);

    Ok(FlightSummary {
        apogee_m,
        apogee_time,
        max_speed,
        max_mach,
        max_accel,
        max_accel_g: max_accel / 9.80665,
        flight_time: state.time,
        impact_speed,
        apogee_east_m,
        apogee_north_m,
        stage_landings,
        total_prop_mass_kg,
    })
}

fn ground_crossing_fraction(previous: &State, current: &State) -> f64 {
    let denominator = previous.pos.z - current.pos.z;
    if denominator.abs() <= f64::EPSILON {
        1.0
    } else {
        (previous.pos.z / denominator).clamp(0.0, 1.0)
    }
}

/// Interpolate the exact z=0 crossing instead of reporting the first RK4
/// sample below ground. This keeps the Rust proxy deterministic across
/// timesteps and avoids a systematic downrange/impact-speed bias. OpenRocket
/// branch telemetry remains the competition authority.
fn interpolate_touchdown(previous: &State, current: &State, stage_idx: usize) -> StageLanding {
    let fraction = ground_crossing_fraction(previous, current);
    let position = previous.pos + (current.pos - previous.pos) * fraction;
    let velocity = previous.vel + (current.vel - previous.vel) * fraction;
    let east_m = position.x;
    let north_m = position.y;
    StageLanding {
        stage_idx,
        touchdown_time_s: previous.time + (current.time - previous.time) * fraction,
        east_m,
        north_m,
        distance_m: east_m.hypot(north_m),
        vz_ms: velocity.z,
        vxy_ms: velocity.x.hypot(velocity.y),
        total_speed_ms: velocity.norm(),
    }
}

fn simulate_loop(
    mission: &Mission,
    config: &SimConfig,
    controller: &mut dyn Controller,
    mode: PhysicsMode,
) -> (Vec<State>, Vec<GncCommand>) {
    let mut state = State {
        time: 0.0,
        pos: Vector3::zeros(),
        vel: Vector3::zeros(),
        quat: UnitQuaternion::identity(),
        omega: Vector3::zeros(),
        mass: mission.total_mass(),
        stage_idx: 0,
        stage_activated_at: 0.0,
        stage_depleted_at: None,
        parachute_deployed: false,
    };

    let capacity = (config.max_time / config.dt) as usize + 1;
    let cap = capacity.min(200_000);
    let mut trajectory = Vec::with_capacity(cap);
    let mut commands = Vec::with_capacity(cap);

    trajectory.push(state.clone());
    commands.push(GncCommand::default());

    let mut launched = false;
    let no_liftoff_deadline = no_liftoff_deadline(mission);
    let mut previous_dt = config.dt;
    let mut previous_cmd = GncCommand::default();

    while state.time < config.max_time {
        let selected =
            select_runner_step(&state, mission, config, mode, previous_dt, &previous_cmd);
        let dt = selected.dt.min(config.max_time - state.time);

        // GNC update at the actual command cadence.
        let cmd = controller.control(&state, mission, dt);

        // Integrate
        state = rk4_step(&state, mission, &cmd, dt);
        apply_launch_guide_constraint(&mut state, mission);
        let _ = check_staging(&mut state, mission);

        if state.pos.z > 1.0 {
            launched = true;
        }

        if !launched
            && no_liftoff_deadline.is_some_and(|deadline| state.time >= deadline)
        {
            trajectory.push(state);
            commands.push(cmd);
            break;
        }

        // Ground impact
        if launched && state.pos.z <= 0.0 {
            state.pos.z = 0.0;
            trajectory.push(state);
            commands.push(cmd);
            break;
        }

        trajectory.push(state.clone());
        commands.push(cmd);
        previous_dt = dt;
        previous_cmd = cmd;
    }

    (trajectory, commands)
}

fn select_runner_step(
    state: &State,
    mission: &Mission,
    config: &SimConfig,
    mode: PhysicsMode,
    previous_dt: f64,
    previous_cmd: &GncCommand,
) -> SelectedStep {
    if mode != PhysicsMode::OpenRocketLegacy {
        return SelectedStep {
            dt: config.dt,
            limit: StepLimit::User,
        };
    }

    let derivative = derivatives(state, mission, previous_cmd);
    let distance_to_event = distance_to_next_scheduled_event(state, mission)
        .min(distance_to_predicted_apogee(state, derivative.dvel.z));
    let (on_launch_rod, launch_rod_length) =
        mission.launch_guide.as_ref().map_or((false, 0.0), |guide| {
            (
                state.pos.dot(&guide.direction) < guide.length_m,
                guide.length_m,
            )
        });

    select_time_step(AdaptiveStepInputs {
        user_dt: config.dt,
        distance_to_event,
        maximum_angle_step: 3.0_f64.to_radians(),
        lateral_pitch_rate: state.omega.x.hypot(state.omega.y),
        roll_rate: state.omega.z,
        roll_acceleration: derivative.domega.z,
        lateral_angular_acceleration: derivative.domega.x.abs().max(derivative.domega.y.abs()),
        on_launch_rod,
        launch_rod_length,
        speed: (state.vel - mission.wind_velocity_mps).norm(),
        previous_dt,
    })
}

/// Simulate with the default TvcController (convenience wrapper).
pub fn simulate(mission: &Mission, config: &SimConfig) -> (Vec<State>, Vec<GncCommand>) {
    let mut controller = TvcController::new();
    simulate_with(mission, config, &mut controller)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::dynamics::state::G0;
    use crate::sim_core::vehicle::{MotorBurn, Stage, StageBuilder};

    fn two_stage_mission() -> Mission {
        Mission {
            name: "2-Stage Test".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![
                Stage {
                    name: "Booster".into(),
                    dry_mass: 40.0,
                motors: vec![crate::sim_core::vehicle::MotorBurn {
                    role: "main".to_string(),
                    propellant_mass: 30.0,
                    thrust: 5000.0,
                    isp: 220.0,
                    thrust_curve: vec![],
                    ignition_delay: 0.0,
                    position_from_nose_m: Vector3::zeros(),
                    nozzle_position_from_nose_m: Vector3::zeros(),
                }],
                    cd: 0.35,
                    area: 0.02,
                    inertia: Vector3::new(20.0, 20.0, 2.0),
                    nozzle_offset: 1.5,
                    cp_offset: 0.4,
                    dry_cg_from_nose: 0.0,
                    motor_axial_offset_m: 0.0,
                    rotational_fixed_mass_kg: 0.0,
                    rotational_fixed_cg_from_nose: 0.0,
                    rotational_fixed_cg_radial_m: Vector3::zeros(),
                    tvc_max: 0.1,
                    cn_alpha: None,
                    aero_stability_table: vec![],
                    pitch_damping_multiplier: 0.0,
                    cd_table: vec![],
                    cd_nonfric_table: vec![],
                    friction_params: None,
                    separation_coast: 0.0,
                    parachute_delay: None,
                    parachute_cd_area: None,
                },
                Stage {
                    name: "Sustainer".into(),
                    dry_mass: 10.0,
                motors: vec![crate::sim_core::vehicle::MotorBurn {
                    role: "main".to_string(),
                    propellant_mass: 30.0,
                    thrust: 5000.0,
                    isp: 220.0,
                    thrust_curve: vec![],
                    ignition_delay: 0.0,
                    position_from_nose_m: Vector3::zeros(),
                    nozzle_position_from_nose_m: Vector3::zeros(),
                }],
                    cd: 0.3,
                    area: 0.01,
                    inertia: Vector3::new(3.0, 3.0, 0.3),
                    nozzle_offset: 0.8,
                    cp_offset: 0.3,
                    dry_cg_from_nose: 0.0,
                    motor_axial_offset_m: 0.0,
                    rotational_fixed_mass_kg: 0.0,
                    rotational_fixed_cg_from_nose: 0.0,
                    rotational_fixed_cg_radial_m: Vector3::zeros(),
                    tvc_max: 0.08,
                    cn_alpha: None,
                    aero_stability_table: vec![],
                    pitch_damping_multiplier: 0.0,
                    cd_table: vec![],
                    cd_nonfric_table: vec![],
                    friction_params: None,
                    separation_coast: 0.0,
                    parachute_delay: None,
                    parachute_cd_area: None,
                },
            ],
        }
    }

    fn single_stage() -> Mission {
        Mission {
            name: "1-Stage".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![Stage {
                name: "Main".into(),
                dry_mass: 20.0,
                motors: vec![crate::sim_core::vehicle::MotorBurn {
                    role: "main".to_string(),
                    propellant_mass: 30.0,
                    thrust: 5000.0,
                    isp: 220.0,
                    thrust_curve: vec![],
                    ignition_delay: 0.0,
                    position_from_nose_m: Vector3::zeros(),
                    nozzle_position_from_nose_m: Vector3::zeros(),
                }],
                cd: 0.3,
                area: 0.008,
                inertia: Vector3::new(5.0, 5.0, 0.5),
                nozzle_offset: 1.0,
                cp_offset: 0.3,
                dry_cg_from_nose: 0.0,
                motor_axial_offset_m: 0.0,
                rotational_fixed_mass_kg: 0.0,
                rotational_fixed_cg_from_nose: 0.0,
                rotational_fixed_cg_radial_m: Vector3::zeros(),
                tvc_max: 0.1,
                cn_alpha: None,
                aero_stability_table: vec![],
                pitch_damping_multiplier: 0.0,
                cd_table: vec![],
                cd_nonfric_table: vec![],
                friction_params: None,
                separation_coast: 0.0,
                parachute_delay: None,
                parachute_cd_area: None,
            }],
        }
    }

    #[test]
    fn single_stage_reaches_apogee() {
        let m = single_stage();
        let config = SimConfig {
            dt: 0.005,
            max_time: 300.0,
        };
        let (traj, _) = simulate(&m, &config);
        let apogee = traj.iter().map(|s| s.pos.z).fold(0.0_f64, f64::max);
        assert!(
            apogee > 1_000.0,
            "Single stage should reach >1 km, got {}",
            apogee
        );
    }

    #[test]
    fn two_stage_higher_than_single() {
        let m1 = single_stage();
        let m2 = two_stage_mission();
        let config = SimConfig {
            dt: 0.005,
            max_time: 300.0,
        };
        let (t1, _) = simulate(&m1, &config);
        let (t2, _) = simulate(&m2, &config);
        let ap1 = t1.iter().map(|s| s.pos.z).fold(0.0_f64, f64::max);
        let ap2 = t2.iter().map(|s| s.pos.z).fold(0.0_f64, f64::max);
        assert!(
            ap2 > ap1,
            "2-stage ({:.0}m) should beat 1-stage ({:.0}m)",
            ap2,
            ap1
        );
    }

    #[test]
    fn ascent_summary_stops_at_apogee_without_propagating_landings() {
        let mut mission = single_stage();
        mission.stages[0].motors[0].propellant_mass = 1.0;
        mission.stages[0].motors[0].thrust = 1000.0;
        let config = SimConfig {
            dt: 0.01,
            max_time: 120.0,
        };
        let mut controller = crate::mission_adapter::NoOpController;
        let summary = simulate_summary_with_mode(
            &mission,
            &config,
            &mut controller,
            PhysicsMode::HyperReal,
            true,
        )
        .expect("ascent summary");

        assert!(summary.apogee_m > 0.0);
        assert!(summary.stage_landings.is_empty());
        assert!(summary.flight_time < config.max_time);
    }

    #[test]
    fn staging_occurs() {
        let m = two_stage_mission();
        let config = SimConfig {
            dt: 0.005,
            max_time: 300.0,
        };
        let (traj, _) = simulate(&m, &config);
        let max_stage = traj.iter().map(|s| s.stage_idx).max().unwrap();
        assert_eq!(max_stage, 1, "Should reach stage index 1 (sustainer)");
    }

    #[test]
    fn staging_respects_separation_coast() {
        // Booster with a 14s post-depletion ejection-charge coast should NOT
        // advance stage_idx immediately at propellant depletion.
        let mut m = two_stage_mission();
        m.stages[0].separation_coast = 14.0;
        let config = SimConfig {
            dt: 0.01,
            max_time: 40.0,
        };
        let (traj, _) = simulate(&m, &config);

        let mid_coast = traj.iter().find(|s| s.time >= 20.0);
        assert_eq!(
            mid_coast.map(|s| s.stage_idx),
            Some(0),
            "Should still be coasting on stage 0 mid-coast, before separation_coast elapses"
        );

        let max_stage = traj.iter().map(|s| s.stage_idx).max().unwrap();
        assert_eq!(
            max_stage, 1,
            "Should eventually reach stage 1 once separation_coast elapses"
        );
    }

    #[test]
    fn booster_separation_respects_ejection_delay() {
        // Ground-truth timeline (see 01-02-PLAN.md <interfaces>): booster
        // burnout at t=5.206s, ejection-charge separation at t=19.206s
        // (14.0s coast). [01-08 update]: depletion is now impulse-weighted
        // from the real `thrust_curve` (see `sixdof::derivatives`'s `dmass`
        // -- wiring this in was this plan's own fix for a real bug where
        // mass depletion used a constant peak-thrust-based rate instead of
        // tracking the curve). The synthetic `thrust_curve` here is
        // therefore a **rectangular** (constant-value) curve rather than a
        // ramp-to-zero: its impulse-weighted depletion is then identical to
        // the old constant-mass-flow model by construction (avg thrust ==
        // peak thrust for a rectangular curve), preserving this test's
        // original ~5.206s depletion/~19.206s separation timing intent
        // without re-deriving it. (A ramp-to-zero curve would only deliver
        // half this constant-model's assumed impulse by burnout_time under
        // the corrected impulse-weighted depletion, which is exactly the
        // kind of curve-shape/depletion-rate mismatch this plan's fix
        // closes -- not a regression.)
        let isp = 220.0;
        let propellant_mass = 30.0;
        let burnout_time = 5.206;
        let mass_flow = propellant_mass / burnout_time;
        let synth_thrust = mass_flow * isp * G0;

        let booster = StageBuilder::new("Booster")
            .dry_mass(40.0)
            .propellant_mass(propellant_mass)
            .thrust(synth_thrust)
            .isp(isp)
            .cd(0.35)
            .area(0.02)
            .inertia(Vector3::new(20.0, 20.0, 2.0))
            .nozzle_offset(1.5)
            .cp_offset(0.4)
            .tvc_max(0.1)
            .thrust_curve(vec![(0.0, synth_thrust), (burnout_time, synth_thrust)])
            .separation_coast(14.0)
            .build();

        let sustainer = StageBuilder::new("Sustainer")
            .dry_mass(10.0)
            .propellant_mass(8.0)
            .thrust(1500.0)
            .isp(250.0)
            .cd(0.3)
            .area(0.01)
            .inertia(Vector3::new(3.0, 3.0, 0.3))
            .nozzle_offset(0.8)
            .cp_offset(0.3)
            .tvc_max(0.08)
            .build();

        let m = Mission {
            name: "Ejection-delay test".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![booster, sustainer],
        };
        let config = SimConfig {
            dt: 0.01,
            max_time: 40.0,
        };
        let (traj, _) = simulate(&m, &config);

        // Should NOT separate immediately at burnout (~5.2s)
        let shortly_after_burnout = traj.iter().find(|s| s.time >= 6.0);
        assert_eq!(
            shortly_after_burnout.map(|s| s.stage_idx),
            Some(0),
            "Should not separate immediately at burnout -- must wait out the ejection-charge delay"
        );

        // Should separate near t=19.2s (burnout_time + 14.0s coast)
        let sep_state = traj.iter().find(|s| s.stage_idx == 1);
        assert!(sep_state.is_some(), "Should eventually separate to stage 1");
        let sep_time = sep_state.unwrap().time;
        assert!(
            (sep_time - 19.206).abs() < 0.5,
            "Separation should occur near t=19.2s, got {}",
            sep_time
        );
    }

    #[test]
    fn quaternion_stays_unit() {
        let m = single_stage();
        let config = SimConfig {
            dt: 0.005,
            max_time: 30.0,
        };
        let (traj, _) = simulate(&m, &config);
        for s in &traj {
            let norm = s.quat.quaternion().norm();
            assert!(
                (norm - 1.0).abs() < 1e-6,
                "Quaternion norm drifted to {} at t={:.2}",
                norm,
                s.time
            );
        }
    }

    #[test]
    fn rocket_returns_to_ground() {
        let m = single_stage();
        let config = SimConfig::default();
        let (traj, _) = simulate(&m, &config);
        let last = traj.last().unwrap();
        assert!(last.pos.z <= 0.01, "Rocket should return to ground");
    }

    #[test]
    fn hyperreal_mode_is_identical_to_fixed_entry_point() {
        let mission = single_stage();
        let config = SimConfig {
            dt: 0.01,
            max_time: 2.0,
        };
        let mut fixed_controller = TvcController::new();
        let mut mode_controller = TvcController::new();
        let (fixed, fixed_commands) = simulate_with(&mission, &config, &mut fixed_controller);
        let (hyperreal, hyperreal_commands) = simulate_with_mode(
            &mission,
            &config,
            &mut mode_controller,
            PhysicsMode::HyperReal,
        );

        assert_eq!(fixed.len(), hyperreal.len());
        assert_eq!(fixed_commands.len(), hyperreal_commands.len());
        for (left, right) in fixed.iter().zip(&hyperreal) {
            assert_eq!(left.time.to_bits(), right.time.to_bits());
            assert_eq!(left.pos, right.pos);
            assert_eq!(left.vel, right.vel);
            assert_eq!(left.mass.to_bits(), right.mass.to_bits());
        }
    }

    #[test]
    fn openrocket_mode_uses_variable_steps() {
        let mut mission = single_stage();
        mission.launch_guide = Some(crate::sim_core::vehicle::LaunchGuide {
            length_m: 2.0,
            direction: Vector3::z(),
        });
        let config = SimConfig {
            dt: 0.005,
            max_time: 0.2,
        };
        let mut controller = TvcController::new();
        let (trajectory, _) = simulate_with_mode(
            &mission,
            &config,
            &mut controller,
            PhysicsMode::OpenRocketLegacy,
        );
        let steps = trajectory
            .windows(2)
            .map(|pair| pair[1].time - pair[0].time)
            .collect::<Vec<_>>();

        assert!(steps.iter().any(|dt| (*dt - config.dt).abs() > 1e-12));
        assert!(steps.iter().all(|dt| *dt > 0.0 && *dt <= config.dt + 1e-12));
    }

    #[test]
    fn checked_sixdof_modes_reject_a_pad_bound_rocket() {
        let mut mission = single_stage();
        mission.launch_guide = Some(crate::sim_core::vehicle::LaunchGuide {
            length_m: 2.0,
            direction: Vector3::z(),
        });
        let stage = &mut mission.stages[0];
        stage.dry_mass = 100.0;
        stage.motors[0].propellant_mass = 1.0;
        stage.motors[0].thrust = 10.0;
        stage.motors[0].thrust_curve = vec![(0.0, 10.0), (0.2, 10.0), (0.21, 0.0)];
        let config = SimConfig { dt: 0.01, max_time: 2.0 };

        for mode in [PhysicsMode::HyperReal, PhysicsMode::OpenRocketLegacy] {
            let mut controller = TvcController::new();
            let result = simulate_summary_with_mode(
                &mission,
                &config,
                &mut controller,
                mode,
                false,
            );
            assert!(matches!(result, Err(ref error) if error == "no_liftoff"));
        }
    }

    #[test]
    fn delayed_retro_propellant_stays_with_detached_branch() {
        let mut mission = two_stage_mission();
        let ascent_duration = mission.stages[0].motors[0].nominal_burn_duration();
        mission.stages[0].separation_coast = 0.0;
        mission.stages[0].motors.push(MotorBurn {
            role: "retro".to_string(),
            propellant_mass: 0.5,
            thrust: 1000.0,
            isp: 200.0,
            thrust_curve: vec![(0.0, 1000.0), (1.0, 1000.0), (1.001, 0.0)],
            ignition_delay: ascent_duration + 5.0,
            position_from_nose_m: Vector3::new(0.5, 0.0, 0.0),
            nozzle_position_from_nose_m: Vector3::new(1.0, 0.0, 0.0),
        });
        let upper_mass = mission.stages[1].total_mass();
        let mut state = State {
            time: ascent_duration,
            pos: Vector3::new(0.0, 0.0, 1000.0),
            vel: Vector3::new(0.0, 0.0, -10.0),
            quat: UnitQuaternion::identity(),
            omega: Vector3::zeros(),
            mass: mission.total_mass(),
            stage_idx: 0,
            stage_activated_at: 0.0,
            stage_depleted_at: None,
            parachute_deployed: false,
        };

        let mut dropped = check_staging(&mut state, &mission).expect("must separate");
        assert!((state.mass - upper_mass).abs() < 1e-9);
        assert!((dropped.mass - (mission.stages[0].dry_mass + 0.5)).abs() < 1e-9);

        let mut branch = mission.clone();
        branch.stages = vec![mission.stages[0].clone()];
        dropped.time = ascent_duration + 5.5;
        let deriv = derivatives(&dropped, &branch, &GncCommand::default());
        assert!(deriv.dmass < 0.0, "detached retro motor must still ignite");
    }

    #[test]
    fn touchdown_interpolation_reports_exact_ground_crossing() {
        let previous = State {
            time: 10.0,
            pos: Vector3::new(10.0, -4.0, 2.0),
            vel: Vector3::new(3.0, 4.0, -8.0),
            quat: UnitQuaternion::identity(),
            omega: Vector3::zeros(),
            mass: 1.0,
            stage_idx: 0,
            stage_activated_at: 0.0,
            stage_depleted_at: Some(1.0),
            parachute_deployed: false,
        };
        let mut current = previous.clone();
        current.time = 12.0;
        current.pos = Vector3::new(16.0, 4.0, -2.0);
        current.vel = Vector3::new(5.0, 8.0, -12.0);

        let landing = interpolate_touchdown(&previous, &current, 3);

        assert_eq!(landing.stage_idx, 3);
        assert!((landing.touchdown_time_s - 11.0).abs() < 1e-12);
        assert!((landing.east_m - 13.0).abs() < 1e-12);
        assert!(landing.north_m.abs() < 1e-12);
        assert!((landing.distance_m - 13.0).abs() < 1e-12);
        assert!((landing.vz_ms + 10.0).abs() < 1e-12);
        assert!((landing.vxy_ms - 7.211_102_550_927_978).abs() < 1e-12);
        assert!((landing.total_speed_ms - 12.328_828_005_937_952).abs() < 1e-12);
    }

    #[test]
    fn checked_summary_reports_every_stage_touchdown() {
        let mission = two_stage_mission();
        let config = SimConfig {
            dt: 0.02,
            max_time: 600.0,
        };
        let mut controller = TvcController::new();

        let summary = simulate_summary_with_mode(
            &mission,
            &config,
            &mut controller,
            PhysicsMode::HyperReal,
            false,
        )
        .expect("both stages should reach the ground");

        assert_eq!(summary.stage_landings.len(), mission.stages.len());
        let loaded_propellant = mission
            .stages
            .iter()
            .map(|stage| stage.propellant_mass())
            .sum::<f64>();
        assert!(
            (summary.total_prop_mass_kg - loaded_propellant).abs() < 1e-6,
            "consumed={} loaded={}",
            summary.total_prop_mass_kg,
            loaded_propellant
        );
        for (expected_idx, landing) in summary.stage_landings.iter().enumerate() {
            assert_eq!(landing.stage_idx, expected_idx);
            assert!(landing.touchdown_time_s.is_finite());
            assert!(landing.total_speed_ms.is_finite());
            assert!(
                (landing.distance_m - landing.east_m.hypot(landing.north_m)).abs() < 1e-9
            );
        }
    }

    #[test]
    fn wind_produces_dynamic_apogee_and_touchdown_displacement() {
        let mut mission = single_stage();
        mission.wind_velocity_mps = Vector3::new(5.0, 0.0, 0.0);
        let config = SimConfig {
            dt: 0.02,
            max_time: 600.0,
        };
        let mut controller = TvcController::new();

        let summary = simulate_summary_with_mode(
            &mission,
            &config,
            &mut controller,
            PhysicsMode::HyperReal,
            false,
        )
        .expect("windy flight should complete");

        assert!(summary.apogee_east_m.abs() > 1.0);
        let landing = summary.stage_landings.first().expect("touchdown");
        assert!(landing.east_m.abs() > 1.0);
        assert!(landing.distance_m.is_finite());
    }
}
