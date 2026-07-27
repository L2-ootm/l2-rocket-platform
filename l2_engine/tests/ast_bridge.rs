use std::collections::HashMap;

use l2_engine::PhysicsMode;
use l2_engine::ast::{
    AstCalibration, AstEvalBatch, AstNode, AstObjective, ast_to_geometry, evaluate_ast,
};
use l2_engine::builder;
use l2_engine::mission_adapter::build_mission_with_motor_clusters;
use l2_engine::motor_db::{ThrustCurve, parse_eng_file};
use nalgebra::Vector2;
use serde_json::json;

// Real OpenRocket motor designations (the catalog-code-prefixed "-P" form
// proven to resolve correctly in OpenRocket 23.09, matching what
// l2_hyper/generator.py and the declarative mission JSONs already use), not
// arbitrary short aliases. `.eng` files are keyed by these exact strings in
// their own header (see rocket_forge.py::MOTOR_DATABASE's comment) --
// `parse_eng_file` reads the designation from the file itself instead of
// requiring the caller to already know it.
const N5800: &str = "20146N5800-P";
const O8000: &str = "40960O8000-P";

fn curves() -> HashMap<String, ThrustCurve> {
    let mut curves = HashMap::new();
    let (designation, curve) =
        parse_eng_file(include_str!("../motors/N5800.eng")).expect("N5800 fixture");
    curves.insert(designation, curve);
    let (designation, curve) =
        parse_eng_file(include_str!("../motors/O8000.eng")).expect("O8000 fixture");
    curves.insert(designation, curve);
    curves
}

fn synthetic_curve(burn_time_s: f64) -> ThrustCurve {
    ThrustCurve {
        time_s: vec![0.0, burn_time_s],
        thrust_n: vec![0.0, 1000.0],
        propellant_mass_kg: 1.0,
        total_mass_kg: 2.0,
        diameter_m: 0.054,
        length_m: 0.5,
    }
}

fn one_stage_json() -> String {
    format!(
        r#"[
      {{"type":"STAGE","params":{{"name":"Sustainer"}}}},
      {{"type":"NOSE_CONE","params":{{"shape":"conical","length":0.35,"material":"cardboard"}}}},
      {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.055,"material":"cardboard"}}}},
      {{"type":"FIN_SET","params":{{"count":4,"sweep":25.0,"root":0.16,"height":0.09,"cross_section":"rounded"}}}},
      {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}","ignition":"automatic"}}}},
      {{"type":"CLOSE_BODY","params":{{}}}}
    ]"#
    )
}

#[test]
fn parses_ast_nodes_from_json() {
    let nodes: Vec<AstNode> = serde_json::from_str(&one_stage_json()).expect("valid AST JSON");

    assert_eq!(nodes.len(), 6);
    assert_eq!(nodes[0].node_type, "STAGE");
    assert_eq!(nodes[4].params["motor_designation"], N5800);
}

#[test]
fn compiles_cluster_multiplicity_and_independent_delayed_motor() {
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Cluster"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.3,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.30,"material":"cardboard"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}","role":"main","multiplicity":3,"ignition":"launch"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{O8000}","role":"retro","multiplicity":1,"ignition":"primary_burnout","ignition_delay":1.25}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    )).unwrap();
    let geometry = ast_to_geometry(&nodes).unwrap();
    let stage = &geometry.stages[0];
    assert_eq!(stage.motor_mount.role, "main");
    assert_eq!(stage.motor_mount.multiplicity, 3);
    assert_eq!(stage.auxiliary_motor_mounts.len(), 1);
    assert_eq!(stage.auxiliary_motor_mounts[0].role, "retro");

    let db = curves();
    let main = db[N5800].clone();
    let retro = db[O8000].clone();
    let primary_burn = *main.time_s.last().unwrap();
    let mission = build_mission_with_motor_clusters(
        &geometry,
        &[vec![main.clone(), main.clone(), main, retro]],
        PhysicsMode::OpenRocketLegacy,
    )
    .unwrap();
    assert_eq!(mission.stages[0].motors.len(), 4);
    assert!((mission.stages[0].motors[3].ignition_delay - (primary_burn + 1.25)).abs() < 1e-9);
    let expected_propellant = 3.0 * db[N5800].propellant_mass_kg + db[O8000].propellant_mass_kg;
    assert!((mission.stages[0].propellant_mass() - expected_propellant).abs() < 1e-9);
}

#[test]
fn compiles_three_external_pods_with_explicit_motor_poses() {
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"3+1 PodSet"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.3,"aft_radius":0.09,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.09,"material":"cardboard"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{O8000}","role":"retro","ignition":"primary_burnout","ignition_delay":1.0}}}},
          {{"type":"CLOSE_BODY","params":{{}}}},
          {{"type":"POD","params":{{
            "name":"Ascent pods","instance_count":3,"radial_offset_m":0.151,
            "angle_offset_deg":0.0,"aero_interference_factor":1.15,
            "children":[
              {{"type":"NOSE_CONE","params":{{"length":0.25,"aft_radius":0.06,"material":"cardboard"}}}},
              {{"type":"BODY_TUBE","params":{{"length":1.0,"radius":0.06,"material":"cardboard"}}}},
              {{"type":"FIN_SET","params":{{"count":3,"root":0.18,"height":0.08}}}},
              {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}","role":"main","ignition":"launch"}}}},
              {{"type":"CLOSE_BODY","params":{{}}}}
            ]
          }}}}
        ]"#
    ))
    .expect("valid radial AST JSON");

    let mut geometry = ast_to_geometry(&nodes).expect("radial geometry");
    let stage = &geometry.stages[0];
    assert_eq!(stage.radial_assemblies.len(), 1);
    assert_eq!(stage.radial_assemblies[0].instance_count, 3);
    assert_eq!(stage.auxiliary_motor_mounts.len(), 1);
    assert_eq!(stage.auxiliary_motor_mounts[0].multiplicity, 3);
    assert!((stage.auxiliary_motor_mounts[0].radial_offset_m - 0.151).abs() < 1e-12);

    let db = curves();
    let pod = db[N5800].clone();
    let retro = db[O8000].clone();
    l2_engine::ast::enrich_ast_motor_mounts_multi(
        &mut geometry,
        &[vec![retro.clone(), pod.clone(), pod.clone(), pod]],
    );
    let mission = build_mission_with_motor_clusters(
        &geometry,
        &[vec![retro, db[N5800].clone(), db[N5800].clone(), db[N5800].clone()]],
        PhysicsMode::OpenRocketLegacy,
    )
    .expect("native pod mission");
    assert_eq!(mission.stages[0].motors.len(), 4);
    let radial_motors = &mission.stages[0].motors[1..];
    assert!(radial_motors.iter().all(|motor| motor.role == "main"));
    assert!(radial_motors.iter().all(|motor| {
        motor.nozzle_position_from_nose_m.y.hypot(motor.nozzle_position_from_nose_m.z)
            > 0.15
    }));
    let radial_sum = radial_motors
        .iter()
        .map(|motor| {
            Vector2::new(
                motor.nozzle_position_from_nose_m.y,
                motor.nozzle_position_from_nose_m.z,
            )
        })
        .sum::<nalgebra::Vector2<f64>>();
    assert!(radial_sum.norm() < 1e-12);

    let mut core_only = geometry.clone();
    core_only.stages[0].radial_assemblies.clear();
    core_only.stages[0].auxiliary_motor_mounts.clear();
    let core_mission = build_mission_with_motor_clusters(
        &core_only,
        &[vec![db[O8000].clone()]],
        PhysicsMode::OpenRocketLegacy,
    )
    .expect("core-only comparison mission");
    assert!(
        mission.stages[0].cd_at(0.5) > core_mission.stages[0].cd_at(0.5),
        "pod frontal area and interference must increase drag"
    );
    assert!(
        mission.stages[0].cn_alpha.unwrap() > core_mission.stages[0].cn_alpha.unwrap(),
        "pod finsets must contribute native stability authority"
    );

    let symmetric = l2_engine::mass_calculator::mass_properties_3d(&geometry.stages[0], &[]);
    assert!(symmetric.cg_m.y.hypot(symmetric.cg_m.z) < 1e-12);
    assert!((symmetric.inertia.x - symmetric.inertia.y).abs() < 1e-9);

    let mut one_pod = geometry.stages[0].clone();
    one_pod.radial_assemblies[0].instance_count = 1;
    one_pod.auxiliary_motor_mounts[0].multiplicity = 1;
    one_pod.auxiliary_motor_mounts[0].instance_angle_step_rad = 0.0;
    let asymmetric = l2_engine::mass_calculator::mass_properties_3d(&one_pod, &[]);
    assert!(asymmetric.cg_m.y.abs() > 1e-3);
    assert!((asymmetric.inertia.x - asymmetric.inertia.y).abs() > 1e-4);
}

#[test]
fn rejects_separable_strap_on_until_a_third_flight_branch_exists() {
    let nodes: Vec<AstNode> = serde_json::from_str(
        r#"[
          {"type":"STAGE","params":{"name":"Core"}},
          {"type":"BODY_TUBE","params":{"length":1.0,"radius":0.05}},
          {"type":"MOTOR_MOUNT","params":{"motor_designation":"20146N5800-P"}},
          {"type":"CLOSE_BODY","params":{}},
          {"type":"STRAP_ON","params":{"separable":true,"instance_count":2,"radial_offset_m":0.1,"children":[]}}
        ]"#,
    )
    .unwrap();

    let error = ast_to_geometry(&nodes).expect_err("must fail closed");
    assert!(error.to_string().contains("additional flight branch"));
}

#[test]
fn parses_batch_objectives_for_target_apogee() {
    let batch: AstEvalBatch = serde_json::from_str(
        r#"{
          "target_apogee_m": 15000.0,
          "objectives": [
            {"metric":"apogee","kind":"maximize","scale":1000000.0}
          ],
          "candidates": []
        }"#,
    )
    .expect("valid batch JSON");

    assert_eq!(batch.resolved_target_apogee_m(), 1_000_000.0);
    assert_eq!(
        batch.resolved_physics_mode().unwrap(),
        PhysicsMode::OpenRocketLegacy
    );
}

#[test]
fn parses_batch_physics_mode_override() {
    let batch: AstEvalBatch = serde_json::from_str(
        r#"{
          "target_apogee_m": 15000.0,
          "physics_mode": "hyperreal",
          "candidates": []
        }"#,
    )
    .expect("valid batch JSON");

    assert_eq!(
        batch.resolved_physics_mode().unwrap(),
        PhysicsMode::HyperReal
    );
}

#[test]
fn parses_batch_phase_machs_for_static_margin_gates() {
    let batch: AstEvalBatch = serde_json::from_str(
        r#"{
          "target_apogee_m": 15000.0,
          "phase_machs": [0.3, 3.0, 10.0],
          "candidates": []
        }"#,
    )
    .expect("valid batch JSON");

    assert_eq!(batch.resolved_phase_machs(), vec![0.3, 3.0, 10.0]);
}

#[test]
fn compiles_two_stage_ast_to_ignition_order_geometry() {
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Sustainer"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.35,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.0,"radius":0.055,"material":"cardboard"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}},
          {{"type":"STAGE","params":{{"name":"Booster"}}}},
          {{"type":"BODY_TUBE","params":{{"length":0.8,"radius":0.09,"material":"cardboard"}}}},
          {{"type":"FIN_SET","params":{{"count":4,"root":0.22,"height":0.14}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{O8000}"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");

    let geometry = ast_to_geometry(&nodes).expect("geometry");

    assert_eq!(geometry.stages.len(), 2);
    assert_eq!(geometry.stages[0].name, "Booster");
    assert_eq!(geometry.stages[1].name, "Sustainer");
    assert!(geometry.stages[0].axial_offset_m > geometry.stages[1].axial_offset_m);
    assert!((geometry.stages[0].motor_mount.motor_overhang_m - 0.005).abs() < 1e-9);
}

#[test]
fn rust_evaluation_enriches_ast_motor_mount_tube_mass() {
    let nodes: Vec<AstNode> = serde_json::from_str(&one_stage_json()).expect("valid AST JSON");
    let result = evaluate_ast(
        "mount-mass-candidate",
        &nodes,
        &curves(),
        &[],
        &json!({"target_apogee_m": 15_000.0}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(result.status, "success");
    assert!(result.score.is_finite());
    let serialized = serde_json::to_value(&result).expect("result telemetry serializes");
    assert!(serialized["apogee_east_m"].is_number());
    assert!(serialized["apogee_north_m"].is_number());
    assert!(serialized["total_prop_mass_kg"].is_number());
    assert_eq!(
        serialized["stage_landings"]
            .as_array()
            .expect("stage landing array")
            .len(),
        1
    );
    assert!(serialized["stage_landings"][0]["distance_m"].is_number());
    assert!(serialized["stage_landings"][0]["total_speed_ms"].is_number());
}

#[test]
fn rust_evaluation_rejects_hard_max_mach_constraint() {
    let nodes: Vec<AstNode> = serde_json::from_str(&one_stage_json()).expect("valid AST JSON");
    let result = evaluate_ast(
        "too-fast-candidate",
        &nodes,
        &curves(),
        &[],
        &json!({"target_apogee_m": 16_000.0, "max_mach": 0.01}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(result.status, "failed");
    assert!(result.reason.starts_with("constraint_violation:max_mach"));
}

#[test]
fn rust_evaluation_rejects_hard_static_margin_constraint() {
    let nodes: Vec<AstNode> = serde_json::from_str(&one_stage_json()).expect("valid AST JSON");
    let result = evaluate_ast(
        "unstable-candidate",
        &nodes,
        &curves(),
        &[],
        &json!({"target_apogee_m": 16_000.0, "min_static_margin": 1000.0}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(result.status, "failed");
    assert!(
        result
            .reason
            .starts_with("constraint_violation:min_static_margin")
    );
}

#[test]
fn phase_mach_static_margin_uses_requested_supersonic_phase() {
    let nodes: Vec<AstNode> = serde_json::from_str(&one_stage_json()).expect("valid AST JSON");
    let geometry = ast_to_geometry(&nodes).expect("geometry");
    let curves = vec![curves()[N5800].clone()];

    let low_mach = builder::static_margins_with_mode_at_machs(
        &geometry,
        &curves,
        PhysicsMode::OpenRocketLegacy,
        &[0.3],
    );
    let high_mach = builder::static_margins_with_mode_at_machs(
        &geometry,
        &curves,
        PhysicsMode::OpenRocketLegacy,
        &[10.0],
    );

    assert!(high_mach[0] < low_mach[0]);
}

#[test]
fn rust_evaluation_can_score_longer_motor_burn_time_objective() {
    // Larger fins than the shared one_stage_json() fixture: this test
    // exercises the "objectives" scoring path (does a longer burn score
    // higher?), not stability, so the fixture needs genuinely positive
    // static margin to reach evaluate_ast's "success" status under the real
    // OSIFOG static-stability floor (OSIFOG_Nivel3_ProjetoFalcon.pdf sec. 2
    // item 3 -- see enforce_hard_constraints). The old
    // "min_static_margin": -1000.0 trick used to bypass the check entirely
    // when the key was present with an absurd value; that check is no
    // longer configurable below the real 0.0 physical floor.
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Sustainer"}}}},
          {{"type":"NOSE_CONE","params":{{"shape":"conical","length":0.35,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.055,"material":"cardboard"}}}},
          {{"type":"FIN_SET","params":{{"count":4,"sweep":10.0,"root":0.30,"height":0.35,"cross_section":"rounded"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}","ignition":"automatic"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");

    let mut short_curves = HashMap::new();
    short_curves.insert(N5800.to_string(), synthetic_curve(1.0));
    let mut long_curves = HashMap::new();
    long_curves.insert(N5800.to_string(), synthetic_curve(8.0));

    let objectives: Vec<AstObjective> = vec![
        serde_json::from_value(json!({
            "metric": "burn_time",
            "kind": "maximize",
            "scale": 10.0,
            "weight": 10.0
        }))
        .expect("objective"),
    ];
    let constraints = json!({"max_mach": 999.0});

    let short = evaluate_ast(
        "short-burn",
        &nodes,
        &short_curves,
        &objectives,
        &constraints,
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );
    let long = evaluate_ast(
        "long-burn",
        &nodes,
        &long_curves,
        &objectives,
        &constraints,
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(short.status, "success");
    assert_eq!(long.status, "success");
    assert!(long.score > short.score);
}

#[test]
fn ast_fin_cross_section_defaults_to_airfoil_and_preserves_explicit_value() {
    let explicit_nodes: Vec<AstNode> =
        serde_json::from_str(&one_stage_json()).expect("valid AST JSON");
    let explicit = ast_to_geometry(&explicit_nodes).expect("geometry");
    assert_eq!(explicit.stages[0].finsets[0].cross_section, "rounded");

    let default_nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Sustainer"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.055,"material":"cardboard"}}}},
          {{"type":"FIN_SET","params":{{"count":4,"root":0.16,"height":0.09}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");
    let defaulted = ast_to_geometry(&default_nodes).expect("geometry");
    assert_eq!(defaulted.stages[0].finsets[0].cross_section, "airfoil");
}

#[test]
fn ast_payload_compiles_to_body_top_point_mass() {
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Sustainer"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.35,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.055,"material":"cardboard"}}}},
          {{"type":"PAYLOAD","params":{{"mass":2.5}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");

    let geometry = ast_to_geometry(&nodes).expect("geometry");

    assert_eq!(geometry.stages[0].point_masses.len(), 1);
    assert!((geometry.stages[0].point_masses[0].mass_kg - 2.5).abs() < 1e-9);
    assert!((geometry.stages[0].point_masses[0].axial_offset_m - 0.40).abs() < 1e-9);
    assert_eq!(
        geometry.stages[0]
            .nosecone
            .as_ref()
            .expect("nosecone")
            .ballast_mass,
        0.0
    );
}

#[test]
fn ast_parachute_carries_openrocket_packed_mass() {
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Sustainer"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.35,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.055,"material":"cardboard"}}}},
          {{"type":"PARACHUTE","params":{{"diameter":0.5}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");

    let geometry = ast_to_geometry(&nodes).expect("geometry");
    let parachute = geometry.stages[0].parachute.as_ref().expect("parachute");
    let expected_mass =
        std::f64::consts::PI * (0.5_f64 / 2.0).powi(2) * 0.067 + 6.0 * (0.5 * 1.1) * 0.0018;

    assert!((parachute.packed_mass_kg - expected_mass).abs() < 1e-9);
    assert!((parachute.axial_offset_m - 0.95).abs() < 1e-9);
}

#[test]
fn rejects_malformed_ast_shapes() {
    let unmatched: Vec<AstNode> =
        serde_json::from_str(r#"[{"type":"CLOSE_BODY","params":{}}]"#).unwrap();
    assert!(ast_to_geometry(&unmatched).is_err());

    let missing_motor: Vec<AstNode> = serde_json::from_str(
        r#"[
          {"type":"STAGE","params":{"name":"No Motor"}},
          {"type":"BODY_TUBE","params":{"length":1.0,"radius":0.05}},
          {"type":"CLOSE_BODY","params":{}}
        ]"#,
    )
    .unwrap();
    assert!(ast_to_geometry(&missing_motor).is_err());

    let missing_body: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"No Body"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}"}}}}
        ]"#
    ))
    .unwrap();
    assert!(ast_to_geometry(&missing_body).is_err());
}

#[test]
fn rejects_motor_wider_than_body_tube() {
    // Regression for docs/organic_loop_report.md #3: the AST evolution loop
    // once discovered it could strap a physically oversized motor (N5800,
    // ~75mm case) into a 36mm-radius tube and score well on apogee, because
    // nothing checked motor diameter against tube diameter before simulating.
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Sustainer"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.2,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":0.5,"radius":0.018,"material":"cardboard"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");

    let result = evaluate_ast(
        "oversized-motor",
        &nodes,
        &curves(),
        &[],
        &json!({"target_apogee_m": 15_000.0}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(result.status, "failed");
    assert!(result.min_static_margin.is_finite());
    assert!(
        result.reason.contains("motor_oversized"),
        "expected motor_oversized rejection, got: {}",
        result.reason
    );
}

#[test]
fn rejects_clustered_motor_placed_outside_body_tube_by_radial_offset() {
    // Regression for a live-campaign bug (osifog_campaign_v7, main=H238T,
    // retro=F50T): the fit check compared only the motor's own radius
    // against the host tube's inner radius, never adding
    // `radial_offset_m` -- so a clustered/off-center mount (e.g. an
    // octaweb 3-ring main motor) could sit with its outer edge entirely
    // past the body tube's own wall and still pass. N5800 has a 98mm
    // (49mm-radius) case; a 100mm-radius body tube's ~98mm inner radius
    // comfortably fits the motor ON CENTER (radial_offset_m=0.0) but not
    // when it's pushed 60mm off-axis.
    let nodes: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Offset Cluster"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.3,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.10,"material":"cardboard"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}","role":"main","multiplicity":1,"radial_offset_m":0.06,"ignition":"automatic"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");

    let result = evaluate_ast(
        "offset-cluster",
        &nodes,
        &curves(),
        &[],
        &json!({"target_apogee_m": 3000.0}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(result.status, "failed");
    assert!(
        result.reason.contains("motor_oversized"),
        "expected motor_oversized rejection for a radially-offset motor exceeding the body tube, got: {}",
        result.reason
    );

    // Sanity: the SAME motor at radial_offset_m=0.0 (on-center) must still
    // fit inside the same body tube -- proves the rejection above is about
    // the radial offset, not the motor itself being too wide.
    let centered: Vec<AstNode> = serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Centered"}}}},
          {{"type":"NOSE_CONE","params":{{"length":0.3,"material":"cardboard"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.10,"material":"cardboard"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}","role":"main","multiplicity":1,"ignition":"automatic"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON");
    let centered_result = evaluate_ast(
        "centered-control",
        &centered,
        &curves(),
        &[],
        &json!({"target_apogee_m": 3000.0}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );
    assert!(
        !centered_result.reason.contains("motor_oversized"),
        "on-center motor should not be rejected as oversized, got: {}",
        centered_result.reason
    );
}

#[test]
fn evaluates_valid_ast_with_finite_proxy_metrics() {
    let nodes: Vec<AstNode> = serde_json::from_str(&one_stage_json()).expect("valid AST JSON");
    let result = evaluate_ast(
        "candidate-0",
        &nodes,
        &curves(),
        &[],
        &json!({"target_apogee_m": 15_000.0}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(result.id, "candidate-0");
    assert_eq!(result.status, "success");
    assert!(result.score.is_finite());
    assert!(result.apogee_m.is_finite());
    assert!(result.mach.is_finite());
}
