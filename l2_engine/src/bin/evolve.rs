//! GA explorer -- Stage 1 of the hyper evolution pipeline
//! (docs/HYPER_EVOLUTION_PIPELINE.md §4). Explores the design genome against
//! the Rust proxy physics and exports the elite as `elite.json`, which
//! `l2_hyper/run_mission.py --seed-file` consumes for the OpenRocket polish.

use l2_engine::builder::{self, static_margins_with_mode};
use l2_engine::genome::{DesignGenome, EliteExport, EliteMember};
use l2_engine::mission_adapter::simulate_genome;
use l2_engine::motor_db::{self, ThrustCurve};
use l2_engine::PhysicsMode;
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;
use serde::Deserialize;
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

const REQ_MARGINS: [f64; 3] = [0.5, 3.2, 6.5];

#[derive(Deserialize, Debug, Clone)]
struct Objective {
    metric: String,
    kind: String,
    value: Option<f64>,
    #[allow(dead_code)]
    tolerance: Option<f64>,
    weight: Option<f64>,
    scale: Option<f64>,
}

#[derive(Deserialize, Debug, Clone)]
struct Constraints {
    #[allow(dead_code)]
    min_static_margin: f64,
}

#[derive(Deserialize, Debug, Clone)]
struct MotorSpec {
    #[allow(dead_code)]
    manufacturer: String,
    designation: String,
}

#[derive(Deserialize, Debug, Clone)]
struct StackStage {
    #[allow(dead_code)]
    name: Option<String>,
    #[allow(dead_code)]
    body_radius: f64,
    motor: MotorSpec,
}

fn default_constraints() -> Constraints {
    Constraints { min_static_margin: 1.5 }
}

fn default_penalty() -> f64 {
    0.05
}

#[derive(Deserialize, Debug, Clone)]
struct MissionSpec {
    #[serde(default)]
    stack: Vec<StackStage>,
    objectives: Vec<Objective>,
    #[serde(default = "default_constraints")]
    #[allow(dead_code)]
    constraints: Constraints,
    #[serde(default = "default_penalty", rename = "stability_penalty")]
    penalty: f64,
}

fn validate_supported_stack(mission: &MissionSpec) {
    let expected = ["M2245", "N5800", "O8000"];
    let actual: Vec<&str> = mission
        .stack
        .iter()
        .map(|stage| stage.motor.designation.as_str())
        .collect();

    if actual != expected {
        panic!(
            "l2_engine evolve currently supports the fixed 3-stage stack {:?} \
             (top-to-bottom mission order), got {:?}. Use l2_hyper/OpenRocket \
             for this mission until the Rust builder is made mission-stack dynamic.",
            expected, actual
        );
    }
}

fn load_motor_curves() -> Vec<ThrustCurve> {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("motors");
    ["O8000", "N5800", "M2245"]
        .iter()
        .map(|name| {
            let text = fs::read_to_string(base.join(format!("{name}.eng")))
                .unwrap_or_else(|e| panic!("missing motors/{name}.eng: {e}"));
            motor_db::parse_eng(&text, name)
                .unwrap_or_else(|e| panic!("bad motors/{name}.eng: {e:?}"))
        })
        .collect()
}

fn evaluate(
    genome: &DesignGenome,
    curves: &[ThrustCurve],
    mission: &MissionSpec,
    physics_mode: PhysicsMode,
) -> (f64, EliteMember) {
    let geometry = builder::build_geometry(genome);
    let margins = static_margins_with_mode(&geometry, curves, physics_mode);
    let worst_ratio = margins
        .iter()
        .zip(REQ_MARGINS.iter())
        .map(|(m, req)| m / req)
        .fold(f64::INFINITY, f64::min);
    let min_margin = margins.iter().cloned().fold(f64::INFINITY, f64::min);

    let (apogee, mach) = match simulate_genome(genome, curves, physics_mode) {
        Ok(summary) => (summary.apogee_m.max(0.0), summary.max_mach.max(0.0)),
        Err(_) => (0.0, 0.0),
    };

    let get_metric = |m: &str| match m {
        "apogee" => apogee,
        "mach" => mach,
        _ => 0.0,
    };

    let mut score = 0.0;
    for o in &mission.objectives {
        let x = get_metric(&o.metric);
        let w = o.weight.unwrap_or(1.0);
        let v = o.value.unwrap_or(1.0);
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
                if x > 0.0 {
                    score += w * (v / x).min(1.0);
                } else {
                    score += w;
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

    if worst_ratio < 1.0 {
        let ratio = mission.penalty + (1.0 - mission.penalty) * worst_ratio.max(0.0);
        if score > 0.0 {
            score *= ratio;
        } else {
            score *= 1.0 / ratio.max(0.01);
        }
    }

    (
        score,
        EliteMember {
            genome: *genome,
            rust_apogee_m: apogee,
            rust_mach: mach,
            rust_static_margin_min: min_margin,
            rust_score: score,
        },
    )
}

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1).cloned())
}

fn physics_mode_arg(args: &[String]) -> PhysicsMode {
    match arg(args, "--physics").as_deref() {
        Some("openrocket") | Some("or") | Some("openrocket-legacy") => PhysicsMode::OpenRocketLegacy,
        Some("hyperreal") | Some("real") | None => PhysicsMode::HyperReal,
        Some(other) => panic!("unknown --physics '{other}' (expected hyperreal|openrocket)"),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let pop_size: usize = arg(&args, "--pop").and_then(|v| v.parse().ok()).unwrap_or(300);
    let max_gens: usize = arg(&args, "--gens").and_then(|v| v.parse().ok()).unwrap_or(40);
    let out = arg(&args, "--out").unwrap_or_else(|| "elite.json".to_string());
    let seed: u64 = arg(&args, "--seed").and_then(|v| v.parse().ok()).unwrap_or(42);
    let physics_mode = physics_mode_arg(&args);

    let curves = load_motor_curves();

    if let Some(path) = arg(&args, "--probe") {
        let export: EliteExport =
            serde_json::from_str(&fs::read_to_string(&path).expect("probe file")).expect("probe json");
        for (i, member) in export.elite.iter().enumerate() {
            let geometry = builder::build_geometry(&member.genome);
            let margins = static_margins_with_mode(&geometry, &curves, physics_mode);
            let sim = simulate_genome(&member.genome, &curves, physics_mode);
            let (apo, mach) = sim.map(|s| (s.apogee_m, s.max_mach)).unwrap_or((0.0, 0.0));
            println!(
                "elite[{i:02}] margins {:?} | apogee {:.1} km | Mach {:.2}",
                margins.iter().map(|m| (m * 100.0).round() / 100.0).collect::<Vec<_>>(),
                apo / 1000.0,
                mach
            );
        }
        return;
    }

    let mission_file = arg(&args, "--mission").expect("--mission <file.json> required");
    let mission_json = fs::read_to_string(&mission_file).expect("read mission");
    let mission: MissionSpec = serde_json::from_str(&mission_json).expect("parse mission");
    validate_supported_stack(&mission);

    let seed_genome: Option<DesignGenome> = arg(&args, "--seed-genome").and_then(|v| {
        serde_json::from_str::<DesignGenome>(&v)
            .or_else(|_| {
                fs::read_to_string(&v)
                    .ok()
                    .and_then(|s| serde_json::from_str::<DesignGenome>(&s).ok())
                    .ok_or_else(|| serde_json::from_str::<DesignGenome>("null").unwrap_err())
            })
            .ok()
    });

    println!("[evolve] pop={pop_size} gens={max_gens} seed={seed} physics={physics_mode:?} req_margins={REQ_MARGINS:?}cal");
    let elite_count = ((pop_size as f64) * 0.05).ceil().max(1.0) as usize;
    let mut rng = StdRng::seed_from_u64(seed);

    let mut population: Vec<DesignGenome> =
        (0..pop_size).map(|_| DesignGenome::random(&mut rng)).collect();

    if let Some(g) = seed_genome {
        println!("[evolve] injecting --seed-genome into gen-0");
        population[0] = g;
    }

    let mut best_score = f64::NEG_INFINITY;
    let mut stagnant = 0usize;
    let mut evaluated: Vec<(f64, EliteMember)> = Vec::new();
    let t0 = Instant::now();

    for generation in 0..max_gens {
        let start = Instant::now();
        evaluated = population
            .par_iter()
            .map(|g| evaluate(g, &curves, &mission, physics_mode))
            .collect();
        evaluated.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

        let top = &evaluated[0];
        let mean: f64 = evaluated.iter().map(|e| e.0).sum::<f64>() / evaluated.len() as f64;
        println!(
            "gen {generation:02} | best {:.4} (apogee {:7.1} km, Mach {:.2}, margin {:+.2} cal) | mean {:.4} | {:.1}s",
            top.0,
            top.1.rust_apogee_m / 1000.0,
            top.1.rust_mach,
            top.1.rust_static_margin_min,
            mean,
            start.elapsed().as_secs_f64()
        );

        if top.0 <= best_score + 1e-9 {
            stagnant += 1;
            if stagnant >= 8 {
                println!("[evolve] early stop: stagnated {stagnant} generations");
                break;
            }
        } else {
            best_score = top.0;
            stagnant = 0;
        }

        let mut next: Vec<DesignGenome> = evaluated
            .iter()
            .take(elite_count)
            .map(|e| e.1.genome)
            .collect();
        while next.len() < pop_size {
            let pick = |rng: &mut StdRng| -> DesignGenome {
                let best = (0..3)
                    .map(|_| rng.gen_range(0..evaluated.len()))
                    .max_by(|&a, &b| {
                        evaluated[a].0.partial_cmp(&evaluated[b].0).unwrap_or(std::cmp::Ordering::Equal)
                    })
                    .unwrap();
                evaluated[best].1.genome
            };
            let (p1, p2) = (pick(&mut rng), pick(&mut rng));
            next.push(DesignGenome::crossover(&p1, &p2, &mut rng).mutate(&mut rng, 0.15));
        }
        population = next;
    }

    let mut elite: Vec<EliteMember> = Vec::new();
    for (_, member) in &evaluated {
        if elite.len() >= 16 {
            break;
        }
        let distinct = elite.iter().all(|e| {
            let a = e.genome.to_array();
            let b = member.genome.to_array();
            let d2: f64 = a
                .iter()
                .zip(b.iter())
                .zip(l2_engine::genome::BOUNDS.iter())
                .map(|((x, y), (lo, hi))| ((x - y) / (hi - lo)).powi(2))
                .sum();
            d2.sqrt() > 0.15
        });
        if distinct {
            elite.push(member.clone());
        }
    }
    let export = EliteExport {
        generated_by: format!("l2_engine evolve v1 (pop={pop_size}, gens={max_gens}, seed={seed}, physics={physics_mode:?})"),
        fitness_def: "dynamic from mission JSON objectives".to_string(),
        elite,
    };
    fs::write(&out, serde_json::to_string_pretty(&export).unwrap())
        .unwrap_or_else(|e| panic!("cannot write {out}: {e}"));
    println!(
        "[evolve] done in {:.1}s -- top16 -> {out} (best: apogee {:.1} km, Mach {:.2}, margin {:+.2} cal)",
        t0.elapsed().as_secs_f64(),
        evaluated[0].1.rust_apogee_m / 1000.0,
        evaluated[0].1.rust_mach,
        evaluated[0].1.rust_static_margin_min
    );
}
