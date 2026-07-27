//! `cargo run` entry point — the Phase 1 checkpoint (see 01-CONTEXT.md's
//! `<specifics>` block and 01-08-PLAN.md). Loads the reference 2-stage
//! vehicle (`L2_Hyper_Parallel_15K.ork`), runs the full native Rust
//! simulation (no Python/JVM/GUI), and prints Altitude/Mach to the
//! terminal.
//!
//! The `.ork` path is resolved via `CARGO_MANIFEST_DIR` (not a relative
//! path) so this binary produces the same result regardless of the
//! invocation working directory -- `cargo run --manifest-path
//! l2_engine/Cargo.toml` may be invoked from the repo root, not from
//! inside `l2_engine/`.

use std::path::Path;

/// Reference vehicle's `.ork` file, resolved relative to this crate's
/// manifest directory so invocation cwd never matters.
const ORK_PATH: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../designs/optimized/L2_Hyper_Parallel_15K.ork"
);

/// Both stages of the reference vehicle use the same N4800T motor.
/// Embedded at compile time so `cargo run` needs no jar/network lookup.
const N4800T_ENG: &str = include_str!("../tests/fixtures/N4800T.eng");

fn main() -> anyhow::Result<()> {
    let ork_path = Path::new(ORK_PATH);

    let summary = l2_engine::simulate_rocket(
        ork_path,
        N4800T_ENG,
        "N4800T",
        l2_engine::PhysicsMode::HyperReal,
    )
    .expect("simulate_rocket failed");

    println!("Altitude: {:.2} km", summary.apogee_m / 1000.0);
    println!("Mach: {:.3}", summary.max_mach);

    Ok(())
}
