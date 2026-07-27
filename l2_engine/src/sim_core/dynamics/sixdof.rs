use nalgebra::{Quaternion, Vector3};

use crate::sim_core::dynamics::state::{Deriv, EARTH_RADIUS, G0, GncCommand, State};
use crate::sim_core::vehicle::Mission;

// ---------------------------------------------------------------------------
// 6DOF Equations of motion
// ---------------------------------------------------------------------------

/// Compute full 6DOF state derivatives.
///
/// Forces & moments:
///   1. Gravity (inverse-square, inertial frame)
///   2. Thrust with TVC gimbal (body frame → inertial)
///   3. Aerodynamic drag (opposing velocity)
///   4. Aerodynamic restoring moment (CP-CG offset)
///   5. Aerodynamic damping moment
pub fn derivatives(state: &State, mission: &Mission, cmd: &GncCommand) -> Deriv {
    let stage = match mission.active_stage(state.stage_idx) {
        Some(s) => s,
        None => return zero_deriv(state),
    };

    let alt = state.pos.z.max(0.0);
    let atm = mission.atmosphere_at(alt);

    // --- Mass properties ---
    let remaining_prop = state.mass - stage.dry_mass - upper_stages_mass(mission, state.stage_idx);
    let t_since_ignition = state.time - state.stage_activated_at;
    // `Stage::thrust_at(t_since_activation)` takes time-since-STAGE-
    // ACTIVATION and subtracts each motor's own `ignition_delay` internally
    // (summing across all motors -- see stage.rs), so callers must NOT
    // pre-subtract `ignition_delay` themselves. An earlier single-motor
    // version of this file did pre-subtract (to work around a curve-lookup
    // bug in the old, non-aggregating `thrust_at`), but doing so now against
    // the multi-motor `Stage::thrust_at` double-subtracts `ignition_delay`,
    // shifting every motor's curve lookup and silently killing thrust
    // (root-caused while fixing the migration to `Stage.motors: Vec<MotorBurn>`
    // -- `ignition_delay_holds_thrust` failed with zero thrust past the
    // delay until this was corrected to pass `t_since_ignition` raw).
    let burning = remaining_prop > stage.propellant_depletion_tolerance_kg()
        && stage.thrust_at(t_since_ignition) > 0.0;

    // --- Gravity (inertial) ---
    let g = G0 * (EARTH_RADIUS / (EARTH_RADIUS + alt)).powi(2);
    let f_gravity = Vector3::new(0.0, 0.0, -g * state.mass);

    // --- Thrust (body frame → inertial) ---
    let dynamic_cg_3d = stage.cg_from_nose_3d_at(t_since_ignition);
    let gy = cmd.gimbal_y.clamp(-stage.tvc_max, stage.tvc_max);
    let gz = cmd.gimbal_z.clamp(-stage.tvc_max, stage.tvc_max);
    let mut f_thrust_body = Vector3::zeros();
    let mut thrust_torque_body = Vector3::zeros();
    if burning {
        for motor in &stage.motors {
            let motor_time = t_since_ignition - motor.ignition_delay;
            let motor_thrust = motor.thrust_at(motor_time);
            if motor_thrust <= 0.0 {
                continue;
            }
            let force = Vector3::new(
                motor_thrust * gz.sin(),
                motor_thrust * gy.sin(),
                motor_thrust * gy.cos() * gz.cos(),
            );
            // Geometry uses (axial-from-nose, radial-y, radial-z), while the
            // dynamics body frame uses +Z along the rocket axis. Convert the
            // CG-to-nozzle arm before applying tau = r x F.
            let arm = Vector3::new(
                motor.nozzle_position_from_nose_m.y - dynamic_cg_3d.y,
                motor.nozzle_position_from_nose_m.z - dynamic_cg_3d.z,
                dynamic_cg_3d.x - motor.nozzle_position_from_nose_m.x,
            );
            f_thrust_body += force;
            thrust_torque_body += arm.cross(&force);
        }
    }
    // [01-08 bug fix]: skip the quaternion rotation entirely when not
    // burning, rather than relying on `state.quat * Vector3::zeros()` to
    // yield an exact zero vector. Evidence: at this vehicle's extreme
    // dynamic pressure (q_dyn ~4e5 Pa near Mach 7 at low altitude), the
    // aerodynamic damping torque (`torque_body -= state.omega * damp`) is
    // numerically stiff relative to the integration timestep (damp/I * dt
    // far exceeds RK4's absolute-stability bound for a pure decay term),
    // so `omega`/`quat` can diverge to `NaN` purely from floating-point
    // round-off noise over a long coast, with **zero physical effect on
    // translational motion** since this is a ballistic no-op-controller
    // flight (rotational dynamics is out of scope for apogee/Mach
    // accuracy -- see mission_adapter.rs's `NoOpController`/01-07-SUMMARY's
    // inertia-placeholder caveat). Without this guard, `0.0 * NaN = NaN`
    // in the rotation leaks a NaN thrust force into `f_total`/`accel` even
    // though thrust magnitude is exactly zero, corrupting position/
    // velocity for the rest of the flight (root-caused via 01-08's
    // diagnostic trace: trajectory went finite -> NaN at t=454.88s, deep
    // in the unpowered coast/descent, with the immediately-preceding state
    // still fully finite).
    let f_thrust_inertial = if burning {
        state.quat * f_thrust_body
    } else {
        Vector3::zeros()
    };

    // --- Aerodynamic drag (inertial, opposing air-relative velocity) ---
    let wind_vel = if let Some(wp) = &mission.wind_profile {
        let (e, n) = wp.wind_vector_at(alt);
        Vector3::new(e, n, 0.0)
    } else {
        mission.wind_velocity_mps
    };

    let air_relative_vel = state.vel - wind_vel;
    let speed = air_relative_vel.norm();
    let mach = if atm.sound_speed > 0.0 {
        speed / atm.sound_speed
    } else {
        0.0
    };
    let cd = stage.cd_at_conditions(mach, speed, atm.kinematic_viscosity);
    let f_drag = if speed > 1e-6 {
        let q_dyn = 0.5 * atm.density * speed * speed;
        let mut drag_mag = q_dyn * cd * stage.area;
        if state.parachute_deployed {
            if let Some(cd_area) = stage.parachute_cd_area {
                drag_mag += q_dyn * cd_area;
            }
        }
        -air_relative_vel.normalize() * drag_mag
    } else {
        Vector3::zeros()
    };

    // --- Aerodynamic normal force and Mach-dependent stability ---
    let vel_body = state.quat.inverse() * air_relative_vel;
    let mut alpha_y = if speed > 1.0 {
        vel_body.y.atan2(vel_body.z)
    } else {
        0.0
    };
    let mut alpha_z = if speed > 1.0 {
        vel_body.x.atan2(vel_body.z)
    } else {
        0.0
    };
    let alpha = alpha_y.hypot(alpha_z);
    // The Mach/AOA stability table includes the dynamic Galejs body-lift CN
    // and planform CP computed by barrowman::body_lift_at_aoa.
    let (dry_cp_offset, cn_alpha, _damping_moment_sum_m2) = stage.stability_at(mach, alpha);
    let cp_from_nose = stage.dry_cg_from_nose + dry_cp_offset;
    let dynamic_cg = dynamic_cg_3d.x;
    let cp_offset = cp_from_nose - dynamic_cg;
    let mut f_normal_body = Vector3::zeros();
    if speed > 1.0 {
        const STALL_ANGLE_RAD: f64 = 20.0 * std::f64::consts::PI / 180.0;
        if alpha > STALL_ANGLE_RAD {
            let scale = STALL_ANGLE_RAD / alpha;
            alpha_y *= scale;
            alpha_z *= scale;
        }
        let normal_slope_force = 0.5 * atm.density * speed * speed * stage.area * cn_alpha;
        // Oppose the lateral air-relative velocity in body coordinates.
        f_normal_body = Vector3::new(
            -normal_slope_force * alpha_z,
            -normal_slope_force * alpha_y,
            0.0,
        );
    }
    let f_normal_inertial = state.quat * f_normal_body;

    // --- Total force → translational acceleration ---
    let f_total = f_gravity + f_thrust_inertial + f_drag + f_normal_inertial;
    let accel = f_total / state.mass;
    let on_launch_guide = mission
        .launch_guide
        .as_ref()
        .is_some_and(|guide| state.pos.dot(&guide.direction) < guide.length_m);

    // --- Torques (body frame) ---
    let mut torque_body = thrust_torque_body;

    // The same normal force acts at the CP and therefore produces the pitch
    // and yaw moment. Previously only this torque was modeled; omitting the
    // force left velocity vertical while attitude rotated, creating huge AoA
    // oscillations instead of OpenRocket's weathercocked trajectory.
    let mut aerodynamic_torque_body = Vector3::zeros();
    if speed > 1.0 && cp_offset.abs() > 1e-6 {
        let cp_arm = Vector3::new(0.0, 0.0, -cp_offset);
        aerodynamic_torque_body = cp_arm.cross(&f_normal_body);
        torque_body += aerodynamic_torque_body;
    }

    // OpenRocket's flight-dynamics damping is nonlinear in angular rate and
    // capped by the corrective moment (BarrowmanStabilityCalculator), not
    // the linear diagnostic coefficient recorded in FlightData.
    if speed > 1.0 {
        let q_dyn = 0.5 * atm.density * speed * speed;
        let reference_length = (stage.area / std::f64::consts::PI).sqrt() * 2.0;
        let scale = q_dyn * stage.area * reference_length * 3.0 * stage.pitch_damping_multiplier;
        let pitch_damping =
            (scale * (state.omega.x / speed).powi(2)).min(aerodynamic_torque_body.x.abs());
        let yaw_damping =
            (scale * (state.omega.y / speed).powi(2)).min(aerodynamic_torque_body.y.abs());
        torque_body.x -= state.omega.x.signum() * pitch_damping;
        torque_body.y -= state.omega.y.signum() * yaw_damping;
    }

    // --- Euler's equation: I * domega = torque - omega × (I * omega) ---
    let i_vec = stage.inertia;
    let i_omega = Vector3::new(
        i_vec.x * state.omega.x,
        i_vec.y * state.omega.y,
        i_vec.z * state.omega.z,
    );
    let domega = Vector3::new(
        (torque_body.x - (state.omega.y * i_omega.z - state.omega.z * i_omega.y)) / i_vec.x,
        (torque_body.y - (state.omega.z * i_omega.x - state.omega.x * i_omega.z)) / i_vec.y,
        (torque_body.z - (state.omega.x * i_omega.y - state.omega.y * i_omega.x)) / i_vec.z,
    );

    // --- Quaternion kinematics: dq/dt = 0.5 * q * omega_quat ---
    let omega_quat = Quaternion::new(0.0, state.omega.x, state.omega.y, state.omega.z);
    let dquat = state.quat.quaternion() * omega_quat * 0.5;

    // --- Mass flow ---
    // When a real thrust curve is present, mass flow must track the curve's
    // instantaneous thrust (thrust_at(t)/(isp*g0)), not the constant
    // peak-thrust-based `mass_flow()` rate. `isp` is derived as
    // total_impulse/(propellant_mass*g0) (see mission_adapter.rs), so
    // integrating thrust_at(t)/(isp*g0) over the curve's full duration
    // exactly consumes propellant_mass -- self-consistent with the real
    // burn duration. Using the constant `mass_flow()` rate here (based on
    // `stage.thrust` = peak thrust from the curve) depletes propellant far
    // earlier than the curve's actual burn time whenever peak thrust
    // exceeds average thrust, silently discarding the curve's tail-end
    // impulse (evidence: N4800T curve burns to t=5.206s, but the constant
    // peak-thrust model exhausted "modeled" propellant by t=3.21s,
    // discarding ~45% of the real impulse -- root cause of 01-08's initial
    // apogee/Mach validation failure, ~68%/59% low). Falls back to the
    // pre-existing constant-flow formula for constant-thrust stages
    // (empty thrust_curve), preserving all prior behavior/tests.
    let dmass = -stage
        .motors
        .iter()
        .map(|motor| {
            let motor_time = t_since_ignition - motor.ignition_delay;
            let thrust = motor.thrust_at(motor_time);
            if thrust <= 0.0 || motor.isp <= 0.0 {
                0.0
            } else if motor.thrust_curve.is_empty() {
                motor.mass_flow()
            } else {
                thrust / (motor.isp * G0)
            }
        })
        .sum::<f64>();

    if on_launch_guide {
        let guide = mission.launch_guide.as_ref().expect("checked above");
        let rod_speed = state.vel.dot(&guide.direction).max(0.0);
        let rod_accel = accel.dot(&guide.direction).max(0.0);
        return Deriv {
            dpos: guide.direction * rod_speed,
            dvel: guide.direction * rod_accel,
            dquat: Quaternion::new(0.0, 0.0, 0.0, 0.0),
            domega: Vector3::zeros(),
            dmass,
        };
    }

    Deriv {
        dpos: state.vel,
        dvel: accel,
        dquat,
        domega,
        dmass,
    }
}

pub(crate) fn upper_stages_mass(mission: &Mission, current_idx: usize) -> f64 {
    mission.stages[current_idx + 1..]
        .iter()
        .map(|s| s.total_mass())
        .sum()
}

fn zero_deriv(state: &State) -> Deriv {
    // Post-mission: only gravity
    let alt = state.pos.z.max(0.0);
    let g = G0 * (EARTH_RADIUS / (EARTH_RADIUS + alt)).powi(2);
    Deriv {
        dpos: state.vel,
        dvel: Vector3::new(0.0, 0.0, -g),
        dquat: Quaternion::new(0.0, 0.0, 0.0, 0.0),
        domega: Vector3::zeros(),
        dmass: 0.0,
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::vehicle::{MotorBurn, Stage};
    use nalgebra::UnitQuaternion;

    fn test_mission() -> Mission {
        Mission {
            name: "Test".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![Stage {
                name: "S1".into(),
                dry_mass: 20.0,
                motors: vec![crate::sim_core::vehicle::MotorBurn {
                    role: "main".to_string(),
                    propellant_mass: 10.0,
                    thrust: 2000.0,
                    isp: 220.0,
                    thrust_curve: vec![],
                    ignition_delay: 0.0,
                    position_from_nose_m: Vector3::zeros(),
                    nozzle_position_from_nose_m: Vector3::new(1.0, 0.0, 0.0),
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

    fn pad_state(mission: &Mission) -> State {
        State {
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
        }
    }

    #[test]
    fn net_upward_accel_on_pad() {
        let m = test_mission();
        let s = pad_state(&m);
        let d = derivatives(&s, &m, &GncCommand::default());
        assert!(d.dvel.z > 0.0, "TWR > 1 → net upward, got {}", d.dvel.z);
    }

    #[test]
    fn tvc_creates_torque() {
        let m = test_mission();
        let s = pad_state(&m);
        let cmd = GncCommand {
            gimbal_y: 0.05,
            gimbal_z: 0.0,
        };
        let d = derivatives(&s, &m, &cmd);
        assert!(d.domega.x.abs() > 1e-6, "TVC should create pitch torque");
    }

    fn radial_motor(angle: f64, cutoff_s: f64) -> MotorBurn {
        let radius = 0.5;
        MotorBurn {
            role: "main".to_string(),
            propellant_mass: 1.0,
            thrust: 1000.0,
            isp: 200.0,
            thrust_curve: vec![(0.0, 1000.0), (cutoff_s, 1000.0), (cutoff_s + 0.001, 0.0)],
            ignition_delay: 0.0,
            position_from_nose_m: Vector3::new(
                0.5,
                radius * angle.cos(),
                radius * angle.sin(),
            ),
            nozzle_position_from_nose_m: Vector3::new(
                1.0,
                radius * angle.cos(),
                radius * angle.sin(),
            ),
        }
    }

    #[test]
    fn symmetric_three_pod_thrust_cancels_radial_torque() {
        let mut mission = test_mission();
        mission.stages[0].motors = (0..3)
            .map(|index| radial_motor(std::f64::consts::TAU * index as f64 / 3.0, 1.0))
            .collect();
        let mut state = pad_state(&mission);
        state.time = 0.5;

        let deriv = derivatives(&state, &mission, &GncCommand::default());

        assert!(
            deriv.domega.x.hypot(deriv.domega.y) < 1e-9,
            "symmetric pod torque must cancel, got {:?}",
            deriv.domega
        );
    }

    #[test]
    fn asymmetric_pod_flameout_produces_rotation() {
        let mut mission = test_mission();
        mission.stages[0].motors = (0..3)
            .map(|index| {
                radial_motor(
                    std::f64::consts::TAU * index as f64 / 3.0,
                    if index == 0 { 0.4 } else { 1.0 },
                )
            })
            .collect();
        let mut state = pad_state(&mission);
        state.time = 0.6;

        let deriv = derivatives(&state, &mission, &GncCommand::default());

        assert!(
            deriv.domega.x.hypot(deriv.domega.y) > 1e-3,
            "one early pod cutoff must create torque, got {:?}",
            deriv.domega
        );
    }

    #[test]
    fn ignition_delay_holds_thrust() {
        let mut m = test_mission();
        m.stages[0].motors[0].ignition_delay = 2.5;

        // Before ignition_delay elapses: stage_idx already active, propellant
        // remains, but thrust must stay off.
        let mut s_before = pad_state(&m);
        s_before.time = 1.0; // 1.0s since stage_activated_at (0.0) < 2.5s delay
        let d_before = derivatives(&s_before, &m, &GncCommand::default());
        assert!(
            d_before.dvel.z < 0.0,
            "No thrust before ignition_delay elapses -- should be falling under gravity alone, got dvel.z={}",
            d_before.dvel.z
        );
        assert!(
            d_before.dmass.abs() < 1e-10,
            "No propellant should be consumed before ignition_ready"
        );

        // After ignition_delay elapses: thrust resumes.
        let mut s_after = pad_state(&m);
        s_after.time = 3.0; // 3.0s since ignition >= 2.5s delay
        let d_after = derivatives(&s_after, &m, &GncCommand::default());
        assert!(
            d_after.dvel.z > 0.0,
            "Thrust should resume once ignition_delay has elapsed, got dvel.z={}",
            d_after.dvel.z
        );
    }

    #[test]
    fn no_thrust_after_burnout() {
        let m = test_mission();
        let s = State {
            time: 100.0,
            pos: Vector3::new(0.0, 0.0, 5000.0),
            vel: Vector3::new(0.0, 0.0, 200.0),
            quat: UnitQuaternion::identity(),
            omega: Vector3::zeros(),
            mass: m.stages[0].dry_mass,
            stage_idx: 0,
            stage_activated_at: 0.0,
            stage_depleted_at: None,
            parachute_deployed: false,
        };
        let d = derivatives(&s, &m, &GncCommand::default());
        assert!(d.dvel.z < 0.0, "Only gravity + drag after burnout");
        assert!(d.dmass.abs() < 1e-10);
    }

    #[test]
    fn small_motor_tail_burns_below_ten_grams_remaining() {
        let mut m = test_mission();
        m.stages[0].friction_params = Some(crate::sim_core::vehicle::FrictionParams {
            vehicle_length: 1.0,
            wetted_area_ratio: 1.0,
            body_wetted_area_ratio: 1.0,
            body_fineness_ratio: 10.0,
            roughness_m: 1e-6,
            model: crate::sim_core::vehicle::FrictionModel::OpenRocketLegacy,
        });
        let mut s = pad_state(&m);
        s.mass = m.stages[0].dry_mass + 0.005;
        let d = derivatives(&s, &m, &GncCommand::default());
        assert!(d.dmass < 0.0, "5 g of propellant must still be consumed");
        assert!(d.dvel.z > 0.0, "tail thrust must remain active");
    }

    #[test]
    fn quat_deriv_zero_at_rest() {
        let m = test_mission();
        let s = pad_state(&m);
        let d = derivatives(&s, &m, &GncCommand::default());
        let dq_norm =
            (d.dquat.w.powi(2) + d.dquat.i.powi(2) + d.dquat.j.powi(2) + d.dquat.k.powi(2)).sqrt();
        assert!(dq_norm < 1e-10, "No rotation → zero quat derivative");
    }

    #[test]
    fn launch_guide_projects_motion_until_clear() {
        let mut m = test_mission();
        m.wind_velocity_mps = Vector3::new(5.0, 0.0, 0.0);
        m.launch_guide = Some(crate::sim_core::vehicle::LaunchGuide {
            length_m: 2.0,
            direction: Vector3::z(),
        });
        let s = pad_state(&m);
        let d = derivatives(&s, &m, &GncCommand::default());
        assert_eq!(d.dpos.x, 0.0);
        assert_eq!(d.dvel.x, 0.0);
        assert_eq!(d.domega, Vector3::zeros());
    }
}
