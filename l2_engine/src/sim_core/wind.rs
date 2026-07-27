use std::error::Error;
use std::fs::File;
use std::io::{BufRead, BufReader};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WindLevel {
    pub altitude_m: f64,
    pub speed_ms: f64,
    pub direction_deg: f64,
    pub std_dev_ms: f64,
}

#[derive(Debug, Clone)]
pub struct WindProfile {
    levels: Vec<WindLevel>,
}

impl WindProfile {
    pub fn new(levels: Vec<WindLevel>) -> Self {
        Self { levels }
    }

    pub fn empty() -> Self {
        Self { levels: Vec::new() }
    }

    pub fn from_csv(path: &str) -> Result<Self, Box<dyn Error>> {
        let file = File::open(path)?;
        let reader = BufReader::new(file);
        let mut levels = Vec::new();

        let mut lines = reader.lines();
        let _header = lines.next(); // Skip header

        for line in lines {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let parts: Vec<&str> = line.split(',').collect();
            if parts.len() < 4 {
                continue;
            }
            let altitude_m: f64 = parts[0].trim().parse()?;
            let speed_ms: f64 = parts[1].trim().parse()?;
            let direction_deg: f64 = parts[2].trim().parse()?;
            let std_dev_ms: f64 = parts[3].trim().parse()?;

            levels.push(WindLevel {
                altitude_m,
                speed_ms,
                direction_deg,
                std_dev_ms,
            });
        }

        levels.sort_by(|a, b| a.altitude_m.partial_cmp(&b.altitude_m).unwrap());

        Ok(Self { levels })
    }

    pub fn wind_vector_at(&self, alt_m: f64) -> (f64, f64) {
        if self.levels.is_empty() {
            return (0.0, 0.0);
        }

        if alt_m <= self.levels.first().unwrap().altitude_m {
            let l = self.levels.first().unwrap();
            return Self::polar_to_cartesian(l.speed_ms, l.direction_deg);
        }

        if alt_m >= self.levels.last().unwrap().altitude_m {
            let l = self.levels.last().unwrap();
            return Self::polar_to_cartesian(l.speed_ms, l.direction_deg);
        }

        // Linear interpolation
        for w in self.levels.windows(2) {
            let l0 = &w[0];
            let l1 = &w[1];
            if alt_m >= l0.altitude_m && alt_m <= l1.altitude_m {
                let frac = (alt_m - l0.altitude_m) / (l1.altitude_m - l0.altitude_m);
                let speed = l0.speed_ms + frac * (l1.speed_ms - l0.speed_ms);
                
                // Interpolate direction (handling wraparound)
                let mut d0 = l0.direction_deg;
                let mut d1 = l1.direction_deg;
                if (d1 - d0).abs() > 180.0 {
                    if d1 > d0 {
                        d0 += 360.0;
                    } else {
                        d1 += 360.0;
                    }
                }
                let mut dir = d0 + frac * (d1 - d0);
                if dir >= 360.0 {
                    dir -= 360.0;
                } else if dir < 0.0 {
                    dir += 360.0;
                }

                return Self::polar_to_cartesian(speed, dir);
            }
        }
        
        (0.0, 0.0)
    }

    fn polar_to_cartesian(speed: f64, direction_deg: f64) -> (f64, f64) {
        // Wind blows FROM direction_deg. To get the vector it's pushing TOWARDS, we invert it.
        // Wait, standard meteorological wind direction: "288" means wind comes FROM 288.
        // The velocity of the air mass is therefore going TOWARDS 108.
        // N is 0, E is 90.
        // direction_deg = 288 (WNW).
        // Sin(288) = -0.95 (West). Cos(288) = 0.309 (North).
        // This is where it's coming from.
        // The air velocity vector is (-speed * sin, -speed * cos).
        let rad = direction_deg.to_radians();
        let east = -speed * rad.sin();
        let north = -speed * rad.cos();
        (east, north)
    }
}
