use std::collections::HashMap;
use std::fs;
use std::io::{self, BufRead, Read, Write};
use std::path::PathBuf;

use l2_engine::ast::{AstEvalBatch, AstEvalBatchOutput, evaluate_ast_with_profile};
use l2_engine::motor_db::{self, ThrustCurve};
use rayon::prelude::*;

fn main() {
    configure_rayon();

    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|value| value == "--capabilities") {
        println!("{}", r#"{"protocols":["one-shot-v1","jsonl-v1"]}"#);
        return;
    }
    let serve = args.iter().any(|value| value == "--serve");
    if serve && arg(&args, "--input").is_some() {
        eprintln!("--serve and --input are mutually exclusive");
        std::process::exit(2);
    }
    let curves = load_motor_curves();
    if serve {
        serve_jsonl(&curves);
        return;
    }
    let input = arg(&args, "--input")
        .map(|path| fs::read_to_string(path).expect("read --input file"))
        .unwrap_or_else(read_stdin);
    let batch: AstEvalBatch = serde_json::from_str(&input).expect("parse AST eval batch JSON");
    let output = evaluate_batch(batch, &curves).expect("evaluate AST batch");
    println!("{}", serde_json::to_string(&output).expect("serialize ast_eval output"));
}

fn evaluate_batch(
    batch: AstEvalBatch,
    curves: &HashMap<String, ThrustCurve>,
) -> Result<AstEvalBatchOutput, String> {
    let target_apogee_m = batch.resolved_target_apogee_m();
    let physics_mode = batch.resolved_physics_mode()?;
    let execution_profile = batch
        .resolved_execution_profile()
        ?;
    let mut constraints = batch.constraints.clone();
    if !constraints.is_object() {
        constraints = serde_json::json!({});
    }
    constraints
        .as_object_mut()
        .expect("constraints object")
        .entry("target_apogee_m".to_string())
        .or_insert_with(|| serde_json::json!(target_apogee_m));
    constraints
        .as_object_mut()
        .expect("constraints object")
        .entry("phase_machs".to_string())
        .or_insert_with(|| serde_json::json!(batch.resolved_phase_machs()));
    let results = batch
        .candidates
        .par_iter()
        .map(|candidate| {
            evaluate_ast_with_profile(
                &candidate.id,
                &candidate.ast,
                curves,
                &batch.objectives,
                &constraints,
                physics_mode,
                execution_profile,
                &candidate.signature,
                &batch.calibrations,
                batch.divergence_model.as_ref(),
                candidate.environment.clone(),
            )
        })
        .collect();

    Ok(AstEvalBatchOutput { results })
}

fn serve_jsonl(curves: &HashMap<String, ThrustCurve>) {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(line) => line,
            Err(error) => {
                eprintln!("read JSONL request: {error}");
                break;
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<AstEvalBatch>(&line) {
            Ok(batch) => match evaluate_batch(batch, curves) {
                Ok(output) => serde_json::to_value(output).expect("serialize AST batch"),
                Err(message) => serde_json::json!({"error":{"code":"invalid_batch","message":message}}),
            },
            Err(error) => serde_json::json!({"error":{"code":"invalid_json","message":error.to_string()}}),
        };
        if serde_json::to_writer(&mut stdout, &response).is_err()
            || stdout.write_all(b"\n").is_err()
            || stdout.flush().is_err()
        {
            break;
        }
    }
}

fn configure_rayon() {
    if std::env::var("RAYON_NUM_THREADS").is_ok() {
        return;
    }
    let logical = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1);
    let capped = ((logical as f64) * 0.70).floor().max(1.0) as usize;
    let _ = rayon::ThreadPoolBuilder::new()
        .num_threads(capped)
        .build_global();
}

/// Loads every real motor available to the organic-evolution engine by
/// scanning `l2_engine/motors/*.eng` -- no hardcoded motor list. Adding a
/// motor (e.g. via `extract_motors.py` against OpenRocket's own bundled
/// `initial_motors.db`) makes it usable by the GA with zero Rust changes.
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
