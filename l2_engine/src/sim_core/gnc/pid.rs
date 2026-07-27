// ---------------------------------------------------------------------------
// PID Controller (single axis)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct Pid {
    pub kp: f64,
    pub ki: f64,
    pub kd: f64,
    integral: f64,
    prev_error: f64,
}

impl Pid {
    pub fn new(kp: f64, ki: f64, kd: f64) -> Self {
        Self {
            kp,
            ki,
            kd,
            integral: 0.0,
            prev_error: 0.0,
        }
    }

    pub fn update(&mut self, error: f64, dt: f64) -> f64 {
        self.integral += error * dt;
        // Anti-windup: clamp integral to prevent saturation
        self.integral = self.integral.clamp(-1.0, 1.0);
        let derivative = if dt > 0.0 {
            (error - self.prev_error) / dt
        } else {
            0.0
        };
        self.prev_error = error;
        self.kp * error + self.ki * self.integral + self.kd * derivative
    }

    pub fn reset(&mut self) {
        self.integral = 0.0;
        self.prev_error = 0.0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pid_proportional() {
        let mut pid = Pid::new(1.0, 0.0, 0.0);
        let out = pid.update(0.5, 0.01);
        assert!((out - 0.5).abs() < 1e-10, "Pure P should output Kp * error");
    }

    #[test]
    fn pid_integral_accumulates() {
        let mut pid = Pid::new(0.0, 1.0, 0.0);
        pid.update(1.0, 0.1);
        let out = pid.update(1.0, 0.1);
        assert!((out - 0.2).abs() < 1e-10, "Integral should accumulate");
    }
}
