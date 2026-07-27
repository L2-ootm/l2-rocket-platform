use thiserror::Error;

/// Top-level error type for the l2_engine crate.
///
/// Kept minimal by design (see 01-01-PLAN.md task 1) -- downstream plans add
/// variants as needed (XML parsing, Barrowman validation, motor-curve parsing,
/// etc.) rather than over-engineering the error surface before that code exists.
#[derive(Debug, Error)]
pub enum L2EngineError {
    #[error("parse error: {0}")]
    ParseError(String),

    #[error(transparent)]
    Io(#[from] std::io::Error),

    #[error(transparent)]
    Zip(#[from] zip::result::ZipError),
}
