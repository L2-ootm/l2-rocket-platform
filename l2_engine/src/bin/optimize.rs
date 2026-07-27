use anyhow::Context;
use l2_engine::geometry::RocketGeometry;
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;
use std::cmp::Ordering;
use std::path::Path;
use std::time::Instant;

const ORK_PATH: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../designs/optimized/L2_Hyper_Parallel_15K_Fixed.ork"
);

const N4800T_ENG: &str = include_str!("../../tests/fixtures/N4800T.eng");

#[derive(Debug, Clone)]
struct Individual {
    apogee: f64,
    mach: f64,
    nose_len_mult: f64,
    fin_size_mult: f64,
    body_len_mult: f64,
}

impl PartialEq for Individual {
    fn eq(&self, other: &Self) -> bool {
        self.apogee == other.apogee
    }
}
impl Eq for Individual {}

impl PartialOrd for Individual {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        // Reverse ordering so BinaryHeap acts as a min-heap,
        // allowing us to keep the N largest elements.
        other.apogee.partial_cmp(&self.apogee)
    }
}
impl Ord for Individual {
    fn cmp(&self, other: &Self) -> Ordering {
        self.partial_cmp(other).unwrap_or(Ordering::Equal)
    }
}

fn apply_mutation(rocket: &mut RocketGeometry, nose_mult: f64, fin_mult: f64, body_mult: f64) {
    for stage in &mut rocket.stages {
        // Mutate nosecone
        if let Some(nc) = &mut stage.nosecone {
            nc.length *= nose_mult;
        }

        // Mutate bodytubes
        for bt in &mut stage.bodytubes {
            bt.length *= body_mult;
        }

        // Mutate fins
        for finset in &mut stage.finsets {
            for pt in &mut finset.points {
                // Scale the fin shape (X and Y)
                pt.0 *= fin_mult;
                pt.1 *= fin_mult;
            }
        }
    }
}

fn main() -> anyhow::Result<()> {
    println!("Loading reference rocket...");
    let ork_path = Path::new(ORK_PATH);
    let xml =
        l2_engine::xml_parser::extract_ork_xml(ork_path).context("Failed to extract ORK zip")?;
    let base_rocket = l2_engine::xml_parser::parse_rocket_geometry(&xml)
        .context("Failed to parse base rocket")?;

    let num_simulations = 500;
    println!(
        "Starting exhaustive training run of {} parallel simulations (Target: 83456m)...",
        num_simulations
    );
    let start_time = Instant::now();

    let thrust_curve =
        l2_engine::motor_db::parse_eng(N4800T_ENG, "N4800T").context("Failed to parse motor")?;

    let mut all_results: Vec<Individual> = (0..num_simulations)
        .into_par_iter()
        .filter_map(|id| {
            let mut rng = StdRng::seed_from_u64(id as u64);

            let nose_mult = rng.gen_range(0.5..2.0);
            let fin_mult = rng.gen_range(0.5..1.5);
            let body_mult = rng.gen_range(0.7..1.3);

            let mut mutated_rocket = base_rocket.clone();
            apply_mutation(&mut mutated_rocket, nose_mult, fin_mult, body_mult);

            let curves = vec![thrust_curve.clone(); mutated_rocket.stages.len()];
            if let Ok(mission) = l2_engine::mission_adapter::build_mission(
                &mutated_rocket,
                &curves,
                l2_engine::PhysicsMode::HyperReal,
            ) {
                let config = l2_engine::sim_core::dynamics::state::SimConfig {
                    dt: 0.005,
                    max_time: 600.0,
                };
                let mut controller = l2_engine::mission_adapter::NoOpController;
                let (trajectory, _) =
                    l2_engine::sim_core::sim::simulate_with(&mission, &config, &mut controller);
                let summary =
                    l2_engine::sim_core::io::json::FlightSummary::from_trajectory(&trajectory, &mission);

                let apogee = summary.apogee_m;
                if apogee > 0.0 && !apogee.is_nan() {
                    return Some(Individual {
                        apogee,
                        mach: summary.max_mach,
                        nose_len_mult: nose_mult,
                        fin_size_mult: fin_mult,
                        body_len_mult: body_mult,
                    });
                }
            }
            None
        })
        .collect();

    let elapsed = start_time.elapsed();
    println!("---");
    println!("Training completed in {:.2?}", elapsed);
    println!(
        "Simulation rate: {:.0} sims/sec",
        (num_simulations as f64) / elapsed.as_secs_f64()
    );

    // Custom fitness: We want EXACTLY 83456m, and then max mach.
    // Score = - abs(apogee - 83456.0) + (mach * 10.0)
    all_results.sort_by(|a, b| {
        let score_a = -(a.apogee - 83456.0).abs() + (a.mach * 10.0);
        let score_b = -(b.apogee - 83456.0).abs() + (b.mach * 10.0);
        score_b.partial_cmp(&score_a).unwrap_or(Ordering::Equal)
    });

    println!("\nTop 10 Foguetes Otimizados para Alvo Exato 83456m:");
    for (i, r) in all_results.iter().take(10).enumerate() {
        println!(
            "#{}: Apogee {:.2} km (Err: {:.2}m) | Mach {:.2} | Nose x{:.4} | Fins x{:.4} | Body x{:.4}",
            i + 1,
            r.apogee / 1000.0,
            (r.apogee - 83456.0).abs(),
            r.mach,
            r.nose_len_mult,
            r.fin_size_mult,
            r.body_len_mult
        );
    }

    Ok(())
}
