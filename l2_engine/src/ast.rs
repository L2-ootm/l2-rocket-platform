use std::collections::HashMap;

use crate::sim_core::dynamics::state::SimConfig;
use crate::sim_core::io::json::{FlightSummary, StageLanding};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::PhysicsMode;
use crate::divergence::{DivergenceModel, extract_features};
use crate::errors::L2EngineError;
use crate::geometry::{
    BodyTubeGeometry, FinsetGeometry, MotorMountGeometry, NoseShape, NoseconeGeometry,
    ParachuteGeometry, PointMassGeometry, RadialAssemblyGeometry, RocketGeometry, StageGeometry,
    SurfaceFinish,
};
use crate::mission_adapter::{
    NoOpController, OrkSimulationEnvironment, apply_openrocket_environment,
};
use crate::motor_db::ThrustCurve;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AstNode {
    #[serde(rename = "type")]
    pub node_type: String,
    #[serde(default)]
    pub params: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AstEvalResult {
    pub id: String,
    pub status: String,
    pub score: f64,
    pub apogee_m: f64,
    pub apogee_east_m: f64,
    pub apogee_north_m: f64,
    pub stage_landings: Vec<StageLanding>,
    pub total_prop_mass_kg: f64,
    pub mach: f64,
    pub min_static_margin: f64,
    pub margins: Vec<f64>,
    pub features: Vec<f64>,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AstCandidate {
    pub id: String,
    pub ast: Vec<AstNode>,
    #[serde(default)]
    pub signature: String,
    #[serde(default)]
    pub environment: Option<OrkSimulationEnvironment>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AstEvalBatch {
    #[serde(default = "default_target_apogee")]
    pub target_apogee_m: f64,
    #[serde(default)]
    pub physics_mode: Option<String>,
    #[serde(default)]
    pub execution_profile: Option<String>,
    #[serde(default)]
    pub objectives: Vec<AstObjective>,
    #[serde(default)]
    pub constraints: Value,
    #[serde(default)]
    pub phase_machs: Vec<f64>,
    pub candidates: Vec<AstCandidate>,
    #[serde(default)]
    pub calibrations: HashMap<String, AstCalibration>,
    #[serde(default)]
    pub divergence_model: Option<DivergenceModel>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(untagged)]
pub enum AstCalibration {
    Legacy(f64),
    Components {
        #[serde(default = "unit_delta")]
        apogee_delta: f64,
        #[serde(default = "unit_delta")]
        mach_delta: f64,
        #[serde(default = "unit_delta")]
        margin_delta: f64,
    },
}

impl AstCalibration {
    fn apogee_delta(self) -> f64 {
        match self {
            AstCalibration::Legacy(delta) => delta,
            AstCalibration::Components { apogee_delta, .. } => apogee_delta,
        }
    }

    fn mach_delta(self) -> f64 {
        match self {
            AstCalibration::Legacy(delta) => delta,
            AstCalibration::Components { mach_delta, .. } => mach_delta,
        }
    }

    fn margin_delta(self) -> f64 {
        match self {
            AstCalibration::Legacy(_) => 1.0,
            AstCalibration::Components { margin_delta, .. } => margin_delta,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AstObjective {
    #[serde(default)]
    pub metric: String,
    #[serde(default)]
    pub kind: String,
    pub value: Option<f64>,
    pub target: Option<f64>,
    pub min_value: Option<f64>,
    pub scale: Option<f64>,
    pub weight: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScoringAggregate {
    Scalar,
    MeanOverStages,
    SumOverStages,
    MaxOverStages,
}

/// One weighted term of a data-driven scoring formula: `coefficient * Sum_i
/// (metric_i - reference_i)^power`, with per-stage metrics collapsed to a
/// scalar per `aggregate` before the power/coefficient is applied. Negative
/// coefficients are penalties, positive are bonuses -- sign lives in mission
/// data, not in a Rust-side "kind" branch.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoringTerm {
    #[serde(default)]
    pub name: String,
    pub metrics: Vec<String>,
    pub reference: Vec<f64>,
    #[serde(default = "unit_delta")]
    pub power: f64,
    pub coefficient: f64,
    #[serde(default = "default_scoring_aggregate")]
    pub aggregate: ScoringAggregate,
}

fn default_scoring_aggregate() -> ScoringAggregate {
    ScoringAggregate::Scalar
}

/// A competition/mission scoring formula expressed entirely as data: a base
/// score plus a list of weighted terms over named flight metrics. Adding a
/// new competition's scoring rule never requires a Rust code change -- only a
/// new mission JSON `scoring` block.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoringTable {
    #[serde(default = "default_base_score")]
    pub base_score: f64,
    #[serde(default)]
    pub terms: Vec<ScoringTerm>,
}

fn default_base_score() -> f64 {
    0.0
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AstEvalBatchOutput {
    pub results: Vec<AstEvalResult>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionProfile {
    SuperSpeed,
    Balanced,
    AuthorityHeavy,
}

impl AstEvalBatch {
    pub fn resolved_target_apogee_m(&self) -> f64 {
        self.objectives
            .iter()
            .find_map(|objective| {
                let metric = objective.metric.to_ascii_lowercase();
                if !matches!(
                    metric.as_str(),
                    "apogee" | "apogee_m" | "max_altitude" | "altitude"
                ) {
                    return None;
                }
                objective
                    .value
                    .or(objective.target)
                    .or(objective.min_value)
                    .or(objective.scale)
            })
            .unwrap_or(self.target_apogee_m)
    }

    pub fn resolved_physics_mode(&self) -> Result<PhysicsMode, String> {
        match self.physics_mode.as_deref().unwrap_or("openrocket") {
            "openrocket" | "or" | "openrocket-legacy" => Ok(PhysicsMode::OpenRocketLegacy),
            "hyperreal" | "real" => Ok(PhysicsMode::HyperReal),
            other => Err(format!("unknown physics_mode '{other}'")),
        }
    }

    pub fn resolved_execution_profile(&self) -> Result<ExecutionProfile, String> {
        match self
            .execution_profile
            .as_deref()
            .unwrap_or("authority-heavy")
        {
            "super-speed" | "superspeed" | "fast" => Ok(ExecutionProfile::SuperSpeed),
            "balanced" => Ok(ExecutionProfile::Balanced),
            "authority-heavy" | "authority" | "strict" => Ok(ExecutionProfile::AuthorityHeavy),
            other => Err(format!("unknown execution_profile '{other}'")),
        }
    }

    pub fn resolved_phase_machs(&self) -> Vec<f64> {
        if self.phase_machs.is_empty() {
            vec![0.3]
        } else {
            self.phase_machs
                .iter()
                .copied()
                .filter(|mach| mach.is_finite() && *mach >= 0.0)
                .collect()
        }
    }
}

#[derive(Default)]
struct PendingStage {
    name: String,
    nosecone: Option<NoseconeGeometry>,
    bodytubes: Vec<BodyTubeGeometry>,
    finsets: Vec<FinsetGeometry>,
    point_masses: Vec<PointMassGeometry>,
    motor_mount: Option<MotorMountGeometry>,
    auxiliary_motor_mounts: Vec<MotorMountGeometry>,
    radial_assemblies: Vec<RadialAssemblyGeometry>,
    parachute: Option<ParachuteGeometry>,
    body_open: bool,
    cursor_m: f64,
}

fn default_target_apogee() -> f64 {
    15_000.0
}

fn unit_delta() -> f64 {
    1.0
}

pub fn ast_to_geometry(nodes: &[AstNode]) -> Result<RocketGeometry, L2EngineError> {
    let mut stages = Vec::new();
    let mut current: Option<PendingStage> = None;

    for node in nodes {
        match node.node_type.as_str() {
            "STAGE" => {
                if let Some(stage) = current.take() {
                    stages.push(finalize_stage(stage)?);
                }
                current = Some(PendingStage {
                    name: string_param(&node.params, "name")
                        .unwrap_or_else(|| "Evolved Stage".to_string()),
                    ..PendingStage::default()
                });
            }
            "NOSE_CONE" => {
                let stage = current_stage_mut(&mut current)?;
                let length = f64_param(&node.params, "length", 0.3);
                let material_density =
                    material_density_checked(string_param(&node.params, "material").as_deref())?;
                let shape = nose_shape(string_param(&node.params, "shape").as_deref());
                // ASTCompiler omits <shapeparameter>, so OpenRocket applies
                // the selected shape's own default. Mirror those defaults in
                // the direct Rust AST path instead of forcing every shape to
                // 0.0 (notably, OGIVE defaults to tangent-ogive parameter 1).
                let shape_parameter = match shape {
                    NoseShape::Ogive | NoseShape::Parabolic => 1.0,
                    NoseShape::PowerSeries => 0.5,
                    _ => 0.0,
                };
                stage.nosecone = Some(NoseconeGeometry {
                    shape,
                    shape_parameter,
                    length,
                    aft_radius: 0.0,
                    thickness: f64_param(&node.params, "thickness", 0.002),
                    material_density,
                    finish: SurfaceFinish::Polished,
                    axial_offset_m: stage.cursor_m,
                    ballast_mass: 0.0,
                });
                stage.cursor_m += length;
            }
            "BODY_TUBE" => {
                let stage = current_stage_mut(&mut current)?;
                if stage.body_open {
                    return Err(parse_error("nested BODY_TUBE is not supported"));
                }
                let radius = f64_param(&node.params, "radius", 0.04);
                if radius <= 0.0 {
                    return Err(parse_error("BODY_TUBE radius must be positive"));
                }
                if let Some(nose) = stage.nosecone.as_mut() {
                    if nose.aft_radius <= 0.0 {
                        nose.aft_radius = radius;
                    }
                }
                let length = f64_param(&node.params, "length", 0.8);
                stage.bodytubes.push(BodyTubeGeometry {
                    length,
                    radius,
                    thickness: f64_param(&node.params, "thickness", 0.002),
                    material_density: material_density_checked(
                        string_param(&node.params, "material").as_deref(),
                    )?,
                    finish: SurfaceFinish::Polished,
                    axial_offset_m: stage.cursor_m,
                });
                stage.cursor_m += length;
                stage.body_open = true;
            }
            "CLOSE_BODY" => {
                let stage = current_stage_mut(&mut current)?;
                if !stage.body_open {
                    return Err(parse_error("CLOSE_BODY without open BODY_TUBE"));
                }
                stage.body_open = false;
            }
            "POD" | "STRAP_ON" => {
                let stage = current_stage_mut(&mut current)?;
                if stage.body_open {
                    return Err(parse_error(
                        "POD/STRAP_ON must be a sibling of the core BODY_TUBE, after CLOSE_BODY",
                    ));
                }
                let core_radius = stage
                    .bodytubes
                    .iter()
                    .map(|tube| tube.radius)
                    .fold(0.0_f64, f64::max);
                let (assembly, mounts) = parse_radial_assembly(node, core_radius)?;
                for mount in mounts {
                    if stage.motor_mount.is_none() {
                        stage.motor_mount = Some(mount);
                    } else {
                        stage.auxiliary_motor_mounts.push(mount);
                    }
                }
                stage.radial_assemblies.push(assembly);
            }
            "MOTOR_MOUNT" => {
                let stage = current_stage_mut(&mut current)?;
                let designation = motor_designation(&node.params)?;
                let mount = MotorMountGeometry {
                    role: string_param(&node.params, "role").unwrap_or_else(|| "main".to_string()),
                    multiplicity: f64_param(&node.params, "multiplicity", 1.0)
                        .round()
                        .max(1.0) as u32,
                    radial_offset_m: f64_param(&node.params, "radial_offset_m", 0.0),
                    radial_angle_rad: f64_param(&node.params, "radial_angle_deg", 0.0).to_radians(),
                    instance_angle_step_rad: f64_param(
                        &node.params,
                        "instance_angle_step_deg",
                        0.0,
                    )
                    .to_radians(),
                    host_inner_radius_m: f64_param(&node.params, "host_inner_radius_m", 0.0),
                    host_aft_m: f64_param(&node.params, "host_aft_m", 0.0),
                    ignition_event: string_param(&node.params, "ignition")
                        .unwrap_or_else(|| "automatic".to_string()),
                    ignition_delay: f64_param(&node.params, "ignition_delay", 0.0),
                    motor_designation: designation,
                    motor_overhang_m: f64_param(&node.params, "overhang", 0.005),
                    mount_length_m: f64_param(&node.params, "mount_length_m", 0.0),
                    mount_outer_radius_m: f64_param(
                        &node.params,
                        "mount_outer_radius_m",
                        0.0,
                    ),
                    mount_thickness_m: f64_param(
                        &node.params,
                        "mount_thickness_m",
                        0.001,
                    ),
                    mount_material_density: f64_param(
                        &node.params,
                        "mount_material_density",
                        700.0,
                    ),
                    mount_axial_offset_m: 0.0,
                    ejection_charge_delay: f64_param(&node.params, "delay", 0.0),
                };
                if stage.motor_mount.is_none() {
                    stage.motor_mount = Some(mount);
                } else {
                    stage.auxiliary_motor_mounts.push(mount);
                }
            }
            "FIN_SET" => {
                let stage = current_stage_mut(&mut current)?;
                let root = f64_param(&node.params, "root", 0.1);
                let height = f64_param(&node.params, "height", 0.05);
                let sweep = f64_param(&node.params, "sweep", 30.0).to_radians();
                let tip = f64_param(&node.params, "tip", root * 0.3);
                let sweep_offset = height * sweep.tan();
                let count = f64_param(&node.params, "count", 4.0).round().max(1.0) as u32;
                let body = stage.bodytubes.last();
                let parent_offset = body
                    .map(|bt| bt.axial_offset_m + bt.length)
                    .unwrap_or(stage.cursor_m);
                let axial_offset_m = node
                    .params
                    .get("position_from_top_m")
                    .and_then(Value::as_f64)
                    .and_then(|offset| {
                        body.map(|bt| bt.axial_offset_m + offset.clamp(0.0, bt.length))
                    })
                    .unwrap_or_else(|| (parent_offset - root).max(0.0));
                stage.finsets.push(FinsetGeometry {
                    fin_count: count,
                    points: vec![
                        (0.0, 0.0),
                        (sweep_offset, height),
                        (sweep_offset + tip, height),
                        (root, 0.0),
                    ],
                    thickness: f64_param(&node.params, "thickness", 0.003),
                    cross_section: string_param(&node.params, "cross_section")
                        .unwrap_or_else(|| "airfoil".to_string())
                        .to_ascii_lowercase(),
                    material_density: material_density_checked(
                        string_param(&node.params, "material").as_deref(),
                    )?,
                    finish: SurfaceFinish::Polished,
                    cant_rad: 0.0,
                    axial_offset_m,
                });
            }
            "PARACHUTE" => {
                let stage = current_stage_mut(&mut current)?;
                let diameter = f64_param(&node.params, "diameter", 0.5);
                let parent_mid = stage
                    .bodytubes
                    .last()
                    .map(|bt| bt.axial_offset_m + bt.length * 0.5)
                    .unwrap_or(stage.cursor_m);
                stage.parachute = Some(ParachuteGeometry {
                    diameter,
                    cd: f64_param(&node.params, "cd", 1.5),
                    deploy_delay: f64_param(&node.params, "delay", 0.0),
                    packed_mass_kg: parachute_component_mass_kg(
                        diameter,
                        0.067,
                        6.0,
                        diameter * 1.1,
                        0.0018,
                    ),
                    axial_offset_m: parent_mid,
                });
            }
            "PAYLOAD" => {
                let stage = current_stage_mut(&mut current)?;
                let parent_top = stage
                    .bodytubes
                    .last()
                    .map(|bt| bt.axial_offset_m)
                    .unwrap_or(stage.cursor_m);
                stage.point_masses.push(PointMassGeometry {
                    mass_kg: f64_param(&node.params, "mass", 0.0).max(0.0),
                    axial_offset_m: parent_top + 0.05,
                    radial_y_m: 0.0,
                    radial_z_m: 0.0,
                });
            }
            "BALLAST" => {
                let stage = current_stage_mut(&mut current)?;
                let mass_kg = f64_param(&node.params, "mass", 0.0).max(0.0);
                if mass_kg <= 0.0 {
                    return Err(parse_error("BALLAST requires a positive 'mass' param"));
                }
                // Density is validated for physical plausibility (the mission's
                // allowed material range) but PointMassGeometry has no volume
                // field, so it does not otherwise affect mass/CG math.
                material_density_checked(string_param(&node.params, "material").as_deref())?;
                let default_offset_m = match string_param(&node.params, "position").as_deref() {
                    Some("aft") => stage
                        .bodytubes
                        .last()
                        .map(|bt| bt.axial_offset_m + bt.length - 0.05)
                        .unwrap_or(stage.cursor_m),
                    _ => {
                        stage
                            .bodytubes
                            .last()
                            .map(|bt| bt.axial_offset_m)
                            .unwrap_or(stage.cursor_m)
                            + 0.05
                    }
                };
                let count = f64_param(&node.params, "instance_count", 1.0)
                    .round()
                    .max(1.0) as u32;
                let radial_offset_m = f64_param(&node.params, "radial_offset_m", 0.0);
                let angle_offset_rad =
                    f64_param(&node.params, "angle_offset_deg", 0.0).to_radians();
                let axial_offset_m =
                    f64_param(&node.params, "axial_offset_m", default_offset_m);
                for index in 0..count {
                    let angle = angle_offset_rad
                        + std::f64::consts::TAU * index as f64 / count as f64;
                    stage.point_masses.push(PointMassGeometry {
                        mass_kg: mass_kg / count as f64,
                        axial_offset_m,
                        radial_y_m: radial_offset_m * angle.cos(),
                        radial_z_m: radial_offset_m * angle.sin(),
                    });
                }
            }
            other => {
                return Err(parse_error(&format!("unsupported AST node type '{other}'")));
            }
        }
    }

    if let Some(stage) = current.take() {
        stages.push(finalize_stage(stage)?);
    }
    if stages.is_empty() {
        return Err(parse_error("AST must contain at least one STAGE"));
    }

    let mut offset = 0.0;
    for stage in &mut stages {
        stage.axial_offset_m = offset;
        offset += stage_length(stage);
    }
    stages.reverse();

    Ok(RocketGeometry { stages })
}

pub fn evaluate_ast(
    id: &str,
    nodes: &[AstNode],
    curves_by_designation: &HashMap<String, ThrustCurve>,
    objectives: &[AstObjective],
    constraints: &Value,
    physics_mode: PhysicsMode,
    signature: &str,
    calibrations: &HashMap<String, AstCalibration>,
    environment: Option<OrkSimulationEnvironment>,
) -> AstEvalResult {
    evaluate_ast_with_profile(
        id,
        nodes,
        curves_by_designation,
        objectives,
        constraints,
        physics_mode,
        ExecutionProfile::AuthorityHeavy,
        signature,
        calibrations,
        None,
        environment,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn evaluate_ast_with_profile(
    id: &str,
    nodes: &[AstNode],
    curves_by_designation: &HashMap<String, ThrustCurve>,
    objectives: &[AstObjective],
    constraints: &Value,
    physics_mode: PhysicsMode,
    execution_profile: ExecutionProfile,
    signature: &str,
    calibrations: &HashMap<String, AstCalibration>,
    divergence_model: Option<&DivergenceModel>,
    environment: Option<OrkSimulationEnvironment>,
) -> AstEvalResult {
    match evaluate_ast_inner(
        nodes,
        curves_by_designation,
        objectives,
        constraints,
        physics_mode,
        execution_profile,
        signature,
        calibrations,
        divergence_model,
        environment,
    ) {
        Ok((score, summary, margins, features)) => AstEvalResult {
            id: id.to_string(),
            status: "success".to_string(),
            score,
            apogee_m: summary.apogee_m.max(0.0),
            apogee_east_m: summary.apogee_east_m,
            apogee_north_m: summary.apogee_north_m,
            stage_landings: summary.stage_landings,
            total_prop_mass_kg: summary.total_prop_mass_kg,
            mach: summary.max_mach.max(0.0),
            min_static_margin: margins.iter().copied().fold(f64::INFINITY, f64::min),
            margins,
            features: features.0.to_vec(),
            reason: "ok".to_string(),
        },
        Err(failure) => {
            // Constraint-violation errors carry an embedded closeness-to-
            // passing ratio (see `violation()`/CLOSENESS_SEPARATOR) so a
            // failed candidate can still be ranked against other failed
            // candidates during selection. Errors from earlier stages
            // (malformed AST, missing motor curves, structural build
            // failures) carry no such ratio and fall back to 0.0 -- those
            // aren't "almost legal", they're unbuildable.
            let (reason, closeness) = match failure.reason.split_once(CLOSENESS_SEPARATOR) {
                Some((reason, ratio)) => (reason.to_string(), ratio.parse::<f64>().unwrap_or(0.0)),
                None => (failure.reason, 0.0),
            };
            match failure.telemetry {
                // Constraint failed AFTER a real flight was simulated
                // (enforce_hard_constraints) -- preserve what actually
                // happened instead of reporting a blank/zeroed flight.
                Some((summary, margins, features)) => AstEvalResult {
                    id: id.to_string(),
                    status: "failed".to_string(),
                    score: closeness,
                    apogee_m: summary.apogee_m.max(0.0),
                    apogee_east_m: summary.apogee_east_m,
                    apogee_north_m: summary.apogee_north_m,
                    stage_landings: summary.stage_landings,
                    total_prop_mass_kg: summary.total_prop_mass_kg,
                    mach: summary.max_mach.max(0.0),
                    min_static_margin: margins.iter().copied().fold(f64::INFINITY, f64::min),
                    margins,
                    features: features.0.to_vec(),
                    reason,
                },
                // No flight was ever simulated (unbuildable geometry,
                // missing motor curve, motor-mount collision, inadequate
                // TWR when opted in) -- nothing to report.
                None => AstEvalResult {
                    id: id.to_string(),
                    status: "failed".to_string(),
                    score: closeness,
                    apogee_m: 0.0,
                    apogee_east_m: 0.0,
                    apogee_north_m: 0.0,
                    stage_landings: Vec::new(),
                    total_prop_mass_kg: 0.0,
                    mach: 0.0,
                    // JSON has no Infinity/NaN representation -- serde_json
                    // silently emits `null` for f64::NEG_INFINITY, which
                    // crashes any downstream parser (e.g. organic_loop.py)
                    // that does an unconditional
                    // float(item["min_static_margin"]). A large-but-finite
                    // sentinel keeps the "this candidate is worthless" sort
                    // order without being JSON-unsafe.
                    min_static_margin: -1.0e9,
                    margins: Vec::new(),
                    features: Vec::new(),
                    reason,
                },
            }
        }
    }
}

/// Error from `evaluate_ast_inner`. Most failure sites (motor curve lookup,
/// geometry build, motor-mount clearance, TWR adequacy) happen before a
/// flight is ever simulated -- there's no telemetry to attach, they're
/// "unbuildable", not "almost legal". Constraint violations checked AFTER
/// simulation (enforce_hard_constraints: max_height_m, max_mach,
/// min_static_margin when opted in, not_all_stages_landed,
/// max_touchdown_speed_ms) DO have a real, already-computed FlightSummary at
/// that point -- discarding it (as this used to do unconditionally) meant a
/// candidate that flew a real trajectory and landed 1 of 2 stages reported
/// apogee_m=0.0 and stage_landings=[] downstream, identical to a candidate
/// that never simulated at all. `telemetry` preserves that real data when
/// it exists so monitoring/scoring code (organic_loop.py's
/// official_score_breakdown, CKG) can see what actually happened instead of
/// an artificially blanked-out failure.
struct EvalFailure {
    reason: String,
    telemetry: Option<(
        FlightSummary,
        Vec<f64>,
        crate::divergence::DivergenceFeatures,
    )>,
}

impl From<String> for EvalFailure {
    fn from(reason: String) -> Self {
        EvalFailure {
            reason,
            telemetry: None,
        }
    }
}

fn evaluate_ast_inner(
    nodes: &[AstNode],
    curves_by_designation: &HashMap<String, ThrustCurve>,
    objectives: &[AstObjective],
    constraints: &Value,
    physics_mode: PhysicsMode,
    execution_profile: ExecutionProfile,
    signature: &str,
    calibrations: &HashMap<String, AstCalibration>,
    divergence_model: Option<&DivergenceModel>,
    environment: Option<OrkSimulationEnvironment>,
) -> Result<
    (
        f64,
        FlightSummary,
        Vec<f64>,
        crate::divergence::DivergenceFeatures,
    ),
    EvalFailure,
> {
    let mut geometry = ast_to_geometry(nodes).map_err(|e| format!("{e:?}"))?;
    let mut motor_clusters: Vec<Vec<ThrustCurve>> = geometry
        .stages
        .iter()
        .map(|stage| {
            std::iter::once(&stage.motor_mount)
                .chain(stage.auxiliary_motor_mounts.iter())
                .flat_map(|mount| std::iter::repeat(mount).take(mount.multiplicity as usize))
                .map(|mount| {
                    curves_by_designation
                        .get(&mount.motor_designation)
                        .cloned()
                        .ok_or_else(|| format!("missing_motor_curve:{}", mount.motor_designation))
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .collect::<Result<Vec<_>, _>>()?;
    enrich_ast_motor_mounts_multi(&mut geometry, &motor_clusters);
    enforce_motor_mount_clearance(&geometry)?;
    let ascent_screen = constraints
        .get("simulation_phase")
        .and_then(Value::as_str)
        .is_some_and(|phase| phase.eq_ignore_ascii_case("ascent"));
    if ascent_screen {
        prepare_ascent_screen(&mut geometry, &mut motor_clusters)?;
    }
    let curves: Vec<ThrustCurve> = motor_clusters
        .iter()
        .map(|cluster| crate::mission_adapter::aggregate_motor_curves(cluster))
        .collect::<Result<_, _>>()
        .map_err(|e: L2EngineError| format!("{e:?}"))?;

    // Fidelity profiles alter cadence/table density, never the requested
    // aerodynamic model. This keeps coarse screens rank-compatible with
    // promoted OpenRocket-mode candidates.
    let effective_physics_mode = physics_mode;
    let phase_machs = phase_machs_from_constraints(constraints);
    let mut margins = crate::builder::static_margins_with_motor_clusters_at_machs(
        &geometry,
        &motor_clusters,
        effective_physics_mode,
        &phase_machs,
    );
    let mut mission = match execution_profile {
        ExecutionProfile::SuperSpeed => {
            crate::mission_adapter::build_mission_with_motor_clusters_fast(
                &geometry,
                &motor_clusters,
                effective_physics_mode,
            )
        }
        ExecutionProfile::Balanced | ExecutionProfile::AuthorityHeavy => {
            crate::mission_adapter::build_mission_with_motor_clusters(
                &geometry,
                &motor_clusters,
                effective_physics_mode,
            )
        }
    }
    .map_err(|e| format!("{e:?}"))?;

    if let Some(path) = constraints.get("wind_csv_path").and_then(|v| v.as_str()) {
        if let Ok(profile) = crate::sim_core::wind::WindProfile::from_csv(path) {
            mission.wind_profile = Some(profile);
        }
    }
    if effective_physics_mode == PhysicsMode::OpenRocketLegacy {
        if let Some(environment) = environment {
            apply_openrocket_environment(&mut mission, environment);
        }
    }
    enforce_motor_adequacy(&mission, constraints)?;
    let config = SimConfig {
        dt: match execution_profile {
            ExecutionProfile::SuperSpeed => 0.025,
            ExecutionProfile::Balanced => 0.02,
            ExecutionProfile::AuthorityHeavy => 0.005,
        },
        // 1200s, not 600s -- confirmed against OSIFOG's own reference
        // screenshot (OSIFOG_Nivel3_ProjetoFalcon.pdf p.12, "Maximum
        // simulation time: 1200 s" field in the OpenRocket Simulation
        // Options dialog). The Rust proxy is meant to rank-predict what
        // real OpenRocket (the competition authority) would do; running at
        // half the real time budget could reject a stage as
        // not_all_stages_landed simply because the proxy gave up 600s
        // early, not because the design is actually bad.
        max_time: 1200.0,
    };
    let mut controller = NoOpController;
    let mut summary = match execution_profile {
        ExecutionProfile::AuthorityHeavy => crate::sim_core::sim::simulate_summary_with_mode(
            &mission,
            &config,
            &mut controller,
            effective_physics_mode,
            ascent_screen,
        )?,
        ExecutionProfile::Balanced => crate::sim_core::sim::simulate_summary_with_mode(
            &mission,
            &config,
            &mut controller,
            // Balanced keeps the OpenRocket-calibrated mission coefficients
            // but uses the fixed 20 ms stepping policy.  Physics identity and
            // execution cadence are deliberately orthogonal.
            PhysicsMode::HyperReal,
            ascent_screen,
        )?,
        // The fast screen remains full 6-DOF so radial thrust imbalance,
        // torque, wind and attitude instability can reject candidates. Its
        // speed comes from a coarser fixed step, not a different physics
        // model; promoted candidates are re-run at finer cadence.
        ExecutionProfile::SuperSpeed => crate::sim_core::sim::simulate_summary_with_mode_gated(
            &mission,
            &config,
            &mut controller,
            PhysicsMode::HyperReal,
            ascent_screen,
            if ascent_screen {
                None
            } else {
                constraints.get("max_mach").and_then(Value::as_f64)
            },
        )?,
    };
    if execution_profile != ExecutionProfile::SuperSpeed && divergence_model.is_none() {
        if let Some(calibration) = calibrations.get(signature).copied() {
            summary.apogee_m *= calibration.apogee_delta();
            summary.max_mach *= calibration.mach_delta();
            for margin in &mut margins {
                *margin *= calibration.margin_delta();
            }
        }
    }
    let features = extract_features(&geometry, &curves, &summary);
    if let Some(model) = divergence_model {
        let prediction = model
            .predict(&features)
            .map_err(|error| format!("divergence_model:{error:?}"))?;
        summary.apogee_m += prediction.apogee_correction_m * prediction.confidence;
        summary.max_mach += prediction.mach_correction * prediction.confidence;
    }
    let total_height_m: f64 = geometry.stages.iter().map(stage_length).sum();
    if let Err(reason) =
        enforce_hard_constraints(&summary, &margins, constraints, &mission, total_height_m)
    {
        return Err(EvalFailure {
            reason,
            telemetry: Some((summary, margins, features)),
        });
    }
    let score = score_summary(
        &summary,
        &margins,
        objectives,
        constraints,
        &geometry,
        &curves,
    );
    Ok((score, summary, margins, features))
}

/// Project a multi-role motor cluster onto the powered-ascent problem.
///
/// Delayed descent/retro motors remain physically installed as wet point
/// masses, including their motor-mount tube mass and axial location, but are
/// removed from the active thrust cluster. This lets a primary cluster burn
/// out and separate normally without pretending that an unignited landing
/// motor's propellant must be consumed first. OpenRocket remains authoritative
/// for the later branch-specific descent and retro burns.
fn prepare_ascent_screen(
    geometry: &mut RocketGeometry,
    motor_clusters: &mut [Vec<ThrustCurve>],
) -> Result<(), String> {
    for (stage, curves) in geometry.stages.iter_mut().zip(motor_clusters.iter_mut()) {
        let mounts = std::iter::once(stage.motor_mount.clone())
            .chain(stage.auxiliary_motor_mounts.clone())
            .collect::<Vec<_>>();
        let mut curve_cursor = 0usize;
        let mut active_mounts = Vec::new();
        let mut active_curves = Vec::new();

        for mount in mounts {
            let count = mount.multiplicity as usize;
            let end = curve_cursor + count;
            let mount_curves = curves.get(curve_cursor..end).ok_or_else(|| {
                format!(
                    "ascent_screen:motor/mount multiplicity mismatch in stage '{}'",
                    stage.name
                )
            })?;
            curve_cursor = end;
            let inert_role = matches!(
                mount.role.to_ascii_lowercase().as_str(),
                "retro" | "landing" | "descent"
            );

            if inert_role {
                let body_aft_m = stage
                    .bodytubes
                    .first()
                    .map(|tube| tube.axial_offset_m + tube.length)
                    .unwrap_or(0.0);
                for (index, curve) in mount_curves.iter().enumerate() {
                    let angle = mount.radial_angle_rad
                        + mount.instance_angle_step_rad * index as f64;
                    stage.point_masses.push(PointMassGeometry {
                        mass_kg: curve.total_mass_kg,
                        axial_offset_m: body_aft_m - curve.length_m * 0.5 + mount.motor_overhang_m,
                        radial_y_m: mount.radial_offset_m * angle.cos(),
                        radial_z_m: mount.radial_offset_m * angle.sin(),
                    });
                }
                let inner_radius = (mount.mount_outer_radius_m - mount.mount_thickness_m).max(0.0);
                let single_mount_mass = mount.mount_material_density
                    * std::f64::consts::PI
                    * (mount.mount_outer_radius_m.powi(2) - inner_radius.powi(2))
                    * mount.mount_length_m;
                if single_mount_mass > 0.0 {
                    for index in 0..mount.multiplicity {
                        let angle = mount.radial_angle_rad
                            + mount.instance_angle_step_rad * index as f64;
                        stage.point_masses.push(PointMassGeometry {
                            mass_kg: single_mount_mass,
                            axial_offset_m: mount.mount_axial_offset_m
                                + mount.mount_length_m * 0.5,
                            radial_y_m: mount.radial_offset_m * angle.cos(),
                            radial_z_m: mount.radial_offset_m * angle.sin(),
                        });
                    }
                }
            } else {
                active_curves.extend(mount_curves.iter().cloned());
                active_mounts.push(mount);
            }
        }

        if curve_cursor != curves.len() {
            return Err(format!(
                "ascent_screen:unused motor curves in stage '{}'",
                stage.name
            ));
        }
        if active_mounts.is_empty() {
            return Err(format!(
                "ascent_screen:no ascent motor in stage '{}'",
                stage.name
            ));
        }
        stage.motor_mount = active_mounts.remove(0);
        stage.auxiliary_motor_mounts = active_mounts;
        *curves = active_curves;
    }
    Ok(())
}

/// Separator between a constraint-violation reason and its embedded
/// closeness-to-passing ratio (see [`CLOSENESS_SEPARATOR`] usage below). A
/// NUL byte can't appear in any of the formatted reason text, so it's safe
/// as a delimiter without needing a real structured error type.
const CLOSENESS_SEPARATOR: char = '\u{0}';

/// A [0,1] ratio (1.0 = just barely failing, near 0.0 = far from passing)
/// embedded in constraint-violation errors so illegal candidates can still
/// be ranked against each other during selection instead of tying at a flat
/// score. This never affects legality -- `enforce_hard_constraints` still
/// unconditionally rejects the candidate; it only gives the GA a gradient to
/// climb while searching for a legal one.
fn violation(reason: String, closeness_ratio: f64) -> String {
    format!(
        "{reason}{CLOSENESS_SEPARATOR}{:.6}",
        closeness_ratio.clamp(0.0, 1.0)
    )
}

/// Rejects any two motor-mount tube instances -- including replicated ring
/// instances of the *same* `MOTOR_MOUNT` when `multiplicity > 1` -- whose
/// physical footprints overlap. This is the one hard-constraint gap that
/// blocked internal shared-body-tube clusters (e.g. an octaweb-style 3 outer
/// motors + 1 central retro, all within one BODY_TUBE, no external pod):
/// before this check, nothing in the engine verified that ring instances (or
/// two different mounts at different radii) don't physically collide -- the
/// only other collision check anywhere is the POD-vs-core overlap inside
/// `parse_radial_assembly`. Must run after `enrich_ast_motor_mounts_multi`
/// so `mount_outer_radius_m` is populated with the real tube radius.
fn enforce_motor_mount_clearance(geometry: &RocketGeometry) -> Result<(), String> {
    let clearance = 0.002;
    for stage in &geometry.stages {
        let mounts: Vec<&MotorMountGeometry> = std::iter::once(&stage.motor_mount)
            .chain(stage.auxiliary_motor_mounts.iter())
            .collect();
        let mut instances: Vec<(f64, f64, f64)> = Vec::new();
        for mount in &mounts {
            for index in 0..mount.multiplicity {
                let angle =
                    mount.radial_angle_rad + mount.instance_angle_step_rad * index as f64;
                let x = mount.radial_offset_m * angle.cos();
                let y = mount.radial_offset_m * angle.sin();
                instances.push((x, y, mount.mount_outer_radius_m));
            }
        }
        for i in 0..instances.len() {
            for j in (i + 1)..instances.len() {
                let (x1, y1, r1) = instances[i];
                let (x2, y2, r2) = instances[j];
                let dist = ((x1 - x2).powi(2) + (y1 - y2).powi(2)).sqrt();
                let needed = r1 + r2 + clearance;
                if dist + 1e-9 < needed {
                    // Unlike max_height_m/min_static_margin/max_mach below
                    // (all wrapped in `violation()`), this check used a bare
                    // `format!` with no embedded closeness ratio -- every
                    // candidate failing here (regardless of whether dist was
                    // 99% of needed or 10% of needed) tied at the same flat
                    // 0.0 score, giving the GA zero gradient to climb.
                    // Confirmed as the actual cause of a live campaign
                    // "looks random, not evolving" symptom: once earlier
                    // fixes made this the dominant blocking constraint
                    // (previously min_static_margin was, which already had
                    // a working ratio), the whole population lost its
                    // gradient signal simultaneously. Same ratio convention
                    // as the checks below: dist/needed is <1.0 while
                    // failing, approaching 1.0 as it approaches passing.
                    return Err(violation(
                        format!(
                            "constraint_violation:motor_mount_collision dist={dist:.6} < needed={needed:.6} in stage '{}'",
                            stage.name
                        ),
                        dist / needed.max(1e-9),
                    ));
                }
            }
        }
    }
    Ok(())
}

fn enforce_hard_constraints(
    summary: &FlightSummary,
    margins: &[f64],
    constraints: &Value,
    mission: &crate::sim_core::vehicle::Mission,
    total_height_m: f64,
) -> Result<(), String> {
    enforce_motor_adequacy(mission, constraints)?;

    if let Some(limit) = constraints.get("max_height_m").and_then(|v| v.as_f64()) {
        if total_height_m > limit {
            return Err(violation(
                format!(
                    "constraint_violation:max_height_m {:.6} > {:.6}",
                    total_height_m, limit
                ),
                limit / total_height_m.max(1e-9),
            ));
        }
    }

    let max_mach = constraints
        .get("max_mach")
        .or_else(|| constraints.get("mach_max"))
        .or_else(|| constraints.get("max_speed_mach"))
        .and_then(|v| v.as_f64());
    if let Some(limit) = max_mach {
        if summary.max_mach > limit {
            return Err(violation(
                format!(
                    "constraint_violation:max_mach {:.6} > {:.6}",
                    summary.max_mach, limit
                ),
                limit / summary.max_mach.max(1e-9),
            ));
        }
    }

    // NOT a numeric legality gate by default. OSIFOG_Nivel3_ProjetoFalcon.pdf
    // sec. 2 item 3 ("Manter apenas estabilidade estatica ao longo de toda a
    // trajetoria de ascensao") is a CONTROL-METHOD requirement, read against
    // item 2's contrast ("... sem estabilidade dinamica ativa, sem correcao
    // de angulo dos motores ou das asas ou regulagem de potencia..."): the
    // rocket must reach apogee using ONLY passive/static aerodynamic means,
    // never active guidance (TVC, fin actuation, throttling). This engine
    // never models active guidance at all -- the requirement is satisfied by
    // construction, not by a runtime margin check. It is NOT "margin must
    // stay above some number at every instant"; a marginally or even
    // momentarily negative-margin candidate that still reaches ~3000m under
    // the real simulated (wind-perturbed) trajectory is legal exactly as
    // OSIFOG intends -- the real apogee-accuracy scoring term
    // (-3000*(apogee-3000)^2) already punishes any design too unstable to
    // get there, with no separate gate needed (same reasoning already
    // applied to removing the min_thrust_to_weight gate above). Kept as an
    // OPT-IN check only, mirroring min_thrust_to_weight: a mission may still
    // set an explicit "min_static_margin" for its own extra-safety design
    // margin, but missions/osifog_l3_precision.json deliberately does not.
    if let Some(required_margin) = constraints.get("min_static_margin").and_then(|v| v.as_f64()) {
        if !margins.is_empty() {
            let min_margin = margins.iter().copied().fold(f64::INFINITY, f64::min);
            if !min_margin.is_finite() || min_margin < required_margin {
                // Smooth ratio that stays gradient-bearing even for negative
                // margins (unlike `min_margin.max(0.0)/required.max(1e-9)`,
                // which flattens every unstable candidate to the same
                // ratio=0.0 -- the flat-gradient bug already fixed for
                // motor_mount_collision/motor_adequacy earlier this
                // campaign).
                let deficit = if min_margin.is_finite() {
                    (required_margin - min_margin).max(0.0)
                } else {
                    f64::INFINITY
                };
                let ratio = if deficit.is_finite() {
                    1.0 / (1.0 + deficit)
                } else {
                    0.0
                };
                return Err(violation(
                    format!(
                        "constraint_violation:min_static_margin {:.6} < {:.6}",
                        min_margin, required_margin
                    ),
                    ratio,
                ));
            }
        }
    }

    if constraints
        .get("require_all_stages_land")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        if summary.stage_landings.len() < mission.stages.len() {
            return Err(violation(
                format!(
                    "constraint_violation:not_all_stages_landed {} < {}",
                    summary.stage_landings.len(),
                    mission.stages.len()
                ),
                summary.stage_landings.len() as f64 / mission.stages.len().max(1) as f64,
            ));
        }
    }

    if let Some(max_td_speed) = constraints
        .get("max_touchdown_speed_ms")
        .and_then(|v| v.as_f64())
    {
        for landing in &summary.stage_landings {
            if landing.total_speed_ms > max_td_speed {
                return Err(violation(
                    format!(
                        "constraint_violation:max_touchdown_speed_ms {:.6} > {:.6}",
                        landing.total_speed_ms, max_td_speed
                    ),
                    max_td_speed / landing.total_speed_ms.max(1e-9),
                ));
            }
        }
    }

    Ok(())
}

fn enforce_motor_adequacy(
    mission: &crate::sim_core::vehicle::Mission,
    constraints: &Value,
) -> Result<(), String> {
    // min_thrust_to_weight is NOT an OSIFOG rule -- confirmed absent from
    // both organizer PDFs (OSIFOG_Nivel3_ProjetoFalcon.pdf sec. 2 lists
    // exactly 6 real restrictions: <5m/s all-stage landing with no passive
    // recovery, retro-only braking with no active correction, static
    // stability during ascent, multi-stage, real OpenWind atmosphere,
    // OpenEarth trajectory capture; OSIFOG_Missao_Secreta_2026.pdf's 15-item
    // checklist adds height/rod-length/material/dimension/physics-realism
    // rules but nothing about thrust-to-weight). A rocket that can't
    // actually lift off just scores catastrophically on the real apogee
    // term (-3000*(apogee-3000)^2) -- no separate legality gate is needed
    // or wanted. Previously defaulted to 1.5 (then missions/
    // osifog_l3_precision.json set 1.2 explicitly) whenever the key was
    // omitted, silently re-imposing a self-imposed convention as a hard
    // legality wall; now skipped entirely unless a mission opts in
    // explicitly for its own reasons.
    let minimum_twr = match constraints
        .get("min_thrust_to_weight")
        .and_then(|value| value.as_f64())
    {
        Some(value) => value,
        None => return Ok(()),
    };
    let peak_launch_thrust = mission
        .stages
        .first()
        .map(|stage| {
            stage
                .motors
                .iter()
                .filter(|motor| motor.ignition_delay <= 0.0)
                .map(|m| {
                    m.thrust_curve
                        .iter()
                        .map(|(_, thrust)| *thrust)
                        .fold(0.0_f64, f64::max)
                })
                .sum()
        })
        .unwrap_or(0.0);
    let launch_weight = mission.total_mass() * 9.80665;
    let launch_twr = if launch_weight > 0.0 {
        peak_launch_thrust / launch_weight
    } else {
        0.0
    };
    if !launch_twr.is_finite() || launch_twr < minimum_twr {
        // Same flat-zero-gradient bug as motor_mount_collision above: this
        // is directly the "motor too weak to lift this much structure"
        // constraint, exactly the axis a user would expect the GA to trade
        // off (lighter materials vs bigger motor) -- with no closeness
        // ratio, every underpowered candidate tied at the same score
        // regardless of whether it was 95% or 5% of the way to enough
        // thrust, removing the gradient that trade-off search depends on.
        let ratio = if launch_twr.is_finite() {
            launch_twr / minimum_twr.max(1e-9)
        } else {
            0.0
        };
        return Err(violation(
            format!(
                "constraint_violation:min_thrust_to_weight {:.6} < {:.6}",
                launch_twr, minimum_twr
            ),
            ratio,
        ));
    }

    Ok(())
}

fn phase_machs_from_constraints(constraints: &Value) -> Vec<f64> {
    constraints
        .get("phase_machs")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_f64())
                .filter(|mach| mach.is_finite() && *mach >= 0.0)
                .collect::<Vec<_>>()
        })
        .filter(|items| !items.is_empty())
        .unwrap_or_else(|| vec![0.3])
}

/// Populate the physical motor-mount tube geometry derived from each selected
/// motor. Both scoring and diagnostic entry points must call this after AST
/// compilation so they simulate the same vehicle mass and CG.
pub fn enrich_ast_motor_mounts(geometry: &mut RocketGeometry, curves: &[ThrustCurve]) {
    for (stage, curve) in geometry.stages.iter_mut().zip(curves.iter()) {
        let mount_length_m = stage.motor_mount.mount_length_m.max(curve.length_m + 0.02);
        stage.motor_mount.mount_length_m = mount_length_m;
        stage.motor_mount.mount_outer_radius_m = stage
            .motor_mount
            .mount_outer_radius_m
            .max(curve.diameter_m / 2.0 + stage.motor_mount.mount_thickness_m);
        stage.motor_mount.mount_axial_offset_m = stage
            .bodytubes
            .first()
            .map(|bt| bt.axial_offset_m + bt.length - mount_length_m)
            .unwrap_or(0.0);
    }
}

pub fn enrich_ast_motor_mounts_multi(geometry: &mut RocketGeometry, clusters: &[Vec<ThrustCurve>]) {
    for (stage, curves) in geometry.stages.iter_mut().zip(clusters) {
        let mut mounts =
            std::iter::once(&mut stage.motor_mount).chain(stage.auxiliary_motor_mounts.iter_mut());
        let mut curve_index = 0usize;
        while let Some(mount) = mounts.next() {
            let Some(curve) = curves.get(curve_index) else {
                break;
            };
            let mount_length_m = mount.mount_length_m.max(curve.length_m + 0.02);
            mount.mount_length_m = mount_length_m;
            mount.mount_outer_radius_m = mount
                .mount_outer_radius_m
                .max(curve.diameter_m / 2.0 + mount.mount_thickness_m);
            let host_aft_m = if mount.host_aft_m > 0.0 {
                mount.host_aft_m
            } else {
                stage
                    .bodytubes
                    .first()
                    .map(|bt| bt.axial_offset_m + bt.length)
                    .unwrap_or(0.0)
            };
            mount.mount_axial_offset_m = host_aft_m - mount_length_m;
            curve_index += mount.multiplicity as usize;
        }
    }
}

/// A named flight metric resolved off `FlightSummary`, either a single scalar
/// or one value per landed stage. Adding a metric here extends what any
/// mission's `ScoringTable` can reference -- it never requires a new formula
/// function or a new competition-specific branch.
enum ScoringMetricValue {
    Scalar(f64),
    PerStage(Vec<f64>),
}

fn resolve_scoring_metric(name: &str, summary: &FlightSummary) -> Option<ScoringMetricValue> {
    use ScoringMetricValue::{PerStage, Scalar};
    Some(match name {
        "apogee_m" => Scalar(summary.apogee_m),
        "apogee_east_m" => Scalar(summary.apogee_east_m),
        "apogee_north_m" => Scalar(summary.apogee_north_m),
        "mach" | "max_mach" => Scalar(summary.max_mach),
        "flight_time" | "flight_time_s" => Scalar(summary.flight_time),
        "total_prop_mass_kg" => Scalar(summary.total_prop_mass_kg),
        "stage_landing_east_m" => {
            PerStage(summary.stage_landings.iter().map(|s| s.east_m).collect())
        }
        "stage_landing_north_m" => {
            PerStage(summary.stage_landings.iter().map(|s| s.north_m).collect())
        }
        "stage_landing_vz_ms" => PerStage(summary.stage_landings.iter().map(|s| s.vz_ms).collect()),
        "stage_landing_vxy_ms" => {
            PerStage(summary.stage_landings.iter().map(|s| s.vxy_ms).collect())
        }
        "stage_landing_total_speed_ms" => PerStage(
            summary
                .stage_landings
                .iter()
                .map(|s| s.total_speed_ms)
                .collect(),
        ),
        _ => return None,
    })
}

fn aggregate_scoring_metric(
    value: &ScoringMetricValue,
    aggregate: ScoringAggregate,
) -> Result<f64, String> {
    match (value, aggregate) {
        (ScoringMetricValue::Scalar(v), ScoringAggregate::Scalar) => Ok(*v),
        (ScoringMetricValue::Scalar(_), _) => {
            Err("aggregate requires a per-stage metric".to_string())
        }
        (ScoringMetricValue::PerStage(_), ScoringAggregate::Scalar) => {
            Err("scalar aggregate requires a scalar metric".to_string())
        }
        (ScoringMetricValue::PerStage(values), _) if values.is_empty() => {
            Err("no stage data to aggregate".to_string())
        }
        (ScoringMetricValue::PerStage(values), ScoringAggregate::MeanOverStages) => {
            Ok(values.iter().sum::<f64>() / values.len() as f64)
        }
        (ScoringMetricValue::PerStage(values), ScoringAggregate::SumOverStages) => {
            Ok(values.iter().sum())
        }
        (ScoringMetricValue::PerStage(values), ScoringAggregate::MaxOverStages) => {
            Ok(values.iter().copied().fold(f64::NEG_INFINITY, f64::max))
        }
    }
}

/// Evaluates a data-driven `ScoringTable` against one flight's telemetry:
/// `base_score + Sum_term( coefficient * Sum_i (metric_i - reference_i)^power )`.
fn evaluate_scoring_table(table: &ScoringTable, summary: &FlightSummary) -> Result<f64, String> {
    let mut score = table.base_score;
    for term in &table.terms {
        if term.metrics.len() != term.reference.len() {
            return Err(format!(
                "scoring term '{}': {} metrics but {} reference values",
                term.name,
                term.metrics.len(),
                term.reference.len()
            ));
        }
        let mut penalty_sum = 0.0;
        for (metric_name, reference) in term.metrics.iter().zip(term.reference.iter()) {
            let value = resolve_scoring_metric(metric_name, summary).ok_or_else(|| {
                format!(
                    "scoring term '{}': unknown metric '{metric_name}'",
                    term.name
                )
            })?;
            let aggregated = aggregate_scoring_metric(&value, term.aggregate)
                .map_err(|e| format!("scoring term '{}': {e}", term.name))?;
            penalty_sum += (aggregated - reference).powf(term.power);
        }
        score += term.coefficient * penalty_sum;
    }
    Ok(score)
}

/// Score sentinel for a scoring-table evaluation failure (unknown metric,
/// missing stage telemetry, malformed term). f64::NEG_INFINITY cannot be used
/// because serde_json silently emits `null` for it, which crashes any
/// downstream parser doing an unconditional float(item["score"]).
const SCORING_FAILURE_SENTINEL: f64 = -1.0e9;

fn score_summary(
    summary: &FlightSummary,
    margins: &[f64],
    objectives: &[AstObjective],
    constraints: &Value,
    geometry: &RocketGeometry,
    curves: &[ThrustCurve],
) -> f64 {
    let total_burn_time_s: f64 = curves
        .iter()
        .map(|curve| {
            let first = curve.time_s.first().copied().unwrap_or(0.0);
            let last = curve.time_s.last().copied().unwrap_or(first);
            (last - first).max(0.0)
        })
        .sum();

    let get_metric = |metric: &str| -> f64 {
        match metric {
            "apogee" | "apogee_m" | "max_altitude" | "altitude" => summary.apogee_m,
            "mach" | "max_mach" => summary.max_mach,
            "burn_time" | "burn_time_s" | "motor_burn_time" | "total_burn_time_s" => {
                total_burn_time_s
            }
            "flight_time" | "flight_time_s" => summary.flight_time,
            "accel" | "max_accel" | "max_accel_g" => summary.max_accel_g,
            "mass" | "liftoff_mass" | "takeoff_mass" => geometry
                .stages
                .iter()
                .map(|stage| crate::mass_calculator::total_mass(stage, 0.0))
                .sum(),
            _ => 0.0,
        }
    };

    let mut score = 0.0;

    if let Some(scoring) = constraints.get("scoring") {
        let table: ScoringTable =
            serde_json::from_value(scoring.clone()).unwrap_or_else(|_| ScoringTable {
                base_score: 0.0,
                terms: Vec::new(),
            });
        // Official OSIFOG formula path: return directly, no fallthrough.
        // The real competition SCORE formula (OSIFOG_Nivel3_ProjetoFalcon.pdf
        // sec. 3) has no static-margin term at all -- legality (a genuinely
        // positive margin, not any specific caliber minimum) is already
        // enforced upstream in enforce_hard_constraints before this function
        // is ever reached, so nothing failing that gate can arrive here.
        // The margin-penalty fallthrough below is legacy evolve.rs-era
        // scoring, kept only for the objectives-list path (no "scoring" key
        // present at all) -- letting it apply here after computing the
        // otherwise-correct formula silently multiplied every "official"
        // score by a self-imposed <1.5-caliber penalty factor even after
        // min_static_margin was removed from the mission's constraints,
        // corrupting exactly the number this pipeline hands back to the
        // user as the real competition score.
        return evaluate_scoring_table(&table, summary).unwrap_or(SCORING_FAILURE_SENTINEL);
    } else if objectives.is_empty() {
        let target = constraints
            .get("target_apogee_m")
            .and_then(|v| v.as_f64())
            .unwrap_or(15_000.0);
        let apogee = summary.apogee_m.max(0.0);
        let target_fit = 1.0 / (1.0 + (apogee - target).abs() / target);
        let min_margin = margins.iter().copied().fold(f64::INFINITY, f64::min);
        let margin_factor = if min_margin.is_finite() {
            (min_margin / 1.5).clamp(0.05, 1.25)
        } else {
            0.05
        };
        return (target_fit * 100.0 + summary.max_mach.max(0.0)) * margin_factor;
    } else {
        for o in objectives {
            let x = get_metric(&o.metric.to_lowercase());
            let w = o.weight.unwrap_or(1.0);
            let v = o.value.or(o.target).unwrap_or(1.0);
            let scale = o.scale.unwrap_or(1.0);

            match o.kind.as_str() {
                "atleast" => {
                    if v > 0.0 {
                        score += w * (x / v).min(1.0);
                    } else {
                        score += w;
                    }
                }
                "atmost" => {
                    let minimum = o.min_value.unwrap_or(0.0);
                    if x.is_finite() && x > 0.0 && x >= minimum {
                        score += w * (v / x).min(1.0);
                    }
                }
                "target" => {
                    if v > 0.0 {
                        score += w * (1.0 - (x - v).abs() / v);
                    } else {
                        score += w * (1.0 - x.abs());
                    }
                }
                "maximize" => {
                    score += w * x / scale;
                }
                "minimize" => {
                    score -= w * x / scale;
                }
                _ => {}
            }
        }
    }

    // Apply constraints penalty
    let min_margin = margins.iter().copied().fold(f64::INFINITY, f64::min);
    let req_margin = constraints
        .get("min_static_margin")
        .and_then(|v| v.as_f64())
        .unwrap_or(1.5);

    let worst_ratio = min_margin / req_margin;

    // Penalty ratio logic from legacy evolve.rs
    if worst_ratio < 1.0 {
        let penalty = 0.1; // Default penalty factor
        let ratio = penalty + (1.0 - penalty) * worst_ratio.max(0.0);
        if score > 0.0 {
            score *= ratio;
        } else {
            score *= 1.0 / ratio.max(0.01);
        }
    }

    score
}

fn finalize_stage(mut pending: PendingStage) -> Result<StageGeometry, L2EngineError> {
    if pending.body_open {
        return Err(parse_error("BODY_TUBE missing CLOSE_BODY"));
    }
    if pending.bodytubes.is_empty() {
        return Err(parse_error("STAGE missing BODY_TUBE"));
    }
    let motor_mount = pending
        .motor_mount
        .take()
        .ok_or_else(|| parse_error("STAGE missing MOTOR_MOUNT"))?;
    if let Some(nose) = pending.nosecone.as_mut() {
        if nose.aft_radius <= 0.0 {
            nose.aft_radius = pending
                .bodytubes
                .first()
                .map(|bt| bt.radius)
                .unwrap_or(0.04);
        }
    }

    Ok(StageGeometry {
        name: pending.name,
        nosecone: pending.nosecone,
        bodytubes: pending.bodytubes,
        finsets: pending.finsets,
        point_masses: pending.point_masses,
        motor_mount,
        auxiliary_motor_mounts: pending.auxiliary_motor_mounts,
        radial_assemblies: pending.radial_assemblies,
        separation: None,
        parachute: pending.parachute,
        axial_offset_m: 0.0,
    })
}

fn parse_radial_assembly(
    node: &AstNode,
    core_radius_m: f64,
) -> Result<(RadialAssemblyGeometry, Vec<MotorMountGeometry>), L2EngineError> {
    let kind = node.node_type.to_ascii_lowercase();
    if node.node_type == "STRAP_ON"
        && node
            .params
            .get("separable")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    {
        return Err(parse_error(
            "separable STRAP_ON is unsupported: it requires an additional flight branch",
        ));
    }
    let instance_count = f64_param(&node.params, "instance_count", 1.0).round() as i64;
    if instance_count < 1 {
        return Err(parse_error("POD/STRAP_ON instance_count must be >= 1"));
    }
    let radial_offset_m = f64_param(&node.params, "radial_offset_m", 0.0);
    if !radial_offset_m.is_finite() || radial_offset_m <= 0.0 {
        return Err(parse_error("POD/STRAP_ON radial_offset_m must be finite and positive"));
    }
    let factor = f64_param(&node.params, "aero_interference_factor", 1.0);
    if !factor.is_finite() || factor < 1.0 {
        return Err(parse_error(
            "POD/STRAP_ON aero_interference_factor must be finite and >= 1.0",
        ));
    }
    let children_value = node
        .params
        .get("children")
        .cloned()
        .ok_or_else(|| parse_error("POD/STRAP_ON requires a children array"))?;
    let children: Vec<AstNode> = serde_json::from_value(children_value)
        .map_err(|error| parse_error(&format!("invalid POD/STRAP_ON children: {error}")))?;
    if children.is_empty()
        || children.iter().any(|child| {
            matches!(child.node_type.as_str(), "STAGE" | "POD" | "STRAP_ON")
        })
    {
        return Err(parse_error(
            "POD/STRAP_ON children must be non-empty and may not contain STAGE or nested radial assemblies",
        ));
    }
    let mut synthetic = Vec::with_capacity(children.len() + 1);
    synthetic.push(AstNode {
        node_type: "STAGE".to_string(),
        params: serde_json::json!({"name": "Radial assembly template"}),
    });
    synthetic.extend(children);
    let mut geometry = ast_to_geometry(&synthetic)?;
    let template = geometry
        .stages
        .pop()
        .ok_or_else(|| parse_error("POD/STRAP_ON children produced no geometry"))?;
    let pod_radius_m = template
        .bodytubes
        .iter()
        .map(|tube| tube.radius)
        .chain(template.nosecone.iter().map(|nose| nose.aft_radius))
        .fold(0.0_f64, f64::max);
    if radial_offset_m + 1e-9 < core_radius_m + pod_radius_m {
        return Err(parse_error(&format!(
            "radial assembly overlaps core: offset={radial_offset_m:.6}m < core+pod radius={:.6}m",
            core_radius_m + pod_radius_m
        )));
    }
    let host_inner_radius_m = template
        .bodytubes
        .iter()
        .map(|tube| tube.radius - tube.thickness)
        .fold(f64::INFINITY, f64::min);
    let angle_offset_rad = f64_param(&node.params, "angle_offset_deg", 0.0).to_radians();
    let axial_offset_m = f64_param(&node.params, "axial_offset_m", 0.0);
    let host_aft_m = axial_offset_m
        + template
            .bodytubes
            .iter()
            .map(|tube| tube.axial_offset_m + tube.length)
            .fold(0.0_f64, f64::max);
    let count = instance_count as u32;
    let mut mounts = std::iter::once(template.motor_mount.clone())
        .chain(template.auxiliary_motor_mounts.clone())
        .collect::<Vec<_>>();
    for mount in &mut mounts {
        mount.multiplicity = mount.multiplicity.saturating_mul(count);
        mount.radial_offset_m = radial_offset_m;
        mount.radial_angle_rad = angle_offset_rad;
        mount.instance_angle_step_rad = std::f64::consts::TAU / count as f64;
        mount.host_inner_radius_m = host_inner_radius_m;
        mount.host_aft_m = host_aft_m;
    }
    Ok((
        RadialAssemblyGeometry {
            name: string_param(&node.params, "name")
                .unwrap_or_else(|| "Radial assembly".to_string()),
            kind,
            instance_count: count,
            radial_offset_m,
            angle_offset_rad,
            axial_offset_m,
            nosecone: template.nosecone,
            bodytubes: template.bodytubes,
            finsets: template.finsets,
            point_masses: template.point_masses,
            aero_interference_factor: factor,
        },
        mounts,
    ))
}

fn current_stage_mut(
    current: &mut Option<PendingStage>,
) -> Result<&mut PendingStage, L2EngineError> {
    current
        .as_mut()
        .ok_or_else(|| parse_error("AST must start with STAGE"))
}

fn stage_length(stage: &StageGeometry) -> f64 {
    let nose_end = stage
        .nosecone
        .as_ref()
        .map(|nose| nose.axial_offset_m + nose.length)
        .unwrap_or(0.0);
    let tube_end = stage
        .bodytubes
        .iter()
        .map(|tube| tube.axial_offset_m + tube.length)
        .fold(0.0, f64::max);
    let radial_end = stage
        .radial_assemblies
        .iter()
        .map(|assembly| {
            let nose_end = assembly
                .nosecone
                .as_ref()
                .map(|nose| nose.axial_offset_m + nose.length)
                .unwrap_or(0.0);
            let tube_end = assembly
                .bodytubes
                .iter()
                .map(|tube| tube.axial_offset_m + tube.length)
                .fold(0.0, f64::max);
            assembly.axial_offset_m + nose_end.max(tube_end)
        })
        .fold(0.0, f64::max);
    nose_end.max(tube_end).max(radial_end)
}

fn f64_param(params: &Value, name: &str, default: f64) -> f64 {
    params.get(name).and_then(Value::as_f64).unwrap_or(default)
}

fn string_param(params: &Value, name: &str) -> Option<String> {
    params.get(name).and_then(Value::as_str).map(str::to_string)
}

fn material_density(name: Option<&str>) -> f64 {
    match name.unwrap_or("cardboard") {
        "fiberglass" => 1800.0,
        "carbon" => 1780.0,
        "cardboard" => 600.0,
        "pla" => 1250.0,
        "birch" => 670.0,
        "balsa" => 170.0,
        "aluminum" => 2700.0,
        "kraft" => 700.0,
        "abs" => 1050.0,
        "polycarbonate" => 1200.0,
        "steel" => 7850.0,
        "lead" => 11_340.0,
        _ => 680.0,
    }
}

/// The mission-wide legal material density range (170-11,340 kg/m3, from
/// balsa to lead) every buildable component -- structural or ballast -- must
/// fall within.
fn is_density_in_allowed_range(density_kg_m3: f64) -> bool {
    (170.0..=11_340.0).contains(&density_kg_m3)
}

/// Resolves a material name to a density and rejects anything outside the
/// allowed range. A single choke point so the range never needs re-checking
/// per node type.
fn material_density_checked(name: Option<&str>) -> Result<f64, L2EngineError> {
    let density = material_density(name);
    if !is_density_in_allowed_range(density) {
        return Err(parse_error(&format!(
            "material '{}' density {density} kg/m3 outside allowed 170-11340 kg/m3 range",
            name.unwrap_or("cardboard")
        )));
    }
    Ok(density)
}

fn nose_shape(name: Option<&str>) -> NoseShape {
    match name.unwrap_or("ogive") {
        "conical" => NoseShape::Conical,
        "power" => NoseShape::PowerSeries,
        "parabolic" => NoseShape::Parabolic,
        "haack" => NoseShape::VonKarmanHaack,
        "ellipsoid" => NoseShape::Ellipsoid,
        _ => NoseShape::Ogive,
    }
}

fn parachute_component_mass_kg(
    diameter_m: f64,
    surface_density_kg_m2: f64,
    line_count: f64,
    line_length_m: f64,
    line_density_kg_m: f64,
) -> f64 {
    let canopy_area_m2 = std::f64::consts::PI * (diameter_m / 2.0).powi(2);
    canopy_area_m2 * surface_density_kg_m2 + line_count * line_length_m * line_density_kg_m
}

/// Requires the real OpenRocket motor designation directly (e.g.
/// "20146N5800-P", "F50T") -- the same string `rocket_forge.py`'s
/// `MOTOR_DATABASE` uses for both `.eng` lookup and `.ork` compilation (see
/// `motor_db::parse_eng_file`). There is no `motor_index`-only fallback: the
/// organic-evolution engine's motor pool is whatever real `.eng` files are
/// present, not a small hardcoded set the Rust side happens to recognize by
/// substring, so a candidate that doesn't carry its resolved designation is
/// a genuine authoring error, not something to guess a default for.
fn motor_designation(params: &Value) -> Result<String, L2EngineError> {
    string_param(params, "motor_designation")
        .ok_or_else(|| parse_error("MOTOR_MOUNT missing required 'motor_designation' string param"))
}

fn parse_error(message: &str) -> L2EngineError {
    L2EngineError::ParseError(message.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::io::json::StageLanding;
    use nalgebra::Vector3;

    fn minimal_summary() -> FlightSummary {
        FlightSummary {
            apogee_m: 0.0,
            apogee_time: 0.0,
            max_speed: 0.0,
            max_mach: 0.0,
            max_accel: 0.0,
            max_accel_g: 0.0,
            flight_time: 0.0,
            impact_speed: 0.0,
            apogee_east_m: 0.0,
            apogee_north_m: 0.0,
            stage_landings: Vec::new(),
            total_prop_mass_kg: 0.0,
        }
    }

    fn minimal_stage_nodes(extra: Vec<AstNode>) -> Vec<AstNode> {
        let mut nodes = vec![
            AstNode {
                node_type: "STAGE".to_string(),
                params: serde_json::json!({"name": "S0"}),
            },
            AstNode {
                node_type: "NOSE_CONE".to_string(),
                params: serde_json::json!({"length": 0.3}),
            },
            AstNode {
                node_type: "BODY_TUBE".to_string(),
                params: serde_json::json!({"radius": 0.04, "length": 0.8}),
            },
        ];
        nodes.extend(extra);
        nodes.push(AstNode {
            node_type: "CLOSE_BODY".to_string(),
            params: serde_json::json!({}),
        });
        nodes.push(AstNode {
            node_type: "MOTOR_MOUNT".to_string(),
            params: serde_json::json!({"motor_designation": "F50T"}),
        });
        nodes
    }

    #[test]
    fn ballast_node_adds_a_point_mass_at_the_aft_end() {
        let nodes = minimal_stage_nodes(vec![AstNode {
            node_type: "BALLAST".to_string(),
            params: serde_json::json!({"mass": 0.15, "position": "aft"}),
        }]);
        let geometry = ast_to_geometry(&nodes).expect("valid ballast AST");
        let stage = &geometry.stages[0];
        assert_eq!(stage.point_masses.len(), 1);
        assert!((stage.point_masses[0].mass_kg - 0.15).abs() < 1e-9);
        let bodytube = &stage.bodytubes[0];
        let expected_aft_offset = bodytube.axial_offset_m + bodytube.length - 0.05;
        assert!(
            (stage.point_masses[0].axial_offset_m - expected_aft_offset).abs() < 1e-9,
            "aft ballast offset was {}, expected {}",
            stage.point_masses[0].axial_offset_m,
            expected_aft_offset
        );
    }

    #[test]
    fn ballast_node_supports_explicit_axial_offset() {
        let nodes = minimal_stage_nodes(vec![AstNode {
            node_type: "BALLAST".to_string(),
            params: serde_json::json!({"mass": 0.05, "axial_offset_m": 0.42}),
        }]);
        let geometry = ast_to_geometry(&nodes).expect("valid ballast AST");
        assert!((geometry.stages[0].point_masses[0].axial_offset_m - 0.42).abs() < 1e-9);
    }

    #[test]
    fn ballast_node_expands_symmetric_radial_instances() {
        let nodes = minimal_stage_nodes(vec![AstNode {
            node_type: "BALLAST".to_string(),
            params: serde_json::json!({
                "mass": 0.9,
                "axial_offset_m": 0.42,
                "radial_offset_m": 0.03,
                "instance_count": 3,
            }),
        }]);
        let geometry = ast_to_geometry(&nodes).expect("valid radial ballast AST");
        let masses = &geometry.stages[0].point_masses;
        assert_eq!(masses.len(), 3);
        assert!((masses.iter().map(|mass| mass.mass_kg).sum::<f64>() - 0.9).abs() < 1e-9);
        assert!(masses.iter().map(|mass| mass.radial_y_m).sum::<f64>().abs() < 1e-9);
        assert!(masses.iter().map(|mass| mass.radial_z_m).sum::<f64>().abs() < 1e-9);
    }

    #[test]
    fn ballast_node_rejects_non_positive_mass() {
        let nodes = minimal_stage_nodes(vec![AstNode {
            node_type: "BALLAST".to_string(),
            params: serde_json::json!({"mass": 0.0}),
        }]);
        assert!(ast_to_geometry(&nodes).is_err());
    }

    #[test]
    fn density_range_check_accepts_boundaries_and_rejects_outside() {
        assert!(is_density_in_allowed_range(170.0));
        assert!(is_density_in_allowed_range(11_340.0));
        assert!(is_density_in_allowed_range(680.0));
        assert!(!is_density_in_allowed_range(169.999));
        assert!(!is_density_in_allowed_range(11_340.001));
        assert!(!is_density_in_allowed_range(19_300.0)); // e.g. tungsten -- too dense to be legal
    }

    #[test]
    fn named_materials_all_resolve_within_the_allowed_range() {
        for material in [
            "fiberglass",
            "carbon",
            "cardboard",
            "pla",
            "birch",
            "balsa",
            "aluminum",
            "kraft",
            "abs",
            "polycarbonate",
            "steel",
            "lead",
        ] {
            assert!(
                material_density_checked(Some(material)).is_ok(),
                "material '{material}' should resolve within the allowed range"
            );
        }
    }

    #[test]
    fn scoring_table_reproduces_osifog_style_formula_by_hand() {
        let summary = FlightSummary {
            apogee_m: 3010.0,
            apogee_east_m: 5.0,
            apogee_north_m: 0.0,
            stage_landings: vec![
                StageLanding {
                    stage_idx: 0,
                    touchdown_time_s: 100.0,
                    east_m: 10.0,
                    north_m: 0.0,
                    distance_m: 10.0,
                    vz_ms: 2.0,
                    vxy_ms: 0.0,
                    total_speed_ms: 2.0,
                },
                StageLanding {
                    stage_idx: 1,
                    touchdown_time_s: 110.0,
                    east_m: 30.0,
                    north_m: 0.0,
                    distance_m: 30.0,
                    vz_ms: 4.0,
                    vxy_ms: 0.0,
                    total_speed_ms: 4.0,
                },
            ],
            total_prop_mass_kg: 2.0,
            ..minimal_summary()
        };

        let table: ScoringTable = serde_json::from_value(serde_json::json!({
            "base_score": 900000.0,
            "terms": [
                {"name": "apogee_altitude", "metrics": ["apogee_m"], "reference": [3000.0], "power": 2, "coefficient": -3000.0},
                {"name": "apogee_horizontal", "metrics": ["apogee_east_m", "apogee_north_m"], "reference": [0.0, 0.0], "power": 2, "coefficient": -16.0},
                {"name": "touchdown_position", "metrics": ["stage_landing_east_m", "stage_landing_north_m"], "reference": [0.0, 0.0], "power": 2, "coefficient": -2.0, "aggregate": "mean_over_stages"},
                {"name": "touchdown_speed", "metrics": ["stage_landing_total_speed_ms"], "reference": [0.0], "power": 2, "coefficient": -500.0, "aggregate": "mean_over_stages"},
                {"name": "propellant_used", "metrics": ["total_prop_mass_kg"], "reference": [0.0], "power": 1, "coefficient": -7500.0}
            ]
        }))
        .expect("valid scoring table");

        let score = evaluate_scoring_table(&table, &summary).expect("scoring succeeds");

        // Hand-computed: 900000 - 3000*10^2 - 16*5^2 - 2*20^2 - 500*3^2 - 7500*2
        //              = 900000 - 300000 - 400 - 800 - 4500 - 15000 = 579300
        assert!((score - 579_300.0).abs() < 1e-6, "score was {score}");
    }

    #[test]
    fn scoring_table_fails_closed_when_no_stage_landed() {
        let summary = minimal_summary();
        let table: ScoringTable = serde_json::from_value(serde_json::json!({
            "base_score": 900000.0,
            "terms": [
                {"name": "touchdown_speed", "metrics": ["stage_landing_total_speed_ms"], "reference": [0.0], "power": 2, "coefficient": -500.0, "aggregate": "mean_over_stages"}
            ]
        }))
        .expect("valid scoring table");

        assert!(evaluate_scoring_table(&table, &summary).is_err());
    }

    #[test]
    fn scoring_table_rejects_unknown_metric_name() {
        let summary = minimal_summary();
        let table: ScoringTable = serde_json::from_value(serde_json::json!({
            "base_score": 0.0,
            "terms": [
                {"name": "bogus", "metrics": ["not_a_real_metric"], "reference": [0.0], "power": 1.0, "coefficient": 1.0}
            ]
        }))
        .expect("valid scoring table");

        assert!(evaluate_scoring_table(&table, &summary).is_err());
    }

    #[test]
    fn scoring_table_rejects_scalar_aggregate_on_per_stage_metric() {
        let summary = FlightSummary {
            stage_landings: vec![StageLanding {
                stage_idx: 0,
                touchdown_time_s: 10.0,
                east_m: 1.0,
                north_m: 0.0,
                distance_m: 1.0,
                vz_ms: 0.0,
                vxy_ms: 0.0,
                total_speed_ms: 0.0,
            }],
            ..minimal_summary()
        };
        let table: ScoringTable = serde_json::from_value(serde_json::json!({
            "base_score": 0.0,
            "terms": [
                {"name": "bad_aggregate", "metrics": ["stage_landing_east_m"], "reference": [0.0], "power": 1.0, "coefficient": 1.0}
            ]
        }))
        .expect("valid scoring table");

        assert!(evaluate_scoring_table(&table, &summary).is_err());
    }

    #[test]
    fn atmost_objective_does_not_reward_non_flying_measurement() {
        let summary = FlightSummary {
            apogee_m: 0.0,
            apogee_time: 0.0,
            max_speed: 0.0,
            max_mach: 0.0059,
            max_accel: 0.0,
            max_accel_g: 0.0,
            flight_time: 600.0,
            impact_speed: 0.0,
            apogee_east_m: 0.0,
            apogee_north_m: 0.0,
            stage_landings: Vec::new(),
            total_prop_mass_kg: 0.0,
        };
        let objectives = vec![
            AstObjective {
                metric: "apogee".into(),
                kind: "target".into(),
                value: Some(15_000.0),
                target: None,
                min_value: None,
                scale: None,
                weight: Some(100.0),
            },
            AstObjective {
                metric: "mach".into(),
                kind: "atmost".into(),
                value: Some(3.0),
                target: None,
                min_value: Some(0.1),
                scale: None,
                weight: Some(100.0),
            },
        ];
        let score = score_summary(
            &summary,
            &[3.0],
            &objectives,
            &serde_json::json!({"min_static_margin": 1.5}),
            &RocketGeometry { stages: Vec::new() },
            &[],
        );
        assert!(score < 50.0, "non-flying score was {score}");
    }

    #[test]
    fn motor_adequacy_skips_check_when_constraint_key_absent() {
        // min_thrust_to_weight is not an OSIFOG rule -- confirmed absent
        // from both organizer PDFs. A mission that omits the key entirely
        // (missions/osifog_l3_precision.json, since 2026-07-24) must not
        // have any TWR floor silently reimposed.
        let mission = crate::sim_core::vehicle::Mission {
            name: "underpowered-but-legal".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![crate::sim_core::vehicle::Stage {
                name: "stage".into(),
                dry_mass: 100.0,
                motors: vec![crate::sim_core::vehicle::MotorBurn {
                    role: "main".to_string(),
                    propellant_mass: 1.0,
                    thrust: 10.0,
                    isp: 100.0,
                    thrust_curve: vec![(0.0, 10.0), (1.0, 10.0)],
                    ignition_delay: 0.0,
                    position_from_nose_m: Vector3::zeros(),
                    nozzle_position_from_nose_m: Vector3::zeros(),
                }],
                cd: 0.3,
                area: 0.01,
                inertia: Vector3::new(1.0, 1.0, 1.0),
                nozzle_offset: 0.0,
                cp_offset: 0.0,
                dry_cg_from_nose: 0.0,
                motor_axial_offset_m: 0.0,
                rotational_fixed_mass_kg: 0.0,
                rotational_fixed_cg_from_nose: 0.0,
                rotational_fixed_cg_radial_m: Vector3::zeros(),
                tvc_max: 0.0,
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
        };
        assert!(
            enforce_motor_adequacy(&mission, &serde_json::json!({})).is_ok(),
            "badly underpowered rocket (TWR~0.01) must still pass when no \
             min_thrust_to_weight key is present -- that's not an OSIFOG rule"
        );
    }

    #[test]
    fn motor_adequacy_rejects_launch_twr_below_explicit_requirement() {
        let mission = crate::sim_core::vehicle::Mission {
            name: "underpowered".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![crate::sim_core::vehicle::Stage {
                name: "stage".into(),
                dry_mass: 100.0,
                motors: vec![crate::sim_core::vehicle::MotorBurn {
                    role: "main".to_string(),
                    propellant_mass: 1.0,
                    thrust: 10.0,
                    isp: 100.0,
                    thrust_curve: vec![(0.0, 10.0), (1.0, 10.0)],
                    ignition_delay: 0.0,
                    position_from_nose_m: Vector3::zeros(),
                    nozzle_position_from_nose_m: Vector3::zeros(),
                }],
                cd: 0.3,
                area: 0.01,
                inertia: Vector3::new(1.0, 1.0, 1.0),
                nozzle_offset: 0.0,
                cp_offset: 0.0,
                dry_cg_from_nose: 0.0,
                motor_axial_offset_m: 0.0,
                rotational_fixed_mass_kg: 0.0,
                rotational_fixed_cg_from_nose: 0.0,
                rotational_fixed_cg_radial_m: Vector3::zeros(),
                tvc_max: 0.0,
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
        };
        let error = enforce_motor_adequacy(&mission, &serde_json::json!({"min_thrust_to_weight": 1.5}))
            .unwrap_err();
        assert!(error.starts_with("constraint_violation:min_thrust_to_weight"));
    }

    #[test]
    fn motor_adequacy_embeds_graded_closeness_ratio() {
        // Regression: same flat-zero-gradient bug as motor_mount_collision
        // above -- this is directly the "motor too weak to lift this much
        // structure" constraint, exactly the axis a design search needs a
        // gradient on to trade off lighter materials vs a bigger motor.
        fn twr_closeness(thrust: f64, dry_mass: f64) -> f64 {
            let mission = crate::sim_core::vehicle::Mission {
                name: "twr-closeness".into(),
                wind_velocity_mps: Vector3::zeros(),
                wind_profile: None,
                launch_guide: None,
                relative_humidity: 0.0,
                base_temperature_k: 288.15,
                base_pressure_pa: 101_325.0,
                launch_altitude_m: 0.0,
                stages: vec![crate::sim_core::vehicle::Stage {
                    name: "stage".into(),
                    dry_mass,
                    motors: vec![crate::sim_core::vehicle::MotorBurn {
                        role: "main".to_string(),
                        propellant_mass: 1.0,
                        thrust,
                        isp: 100.0,
                        thrust_curve: vec![(0.0, thrust), (1.0, thrust)],
                        ignition_delay: 0.0,
                        position_from_nose_m: Vector3::zeros(),
                        nozzle_position_from_nose_m: Vector3::zeros(),
                    }],
                    cd: 0.3,
                    area: 0.01,
                    inertia: Vector3::new(1.0, 1.0, 1.0),
                    nozzle_offset: 0.0,
                    cp_offset: 0.0,
                    dry_cg_from_nose: 0.0,
                    motor_axial_offset_m: 0.0,
                    rotational_fixed_mass_kg: 0.0,
                    rotational_fixed_cg_from_nose: 0.0,
                    rotational_fixed_cg_radial_m: Vector3::zeros(),
                    tvc_max: 0.0,
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
            };
            let error = enforce_motor_adequacy(
                &mission,
                &serde_json::json!({"min_thrust_to_weight": 1.5}),
            )
            .unwrap_err();
            let (reason, ratio) = error
                .split_once(CLOSENESS_SEPARATOR)
                .expect("TWR error must embed a closeness ratio");
            assert!(reason.starts_with("constraint_violation:min_thrust_to_weight"));
            ratio.parse::<f64>().expect("closeness ratio must be a valid float")
        }

        // Explicit minimum_twr of 1.5 (this check is opt-in only, not a
        // real OSIFOG rule -- see motor_adequacy_skips_check_when_
        // constraint_key_absent). far_miss: thrust=10N vs ~981N weight
        // (TWR~0.01, badly underpowered). near_miss: TWR~1.45, just short.
        let far_miss = twr_closeness(10.0, 100.0);
        let near_miss = twr_closeness(1000.0, 70.32);
        assert!(
            near_miss > far_miss,
            "near-miss ({near_miss}) should embed a higher closeness ratio than far-miss ({far_miss})"
        );
        assert!((0.0..=1.0).contains(&far_miss));
        assert!((0.0..=1.0).contains(&near_miss));
    }

    fn adequately_powered_mission() -> crate::sim_core::vehicle::Mission {
        crate::sim_core::vehicle::Mission {
            name: "adequate".into(),
            wind_velocity_mps: Vector3::zeros(),
            wind_profile: None,
            launch_guide: None,
            relative_humidity: 0.0,
            base_temperature_k: 288.15,
            base_pressure_pa: 101_325.0,
            launch_altitude_m: 0.0,
            stages: vec![crate::sim_core::vehicle::Stage {
                name: "stage".into(),
                dry_mass: 10.0,
                motors: vec![crate::sim_core::vehicle::MotorBurn {
                    role: "main".to_string(),
                    propellant_mass: 1.0,
                    thrust: 2000.0,
                    isp: 100.0,
                    thrust_curve: vec![(0.0, 2000.0), (1.0, 2000.0)],
                    ignition_delay: 0.0,
                    position_from_nose_m: Vector3::zeros(),
                    nozzle_position_from_nose_m: Vector3::zeros(),
                }],
                cd: 0.3,
                area: 0.01,
                inertia: Vector3::new(1.0, 1.0, 1.0),
                nozzle_offset: 0.0,
                cp_offset: 0.0,
                dry_cg_from_nose: 0.0,
                motor_axial_offset_m: 0.0,
                rotational_fixed_mass_kg: 0.0,
                rotational_fixed_cg_from_nose: 0.0,
                rotational_fixed_cg_radial_m: Vector3::zeros(),
                tvc_max: 0.0,
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
    fn hard_constraints_reject_rocket_taller_than_max_height_m() {
        let mission = adequately_powered_mission();
        let error = enforce_hard_constraints(
            &minimal_summary(),
            &[],
            &serde_json::json!({"max_height_m": 4.0}),
            &mission,
            4.5,
        )
        .unwrap_err();
        assert!(error.starts_with("constraint_violation:max_height_m"));
    }

    #[test]
    fn hard_constraints_allow_rocket_within_max_height_m() {
        let mission = adequately_powered_mission();
        assert!(
            enforce_hard_constraints(
                &minimal_summary(),
                &[],
                &serde_json::json!({"max_height_m": 4.0}),
                &mission,
                3.5,
            )
            .is_ok()
        );
    }

    #[test]
    fn ascent_screen_keeps_retro_wet_mass_but_only_burns_primary_cluster() {
        let nodes: Vec<AstNode> = serde_json::from_value(serde_json::json!([
            {"type":"STAGE","params":{"name":"Cluster"}},
            {"type":"NOSE_CONE","params":{"length":0.3}},
            {"type":"BODY_TUBE","params":{"length":1.2,"radius":0.30}},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"MAIN","role":"main","multiplicity":3,"ignition":"launch"
            }},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"RETRO","role":"retro","multiplicity":1,
                "ignition":"launch","ignition_delay":200.0
            }},
            {"type":"CLOSE_BODY","params":{}}
        ]))
        .expect("valid cluster AST");
        let main = ThrustCurve {
            time_s: vec![0.0, 1.0],
            thrust_n: vec![1000.0, 0.0],
            propellant_mass_kg: 1.0,
            total_mass_kg: 2.0,
            diameter_m: 0.05,
            length_m: 0.5,
        };
        let retro = ThrustCurve {
            time_s: vec![0.0, 2.0],
            thrust_n: vec![500.0, 0.0],
            propellant_mass_kg: 1.5,
            total_mass_kg: 3.0,
            diameter_m: 0.06,
            length_m: 0.7,
        };
        let mut geometry = ast_to_geometry(&nodes).expect("valid geometry");
        let mut clusters = vec![vec![main.clone(), main.clone(), main, retro]];
        enrich_ast_motor_mounts_multi(&mut geometry, &clusters);
        let original_mission = crate::mission_adapter::build_mission_with_motor_clusters(
            &geometry,
            &clusters,
            PhysicsMode::OpenRocketLegacy,
        )
        .expect("full cluster mission");
        let original_mass = original_mission.total_mass();

        prepare_ascent_screen(&mut geometry, &mut clusters).expect("ascent projection");
        assert_eq!(geometry.stages[0].motor_mount.role, "main");
        assert!(geometry.stages[0].auxiliary_motor_mounts.is_empty());
        assert_eq!(clusters[0].len(), 3);
        assert!(
            geometry.stages[0]
                .point_masses
                .iter()
                .any(|mass| (mass.mass_kg - 3.0).abs() < 1.0e-12),
            "retro motor must remain as installed wet mass"
        );

        let ascent_mission = crate::mission_adapter::build_mission_with_motor_clusters(
            &geometry,
            &clusters,
            PhysicsMode::OpenRocketLegacy,
        )
        .expect("ascent cluster mission");
        assert!(
            (ascent_mission.total_mass() - original_mass).abs() < 1.0e-9,
            "ascent projection changed launch mass: {} vs {}",
            ascent_mission.total_mass(),
            original_mass
        );
    }

    #[test]
    fn motor_mount_clearance_rejects_coincident_ring_instances() {
        // multiplicity=3 with no radial_offset_m/instance_angle_step_deg set
        // -- all 3 instances default to the exact same position. This is the
        // unsafe-default scenario `instance_angle_step_deg` docs warn about.
        let nodes: Vec<AstNode> = serde_json::from_value(serde_json::json!([
            {"type":"STAGE","params":{"name":"Cluster"}},
            {"type":"NOSE_CONE","params":{"length":0.3}},
            {"type":"BODY_TUBE","params":{"length":1.2,"radius":0.30}},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"MAIN","role":"main","multiplicity":3,"ignition":"launch"
            }},
            {"type":"CLOSE_BODY","params":{}}
        ]))
        .expect("valid cluster AST");
        let main = ThrustCurve {
            time_s: vec![0.0, 1.0],
            thrust_n: vec![1000.0, 0.0],
            propellant_mass_kg: 1.0,
            total_mass_kg: 2.0,
            diameter_m: 0.05,
            length_m: 0.5,
        };
        let mut geometry = ast_to_geometry(&nodes).expect("valid geometry");
        let clusters = vec![vec![main.clone(), main.clone(), main]];
        enrich_ast_motor_mounts_multi(&mut geometry, &clusters);
        let error = enforce_motor_mount_clearance(&geometry).unwrap_err();
        assert!(error.starts_with("constraint_violation:motor_mount_collision"));
    }

    #[test]
    fn motor_mount_clearance_embeds_graded_closeness_ratio() {
        // Regression: this check used a bare `format!` with no embedded
        // closeness ratio -- every failing candidate tied at the same flat
        // 0.0 score in evaluate_ast's ranking (see `violation()`'s own
        // doc comment), regardless of how close dist was to needed.
        // Confirmed as the actual cause of a live campaign losing its GA
        // gradient the moment this became the population's dominant
        // blocking constraint. Two radial offsets: one far from legal
        // (near-total overlap) and one just barely short of legal -- the
        // near-miss must embed a HIGHER closeness ratio than the far miss.
        fn collision_closeness(radial_offset_m: f64) -> f64 {
            let nodes: Vec<AstNode> = serde_json::from_value(serde_json::json!([
                {"type":"STAGE","params":{"name":"Cluster"}},
                {"type":"NOSE_CONE","params":{"length":0.3}},
                {"type":"BODY_TUBE","params":{"length":1.2,"radius":0.30}},
                {"type":"MOTOR_MOUNT","params":{
                    "motor_designation":"MAIN","role":"main","multiplicity":3,"ignition":"launch",
                    "radial_offset_m": radial_offset_m, "instance_angle_step_deg": 120.0
                }},
                {"type":"CLOSE_BODY","params":{}}
            ]))
            .expect("valid cluster AST");
            let main = ThrustCurve {
                time_s: vec![0.0, 1.0],
                thrust_n: vec![1000.0, 0.0],
                propellant_mass_kg: 1.0,
                total_mass_kg: 2.0,
                diameter_m: 0.05,
                length_m: 0.5,
            };
            let mut geometry = ast_to_geometry(&nodes).expect("valid geometry");
            let clusters = vec![vec![main.clone(), main.clone(), main]];
            enrich_ast_motor_mounts_multi(&mut geometry, &clusters);
            let error = enforce_motor_mount_clearance(&geometry).unwrap_err();
            let (reason, ratio) = error
                .split_once(CLOSENESS_SEPARATOR)
                .expect("collision error must embed a closeness ratio");
            assert!(reason.starts_with("constraint_violation:motor_mount_collision"));
            ratio.parse::<f64>().expect("closeness ratio must be a valid float")
        }

        // mount_outer_radius_m = 0.05/2 + mount_thickness_m default (0.001)
        // = 0.026; needed = 2*0.026 + 0.002 clearance = 0.054; collision
        // threshold is offset*sqrt(3) < 0.054, i.e. offset < ~0.03118.
        // 0.03 is comfortably inside that (a real near-miss); 0.0 (fully
        // coincident, same AST as motor_mount_clearance_rejects_
        // coincident_ring_instances above) is the maximally-far miss.
        let far_miss = collision_closeness(0.0);
        let near_miss = collision_closeness(0.03);
        assert!(
            near_miss > far_miss,
            "near-miss ({near_miss}) should embed a higher closeness ratio than far-miss ({far_miss})"
        );
        assert!((0.0..=1.0).contains(&far_miss));
        assert!((0.0..=1.0).contains(&near_miss));
    }

    #[test]
    fn motor_mount_clearance_allows_properly_spread_ring() {
        let nodes: Vec<AstNode> = serde_json::from_value(serde_json::json!([
            {"type":"STAGE","params":{"name":"Cluster"}},
            {"type":"NOSE_CONE","params":{"length":0.3}},
            {"type":"BODY_TUBE","params":{"length":1.2,"radius":0.30}},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"MAIN","role":"main","multiplicity":3,"ignition":"launch",
                "radial_offset_m": 0.20, "instance_angle_step_deg": 120.0
            }},
            {"type":"CLOSE_BODY","params":{}}
        ]))
        .expect("valid cluster AST");
        let main = ThrustCurve {
            time_s: vec![0.0, 1.0],
            thrust_n: vec![1000.0, 0.0],
            propellant_mass_kg: 1.0,
            total_mass_kg: 2.0,
            diameter_m: 0.05,
            length_m: 0.5,
        };
        let mut geometry = ast_to_geometry(&nodes).expect("valid geometry");
        let clusters = vec![vec![main.clone(), main.clone(), main]];
        enrich_ast_motor_mounts_multi(&mut geometry, &clusters);
        assert!(enforce_motor_mount_clearance(&geometry).is_ok());
    }

    #[test]
    fn motor_mount_clearance_allows_octaweb_style_3_plus_1_tangent_ring() {
        // 3 main motors (38mm diameter, r=0.019m) each tangent to a central
        // retro motor (29mm diameter, r=0.0145m): radial_offset = r_retro +
        // r_main (+0.005 buffer, matching create_random_ast's existing
        // retro-offset convention of a small explicit clearance rather than
        // exact zero-tolerance tangency). Verified analytically (see
        // OSIFOG/PLAN_INTERNAL_OCTAWEB_CLUSTER.md): this ratio (0.296) is
        // nowhere near the 0.1547 threshold where outer motors would start
        // overlapping each other, across all 480 real main x retro pairs in
        // the mission's motor pools -- this is a representative real case,
        // not a hand-picked lucky one.
        let r_main = 0.019;
        let r_retro = 0.0145;
        let radial_offset = r_retro + r_main + 0.005;
        let nodes: Vec<AstNode> = serde_json::from_value(serde_json::json!([
            {"type":"STAGE","params":{"name":"Octaweb"}},
            {"type":"NOSE_CONE","params":{"length":0.3}},
            {"type":"BODY_TUBE","params":{"length":1.2,"radius":0.12}},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"MAIN","role":"main","multiplicity":3,"ignition":"launch",
                "radial_offset_m": radial_offset, "instance_angle_step_deg": 120.0
            }},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"RETRO","role":"retro","multiplicity":1,
                "ignition":"launch","ignition_delay":200.0
            }},
            {"type":"CLOSE_BODY","params":{}}
        ]))
        .expect("valid octaweb AST");
        let main = ThrustCurve {
            time_s: vec![0.0, 1.0],
            thrust_n: vec![1000.0, 0.0],
            propellant_mass_kg: 1.0,
            total_mass_kg: 2.0,
            diameter_m: r_main * 2.0,
            length_m: 0.5,
        };
        let retro = ThrustCurve {
            time_s: vec![0.0, 2.0],
            thrust_n: vec![500.0, 0.0],
            propellant_mass_kg: 1.5,
            total_mass_kg: 3.0,
            diameter_m: r_retro * 2.0,
            length_m: 0.7,
        };
        let mut geometry = ast_to_geometry(&nodes).expect("valid geometry");
        let clusters = vec![vec![main.clone(), main.clone(), main, retro]];
        enrich_ast_motor_mounts_multi(&mut geometry, &clusters);
        assert!(
            enforce_motor_mount_clearance(&geometry).is_ok(),
            "real main/retro diameter ratio should never trigger outer-outer overlap"
        );
    }

    #[test]
    fn motor_mount_clearance_rejects_undersized_octaweb_center_motor() {
        // Same shape as the tangent-ring test above, but with a central
        // motor deliberately smaller than the 0.1547 * r_main safety ratio
        // -- the 3 outer motors are each still tangent to the (too-small)
        // center, but now overlap each other. This is exactly the failure
        // mode a mutation-jittered radial_offset_m could hit if this check
        // did not exist.
        let r_main = 0.019;
        let r_retro = 0.001; // far below 0.1547 * r_main ~= 0.00294
        let radial_offset = r_retro + r_main;
        let nodes: Vec<AstNode> = serde_json::from_value(serde_json::json!([
            {"type":"STAGE","params":{"name":"Octaweb"}},
            {"type":"NOSE_CONE","params":{"length":0.3}},
            {"type":"BODY_TUBE","params":{"length":1.2,"radius":0.12}},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"MAIN","role":"main","multiplicity":3,"ignition":"launch",
                "radial_offset_m": radial_offset, "instance_angle_step_deg": 120.0
            }},
            {"type":"MOTOR_MOUNT","params":{
                "motor_designation":"RETRO","role":"retro","multiplicity":1,
                "ignition":"launch","ignition_delay":200.0
            }},
            {"type":"CLOSE_BODY","params":{}}
        ]))
        .expect("valid octaweb AST");
        let main = ThrustCurve {
            time_s: vec![0.0, 1.0],
            thrust_n: vec![1000.0, 0.0],
            propellant_mass_kg: 1.0,
            total_mass_kg: 2.0,
            diameter_m: r_main * 2.0,
            length_m: 0.5,
        };
        let retro = ThrustCurve {
            time_s: vec![0.0, 2.0],
            thrust_n: vec![500.0, 0.0],
            propellant_mass_kg: 1.5,
            total_mass_kg: 3.0,
            diameter_m: r_retro * 2.0,
            length_m: 0.7,
        };
        let mut geometry = ast_to_geometry(&nodes).expect("valid geometry");
        let clusters = vec![vec![main.clone(), main.clone(), main, retro]];
        enrich_ast_motor_mounts_multi(&mut geometry, &clusters);
        let error = enforce_motor_mount_clearance(&geometry).unwrap_err();
        assert!(error.starts_with("constraint_violation:motor_mount_collision"));
    }
}
