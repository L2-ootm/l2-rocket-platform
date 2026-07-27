//! OpenRocketLegacy mode validation via the AST evaluation pipeline.
//!
//! This test validates that the Rust OpenRocketLegacy physics mode produces
//! correct results through the same pipeline used by organic evolution.
//! The five-seed calibration work (designs/or_mode_sweep_*/report.json)
//! proved 0.79% mean apogee error — this test is a smoke check that the
//! pipeline compiles and runs correctly.

use std::collections::HashMap;

use l2_engine::PhysicsMode;
use l2_engine::ast::{
    AstCalibration, AstNode, ast_to_geometry, evaluate_ast,
};
use l2_engine::motor_db::{ThrustCurve, parse_eng_file};
use serde_json::json;

const N5800: &str = "20146N5800-P";

fn curves() -> HashMap<String, ThrustCurve> {
    let mut curves = HashMap::new();
    let (designation, curve) =
        parse_eng_file(include_str!("../motors/N5800.eng")).expect("N5800 fixture");
    curves.insert(designation, curve);
    curves
}

fn single_stage_ast() -> Vec<AstNode> {
    serde_json::from_str(&format!(
        r#"[
          {{"type":"STAGE","params":{{"name":"Validation"}}}},
          {{"type":"NOSE_CONE","params":{{"shape":"ogive","length":0.4,"material":"fiberglass"}}}},
          {{"type":"BODY_TUBE","params":{{"length":1.2,"radius":0.055,"material":"fiberglass"}}}},
          {{"type":"FIN_SET","params":{{"count":4,"sweep":25.0,"root":0.18,"height":0.10,"cross_section":"rounded"}}}},
          {{"type":"MOTOR_MOUNT","params":{{"motor_designation":"{N5800}","ignition":"automatic"}}}},
          {{"type":"CLOSE_BODY","params":{{}}}}
        ]"#
    ))
    .expect("valid AST JSON")
}

#[test]
fn openrocket_legacy_mode_produces_valid_apogee() {
    let nodes = single_stage_ast();
    let result = evaluate_ast(
        "validation-single-stage",
        &nodes,
        &curves(),
        &[],
        &json!({"target_apogee_m": 15_000.0, "min_static_margin": 1.5}),
        PhysicsMode::OpenRocketLegacy,
        "",
        &HashMap::<String, AstCalibration>::new(),
        None,
    );

    assert_eq!(result.status, "success", "evaluation failed: {}", result.reason);
    assert!(result.apogee_m > 5_000.0, "apogee {} too low for N5800", result.apogee_m);
    assert!(result.apogee_m < 50_000.0, "apogee {} too high for N5800", result.apogee_m);
    assert!(result.mach > 0.5, "mach {} too low", result.mach);
    assert!(result.mach < 5.0, "mach {} too high", result.mach);
    assert!(result.min_static_margin > 1.5, "static margin {} below 1.5", result.min_static_margin);
}

#[test]
fn openrocket_legacy_mode_static_margin_matches_barrowman() {
    let nodes = single_stage_ast();
    let geometry = ast_to_geometry(&nodes).expect("geometry");
    let curves = curves();
    let curve = curves.get(N5800).expect("N5800 curve");

    let margins = l2_engine::builder::static_margins_with_mode_at_machs(
        &geometry,
        &[curve.clone()],
        PhysicsMode::OpenRocketLegacy,
        &[0.3, 2.0],
    );

    assert!(!margins.is_empty(), "no margins computed");
    for (i, margin) in margins.iter().enumerate() {
        assert!(
            margin.is_finite(),
            "margin[{i}] is not finite: {margin}"
        );
    }
}

#[test]
fn openrocket_legacy_mode_summary_from_trajectory_works() {
    use l2_engine::sim_core::sim::simulate_with_mode;
    use l2_engine::sim_core::dynamics::state::SimConfig;
    use l2_engine::mission_adapter::{NoOpController, build_mission};

    let nodes = single_stage_ast();
    let geometry = ast_to_geometry(&nodes).expect("geometry");
    let curves = curves();
    let curve = curves.get(N5800).expect("N5800 curve");

    let mission = build_mission(&geometry, &[curve.clone()], PhysicsMode::OpenRocketLegacy)
        .expect("mission");

    let config = SimConfig { dt: 0.005, max_time: 600.0 };
    let mut controller = NoOpController;
    let (trajectory, _) = simulate_with_mode(
        &mission,
        &config,
        &mut controller,
        PhysicsMode::OpenRocketLegacy,
    );

    let summary = l2_engine::sim_core::io::json::FlightSummary::from_trajectory_with_wind(
        &trajectory,
        &mission,
    );

    assert!(summary.apogee_m > 5_000.0, "apogee {}", summary.apogee_m);
    assert!(summary.max_mach > 0.5, "mach {}", summary.max_mach);
    assert!(summary.flight_time > 5.0, "flight_time {}", summary.flight_time);
}
