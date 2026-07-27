use std::fs;
use std::io::{self, Read};

use l2_engine::divergence::{CalibrationSample, DivergenceModel, RidgeConfig};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct FitRequest {
    #[serde(default)]
    model: Option<DivergenceModel>,
    #[serde(default)]
    config: Option<RidgeConfig>,
    #[serde(default)]
    samples: Vec<CalibrationSample>,
}

#[derive(Serialize)]
struct FitResponse {
    sample_count: usize,
    model: DivergenceModel,
}

fn main() {
    let args = std::env::args().collect::<Vec<_>>();
    let input = argument(&args, "--input")
        .map(|path| fs::read_to_string(path).expect("read --input file"))
        .unwrap_or_else(read_stdin);
    let request: FitRequest = serde_json::from_str(&input).expect("parse fit request");
    let mut model = request
        .model
        .unwrap_or_else(|| DivergenceModel::new(request.config.unwrap_or_default()).expect("config"));
    model.update(&request.samples).expect("fit divergence model");
    println!(
        "{}",
        serde_json::to_string(&FitResponse {
            sample_count: model.sample_count(),
            model,
        })
        .expect("serialize fit response")
    );
}

fn argument(args: &[String], flag: &str) -> Option<String> {
    args.windows(2)
        .find(|pair| pair[0] == flag)
        .map(|pair| pair[1].clone())
}

fn read_stdin() -> String {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).expect("read stdin");
    input
}
