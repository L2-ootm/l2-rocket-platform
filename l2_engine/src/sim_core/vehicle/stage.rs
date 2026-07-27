use nalgebra::Vector3;

use crate::sim_core::dynamics::state::G0;

// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct MotorBurn {
    /// Mission role propagated from the AST mount (for example `main` or `retro`).
    pub role: String,
    pub propellant_mass: f64,
    pub thrust: f64,
    pub isp: f64,
    pub thrust_curve: Vec<(f64, f64)>,
    pub ignition_delay: f64,
    /// `(axial from nose, radial y, radial z)` in meters.
    pub position_from_nose_m: Vector3<f64>,
    /// Nozzle exit pose `(axial from nose, radial y, radial z)` in meters.
    pub nozzle_position_from_nose_m: Vector3<f64>,
}

impl MotorBurn {
    pub fn mass_flow(&self) -> f64 {
        if self.thrust > 0.0 && self.isp > 0.0 {
            self.thrust / (self.isp * G0)
        } else {
            0.0
        }
    }

    pub fn burn_time(&self) -> f64 {
        if self.thrust > 0.0 {
            self.propellant_mass / self.mass_flow()
        } else {
            0.0
        }
    }

    #[inline]
    pub fn thrust_at(&self, t_since_ignition: f64) -> f64 {
        if self.thrust_curve.is_empty() {
            return if t_since_ignition >= 0.0 && t_since_ignition <= self.burn_time() {
                self.thrust
            } else {
                0.0
            };
        }
        let curve = &self.thrust_curve;
        let first_time = curve.first().unwrap().0;
        let last_time = curve.last().unwrap().0;
        if t_since_ignition < first_time || t_since_ignition > last_time {
            return 0.0;
        }
        for w in curve.windows(2) {
            let (t0, v0) = w[0];
            let (t1, v1) = w[1];
            if t_since_ignition >= t0 && t_since_ignition <= t1 {
                if (t1 - t0).abs() < 1e-12 {
                    return v1;
                }
                let frac = (t_since_ignition - t0) / (t1 - t0);
                return v0 + frac * (v1 - v0);
            }
        }
        curve.last().unwrap().1
    }

    pub fn impulse_weighted_burn_fraction(&self, t_since_ignition: f64) -> f64 {
        if self.thrust_curve.is_empty() {
            return 0.0;
        }
        let total = trapezoidal_integral(&self.thrust_curve, self.thrust_curve.last().unwrap().0);
        if total <= 0.0 {
            return 0.0;
        }
        let partial = trapezoidal_integral(&self.thrust_curve, t_since_ignition);
        (partial / total).clamp(0.0, 1.0)
    }
    
    pub fn nominal_burn_duration(&self) -> f64 {
        self.thrust_curve
            .last()
            .map(|point| point.0.max(0.0))
            .unwrap_or_else(|| self.burn_time())
    }

    pub fn remaining_propellant_at(&self, t_since_activation: f64) -> f64 {
        let motor_time = t_since_activation - self.ignition_delay;
        if motor_time <= 0.0 {
            return self.propellant_mass;
        }
        if self.thrust_curve.is_empty() {
            let duration = self.burn_time();
            if duration <= 0.0 {
                return self.propellant_mass;
            }
            return self.propellant_mass * (1.0 - motor_time / duration).clamp(0.0, 1.0);
        }
        self.propellant_mass * (1.0 - self.impulse_weighted_burn_fraction(motor_time))
    }

    pub fn is_ascent_motor(&self) -> bool {
        !matches!(
            self.role.trim().to_ascii_lowercase().as_str(),
            "retro" | "landing" | "recovery" | "braking"
        )
    }
}

// ---------------------------------------------------------------------------
// Stage definition (one stage of a multi-stage rocket)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct Stage {
    pub name: String,
    pub dry_mass: f64,
    pub motors: Vec<MotorBurn>,
    pub cd: f64,               // constant fallback when cd_table is empty
    pub area: f64,             // m^2
    pub inertia: Vector3<f64>, // [Ixx, Iyy, Izz] principal moments, kg·m^2
    pub nozzle_offset: f64,    // distance from CG to nozzle, m (positive = nozzle behind CG)
    pub cp_offset: f64,        // distance from CG to CP, m (positive = CP ahead, stable)
    pub dry_cg_from_nose: f64,
    pub motor_axial_offset_m: f64,
    pub rotational_fixed_mass_kg: f64,
    pub rotational_fixed_cg_from_nose: f64,
    /// Lateral `(y,z)` coordinates of the fixed-mass CG. The x component is
    /// unused; axial CG remains in `rotational_fixed_cg_from_nose`.
    pub rotational_fixed_cg_radial_m: Vector3<f64>,
    pub tvc_max: f64,                  // max gimbal angle, rad
    pub cn_alpha: Option<f64>, // normal force coefficient override; None = generic 2.0 fallback
    pub aero_stability_table: Vec<(f64, f64, f64, f64, f64)>, // (mach, AoA, cp_offset_m, CNa, sum(CNa*d^2))
    pub pitch_damping_multiplier: f64,
    pub cd_table: Vec<(f64, f64)>, // (mach, cd_total), sorted by mach — sea-level reference
    pub cd_nonfric_table: Vec<(f64, f64)>, // (mach, cd_pressure+base+wave) — altitude-independent
    pub friction_params: Option<FrictionParams>, // parameters for dynamic friction CD calculation
    pub separation_coast: f64,     // s after propellant depletion before mass drop + stage advance
    pub parachute_delay: Option<f64>, // s after separation_coast before parachute opens
    pub parachute_cd_area: Option<f64>, // parachute cd * area (m^2)
}

/// Skin-friction law used for altitude-dependent CD recomputation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FrictionModel {
    HyperReal,
    OpenRocketLegacy,
}

/// Parameters for altitude-dependent skin friction CD computation.
/// Stored per-stage so sixdof.rs can recompute friction dynamically.
#[derive(Debug, Clone)]
pub struct FrictionParams {
    pub vehicle_length: f64,    // total body length, m
    pub wetted_area_ratio: f64, // wetted_area / ref_area (dimensionless)
    pub body_wetted_area_ratio: f64,
    pub body_fineness_ratio: f64,
    pub roughness_m: f64, // surface roughness, m
    pub model: FrictionModel,
}

impl Stage {
    pub fn propellant_depletion_tolerance_kg(&self) -> f64 {
        match self.friction_params.as_ref().map(|params| params.model) {
            Some(FrictionModel::OpenRocketLegacy) => {
                crate::sim_core::dynamics::state::PROPELLANT_EPSILON_KG
            }
            // Preserve the frozen HyperReal and generic-stage behavior.
            _ => 0.01,
        }
    }

    pub fn propellant_mass(&self) -> f64 {
        self.motors.iter().map(|m| m.propellant_mass).sum()
    }

    pub fn mass_flow(&self) -> f64 {
        self.motors.iter().map(|m| m.mass_flow()).sum()
    }

    pub fn total_mass(&self) -> f64 {
        self.dry_mass + self.propellant_mass()
    }

    /// Self-consistent burn time from propellant and mass flow.
    pub fn burn_time(&self) -> f64 {
        self.motors.iter().map(|m| m.burn_time()).fold(0.0, f64::max)
    }

    pub fn delta_v(&self, payload_mass: f64) -> f64 {
        let m0 = self.total_mass() + payload_mass;
        let mf = self.dry_mass + payload_mass;
        let isp = self.motors.first().map(|m| m.isp).unwrap_or(0.0);
        if isp > 0.0 {
            isp * G0 * (m0 / mf).ln()
        } else {
            0.0
        }
    }

    /// Thrust at a given time since ignition.
    #[inline]
    pub fn thrust_at(&self, t_since_activation: f64) -> f64 {
        let mut total = 0.0;
        for m in &self.motors {
            let t_motor = t_since_activation - m.ignition_delay;
            if t_motor >= 0.0 {
                total += m.thrust_at(t_motor);
            }
        }
        total
    }

    /// Drag coefficient at a given Mach number, linearly interpolated from
    /// `cd_table`. Falls back to the constant `cd` field when no table is
    /// set. Clamps to the nearest endpoint outside the table's range.
    /// NOTE: This uses the SEA-LEVEL reference CD table. For altitude-dependent
    /// CD, use `cd_at_conditions` which recomputes friction dynamically.
    #[inline]
    pub fn cd_at(&self, mach: f64) -> f64 {
        Self::interp_table(&self.cd_table, mach).unwrap_or(self.cd)
    }

    /// Mach-dependent CP-CG offset and normal-force slope. OpenRocket's
    /// Barrowman model moves fin CP/CNa substantially through transonic and
    /// supersonic flight; a single Mach-0.3 scalar can miss real instability.
    #[inline]
    pub fn stability_at(&self, mach: f64, aoa_rad: f64) -> (f64, f64, f64) {
        if self.aero_stability_table.is_empty() {
            let cn = self.cn_alpha.unwrap_or(2.0);
            return (self.cp_offset, cn, cn * self.cp_offset.powi(2));
        }
        let aoa = aoa_rad.abs().clamp(0.0, 20.0_f64.to_radians());
        let mut min_level = f64::INFINITY;
        let mut max_level = f64::NEG_INFINITY;
        let mut lower = f64::NEG_INFINITY;
        let mut upper = f64::INFINITY;
        for point in &self.aero_stability_table {
            let level = point.1;
            min_level = min_level.min(level);
            max_level = max_level.max(level);
            if level <= aoa {
                lower = lower.max(level);
            }
            if level >= aoa {
                upper = upper.min(level);
            }
        }
        if !lower.is_finite() {
            lower = min_level;
        }
        if !upper.is_finite() {
            upper = max_level;
        }
        let interpolate_mach = |level: f64| {
            let mut matching = self
                .aero_stability_table
                .iter()
                .filter(|point| (point.1 - level).abs() < 1e-12);
            let first = matching.next().expect("stability level must have a row");
            if mach <= first.0 {
                return (first.2, first.3, first.4);
            }
            let mut left = first;
            for right in matching {
                if mach >= left.0 && mach <= right.0 {
                    let fraction = (mach - left.0) / (right.0 - left.0);
                    return (
                        left.2 + fraction * (right.2 - left.2),
                        left.3 + fraction * (right.3 - left.3),
                        left.4 + fraction * (right.4 - left.4),
                    );
                }
                left = right;
            }
            (left.2, left.3, left.4)
        };
        let low = interpolate_mach(lower);
        if (upper - lower).abs() < 1e-12 {
            return low;
        }
        let high = interpolate_mach(upper);
        let fraction = (aoa - lower) / (upper - lower);
        (
            low.0 + fraction * (high.0 - low.0),
            low.1 + fraction * (high.1 - low.1),
            low.2 + fraction * (high.2 - low.2),
        )
    }

    pub fn cg_from_nose_at_propellant(&self, remaining_propellant_kg: f64) -> f64 {
        let propellant = remaining_propellant_kg.clamp(0.0, self.propellant_mass());
        let fixed_mass = if self.rotational_fixed_mass_kg > 0.0 {
            self.rotational_fixed_mass_kg
        } else {
            self.dry_mass
        };
        let fixed_cg = if self.rotational_fixed_mass_kg > 0.0 {
            self.rotational_fixed_cg_from_nose
        } else {
            self.dry_cg_from_nose
        };
        let total = fixed_mass + propellant;
        if total <= 0.0 {
            return self.dry_cg_from_nose;
        }
        (fixed_mass * fixed_cg + propellant * self.motor_axial_offset_m) / total
    }

    pub fn ascent_burn_complete(&self, t_since_activation: f64) -> bool {
        let has_ascent_motor = self.motors.iter().any(MotorBurn::is_ascent_motor);
        self.motors
            .iter()
            .filter(|motor| !has_ascent_motor || motor.is_ascent_motor())
            .all(|motor| {
                t_since_activation
                    >= motor.ignition_delay + motor.nominal_burn_duration()
            })
    }

    pub fn remaining_propellant_at(&self, t_since_activation: f64) -> f64 {
        self.motors
            .iter()
            .map(|motor| motor.remaining_propellant_at(t_since_activation))
            .sum()
    }

    pub fn cg_from_nose_3d_at(&self, t_since_activation: f64) -> Vector3<f64> {
        let fixed_mass = if self.rotational_fixed_mass_kg > 0.0 {
            self.rotational_fixed_mass_kg
        } else {
            self.dry_mass
        };
        let fixed = Vector3::new(
            if self.rotational_fixed_mass_kg > 0.0 {
                self.rotational_fixed_cg_from_nose
            } else {
                self.dry_cg_from_nose
            },
            self.rotational_fixed_cg_radial_m.y,
            self.rotational_fixed_cg_radial_m.z,
        );
        let mut total = fixed_mass;
        let mut moment = fixed * fixed_mass;
        for motor in &self.motors {
            let propellant = motor.remaining_propellant_at(t_since_activation);
            total += propellant;
            moment += motor.position_from_nose_m * propellant;
        }
        if total > 0.0 { moment / total } else { fixed }
    }

    /// Altitude-dependent CD: non-friction components from static table +
    /// friction recomputed using actual atmospheric kinematic viscosity.
    /// Falls back to `cd_at` when friction_params or cd_nonfric_table is absent.
    #[inline]
    pub fn cd_at_conditions(&self, mach: f64, speed: f64, kinematic_viscosity: f64) -> f64 {
        let (friction, nonfriction) =
            self.cd_components_at_conditions(mach, speed, kinematic_viscosity);
        friction + nonfriction
    }

    pub fn cd_components_at_conditions(
        &self,
        mach: f64,
        speed: f64,
        kinematic_viscosity: f64,
    ) -> (f64, f64) {
        let fp = match &self.friction_params {
            Some(fp) if !self.cd_nonfric_table.is_empty() => fp,
            _ => return (0.0, self.cd_at(mach)),
        };

        // Non-friction CD (pressure + base + wave) from static table
        let cd_nonfric = Self::interp_table(&self.cd_nonfric_table, mach).unwrap_or(0.0);

        // Dynamic friction CD using actual Reynolds number.
        let reynolds = (speed * fp.vehicle_length / kinematic_viscosity).max(2.0);
        let cf = match fp.model {
            FrictionModel::HyperReal => {
                hyperreal_skin_friction_cf(mach, reynolds, fp.roughness_m, fp.vehicle_length)
            }
            FrictionModel::OpenRocketLegacy => {
                let smooth_cf = openrocket_skin_friction_cf(mach, reynolds);
                let roughness_limited_cf = 0.032
                    * (fp.roughness_m.max(1e-12) / fp.vehicle_length.max(1e-12)).powf(0.2)
                    * openrocket_roughness_correction(mach);
                smooth_cf.max(roughness_limited_cf)
            }
        };
        let fin_wetted_area_ratio = (fp.wetted_area_ratio - fp.body_wetted_area_ratio).max(0.0);
        let body_correction =
            if fp.model == FrictionModel::OpenRocketLegacy && fp.body_fineness_ratio > 0.0 {
                1.0 + 1.0 / (2.0 * fp.body_fineness_ratio)
            } else {
                1.0
            };
        let friction_cd =
            cf * (fin_wetted_area_ratio + fp.body_wetted_area_ratio * body_correction);

        (friction_cd, cd_nonfric)
    }

    /// Linear interpolation helper for (x, y) tables.
    #[inline]
    fn interp_table(table: &[(f64, f64)], x: f64) -> Option<f64> {
        if table.is_empty() {
            return None;
        }
        let first = *table.first().unwrap();
        let last = *table.last().unwrap();
        if x <= first.0 {
            return Some(first.1);
        }
        if x >= last.0 {
            return Some(last.1);
        }
        for w in table.windows(2) {
            let (x0, y0) = w[0];
            let (x1, y1) = w[1];
            if x >= x0 && x <= x1 {
                if (x1 - x0).abs() < 1e-12 {
                    return Some(y1);
                }
                let frac = (x - x0) / (x1 - x0);
                return Some(y0 + frac * (y1 - y0));
            }
        }
        Some(last.1)
    }

    /// Fraction (0.0-1.0) of this stage's total impulse delivered by
    /// `t_since_activation`, via trapezoidal integration of `thrust_curve`.
    pub fn impulse_weighted_burn_fraction(&self, t_since_activation: f64) -> f64 {
        // We evaluate this for the primary motor. Multi-motor mass distribution
        // uses this as a proxy for the entire stage's mass depletion curve.
        if let Some(m) = self.motors.first() {
            let t_motor = t_since_activation - m.ignition_delay;
            if t_motor >= 0.0 {
                return m.impulse_weighted_burn_fraction(t_motor);
            }
        }
        0.0
    }
}

#[cfg(test)]
mod tests {
    use super::StageBuilder;

    fn assert_tuple_close(actual: (f64, f64, f64), expected: (f64, f64, f64)) {
        assert!((actual.0 - expected.0).abs() < 1e-12);
        assert!((actual.1 - expected.1).abs() < 1e-12);
        assert!((actual.2 - expected.2).abs() < 1e-12);
    }

    fn stability_stage() -> super::Stage {
        StageBuilder::new("test")
            .aero_stability_table(vec![
                (0.0, 0.0, 1.0, 2.0, 3.0),
                (1.0, 0.0, 2.0, 4.0, 6.0),
                (2.0, 0.0, 3.0, 6.0, 9.0),
                (0.0, 0.2, 2.0, 3.0, 4.0),
                (1.0, 0.2, 4.0, 6.0, 8.0),
                (2.0, 0.2, 6.0, 9.0, 12.0),
            ])
            .build()
    }

    #[test]
    fn stability_at_interpolates_mach_and_aoa_without_changing_results() {
        let stage = stability_stage();
        assert_tuple_close(stage.stability_at(0.5, 0.1), (2.25, 3.75, 5.25));
        assert_tuple_close(stage.stability_at(1.5, -0.1), (3.75, 6.25, 8.75));
    }

    #[test]
    fn stability_at_clamps_mach_and_aoa_boundaries() {
        let stage = stability_stage();
        assert_tuple_close(stage.stability_at(-1.0, 0.1), (1.5, 2.5, 3.5));
        assert_tuple_close(stage.stability_at(3.0, 0.1), (4.5, 7.5, 10.5));
        assert_tuple_close(stage.stability_at(1.0, -1.0), (4.0, 6.0, 8.0));
    }
}

/// Trapezoidal integral of a (x, y) curve from its first point up to `up_to`.
fn trapezoidal_integral(curve: &[(f64, f64)], up_to: f64) -> f64 {
    let mut total = 0.0;
    for w in curve.windows(2) {
        let (t0, v0) = w[0];
        let (t1, v1) = w[1];
        if up_to <= t0 {
            break;
        }
        if up_to < t1 {
            let frac = (up_to - t0) / (t1 - t0);
            let v_at = v0 + frac * (v1 - v0);
            total += (v0 + v_at) * 0.5 * (up_to - t0);
            break;
        }
        total += (v0 + v1) * 0.5 * (t1 - t0);
    }
    total
}

fn hyperreal_skin_friction_cf(
    mach: f64,
    reynolds: f64,
    roughness_m: f64,
    vehicle_length: f64,
) -> f64 {
    let roughness_ratio = (roughness_m.max(1e-9) / vehicle_length).max(1e-12);
    let re_crit = 51.0 * roughness_ratio.powf(-1.039);
    let re_eff = reynolds.min(re_crit).max(2.0);

    let cf_incomp = 0.455 / re_eff.log10().powf(2.58);
    let compressibility = (1.0 + 0.144 * mach.powi(2)).powf(0.65);
    cf_incomp / compressibility
}

fn openrocket_skin_friction_cf(mach: f64, reynolds: f64) -> f64 {
    let cf = if reynolds < 1.0e4 {
        1.48e-2
    } else {
        1.0 / (1.50 * reynolds.ln() - 5.6).powi(2)
    };

    let mut c1 = 1.0;
    let mut c2 = 1.0;
    if mach < 1.1 {
        c1 = 1.0 - 0.1 * mach.powi(2);
    }
    if mach > 0.9 {
        c2 = 1.0 / (1.0 + 0.15 * mach.powi(2)).powf(0.58);
    }

    if mach < 0.9 {
        cf * c1
    } else if mach < 1.1 {
        cf * (c2 * (mach - 0.9) / 0.2 + c1 * (1.1 - mach) / 0.2)
    } else {
        cf * c2
    }
}

fn openrocket_roughness_correction(mach: f64) -> f64 {
    if mach < 0.9 {
        1.0 - 0.1 * mach.powi(2)
    } else if mach > 1.1 {
        1.0 / (1.0 + 0.18 * mach.powi(2))
    } else {
        let c1 = 1.0 - 0.1 * 0.9_f64.powi(2);
        let c2 = 1.0 / (1.0 + 0.18 * 1.1_f64.powi(2));
        c2 * (mach - 0.9) / 0.2 + c1 * (1.1 - mach) / 0.2
    }
}

// ---------------------------------------------------------------------------
// Stage builder
// ---------------------------------------------------------------------------

pub struct StageBuilder {
    name: String,
    dry_mass: f64,
    motors: Vec<MotorBurn>,
    propellant_mass: f64,
    thrust: f64,
    isp: f64,
    cd: f64,
    area: f64,
    inertia: Vector3<f64>,
    nozzle_offset: f64,
    cp_offset: f64,
    dry_cg_from_nose: f64,
    motor_axial_offset_m: f64,
    rotational_fixed_mass_kg: f64,
    rotational_fixed_cg_from_nose: f64,
    rotational_fixed_cg_radial_m: Vector3<f64>,
    tvc_max: f64,
    thrust_curve: Vec<(f64, f64)>,
    cn_alpha: Option<f64>,
    aero_stability_table: Vec<(f64, f64, f64, f64, f64)>,
    pitch_damping_multiplier: f64,
    cd_table: Vec<(f64, f64)>,
    cd_nonfric_table: Vec<(f64, f64)>,
    friction_params: Option<FrictionParams>,
    ignition_delay: f64,
    separation_coast: f64,
    parachute_delay: Option<f64>,
    parachute_cd_area: Option<f64>,
}

impl StageBuilder {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            dry_mass: 10.0,
            motors: vec![],
            propellant_mass: 5.0,
            thrust: 1000.0,
            isp: 220.0,
            cd: 0.3,
            area: 0.01,
            inertia: Vector3::new(5.0, 5.0, 0.5),
            nozzle_offset: 1.0,
            cp_offset: 0.3,
            dry_cg_from_nose: 0.0,
            motor_axial_offset_m: 0.0,
            rotational_fixed_mass_kg: 0.0,
            rotational_fixed_cg_from_nose: 0.0,
            rotational_fixed_cg_radial_m: Vector3::zeros(),
            tvc_max: 0.1,
            thrust_curve: vec![],
            cn_alpha: None,
            aero_stability_table: vec![],
            pitch_damping_multiplier: 0.0,
            cd_table: vec![],
            cd_nonfric_table: vec![],
            friction_params: None,
            ignition_delay: 0.0,
            separation_coast: 0.0,
            parachute_delay: None,
            parachute_cd_area: None,
        }
    }

    pub fn dry_mass(mut self, m: f64) -> Self {
        self.dry_mass = m;
        self
    }
    pub fn motors(mut self, v: Vec<MotorBurn>) -> Self {
        self.motors = v;
        self
    }
    pub fn propellant_mass(mut self, v: f64) -> Self {
        self.propellant_mass = v;
        self
    }
    pub fn thrust(mut self, v: f64) -> Self {
        self.thrust = v;
        self
    }
    pub fn isp(mut self, v: f64) -> Self {
        self.isp = v;
        self
    }
    pub fn cd(mut self, v: f64) -> Self {
        self.cd = v;
        self
    }
    pub fn area(mut self, v: f64) -> Self {
        self.area = v;
        self
    }
    pub fn inertia(mut self, v: Vector3<f64>) -> Self {
        self.inertia = v;
        self
    }
    pub fn nozzle_offset(mut self, v: f64) -> Self {
        self.nozzle_offset = v;
        self
    }
    pub fn cp_offset(mut self, v: f64) -> Self {
        self.cp_offset = v;
        self
    }
    pub fn mass_locations(
        mut self,
        dry_cg_from_nose: f64,
        motor_axial_offset_m: f64,
        rotational_fixed_mass_kg: f64,
        rotational_fixed_cg_from_nose: f64,
    ) -> Self {
        self.dry_cg_from_nose = dry_cg_from_nose;
        self.motor_axial_offset_m = motor_axial_offset_m;
        self.rotational_fixed_mass_kg = rotational_fixed_mass_kg;
        self.rotational_fixed_cg_from_nose = rotational_fixed_cg_from_nose;
        self
    }
    pub fn fixed_cg_radial(mut self, value: Vector3<f64>) -> Self {
        self.rotational_fixed_cg_radial_m = value;
        self
    }
    pub fn tvc_max(mut self, v: f64) -> Self {
        self.tvc_max = v;
        self
    }
    pub fn thrust_curve(mut self, v: Vec<(f64, f64)>) -> Self {
        self.thrust_curve = v;
        self
    }
    pub fn cn_alpha(mut self, v: f64) -> Self {
        self.cn_alpha = Some(v);
        self
    }
    pub fn aero_stability_table(mut self, v: Vec<(f64, f64, f64, f64, f64)>) -> Self {
        self.aero_stability_table = v;
        self
    }
    pub fn pitch_damping_multiplier(mut self, value: f64) -> Self {
        self.pitch_damping_multiplier = value;
        self
    }
    pub fn cd_table(mut self, v: Vec<(f64, f64)>) -> Self {
        self.cd_table = v;
        self
    }
    pub fn cd_nonfric_table(mut self, v: Vec<(f64, f64)>) -> Self {
        self.cd_nonfric_table = v;
        self
    }
    pub fn friction_params(mut self, v: FrictionParams) -> Self {
        self.friction_params = Some(v);
        self
    }
    pub fn ignition_delay(mut self, v: f64) -> Self {
        self.ignition_delay = v;
        self
    }
    pub fn separation_coast(mut self, v: f64) -> Self {
        self.separation_coast = v;
        self
    }

    pub fn parachute_delay(mut self, v: f64) -> Self {
        self.parachute_delay = Some(v);
        self
    }
    pub fn parachute_cd_area(mut self, v: f64) -> Self {
        self.parachute_cd_area = Some(v);
        self
    }

    pub fn build(self) -> Stage {
        let motors = if self.motors.is_empty() {
            vec![MotorBurn {
                role: "main".to_string(),
                propellant_mass: self.propellant_mass,
                thrust: self.thrust,
                isp: self.isp,
                thrust_curve: self.thrust_curve.clone(),
                ignition_delay: self.ignition_delay,
                position_from_nose_m: Vector3::new(self.motor_axial_offset_m, 0.0, 0.0),
                nozzle_position_from_nose_m: Vector3::new(
                    self.dry_cg_from_nose + self.nozzle_offset,
                    0.0,
                    0.0,
                ),
            }]
        } else {
            self.motors
        };
        Stage {
            name: self.name,
            dry_mass: self.dry_mass,
            motors,
            cd: self.cd,
            area: self.area,
            inertia: self.inertia,
            nozzle_offset: self.nozzle_offset,
            cp_offset: self.cp_offset,
            dry_cg_from_nose: self.dry_cg_from_nose,
            motor_axial_offset_m: self.motor_axial_offset_m,
            rotational_fixed_mass_kg: self.rotational_fixed_mass_kg,
            rotational_fixed_cg_from_nose: self.rotational_fixed_cg_from_nose,
            rotational_fixed_cg_radial_m: self.rotational_fixed_cg_radial_m,
            tvc_max: self.tvc_max,
            cn_alpha: self.cn_alpha,
            aero_stability_table: self.aero_stability_table,
            pitch_damping_multiplier: self.pitch_damping_multiplier,
            cd_table: self.cd_table,
            cd_nonfric_table: self.cd_nonfric_table,
            friction_params: self.friction_params,
            separation_coast: self.separation_coast,
            parachute_delay: self.parachute_delay,
            parachute_cd_area: self.parachute_cd_area,
        }
    }
}
