use crate::geometry::NoseShape;

pub fn calculate_stagnation_cd(m: f64) -> f64 {
    let pressure = if m <= 1.0 {
        1.0 + m.powi(2) / 4.0 + m.powi(4) / 40.0
    } else {
        1.84 - 0.76 / m.powi(2) + 0.166 / m.powi(4) + 0.035 / m.powi(6)
    };
    0.85 * pressure
}

fn interpolate_linear(x_points: &[f64], y_points: &[f64], x: f64) -> f64 {
    if x <= x_points[0] {
        return y_points[0];
    }
    let last = x_points.len() - 1;
    if x >= x_points[last] {
        return y_points[last];
    }
    for i in 0..last {
        if x >= x_points[i] && x <= x_points[i + 1] {
            let t = (x - x_points[i]) / (x_points[i + 1] - x_points[i]);
            return y_points[i] + t * (y_points[i + 1] - y_points[i]);
        }
    }
    0.0
}

fn eval_conical_poly(x: f64, cd_mach1: f64, cd_mach1_3: f64, d1: f64, d1_3: f64) -> f64 {
    let t = (x - 1.0) / 0.3;
    let h00 = 2.0 * t.powi(3) - 3.0 * t.powi(2) + 1.0;
    let h10 = t.powi(3) - 2.0 * t.powi(2) + t;
    let h01 = -2.0 * t.powi(3) + 3.0 * t.powi(2);
    let h11 = t.powi(3) - t.powi(2);

    let m0 = d1 * 0.3;
    let m1 = d1_3 * 0.3;

    h00 * cd_mach1 + h10 * m0 + h01 * cd_mach1_3 + h11 * m1
}

pub fn calculate_nose_pressure_cd(shape: NoseShape, param: f64, fineness: f64, mach: f64) -> f64 {
    let sinphi = if fineness > 0.0 {
        if shape == NoseShape::Ogive && (param - 1.0).abs() < 1e-5 {
            0.0
        } else {
            1.0 / (1.0 + 4.0 * fineness.powi(2)).sqrt()
        }
    } else {
        0.0
    };

    let m_ellipsoid = [1.2, 1.25, 1.3, 1.4, 1.6, 2.0, 2.4];
    let cd_ellipsoid = [0.110, 0.128, 0.140, 0.148, 0.152, 0.159, 0.162];

    let m_x14 = [1.2, 1.3, 1.4, 1.6, 1.8, 2.2, 2.6, 3.0, 3.6];
    let cd_x14 = [
        0.140, 0.156, 0.169, 0.192, 0.206, 0.227, 0.241, 0.249, 0.252,
    ];

    let m_x12 = [0.925, 0.95, 1.0, 1.05, 1.1, 1.2, 1.3, 1.7, 2.0];
    let cd_x12 = [0.0, 0.014, 0.050, 0.060, 0.059, 0.081, 0.084, 0.085, 0.078];

    let m_x34 = [0.8, 0.9, 1.0, 1.06, 1.2, 1.4, 1.6, 2.0, 2.8, 3.4];
    let cd_x34 = [
        0.0, 0.015, 0.078, 0.121, 0.110, 0.098, 0.090, 0.084, 0.078, 0.074,
    ];

    let m_karman = [0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.4, 1.6, 2.0, 3.0];
    let cd_karman = [
        0.0, 0.010, 0.027, 0.055, 0.070, 0.081, 0.095, 0.097, 0.091, 0.083,
    ];

    let m_haack = [0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.4, 1.6, 2.0];
    let cd_haack = [0.0, 0.010, 0.024, 0.066, 0.084, 0.100, 0.114, 0.117, 0.113];

    let m_para = [0.95, 0.975, 1.0, 1.05, 1.1, 1.2, 1.4, 1.7];
    let cd_para = [0.0, 0.016, 0.041, 0.092, 0.109, 0.119, 0.113, 0.108];

    let m_para12 = [0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.3, 1.5, 1.8];
    let cd_para12 = [0.0, 0.016, 0.042, 0.100, 0.126, 0.125, 0.100, 0.090, 0.088];

    let m_para34 = [0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.4, 1.7];
    let cd_para34 = [0.0, 0.023, 0.073, 0.098, 0.107, 0.106, 0.089, 0.082];

    let get_blunt = |m: f64| calculate_stagnation_cd(m);

    let calc_ogive = |m: f64, p: f64, sphi: f64| -> f64 {
        if m < 1.0 {
            0.0
        } else if m <= 1.3 {
            let cd_mach1 = sphi;
            let cd_mach1_3 = 2.1 * sphi.powi(2) + 0.6019 * sphi;
            let gamma_air = 1.4;
            let d1 = (4.0 / (gamma_air + 1.0)) * (1.0 - 0.5 * cd_mach1);
            let d1_3 = -1.1341 * sphi;
            let mul = 0.72 * (p - 0.5).powi(2) + 0.82;
            mul * eval_conical_poly(m, cd_mach1, cd_mach1_3, d1, d1_3)
        } else {
            let mul = 0.72 * (p - 0.5).powi(2) + 0.82;
            mul * (2.1 * sphi.powi(2) + 0.5 * sphi / (m * m - 1.0).sqrt())
        }
    };

    let mut base_cd_at_f3 = 0.0;
    let min_m: f64;

    match shape {
        NoseShape::Conical => {
            if mach >= 1.0 {
                return calc_ogive(mach, 0.0, sinphi);
            }
            min_m = 1.0;
        }
        NoseShape::Ogive => {
            if mach >= 1.0 {
                return calc_ogive(mach, param, sinphi);
            }
            min_m = 1.0;
        }
        NoseShape::Ellipsoid => {
            base_cd_at_f3 = interpolate_linear(&m_ellipsoid, &cd_ellipsoid, mach);
            min_m = m_ellipsoid[0];
        }
        NoseShape::VonKarmanHaack => {
            let p = param * 3.0;
            let v_vk = interpolate_linear(&m_karman, &cd_karman, mach);
            let v_lvh = interpolate_linear(&m_haack, &cd_haack, mach);
            base_cd_at_f3 = p * v_lvh + (1.0 - p) * v_vk;
            min_m = m_karman[0].min(m_haack[0]);
        }
        NoseShape::PowerSeries => {
            let (p, v1, v2);
            if param <= 0.25 {
                v1 = get_blunt(mach);
                v2 = interpolate_linear(&m_x14, &cd_x14, mach);
                p = param * 4.0;
                min_m = m_x14[0];
            } else if param <= 0.5 {
                v1 = interpolate_linear(&m_x14, &cd_x14, mach);
                v2 = interpolate_linear(&m_x12, &cd_x12, mach);
                p = (param - 0.25) * 4.0;
                min_m = m_x14[0].min(m_x12[0]);
            } else if param <= 0.75 {
                v1 = interpolate_linear(&m_x12, &cd_x12, mach);
                v2 = interpolate_linear(&m_x34, &cd_x34, mach);
                p = (param - 0.5) * 4.0;
                min_m = m_x12[0].min(m_x34[0]);
            } else {
                v1 = interpolate_linear(&m_x34, &cd_x34, mach);
                let f3_sphi = 1.0 / (1.0 + 4.0 * fineness.powi(2)).sqrt();
                v2 = calc_ogive(mach, 0.0, f3_sphi);
                p = (param - 0.75) * 4.0;
                min_m = m_x34[0].min(1.0);
            }
            base_cd_at_f3 = p * v2 + (1.0 - p) * v1;
        }
        NoseShape::Parabolic => {
            let (p, v1, v2);
            if param <= 0.5 {
                let f3_sphi = 1.0 / (1.0 + 4.0 * fineness.powi(2)).sqrt();
                v1 = calc_ogive(mach, 0.0, f3_sphi);
                v2 = interpolate_linear(&m_para12, &cd_para12, mach);
                p = param * 2.0;
                min_m = m_para12[0].min(1.0);
            } else if param <= 0.75 {
                v1 = interpolate_linear(&m_para12, &cd_para12, mach);
                v2 = interpolate_linear(&m_para34, &cd_para34, mach);
                p = (param - 0.5) * 4.0;
                min_m = m_para12[0].min(m_para34[0]);
            } else {
                v1 = interpolate_linear(&m_para34, &cd_para34, mach);
                v2 = interpolate_linear(&m_para, &cd_para, mach);
                p = (param - 0.75) * 4.0;
                min_m = m_para34[0].min(m_para[0]);
            }
            base_cd_at_f3 = p * v2 + (1.0 - p) * v1;
        }
    }

    if mach >= min_m
        && (shape == NoseShape::Ellipsoid
            || shape == NoseShape::VonKarmanHaack
            || shape == NoseShape::PowerSeries
            || shape == NoseShape::Parabolic)
    {
        let log4 = (fineness + 1.0).ln() / 4.0_f64.ln();
        let stag = get_blunt(mach);
        if stag > 0.0 && base_cd_at_f3 > 0.0 {
            return stag * (base_cd_at_f3 / stag).powf(log4);
        } else {
            return 0.0;
        }
    }

    let get_f3_at = |m: f64| -> f64 {
        match shape {
            NoseShape::Conical => calc_ogive(m, 0.0, sinphi),
            NoseShape::Ogive => calc_ogive(m, param, sinphi),
            NoseShape::Ellipsoid => interpolate_linear(&m_ellipsoid, &cd_ellipsoid, m),
            NoseShape::VonKarmanHaack => {
                let p = param * 3.0;
                p * interpolate_linear(&m_haack, &cd_haack, m)
                    + (1.0 - p) * interpolate_linear(&m_karman, &cd_karman, m)
            }
            NoseShape::PowerSeries => {
                let (p, v1, v2);
                if param <= 0.25 {
                    v1 = get_blunt(m);
                    v2 = interpolate_linear(&m_x14, &cd_x14, m);
                    p = param * 4.0;
                } else if param <= 0.5 {
                    v1 = interpolate_linear(&m_x14, &cd_x14, m);
                    v2 = interpolate_linear(&m_x12, &cd_x12, m);
                    p = (param - 0.25) * 4.0;
                } else if param <= 0.75 {
                    v1 = interpolate_linear(&m_x12, &cd_x12, m);
                    v2 = interpolate_linear(&m_x34, &cd_x34, m);
                    p = (param - 0.5) * 4.0;
                } else {
                    v1 = interpolate_linear(&m_x34, &cd_x34, m);
                    let f3_sphi = 1.0 / (1.0 + 4.0 * fineness.powi(2)).sqrt();
                    v2 = calc_ogive(m, 0.0, f3_sphi);
                    p = (param - 0.75) * 4.0;
                }
                p * v2 + (1.0 - p) * v1
            }
            NoseShape::Parabolic => {
                let (p, v1, v2);
                if param <= 0.5 {
                    let f3_sphi = 1.0 / (1.0 + 4.0 * fineness.powi(2)).sqrt();
                    v1 = calc_ogive(m, 0.0, f3_sphi);
                    v2 = interpolate_linear(&m_para12, &cd_para12, m);
                    p = param * 2.0;
                } else if param <= 0.75 {
                    v1 = interpolate_linear(&m_para12, &cd_para12, m);
                    v2 = interpolate_linear(&m_para34, &cd_para34, m);
                    p = (param - 0.5) * 4.0;
                } else {
                    v1 = interpolate_linear(&m_para34, &cd_para34, m);
                    v2 = interpolate_linear(&m_para, &cd_para, m);
                    p = (param - 0.75) * 4.0;
                }
                p * v2 + (1.0 - p) * v1
            }
        }
    };

    let get_extrapolated = |m: f64| -> f64 {
        if shape == NoseShape::Conical || shape == NoseShape::Ogive {
            return get_f3_at(m);
        }
        let base_val = get_f3_at(m);
        let stag = get_blunt(m);
        let log4 = (fineness + 1.0).ln() / 4.0_f64.ln();
        if stag > 0.0 && base_val > 0.0 {
            stag * (base_val / stag).powf(log4)
        } else {
            0.0
        }
    };

    let min_value = get_extrapolated(min_m);
    if min_value < 0.001 {
        return 0.0;
    }

    let cd_mach0 = 0.8 * sinphi.powi(2);
    let min_deriv = (get_extrapolated(min_m + 0.01) - min_value) / 0.01;

    if cd_mach0 >= min_value - 0.01 || min_deriv <= 0.01 {
        return 0.0;
    }

    let b = min_m * min_deriv / (min_value - cd_mach0);
    let a = (min_value - cd_mach0) / min_m.powf(b);

    if mach < min_m {
        a * mach.powf(b) + cd_mach0
    } else {
        0.0
    }
}
