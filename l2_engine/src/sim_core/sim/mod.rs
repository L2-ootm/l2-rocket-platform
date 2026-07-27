pub mod adaptive;
pub mod axial;
pub mod event;
pub mod integrator;
pub mod runner;

pub use axial::{AxialConfig, AxialError, simulate_axial};
pub use integrator::rk4_step;
pub use runner::{
    simulate, simulate_summary_with_mode, simulate_summary_with_mode_gated, simulate_with,
    simulate_with_mode,
};
