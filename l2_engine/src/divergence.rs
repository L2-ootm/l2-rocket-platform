//! Characteristic-aware correction model for Rust/OpenRocket divergence.
//!
//! The feature order is deliberately fixed so calibration artifacts remain
//! portable across runs. Callers own the semantics of the 25 entries, but must
//! use the same order when training and predicting.

use nalgebra::{DMatrix, DVector};
use serde::{Deserialize, Serialize};

use crate::geometry::{NoseShape, RocketGeometry};
use crate::motor_db::ThrustCurve;
use crate::sim_core::io::json::FlightSummary;

pub const FEATURE_COUNT: usize = 25;
const COEFFICIENT_COUNT: usize = FEATURE_COUNT + 1; // intercept + features

/// Dense, persistence-friendly feature vector with a stable dimensionality.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct DivergenceFeatures(pub [f64; FEATURE_COUNT]);

impl DivergenceFeatures {
    pub fn is_finite(&self) -> bool {
        self.0.iter().all(|value| value.is_finite())
    }
}

/// Stable feature extraction contract used by persisted calibration models.
///
/// Indices: impulse, peak thrust, burn time, motor diameter, motor mass,
/// length, body radius, nose length, nose-shape code, fin count, root chord,
/// fin height, fin sweep, fin cross-section code, wet mass, payload mass,
/// propellant fraction, structure fraction, stage count, impulse ratio,
/// predicted apogee, Mach, max acceleration g, max speed, flight time.
pub fn extract_features(
    geometry: &RocketGeometry,
    curves: &[ThrustCurve],
    summary: &FlightSummary,
) -> DivergenceFeatures {
    let impulses = curves.iter().map(ThrustCurve::total_impulse).collect::<Vec<_>>();
    let total_impulse = impulses.iter().sum::<f64>();
    let peak_thrust = curves
        .iter()
        .flat_map(|curve| curve.thrust_n.iter().copied())
        .fold(0.0_f64, f64::max);
    let total_burn_time = curves
        .iter()
        .map(|curve| {
            curve.time_s.last().copied().unwrap_or(0.0)
                - curve.time_s.first().copied().unwrap_or(0.0)
        })
        .map(|duration| duration.max(0.0))
        .sum::<f64>();
    let motor_diameter = curves.iter().map(|curve| curve.diameter_m).fold(0.0, f64::max);
    let motor_mass = curves.iter().map(|curve| curve.total_mass_kg).sum::<f64>();
    let propellant_mass = curves
        .iter()
        .map(|curve| curve.propellant_mass_kg)
        .sum::<f64>();

    let mut total_length = 0.0_f64;
    let mut radius_sum = 0.0_f64;
    let mut radius_count = 0_usize;
    let mut nose_length = 0.0_f64;
    let mut nose_shape_sum = 0.0_f64;
    let mut nose_count = 0_usize;
    let mut fin_count = 0_u32;
    let mut root_chord_sum = 0.0_f64;
    let mut fin_height_sum = 0.0_f64;
    let mut fin_sweep_sum = 0.0_f64;
    let mut fin_cross_section_sum = 0.0_f64;
    let mut finset_count = 0_usize;
    let mut payload_mass = 0.0_f64;
    let mut wet_mass = 0.0_f64;

    for stage in &geometry.stages {
        let stage_end = stage
            .bodytubes
            .iter()
            .map(|tube| tube.axial_offset_m + tube.length)
            .chain(
                stage
                    .nosecone
                    .iter()
                    .map(|nose| nose.axial_offset_m + nose.length),
            )
            .fold(0.0_f64, f64::max);
        total_length += stage_end;
        wet_mass += crate::mass_calculator::total_mass(stage, 0.0);
        payload_mass += stage.point_masses.iter().map(|mass| mass.mass_kg).sum::<f64>();
        for tube in &stage.bodytubes {
            radius_sum += tube.radius;
            radius_count += 1;
        }
        if let Some(nose) = &stage.nosecone {
            nose_length += nose.length;
            nose_shape_sum += match nose.shape {
                NoseShape::Conical => 0.0,
                NoseShape::Ogive => 1.0,
                NoseShape::VonKarmanHaack => 2.0,
                NoseShape::Ellipsoid => 3.0,
                NoseShape::PowerSeries => 4.0,
                NoseShape::Parabolic => 5.0,
            };
            nose_count += 1;
        }
        for fins in &stage.finsets {
            fin_count += fins.fin_count;
            if !fins.points.is_empty() {
                let min_x = fins.points.iter().map(|point| point.0).fold(f64::INFINITY, f64::min);
                let max_x = fins
                    .points
                    .iter()
                    .map(|point| point.0)
                    .fold(f64::NEG_INFINITY, f64::max);
                root_chord_sum += (max_x - min_x).max(0.0);
                fin_height_sum += fins
                    .points
                    .iter()
                    .map(|point| point.1.abs())
                    .fold(0.0_f64, f64::max);
                fin_sweep_sum += fins
                    .points
                    .iter()
                    .max_by(|left, right| left.1.abs().total_cmp(&right.1.abs()))
                    .map(|point| point.0 - min_x)
                    .unwrap_or(0.0);
            }
            fin_cross_section_sum += match fins.cross_section.as_str() {
                "airfoil" => 0.0,
                "rounded" => 1.0,
                "square" => 2.0,
                _ => 3.0,
            };
            finset_count += 1;
        }
    }

    let min_impulse = impulses
        .iter()
        .copied()
        .filter(|value| *value > 0.0)
        .fold(f64::INFINITY, f64::min);
    let max_impulse = impulses.iter().copied().fold(0.0_f64, f64::max);
    let impulse_ratio = if min_impulse.is_finite() {
        max_impulse / min_impulse
    } else {
        0.0
    };
    let propellant_fraction = propellant_mass / wet_mass.max(1.0e-12);

    DivergenceFeatures([
        total_impulse,
        peak_thrust,
        total_burn_time,
        motor_diameter,
        motor_mass,
        total_length,
        radius_sum / radius_count.max(1) as f64,
        nose_length,
        nose_shape_sum / nose_count.max(1) as f64,
        fin_count as f64,
        root_chord_sum / finset_count.max(1) as f64,
        fin_height_sum / finset_count.max(1) as f64,
        fin_sweep_sum / finset_count.max(1) as f64,
        fin_cross_section_sum / finset_count.max(1) as f64,
        wet_mass,
        payload_mass,
        propellant_fraction,
        1.0 - propellant_fraction,
        geometry.stages.len() as f64,
        impulse_ratio,
        summary.apogee_m,
        summary.max_mach,
        summary.max_accel_g,
        summary.max_speed,
        summary.flight_time,
    ])
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CalibrationSample {
    pub features: DivergenceFeatures,
    /// OpenRocket apogee minus the uncorrected Rust apogee, in metres.
    pub apogee_correction_m: f64,
    /// OpenRocket maximum Mach minus the uncorrected Rust maximum Mach.
    pub mach_correction: f64,
}

impl CalibrationSample {
    fn is_finite(&self) -> bool {
        self.features.is_finite()
            && self.apogee_correction_m.is_finite()
            && self.mach_correction.is_finite()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct RidgeConfig {
    /// L2 penalty applied to slopes. The intercept is never regularized.
    pub lambda: f64,
    /// Number of observations at which sample confidence reaches 50%.
    pub confidence_half_samples: f64,
    /// Extrapolation distance (normalized RMS) at which confidence is `e^-1`.
    pub confidence_distance_scale: f64,
}

impl Default for RidgeConfig {
    fn default() -> Self {
        Self {
            lambda: 1.0,
            confidence_half_samples: 12.0,
            confidence_distance_scale: 3.0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct DivergencePrediction {
    pub apogee_correction_m: f64,
    pub mach_correction: f64,
    /// `[0, 1]`; combines calibration-set size and extrapolation distance.
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DivergenceError {
    InvalidConfig,
    InvalidSample { index: usize },
    InvalidFeatures,
    SingularSystem,
}

/// Ridge regression with training-set normalization and two correction heads.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DivergenceModel {
    config: RidgeConfig,
    mean: [f64; FEATURE_COUNT],
    scale: [f64; FEATURE_COUNT],
    apogee_coefficients: Vec<f64>,
    mach_coefficients: Vec<f64>,
    samples: Vec<CalibrationSample>,
    trained: bool,
}

impl DivergenceModel {
    pub fn new(config: RidgeConfig) -> Result<Self, DivergenceError> {
        if !config.lambda.is_finite()
            || config.lambda < 0.0
            || !config.confidence_half_samples.is_finite()
            || config.confidence_half_samples <= 0.0
            || !config.confidence_distance_scale.is_finite()
            || config.confidence_distance_scale <= 0.0
        {
            return Err(DivergenceError::InvalidConfig);
        }
        Ok(Self {
            config,
            mean: [0.0; FEATURE_COUNT],
            scale: [1.0; FEATURE_COUNT],
            apogee_coefficients: vec![0.0; COEFFICIENT_COUNT],
            mach_coefficients: vec![0.0; COEFFICIENT_COUNT],
            samples: Vec::new(),
            trained: false,
        })
    }

    pub fn sample_count(&self) -> usize {
        self.samples.len()
    }

    pub fn fit(&mut self, samples: &[CalibrationSample]) -> Result<(), DivergenceError> {
        self.samples.clear();
        self.samples.extend_from_slice(samples);
        self.refit()
    }

    /// Adds newly observed OpenRocket calibrations and refits the small model.
    pub fn update(&mut self, new_samples: &[CalibrationSample]) -> Result<(), DivergenceError> {
        let base = self.samples.len();
        if let Some(offset) = new_samples.iter().position(|sample| !sample.is_finite()) {
            return Err(DivergenceError::InvalidSample {
                index: base + offset,
            });
        }
        self.samples.extend_from_slice(new_samples);
        self.refit()
    }

    pub fn predict(
        &self,
        features: &DivergenceFeatures,
    ) -> Result<DivergencePrediction, DivergenceError> {
        if !features.is_finite() {
            return Err(DivergenceError::InvalidFeatures);
        }
        if !self.trained {
            return Ok(DivergencePrediction {
                apogee_correction_m: 0.0,
                mach_correction: 0.0,
                confidence: 0.0,
            });
        }

        let mut apogee = self.apogee_coefficients[0];
        let mut mach = self.mach_coefficients[0];
        let mut squared_distance = 0.0;
        for index in 0..FEATURE_COUNT {
            let normalized = (features.0[index] - self.mean[index]) / self.scale[index];
            apogee += self.apogee_coefficients[index + 1] * normalized;
            mach += self.mach_coefficients[index + 1] * normalized;
            squared_distance += normalized * normalized;
        }
        let rms_distance = (squared_distance / FEATURE_COUNT as f64).sqrt();
        let n = self.samples.len() as f64;
        let sample_confidence = n / (n + self.config.confidence_half_samples);
        let extrapolation_confidence =
            (-rms_distance / self.config.confidence_distance_scale).exp();

        Ok(DivergencePrediction {
            apogee_correction_m: apogee,
            mach_correction: mach,
            confidence: (sample_confidence * extrapolation_confidence).clamp(0.0, 1.0),
        })
    }

    fn refit(&mut self) -> Result<(), DivergenceError> {
        if let Some(index) = self.samples.iter().position(|sample| !sample.is_finite()) {
            return Err(DivergenceError::InvalidSample { index });
        }
        if self.samples.is_empty() {
            self.trained = false;
            self.apogee_coefficients.fill(0.0);
            self.mach_coefficients.fill(0.0);
            return Ok(());
        }

        let n = self.samples.len();
        for feature in 0..FEATURE_COUNT {
            self.mean[feature] = self
                .samples
                .iter()
                .map(|sample| sample.features.0[feature])
                .sum::<f64>()
                / n as f64;
            let variance = self
                .samples
                .iter()
                .map(|sample| {
                    let delta = sample.features.0[feature] - self.mean[feature];
                    delta * delta
                })
                .sum::<f64>()
                / n as f64;
            // Constant and numerically negligible columns stay harmlessly zero.
            self.scale[feature] = if variance > 1.0e-24 {
                variance.sqrt()
            } else {
                1.0
            };
        }

        let mut design = DMatrix::zeros(n, COEFFICIENT_COUNT);
        let mut apogee = DVector::zeros(n);
        let mut mach = DVector::zeros(n);
        for (row, sample) in self.samples.iter().enumerate() {
            design[(row, 0)] = 1.0;
            for feature in 0..FEATURE_COUNT {
                design[(row, feature + 1)] =
                    (sample.features.0[feature] - self.mean[feature]) / self.scale[feature];
            }
            apogee[row] = sample.apogee_correction_m;
            mach[row] = sample.mach_correction;
        }

        let transpose = design.transpose();
        let mut normal = &transpose * &design;
        for diagonal in 1..COEFFICIENT_COUNT {
            normal[(diagonal, diagonal)] += self.config.lambda;
        }
        let rhs_apogee = &transpose * apogee;
        let rhs_mach = &transpose * mach;
        let svd = normal.svd(true, true);
        let apogee_solution = svd
            .solve(&rhs_apogee, 1.0e-12)
            .map_err(|_| DivergenceError::SingularSystem)?;
        let mach_solution = svd
            .solve(&rhs_mach, 1.0e-12)
            .map_err(|_| DivergenceError::SingularSystem)?;
        self.apogee_coefficients
            .copy_from_slice(apogee_solution.as_slice());
        self.mach_coefficients
            .copy_from_slice(mach_solution.as_slice());
        self.trained = true;
        Ok(())
    }
}

impl Default for DivergenceModel {
    fn default() -> Self {
        Self::new(RidgeConfig::default()).expect("default ridge configuration is valid")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(x: f64) -> CalibrationSample {
        let mut features = [0.0; FEATURE_COUNT];
        features[0] = x;
        features[1] = x * x;
        CalibrationSample {
            features: DivergenceFeatures(features),
            apogee_correction_m: 30.0 + 4.0 * x - 0.5 * x * x,
            mach_correction: -0.02 + 0.003 * x,
        }
    }

    #[test]
    fn recovers_exact_synthetic_relationship() {
        let samples: Vec<_> = (-10..=10).map(|x| sample(x as f64)).collect();
        let mut model = DivergenceModel::new(RidgeConfig {
            lambda: 0.0,
            ..RidgeConfig::default()
        })
        .unwrap();
        model.fit(&samples).unwrap();
        let prediction = model.predict(&sample(2.5).features).unwrap();
        assert!((prediction.apogee_correction_m - 36.875).abs() < 1.0e-7);
        assert!((prediction.mach_correction + 0.0125).abs() < 1.0e-9);
    }

    #[test]
    fn regularization_shrinks_prediction_toward_intercept() {
        let samples: Vec<_> = (-2..=2).map(|x| sample(x as f64)).collect();
        let mut loose = DivergenceModel::new(RidgeConfig {
            lambda: 0.0,
            ..RidgeConfig::default()
        })
        .unwrap();
        let mut tight = DivergenceModel::new(RidgeConfig {
            lambda: 1.0e6,
            ..RidgeConfig::default()
        })
        .unwrap();
        loose.fit(&samples).unwrap();
        tight.fit(&samples).unwrap();
        let query = sample(2.0).features;
        let loose_delta = (loose.predict(&query).unwrap().apogee_correction_m - 29.0).abs();
        let tight_delta = (tight.predict(&query).unwrap().apogee_correction_m - 29.0).abs();
        assert!(tight_delta < loose_delta);
    }

    #[test]
    fn confidence_increases_with_samples_and_falls_outside_training_domain() {
        let mut small = DivergenceModel::default();
        let mut large = DivergenceModel::default();
        small.fit(&[sample(-1.0), sample(1.0)]).unwrap();
        let samples: Vec<_> = (-10..=10).map(|x| sample(x as f64 / 5.0)).collect();
        large.fit(&samples).unwrap();
        let origin = DivergenceFeatures([0.0; FEATURE_COUNT]);
        assert!(
            large.predict(&origin).unwrap().confidence > small.predict(&origin).unwrap().confidence
        );
        let mut distant = origin;
        distant.0[0] = 100.0;
        assert!(
            large.predict(&distant).unwrap().confidence
                < large.predict(&origin).unwrap().confidence
        );
    }

    #[test]
    fn handles_empty_and_rejects_non_finite_inputs() {
        let mut model = DivergenceModel::default();
        model.fit(&[]).unwrap();
        assert_eq!(
            model
                .predict(&DivergenceFeatures([0.0; FEATURE_COUNT]))
                .unwrap()
                .confidence,
            0.0
        );
        let mut invalid = sample(1.0);
        invalid.features.0[3] = f64::NAN;
        assert_eq!(
            model.fit(&[invalid]).unwrap_err(),
            DivergenceError::InvalidSample { index: 0 }
        );
        let mut invalid_features = DivergenceFeatures([0.0; FEATURE_COUNT]);
        invalid_features.0[0] = f64::INFINITY;
        assert_eq!(
            model.predict(&invalid_features).unwrap_err(),
            DivergenceError::InvalidFeatures
        );
    }
}
