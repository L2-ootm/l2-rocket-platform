//! Shared CG/static-margin math for any `RocketGeometry`, regardless of how
//! it was produced (real `.ork` parse, or the organic AST-evolution engine's
//! generated topology). Used by both `mission_adapter.rs` and `ast.rs`.
//!
//! The fixed-3-stage "hyper-evolution" parametric GA that used to live here
//! (`DesignGenome` -> hardcoded Kick/Sustainer/Booster template with
//! hand-picked radii) has been retired: the organic AST engine is now the
//! only design-generation path, on real motors/materials pulled from
//! OpenRocket's own database, with arbitrary topology instead of a fixed
//! template. See STATE.md for the retirement note.

use crate::geometry::StageGeometry;
use crate::motor_db::ThrustCurve;
use crate::{PhysicsMode, barrowman, mass_calculator};
use nalgebra::Vector3;
use std::f64::consts::PI;

#[derive(Debug, Clone, PartialEq)]
pub struct ExposedStagePhaseMargin {
    pub phase: &'static str,
    pub time_s: f64,
    pub remaining_motor_mass_kg: f64,
    pub cg_from_nose_m: f64,
    pub cp_from_nose_m: f64,
    pub static_margin_cal: f64,
}

pub fn exposed_stage_passes(
    phases: &[ExposedStagePhaseMargin],
    minimum_margin_cal: f64,
) -> bool {
    phases.len() == 5
        && phases.iter().all(|phase| {
            phase.static_margin_cal.is_finite()
                && phase.static_margin_cal >= minimum_margin_cal
        })
}

/// Phase-aware exposed-stage stability with impulse-weighted propellant
/// depletion. Positive margin means CP is aft of CG in the nose-tip frame.
pub fn exposed_stage_phase_margins(
    stage: &StageGeometry,
    curve: &ThrustCurve,
    physics_mode: PhysicsMode,
    machs: &[f64],
) -> Vec<ExposedStagePhaseMargin> {
    let main = stage.bodytubes.first();
    let motor_pos = main
        .map(|bt| {
            bt.axial_offset_m + bt.length - curve.length_m * 0.5
                + stage.motor_mount.motor_overhang_m
        })
        .unwrap_or(0.0);
    let dry_motor_mass = curve.total_mass_kg - curve.propellant_mass_kg;
    let dry_cg = mass_calculator::static_cg_from_nose(
        stage,
        dry_motor_mass,
        motor_pos,
    );
    let duration = curve.time_s.last().copied().unwrap_or(0.0);
    let phases = [
        ("separation_ignition", 0.0),
        ("representative_powered_ascent", 0.25 * duration),
        ("maximum_dynamic_pressure", 0.50 * duration),
        ("main_motor_burnout", duration),
        ("post_burn_coast", duration + 1.0e-6),
    ];
    phases
        .iter()
        .enumerate()
        .map(|(index, &(phase, time_s))| {
            let cg = mass_calculator::dynamic_cg_at(
                stage, time_s, curve, dry_cg, motor_pos,
            );
            let mach = machs
                .get(index)
                .or_else(|| machs.last())
                .copied()
                .unwrap_or(0.3);
            let active = [stage];
            let aero = barrowman::compute_aero_at_mach(
                &active, cg, 1e-6, physics_mode, mach,
            );
            match aero {
                Ok(aero) => {
                    let radius = (aero.reference_area / PI).sqrt();
                    ExposedStagePhaseMargin {
                        phase,
                        time_s,
                        remaining_motor_mass_kg: curve.mass_at(time_s),
                        cg_from_nose_m: cg,
                        cp_from_nose_m: cg + aero.cp_offset_from_cg,
                        static_margin_cal: aero.cp_offset_from_cg / (2.0 * radius),
                    }
                }
                Err(_) => ExposedStagePhaseMargin {
                    phase,
                    time_s,
                    remaining_motor_mass_kg: curve.mass_at(time_s),
                    cg_from_nose_m: cg,
                    cp_from_nose_m: f64::NAN,
                    static_margin_cal: f64::NEG_INFINITY,
                },
            }
        })
        .collect()
}

fn positioned_motor_masses_at(
    stage: &StageGeometry,
    curves: &[ThrustCurve],
    time_s: f64,
) -> Vec<mass_calculator::PositionedMotorMass> {
    std::iter::once(&stage.motor_mount)
        .chain(stage.auxiliary_motor_mounts.iter())
        .flat_map(|mount| (0..mount.multiplicity).map(move |instance| (mount, instance)))
        .zip(curves.iter())
        .map(|((mount, instance), curve)| {
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
            mass_calculator::PositionedMotorMass {
                mass_kg: curve.mass_at(time_s),
                length_m: curve.length_m,
                radius_m: curve.diameter_m * 0.5,
                position_m: Vector3::new(
                    host_aft - curve.length_m * 0.5 + mount.motor_overhang_m,
                    mount.radial_offset_m * angle.cos(),
                    mount.radial_offset_m * angle.sin(),
                ),
            }
        })
        .collect()
}

/// Phase-aware stability for a stage containing motors at distinct axial and
/// radial stations. Curves must use the same expanded mount order as the
/// mission adapter: primary mount instances followed by auxiliary instances.
pub fn exposed_stage_phase_margins_multi(
    stage: &StageGeometry,
    curves: &[ThrustCurve],
    physics_mode: PhysicsMode,
    machs: &[f64],
) -> Vec<ExposedStagePhaseMargin> {
    let duration = curves
        .iter()
        .filter_map(|curve| curve.time_s.last().copied())
        .fold(0.0_f64, f64::max);
    let phases = [
        ("separation_ignition", 0.0),
        ("representative_powered_ascent", 0.25 * duration),
        ("maximum_dynamic_pressure", 0.50 * duration),
        ("main_motor_burnout", duration),
        ("post_burn_coast", duration + 1.0e-6),
    ];
    phases
        .iter()
        .enumerate()
        .map(|(index, &(phase, time_s))| {
            let motors = positioned_motor_masses_at(stage, curves, time_s);
            let properties = mass_calculator::mass_properties_3d(stage, &motors);
            let cg = properties.cg_m.x;
            let mach = machs
                .get(index)
                .or_else(|| machs.last())
                .copied()
                .unwrap_or(0.3);
            let active = [stage];
            match barrowman::compute_aero_at_mach(&active, cg, 1e-6, physics_mode, mach) {
                Ok(aero) => {
                    let radius = (aero.reference_area / PI).sqrt();
                    ExposedStagePhaseMargin {
                        phase,
                        time_s,
                        remaining_motor_mass_kg: motors.iter().map(|motor| motor.mass_kg).sum(),
                        cg_from_nose_m: cg,
                        cp_from_nose_m: cg + aero.cp_offset_from_cg,
                        static_margin_cal: aero.cp_offset_from_cg / (2.0 * radius),
                    }
                }
                Err(_) => ExposedStagePhaseMargin {
                    phase,
                    time_s,
                    remaining_motor_mass_kg: motors.iter().map(|motor| motor.mass_kg).sum(),
                    cg_from_nose_m: cg,
                    cp_from_nose_m: f64::NAN,
                    static_margin_cal: f64::NEG_INFINITY,
                },
            }
        })
        .collect()
}

pub fn stack_wet_cg_multi(
    stages: &[StageGeometry],
    motor_clusters: &[Vec<ThrustCurve>],
    start: usize,
) -> f64 {
    let mut mass = 0.0;
    let mut moment = 0.0;
    for (index, stage) in stages.iter().enumerate().skip(start) {
        let motors = positioned_motor_masses_at(stage, &motor_clusters[index], 0.0);
        let properties = mass_calculator::mass_properties_3d(stage, &motors);
        mass += properties.mass_kg;
        moment += properties.mass_kg * (stage.axial_offset_m + properties.cg_m.x);
    }
    if mass > 0.0 { moment / mass } else { 0.0 }
}

pub fn static_margins_with_motor_clusters_at_machs(
    geometry: &crate::geometry::RocketGeometry,
    motor_clusters: &[Vec<ThrustCurve>],
    physics_mode: PhysicsMode,
    phase_machs: &[f64],
) -> Vec<f64> {
    let n = geometry.stages.len();
    (0..n)
        .map(|start| {
            let active: Vec<&StageGeometry> = geometry.stages[start..].iter().collect();
            if active.len() == 1 {
                return exposed_stage_phase_margins_multi(
                    active[0],
                    &motor_clusters[start],
                    physics_mode,
                    phase_machs,
                )
                .iter()
                .map(|phase| phase.static_margin_cal)
                .fold(f64::INFINITY, f64::min);
            }
            let cg_abs = stack_wet_cg_multi(&geometry.stages, motor_clusters, start);
            let mach = phase_machs
                .get(start)
                .or_else(|| phase_machs.last())
                .copied()
                .unwrap_or(0.3);
            match barrowman::compute_aero_at_mach(&active, cg_abs, 1e-6, physics_mode, mach) {
                Ok(aero) => {
                    let r_ref = (aero.reference_area / PI).sqrt();
                    aero.cp_offset_from_cg / (2.0 * r_ref)
                }
                Err(_) => f64::NEG_INFINITY,
            }
        })
        .collect()
}

/// Absolute wet (launch) CG of the active stack `stages[start..]`, from the
/// stack nose tip. Component CENTROIDS (not front edges), and the wet motor
/// at the aft end of the stage's main tube.
pub fn stack_wet_cg(
    stages: &[StageGeometry],
    curves: &[ThrustCurve],
    start: usize,
    physics_mode: PhysicsMode,
) -> f64 {
    let mut mass = 0.0;
    let mut moment = 0.0;
    for (j, stage) in stages.iter().enumerate().skip(start) {
        let base = stage.axial_offset_m;
        if physics_mode == PhysicsMode::OpenRocketLegacy {
            let mount_mass = mass_calculator::motor_mount_tube_mass(stage);
            mass += mount_mass;
            moment += mount_mass
                * (base
                    + stage.motor_mount.mount_axial_offset_m
                    + stage.motor_mount.mount_length_m * 0.5);
        }
        for bt in &stage.bodytubes {
            let m = mass_calculator::bodytube_mass(bt);
            mass += m;
            moment += m * (base + bt.axial_offset_m + bt.length * 0.5);
        }
        if let Some(nc) = &stage.nosecone {
            let m = mass_calculator::nosecone_mass(nc);
            mass += m;
            // shell centroid ~0.55L; ballast actually sits at the tip, so
            // lumping at 0.55L is the conservative (aft) side.
            moment += m * (base + nc.axial_offset_m + 0.55 * nc.length);
        }
        for fsx in &stage.finsets {
            let m = mass_calculator::fin_mass(fsx);
            let root = fsx.points.last().map(|p| p.0).unwrap_or(0.0);
            mass += m;
            moment += m * (base + fsx.axial_offset_m + 0.5 * root);
        }
        for pm in &stage.point_masses {
            mass += pm.mass_kg;
            moment += pm.mass_kg * (base + pm.axial_offset_m);
        }
        if let Some(parachute) = &stage.parachute {
            mass += parachute.packed_mass_kg;
            moment += parachute.packed_mass_kg * (base + parachute.axial_offset_m);
        }
        let main = stage.bodytubes.first();
        let motor_len = curves[j].length_m;
        let motor_pos = main
            .map(|bt| {
                bt.axial_offset_m + bt.length - motor_len * 0.5 + stage.motor_mount.motor_overhang_m
            })
            .unwrap_or(0.0);
        let wet = curves[j].total_mass_kg;
        mass += wet;
        moment += wet * (base + motor_pos);
    }
    moment / mass
}

/// Static margin [cal] for each flight phase (index 0 = liftoff full stack,
/// last = top stage alone). Positive = stable, same sign convention as
/// `AerodynamicCoefficients.cp_offset_from_cg`.
pub fn static_margins_with_mode(
    geometry: &crate::geometry::RocketGeometry,
    curves: &[ThrustCurve],
    physics_mode: PhysicsMode,
) -> Vec<f64> {
    static_margins_with_mode_at_machs(geometry, curves, physics_mode, &[0.3])
}

pub fn static_margins_with_mode_at_machs(
    geometry: &crate::geometry::RocketGeometry,
    curves: &[ThrustCurve],
    physics_mode: PhysicsMode,
    phase_machs: &[f64],
) -> Vec<f64> {
    let n = geometry.stages.len();
    (0..n)
        .map(|start| {
            let active: Vec<&StageGeometry> = geometry.stages[start..].iter().collect();
            if active.len() == 1 {
                return exposed_stage_phase_margins(
                    active[0],
                    &curves[start],
                    physics_mode,
                    phase_machs,
                )
                .iter()
                .map(|phase| phase.static_margin_cal)
                .fold(f64::INFINITY, f64::min);
            }
            let cg_abs = stack_wet_cg(&geometry.stages, curves, start, physics_mode);
            let mach = phase_machs
                .get(start)
                .or_else(|| phase_machs.last())
                .copied()
                .unwrap_or(0.3);
            match barrowman::compute_aero_at_mach(&active, cg_abs, 1e-6, physics_mode, mach) {
                Ok(aero) => {
                    let r_ref = (aero.reference_area / PI).sqrt();
                    aero.cp_offset_from_cg / (2.0 * r_ref)
                }
                Err(_) => f64::NEG_INFINITY,
            }
        })
        .collect()
}

pub fn static_margins(
    geometry: &crate::geometry::RocketGeometry,
    curves: &[ThrustCurve],
) -> Vec<f64> {
    static_margins_with_mode(geometry, curves, PhysicsMode::HyperReal)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::motor_db::parse_eng;
    use crate::xml_parser::{extract_ork_xml, parse_rocket_geometry};
    use std::path::PathBuf;

    fn reference_stage_and_curve() -> (StageGeometry, ThrustCurve) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let ork = root.join("tests/fixtures/L2_Hyper_Parallel_15K.ork");
        let xml = extract_ork_xml(&ork).expect("extract fixture");
        let geometry = parse_rocket_geometry(&xml).expect("parse fixture");
        let eng = std::fs::read_to_string(root.join("tests/fixtures/N4800T.eng"))
            .expect("read motor fixture");
        let curve = parse_eng(&eng, "N4800T").expect("parse motor");
        (geometry.stages[1].clone(), curve)
    }

    #[test]
    fn exposed_phase_margin_uses_cp_aft_of_cg_as_positive_stability() {
        let (stage, curve) = reference_stage_and_curve();
        let phases = exposed_stage_phase_margins(
            &stage, &curve, PhysicsMode::HyperReal, &[0.3; 5],
        );
        assert_eq!(phases.len(), 5);
        for phase in phases {
            let diameter = 2.0 * stage.bodytubes[0].radius;
            assert!(
                (phase.static_margin_cal
                    - (phase.cp_from_nose_m - phase.cg_from_nose_m) / diameter)
                    .abs()
                    < 1e-9
            );
        }
    }

    #[test]
    fn exposed_phase_order_tracks_propellant_depletion_and_cg_motion() {
        let (stage, curve) = reference_stage_and_curve();
        let phases = exposed_stage_phase_margins(
            &stage, &curve, PhysicsMode::HyperReal, &[0.1, 0.2, 0.3, 0.2, 0.1],
        );
        assert_eq!(phases[0].phase, "separation_ignition");
        assert_eq!(phases[3].phase, "main_motor_burnout");
        assert_eq!(phases[4].phase, "post_burn_coast");
        assert!(
            phases.windows(2).all(|pair| {
                pair[1].remaining_motor_mass_kg
                    <= pair[0].remaining_motor_mass_kg + 1e-12
            })
        );
        assert!(
            (phases[0].cg_from_nose_m - phases[3].cg_from_nose_m).abs()
                > 1e-6
        );
    }

    #[test]
    fn multi_motor_phase_cg_uses_each_mount_axial_station() {
        let (mut stage, curve) = reference_stage_and_curve();
        stage.motor_mount.multiplicity = 1;
        stage.motor_mount.host_aft_m = stage.bodytubes[0].axial_offset_m
            + stage.bodytubes[0].length;
        let mut forward_mount = stage.motor_mount.clone();
        forward_mount.host_aft_m = stage.motor_mount.host_aft_m - 0.30;
        forward_mount.radial_offset_m = stage.bodytubes[0].radius * 2.0;
        forward_mount.radial_angle_rad = 0.0;
        forward_mount.instance_angle_step_rad = 0.0;
        stage.auxiliary_motor_mounts = vec![forward_mount];

        let curves = vec![curve.clone(), curve];
        let phases = exposed_stage_phase_margins_multi(
            &stage, &curves, PhysicsMode::HyperReal, &[0.3; 5],
        );
        let positioned = positioned_motor_masses_at(&stage, &curves, 0.0);
        let expected = mass_calculator::mass_properties_3d(&stage, &positioned);
        assert!((phases[0].cg_from_nose_m - expected.cg_m.x).abs() < 1.0e-12);
        assert!((phases[0].remaining_motor_mass_kg
            - curves.iter().map(|item| item.total_mass_kg).sum::<f64>())
            .abs() < 1.0e-12);

        let original_cg = phases[0].cg_from_nose_m;
        stage.auxiliary_motor_mounts[0].host_aft_m -= 0.20;
        let shifted = exposed_stage_phase_margins_multi(
            &stage, &curves, PhysicsMode::HyperReal, &[0.3; 5],
        );
        assert!(shifted[0].cg_from_nose_m < original_cg);
    }

    fn synthetic_phase(margin: f64) -> ExposedStagePhaseMargin {
        ExposedStagePhaseMargin {
            phase: "synthetic",
            time_s: 0.0,
            remaining_motor_mass_kg: 1.0,
            cg_from_nose_m: 1.0,
            cp_from_nose_m: 1.0 + margin,
            static_margin_cal: margin,
        }
    }

    #[test]
    fn initially_stable_but_burnout_unstable_fails_closed() {
        let phases = vec![
            synthetic_phase(2.0),
            synthetic_phase(1.8),
            synthetic_phase(1.6),
            synthetic_phase(1.4),
            synthetic_phase(1.3),
        ];
        assert!(!exposed_stage_passes(&phases, 1.5));
    }

    #[test]
    fn stage_unstable_throughout_fails_closed() {
        let phases = vec![synthetic_phase(-0.5); 5];
        assert!(!exposed_stage_passes(&phases, 1.5));
    }
}
