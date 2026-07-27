use std::collections::HashMap;
use std::fs;
use std::io::{self, Read};
use std::path::PathBuf;

use l2_engine::PhysicsMode;
use l2_engine::ast::{AstCandidate, ast_to_geometry, enrich_ast_motor_mounts};
use l2_engine::mission_adapter::{NoOpController, apply_openrocket_environment, build_mission};
use l2_engine::motor_db::{self, ThrustCurve};
use l2_engine::sim_core::dynamics::state::SimConfig;
use l2_engine::sim_core::io::json::FlightSummary;
use l2_engine::sim_core::physics::atmosphere;
use serde::Serialize;

#[derive(Serialize)]
struct TracePoint {
    time_s: f64,
    altitude_m: f64,
    speed_mps: f64,
    vertical_speed_mps: f64,
    horizontal_speed_mps: f64,
    downrange_m: f64,
    mach: f64,
    density_kg_m3: f64,
    pressure_pa: f64,
    temperature_k: f64,
    stage_idx: usize,
    mass_kg: f64,
    thrust_n: f64,
    drag_n: f64,
    drag_cd: f64,
    friction_drag_cd: f64,
    nonfriction_drag_cd: f64,
    reference_area_m2: f64,
    orientation_theta_rad: f64,
    angle_of_attack_rad: f64,
    pitch_rate_rad_s: f64,
    longitudinal_inertia_kg_m2: f64,
    rotational_inertia_kg_m2: f64,
    cp_offset_m: f64,
    cn_alpha: f64,
    cp_location_m: f64,
    cg_location_m: f64,
    pitch_damping_multiplier: f64,
}

#[derive(Serialize)]
struct TraceSummary {
    integration_dt_s: f64,
    apogee_m: f64,
    apogee_time_s: f64,
    max_speed_mps: f64,
    max_mach: f64,
    max_accel_g: f64,
    flight_time_s: f64,
    impact_speed_mps: f64,
}

#[derive(Serialize)]
struct TraceOutput {
    id: String,
    summary: TraceSummary,
    points: Vec<TracePoint>,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let input = arg(&args, "--input")
        .map(|path| fs::read_to_string(path).expect("read --input file"))
        .unwrap_or_else(read_stdin);
    let candidate: AstCandidate = serde_json::from_str(&input).expect("parse AST candidate JSON");

    let curves_by_designation = load_motor_curves();
    let mut geometry = ast_to_geometry(&candidate.ast).expect("compile AST geometry");
    let curves = geometry
        .stages
        .iter()
        .map(|stage| {
            curves_by_designation
                .get(&stage.motor_mount.motor_designation)
                .cloned()
                .unwrap_or_else(|| {
                    panic!(
                        "missing motor curve {}",
                        stage.motor_mount.motor_designation
                    )
                })
        })
        .collect::<Vec<_>>();
    enrich_ast_motor_mounts(&mut geometry, &curves);
    let mut mission =
        build_mission(&geometry, &curves, PhysicsMode::OpenRocketLegacy).expect("build mission");
    if let Some(environment) = candidate.environment {
        apply_openrocket_environment(&mut mission, environment);
    }
    let mut controller = NoOpController;
    // Keep diagnostics bit-for-bit aligned with `ast_eval`; using
    // `SimConfig::default()` here previously made the curve tool analyze a
    // different integration step than the scorer it was meant to explain.
    let trace_dt = std::env::var("L2_TRACE_DT")
        .ok()
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value > 0.0)
        .unwrap_or(0.005);
    let dense_trace = std::env::var_os("L2_TRACE_DENSE").is_some();
    let config = SimConfig {
        dt: trace_dt,
        max_time: 600.0,
    };
    let (trajectory, _commands) = l2_engine::sim_core::sim::simulate_with_mode(
        &mission,
        &config,
        &mut controller,
        PhysicsMode::OpenRocketLegacy,
    );
    let summary = FlightSummary::from_trajectory_with_wind(&trajectory, &mission);

    let get_wind = |alt: f64| -> nalgebra::Vector3<f64> {
        if let Some(wp) = &mission.wind_profile {
            let (e, n) = wp.wind_vector_at(alt);
            nalgebra::Vector3::new(e, n, 0.0)
        } else {
            mission.wind_velocity_mps
        }
    };

    let stride = ((trajectory.len() as f64) / 600.0).ceil().max(1.0) as usize;
    let points = trajectory
        .iter()
        .enumerate()
        .filter(|(index, state)| {
            if dense_trace && state.time <= 15.0 {
                true
            } else if state.time <= 15.0 {
                index % 10 == 0
            } else {
                index % stride == 0
            }
        })
        .map(|(_, state)| {
            let atmo = atmosphere::isa(state.pos.z);
            let wind = get_wind(state.pos.z.max(0.0));
            let speed = (state.vel - wind).norm();
            let air_relative_vel = state.vel - wind;
            let body_axis = state.quat * nalgebra::Vector3::z();
            let orientation_theta_rad = body_axis.z.clamp(-1.0, 1.0).acos();
            let angle_of_attack_rad = if speed > 1e-9 {
                body_axis
                    .dot(&air_relative_vel.normalize())
                    .clamp(-1.0, 1.0)
                    .acos()
            } else {
                0.0
            };
            let (
                thrust_n,
                drag_n,
                drag_cd,
                friction_drag_cd,
                nonfriction_drag_cd,
                reference_area_m2,
                cp_offset_m,
                cn_alpha,
                cp_location_m,
                cg_location_m,
            ) = mission
                .active_stage(state.stage_idx)
                .map(|stage| {
                    let mach = speed / atmo.sound_speed.max(1.0);
                    let cd = stage.cd_at_conditions(mach, speed, atmo.kinematic_viscosity);
                    let (friction_cd, nonfriction_cd) =
                        stage.cd_components_at_conditions(mach, speed, atmo.kinematic_viscosity);
                    let drag = 0.5 * atmo.density * speed * speed * cd * stage.area;
                    let since_ignition =
                        (state.time - state.stage_activated_at - stage.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0)).max(0.0);
                    let (dry_cp_offset, cn_alpha, _damping_moment_sum_m2) =
                        stage.stability_at(mach, angle_of_attack_rad);
                    let upper_mass = mission.stages[state.stage_idx + 1..]
                        .iter()
                        .map(|upper| upper.total_mass())
                        .sum::<f64>();
                    let remaining_propellant = (state.mass - stage.dry_mass - upper_mass).max(0.0);
                    let ignition_ready =
                        state.time - state.stage_activated_at >= stage.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0);
                    let interpolated_thrust = stage.thrust_at(since_ignition);
                    let actual_thrust = if remaining_propellant
                        > stage.propellant_depletion_tolerance_kg()
                        && ignition_ready
                        && (interpolated_thrust > 0.0
                            || stage.motors.first().map(|m| m.thrust).unwrap_or(0.0) > 0.0)
                    {
                        interpolated_thrust
                    } else {
                        0.0
                    };
                    let cg = stage.cg_from_nose_at_propellant(remaining_propellant);
                    let cp = stage.dry_cg_from_nose + dry_cp_offset;
                    (
                        actual_thrust,
                        drag,
                        cd,
                        friction_cd,
                        nonfriction_cd,
                        stage.area,
                        cp - cg,
                        cn_alpha,
                        cp,
                        cg,
                    )
                })
                .unwrap_or((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0));
            TracePoint {
                time_s: state.time,
                altitude_m: state.pos.z,
                speed_mps: speed,
                vertical_speed_mps: state.vel.z,
                horizontal_speed_mps: (state.vel.x.powi(2) + state.vel.y.powi(2)).sqrt(),
                downrange_m: (state.pos.x.powi(2) + state.pos.y.powi(2)).sqrt(),
                mach: speed / atmo.sound_speed.max(1.0),
                density_kg_m3: atmo.density,
                pressure_pa: atmo.pressure,
                temperature_k: atmo.temperature,
                stage_idx: state.stage_idx,
                mass_kg: state.mass,
                thrust_n,
                drag_n,
                drag_cd,
                friction_drag_cd,
                nonfriction_drag_cd,
                reference_area_m2,
                orientation_theta_rad,
                angle_of_attack_rad,
                pitch_rate_rad_s: state.omega.x.hypot(state.omega.y),
                longitudinal_inertia_kg_m2: mission
                    .active_stage(state.stage_idx)
                    .map(|stage| stage.inertia.x)
                    .unwrap_or(0.0),
                rotational_inertia_kg_m2: mission
                    .active_stage(state.stage_idx)
                    .map(|stage| stage.inertia.z)
                    .unwrap_or(0.0),
                cp_offset_m,
                cn_alpha,
                cp_location_m,
                cg_location_m,
                pitch_damping_multiplier: mission
                    .active_stage(state.stage_idx)
                    .map(|stage| stage.pitch_damping_multiplier)
                    .unwrap_or(0.0),
            }
        })
        .collect();

    let output = TraceOutput {
        id: candidate.id,
        summary: TraceSummary {
            integration_dt_s: trace_dt,
            apogee_m: summary.apogee_m,
            apogee_time_s: summary.apogee_time,
            max_speed_mps: summary.max_speed,
            max_mach: summary.max_mach,
            max_accel_g: summary.max_accel_g,
            flight_time_s: summary.flight_time,
            impact_speed_mps: summary.impact_speed,
        },
        points,
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&output).expect("serialize trace")
    );
}

fn load_motor_curves() -> HashMap<String, ThrustCurve> {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("motors");
    let mut curves = HashMap::new();
    for entry in fs::read_dir(&base).unwrap_or_else(|e| panic!("read motors dir {base:?}: {e}")) {
        let path = entry.expect("read motors dir entry").path();
        if path.extension().and_then(|e| e.to_str()) != Some("eng") {
            continue;
        }
        let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {path:?}: {e}"));
        let (designation, curve) = motor_db::parse_eng_file(&text)
            .unwrap_or_else(|e| panic!("bad motor file {path:?}: {e:?}"));
        curves.insert(designation, curve);
    }
    curves
}

fn arg(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone())
}

fn read_stdin() -> String {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    input
}
