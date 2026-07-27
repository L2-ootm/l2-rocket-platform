//! Shared geometry type contracts produced by `xml_parser.rs` and consumed by
//! every downstream Wave 3+ module (`mass_calculator.rs`, `barrowman.rs`,
//! `mission_adapter.rs`). Defined interface-first in Plan 01-03 even though
//! this plan's own file ownership is otherwise just the `.ork` parser.

/// Full parsed rocket, stages ordered by ignition sequence (index 0 fires first),
/// NOT raw `.ork` XML document order (which lists stages nose-to-tail).
#[derive(Debug, Clone)]
pub struct RocketGeometry {
    pub stages: Vec<StageGeometry>,
}

#[derive(Debug, Clone)]
pub struct StageGeometry {
    pub name: String,
    pub nosecone: Option<NoseconeGeometry>,
    pub bodytubes: Vec<BodyTubeGeometry>,
    pub finsets: Vec<FinsetGeometry>,
    pub point_masses: Vec<PointMassGeometry>,
    /// Primary mount retained for source compatibility with parsed `.ork`
    /// vehicles and older single-MOTOR_MOUNT ASTs.
    pub motor_mount: MotorMountGeometry,
    /// Additional independently ignited mounts in the same physical stage.
    pub auxiliary_motor_mounts: Vec<MotorMountGeometry>,
    /// Permanent radial component assemblies (OpenRocket PodSet semantics).
    /// Each record is a compact template replicated `instance_count` times.
    pub radial_assemblies: Vec<RadialAssemblyGeometry>,
    pub separation: Option<SeparationConfig>,
    pub parachute: Option<ParachuteGeometry>,
    /// Distance from stage top/nosecone-tip to this stage's own top face,
    /// needed by Plan 05's CG math. Populated as `0.0` placeholder here since
    /// it is not directly derivable from a single XML attribute in this
    /// plan's scope -- Plan 05 may extend this further.
    pub axial_offset_m: f64,
}

impl StageGeometry {
    /// Projected side area of the symmetric components used by Galejs body lift.
    pub fn planform_area(&self) -> f64 {
        self.nosecone
            .as_ref()
            .map_or(0.0, |nose| nose.length * nose.aft_radius)
            + self
                .bodytubes
                .iter()
                .map(|tube| 2.0 * tube.radius * tube.length)
                .sum::<f64>()
    }

    /// Longitudinal centroid of symmetric-component projected side area.
    /// The returned coordinate is measured from the stage origin.
    pub fn planform_center(&self) -> f64 {
        let mut area = 0.0;
        let mut first_moment = 0.0;

        if let Some(nose) = &self.nosecone {
            let nose_area = nose.length * nose.aft_radius;
            let nose_center = nose.axial_offset_m + 2.0 * nose.length / 3.0;
            area += nose_area;
            first_moment += nose_area * nose_center;
        }
        for tube in &self.bodytubes {
            let tube_area = 2.0 * tube.radius * tube.length;
            area += tube_area;
            first_moment += tube_area * (tube.axial_offset_m + tube.length / 2.0);
        }

        if area > 1e-12 {
            first_moment / area
        } else {
            0.0
        }
    }
}

#[derive(Debug, Clone)]
pub struct PointMassGeometry {
    pub mass_kg: f64,
    /// Distance from the stage nose-tip origin to the mass component's own
    /// position, in meters.
    pub axial_offset_m: f64,
    /// Lateral coordinates in the stage frame. Inline legacy masses use zero.
    pub radial_y_m: f64,
    pub radial_z_m: f64,
}

#[derive(Debug, Clone)]
pub struct NoseconeGeometry {
    pub shape: NoseShape,
    pub shape_parameter: f64,
    pub length: f64,
    pub aft_radius: f64,
    pub thickness: f64,
    pub material_density: f64,
    pub finish: SurfaceFinish,
    pub axial_offset_m: f64,
    pub ballast_mass: f64,
}

/// Maps OpenRocket's `<shape>` string 1:1. Unrecognized values are a parse
/// error, not a silent default -- see `xml_parser::parse_nosecone`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoseShape {
    VonKarmanHaack,
    Ogive,
    Conical,
    Ellipsoid,
    PowerSeries,
    Parabolic,
}

#[derive(Debug, Clone)]
pub struct BodyTubeGeometry {
    pub length: f64,
    pub radius: f64,
    pub thickness: f64,
    pub material_density: f64,
    pub finish: SurfaceFinish,
    /// Distance from the stage's nose-tip origin to this bodytube's own
    /// front edge, in meters. Added in Plan 05 -- populated by
    /// `xml_parser::parse_stage`'s sequential-stacking cursor (external
    /// components stack nose-to-tail in document order in this repo's
    /// `.ork` files, matching OpenRocket's default "after previous sibling"
    /// placement for stage-level children with no explicit `<axialoffset>`).
    pub axial_offset_m: f64,
}

/// Freeform fin points only -- `<trapezoidfinset>` is never present in this
/// repo's `.ork` files per 01-RESEARCH.md finding #3, so no trapezoid path
/// is implemented.
#[derive(Debug, Clone)]
pub struct FinsetGeometry {
    pub fin_count: u32,
    pub points: Vec<(f64, f64)>,
    pub thickness: f64,
    /// OpenRocket fin cross-section string (`airfoil`, `rounded`, or `square`).
    /// Carried through from AST/XML so OpenRocket compatibility mode can mirror
    /// FinSetCalc pressure/base drag semantics.
    pub cross_section: String,
    pub material_density: f64,
    pub finish: SurfaceFinish,
    pub cant_rad: f64,
    /// Distance from the stage's nose-tip origin to this finset's own front
    /// edge, in meters. Added in Plan 05 -- resolved from the finset's own
    /// `<axialoffset method="...">` XML value against its parent bodytube's
    /// `axial_offset_m`/`length`, via `xml_parser::resolve_axial_offset()`.
    /// [ASSUMED reasonable -- see that function's doc comment for the
    /// method-sign-convention caveat.]
    pub axial_offset_m: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SurfaceFinish {
    Polished,
    Smooth,
    Unfinished,
    Rough,
}

impl SurfaceFinish {
    /// [ASSUMED reasonable approximations of OpenRocket's
    /// `ExternalComponent.Finish` roughness table -- not independently
    /// source-verified in 01-RESEARCH.md; flag for Plan 06/08 to revisit if
    /// drag accuracy falls short.]
    pub fn roughness_m(&self) -> f64 {
        match self {
            SurfaceFinish::Polished => 1e-6,
            SurfaceFinish::Smooth => 2e-6,
            SurfaceFinish::Unfinished => 6e-6,
            SurfaceFinish::Rough => 2e-5,
        }
    }
}

#[derive(Debug, Clone)]
pub struct MotorMountGeometry {
    /// Functional role used by topology/scoring diagnostics (`main`,
    /// `retro`, `booster`, ...). It does not alter thrust direction yet.
    pub role: String,
    /// Number of identical motors installed in this mount/cluster position.
    pub multiplicity: u32,
    /// Distance of each motor axis from the vehicle centerline.
    pub radial_offset_m: f64,
    /// Angle of the first instance in the body lateral plane.
    pub radial_angle_rad: f64,
    /// Angular spacing between replicated instances. Zero keeps legacy
    /// colocated cluster semantics; PodSet uses `2*pi/multiplicity`.
    pub instance_angle_step_rad: f64,
    /// Explicit host-tube inner radius for external assemblies. Zero means
    /// use the parent stage's narrowest core tube (legacy behavior).
    pub host_inner_radius_m: f64,
    /// Aft coordinate of the tube hosting this mount, in parent-stage axial
    /// coordinates. Zero preserves legacy inference from the core tube.
    pub host_aft_m: f64,
    pub ignition_event: String,
    pub ignition_delay: f64,
    pub motor_designation: String,
    pub motor_overhang_m: f64,
    pub mount_length_m: f64,
    pub mount_outer_radius_m: f64,
    pub mount_thickness_m: f64,
    pub mount_material_density: f64,
    pub mount_axial_offset_m: f64,
    /// The motor's own RASP-style ejection-charge delay, from `<motor><delay>`
    /// (e.g. `<delay>14.0</delay>`). Added in Plan 07 -- this is the value
    /// that drives `StageBuilder::separation_coast` ("how long after
    /// propellant depletion before this stage's mass drops and the mission
    /// advances"), NOT `StageGeometry.separation.delay` (which mirrors the
    /// unrelated stage-level `<separationdelay>` tag). See 01-07-PLAN.md's
    /// CRITICAL timing-value distinction.
    pub ejection_charge_delay: f64,
}

/// One permanent radial assembly template. Components use assembly-local
/// axial coordinates; the instance pose places their aggregate mass and aero
/// contribution in the parent stage.
#[derive(Debug, Clone)]
pub struct RadialAssemblyGeometry {
    pub name: String,
    pub kind: String,
    pub instance_count: u32,
    pub radial_offset_m: f64,
    pub angle_offset_rad: f64,
    pub axial_offset_m: f64,
    pub nosecone: Option<NoseconeGeometry>,
    pub bodytubes: Vec<BodyTubeGeometry>,
    pub finsets: Vec<FinsetGeometry>,
    pub point_masses: Vec<PointMassGeometry>,
    /// Multiplier over the assembly's isolated drag contribution. `1.0` is
    /// the conservative uncalibrated default; OpenRocket tables may override.
    pub aero_interference_factor: f64,
}

/// Stores the raw `<separationevent>`/`<separationdelay>`/`<separationaltitude>`
/// values verbatim -- this struct does NOT interpret which one is the "real"
/// trigger. Plan 07's mission_adapter (informed by Plan 02's investigation
/// findings) decides that using the motor's own `<delay>` tag drives
/// `separation_coast`, not this struct's `delay` field, which mirrors the
/// stage-level `<separationdelay>` tag verbatim.
#[derive(Debug, Clone)]
pub struct SeparationConfig {
    pub event: String,
    pub delay: f64,
    pub altitude: f64,
}

#[derive(Debug, Clone)]
pub struct ParachuteGeometry {
    pub diameter: f64,
    pub cd: f64,
    pub deploy_delay: f64,
    pub packed_mass_kg: f64,
    /// Distance from stage nose-tip origin to the packed recovery-device CG.
    pub axial_offset_m: f64,
}
