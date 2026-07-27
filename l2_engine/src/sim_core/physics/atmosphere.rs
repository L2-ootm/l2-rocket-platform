use crate::sim_core::dynamics::state::G0;

// ---------------------------------------------------------------------------
// ISA 1976 Standard Atmosphere (sea level to 86 km)
// ---------------------------------------------------------------------------

const R_AIR: f64 = 287.052_87; // specific gas constant for dry air, J/(kg·K)
#[allow(dead_code)]
const GAMMA: f64 = 1.4; // ratio of specific heats -- no longer used by sound_speed
// (OpenRocket's linear approximation replaced sqrt(gamma*R*T)),
// kept for reference per 01-02-PLAN.md

const T0: f64 = 288.15; // sea-level temperature, K
const P0: f64 = 101_325.0; // sea-level pressure, Pa
const EPSILON: f64 = 0.622;
const E0: f64 = 611.3;
const E_A: f64 = 19.854;
const E_B: f64 = 5423.0;

/// Atmospheric properties at a given geometric altitude.
#[derive(Debug, Clone, Copy)]
pub struct Atmo {
    pub density: f64,             // kg/m^3
    pub pressure: f64,            // Pa
    pub temperature: f64,         // K
    pub sound_speed: f64,         // m/s
    pub kinematic_viscosity: f64, // m^2/s (Sutherland's law)
}

/// ISA 1976 standard atmosphere model.
///
/// Piecewise temperature profile with 7 layers from 0-86 km.
/// Clamps negative altitudes to sea level; returns near-vacuum above 86 km.
pub fn isa(altitude_m: f64) -> Atmo {
    isa_with_humidity(altitude_m, 0.0)
}

/// ISA atmosphere with OpenRocket's humidity-corrected gas constant.
pub fn isa_with_humidity(altitude_m: f64, relative_humidity: f64) -> Atmo {
    let h = altitude_m.max(0.0);

    let (temperature, pressure) = if h < 11_000.0 {
        // Troposphere: lapse -6.5 K/km
        gradient_layer(h, 0.0, T0, -0.0065, P0)
    } else if h < 20_000.0 {
        // Tropopause: isothermal 216.65 K
        isothermal_layer(h, 11_000.0, 216.65, 22_632.1)
    } else if h < 32_000.0 {
        // Stratosphere I: lapse +1.0 K/km
        gradient_layer(h, 20_000.0, 216.65, 0.001, 5_474.89)
    } else if h < 47_000.0 {
        // Stratosphere II: lapse +2.8 K/km
        gradient_layer(h, 32_000.0, 228.65, 0.0028, 868.019)
    } else if h < 51_000.0 {
        // Mesosphere I: isothermal 270.65 K
        isothermal_layer(h, 47_000.0, 270.65, 110.906)
    } else if h < 71_000.0 {
        // Mesosphere II: lapse -2.8 K/km
        gradient_layer(h, 51_000.0, 270.65, -0.0028, 66.9389)
    } else if h < 86_000.0 {
        // Mesosphere III: lapse -2.0 K/km
        gradient_layer(h, 71_000.0, 214.65, -0.002, 3.956_42)
    } else {
        // Above 86 km: exponential decay approximation
        let t = 186.87;
        let p = 0.3734 * (-0.000_15 * (h - 86_000.0)).exp();
        (t, p.max(0.0))
    };

    let humidity = relative_humidity.clamp(0.0, 1.0);
    let r_humid = if humidity > 0.0 && temperature > 0.0 {
        let saturation_pressure = E0 * (E_A - E_B / temperature).exp();
        let humid_pressure = humidity * saturation_pressure;
        let denominator = pressure - humid_pressure * (1.0 - EPSILON);
        if denominator > 0.0 {
            R_AIR * (1.0 + EPSILON * humid_pressure * (1.0 / EPSILON - 1.0) / denominator)
        } else {
            R_AIR
        }
    } else {
        R_AIR
    };
    let density = if temperature > 0.0 {
        pressure / (r_humid * temperature)
    } else {
        0.0
    };

    // Sutherland's law for dynamic viscosity:
    //   mu = mu_ref * (T/T_ref)^1.5 * (T_ref + S) / (T + S)
    // where mu_ref = 1.716e-5 Pa·s, T_ref = 273.15 K, S = 110.4 K
    const MU_REF: f64 = 1.716e-5;
    const T_REF: f64 = 273.15;
    const S: f64 = 110.4;
    let mu = if temperature > 0.0 {
        MU_REF * (temperature / T_REF).powf(1.5) * (T_REF + S) / (temperature + S)
    } else {
        MU_REF
    };
    let kinematic_viscosity = if density > 1e-15 {
        mu / density
    } else {
        1.5e-5 // fallback to sea-level value for near-vacuum
    };
    Atmo {
        density,
        pressure,
        temperature,
        // OpenRocket's AtmosphericConditions.getMachSpeed() linear approximation,
        // not the textbook sqrt(gamma*R*T) formula -- see 01-RESEARCH.md Anti-Patterns.
        sound_speed: 165.77 + 0.606 * temperature,
        kinematic_viscosity,
    }
}

/// Mission-relative extended ISA. The supplied launch-site temperature and
/// pressure anchor the same lapse-rate atmosphere at AGL=0, so missions can
/// reproduce their real launch conditions instead of inheriting sea-level ISA.
pub fn isa_from_launch_conditions(
    altitude_agl_m: f64,
    launch_altitude_m: f64,
    base_temperature_k: f64,
    base_pressure_pa: f64,
    relative_humidity: f64,
) -> Atmo {
    let agl = altitude_agl_m.max(0.0);
    let launch_altitude = launch_altitude_m.max(0.0);
    let standard_launch = isa_with_humidity(launch_altitude, relative_humidity);
    let standard_here = isa_with_humidity(launch_altitude + agl, relative_humidity);
    let temperature_offset = base_temperature_k - standard_launch.temperature;
    let temperature = (standard_here.temperature + temperature_offset).max(1.0);
    let pressure_scale = if standard_launch.pressure > 0.0 {
        base_pressure_pa / standard_launch.pressure
    } else {
        1.0
    };
    let pressure = (standard_here.pressure * pressure_scale).max(0.0);
    atmo_from_temperature_pressure(temperature, pressure, relative_humidity)
}

fn atmo_from_temperature_pressure(
    temperature: f64,
    pressure: f64,
    relative_humidity: f64,
) -> Atmo {
    let humidity = relative_humidity.clamp(0.0, 1.0);
    let saturation_pressure = E0 * (E_A - E_B / temperature).exp();
    let humid_pressure = humidity * saturation_pressure;
    let denominator = pressure - humid_pressure * (1.0 - EPSILON);
    let r_humid = if denominator > 0.0 {
        R_AIR * (1.0 + EPSILON * humid_pressure * (1.0 / EPSILON - 1.0) / denominator)
    } else {
        R_AIR
    };
    let density = pressure / (r_humid * temperature);
    const MU_REF: f64 = 1.716e-5;
    const T_REF: f64 = 273.15;
    const S: f64 = 110.4;
    let mu = MU_REF * (temperature / T_REF).powf(1.5) * (T_REF + S) / (temperature + S);
    Atmo {
        density,
        pressure,
        temperature,
        sound_speed: 165.77 + 0.606 * temperature,
        kinematic_viscosity: if density > 1e-15 { mu / density } else { 1.5e-5 },
    }
}

// ---------------------------------------------------------------------------
// Layer helpers
// ---------------------------------------------------------------------------

/// Gradient layer: T = T_base + lapse * (h - h_base)
fn gradient_layer(h: f64, h_base: f64, t_base: f64, lapse: f64, p_base: f64) -> (f64, f64) {
    let t = t_base + lapse * (h - h_base);
    let p = p_base * (t / t_base).powf(-G0 / (lapse * R_AIR));
    (t, p)
}

/// Isothermal layer: T = const, pressure decays exponentially
fn isothermal_layer(h: f64, h_base: f64, t: f64, p_base: f64) -> (f64, f64) {
    let p = p_base * ((-G0 / (R_AIR * t)) * (h - h_base)).exp();
    (t, p)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sea_level_standard_values() {
        let a = isa(0.0);
        assert!((a.temperature - 288.15).abs() < 0.01);
        assert!((a.pressure - 101_325.0).abs() < 1.0);
        assert!((a.density - 1.225).abs() < 0.001);
        assert!((a.sound_speed - 340.29).abs() < 0.1);
    }

    #[test]
    fn tropopause_11km() {
        let a = isa(11_000.0);
        assert!((a.temperature - 216.65).abs() < 0.5);
        assert!((a.pressure - 22_632.0).abs() < 100.0);
    }

    #[test]
    fn density_monotonically_decreases() {
        let rho_0 = isa(0.0).density;
        let rho_10k = isa(10_000.0).density;
        let rho_50k = isa(50_000.0).density;
        assert!(rho_0 > rho_10k);
        assert!(rho_10k > rho_50k);
        assert!(rho_50k > 0.0);
    }

    #[test]
    fn negative_altitude_clamps_to_sea_level() {
        let a = isa(-500.0);
        assert!((a.temperature - 288.15).abs() < 0.01);
    }

    #[test]
    fn near_vacuum_above_86km() {
        let a = isa(100_000.0);
        assert!(a.density < 1e-5);
        assert!(a.pressure < 1.0);
    }

    #[test]
    fn humidity_zero_matches_dry_atmosphere() {
        assert!((isa(0.0).density - isa_with_humidity(0.0, 0.0).density).abs() < 1e-12);
    }

    #[test]
    fn humidity_reduces_density_monotonically() {
        let dry = isa_with_humidity(0.0, 0.0);
        let half = isa_with_humidity(0.0, 0.5);
        let high = isa_with_humidity(0.0, 0.95);
        assert!(high.density < half.density && half.density < dry.density);
        assert_eq!(dry.sound_speed, high.sound_speed);
    }

    #[test]
    fn humidity_saturation_pressure_matches_openrocket_formula() {
        let saturation_pressure = E0 * (E_A - E_B / 293.0).exp();
        assert!((saturation_pressure - 2346.7).abs() < 10.0);
    }

    #[test]
    fn mission_launch_conditions_anchor_temperature_and_pressure() {
        let a = isa_from_launch_conditions(0.0, 3.0, 303.25, 100_000.0, 0.82);
        assert!((a.temperature - 303.25).abs() < 1e-9);
        assert!((a.pressure - 100_000.0).abs() < 1e-6);
        assert!(a.density.is_finite() && a.density > 0.0);
    }
}
