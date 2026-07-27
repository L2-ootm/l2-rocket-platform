pub mod ast;
pub mod barrowman;
pub mod builder;
pub mod divergence;
pub mod errors;
pub mod geometry;
pub mod mass_calculator;
pub mod mission_adapter;
pub mod motor_db;
pub mod openrocket_nose;
pub mod sim_core;
pub mod xml_parser;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum PhysicsMode {
    #[default]
    OpenRocketLegacy,
    HyperReal,
}

pub use mission_adapter::simulate_rocket;
