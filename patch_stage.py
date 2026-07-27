import re

with open('l2_engine/src/sim_core/vehicle/stage.rs', 'r') as f:
    content = f.read()

# 1. Add MotorBurn definition before Stage
motor_burn_code = """
#[derive(Debug, Clone)]
pub struct MotorBurn {
    pub propellant_mass: f64,
    pub thrust: f64,
    pub isp: f64,
    pub thrust_curve: Vec<(f64, f64)>,
    pub ignition_delay: f64,
}

impl MotorBurn {
    pub fn mass_flow(&self) -> f64 {
        if self.thrust > 0.0 && self.isp > 0.0 {
            self.thrust / (self.isp * crate::sim_core::dynamics::state::G0)
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
            return self.thrust;
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
}

// ---------------------------------------------------------------------------
// Stage definition (one stage of a multi-stage rocket)
"""

content = content.replace("// ---------------------------------------------------------------------------\n// Stage definition (one stage of a multi-stage rocket)", motor_burn_code)

# 2. Replace fields in Stage
stage_fields_old = """    pub dry_mass: f64,
    pub propellant_mass: f64,
    pub thrust: f64,           // N (constant fallback when thrust_curve is empty)
    pub isp: f64,              // s
    pub cd: f64,               // constant fallback when cd_table is empty
    pub area: f64,             // m^2
    pub inertia: Vector3<f64>, // [Ixx, Iyy, Izz] principal moments, kg·m^2
    pub nozzle_offset: f64,    // distance from CG to nozzle, m (positive = nozzle behind CG)
    pub cp_offset: f64,        // distance from CG to CP, m (positive = CP ahead, stable)
    pub dry_cg_from_nose: f64,
    pub motor_axial_offset_m: f64,
    pub rotational_fixed_mass_kg: f64,
    pub rotational_fixed_cg_from_nose: f64,
    pub tvc_max: f64,                  // max gimbal angle, rad
    pub thrust_curve: Vec<(f64, f64)>, // (time_since_ignition_s, thrust_N), sorted by time
    pub cn_alpha: Option<f64>, // normal force coefficient override; None = generic 2.0 fallback
    pub aero_stability_table: Vec<(f64, f64, f64, f64, f64)>, // (mach, AoA, cp_offset_m, CNa, sum(CNa*d^2))
    pub pitch_damping_multiplier: f64,
    pub cd_table: Vec<(f64, f64)>, // (mach, cd_total), sorted by mach — sea-level reference
    pub cd_nonfric_table: Vec<(f64, f64)>, // (mach, cd_pressure+base+wave) — altitude-independent
    pub friction_params: Option<FrictionParams>, // parameters for dynamic friction CD calculation
    pub ignition_delay: f64,       // s after stage_activated_at before thrust begins"""

stage_fields_new = """    pub dry_mass: f64,
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
    pub tvc_max: f64,                  // max gimbal angle, rad
    pub cn_alpha: Option<f64>, // normal force coefficient override; None = generic 2.0 fallback
    pub aero_stability_table: Vec<(f64, f64, f64, f64, f64)>, // (mach, AoA, cp_offset_m, CNa, sum(CNa*d^2))
    pub pitch_damping_multiplier: f64,
    pub cd_table: Vec<(f64, f64)>, // (mach, cd_total), sorted by mach — sea-level reference
    pub cd_nonfric_table: Vec<(f64, f64)>, // (mach, cd_pressure+base+wave) — altitude-independent
    pub friction_params: Option<FrictionParams>, // parameters for dynamic friction CD calculation"""

content = content.replace(stage_fields_old, stage_fields_new)

# 3. Replace methods in impl Stage
stage_methods_old = """    pub fn mass_flow(&self) -> f64 {
        self.thrust / (self.isp * G0)
    }

    pub fn total_mass(&self) -> f64 {
        self.dry_mass + self.propellant_mass
    }

    /// Self-consistent burn time from propellant and mass flow.
    pub fn burn_time(&self) -> f64 {
        if self.thrust > 0.0 {
            self.propellant_mass / self.mass_flow()
        } else {
            0.0
        }
    }

    pub fn delta_v(&self, payload_mass: f64) -> f64 {
        let m0 = self.total_mass() + payload_mass;
        let mf = self.dry_mass + payload_mass;
        self.isp * G0 * (m0 / mf).ln()
    }

    /// Thrust at a given time since ignition, linearly interpolated from
    /// `thrust_curve`. Falls back to the constant `thrust` field when no
    /// curve is set (backward-compatible with generic/constant-thrust stages).
    /// Returns 0.0 outside the curve's time range.
    #[inline]
    pub fn thrust_at(&self, t_since_ignition: f64) -> f64 {
        if self.thrust_curve.is_empty() {
            return self.thrust;
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
    }"""

stage_methods_new = """    pub fn propellant_mass(&self) -> f64 {
        self.motors.iter().map(|m| m.propellant_mass).sum()
    }

    pub fn mass_flow(&self) -> f64 {
        self.motors.iter().map(|m| m.mass_flow()).sum()
    }

    pub fn total_mass(&self) -> f64 {
        self.dry_mass + self.propellant_mass()
    }

    pub fn burn_time(&self) -> f64 {
        self.motors.iter().map(|m| m.burn_time()).fold(0.0, f64::max)
    }

    pub fn delta_v(&self, payload_mass: f64) -> f64 {
        let m0 = self.total_mass() + payload_mass;
        let mf = self.dry_mass + payload_mass;
        // Approximation using first motor's isp (multi-motor delta_v is complex).
        let isp = self.motors.first().map(|m| m.isp).unwrap_or(0.0);
        isp * G0 * (m0 / mf).ln()
    }

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
    }"""

content = content.replace(stage_methods_old, stage_methods_new)

# 4. Replace cg_from_nose_at_propellant
cg_old = """    pub fn cg_from_nose_at_propellant(&self, remaining_propellant_kg: f64) -> f64 {
        let propellant = remaining_propellant_kg.clamp(0.0, self.propellant_mass);"""

cg_new = """    pub fn cg_from_nose_at_propellant(&self, remaining_propellant_kg: f64) -> f64 {
        let propellant = remaining_propellant_kg.clamp(0.0, self.propellant_mass());"""

content = content.replace(cg_old, cg_new)

# 5. Replace impulse_weighted_burn_fraction
imp_old = """    pub fn impulse_weighted_burn_fraction(&self, t_since_ignition: f64) -> f64 {
        if self.thrust_curve.is_empty() {
            return 0.0;
        }
        let total = trapezoidal_integral(&self.thrust_curve, self.thrust_curve.last().unwrap().0);
        if total <= 0.0 {
            return 0.0;
        }
        let partial = trapezoidal_integral(&self.thrust_curve, t_since_ignition);
        (partial / total).clamp(0.0, 1.0)
    }"""

imp_new = """    pub fn impulse_weighted_burn_fraction(&self, t_since_activation: f64) -> f64 {
        // Fallback for single motor backward compatibility
        if let Some(m) = self.motors.first() {
            let t_motor = t_since_activation - m.ignition_delay;
            if t_motor >= 0.0 {
                return m.impulse_weighted_burn_fraction(t_motor);
            }
        }
        0.0
    }"""

content = content.replace(imp_old, imp_new)

# 6. Update StageBuilder (add motors, keep old fields for compat)
sb_old = """pub struct StageBuilder {
    name: String,
    dry_mass: f64,
    propellant_mass: f64,"""

sb_new = """pub struct StageBuilder {
    name: String,
    dry_mass: f64,
    motors: Vec<MotorBurn>,
    propellant_mass: f64,"""

content = content.replace(sb_old, sb_new)

sb_new2 = """    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            dry_mass: 10.0,
            motors: vec![],"""

content = content.replace("    pub fn new(name: impl Into<String>) -> Self {\n        Self {\n            name: name.into(),\n            dry_mass: 10.0,", sb_new2)

sb_build_old = """    pub fn build(self) -> Stage {
        Stage {
            name: self.name,
            dry_mass: self.dry_mass,
            propellant_mass: self.propellant_mass,
            thrust: self.thrust,
            isp: self.isp,
            cd: self.cd,
            area: self.area,
            inertia: self.inertia,
            nozzle_offset: self.nozzle_offset,
            cp_offset: self.cp_offset,
            dry_cg_from_nose: self.dry_cg_from_nose,
            motor_axial_offset_m: self.motor_axial_offset_m,
            rotational_fixed_mass_kg: self.rotational_fixed_mass_kg,
            rotational_fixed_cg_from_nose: self.rotational_fixed_cg_from_nose,
            tvc_max: self.tvc_max,
            thrust_curve: self.thrust_curve,
            cn_alpha: self.cn_alpha,
            aero_stability_table: self.aero_stability_table,
            pitch_damping_multiplier: self.pitch_damping_multiplier,
            cd_table: self.cd_table,
            cd_nonfric_table: self.cd_nonfric_table,
            friction_params: self.friction_params,
            ignition_delay: self.ignition_delay,"""

sb_build_new = """    pub fn build(self) -> Stage {
        let mut motors = self.motors.clone();
        if motors.is_empty() {
            motors.push(MotorBurn {
                propellant_mass: self.propellant_mass,
                thrust: self.thrust,
                isp: self.isp,
                thrust_curve: self.thrust_curve.clone(),
                ignition_delay: self.ignition_delay,
            });
        }
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
            tvc_max: self.tvc_max,
            cn_alpha: self.cn_alpha,
            aero_stability_table: self.aero_stability_table,
            pitch_damping_multiplier: self.pitch_damping_multiplier,
            cd_table: self.cd_table,
            cd_nonfric_table: self.cd_nonfric_table,
            friction_params: self.friction_params,"""

content = content.replace(sb_build_old, sb_build_new)

# Add pub fn motors to StageBuilder
sb_motors = """    pub fn dry_mass(mut self, m: f64) -> Self {
        self.dry_mass = m;
        self
    }
    pub fn motors(mut self, v: Vec<MotorBurn>) -> Self {
        self.motors = v;
        self
    }"""
content = content.replace("    pub fn dry_mass(mut self, m: f64) -> Self {\n        self.dry_mass = m;\n        self\n    }", sb_motors)

with open('l2_engine/src/sim_core/vehicle/stage.rs', 'w') as f:
    f.write(content)
