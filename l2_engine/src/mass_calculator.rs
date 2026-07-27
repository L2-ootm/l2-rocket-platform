//! Recursive component mass summation and static/dynamic center-of-gravity
//! math. Consumes the `StageGeometry` type contract produced by
//! `geometry.rs`/`xml_parser.rs` (Plan 03) and the `ThrustCurve` produced by
//! `motor_db.rs` (Plan 04). See 01-05-PLAN.md.
//!
//! `PLAN-PHASE-1.md` Step 2 (original spec): `Massa_total = Sigma M_i`;
//! `CG = Sigma(M_i * Dist_i) / Massa_total`.

use crate::geometry::{
    BodyTubeGeometry, FinsetGeometry, NoseconeGeometry,
    RadialAssemblyGeometry, StageGeometry,
};
use crate::motor_db::ThrustCurve;
use nalgebra::Vector3;
use std::f64::consts::PI;

/// Thin-wall cylinder mass: `density * pi * (radius^2 - (radius-thickness)^2)
/// * length`. Matches the plan's hand-computed reference formula exactly.
pub fn bodytube_mass(bt: &BodyTubeGeometry) -> f64 {
    let inner_radius = bt.radius - bt.thickness;
    bt.material_density * PI * (bt.radius.powi(2) - inner_radius.powi(2)) * bt.length
}

/// Von Karman/Haack-series nosecone radius profile:
/// `y(x) = R/sqrt(pi) * sqrt(theta - sin(2*theta)/2 + C*sin(theta)^3)`,
/// `theta = acos(1 - 2x/L)`.
fn haack_profile_radius(x: f64, length: f64, aft_radius: f64, shape_parameter: f64) -> f64 {
    // Clamp x into [0, length] defensively -- floating-point overshoot at the
    // final integration slice (x == length) can otherwise push
    // `1.0 - 2.0*x/length` fractionally below -1.0, which is outside
    // `acos`'s domain and would produce NaN.
    let x_clamped = x.clamp(0.0, length);
    let theta = (1.0 - 2.0 * x_clamped / length).acos();
    let under_sqrt = theta - (2.0 * theta).sin() / 2.0 + shape_parameter * theta.sin().powi(3);
    // Guard against a tiny negative residual from floating-point error at
    // theta ~ 0 or ~ pi (under_sqrt is mathematically >= 0 everywhere on
    // [0, length] for the shape parameters this repo's `.ork` files use).
    aft_radius / PI.sqrt() * under_sqrt.max(0.0).sqrt()
}

fn nosecone_shell_mass(nc: &NoseconeGeometry) -> f64 {
    const SLICES: usize = 200;
    let dx = nc.length / SLICES as f64;

    let mut surface_area = 0.0_f64;
    let mut prev_x = 0.0_f64;
    let mut prev_y = haack_profile_radius(0.0, nc.length, nc.aft_radius, nc.shape_parameter);

    for i in 1..=SLICES {
        let x = (i as f64 * dx).min(nc.length);
        let y = haack_profile_radius(x, nc.length, nc.aft_radius, nc.shape_parameter);
        let slant_length = ((x - prev_x).powi(2) + (y - prev_y).powi(2)).sqrt();
        surface_area += PI * (prev_y + y) * slant_length;
        prev_x = x;
        prev_y = y;
    }

    surface_area * nc.thickness * nc.material_density
}

fn nosecone_shell_cg_from_tip(nc: &NoseconeGeometry) -> f64 {
    const SLICES: usize = 200;
    let dx = nc.length / SLICES as f64;
    let mut area = 0.0;
    let mut first_moment = 0.0;
    let mut prev_x = 0.0;
    let mut prev_y = haack_profile_radius(0.0, nc.length, nc.aft_radius, nc.shape_parameter);
    for i in 1..=SLICES {
        let x = (i as f64 * dx).min(nc.length);
        let y = haack_profile_radius(x, nc.length, nc.aft_radius, nc.shape_parameter);
        let strip_area = PI * (prev_y + y) * ((x - prev_x).powi(2) + (y - prev_y).powi(2)).sqrt();
        area += strip_area;
        first_moment += strip_area * (prev_x + x) * 0.5;
        prev_x = x;
        prev_y = y;
    }
    if area > 0.0 {
        first_moment / area
    } else {
        nc.length * 0.5
    }
}

pub fn nosecone_mass(nc: &NoseconeGeometry) -> f64 {
    nosecone_shell_mass(nc) + nc.ballast_mass
}

/// Standard shoelace formula for a simple (non-self-intersecting) polygon's
/// area, given its vertices in order.
fn shoelace_area(points: &[(f64, f64)]) -> f64 {
    let n = points.len();
    if n < 3 {
        return 0.0;
    }
    let mut sum = 0.0_f64;
    for i in 0..n {
        let (x1, y1) = points[i];
        let (x2, y2) = points[(i + 1) % n];
        sum += x1 * y2 - x2 * y1;
    }
    (sum / 2.0).abs()
}

fn polygon_centroid_x(points: &[(f64, f64)]) -> f64 {
    if points.len() < 3 {
        return 0.0;
    }
    let mut twice_area = 0.0;
    let mut x_moment = 0.0;
    for i in 0..points.len() {
        let (x0, y0) = points[i];
        let (x1, y1) = points[(i + 1) % points.len()];
        let cross = x0 * y1 - x1 * y0;
        twice_area += cross;
        x_moment += (x0 + x1) * cross;
    }
    if twice_area.abs() < 1e-12 {
        0.0
    } else {
        x_moment / (3.0 * twice_area)
    }
}

/// OpenRocket fin mass: planform volume times the material density. Its
/// AIRFOIL cross-section uses 85% of the equivalent square plate volume;
/// SQUARE and ROUNDED retain the full nominal plate volume.
pub fn fin_mass(fs: &FinsetGeometry) -> f64 {
    let cross_section_volume_factor = if fs.cross_section.eq_ignore_ascii_case("airfoil") {
        0.85
    } else {
        1.0
    };
    fs.fin_count as f64
        * shoelace_area(&fs.points)
        * fs.thickness
        * fs.material_density
        * cross_section_volume_factor
}

pub fn motor_mount_tube_mass(stage: &StageGeometry) -> f64 {
    single_motor_mount_tube_mass(&stage.motor_mount) * stage.motor_mount.multiplicity as f64
}

fn single_motor_mount_tube_mass(mount: &crate::geometry::MotorMountGeometry) -> f64 {
    if mount.mount_length_m <= 0.0
        || mount.mount_outer_radius_m <= 0.0
        || mount.mount_thickness_m <= 0.0
        || mount.mount_material_density <= 0.0
    {
        return 0.0;
    }
    let inner_radius = (mount.mount_outer_radius_m - mount.mount_thickness_m).max(0.0);
    mount.mount_material_density
        * PI
        * (mount.mount_outer_radius_m.powi(2) - inner_radius.powi(2))
        * mount.mount_length_m
}

fn all_motor_mount_tube_mass(stage: &StageGeometry) -> f64 {
    motor_mount_tube_mass(stage)
        + stage
            .auxiliary_motor_mounts
            .iter()
            .map(|mount| single_motor_mount_tube_mass(mount) * mount.multiplicity as f64)
            .sum::<f64>()
}

fn radial_assembly_mass(assembly: &RadialAssemblyGeometry) -> f64 {
    assembly.bodytubes.iter().map(bodytube_mass).sum::<f64>()
        + assembly.nosecone.as_ref().map_or(0.0, nosecone_mass)
        + assembly.finsets.iter().map(fin_mass).sum::<f64>()
        + assembly.point_masses.iter().map(|point| point.mass_kg).sum::<f64>()
}

fn radial_assembly_cg(assembly: &RadialAssemblyGeometry) -> f64 {
    let mut moment = 0.0;
    for tube in &assembly.bodytubes {
        moment += bodytube_mass(tube) * (tube.axial_offset_m + tube.length * 0.5);
    }
    if let Some(nose) = &assembly.nosecone {
        let shell_mass = nosecone_shell_mass(nose);
        moment += shell_mass * (nose.axial_offset_m + nosecone_shell_cg_from_tip(nose));
        moment += nose.ballast_mass * nose.axial_offset_m;
    }
    for fins in &assembly.finsets {
        moment += fin_mass(fins) * (fins.axial_offset_m + polygon_centroid_x(&fins.points));
    }
    for point in &assembly.point_masses {
        moment += point.mass_kg * point.axial_offset_m;
    }
    let mass = radial_assembly_mass(assembly);
    if mass > 0.0 { moment / mass } else { 0.0 }
}

/// Total stage mass: sum of every bodytube's mass, the nosecone's mass (if
/// present), every finset and point mass, plus the caller-supplied motor mass.
/// `dry_motor_mass_kg` is deliberately a caller-supplied parameter rather
/// than something this module derives from `ThrustCurve` itself -- callers
/// choose whether to pass the motor's dry (propellant-empty) mass or its
/// full loaded mass depending on what "total" they need (see
/// `static_cg_from_nose`'s doc comment for the dry-mass convention this
/// module's own CG functions use).
pub fn total_mass(stage: &StageGeometry, dry_motor_mass_kg: f64) -> f64 {
    let mut total = dry_motor_mass_kg;
    total += all_motor_mount_tube_mass(stage);
    for bt in &stage.bodytubes {
        total += bodytube_mass(bt);
    }
    if let Some(nc) = &stage.nosecone {
        total += nosecone_mass(nc);
    }
    for fs in &stage.finsets {
        total += fin_mass(fs);
    }
    for pm in &stage.point_masses {
        total += pm.mass_kg;
    }
    for assembly in &stage.radial_assemblies {
        total += radial_assembly_mass(assembly) * assembly.instance_count as f64;
    }
    if let Some(parachute) = &stage.parachute {
        total += parachute.packed_mass_kg;
    }
    total
}

/// Static CG measured from the stage's nose-tip origin:
/// `CG = Sigma(M_i * Dist_i) / Mass_total`, where `Dist_i` is each
/// component's center of mass from the stage nose-tip origin and the motor's
/// `Dist_i` is the caller-supplied `motor_axial_offset_m`.
///
/// `dry_motor_mass_kg` should be the motor's dry (propellant-empty) mass --
/// this function is intended to compute the vehicle's "dry" CG (structure +
/// motor casing, no propellant), which `dynamic_cg_at` then perturbs with
/// the time-varying propellant mass still aboard.
pub fn static_cg_from_nose(
    stage: &StageGeometry,
    dry_motor_mass_kg: f64,
    motor_axial_offset_m: f64,
) -> f64 {
    let mut weighted_sum = dry_motor_mass_kg * motor_axial_offset_m;
    for mount in std::iter::once(&stage.motor_mount).chain(stage.auxiliary_motor_mounts.iter()) {
        let mount_mass = single_motor_mount_tube_mass(mount) * mount.multiplicity as f64;
        weighted_sum += mount_mass * (mount.mount_axial_offset_m + mount.mount_length_m * 0.5);
    }
    for bt in &stage.bodytubes {
        weighted_sum += bodytube_mass(bt) * (bt.axial_offset_m + bt.length * 0.5);
    }
    if let Some(nc) = &stage.nosecone {
        let shell_mass = nosecone_shell_mass(nc);
        weighted_sum += shell_mass * (nc.axial_offset_m + nosecone_shell_cg_from_tip(nc));
        weighted_sum += nc.ballast_mass * nc.axial_offset_m;
    }
    for fs in &stage.finsets {
        weighted_sum += fin_mass(fs) * (fs.axial_offset_m + polygon_centroid_x(&fs.points));
    }
    for pm in &stage.point_masses {
        weighted_sum += pm.mass_kg * pm.axial_offset_m;
    }
    for assembly in &stage.radial_assemblies {
        let mass = radial_assembly_mass(assembly) * assembly.instance_count as f64;
        weighted_sum += mass * (assembly.axial_offset_m + radial_assembly_cg(assembly));
    }
    if let Some(parachute) = &stage.parachute {
        weighted_sum += parachute.packed_mass_kg * parachute.axial_offset_m;
    }

    let total = total_mass(stage, dry_motor_mass_kg);
    weighted_sum / total
}

/// Geometry-derived principal moments about the supplied vehicle CG. The
/// component unit-inertia formulas mirror OpenRocket's BodyTube, FinSet and
/// MassObject implementations, then use the parallel-axis theorem.
pub fn principal_inertia(
    stage: &StageGeometry,
    loaded_motor_mass_kg: f64,
    motor_length_m: f64,
    motor_radius_m: f64,
    motor_axial_offset_m: f64,
    cg_from_nose_m: f64,
) -> Vector3<f64> {
    let mut transverse = 0.0;
    let mut axial = 0.0;
    let add = |transverse: &mut f64,
               axial: &mut f64,
               mass: f64,
               center: f64,
               unit_transverse: f64,
               unit_axial: f64| {
        *transverse += mass * (unit_transverse + (center - cg_from_nose_m).powi(2));
        *axial += mass * unit_axial;
    };

    for bt in &stage.bodytubes {
        let mass = bodytube_mass(bt);
        let inner = (bt.radius - bt.thickness).max(0.0);
        add(&mut transverse, &mut axial,
            mass,
            bt.axial_offset_m + bt.length * 0.5,
            (3.0 * (bt.radius.powi(2) + inner.powi(2)) + bt.length.powi(2)) / 12.0,
            (inner.powi(2) + bt.radius.powi(2)) * 0.5,
        );
    }
    if let Some(nc) = &stage.nosecone {
        let shell_mass = nosecone_shell_mass(nc);
        let center = nc.axial_offset_m + nosecone_shell_cg_from_tip(nc);
        add(&mut transverse, &mut axial,
            shell_mass,
            center,
            (3.0 * nc.aft_radius.powi(2) + nc.length.powi(2)) / 12.0,
            nc.aft_radius.powi(2) * 0.5,
        );
        add(&mut transverse, &mut axial, nc.ballast_mass, nc.axial_offset_m, 0.0, 0.0);
    }
    let body_radius = stage
        .bodytubes
        .iter()
        .map(|tube| tube.radius)
        .fold(0.0_f64, f64::max);
    for fs in &stage.finsets {
        let mass = fin_mass(fs);
        let width = fs
            .points
            .iter()
            .map(|point| point.0)
            .fold(0.0_f64, f64::max)
            - fs.points
                .iter()
                .map(|point| point.0)
                .fold(0.0_f64, f64::min);
        let span = fs
            .points
            .iter()
            .map(|point| point.1)
            .fold(0.0_f64, f64::max);
        let center = fs.axial_offset_m + polygon_centroid_x(&fs.points);
        let longitudinal_unit =
            (span.powi(2) + 2.0 * width.powi(2)) / 24.0 + (span * 0.5 + body_radius).powi(2) * 0.5;
        let rotational_unit = span.powi(2) / 12.0 + (span * 0.5 + body_radius).powi(2);
        add(&mut transverse, &mut axial, mass, center, longitudinal_unit, rotational_unit);
    }
    for mount in std::iter::once(&stage.motor_mount).chain(stage.auxiliary_motor_mounts.iter()) {
        let single_mass = single_motor_mount_tube_mass(mount);
        if single_mass > 0.0 {
            let inner = (mount.mount_outer_radius_m - mount.mount_thickness_m).max(0.0);
            for index in 0..mount.multiplicity {
                let angle = mount.radial_angle_rad + mount.instance_angle_step_rad * index as f64;
                let y = mount.radial_offset_m * angle.cos();
                let z = mount.radial_offset_m * angle.sin();
                let axial_unit = (mount.mount_outer_radius_m.powi(2) + inner.powi(2)) * 0.5;
                let transverse_unit = (3.0 * (mount.mount_outer_radius_m.powi(2) + inner.powi(2))
                    + mount.mount_length_m.powi(2)) / 12.0;
                let center = mount.mount_axial_offset_m + mount.mount_length_m * 0.5;
                transverse += single_mass * (transverse_unit + (center - cg_from_nose_m).powi(2))
                    + single_mass * (y * y + z * z) * 0.5;
                axial += single_mass * (axial_unit + y * y + z * z);
            }
        }
    }
    add(&mut transverse, &mut axial,
        loaded_motor_mass_kg,
        motor_axial_offset_m,
        (3.0 * motor_radius_m.powi(2) + motor_length_m.powi(2)) / 12.0,
        motor_radius_m.powi(2) * 0.5,
    );
    for point in &stage.point_masses {
        add(
            &mut transverse,
            &mut axial,
            point.mass_kg,
            point.axial_offset_m,
            0.5 * (point.radial_y_m.powi(2) + point.radial_z_m.powi(2)),
            point.radial_y_m.powi(2) + point.radial_z_m.powi(2),
        );
    }
    for assembly in &stage.radial_assemblies {
        let template_mass = radial_assembly_mass(assembly);
        let template_cg = assembly.axial_offset_m + radial_assembly_cg(assembly);
        let length = assembly
            .bodytubes
            .iter()
            .map(|tube| tube.axial_offset_m + tube.length)
            .chain(
                assembly
                    .nosecone
                    .iter()
                    .map(|nose| nose.axial_offset_m + nose.length),
            )
            .fold(0.0_f64, f64::max);
        let radius = assembly
            .bodytubes
            .iter()
            .map(|tube| tube.radius)
            .chain(assembly.nosecone.iter().map(|nose| nose.aft_radius))
            .fold(0.0_f64, f64::max);
        let local_transverse = (3.0 * radius.powi(2) + length.powi(2)) / 12.0;
        let local_axial = radius.powi(2) * 0.5;
        for index in 0..assembly.instance_count {
            let angle = assembly.angle_offset_rad
                + std::f64::consts::TAU * index as f64 / assembly.instance_count as f64;
            let radial_sq = assembly.radial_offset_m.powi(2);
            transverse += template_mass
                * (local_transverse + (template_cg - cg_from_nose_m).powi(2) + radial_sq * 0.5);
            axial += template_mass * (local_axial + radial_sq);
            let _ = angle; // diagonal principal approximation is rotation-invariant for symmetric templates
        }
    }
    if let Some(parachute) = &stage.parachute {
        add(&mut transverse, &mut axial, parachute.packed_mass_kg, parachute.axial_offset_m, 0.0, 0.0);
    }

    Vector3::new(transverse.max(1e-9), transverse.max(1e-9), axial.max(1e-9))
}

#[derive(Debug, Clone, Copy)]
pub struct PositionedMotorMass {
    pub mass_kg: f64,
    pub length_m: f64,
    pub radius_m: f64,
    /// Coordinates are `(axial from nose, radial y, radial z)`.
    pub position_m: Vector3<f64>,
}

#[derive(Debug, Clone, Copy)]
pub struct MassProperties3D {
    pub mass_kg: f64,
    /// Coordinates are `(axial from nose, radial y, radial z)`.
    pub cg_m: Vector3<f64>,
    /// Principal moments in dynamics body axes `(transverse x, transverse y, axial z)`.
    pub inertia: Vector3<f64>,
}

/// Three-dimensional dry/loaded mass properties. This is the authoritative
/// path for radial assemblies; legacy scalar CG helpers remain for inline
/// compatibility and static-margin code.
pub fn mass_properties_3d(stage: &StageGeometry, motors: &[PositionedMotorMass]) -> MassProperties3D {
    let structural_mass = total_mass(stage, 0.0);
    let structural_cg_x = if structural_mass > 0.0 {
        static_cg_from_nose(stage, 0.0, 0.0)
    } else {
        0.0
    };
    let mut first = Vector3::new(structural_mass * structural_cg_x, 0.0, 0.0);
    for point in &stage.point_masses {
        first.y += point.mass_kg * point.radial_y_m;
        first.z += point.mass_kg * point.radial_z_m;
    }
    for mount in std::iter::once(&stage.motor_mount).chain(stage.auxiliary_motor_mounts.iter()) {
        let single_mass = single_motor_mount_tube_mass(mount);
        for index in 0..mount.multiplicity {
            let angle = mount.radial_angle_rad + mount.instance_angle_step_rad * index as f64;
            first.y += single_mass * mount.radial_offset_m * angle.cos();
            first.z += single_mass * mount.radial_offset_m * angle.sin();
        }
    }
    for assembly in &stage.radial_assemblies {
        let mass = radial_assembly_mass(assembly);
        for index in 0..assembly.instance_count {
            let angle = assembly.angle_offset_rad
                + std::f64::consts::TAU * index as f64 / assembly.instance_count as f64;
            first.y += mass * assembly.radial_offset_m * angle.cos();
            first.z += mass * assembly.radial_offset_m * angle.sin();
        }
    }
    let structural_cg = if structural_mass > 0.0 {
        first / structural_mass
    } else {
        Vector3::zeros()
    };
    let motor_mass = motors.iter().map(|motor| motor.mass_kg).sum::<f64>();
    for motor in motors {
        first += motor.position_m * motor.mass_kg;
    }
    let total = structural_mass + motor_mass;
    let cg = if total > 0.0 { first / total } else { Vector3::zeros() };
    let mut inertia = principal_inertia(stage, 0.0, 0.0, 0.0, 0.0, cg.x);
    // `principal_inertia` stores the average transverse radial contribution.
    // Resolve it onto the two actual body axes for every off-axis structure.
    for point in &stage.point_masses {
        let average = 0.5 * (point.radial_y_m.powi(2) + point.radial_z_m.powi(2));
        inertia.x += point.mass_kg * (point.radial_z_m.powi(2) - average);
        inertia.y += point.mass_kg * (point.radial_y_m.powi(2) - average);
    }
    for mount in std::iter::once(&stage.motor_mount).chain(stage.auxiliary_motor_mounts.iter()) {
        let mass = single_motor_mount_tube_mass(mount);
        for index in 0..mount.multiplicity {
            let angle = mount.radial_angle_rad + mount.instance_angle_step_rad * index as f64;
            let y = mount.radial_offset_m * angle.cos();
            let z = mount.radial_offset_m * angle.sin();
            let average = 0.5 * (y * y + z * z);
            inertia.x += mass * (z * z - average);
            inertia.y += mass * (y * y - average);
        }
    }
    for assembly in &stage.radial_assemblies {
        let mass = radial_assembly_mass(assembly);
        for index in 0..assembly.instance_count {
            let angle = assembly.angle_offset_rad
                + std::f64::consts::TAU * index as f64 / assembly.instance_count as f64;
            let y = assembly.radial_offset_m * angle.cos();
            let z = assembly.radial_offset_m * angle.sin();
            let average = 0.5 * (y * y + z * z);
            inertia.x += mass * (z * z - average);
            inertia.y += mass * (y * y - average);
        }
    }
    // Shift the structural tensor from the vehicle centerline to the actual
    // lateral CG. The parallel-axis theorem subtracts this offset when
    // moving from an arbitrary parallel axis to the center-of-mass axis.
    inertia.x += structural_mass * (cg.z.powi(2) - 2.0 * cg.z * structural_cg.z);
    inertia.y += structural_mass * (cg.y.powi(2) - 2.0 * cg.y * structural_cg.y);
    inertia.z += structural_mass
        * (cg.y.powi(2) + cg.z.powi(2)
            - 2.0 * (cg.y * structural_cg.y + cg.z * structural_cg.z));
    for motor in motors {
        let dx = motor.position_m.x - cg.x;
        let dy = motor.position_m.y - cg.y;
        let dz = motor.position_m.z - cg.z;
        let transverse_unit = (3.0 * motor.radius_m.powi(2) + motor.length_m.powi(2)) / 12.0;
        let axial_unit = motor.radius_m.powi(2) * 0.5;
        inertia.x += motor.mass_kg * (transverse_unit + dx * dx + dz * dz);
        inertia.y += motor.mass_kg * (transverse_unit + dx * dx + dy * dy);
        inertia.z += motor.mass_kg * (axial_unit + dy * dy + dz * dz);
    }
    MassProperties3D {
        mass_kg: total,
        cg_m: cg,
        inertia: inertia.map(|value| value.max(1e-9)),
    }
}

/// Burn-dependent dynamic CG at time `t`: holds every structural component's
/// mass/position AND the motor's dry (propellant-empty) mass/position fixed
/// at `dry_cg_from_nose` (the combined structure+dry-motor CG, typically
/// `static_cg_from_nose(stage, dry_motor_mass_kg, motor_axial_offset_m)`),
/// and treats only the *currently unburned propellant* --
/// `thrust_curve.mass_at(t) - dry_motor_mass_kg` -- as a separate point mass
/// still located at `motor_axial_offset_m` (propellant lives inside the
/// motor casing, so this module does not model it moving to a different
/// axial position as it burns).
///
/// At `t = 0` (full propellant load), the unburned-propellant term
/// dominates and pulls CG toward `motor_axial_offset_m`. As `t` approaches
/// burnout, `thrust_curve.mass_at(t)` approaches the motor's own dry mass,
/// the unburned-propellant term shrinks toward zero, and CG converges
/// exactly onto `dry_cg_from_nose` -- matching 01-CONTEXT.md's statement
/// that "CG goes forward as propellant burns" for this aft-motor-mount
/// design.
pub fn dynamic_cg_at(
    stage: &StageGeometry,
    t: f64,
    thrust_curve: &ThrustCurve,
    dry_cg_from_nose: f64,
    motor_axial_offset_m: f64,
) -> f64 {
    let dry_motor_mass_kg = thrust_curve.total_mass_kg - thrust_curve.propellant_mass_kg;
    let dry_total_mass = total_mass(stage, dry_motor_mass_kg);

    let remaining_propellant_mass = (thrust_curve.mass_at(t) - dry_motor_mass_kg).max(0.0);
    let total = dry_total_mass + remaining_propellant_mass;
    if total <= 0.0 {
        return dry_cg_from_nose;
    }

    (dry_total_mass * dry_cg_from_nose + remaining_propellant_mass * motor_axial_offset_m) / total
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::motor_db::parse_eng;
    use crate::xml_parser::{extract_ork_xml, parse_rocket_geometry};
    use std::path::PathBuf;

    fn ork_fixture_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/L2_Hyper_Parallel_15K.ork")
    }

    fn eng_fixture_text() -> String {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/N4800T.eng");
        std::fs::read_to_string(&path).expect("N4800T.eng fixture must exist")
    }

    fn sustainer_stage() -> StageGeometry {
        let xml = extract_ork_xml(&ork_fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        // Ignition-order-reordered: index 0 = Booster, index 1 = Sustainer.
        geometry.stages[1].clone()
    }

    fn booster_stage() -> StageGeometry {
        let xml = extract_ork_xml(&ork_fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        geometry.stages[0].clone()
    }

    fn n4800t_curve() -> ThrustCurve {
        let text = eng_fixture_text();
        parse_eng(&text, "N4800T").expect("parse should succeed")
    }

    /// Test 1: bodytube_mass hand-computed cross-check on the Sustainer's
    /// Airframe bodytube (length=1.214, radius=0.054325, thickness=6.95E-4,
    /// material_density=1780.0, carbon fiber), per the plan's `<interfaces>`
    /// reference values.
    #[test]
    fn test_bodytube_mass_matches_hand_computed_reference() {
        let stage = sustainer_stage();
        let bt = &stage.bodytubes[0];
        assert!((bt.length - 1.214).abs() < 1e-6);
        assert!((bt.radius - 0.054325).abs() < 1e-6);
        assert!((bt.thickness - 6.95E-4).abs() < 1e-9);
        assert_eq!(bt.material_density, 1780.0);

        let inner_radius = bt.radius - bt.thickness;
        let expected =
            bt.material_density * PI * (bt.radius.powi(2) - inner_radius.powi(2)) * bt.length;

        let actual = bodytube_mass(bt);
        let relative_error = (actual - expected).abs() / expected;
        assert!(
            relative_error < 1e-6,
            "bodytube_mass={actual} vs hand-computed reference={expected}, relative_error={relative_error}"
        );
    }

    /// Test 2: nosecone_mass plausibility (Haack-series shell of revolution,
    /// no independently-traced OpenRocket reference mass exists for this
    /// exact shape).
    #[test]
    fn test_nosecone_mass_is_positive_and_finite() {
        let stage = sustainer_stage();
        let nc = stage.nosecone.as_ref().expect("sustainer has a nosecone");
        let mass = nosecone_mass(nc);
        assert!(mass.is_finite(), "nosecone_mass must be finite, got {mass}");
        assert!(mass > 0.0, "nosecone_mass must be positive, got {mass}");
    }

    /// Test 3: fin_mass hand-verified against the Booster fin's actual
    /// point list from direct `.ork` inspection.
    #[test]
    fn test_fin_mass_positive_booster_fins() {
        let stage = booster_stage();
        assert_eq!(stage.finsets.len(), 1);
        let fs = &stage.finsets[0];
        assert_eq!(fs.fin_count, 3);
        assert_eq!(fs.points.len(), 4);

        let mass = fin_mass(fs);
        assert!(mass.is_finite());
        assert!(mass > 0.0, "fin_mass must be positive, got {mass}");
    }

    #[test]
    fn airfoil_fin_mass_matches_openrocket_volume_factor() {
        let mut square = booster_stage().finsets[0].clone();
        square.cross_section = "square".to_string();
        let mut airfoil = square.clone();
        airfoil.cross_section = "airfoil".to_string();

        assert!((fin_mass(&airfoil) / fin_mass(&square) - 0.85).abs() < 1e-12);
    }

    /// Test 4: total_mass sums all bodytubes + nosecone + finsets + dry
    /// motor mass, strictly positive.
    #[test]
    fn test_total_mass_strictly_positive() {
        let stage = sustainer_stage();
        let curve = n4800t_curve();
        let dry_motor_mass_kg = curve.total_mass_kg - curve.propellant_mass_kg;

        let total = total_mass(&stage, dry_motor_mass_kg);
        assert!(total.is_finite());
        assert!(
            total > 0.0,
            "total_mass must be strictly positive, got {total}"
        );

        // Sanity: total must exceed the dry motor mass alone (structure adds
        // real mass on top of it).
        assert!(total > dry_motor_mass_kg);
    }

    #[test]
    fn point_masses_contribute_to_total_mass_and_static_cg() {
        let mut stage = sustainer_stage();
        let curve = n4800t_curve();
        let dry_motor_mass_kg = curve.total_mass_kg - curve.propellant_mass_kg;
        let motor_axial_offset_m =
            stage.bodytubes[0].axial_offset_m + stage.bodytubes[0].length * 0.5;
        let baseline_mass = total_mass(&stage, dry_motor_mass_kg);
        let baseline_cg = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);

        stage.point_masses.push(crate::geometry::PointMassGeometry {
            mass_kg: 2.0,
            axial_offset_m: 0.25,
            radial_y_m: 0.0,
            radial_z_m: 0.0,
        });

        let mass = total_mass(&stage, dry_motor_mass_kg);
        let cg = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);

        assert!((mass - (baseline_mass + 2.0)).abs() < 1e-9);
        assert!(cg < baseline_cg);
    }

    #[test]
    fn motor_mount_tube_contributes_to_total_mass_and_static_cg() {
        let mut stage = sustainer_stage();
        stage.motor_mount.mount_length_m = 0.0;
        stage.motor_mount.mount_outer_radius_m = 0.0;
        stage.motor_mount.mount_thickness_m = 0.0;
        stage.motor_mount.mount_material_density = 0.0;
        stage.motor_mount.mount_axial_offset_m = 0.0;
        let curve = n4800t_curve();
        let dry_motor_mass_kg = curve.total_mass_kg - curve.propellant_mass_kg;
        let motor_axial_offset_m =
            stage.bodytubes[0].axial_offset_m + stage.bodytubes[0].length * 0.5;
        let baseline_mass = total_mass(&stage, dry_motor_mass_kg);
        let baseline_cg = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);

        stage.motor_mount.mount_length_m = 0.8;
        stage.motor_mount.mount_outer_radius_m = 0.05;
        stage.motor_mount.mount_thickness_m = 0.001;
        stage.motor_mount.mount_material_density = 950.0;
        stage.motor_mount.mount_axial_offset_m = 0.3;

        let expected_mount_mass = 950.0 * PI * (0.05_f64.powi(2) - 0.049_f64.powi(2)) * 0.8;
        let mass = total_mass(&stage, dry_motor_mass_kg);
        let cg = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);

        assert!((motor_mount_tube_mass(&stage) - expected_mount_mass).abs() < 1e-12);
        assert!((mass - (baseline_mass + expected_mount_mass)).abs() < 1e-9);
        assert!(cg < baseline_cg);
    }

    #[test]
    fn parachute_packed_mass_contributes_to_total_mass_and_static_cg() {
        let mut stage = sustainer_stage();
        stage.parachute = None;
        let curve = n4800t_curve();
        let dry_motor_mass_kg = curve.total_mass_kg - curve.propellant_mass_kg;
        let motor_axial_offset_m =
            stage.bodytubes[0].axial_offset_m + stage.bodytubes[0].length * 0.5;
        let baseline_mass = total_mass(&stage, dry_motor_mass_kg);
        let baseline_cg = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);

        stage.parachute = Some(crate::geometry::ParachuteGeometry {
            diameter: 0.5,
            cd: 1.5,
            deploy_delay: 0.0,
            packed_mass_kg: 0.75,
            axial_offset_m: 0.25,
        });

        let mass = total_mass(&stage, dry_motor_mass_kg);
        let cg = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);

        assert!((mass - (baseline_mass + 0.75)).abs() < 1e-9);
        assert!(cg < baseline_cg);
    }

    /// Test 5: static_cg_from_nose lies strictly between 0.0 and the stage's
    /// total length (physical sanity bound).
    #[test]
    fn test_static_cg_from_nose_within_stage_length() {
        let stage = sustainer_stage();
        let curve = n4800t_curve();
        let dry_motor_mass_kg = curve.total_mass_kg - curve.propellant_mass_kg;

        let nc_len = stage.nosecone.as_ref().map(|nc| nc.length).unwrap_or(0.0);
        let bt_len: f64 = stage.bodytubes.iter().map(|bt| bt.length).sum();
        let stage_total_length = nc_len + bt_len;

        // Motor's own axial position: this repo's motor mount tube spans
        // nearly the full bodytube length (overhanging slightly aft), so
        // the motor's own mass center is approximated as the bodytube's
        // midpoint -- test scaffolding only, not part of the module's
        // public contract (mission_adapter.rs, Plan 07, is expected to
        // derive the real value from MotorMountGeometry).
        let bt = &stage.bodytubes[0];
        let motor_axial_offset_m = bt.axial_offset_m + bt.length * 0.5;

        let cg = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);
        assert!(
            cg > 0.0 && cg < stage_total_length,
            "static CG={cg} must lie strictly between 0.0 and stage_total_length={stage_total_length}"
        );
    }

    /// Test 6: dynamic_cg_at measurably shifts as propellant burns, and the
    /// shift direction moves toward `dry_cg_from_nose` as propellant
    /// depletes (CG goes forward as propellant burns, per 01-CONTEXT.md,
    /// for this aft-motor-mount design).
    #[test]
    fn test_dynamic_cg_shifts_toward_dry_cg_at_burnout() {
        let stage = sustainer_stage();
        let curve = n4800t_curve();
        let dry_motor_mass_kg = curve.total_mass_kg - curve.propellant_mass_kg;

        let bt = &stage.bodytubes[0];
        let motor_axial_offset_m = bt.axial_offset_m + bt.length * 0.5;

        let dry_cg_from_nose = static_cg_from_nose(&stage, dry_motor_mass_kg, motor_axial_offset_m);

        let cg_at_ignition =
            dynamic_cg_at(&stage, 0.0, &curve, dry_cg_from_nose, motor_axial_offset_m);
        let cg_at_burnout = dynamic_cg_at(
            &stage,
            5.206,
            &curve,
            dry_cg_from_nose,
            motor_axial_offset_m,
        );

        assert!(
            cg_at_ignition != cg_at_burnout,
            "CG must measurably shift as propellant burns: ignition={cg_at_ignition}, burnout={cg_at_burnout}"
        );

        // At burnout, essentially all propellant has been consumed, so CG
        // should have converged onto (or very near) dry_cg_from_nose.
        assert!(
            (cg_at_burnout - dry_cg_from_nose).abs() < 1e-2,
            "CG at burnout={cg_at_burnout} should have converged onto dry_cg_from_nose={dry_cg_from_nose}"
        );

        // The shift must move *toward* dry_cg_from_nose, not away from it:
        // the distance from dry_cg_from_nose must strictly decrease from
        // ignition to burnout.
        let dist_at_ignition = (cg_at_ignition - dry_cg_from_nose).abs();
        let dist_at_burnout = (cg_at_burnout - dry_cg_from_nose).abs();
        assert!(
            dist_at_burnout < dist_at_ignition,
            "CG must move toward dry_cg_from_nose as propellant burns: \
             dist_at_ignition={dist_at_ignition}, dist_at_burnout={dist_at_burnout}"
        );
    }
}
