//! Proof-of-integration for the `rocket-sim` vendoring strategy.
//!
//! This is NOT the phase's real validation target (that is Plan 08, which
//! validates against the actual `L2_Hyper_Parallel_15K.ork` reference file).
//! This example only proves that the path dependency into
//! `l2_engine_base/rocket-sim` resolves, module paths match, and the public
//! API surface (StageBuilder/MissionBuilder presets, sim::simulate,
//! FlightSummary) is usable end-to-end -- mirroring rocket-sim's own
//! `examples/pathfinder.rs` pattern -- before any Barrowman/parsing work
//! begins.

use l2_engine::sim_core::dynamics::state::SimConfig;
use l2_engine::sim_core::io::json::FlightSummary;
use l2_engine::sim_core::sim;
use l2_engine::sim_core::vehicle::presets;

fn main() {
    let mission = presets::pathfinder();
    let config = SimConfig {
        dt: 0.005,
        max_time: 600.0,
    };

    let (trajectory, _) = sim::simulate(&mission, &config);
    let summary = FlightSummary::from_trajectory(&trajectory, &mission);

    let apogee_km = summary.apogee_m / 1000.0;

    println!("Apogee: {apogee_km:.1} km");
    println!("Max Mach: {:.2}", summary.max_mach);

    assert!(
        apogee_km.is_finite() && apogee_km > 0.0,
        "vendoring check failed: apogee is not a positive, finite value"
    );
    assert!(
        summary.max_mach.is_finite() && summary.max_mach > 0.0,
        "vendoring check failed: max_mach is not a positive, finite value"
    );
}
