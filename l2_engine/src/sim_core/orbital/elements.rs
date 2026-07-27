use nalgebra::Vector3;

use crate::sim_core::physics::gravity::{MU_EARTH, R_EARTH_ECI};

/// Classical Keplerian orbital elements.
#[derive(Debug, Clone, Copy)]
pub struct KeplerianElements {
    pub sma: f64,       // semi-major axis, m
    pub ecc: f64,       // eccentricity (0 = circular)
    pub inc: f64,       // inclination, rad
    pub raan: f64,      // right ascension of ascending node, rad
    pub argp: f64,      // argument of periapsis, rad
    pub true_anom: f64, // true anomaly, rad
}

impl KeplerianElements {
    /// Convert Keplerian elements to ECI state vector (position, velocity).
    pub fn to_state_vector(&self) -> (Vector3<f64>, Vector3<f64>) {
        self.to_state_vector_mu(MU_EARTH)
    }

    /// Convert with explicit gravitational parameter.
    pub fn to_state_vector_mu(&self, mu: f64) -> (Vector3<f64>, Vector3<f64>) {
        let p = self.sma * (1.0 - self.ecc * self.ecc); // semi-latus rectum
        let r_pqw = p / (1.0 + self.ecc * self.true_anom.cos());

        // Position in perifocal frame (PQW)
        let r_pqw_vec = Vector3::new(
            r_pqw * self.true_anom.cos(),
            r_pqw * self.true_anom.sin(),
            0.0,
        );

        // Velocity in perifocal frame
        let sqrt_mu_p = (mu / p).sqrt();
        let v_pqw_vec = Vector3::new(
            -sqrt_mu_p * self.true_anom.sin(),
            sqrt_mu_p * (self.ecc + self.true_anom.cos()),
            0.0,
        );

        // Rotation matrix from PQW to ECI
        let cos_raan = self.raan.cos();
        let sin_raan = self.raan.sin();
        let cos_argp = self.argp.cos();
        let sin_argp = self.argp.sin();
        let cos_inc = self.inc.cos();
        let sin_inc = self.inc.sin();

        let rot = |v: &Vector3<f64>| -> Vector3<f64> {
            Vector3::new(
                (cos_raan * cos_argp - sin_raan * sin_argp * cos_inc) * v.x
                    + (-cos_raan * sin_argp - sin_raan * cos_argp * cos_inc) * v.y,
                (sin_raan * cos_argp + cos_raan * sin_argp * cos_inc) * v.x
                    + (-sin_raan * sin_argp + cos_raan * cos_argp * cos_inc) * v.y,
                (sin_argp * sin_inc) * v.x + (cos_argp * sin_inc) * v.y,
            )
        };

        (rot(&r_pqw_vec), rot(&v_pqw_vec))
    }

    /// Convert ECI state vector to Keplerian elements.
    pub fn from_state_vector(pos: &Vector3<f64>, vel: &Vector3<f64>) -> Self {
        Self::from_state_vector_mu(pos, vel, MU_EARTH)
    }

    /// Convert with explicit gravitational parameter.
    pub fn from_state_vector_mu(pos: &Vector3<f64>, vel: &Vector3<f64>, mu: f64) -> Self {
        let r = pos.norm();
        let v = vel.norm();

        // Angular momentum
        let h = pos.cross(vel);
        let h_mag = h.norm();

        // Node vector
        let n = Vector3::new(-h.y, h.x, 0.0);
        let n_mag = n.norm();

        // Eccentricity vector
        let e_vec = ((v * v - mu / r) * pos - pos.dot(vel) * vel) / mu;
        let ecc = e_vec.norm();

        // Semi-major axis
        let energy = 0.5 * v * v - mu / r;
        let sma = if ecc.abs() < 1.0 - 1e-10 {
            -mu / (2.0 * energy)
        } else {
            h_mag * h_mag / (mu * (1.0 - ecc * ecc).abs())
        };

        // Inclination
        let inc = (h.z / h_mag).clamp(-1.0, 1.0).acos();

        // RAAN
        let raan = if n_mag > 1e-10 {
            let r = (n.x / n_mag).clamp(-1.0, 1.0).acos();
            if n.y < 0.0 {
                2.0 * std::f64::consts::PI - r
            } else {
                r
            }
        } else {
            0.0
        };

        // Argument of periapsis
        let argp = if n_mag > 1e-10 && ecc > 1e-10 {
            let cos_argp = (n.dot(&e_vec) / (n_mag * ecc)).clamp(-1.0, 1.0);
            let w = cos_argp.acos();
            if e_vec.z < 0.0 {
                2.0 * std::f64::consts::PI - w
            } else {
                w
            }
        } else {
            0.0
        };

        // True anomaly
        let true_anom = if ecc > 1e-10 {
            let cos_nu = (e_vec.dot(pos) / (ecc * r)).clamp(-1.0, 1.0);
            let nu = cos_nu.acos();
            if pos.dot(vel) < 0.0 {
                2.0 * std::f64::consts::PI - nu
            } else {
                nu
            }
        } else {
            0.0
        };

        KeplerianElements {
            sma,
            ecc,
            inc,
            raan,
            argp,
            true_anom,
        }
    }

    /// Orbital period for elliptical orbit (s).
    pub fn period(&self) -> f64 {
        self.period_mu(MU_EARTH)
    }

    pub fn period_mu(&self, mu: f64) -> f64 {
        2.0 * std::f64::consts::PI * (self.sma.powi(3) / mu).sqrt()
    }

    /// Create a circular orbit at given altitude and inclination.
    pub fn circular(altitude: f64, inc: f64) -> Self {
        KeplerianElements {
            sma: R_EARTH_ECI + altitude,
            ecc: 0.0,
            inc,
            raan: 0.0,
            argp: 0.0,
            true_anom: 0.0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn circular_leo_roundtrip() {
        let orbit = KeplerianElements::circular(400_000.0, 51.6_f64.to_radians());
        let (pos, vel) = orbit.to_state_vector();

        let recovered = KeplerianElements::from_state_vector(&pos, &vel);
        assert!((recovered.sma - orbit.sma).abs() < 1.0, "SMA mismatch");
        assert!(recovered.ecc < 1e-6, "Should be nearly circular");
        assert!(
            (recovered.inc - orbit.inc).abs() < 1e-6,
            "Inclination mismatch"
        );
    }

    #[test]
    fn circular_orbit_speed() {
        let alt = 400_000.0;
        let orbit = KeplerianElements::circular(alt, 0.0);
        let (_, vel) = orbit.to_state_vector();
        let expected = (MU_EARTH / (R_EARTH_ECI + alt)).sqrt();
        assert!(
            (vel.norm() - expected).abs() < 1.0,
            "Circular orbit speed mismatch"
        );
    }

    #[test]
    fn leo_period() {
        let orbit = KeplerianElements::circular(400_000.0, 0.0);
        let period = orbit.period();
        // ISS period ~92 min = ~5540 s
        assert!(
            period > 5400.0 && period < 5700.0,
            "LEO period should be ~92 min, got {:.0} s",
            period
        );
    }
}
