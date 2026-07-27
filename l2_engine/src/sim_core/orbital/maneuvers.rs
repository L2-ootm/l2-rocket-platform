use crate::sim_core::physics::gravity::MU_EARTH;

/// Result of a Hohmann transfer calculation.
#[derive(Debug, Clone, Copy)]
pub struct HohmannTransfer {
    pub dv1: f64,           // m/s, first burn (raise apoapsis)
    pub dv2: f64,           // m/s, second burn (circularize)
    pub total_dv: f64,      // m/s, total delta-v
    pub transfer_time: f64, // s, half the transfer orbit period
    pub r1: f64,            // m, initial orbit radius
    pub r2: f64,            // m, final orbit radius
}

/// Compute Hohmann transfer between two circular orbits.
///
/// `r1` and `r2` are orbital radii (not altitudes), in meters.
/// `mu` is the gravitational parameter (defaults to Earth).
pub fn hohmann(r1: f64, r2: f64) -> HohmannTransfer {
    hohmann_mu(r1, r2, MU_EARTH)
}

pub fn hohmann_mu(r1: f64, r2: f64, mu: f64) -> HohmannTransfer {
    let a_transfer = (r1 + r2) / 2.0;

    let v_circ1 = (mu / r1).sqrt();
    let v_circ2 = (mu / r2).sqrt();

    let v_transfer_1 = (mu * (2.0 / r1 - 1.0 / a_transfer)).sqrt();
    let v_transfer_2 = (mu * (2.0 / r2 - 1.0 / a_transfer)).sqrt();

    let dv1 = (v_transfer_1 - v_circ1).abs();
    let dv2 = (v_circ2 - v_transfer_2).abs();

    let transfer_time = std::f64::consts::PI * (a_transfer.powi(3) / mu).sqrt();

    HohmannTransfer {
        dv1,
        dv2,
        total_dv: dv1 + dv2,
        transfer_time,
        r1,
        r2,
    }
}

/// Calculate circular orbit velocity at a given radius.
pub fn circular_velocity(r: f64) -> f64 {
    circular_velocity_mu(r, MU_EARTH)
}

pub fn circular_velocity_mu(r: f64, mu: f64) -> f64 {
    (mu / r).sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sim_core::physics::gravity::R_EARTH_ECI;

    #[test]
    fn hohmann_leo_to_geo() {
        let r_leo = R_EARTH_ECI + 200_000.0; // 200 km LEO
        let r_geo = 42_164_000.0; // GEO radius
        let h = hohmann(r_leo, r_geo);

        // Known values: ~2.46 km/s + ~1.48 km/s ≈ 3.94 km/s total
        assert!(
            h.total_dv > 3800.0 && h.total_dv < 4100.0,
            "LEO→GEO dv should be ~3.94 km/s, got {:.0} m/s",
            h.total_dv
        );
        // Transfer time ~5.3 hours
        assert!(
            h.transfer_time > 18_000.0 && h.transfer_time < 20_000.0,
            "Transfer time should be ~5.3 hr, got {:.0} s",
            h.transfer_time
        );
    }

    #[test]
    fn zero_dv_for_same_orbit() {
        let r = R_EARTH_ECI + 400_000.0;
        let h = hohmann(r, r);
        assert!(h.total_dv < 1e-6);
    }
}
