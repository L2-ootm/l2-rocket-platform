//! RASP (`.eng`) motor thrust-curve parser and impulse-weighted mass-loss
//! model. See 01-04-PLAN.md and 01-RESEARCH.md Pitfall 3 / Code Examples.

use crate::errors::L2EngineError;

/// A motor's thrust-vs-time curve plus the propellant/total mass needed to
/// derive impulse-weighted mass loss during burn (RESEARCH.md Pitfall 3:
/// mass loss is linear in delivered impulse, NOT linear in elapsed time).
#[derive(Debug, Clone)]
pub struct ThrustCurve {
    pub time_s: Vec<f64>,
    pub thrust_n: Vec<f64>,
    pub propellant_mass_kg: f64,
    pub total_mass_kg: f64,
    /// Motor case diameter in meters, from the `.eng` header's `diameter_mm`
    /// field. Used to reject genomes that strap a motor into a body tube too
    /// narrow to physically contain it (see organic_loop_report.md #3).
    pub diameter_m: f64,
    /// Motor case length in meters, from the `.eng` header's `length_mm`
    /// field. Used to place the motor's wet mass at its real axial position
    /// for CG/margin calculations (builder.rs::stack_wet_cg) instead of a
    /// hardcoded per-stage-index length that silently assumed a fixed motor
    /// family.
    pub length_m: f64,
}

/// Parses a RASP `.eng` text blob and extracts the motor matching
/// `designation`. Grammar per RESEARCH.md (`RASPMotorLoader.java`,
/// release-23.09): comment lines start with `;`, the header line is
/// `<designation> <diameter_mm> <length_mm> <delays> <propW_kg> <totalW_kg>
/// <manufacturer>`, followed by `<time_s> <thrust_N>` data rows until a
/// blank line, a `;` comment line, or EOF.
///
/// All numeric parsing is `?`-propagated into `L2EngineError::ParseError`
/// (T-01-06: never `.unwrap()` on untrusted file content) -- an unknown
/// `designation` or malformed row returns `Err`, never panics.
pub fn parse_eng(text: &str, designation: &str) -> Result<ThrustCurve, L2EngineError> {
    let mut lines = text.lines();

    // Scan for the header line matching `designation`, skipping comment
    // lines, blank lines, and any other motor's data rows along the way.
    let header_fields: Vec<&str> = loop {
        let Some(line) = lines.next() else {
            return Err(L2EngineError::ParseError(format!(
                "motor designation '{designation}' not found in .eng text"
            )));
        };
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with(';') {
            continue;
        }
        let fields: Vec<&str> = trimmed.split_whitespace().collect();
        if fields.first() == Some(&designation) {
            break fields;
        }
        // This header belongs to a different motor -- skip its data rows
        // (terminated by a blank line, a `;` comment, or EOF) and continue
        // scanning for the requested designation.
        for skip_line in lines.by_ref() {
            let t = skip_line.trim();
            if t.is_empty() || t.starts_with(';') {
                break;
            }
        }
    };

    if header_fields.len() < 6 {
        return Err(L2EngineError::ParseError(format!(
            "malformed .eng header line for '{designation}': expected >= 6 fields, got {}",
            header_fields.len()
        )));
    }

    let diameter_mm = header_fields[1].parse::<f64>().map_err(|e| {
        L2EngineError::ParseError(format!("invalid diameter_mm '{}': {e}", header_fields[1]))
    })?;
    let length_mm = header_fields[2].parse::<f64>().map_err(|e| {
        L2EngineError::ParseError(format!("invalid length_mm '{}': {e}", header_fields[2]))
    })?;
    let propellant_mass_kg = header_fields[4].parse::<f64>().map_err(|e| {
        L2EngineError::ParseError(format!("invalid propW_kg '{}': {e}", header_fields[4]))
    })?;
    let total_mass_kg = header_fields[5].parse::<f64>().map_err(|e| {
        L2EngineError::ParseError(format!("invalid totalW_kg '{}': {e}", header_fields[5]))
    })?;

    let mut time_s = Vec::new();
    let mut thrust_n = Vec::new();
    for line in lines {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with(';') {
            break;
        }
        let fields: Vec<&str> = trimmed.split_whitespace().collect();
        if fields.len() < 2 {
            return Err(L2EngineError::ParseError(format!(
                "malformed .eng data row: '{trimmed}'"
            )));
        }
        let t = fields[0].parse::<f64>().map_err(|e| {
            L2EngineError::ParseError(format!("invalid time_s '{}': {e}", fields[0]))
        })?;
        let thrust = fields[1].parse::<f64>().map_err(|e| {
            L2EngineError::ParseError(format!("invalid thrust_N '{}': {e}", fields[1]))
        })?;
        time_s.push(t);
        thrust_n.push(thrust);
    }

    if time_s.is_empty() {
        return Err(L2EngineError::ParseError(format!(
            "motor '{designation}' header found but no data rows followed"
        )));
    }

    // OpenRocket's RASPMotorLoader anchors an implicit (0.0, 0.0) start
    // point even though the raw file's first tabulated row is typically
    // e.g. (0.098, 4752.72), not literal t=0.
    if time_s[0] != 0.0 {
        time_s.insert(0, 0.0);
        thrust_n.insert(0, 0.0);
    }

    Ok(ThrustCurve {
        time_s,
        thrust_n,
        propellant_mass_kg,
        total_mass_kg,
        diameter_m: diameter_mm / 1000.0,
        length_m: length_mm / 1000.0,
    })
}

/// Parses a `.eng` file without needing to know its designation ahead of
/// time -- reads the first non-comment/non-blank line's first field as the
/// designation, then delegates to `parse_eng`. Used to build the motor pool
/// dynamically from whatever `.eng` files are present in `l2_engine/motors/`
/// (see `bin/ast_eval.rs`), instead of a hardcoded list of motor names: any
/// real motor becomes usable by the organic-evolution engine just by adding
/// its `.eng` file, with zero Rust code changes.
pub fn parse_eng_file(text: &str) -> Result<(String, ThrustCurve), L2EngineError> {
    let designation = text
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty() && !line.starts_with(';'))
        .and_then(|header| header.split_whitespace().next())
        .ok_or_else(|| L2EngineError::ParseError("empty or comment-only .eng file".to_string()))?
        .to_string();
    let curve = parse_eng(text, &designation)?;
    Ok((designation, curve))
}

impl ThrustCurve {
    /// Thrust at time `t`. Returns the exact tabulated value at a knot
    /// (bit-exact, no interpolation rounding), linearly interpolates
    /// between adjacent knots otherwise, and returns `0.0` outside
    /// `[time_s[0], time_s[last]]` (before ignition or after burnout).
    pub fn thrust_at(&self, t: f64) -> f64 {
        let n = self.time_s.len();
        if n == 0 {
            return 0.0;
        }
        for i in 0..n {
            if t == self.time_s[i] {
                return self.thrust_n[i];
            }
        }
        if t < self.time_s[0] || t > self.time_s[n - 1] {
            return 0.0;
        }
        for i in 1..n {
            if t < self.time_s[i] {
                let t0 = self.time_s[i - 1];
                let t1 = self.time_s[i];
                let f0 = self.thrust_n[i - 1];
                let f1 = self.thrust_n[i];
                let frac = (t - t0) / (t1 - t0);
                return f0 + (f1 - f0) * frac;
            }
        }
        0.0
    }

    /// Trapezoidal integral of the full thrust curve -- total delivered
    /// impulse in N*s.
    pub fn total_impulse(&self) -> f64 {
        self.cumulative_impulse_at(*self.time_s.last().unwrap_or(&0.0))
    }

    /// Cumulative-impulse-weighted mass at time `t` (RESEARCH.md Pitfall 3 /
    /// Code Examples `compute_mass_curve`):
    /// `mass(t) = total_mass - propellant_mass * (cumulative_impulse(t) /
    /// total_impulse)`. Deliberately NOT `total_mass - propellant_mass * t /
    /// burn_time` (the naive, wrong, time-linear formula RESEARCH.md flags
    /// as a pitfall).
    pub fn mass_at(&self, t: f64) -> f64 {
        let total_impulse = self.total_impulse();
        if total_impulse <= 0.0 {
            return self.total_mass_kg;
        }
        let burned_fraction = (self.cumulative_impulse_at(t) / total_impulse).clamp(0.0, 1.0);
        self.total_mass_kg - self.propellant_mass_kg * burned_fraction
    }

    /// Trapezoidal-integrates the thrust curve from `time_s[0]` up to `t`,
    /// clamping `t` into the curve's domain first.
    fn cumulative_impulse_at(&self, t: f64) -> f64 {
        let n = self.time_s.len();
        if n < 2 {
            return 0.0;
        }
        let first_t = self.time_s[0];
        let last_t = self.time_s[n - 1];
        let t_clamped = t.clamp(first_t, last_t);

        let mut cumulative = 0.0;
        for i in 1..n {
            let t0 = self.time_s[i - 1];
            let t1 = self.time_s[i];
            if t_clamped <= t0 {
                break;
            }
            if t_clamped >= t1 {
                cumulative += 0.5 * (self.thrust_n[i] + self.thrust_n[i - 1]) * (t1 - t0);
            } else {
                let thrust_at_tc = self.thrust_at(t_clamped);
                cumulative += 0.5 * (self.thrust_n[i - 1] + thrust_at_tc) * (t_clamped - t0);
                break;
            }
        }
        cumulative
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fixture_text() -> String {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/N4800T.eng");
        std::fs::read_to_string(&path).expect("N4800T.eng fixture must exist")
    }

    /// Naive (wrong) time-linear mass formula, used only as a negative
    /// reference in Test 4 to prove impulse-weighting materially changes the
    /// mid-burn mass value (RESEARCH.md Pitfall 3).
    fn naive_linear_mass_at(curve: &ThrustCurve, t: f64) -> f64 {
        let burn_time = *curve.time_s.last().expect("curve must have points");
        let frac = (t / burn_time).clamp(0.0, 1.0);
        curve.total_mass_kg - curve.propellant_mass_kg * frac
    }

    #[test]
    fn test_parse_eng_header_and_anchor_point() {
        let text = fixture_text();
        let curve = parse_eng(&text, "N4800T").expect("parse should succeed");
        assert_eq!(curve.propellant_mass_kg, 9.7664);
        assert_eq!(curve.total_mass_kg, 14.784);
        assert_eq!(curve.time_s[0], 0.0);
        assert_eq!(curve.thrust_n[0], 0.0);
        assert_eq!(*curve.time_s.last().unwrap(), 5.206);
        assert_eq!(*curve.thrust_n.last().unwrap(), 0.0);
    }

    #[test]
    fn test_thrust_at_exact_and_interpolated_and_out_of_range() {
        let text = fixture_text();
        let curve = parse_eng(&text, "N4800T").expect("parse should succeed");

        assert_eq!(curve.thrust_at(0.301), 6007.53);

        let expected_mid = (4752.72_f64 + 6007.53_f64) / 2.0;
        assert!((curve.thrust_at(0.1995) - expected_mid).abs() < 1e-6);

        assert_eq!(curve.thrust_at(-1.0), 0.0);
        assert_eq!(curve.thrust_at(10.0), 0.0);
    }

    #[test]
    fn test_mass_at_impulse_weighted_endpoints() {
        let text = fixture_text();
        let curve = parse_eng(&text, "N4800T").expect("parse should succeed");

        assert!((curve.mass_at(0.0) - 14.784).abs() < 1e-6);
        assert!((curve.mass_at(5.206) - (14.784 - 9.7664)).abs() < 1e-3);
    }

    #[test]
    fn test_mass_at_diverges_from_naive_time_linear_mid_burn() {
        let text = fixture_text();
        let curve = parse_eng(&text, "N4800T").expect("parse should succeed");

        let impulse_weighted = curve.mass_at(2.5);
        let naive = naive_linear_mass_at(&curve, 2.5);
        assert!(
            (impulse_weighted - naive).abs() > 0.05,
            "impulse-weighted mass_at(2.5)={impulse_weighted} too close to naive time-linear={naive}"
        );
    }

    #[test]
    fn test_parse_eng_unknown_designation_returns_err_not_panic() {
        let text = fixture_text();
        let result = parse_eng(&text, "NONEXISTENT");
        assert!(result.is_err());
    }
}
