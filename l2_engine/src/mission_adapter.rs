//! Assembles a `rocket-sim` `Mission`/`StageBuilder` from the outputs of
//! Plans 03-06 (parsed geometry, mass, aerodynamics, motor thrust curve),
//! wires in the timing values Plan 02's patch made possible
//! (`ignition_delay`, `separation_coast`), supplies a no-op GNC controller
//! (this phase is ballistic-only, per 01-CONTEXT.md), and exposes a single
//! top-level `simulate_rocket()` entry point. See 01-07-PLAN.md.
//!
//! **CRITICAL timing-value distinction** (see 01-07-PLAN.md's `<interfaces>`
//! block, verified by direct `.ork` inspection): a stage's `separation_coast`
//! is sourced from that stage's own motor's ejection-charge `<delay>` tag
//! (`MotorMountGeometry::ejection_charge_delay`, added in this plan), NOT
//! from `StageGeometry.separation.delay` (which mirrors the unrelated
//! stage-level `<separationdelay>` XML tag). A stage's `ignition_delay` is
//! sourced directly from `MotorMountGeometry::ignition_delay` and passed
//! through unchanged -- rocket-sim's post-Plan-02 semantics already measure
//! it relative to the previous stage's separation/activation time.

use crate::errors::L2EngineError;
use crate::geometry::RocketGeometry;
use crate::mass_calculator;
use crate::motor_db::{self, ThrustCurve};
use crate::{barrowman, xml_parser};

use crate::sim_core::dynamics::state::{G0, GncCommand, SimConfig, State};
use crate::sim_core::gnc::Controller;
use crate::sim_core::io::json::FlightSummary;
use crate::sim_core::vehicle::{Mission, MissionBuilder, MotorBurn, StageBuilder};
use nalgebra::Vector3;
use roxmltree::{Document, Node};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrkSimulationEnvironment {
    pub launch_rod_length_m: f64,
    pub launch_rod_angle_rad: f64,
    pub launch_rod_direction_rad: f64,
    pub wind_speed_mps: f64,
    pub wind_direction_rad: f64,
    #[serde(default)]
    pub relative_humidity: f64,
    #[serde(default)]
    pub wind_levels: Vec<crate::sim_core::wind::WindLevel>,
    #[serde(default = "standard_temperature_k")]
    pub base_temperature_k: f64,
    #[serde(default = "standard_pressure_pa")]
    pub base_pressure_pa: f64,
    #[serde(default)]
    pub launch_altitude_m: f64,
}

fn standard_temperature_k() -> f64 { 288.15 }
fn standard_pressure_pa() -> f64 { 101_325.0 }

/// No-op GNC controller: always returns the zero/default `GncCommand`,
/// bypassing TVC/guidance entirely. This phase is ballistic-only (unguided
/// vertical flight, no attitude control) -- full attitude-controlled 6-DOF
/// is explicitly out of scope (01-CONTEXT.md's Deferred Ideas / PHYS-01).
pub struct NoOpController;

impl Controller for NoOpController {
    fn control(&mut self, _state: &State, _mission: &Mission, _dt: f64) -> GncCommand {
        GncCommand::default()
    }

    fn name(&self) -> &str {
        "no-op"
    }
}

/// Assembles a `crate::sim_core::vehicle::Mission` from parsed `.ork` geometry and
/// a single motor thrust curve (both stages of the reference vehicle use the
/// same N4800T motor). `geometry.stages` is already ignition-ordered
/// (index 0 fires first) by `xml_parser::parse_rocket_geometry`, so stages
/// are built and appended to the `MissionBuilder` in that same order without
/// any special-casing per stage index.
pub fn build_mission(
    geometry: &RocketGeometry,
    thrust_curves: &[ThrustCurve],
    physics_mode: crate::PhysicsMode,
) -> Result<Mission, L2EngineError> {
    let clusters = thrust_curves
        .iter()
        .cloned()
        .map(|curve| vec![curve])
        .collect::<Vec<_>>();
    build_mission_with_motor_clusters(geometry, &clusters, physics_mode)
}

/// Combine co-located motor curves for legacy mass/stability calculations.
/// Thrust is summed at every input knot; wet/dry masses are additive.
pub fn aggregate_motor_curves(curves: &[ThrustCurve]) -> Result<ThrustCurve, L2EngineError> {
    if curves.is_empty() {
        return Err(L2EngineError::ParseError(
            "motor cluster must not be empty".into(),
        ));
    }
    let mut times = curves
        .iter()
        .flat_map(|c| c.time_s.iter().copied())
        .collect::<Vec<_>>();
    times.sort_by(f64::total_cmp);
    times.dedup_by(|a, b| (*a - *b).abs() < 1e-12);
    let thrust_n = times
        .iter()
        .map(|&t| curves.iter().map(|c| interpolate_curve(c, t)).sum())
        .collect();
    Ok(ThrustCurve {
        time_s: times,
        thrust_n,
        propellant_mass_kg: curves.iter().map(|c| c.propellant_mass_kg).sum(),
        total_mass_kg: curves.iter().map(|c| c.total_mass_kg).sum(),
        diameter_m: curves.iter().map(|c| c.diameter_m).fold(0.0, f64::max),
        length_m: curves.iter().map(|c| c.length_m).fold(0.0, f64::max),
    })
}

fn interpolate_curve(curve: &ThrustCurve, t: f64) -> f64 {
    if curve.time_s.is_empty() || t < curve.time_s[0] || t > *curve.time_s.last().unwrap() {
        return 0.0;
    }
    for (tw, fw) in curve.time_s.windows(2).zip(curve.thrust_n.windows(2)) {
        if t >= tw[0] && t <= tw[1] {
            let span = tw[1] - tw[0];
            return if span.abs() < 1e-12 {
                fw[1]
            } else {
                fw[0] + (fw[1] - fw[0]) * (t - tw[0]) / span
            };
        }
    }
    *curve.thrust_n.last().unwrap_or(&0.0)
}

pub fn build_mission_with_motor_clusters(
    geometry: &RocketGeometry,
    motor_clusters: &[Vec<ThrustCurve>],
    physics_mode: crate::PhysicsMode,
) -> Result<Mission, L2EngineError> {
    build_mission_with_motor_clusters_profile(geometry, motor_clusters, physics_mode, false)
}

/// Coarse stability-grid variant for population screening. It preserves the
/// same 6-DOF force/moment equations and only reduces coefficient-table setup.
pub fn build_mission_with_motor_clusters_fast(
    geometry: &RocketGeometry,
    motor_clusters: &[Vec<ThrustCurve>],
    physics_mode: crate::PhysicsMode,
) -> Result<Mission, L2EngineError> {
    build_mission_with_motor_clusters_profile(geometry, motor_clusters, physics_mode, true)
}

fn build_mission_with_motor_clusters_profile(
    geometry: &RocketGeometry,
    motor_clusters: &[Vec<ThrustCurve>],
    physics_mode: crate::PhysicsMode,
    fast_aero_grid: bool,
) -> Result<Mission, L2EngineError> {
    let thrust_curves = motor_clusters
        .iter()
        .map(|cluster| aggregate_motor_curves(cluster))
        .collect::<Result<Vec<_>, _>>()?;
    if geometry.stages.len() != thrust_curves.len() {
        return Err(L2EngineError::ParseError(format!(
            "build_mission: {} stages but {} thrust curves provided",
            geometry.stages.len(),
            thrust_curves.len()
        )));
    }

    // Reject motor/airframe combinations that cannot physically fit before
    // any physics runs -- matching l2_hyper's own "airframe ID vs motor
    // diameter with 1mm radial clearance" rule so the Rust proxy rejects the
    // same designs OpenRocket would, instead of scoring them as valid and
    // only catching the mismatch during ground-truth validation (see
    // docs/organic_loop_report.md #3).
    const MOTOR_RADIAL_CLEARANCE_M: f64 = 0.001;
    for (stage, cluster) in geometry.stages.iter().zip(motor_clusters.iter()) {
        let expanded_mounts = std::iter::once(&stage.motor_mount)
            .chain(stage.auxiliary_motor_mounts.iter())
            .flat_map(|mount| (0..mount.multiplicity).map(move |instance| (mount, instance)))
            .collect::<Vec<_>>();
        if expanded_mounts.len() != cluster.len() {
            return Err(L2EngineError::ParseError(format!(
                "motor/mount multiplicity mismatch in stage '{}'",
                stage.name
            )));
        }
        let core_inner_radius = stage
            .bodytubes
            .iter()
            .map(|bt| bt.radius - bt.thickness)
            .fold(f64::INFINITY, f64::min);
        for ((mount, _), curve) in expanded_mounts.iter().zip(cluster) {
            // `host_inner_radius_m > 0.0` means this mount lives inside its
            // OWN local tube (a POD/STRAP_ON assembly, per ast.rs's
            // radial-assembly builder) -- `radial_offset_m` there is the
            // pod's offset from the CORE vehicle, irrelevant to whether the
            // motor fits inside the pod's own small tube, so only the
            // motor's own radius matters. Falling back to `core_inner_radius`
            // means this mount shares the STAGE's own body tube directly
            // (e.g. an octaweb 3-ring cluster) -- there, `radial_offset_m` is
            // measured from that same tube's centerline, so the motor's
            // outermost point is `radial_offset_m + motor_radius`. Checking
            // only `motor_radius` in that case is satisfied by any small
            // motor regardless of how far off-center it sits -- confirmed
            // via a live campaign candidate (main=H238T, retro=F50T) whose
            // tangent-fit cage placed the 3 main motors' outer edge past the
            // shared body tube's own inner wall while the old check still
            // passed.
            let is_own_local_tube = mount.host_inner_radius_m > 0.0;
            let host_inner_radius = if is_own_local_tube {
                mount.host_inner_radius_m
            } else {
                core_inner_radius
            };
            let motor_radius = curve.diameter_m * 0.5;
            let outermost_radius = if is_own_local_tube {
                motor_radius
            } else {
                mount.radial_offset_m + motor_radius
            };
            if outermost_radius + MOTOR_RADIAL_CLEARANCE_M > host_inner_radius {
                return Err(L2EngineError::ParseError(format!(
                    "motor_oversized:{}: stage '{}' motor diameter {:.1}mm at radial offset {:.1}mm (+{:.1}mm clearance) exceeds host inner diameter {:.1}mm",
                    mount.role,
                    stage.name,
                    motor_radius * 2.0 * 1000.0,
                    if is_own_local_tube { 0.0 } else { mount.radial_offset_m * 1000.0 },
                    MOTOR_RADIAL_CLEARANCE_M * 1000.0,
                    host_inner_radius * 2.0 * 1000.0
                )));
            }
        }
    }

    let mut mission_builder = MissionBuilder::new(
        geometry
            .stages
            .first()
            .map(|s| s.name.as_str())
            .unwrap_or("L2 Mission"),
    );

    let mut scheduled_stage_activation_s = 0.0_f64;
    for (i, stage) in geometry.stages.iter().enumerate() {
        let thrust_curve = &thrust_curves[i];

        if thrust_curve.propellant_mass_kg <= 0.0 {
            return Err(L2EngineError::ParseError(format!(
                "build_mission: thrust_curve.propellant_mass_kg must be > 0.0 (isp derivation \
                 divides by it), got {}",
                thrust_curve.propellant_mass_kg
            )));
        }

        let isp = thrust_curve.total_impulse() / (thrust_curve.propellant_mass_kg * G0);
        if !isp.is_finite() || isp <= 0.0 {
            return Err(L2EngineError::ParseError(format!(
                "build_mission: derived isp is not a finite positive value (isp={isp}) -- \
                 thrust_curve.total_impulse()={}, propellant_mass_kg={}",
                thrust_curve.total_impulse(),
                thrust_curve.propellant_mass_kg
            )));
        }

        let max_thrust_n = thrust_curve
            .thrust_n
            .iter()
            .cloned()
            .fold(0.0_f64, f64::max);

        let thrust_curve_pairs: Vec<(f64, f64)> = thrust_curve
            .time_s
            .iter()
            .cloned()
            .zip(thrust_curve.thrust_n.iter().cloned())
            .collect();

        let active_stages: Vec<&crate::geometry::StageGeometry> =
            geometry.stages[i..].iter().collect();

        // OpenRocket positions the motor from the aft end of the mount/tube,
        // with positive overhang extending past the tube bottom. Place motor
        // mass at the same axial center instead of the body midpoint.
        let motor_axial_offset_m = stage
            .bodytubes
            .first()
            .map(|bt| {
                bt.axial_offset_m + bt.length - thrust_curve.length_m * 0.5
                    + stage.motor_mount.motor_overhang_m
            })
            .unwrap_or(0.0);

        let mut hyperreal_mass_stage;
        let mass_stage = if physics_mode == crate::PhysicsMode::OpenRocketLegacy {
            stage
        } else {
            hyperreal_mass_stage = stage.clone();
            hyperreal_mass_stage.motor_mount.mount_length_m = 0.0;
            hyperreal_mass_stage.motor_mount.mount_outer_radius_m = 0.0;
            hyperreal_mass_stage.motor_mount.mount_thickness_m = 0.0;
            hyperreal_mass_stage.motor_mount.mount_material_density = 0.0;
            hyperreal_mass_stage.motor_mount.mount_axial_offset_m = 0.0;
            &hyperreal_mass_stage
        };

        let current_motor_absolute_m = stage.axial_offset_m + motor_axial_offset_m;
        let expanded_mounts = std::iter::once(&stage.motor_mount)
            .chain(stage.auxiliary_motor_mounts.iter())
            .flat_map(|mount| (0..mount.multiplicity).map(move |instance| (mount, instance)))
            .collect::<Vec<_>>();
        let positioned_dry_motors = expanded_mounts
            .iter()
            .zip(motor_clusters[i].iter())
            .map(|((mount, instance), curve)| {
                let angle = mount.radial_angle_rad + mount.instance_angle_step_rad * *instance as f64;
                let host_aft = if mount.host_aft_m > 0.0 {
                    mount.host_aft_m
                } else {
                    stage
                        .bodytubes
                        .first()
                        .map(|tube| tube.axial_offset_m + tube.length)
                        .unwrap_or(0.0)
                };
                mass_calculator::PositionedMotorMass {
                    mass_kg: curve.total_mass_kg - curve.propellant_mass_kg,
                    length_m: curve.length_m,
                    radius_m: curve.diameter_m * 0.5,
                    position_m: nalgebra::Vector3::new(
                        host_aft - curve.length_m * 0.5 + mount.motor_overhang_m,
                        mount.radial_offset_m * angle.cos(),
                        mount.radial_offset_m * angle.sin(),
                    ),
                }
            })
            .collect::<Vec<_>>();
        let dry_mass_properties =
            mass_calculator::mass_properties_3d(mass_stage, &positioned_dry_motors);
        let mass = dry_mass_properties.mass_kg;
        let static_cg = dry_mass_properties.cg_m.x;

        // Rotational/aerodynamic mass properties belong to the complete
        // active stack, not just the stage whose motor is burning. Upper
        // stages remain fully loaded until separation.
        let mut rotational_fixed_mass_kg = mass;
        let mut rotational_fixed_moment = mass * (stage.axial_offset_m + static_cg);
        for upper_index in (i + 1)..geometry.stages.len() {
            let upper_stage = &geometry.stages[upper_index];
            let upper_curve = &thrust_curves[upper_index];
            let upper_dry_motor = upper_curve.total_mass_kg - upper_curve.propellant_mass_kg;
            let upper_motor_local = upper_stage
                .bodytubes
                .first()
                .map(|tube| {
                    tube.axial_offset_m + tube.length - upper_curve.length_m * 0.5
                        + upper_stage.motor_mount.motor_overhang_m
                })
                .unwrap_or(0.0);
            let upper_dry_mass = mass_calculator::total_mass(upper_stage, upper_dry_motor);
            let upper_dry_cg = mass_calculator::static_cg_from_nose(
                upper_stage,
                upper_dry_motor,
                upper_motor_local,
            );
            let upper_motor_absolute = upper_stage.axial_offset_m + upper_motor_local;
            rotational_fixed_mass_kg += upper_dry_mass + upper_curve.propellant_mass_kg;
            rotational_fixed_moment += upper_dry_mass * (upper_stage.axial_offset_m + upper_dry_cg)
                + upper_curve.propellant_mass_kg * upper_motor_absolute;
        }
        let rotational_fixed_cg = rotational_fixed_moment / rotational_fixed_mass_kg;

        let roughness_m = stage
            .bodytubes
            .first()
            .map(|bt| bt.finish.roughness_m())
            .or_else(|| stage.nosecone.as_ref().map(|nc| nc.finish.roughness_m()))
            .unwrap_or(6e-6);

        // compute_aero works in the absolute stack frame (it adds each
        // stage's own axial_offset_m); static_cg is stage-local, so shift it
        // by this stage's offset. Legacy parsed geometries carry offset 0.0,
        // making this a no-op for them.
        let aero = barrowman::compute_aero(
            &active_stages,
            rotational_fixed_cg,
            roughness_m,
            physics_mode,
        )?;
        const STABILITY_MACHS: [f64; 20] = [
            0.0, 0.3, 0.5, 0.9, 1.0, 1.1, 1.2, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.5, 3.0,
            4.0, 5.0, 6.0,
        ];
        const STABILITY_AOAS_RAD: [f64; 5] = [
            0.0,
            5.0_f64.to_radians(),
            10.0_f64.to_radians(),
            15.0_f64.to_radians(),
            20.0_f64.to_radians(),
        ];
        // Competition fast-screen grid: the hard Mach gate is 0.95, so rows
        // beyond 1.1 cannot affect a surviving candidate.
        const FAST_STABILITY_MACHS: [f64; 6] = [0.0, 0.3, 0.5, 0.9, 1.0, 1.1];
        let stability_machs: &[f64] = if fast_aero_grid {
            &FAST_STABILITY_MACHS
        } else {
            &STABILITY_MACHS
        };
        let stability_aoas: &[f64] = if fast_aero_grid {
            &STABILITY_AOAS_RAD
        } else {
            &STABILITY_AOAS_RAD
        };
        let aero_stability_table = stability_aoas
            .iter()
            .flat_map(|&aoa| stability_machs.iter().map(move |&mach| (mach, aoa)))
            .map(|(mach, aoa)| {
                barrowman::compute_aero_at_mach_and_aoa(
                    &active_stages,
                    rotational_fixed_cg,
                    roughness_m,
                    physics_mode,
                    mach,
                    aoa,
                )
                .map(|coefficients| {
                    (
                        mach,
                        aoa,
                        coefficients.cp_offset_from_cg,
                        coefficients.cn_alpha,
                        coefficients.damping_moment_sum_m2,
                    )
                })
            })
            .collect::<Result<Vec<_>, _>>()?;

        // `mass` (from `mass_calculator::total_mass(stage, dry_motor_mass_kg)`)
        // already sums the dry (propellant-empty) motor mass plus all
        // structural components -- it IS the stage's total dry mass.
        // Subtracting `propellant_mass_kg` again here would double-count the
        // propellant removal (Rule 1 bugfix: the plan's literal
        // `.dry_mass(mass - propellant_mass_kg)` phrasing produced negative
        // dry masses in practice -- verified via a debug dump showing
        // dry_mass=-3.2kg/-3.8kg and the simulation diverging to NaN
        // mid-flight once propellant depleted below zero dry structural
        // mass).
        let dry_mass = mass + 0.4;
        let current_propellant_mass = motor_clusters[i]
            .iter()
            .map(|curve| curve.propellant_mass_kg)
            .sum::<f64>();
        let current_propellant_moment = positioned_dry_motors
            .iter()
            .zip(motor_clusters[i].iter())
            .map(|(motor, curve)| {
                curve.propellant_mass_kg * (stage.axial_offset_m + motor.position_m.x)
            })
            .sum::<f64>();
        let current_propellant_cg = if current_propellant_mass > 0.0 {
            current_propellant_moment / current_propellant_mass
        } else {
            current_motor_absolute_m
        };
        let stack_launch_mass = rotational_fixed_mass_kg + current_propellant_mass;
        let stack_launch_cg =
            (rotational_fixed_moment + current_propellant_moment) / stack_launch_mass;
        let mut stack_transverse_x_inertia = 0.0;
        let mut stack_transverse_y_inertia = 0.0;
        let mut stack_axial_inertia = 0.0;
        for active_index in i..geometry.stages.len() {
            let active_stage = &geometry.stages[active_index];
            if active_index == i {
                let positioned_loaded_motors = positioned_dry_motors
                    .iter()
                    .zip(motor_clusters[i].iter())
                    .map(|(motor, curve)| mass_calculator::PositionedMotorMass {
                        mass_kg: curve.total_mass_kg,
                        ..*motor
                    })
                    .collect::<Vec<_>>();
                let loaded =
                    mass_calculator::mass_properties_3d(mass_stage, &positioned_loaded_motors);
                let absolute_cg = stage.axial_offset_m + loaded.cg_m.x;
                let shift = loaded.mass_kg * (absolute_cg - stack_launch_cg).powi(2);
                stack_transverse_x_inertia += loaded.inertia.x + shift;
                stack_transverse_y_inertia += loaded.inertia.y + shift;
                stack_axial_inertia += loaded.inertia.z;
                continue;
            }
            let active_curve = &thrust_curves[active_index];
            let active_dry_motor = active_curve.total_mass_kg - active_curve.propellant_mass_kg;
            let active_motor_local = active_stage
                .bodytubes
                .first()
                .map(|tube| {
                    tube.axial_offset_m + tube.length - active_curve.length_m * 0.5
                        + active_stage.motor_mount.motor_overhang_m
                })
                .unwrap_or(0.0);
            let active_dry_cg = mass_calculator::static_cg_from_nose(
                active_stage,
                active_dry_motor,
                active_motor_local,
            );
            let active_loaded_cg = mass_calculator::dynamic_cg_at(
                active_stage,
                0.0,
                active_curve,
                active_dry_cg,
                active_motor_local,
            );
            let active_loaded_mass = mass_calculator::total_mass(active_stage, active_dry_motor)
                + active_curve.propellant_mass_kg;
            let active_inertia = mass_calculator::principal_inertia(
                active_stage,
                active_curve.total_mass_kg,
                active_curve.length_m,
                active_curve.diameter_m * 0.5,
                active_motor_local,
                active_loaded_cg,
            );
            let active_absolute_cg = active_stage.axial_offset_m + active_loaded_cg;
            let shift = active_loaded_mass * (active_absolute_cg - stack_launch_cg).powi(2);
            stack_transverse_x_inertia += active_inertia.x + shift;
            stack_transverse_y_inertia += active_inertia.y + shift;
            stack_axial_inertia += active_inertia.z;
        }
        let inertia = nalgebra::Vector3::new(
            stack_transverse_x_inertia,
            stack_transverse_y_inertia,
            stack_axial_inertia,
        );

        let mounts = std::iter::once(&stage.motor_mount)
            .chain(stage.auxiliary_motor_mounts.iter())
            .flat_map(|mount| (0..mount.multiplicity).map(move |instance| (mount, instance)));
        let primary_burn_duration = expanded_mounts
            .iter()
            .zip(motor_clusters[i].iter())
            .filter(|((mount, _), _)| {
                !matches!(
                    mount.role.trim().to_ascii_lowercase().as_str(),
                    "retro" | "landing" | "recovery" | "braking"
                )
            })
            .filter_map(|(_, curve)| curve.time_s.last().copied())
            .fold(0.0_f64, f64::max);
        let motors = mounts
            .zip(motor_clusters[i].iter())
            .map(|((mount, instance), curve)| {
                let isp = curve.total_impulse() / (curve.propellant_mass_kg * G0);
                let ignition_delay = match mount.ignition_event.to_ascii_lowercase().as_str() {
                    "automatic" | "stage_activation" | "stage-activation" => {
                        mount.ignition_delay
                    }
                    // Convert a mission-clock launch delay to this stage's
                    // activation clock. Stage activation is deterministic:
                    // preceding ascent burns plus their separation coasts.
                    "launch" => mount.ignition_delay - scheduled_stage_activation_s,
                    "burnout" | "primary_burnout" | "primary-burnout" => {
                        primary_burn_duration + mount.ignition_delay
                    }
                    other => {
                        return Err(L2EngineError::ParseError(format!(
                            "unsupported motor ignition event '{other}' in stage '{}'",
                            stage.name
                        )));
                    }
                };
                Ok(MotorBurn {
                    role: mount.role.clone(),
                    propellant_mass: curve.propellant_mass_kg,
                    thrust: curve.thrust_n.iter().copied().fold(0.0, f64::max),
                    isp,
                    thrust_curve: curve
                        .time_s
                        .iter()
                        .copied()
                        .zip(curve.thrust_n.iter().copied())
                        .collect(),
                    ignition_delay,
                    position_from_nose_m: {
                        let angle = mount.radial_angle_rad
                            + mount.instance_angle_step_rad * instance as f64;
                        let host_aft = if mount.host_aft_m > 0.0 {
                            mount.host_aft_m
                        } else {
                            stage
                                .bodytubes
                                .first()
                                .map(|tube| tube.axial_offset_m + tube.length)
                                .unwrap_or(0.0)
                        };
                        nalgebra::Vector3::new(
                            host_aft - curve.length_m * 0.5 + mount.motor_overhang_m,
                            mount.radial_offset_m * angle.cos(),
                            mount.radial_offset_m * angle.sin(),
                        )
                    },
                    nozzle_position_from_nose_m: {
                        let angle = mount.radial_angle_rad
                            + mount.instance_angle_step_rad * instance as f64;
                        let host_aft = if mount.host_aft_m > 0.0 {
                            mount.host_aft_m
                        } else {
                            stage
                                .bodytubes
                                .first()
                                .map(|tube| tube.axial_offset_m + tube.length)
                                .unwrap_or(0.0)
                        };
                        nalgebra::Vector3::new(
                            host_aft + mount.motor_overhang_m,
                            mount.radial_offset_m * angle.cos(),
                            mount.radial_offset_m * angle.sin(),
                        )
                    },
                })
            })
            .collect::<Result<Vec<_>, _>>()?;

        let mut builder = StageBuilder::new(&stage.name)
            .dry_mass(dry_mass)
            .motors(motors)
            .propellant_mass(thrust_curve.propellant_mass_kg)
            .thrust(max_thrust_n)
            .thrust_curve(thrust_curve_pairs.clone())
            .isp(isp)
            .cd(aero.cd_table.first().map(|(_, c)| *c).unwrap_or(0.3))
            .cd_table(aero.cd_table.clone())
            .cd_nonfric_table(aero.cd_nonfric_table.clone())
            .friction_params(aero.friction_params.clone())
            .cp_offset(aero.cp_offset_from_cg)
            .mass_locations(
                rotational_fixed_cg,
                current_propellant_cg,
                rotational_fixed_mass_kg,
                rotational_fixed_cg,
            )
            .fixed_cg_radial(nalgebra::Vector3::new(
                0.0,
                dry_mass_properties.cg_m.y,
                dry_mass_properties.cg_m.z,
            ))
            .cn_alpha(aero.cn_alpha)
            .aero_stability_table(aero_stability_table)
            .pitch_damping_multiplier(aero.pitch_damping_multiplier)
            .area(aero.reference_area)
            .inertia(inertia)
            .ignition_delay(stage.motor_mount.ignition_delay)
            .separation_coast(stage.motor_mount.ejection_charge_delay);

        if physics_mode == crate::PhysicsMode::HyperReal {
            if let Some(p) = &stage.parachute {
                builder = builder
                    .parachute_delay(p.deploy_delay)
                    .parachute_cd_area(p.cd * std::f64::consts::PI * (p.diameter / 2.0).powi(2));
            }
        }

        let stage_built = builder.build();

        mission_builder = mission_builder.stage(stage_built);
        scheduled_stage_activation_s += primary_burn_duration + stage.motor_mount.ejection_charge_delay;
    }

    Ok(mission_builder.build())
}

fn parse_ork_simulation_environment(xml: &str) -> Option<OrkSimulationEnvironment> {
    let doc = Document::parse(xml).ok()?;
    let simulation = doc.descendants().find(|node| {
        node.has_tag_name("simulation")
            && node
                .descendants()
                .any(|child| child.has_tag_name("launchrodlength"))
    })?;
    let conditions = simulation
        .descendants()
        .find(|node| node.has_tag_name("conditions"))
        .unwrap_or(simulation);
    let launch_into_wind = child_bool(&conditions, "launchintowind").unwrap_or(true);
    let wind_direction_rad = child_angle_rad(&conditions, "winddirection").unwrap_or(0.0);
    let launch_rod_direction_rad = if launch_into_wind {
        wind_direction_rad.rem_euclid(std::f64::consts::TAU)
    } else {
        child_angle_rad(&conditions, "launchroddirection").unwrap_or(0.0)
    };
    let relative_humidity = conditions
        .children()
        .find(|node| node.has_tag_name("atmosphere"))
        .and_then(|atmosphere| child_f64_optional(&atmosphere, "baserelativehumidity"))
        .unwrap_or(0.0)
        .clamp(0.0, 1.0);

    Some(OrkSimulationEnvironment {
        launch_rod_length_m: child_f64_optional(&conditions, "launchrodlength").unwrap_or(0.0),
        launch_rod_angle_rad: child_angle_rad(&conditions, "launchrodangle").unwrap_or(0.0),
        launch_rod_direction_rad,
        wind_speed_mps: child_f64_optional(&conditions, "windaverage").unwrap_or(0.0),
        wind_direction_rad,
        relative_humidity,
        wind_levels: Vec::new(),
        base_temperature_k: conditions
            .descendants()
            .find(|node| node.has_tag_name("atmosphere"))
            .and_then(|node| child_f64_optional(&node, "basetemperature"))
            .unwrap_or_else(standard_temperature_k),
        base_pressure_pa: conditions
            .descendants()
            .find(|node| node.has_tag_name("atmosphere"))
            .and_then(|node| child_f64_optional(&node, "basepressure"))
            .unwrap_or_else(standard_pressure_pa),
        launch_altitude_m: child_f64_optional(&conditions, "launchaltitude").unwrap_or(0.0),
    })
}

pub fn apply_openrocket_environment(mission: &mut Mission, env: OrkSimulationEnvironment) {
    let wind_direction = horizontal_direction(env.wind_direction_rad);
    mission.wind_velocity_mps = wind_direction * env.wind_speed_mps.max(0.0);
    mission.relative_humidity = env.relative_humidity.clamp(0.0, 1.0);
    mission.base_temperature_k = env.base_temperature_k;
    mission.base_pressure_pa = env.base_pressure_pa;
    mission.launch_altitude_m = env.launch_altitude_m;
    if !env.wind_levels.is_empty() {
        mission.wind_profile = Some(crate::sim_core::wind::WindProfile::new(env.wind_levels));
    }

    if env.launch_rod_length_m > 0.0 {
        let angle = env.launch_rod_angle_rad;
        let direction = Vector3::new(
            angle.sin() * (std::f64::consts::FRAC_PI_2 - env.launch_rod_direction_rad).cos(),
            angle.sin() * (std::f64::consts::FRAC_PI_2 - env.launch_rod_direction_rad).sin(),
            angle.cos(),
        );
        mission.launch_guide = Some(crate::sim_core::vehicle::LaunchGuide {
            length_m: env.launch_rod_length_m,
            direction: direction.normalize(),
        });
    }
}

fn horizontal_direction(direction_rad: f64) -> Vector3<f64> {
    Vector3::new(
        (std::f64::consts::FRAC_PI_2 - direction_rad).cos(),
        (std::f64::consts::FRAC_PI_2 - direction_rad).sin(),
        0.0,
    )
}

fn child_bool(node: &Node, tag: &str) -> Option<bool> {
    child_text_optional(node, tag).and_then(|text| text.trim().parse::<bool>().ok())
}

fn child_angle_rad(node: &Node, tag: &str) -> Option<f64> {
    let raw = child_f64_optional(node, tag)?;
    if raw.abs() > std::f64::consts::TAU {
        Some(raw.to_radians())
    } else {
        Some(raw)
    }
}

fn child_f64_optional(node: &Node, tag: &str) -> Option<f64> {
    child_text_optional(node, tag).and_then(|text| text.trim().parse::<f64>().ok())
}

fn child_text_optional<'a, 'input>(node: &Node<'a, 'input>, tag: &str) -> Option<&'a str> {
    node.children()
        .find(|child| child.is_element() && child.has_tag_name(tag))
        .and_then(|child| child.text())
}

/// Orchestrates the full pipeline: `.ork` zip -> XML -> parsed geometry,
/// `.eng` text -> parsed thrust curve, assembled `Mission`, RK4 simulation
/// under the no-op controller, and a summarized `FlightSummary`.
pub fn simulate_rocket(
    ork_path: &std::path::Path,
    eng_text: &str,
    motor_designation: &str,
    physics_mode: crate::PhysicsMode,
) -> Result<FlightSummary, L2EngineError> {
    let xml = xml_parser::extract_ork_xml(ork_path)?;
    let geometry = xml_parser::parse_rocket_geometry(&xml)?;
    let thrust_curve = motor_db::parse_eng(eng_text, motor_designation)?;

    // Duplicate for all stages for backwards compat
    let curves = vec![thrust_curve; geometry.stages.len()];
    let mut mission = build_mission(&geometry, &curves, physics_mode)?;
    if physics_mode == crate::PhysicsMode::OpenRocketLegacy {
        if let Some(env) = parse_ork_simulation_environment(&xml) {
            apply_openrocket_environment(&mut mission, env);
        }
    }

    let config = SimConfig {
        dt: 0.005,
        max_time: 600.0,
    };
    let mut controller = NoOpController;
    crate::sim_core::sim::simulate_summary_with_mode(
        &mission,
        &config,
        &mut controller,
        physics_mode,
        false,
    )
    .map_err(L2EngineError::ParseError)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::{
        BodyTubeGeometry, FinsetGeometry, MotorMountGeometry, NoseShape, NoseconeGeometry,
        ParachuteGeometry, RocketGeometry, StageGeometry, SurfaceFinish,
    };
    use std::path::PathBuf;

    fn ork_fixture_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/L2_Hyper_Parallel_15K.ork")
    }

    const N4800T_ENG: &str = include_str!("../tests/fixtures/N4800T.eng");

    /// Full end-to-end pipeline: parse the reference 2-stage vehicle's real
    /// `.ork` + the real N4800T `.eng` fixture, assemble a Mission, simulate
    /// with the no-op controller, and sanity-check the resulting apogee.
    /// Loose bound only (>10km) -- the strict <2% accuracy check against
    /// OpenRocket's own reported values is Plan 08's job, not this plan's.
    #[test]
    fn builds_two_stage_mission_from_reference_ork() {
        let summary = simulate_rocket(
            &ork_fixture_path(),
            N4800T_ENG,
            "N4800T",
            crate::PhysicsMode::HyperReal,
        )
        .expect("simulate_rocket should succeed for the reference vehicle");

        assert!(
            summary.apogee_m > 10_000.0,
            "expected apogee_m > 10_000.0 for the reference 2-stage vehicle, got {}",
            summary.apogee_m
        );
    }

    #[test]
    fn openrocket_mode_does_not_deploy_proxy_parachute_before_apogee() {
        let (_, curve) = motor_db::parse_eng_file(N4800T_ENG).expect("parse fixture motor");
        let geometry = RocketGeometry {
            stages: vec![StageGeometry {
                name: "Recovery Timing Probe".to_string(),
                nosecone: Some(NoseconeGeometry {
                    shape: NoseShape::Conical,
                    shape_parameter: 0.0,
                    length: 0.3,
                    aft_radius: 0.06,
                    thickness: 0.002,
                    material_density: 680.0,
                    finish: SurfaceFinish::Polished,
                    axial_offset_m: 0.0,
                    ballast_mass: 0.0,
                }),
                bodytubes: vec![BodyTubeGeometry {
                    length: 1.3,
                    radius: 0.06,
                    thickness: 0.002,
                    material_density: 680.0,
                    finish: SurfaceFinish::Polished,
                    axial_offset_m: 0.3,
                }],
                finsets: vec![FinsetGeometry {
                    fin_count: 4,
                    points: vec![(0.0, 0.0), (0.04, 0.08), (0.08, 0.08), (0.16, 0.0)],
                    thickness: 0.003,
                    cross_section: "airfoil".to_string(),
                    material_density: 680.0,
                    finish: SurfaceFinish::Polished,
                    cant_rad: 0.0,
                    axial_offset_m: 1.4,
                }],
                point_masses: Vec::new(),
                radial_assemblies: Vec::new(),
                motor_mount: MotorMountGeometry {
                    role: "main".to_string(),
                    multiplicity: 1,
                    ignition_event: "automatic".to_string(),
                    ignition_delay: 0.0,
                    motor_designation: "N4800T".to_string(),
                    motor_overhang_m: 0.005,
                    mount_length_m: 1.259,
                    mount_outer_radius_m: 0.05,
                    mount_thickness_m: 0.001,
                    mount_material_density: 950.0,
                    mount_axial_offset_m: 0.341,
                    ejection_charge_delay: 0.0,
                    radial_offset_m: 0.0,
                    radial_angle_rad: 0.0,
                    instance_angle_step_rad: 0.0,
                    host_inner_radius_m: 0.0,
                    host_aft_m: 0.0,
                },
                auxiliary_motor_mounts: vec![],
                separation: None,
                parachute: Some(ParachuteGeometry {
                    diameter: 1.0,
                    cd: 1.5,
                    deploy_delay: 0.0,
                    packed_mass_kg: 0.0,
                    axial_offset_m: 0.95,
                }),
                axial_offset_m: 0.0,
            }],
        };
        let curves = vec![curve];

        let hyperreal = build_mission(&geometry, &curves, crate::PhysicsMode::HyperReal)
            .expect("hyperreal mission");
        let openrocket = build_mission(&geometry, &curves, crate::PhysicsMode::OpenRocketLegacy)
            .expect("openrocket mission");

        assert!(hyperreal.stages[0].parachute_delay.is_some());
        assert!(hyperreal.stages[0].parachute_cd_area.is_some());
        assert!(openrocket.stages[0].parachute_delay.is_none());
        assert!(openrocket.stages[0].parachute_cd_area.is_none());
        assert_eq!(hyperreal.wind_velocity_mps, nalgebra::Vector3::zeros());
        assert_eq!(openrocket.wind_velocity_mps, nalgebra::Vector3::zeros());
        assert!(hyperreal.launch_guide.is_none());
        assert!(openrocket.launch_guide.is_none());
    }

    #[test]
    fn parses_openrocket_launch_environment_from_ork_xml() {
        let xml = r#"
            <openrocket>
              <simulation>
                <conditions>
                  <launchrodlength>2.0</launchrodlength>
                  <launchintowind>true</launchintowind>
                  <launchrodangle>0.0</launchrodangle>
                  <launchroddirection>90.0</launchroddirection>
                  <windaverage>2.0</windaverage>
                  <winddirection>1.5707963267948966</winddirection>
                  <atmosphere model="isa">
                    <baserelativehumidity>0.65</baserelativehumidity>
                  </atmosphere>
                </conditions>
              </simulation>
            </openrocket>
        "#;
        let env = parse_ork_simulation_environment(&xml).expect("parse launch env");

        assert!((env.launch_rod_length_m - 2.0).abs() < 1e-9);
        assert_eq!(env.launch_rod_angle_rad, 0.0);
        assert!((env.wind_speed_mps - 2.0).abs() < 1e-9);
        assert!((env.wind_direction_rad - std::f64::consts::FRAC_PI_2).abs() < 1e-9);
        assert!((env.relative_humidity - 0.65).abs() < 1e-9);

        let mut mission = crate::sim_core::vehicle::MissionBuilder::new("environment").build();
        apply_openrocket_environment(&mut mission, env);
        assert!((mission.relative_humidity - 0.65).abs() < 1e-9);
    }
}
