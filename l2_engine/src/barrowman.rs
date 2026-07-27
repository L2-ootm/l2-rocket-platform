//! Barrowman aerodynamics: Center of Pressure (CP) and Normal Force
//! Coefficient slope (CNa) for the nosecone and freeform fins, plus a
//! Mach-dependent drag table. Consumes the `StageGeometry` type contract
//! produced by `geometry.rs`/`xml_parser.rs` (Plan 03) and produces the
//! exact `(cp_offset, cn_alpha, cd_table)` shape Plan 02's patched
//! `rocket-sim` `StageBuilder` accepts. See 01-06-PLAN.md.
//!
//! Formula provenance: 01-RESEARCH.md "Code Examples -> Fin CNa" (verbatim
//! subsonic formula + body-fin/fin-count interference factors, traced to
//! OpenRocket's `FinSetCalc.java`) and 01-RESEARCH.md Common Pitfall 2
//! (transonic/supersonic drag and fin CNa are table-driven in OpenRocket;
//! the real Busemann K1/K2/K3 table values were not transcribed verbatim,
//! so this module uses the documented pragmatic fallback -- linearized
//! supersonic theory for fin CNa, and a hand-derived friction+transonic-peak
//! drag model for `compute_cd_table` -- rather than fabricating numbers).

use crate::errors::L2EngineError;
use crate::geometry::{FinsetGeometry, NoseconeGeometry, StageGeometry};
use std::f64::consts::PI;

/// Combined Barrowman aerodynamic coefficients for one stage, in the exact
/// shape Plan 02's patched `rocket-sim` `StageBuilder` expects
/// (`.cp_offset()`/`.cn_alpha()`/`.area()`/`.cd_table()`).
#[derive(Debug, Clone)]
pub struct AerodynamicCoefficients {
    pub cp_offset_from_cg: f64,
    pub cn_alpha: f64,
    pub damping_moment_sum_m2: f64,
    pub pitch_damping_multiplier: f64,
    pub reference_area: f64,
    pub cd_table: Vec<(f64, f64)>,
    pub cd_nonfric_table: Vec<(f64, f64)>,
    pub friction_params: crate::sim_core::vehicle::FrictionParams,
}

/// Geometric properties of a freeform finset derived via general
/// strip-integration (root/tip chord, span, mid-chord sweep distance,
/// exposed area), independent of the specific point count/shape.
#[derive(Debug, Clone)]
pub struct FinGeometryDerived {
    pub root_chord: f64,
    pub tip_chord: f64,
    pub span: f64,
    pub mid_chord_sweep_distance: f64,
    pub exposed_area: f64,
    pub cos_gamma_lead: f64,
    pub mac_length: f64,
    pub mac_lead: f64,
    pub aspect_ratio: f64,
}

/// Von Karman/Haack-series nosecone radius profile. Duplicated from
/// `mass_calculator::haack_profile_radius` (private there, and this plan's
/// `files_modified` list is scoped to `barrowman.rs` only) rather than
/// widening that module's visibility -- identical formula, same shape
/// family this vehicle actually uses (`shapeparameter=0.0`).
fn haack_profile_radius(x: f64, length: f64, aft_radius: f64, shape_parameter: f64) -> f64 {
    let x_clamped = x.clamp(0.0, length);
    let theta = (1.0 - 2.0 * x_clamped / length).acos();
    let under_sqrt = theta - (2.0 * theta).sin() / 2.0 + shape_parameter * theta.sin().powi(3);
    aft_radius / PI.sqrt() * under_sqrt.max(0.0).sqrt()
}

/// Classical Barrowman nosecone CP (shape-agnostic, volume-based):
/// `X_cp = L_nose - Volume_nose / Area_base`, where `Volume_nose` is
/// numerically integrated (200 disk slices) using the same Haack-series
/// profile as Plan 05's `nosecone_mass`, and `Area_base = pi * aft_radius^2`.
/// Nosecone `cn_alpha` is the classical Barrowman constant `2.0` for any
/// pointed nose shape, always -- Mach-dependence enters only through the
/// drag table, not through nosecone CP/CNa (per 01-06-PLAN.md's interfaces
/// block).
///
/// Returns `(cp_offset_from_tip, cn_alpha)`.
pub fn nosecone_cp_and_cna(nc: &NoseconeGeometry) -> (f64, f64) {
    const SLICES: usize = 200;
    let dx = nc.length / SLICES as f64;

    let mut volume = 0.0_f64;
    for i in 0..SLICES {
        let x_mid = (i as f64 + 0.5) * dx;
        let r = haack_profile_radius(x_mid, nc.length, nc.aft_radius, nc.shape_parameter);
        volume += PI * r * r * dx;
    }

    let area_base = PI * nc.aft_radius.powi(2);
    let cp_offset_from_tip = if area_base > 0.0 {
        nc.length - volume / area_base
    } else {
        nc.length * 0.5
    };

    (cp_offset_from_tip, 2.0)
}

/// Linearly interpolates `x` at a given `y` along a polyline chain that is
/// monotonic in `y` (either increasing or decreasing). Clamps to the
/// nearest endpoint if `y` falls outside the chain's range (defensive --
/// shouldn't happen for well-formed `.ork` freeform fin outlines within
/// `[0, span]`).
fn interp_x_at_y(edge: &[(f64, f64)], y: f64) -> f64 {
    if edge.len() == 1 {
        return edge[0].0;
    }
    for w in edge.windows(2) {
        let (x0, y0) = w[0];
        let (x1, y1) = w[1];
        let (lo, hi) = if y0 <= y1 { (y0, y1) } else { (y1, y0) };
        if y >= lo - 1e-9 && y <= hi + 1e-9 {
            if (y1 - y0).abs() < 1e-12 {
                return x0;
            }
            let frac = (y - y0) / (y1 - y0);
            return x0 + frac * (x1 - x0);
        }
    }
    let first = edge[0];
    let last = edge[edge.len() - 1];
    if (y - first.1).abs() < (y - last.1).abs() {
        first.0
    } else {
        last.0
    }
}

/// Freeform-fin geometric properties via general strip-integration (>= 20
/// spanwise slices, per 01-06-PLAN.md's behavior spec). Handles OpenRocket's
/// Compute freeform fin geometry (root/tip chords, span, mid-chord sweep, area, and leading-edge sweep).
pub fn fin_geometry(fs: &FinsetGeometry) -> FinGeometryDerived {
    let points = &fs.points;
    if points.is_empty() {
        return FinGeometryDerived {
            root_chord: 0.0,
            tip_chord: 0.0,
            span: 0.0,
            mid_chord_sweep_distance: 0.0,
            exposed_area: 0.0,
            cos_gamma_lead: 1.0,
            mac_length: 0.0,
            mac_lead: 0.0,
            aspect_ratio: 0.0,
        };
    }

    let span = points.iter().map(|p| p.1).fold(f64::MIN, f64::max).max(0.0);

    let root_le = points[0];
    let root_te = points[points.len() - 1];
    let root_chord = (root_te.0 - root_le.0).abs();

    let tip_start_idx = points
        .iter()
        .position(|p| (p.1 - span).abs() < 1e-9)
        .unwrap_or(0);
    let tip_end_idx = points
        .iter()
        .rposition(|p| (p.1 - span).abs() < 1e-9)
        .unwrap_or(points.len() - 1);

    let tip_le = points[tip_start_idx];
    let tip_te = points[tip_end_idx];
    let tip_chord = (tip_te.0 - tip_le.0).abs();
    let mid_chord_sweep_distance = tip_le.0 - root_le.0;

    let leading_edge = &points[0..=tip_start_idx];
    let trailing_edge = &points[tip_end_idx..points.len()];

    let y_min = points.iter().map(|p| p.1).fold(f64::MAX, f64::min).min(0.0);
    let integration_height = span - y_min;

    const SLICES: usize = 40; // >= 20 per behavior spec
    let dy = integration_height / SLICES as f64;
    let mut exposed_area = 0.0_f64;
    let mut cos_gamma_lead_sum = 0.0_f64;
    let mut mac_length_numerator = 0.0_f64;
    let mut mac_lead_numerator = 0.0_f64;

    if dy > 0.0 {
        let mut le_x_prev = interp_x_at_y(leading_edge, y_min);
        for i in 0..SLICES {
            let y_curr = y_min + (i as f64 + 1.0) * dy;
            let y_mid = y_min + (i as f64 + 0.5) * dy;

            let le_x_curr = interp_x_at_y(leading_edge, y_curr);
            let te_x_mid = interp_x_at_y(trailing_edge, y_mid);
            let le_x_mid = interp_x_at_y(leading_edge, y_mid);

            let chord = (te_x_mid - le_x_mid).max(0.0);
            exposed_area += chord * dy;
            mac_length_numerator += chord * chord * dy;
            mac_lead_numerator += le_x_mid * chord * dy;

            let dx_le = le_x_curr - le_x_prev;
            let hypot_le = dx_le.hypot(dy);
            if hypot_le > 0.0 {
                cos_gamma_lead_sum += dy / hypot_le;
            }
            le_x_prev = le_x_curr;
        }
        cos_gamma_lead_sum /= SLICES as f64;
    }

    FinGeometryDerived {
        root_chord,
        tip_chord,
        span,
        mid_chord_sweep_distance,
        exposed_area,
        cos_gamma_lead: if cos_gamma_lead_sum == 0.0 {
            1.0
        } else {
            cos_gamma_lead_sum
        },
        mac_length: if exposed_area > 0.0 {
            mac_length_numerator / exposed_area
        } else {
            0.0
        },
        mac_lead: if exposed_area > 0.0 {
            mac_lead_numerator / exposed_area
        } else {
            0.0
        },
        aspect_ratio: if exposed_area > 0.0 {
            2.0 * span.powi(2) / exposed_area
        } else {
            0.0
        },
    }
}

/// Fin-count body-fin/fin-set interference factor, per 01-RESEARCH.md's
/// verbatim table (`FinSetCalc.java` provenance): `1.0` for `fin_count<=4`,
/// `0.948/0.913/0.854/0.81` for `5/6/7/8`, `0.75` for `>8`.
pub fn fin_count_interference_factor(fin_count: u32) -> f64 {
    match fin_count {
        0..=4 => 1.0,
        5 => 0.948,
        6 => 0.913,
        7 => 0.854,
        8 => 0.81,
        _ => 0.75,
    }
}

/// Cosine of the mid-chord sweep angle, used by the subsonic fin CNa
/// formula's `cosGamma` term.
fn mid_chord_sweep_cosine(fin: &FinGeometryDerived) -> f64 {
    if fin.span.abs() < 1e-9 {
        return 1.0;
    }
    let x_root_mc = fin.root_chord / 2.0;
    let x_tip_mc = fin.mid_chord_sweep_distance + fin.tip_chord / 2.0;
    let dx = x_tip_mc - x_root_mc;
    dx.atan2(fin.span).cos()
}

/// Subsonic (`mach <= 0.9`) per-panel CNa, verbatim from 01-RESEARCH.md's
/// Code Examples "Fin CNa" (`FinSetCalc.java` provenance):
/// `cna1 = 2*PI*span^2 / (1 + sqrt(1 + (1-mach^2)*(span^2/(finArea*cosGamma))^2)) / refArea`.
fn fin_cna_subsonic_raw(fin: &FinGeometryDerived, mach: f64, body_radius: f64) -> f64 {
    let ref_area = PI * body_radius.powi(2);
    if ref_area <= 0.0 || fin.exposed_area <= 0.0 {
        return 0.0;
    }
    let cos_gamma = mid_chord_sweep_cosine(fin);
    let beta_term = (fin.span.powi(2) / (fin.exposed_area * cos_gamma)).powi(2);
    let denom = 1.0 + (1.0 + (1.0 - mach.powi(2)) * beta_term).sqrt();
    (2.0 * PI * fin.span.powi(2)) / denom / ref_area
}

/// Supersonic per-panel CNa at alpha ~= 0, matching OpenRocket FinSetCalc's
/// `finArea * K1 / refArea` branch where `K1 = 2 / sqrt(mach^2 - 1)`.
fn fin_cna_supersonic_raw(
    fin: &FinGeometryDerived,
    mach: f64,
    aoa_rad: f64,
    body_radius: f64,
) -> f64 {
    let ref_area = PI * body_radius.powi(2);
    if ref_area <= 0.0 || fin.exposed_area <= 0.0 {
        return 0.0;
    }
    let beta = (mach.powi(2) - 1.0).max(1e-6).sqrt();
    let gamma = 1.4;
    let k1 = 2.0 / beta;
    let k2 = ((gamma + 1.0) * mach.powi(4) - 4.0 * beta.powi(2)) / (4.0 * beta.powi(4));
    let k3 = ((gamma + 1.0) * mach.powi(8)
        + (2.0 * gamma.powi(2) - 7.0 * gamma - 5.0) * mach.powi(6)
        + 10.0 * (gamma + 1.0) * mach.powi(4)
        + 8.0)
        / (6.0 * beta.powi(7));
    let alpha = aoa_rad.clamp(0.0, 20.0_f64.to_radians());
    fin.exposed_area * (k1 + k2 * alpha + k3 * alpha.powi(2)) / ref_area
}

/// Per-panel fin CNa at a given Mach number, blended subsonic -> transonic
/// -> supersonic, then scaled by body-fin interference `(1 + tau)` (where
/// `tau = bodyRadius / (span + bodyRadius)`) and the fin-count interference
/// factor. The transonic band (`0.9 < mach < 1.2`) is a documented linear
/// blend between the subsonic value at Mach 0.9 and the supersonic value at
/// Mach 1.2 -- OpenRocket uses a polynomial `cnaInterpolator` here per
/// 01-RESEARCH.md, but the exact polynomial coefficients were not
/// transcribed verbatim, so a linear blend is used as the documented
/// pragmatic fallback (matches value continuity at both band edges, avoids
/// fabricating polynomial coefficients).
pub fn fin_cna(fin: &FinGeometryDerived, mach: f64, fin_count: u32, body_radius: f64) -> f64 {
    fin_cna_at_aoa(fin, mach, 0.0, fin_count, body_radius)
}

/// OpenRocket's Galejs empirical body-lift multiplier.
pub const BODY_LIFT_K: f64 = 1.1;

/// Galejs normal-force contribution for projected symmetric-component area.
pub fn body_lift_cn(planform_area: f64, ref_area: f64, aoa: f64, mach: f64) -> f64 {
    if planform_area <= 0.0 || ref_area <= 0.0 {
        return 0.0;
    }

    let aoa = aoa.abs().min(PI);
    let sin_aoa = aoa.sin();
    let sinc = if aoa < 1e-4 {
        1.0 - aoa.powi(2) / 6.0
    } else {
        sin_aoa / aoa
    };
    let low_speed_multiplier = if mach < 0.05 && aoa > PI / 4.0 {
        (mach.max(0.0) / 0.05).powi(2)
    } else {
        1.0
    };

    low_speed_multiplier * BODY_LIFT_K * (planform_area / ref_area) * sin_aoa * sinc
}

/// Combined dynamic Galejs contribution and its absolute longitudinal CP.
pub fn body_lift_at_aoa(
    active_stages: &[&StageGeometry],
    aoa: f64,
    mach: f64,
    ref_area: f64,
) -> (f64, f64) {
    let mut total_cn = 0.0;
    let mut weighted_cp = 0.0;
    for stage in active_stages {
        let cn = body_lift_cn(stage.planform_area(), ref_area, aoa, mach);
        if cn > 0.0 {
            let cp = stage.axial_offset_m + stage.planform_center();
            total_cn += cn;
            weighted_cp += cn * cp;
        }
    }
    if total_cn > 1e-12 {
        (total_cn, weighted_cp / total_cn)
    } else {
        (0.0, 0.0)
    }
}

/// Planform area of one trapezoidal-equivalent fin.
pub fn fin_planform_area(fin: &FinGeometryDerived) -> f64 {
    fin.span * (fin.root_chord + fin.tip_chord) / 2.0
}

pub fn fin_cna_at_aoa(
    fin: &FinGeometryDerived,
    mach: f64,
    aoa_rad: f64,
    fin_count: u32,
    body_radius: f64,
) -> f64 {
    let tau = body_radius / (fin.span + body_radius);
    let interference = fin_count_interference_factor(fin_count);

    let cna1 = if mach <= 0.9 {
        fin_cna_subsonic_raw(fin, mach, body_radius)
    } else if mach < 1.5 {
        let cna_at_09 = fin_cna_subsonic_raw(fin, 0.9, body_radius);
        let cna_at_15 = fin_cna_supersonic_raw(fin, 1.5, aoa_rad, body_radius);
        let frac = (mach - 0.9) / (1.5 - 0.9);
        cna_at_09 + frac * (cna_at_15 - cna_at_09)
    } else {
        fin_cna_supersonic_raw(fin, mach, aoa_rad, body_radius)
    };

    cna1 * (1.0 + tau) * interference
}

/// Classical Barrowman fin CP position, measured from the fin's own root
/// chord leading edge:
/// `X_cp = Xr/3 * (Cr + 2*Ct)/(Cr+Ct) + 1/6 * (Cr + Ct - Cr*Ct/(Cr+Ct))`,
/// where `Xr` is the fin's `mid_chord_sweep_distance`.
fn fin_cp_from_root_le(fin: &FinGeometryDerived, mach: f64) -> f64 {
    if fin.mac_length <= 0.0 {
        return 0.0;
    }
    let ar = fin.aspect_ratio;
    let fraction = if mach <= 0.5 {
        0.25
    } else if mach >= 2.0 {
        let beta = (mach * mach - 1.0).max(0.0).sqrt();
        (ar * beta - 0.67) / (2.0 * ar * beta - 1.0)
    } else {
        // Exact fifth-order interpolation used by OpenRocket FinSetCalc.
        let denom = (1.0 - 3.4641 * ar).powi(2);
        let coefficients = [
            9.16049 * (-0.588838 + ar) * (-0.20624 + ar) / denom,
            -31.6049 * (-0.705375 + ar) * (-0.198476 + ar) / denom,
            55.3086 * (-0.711482 + ar) * (-0.196772 + ar) / denom,
            -39.5062 * (-0.72074 + ar) * (-0.194245 + ar) / denom,
            12.8395 * (-0.725688 + ar) * (-0.19292 + ar) / denom,
            -1.58025 * (-0.728769 + ar) * (-0.192105 + ar) / denom,
        ];
        coefficients
            .iter()
            .rev()
            .fold(0.0, |value, coefficient| value * mach + coefficient)
    };
    fin.mac_lead + fraction * fin.mac_length
}

/// Largest bodytube radius (or the nosecone's `aft_radius` if no bodytubes
/// are present), used as the reference-area radius for `cn_alpha`/drag
/// normalization.
fn reference_radius_from_stages(active_stages: &[&StageGeometry]) -> f64 {
    let body_max = active_stages
        .iter()
        .flat_map(|s| &s.bodytubes)
        .map(|bt| bt.radius)
        .fold(0.0_f64, f64::max);
    let nc_max = active_stages
        .iter()
        .flat_map(|s| &s.nosecone)
        .map(|nc| nc.aft_radius)
        .fold(0.0_f64, f64::max);
    body_max.max(nc_max)
}

/// Total stage length (nosecone + all bodytubes), used as the
/// characteristic length for the friction-drag component.
fn total_stage_length_from_stages(active_stages: &[&StageGeometry]) -> f64 {
    let mut total_len = 0.0;
    for s in active_stages {
        total_len += s.nosecone.as_ref().map(|nc| nc.length).unwrap_or(0.0);
        total_len += s.bodytubes.iter().map(|bt| bt.length).sum::<f64>();
    }
    total_len.max(1e-3)
}

/// Wetted area of the axisymmetric body components. Keep the historical
/// cylindrical proxy for both tubes and nosecones until the separately
/// missing OpenRocket friction contribution is modeled without compensation.
fn body_wetted_area_from_stages(active_stages: &[&StageGeometry]) -> f64 {
    active_stages
        .iter()
        .map(|stage| {
            let tubes = stage
                .bodytubes
                .iter()
                .map(|tube| 2.0 * PI * tube.radius * tube.length)
                .sum::<f64>();
            let nose = stage
                .nosecone
                .as_ref()
                .map(|nose| 2.0 * PI * nose.aft_radius * nose.length)
                .unwrap_or(0.0);
            tubes + nose
        })
        .sum()
}

/// Effective fin friction area (both surfaces and exposed edges, all panels).
/// OpenRocket FinSetCalc scales the two-sided planform by
/// `1 + 2*thickness/MAC` to include fin-edge skin friction.
///
/// [Rule 2 fix, 01-08]: `cd_at_mach` previously computed wetted area from
/// `total_stage_length`/`reference_radius` alone (nosecone + bodytubes),
/// completely omitting `stage.finsets` from the drag calculation entirely
/// (fins were only ever consumed for `cn_alpha`, never for `cd`). Evidence:
/// for this vehicle's Booster stage, fin planform area (both sides, 3 fins)
/// is ~49% of the bare-body wetted area (`0.0663 m^2` vs `0.1349 m^2`,
/// computed from the real parsed `L2_Hyper_Parallel_15K.ork` geometry) --
/// not a negligible correction. Root-caused during 01-08's validation
/// debugging: after fixing two real `rocket-sim` mass-flow/thrust-curve-
/// indexing bugs (which corrected an under-shoot), the trajectory switched
/// to a large over-shoot (apogee 3.4x too high, max_mach 1.3x too high),
/// which is the expected signature of under-modeled aerodynamic drag, not
/// an under-modeled thrust/mass budget.
fn fin_wetted_area_from_stages(active_stages: &[&StageGeometry]) -> f64 {
    active_stages
        .iter()
        .flat_map(|s| &s.finsets)
        .map(|fs| {
            let derived = fin_geometry(fs);
            let edge_factor = if derived.mac_length > 1e-12 {
                1.0 + 2.0 * fs.thickness / derived.mac_length
            } else {
                1.0
            };
            2.0 * derived.exposed_area * edge_factor * fs.fin_count as f64
        })
        .sum()
}

/// Fin supersonic wave drag via classical linearized thin-airfoil theory:
/// `Cd_wave = 4 * (t/c)^2 / sqrt(M^2 - 1)` for a symmetric
/// biconvex/double-wedge airfoil in supersonic flow (standard 2D linearized
/// supersonic aerodynamics result -- see e.g. Anderson, *Fundamentals of
/// Aerodynamics*, ch. 12 "Linearized Supersonic Flow"; not a fabricated
/// constant), referenced to each fin's own mean-chord thickness ratio, then
/// scaled from the fin's own planform area to the vehicle's reference area
/// and summed across all finsets.
///
/// [Rule 2 fix, 01-08]: previously omitted entirely -- `cd_at_mach`'s only
/// wave-drag term modeled the axisymmetric body, not the fins. Thin lifting
/// surfaces are a significant wave-drag source at the Mach 4-7 range this
/// vehicle's Sustainer burn reaches, so omitting them entirely under-
/// estimates supersonic/hypersonic drag. Gated at `mach >= 1.2` to avoid
/// the `1/sqrt(M^2-1)` singularity near Mach 1, matching this module's
/// existing transonic-gate convention for the body's own `wave_cd` term.
fn fin_pressure_and_base_drag_cd_from_stages(
    active_stages: &[&StageGeometry],
    mach: f64,
    ref_area: f64,
    mode: crate::PhysicsMode,
) -> f64 {
    if ref_area <= 0.0 {
        return 0.0;
    }
    let base_cd_rocket = calculate_base_cd(mach);

    active_stages
        .iter()
        .flat_map(|s| &s.finsets)
        .map(|fs| {
            let derived = fin_geometry(fs);
            let fin_area = derived.exposed_area;
            if fin_area <= 0.0 {
                return 0.0;
            }

            let cross_section = fs.cross_section.as_str();
            // `double-wedge` is the organic vocabulary for the sharp-edged
            // option. OpenRocket 24.12 loads that XML value as SQUARE, so the
            // proxy must apply the same stagnation and base-drag model.
            let is_square = matches!(cross_section, "square" | "double-wedge");

            // Pressure fore-drag. OpenRocket uses stagnation drag for square
            // fin leading edges and the rounded-leading-edge model for rounded
            // and airfoil sections. HyperReal keeps the previous all-rounded
            // behavior so real/HyperReal mode is not moved by this OR shim.
            let mut pressure_cd =
                if matches!(mode, crate::PhysicsMode::OpenRocketLegacy) && is_square {
                    calculate_stagnation_cd(mach)
                } else if mach < 0.9 {
                    (1.0 - mach.powi(2)).powf(-0.417) - 1.0
                } else if mach < 1.0 {
                    1.0 - 1.785 * (mach - 0.9)
                } else {
                    1.214 - 0.502 / mach.powi(2) + 0.1095 / mach.powi(4)
                };

            // Slanted leading edge
            pressure_cd *= derived.cos_gamma_lead.powi(2);

            // Scale to correct reference area
            pressure_cd *= derived.span * fs.thickness / ref_area;

            let base_multiplier = match mode {
                crate::PhysicsMode::HyperReal => 0.5,
                crate::PhysicsMode::OpenRocketLegacy => match cross_section {
                    "square" | "double-wedge" => 1.0,
                    "rounded" => 0.5,
                    "airfoil" => 0.0,
                    _ => 0.0,
                },
            };
            let fin_base_cd =
                base_multiplier * base_cd_rocket * derived.span * fs.thickness / ref_area;

            (pressure_cd + fin_base_cd) * fs.fin_count as f64
        })
        .sum()
}

/// OpenRocket `BarrowmanDragCalculator.calculatePressureCD` adds a
/// stagnation-disk term whenever a symmetric component's fore radius is
/// larger than the preceding symmetric component's aft radius. Organic
/// multi-stage stacks commonly expose exactly this annular step where a
/// narrow sustainer meets a wider booster. Ignoring it makes the proxy
/// dramatically optimistic for high-TWR stepped vehicles.
fn symmetric_step_pressure_cd_from_stages(
    active_stages: &[&StageGeometry],
    mach: f64,
    ref_area: f64,
) -> f64 {
    if ref_area <= 0.0 {
        return 0.0;
    }

    // Geometry stages are in ignition order (booster -> sustainer), while
    // OpenRocket walks symmetric components physically from nose to tail.
    let mut previous_aft_radius = 0.0_f64;
    let mut total = 0.0;
    let stagnation_cd = calculate_stagnation_cd(mach);

    for stage in active_stages.iter().rev() {
        if let Some(nosecone) = &stage.nosecone {
            // The nose owns its shape-pressure term. Its aft radius becomes
            // the predecessor of the first cylindrical body component.
            previous_aft_radius = nosecone.aft_radius.max(0.0);
        }

        for bodytube in &stage.bodytubes {
            let fore_radius = bodytube.radius.max(0.0);
            if previous_aft_radius < fore_radius {
                let exposed_area = PI * (fore_radius.powi(2) - previous_aft_radius.powi(2));
                total += stagnation_cd * exposed_area / ref_area;
            }
            previous_aft_radius = fore_radius;
        }
    }

    total
}

/// Raw `vonKarmanInterpolator` (mach, Cd) pairs, verbatim from OpenRocket's
/// bytecode static initializer for the Von Karman ogive nose shape.
///
/// Provenance: 01.1-RESEARCH.md "vonKarmanInterpolator -- the exact table
/// for this vehicle" `[VERIFIED: bytecode SymmetricComponentCalc.class,
/// static initializer, vonKarmanInterpolator construction block, offsets
/// 514-653, putstatic #318]`. Domain is `[0.9, 3.0]`; values outside this
/// range are clamped (Pitfall 2), not extrapolated.
const VON_KARMAN_TABLE: [(f64, f64); 10] = [
    (0.9, 0.0),
    (0.95, 0.01),
    (1.0, 0.027),
    (1.05, 0.055),
    (1.1, 0.07),
    (1.2, 0.081),
    (1.4, 0.095),
    (1.6, 0.097),
    (2.0, 0.091),
    (3.0, 0.083),
];

/// `BarrowmanCalculator.calculateStagnationCD(mach)`, verbatim closed form.
///
/// Provenance: 01.1-RESEARCH.md `[VERIFIED: bytecode
/// BarrowmanCalculator.class, calculateStagnationCD, offsets 0-75]`. This is
/// also the "blunt-body" reference value the vonKarman table's fineness
/// power-law blend is normalized against in `nose_pressure_cd`.
fn calculate_stagnation_cd(mach: f64) -> f64 {
    let raw = if mach <= 1.0 {
        1.0 + mach.powi(2) / 4.0 + mach.powi(4) / 40.0
    } else {
        1.84 - 0.76 / mach.powi(2) + 0.166 / mach.powi(4) + 0.035 / mach.powi(6)
    };
    0.85 * raw
}

/// `BarrowmanCalculator.calculateBaseCD(mach)`, verbatim closed form -- a
/// separate additive base-drag term (Pitfall 1: not the same thing as
/// nose/body pressure drag).
///
/// Provenance: 01.1-RESEARCH.md "Base Drag (BarrowmanCalculator.
/// calculateBaseCD)" `[VERIFIED: bytecode BarrowmanCalculator.class,
/// calculateBaseCD, offsets 0-23]`.
fn calculate_base_cd(mach: f64) -> f64 {
    if mach <= 1.0 {
        0.12 + 0.13 * mach.powi(2)
    } else {
        0.25 / mach
    }
}

/// Clamp-then-linear-interpolate a `(x, y)` table: returns the first point's
/// `y` if `x` is at or below the table's domain, the last point's `y` if `x`
/// is at or above it, and linear interpolation between the bracketing knots
/// otherwise. This matches OpenRocket's `LinearInterpolator.getValue()`
/// clamping behavior (Pitfall 2) and `Stage::cd_at()`'s own clamp shape --
/// this is a NEW private helper distinct from `interp_x_at_y` (that one is
/// y-monotonic and clamps to the nearest endpoint, not domain-clamped).
fn interp_clamped(table: &[(f64, f64)], x: f64) -> f64 {
    let first = table[0];
    let last = table[table.len() - 1];
    if x <= first.0 {
        return first.1;
    }
    if x >= last.0 {
        return last.1;
    }
    for w in table.windows(2) {
        let (x0, y0) = w[0];
        let (x1, y1) = w[1];
        if x >= x0 && x <= x1 {
            if x == x0 {
                return y0;
            }
            if x == x1 {
                return y1;
            }
            if (x1 - x0).abs() < 1e-12 {
                return y0;
            }
            let frac = (x - x0) / (x1 - x0);
            return y0 + frac * (y1 - y0);
        }
    }
    last.1
}

/// Nose pressure drag: the bytecode-verified `vonKarmanInterpolator` table
/// (shape_cd) blended against the closed-form blunt-body `calculate_stagnation_cd`
/// (blunt_cd) via the fineness-ratio power law `ln(fineness + 1) / ln(4)`, per
/// 01.1-RESEARCH.md's "HAACK -> vonKarmanInterpolator" mapping (this vehicle's
/// Sustainer nosecone has `shape_parameter = 0.0`, so the blend factor is 0.0
/// and the vonKarman table is used unblended against lvHaack -- see interfaces
/// block; this function implements the shape/blunt-body power-law blend that
/// applies regardless of blend factor).
///
/// Mach is clamped to 3.0 for the blunt-body reference too (Pitfall 2): both
/// `shape_cd` (via `interp_clamped`) and `blunt_cd` hold flat above the
/// vonKarman table's Mach-3.0 ceiling, matching OpenRocket's own
/// `LinearInterpolator.getValue()` clamp -- no closed-form extrapolation
/// beyond the table's domain.
fn nose_pressure_cd(mach: f64, fineness: f64) -> f64 {
    let fineness_exponent = (fineness + 1.0).ln() / 4.0_f64.ln();
    // [01.1-01 Task 3, lever (a), Assumption A2]: pre-bake the fineness
    // power-law blend AT the vonKarman table's OWN x-points (0.9..3.0),
    // building a new (mach, cd) table from those pre-baked points, then
    // interp_clamped that table -- the literal OpenRocket bytecode
    // construction (01.1-RESEARCH.md), rather than applying the power law
    // pointwise at an arbitrary query mach (the Task 1 form). The
    // difference is second-order: identical at the table's own knots,
    // differs only in how the blend is interpolated between them.
    let blended_table: [(f64, f64); 10] = {
        let mut out = [(0.0_f64, 0.0_f64); 10];
        for (i, &(m, shape_cd)) in VON_KARMAN_TABLE.iter().enumerate() {
            let blunt_cd = calculate_stagnation_cd(m.min(3.0));
            let cd = if blunt_cd <= 0.0 {
                shape_cd
            } else {
                blunt_cd * (shape_cd / blunt_cd).powf(fineness_exponent)
            };
            out[i] = (m, cd);
        }
        out
    };
    interp_clamped(&blended_table, mach)
}

/// Drag coefficient at a single Mach number: turbulent skin-friction
/// component (Reynolds- and roughness-dependent, with a compressibility
/// correction) plus the bytecode-verified vonKarman nose pressure-drag term
/// (fineness power-law blend against the closed-form blunt-body stagnation
/// value, clamped flat above Mach 3.0), plus a separate additive base-drag
/// term, plus fin friction (via wetted area) and fin supersonic wave drag
/// (both added in 01-08, see `fin_wetted_area`/`fin_wave_drag_cd` doc
/// comments).
///
/// **Documented formula choice** (01-RESEARCH.md does not give OpenRocket's
/// precise friction-drag constants verbatim -- only that they exist and are
/// Reynolds/roughness/Mach-dependent, per `BarrowmanCalculator.java`):
/// - Friction: Schlichting turbulent flat-plate skin friction,
///   `Cf = 0.455 / log10(Re)^2.58`, roughness-limited via a critical
///   Reynolds number `Re_crit = 51 * (roughness/length)^-1.039` (standard
///   roughness-limited turbulent-flow cutoff), then compressibility-corrected
///   via `Cf_compressible = Cf / (1 + 0.144*mach^2)^0.65` (a standard
///   empirical turbulent compressible correction), scaled by
///   `wetted_area / reference_area` (`wetted_area` now includes fins).
///   Not implicated by 01.1-RESEARCH.md's evidence -- kept as-is.
/// - Nose pressure: `nose_pressure_cd(mach, fineness)` (bytecode-verified
///   `vonKarmanInterpolator` table + fineness power-law blend, 01.1-01-PLAN),
///   scaled by `nc_frontal_area / ref_area` (`nc_frontal_area = pi *
///   aft_radius^2`, `foreRadius = 0` for a pointed nose).
/// - Base: `calculate_base_cd(mach)` (separate additive closed form,
///   01.1-01-PLAN) -- `base_area == ref_area` for these plain cylindrical
///   aft ends, so no additional area scaling is applied.
/// - Fin: `fin_wave_drag_cd` (single term, no double-counting -- this
///   baseline never had a `fin_pressure_cd`/`lv_haack_interpolator` pair to
///   de-duplicate).
fn cd_at_mach_from_stages(
    active_stages: &[&StageGeometry],
    mach: f64,
    roughness_m: f64,
    mode: crate::PhysicsMode,
) -> (f64, f64) {
    let length = total_stage_length_from_stages(active_stages);
    let radius = reference_radius_from_stages(active_stages).max(1e-6);
    let ref_area = PI * radius.powi(2);
    let wetted_area =
        body_wetted_area_from_stages(active_stages) + fin_wetted_area_from_stages(active_stages);

    const SEA_LEVEL_SOUND_SPEED: f64 = 340.3; // m/s, sea-level ISA reference
    const KINEMATIC_VISCOSITY: f64 = 1.5e-5; // m^2/s, approx sea-level air

    let velocity = (mach * SEA_LEVEL_SOUND_SPEED).max(1.0);
    let reynolds = velocity * length / KINEMATIC_VISCOSITY;

    let _roughness_ratio = (roughness_m.max(1e-9) / length).max(1e-12);
    // Note: Re_crit logic and Schlichting are dropped. We now use OpenRocket's explicit formula.

    // OpenRocket calculation for non-perfect finish (roughness > 0 defaults to non-perfect in OR)
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

    let smooth_cf = if mach < 0.9 {
        cf * c1
    } else if mach < 1.1 {
        cf * (c2 * (mach - 0.9) / 0.2 + c1 * (1.1 - mach) / 0.2)
    } else {
        cf * c2
    };
    let roughness_correction = if mach < 0.9 {
        1.0 - 0.1 * mach.powi(2)
    } else if mach > 1.1 {
        1.0 / (1.0 + 0.18 * mach.powi(2))
    } else {
        let rc1 = 1.0 - 0.1 * 0.9_f64.powi(2);
        let rc2 = 1.0 / (1.0 + 0.18 * 1.1_f64.powi(2));
        rc2 * (mach - 0.9) / 0.2 + rc1 * (1.1 - mach) / 0.2
    };
    let roughness_limited_cf =
        0.032 * (roughness_m.max(1e-12) / length).powf(0.2) * roughness_correction;
    let cf_compressible = smooth_cf.max(roughness_limited_cf);

    let friction_cd = cf_compressible * (wetted_area / ref_area);

    let top_nc = active_stages.iter().find_map(|s| s.nosecone.as_ref());
    let (fineness, nc_frontal_area) = match top_nc {
        Some(nc) => (
            nc.length / (2.0 * nc.aft_radius.max(1e-9)),
            PI * nc.aft_radius.powi(2),
        ),
        None => (0.0, 0.0),
    };
    let (nose_pres, base) = match mode {
        crate::PhysicsMode::HyperReal => {
            let n_cd = if nc_frontal_area > 0.0 {
                nose_pressure_cd(mach, fineness) * (nc_frontal_area / ref_area)
            } else {
                0.0
            };
            (n_cd, calculate_base_cd(mach))
        }
        crate::PhysicsMode::OpenRocketLegacy => {
            if let Some(nc) = top_nc {
                let or_nc_cd = crate::openrocket_nose::calculate_nose_pressure_cd(
                    nc.shape,
                    nc.shape_parameter,
                    fineness,
                    mach,
                );
                let n_cd = or_nc_cd * (nc_frontal_area / ref_area);
                // OpenRocket base drag
                let or_base = calculate_base_cd(mach); // We can just use the same base CD for now, unless we want to map it
                (n_cd, or_base)
            } else {
                (0.0, calculate_base_cd(mach))
            }
        }
    };

    let fin_pres = fin_pressure_and_base_drag_cd_from_stages(active_stages, mach, ref_area, mode);
    let symmetric_step_pres = match mode {
        crate::PhysicsMode::OpenRocketLegacy => {
            symmetric_step_pressure_cd_from_stages(active_stages, mach, ref_area)
        }
        crate::PhysicsMode::HyperReal => 0.0,
    };

    (
        friction_cd,
        nose_pres + base + fin_pres + symmetric_step_pres,
    )
}

/// Mach-dependent drag table spanning the required validation range (per
/// 01-06-PLAN.md's behavior spec), in the exact `(mach, cd)` shape Plan 02's
/// patched `Stage.cd_table`/`cd_at()` consumes.
pub(crate) fn compute_cd_table_from_stages(
    active_stages: &[&StageGeometry],
    roughness_m: f64,
    mode: crate::PhysicsMode,
) -> (Vec<(f64, f64)>, Vec<(f64, f64)>) {
    const MACH_POINTS: [f64; 12] = [0.0, 0.5, 0.9, 1.0, 1.1, 1.2, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0];
    let mut cd_table = Vec::with_capacity(12);
    let mut cd_nonfric_table = Vec::with_capacity(12);
    for &m in &MACH_POINTS {
        let (fric, nonfric) = cd_at_mach_from_stages(active_stages, m, roughness_m, mode);
        cd_table.push((m, fric + nonfric));
        cd_nonfric_table.push((m, nonfric));
    }
    (cd_table, cd_nonfric_table)
}

/// Reference Mach for the single static `cn_alpha` figure this module
/// exposes -- low subsonic, matching how stability margin is conventionally
/// reported at a low-speed reference condition. Mach-dependence that
/// matters for flight dynamics lives in `cd_table`, not in this scalar
/// (fin CNa is itself Mach-dependent internally via `fin_cna`, but
/// `AerodynamicCoefficients.cn_alpha` is a single scalar per rocket-sim's
/// `Stage.cn_alpha: Option<f64>` field shape).
const REFERENCE_MACH: f64 = 0.3;

/// Combines the nosecone + all finsets into a single `AerodynamicCoefficients`:
/// area-weighted (CNa-weighted) total CP position, summed total `cn_alpha`,
/// and the Mach-dependent drag table.
pub fn compute_aero(
    active_stages: &[&StageGeometry],
    static_cg_from_nose: f64,
    roughness_m: f64,
    physics_mode: crate::PhysicsMode,
) -> Result<AerodynamicCoefficients, L2EngineError> {
    compute_aero_at_mach(
        active_stages,
        static_cg_from_nose,
        roughness_m,
        physics_mode,
        REFERENCE_MACH,
    )
}

pub fn compute_aero_at_mach(
    active_stages: &[&StageGeometry],
    static_cg_from_nose: f64,
    roughness_m: f64,
    physics_mode: crate::PhysicsMode,
    reference_mach: f64,
) -> Result<AerodynamicCoefficients, L2EngineError> {
    compute_aero_at_mach_and_aoa(
        active_stages,
        static_cg_from_nose,
        roughness_m,
        physics_mode,
        reference_mach,
        0.0,
    )
}

pub fn compute_aero_at_mach_and_aoa(
    active_stages: &[&StageGeometry],
    static_cg_from_nose: f64,
    roughness_m: f64,
    physics_mode: crate::PhysicsMode,
    reference_mach: f64,
    aoa_rad: f64,
) -> Result<AerodynamicCoefficients, L2EngineError> {
    // Radial tubes share the core's flight axis. Represent their finsets in
    // the normal-force/CP calculation, and use frontal-area scaling for the
    // otherwise unmodeled pod/body interference drag. The explicit AST
    // factor is the calibration seam for an OpenRocket authority sample.
    let original_stages = active_stages;
    let mut expanded_storage = Vec::with_capacity(original_stages.len());
    for stage in original_stages {
        let mut expanded = (*stage).clone();
        for assembly in &stage.radial_assemblies {
            for finset in &assembly.finsets {
                let mut replicated = finset.clone();
                replicated.fin_count = replicated
                    .fin_count
                    .saturating_mul(assembly.instance_count);
                replicated.axial_offset_m += assembly.axial_offset_m;
                expanded.finsets.push(replicated);
            }
        }
        expanded.radial_assemblies.clear();
        expanded_storage.push(expanded);
    }
    let expanded_refs = expanded_storage.iter().collect::<Vec<_>>();
    let active_stages = expanded_refs.as_slice();
    let radius = reference_radius_from_stages(active_stages);
    let reference_area = PI * radius.powi(2);
    if reference_area <= 0.0 {
        return Err(L2EngineError::ParseError(
            "compute_aero: stage has zero reference area (no bodytube/nosecone radius)".to_string(),
        ));
    }

    let mut weighted_cp_numerator = 0.0_f64;
    let mut total_cn_alpha = 0.0_f64;
    let mut damping_moment_sum_m2 = 0.0_f64;

    for stage in active_stages {
        if let Some(nc) = &stage.nosecone {
            let (cp_from_tip, linear_cn_alpha) = nosecone_cp_and_cna(nc);
            // OpenRocket SymmetricComponentCalc stores CNa*sinc(AOA) in the
            // CP weight, then multiplies that weight by AOA to obtain CN.
            // Keep Galejs body lift separate: it already returns CN/AOA.
            let sinc_aoa = if aoa_rad.abs() < 1e-4 {
                1.0 - aoa_rad.powi(2) / 6.0
            } else {
                aoa_rad.sin() / aoa_rad
            };
            let cn_alpha = linear_cn_alpha * sinc_aoa;
            let cp = stage.axial_offset_m + nc.axial_offset_m + cp_from_tip;
            weighted_cp_numerator += cn_alpha * cp;
            damping_moment_sum_m2 += cn_alpha * (cp - static_cg_from_nose).powi(2);
            total_cn_alpha += cn_alpha;
        }

        for fs in &stage.finsets {
            let derived = fin_geometry(fs);
            let per_fin_cna =
                fin_cna_at_aoa(&derived, reference_mach, aoa_rad, fs.fin_count, radius);
            // OpenRocket evaluates each fin at its angular instance and sums
            // CNa1*sin(theta-angle)^2.  For an evenly spaced finset the sum
            // is N/2, not N; multiplying by N doubled fin authority and kept
            // genuinely supersonic-unstable rockets artificially stable.
            let finset_cn_alpha = per_fin_cna * fs.fin_count as f64 * 0.5;

            let root_le_x = fs.points.first().map(|p| p.0).unwrap_or(0.0);
            let cp_from_nose = stage.axial_offset_m
                + fs.axial_offset_m
                + root_le_x
                + fin_cp_from_root_le(&derived, reference_mach);

            weighted_cp_numerator += finset_cn_alpha * cp_from_nose;
            damping_moment_sum_m2 += finset_cn_alpha * (cp_from_nose - static_cg_from_nose).powi(2);
            total_cn_alpha += finset_cn_alpha;
        }
    }

    // Galejs body lift is evaluated at the actual AOA used to build each
    // runtime stability-table row. sixdof interpolates these rows at flight AOA.
    let (body_cn, body_cp) =
        body_lift_at_aoa(active_stages, aoa_rad, reference_mach, reference_area);
    if body_cn > 0.0 {
        weighted_cp_numerator += body_cn * body_cp;
        damping_moment_sum_m2 += body_cn * (body_cp - static_cg_from_nose).powi(2);
        total_cn_alpha += body_cn;
    }

    if total_cn_alpha <= 0.0 {
        return Err(L2EngineError::ParseError(
            "compute_aero: stage has zero total cn_alpha (no nosecone or finsets)".to_string(),
        ));
    }

    let _total_cp_from_nose = weighted_cp_numerator / total_cn_alpha;
    let cp_offset_from_cg = (weighted_cp_numerator / total_cn_alpha) - static_cg_from_nose;

    let (mut cd_table, mut cd_nonfric_table) =
        compute_cd_table_from_stages(active_stages, roughness_m, physics_mode);

    let pod_drag_area = original_stages
        .iter()
        .flat_map(|stage| stage.radial_assemblies.iter())
        .map(|assembly| {
            let pod_radius = assembly
                .bodytubes
                .iter()
                .map(|tube| tube.radius)
                .chain(assembly.nosecone.iter().map(|nose| nose.aft_radius))
                .fold(0.0_f64, f64::max);
            assembly.instance_count as f64
                * PI
                * pod_radius.powi(2)
                * assembly.aero_interference_factor
        })
        .sum::<f64>();
    let drag_scale = 1.0 + pod_drag_area / reference_area;
    for (_, cd) in &mut cd_table {
        *cd *= drag_scale;
    }
    for (_, cd) in &mut cd_nonfric_table {
        *cd *= drag_scale;
    }

    let vehicle_length = total_stage_length_from_stages(active_stages);
    let body_wetted_area = body_wetted_area_from_stages(active_stages);
    let wetted_area = body_wetted_area + fin_wetted_area_from_stages(active_stages);
    let body_wetted_area_ratio = body_wetted_area / reference_area;
    let body_fineness_ratio = (vehicle_length + 0.0001) / radius.max(1e-6);

    let friction_params = crate::sim_core::vehicle::FrictionParams {
        vehicle_length,
        wetted_area_ratio: wetted_area / reference_area * drag_scale,
        body_wetted_area_ratio: body_wetted_area_ratio * drag_scale,
        body_fineness_ratio,
        roughness_m,
        model: match physics_mode {
            crate::PhysicsMode::OpenRocketLegacy => {
                crate::sim_core::vehicle::FrictionModel::OpenRocketLegacy
            }
            crate::PhysicsMode::HyperReal => crate::sim_core::vehicle::FrictionModel::HyperReal,
        },
    };
    let symmetric_planform_area = active_stages
        .iter()
        .map(|stage| {
            let body = stage
                .bodytubes
                .iter()
                .map(|tube| 2.0 * tube.radius * tube.length)
                .sum::<f64>();
            let nose = stage.nosecone.as_ref().map_or(0.0, |nose| {
                const SLICES: usize = 100;
                let dx = nose.length / SLICES as f64;
                (0..SLICES)
                    .map(|index| {
                        let x = (index as f64 + 0.5) * dx;
                        2.0 * haack_profile_radius(
                            x,
                            nose.length,
                            nose.aft_radius,
                            nose.shape_parameter,
                        ) * dx
                    })
                    .sum::<f64>()
            });
            body + nose
        })
        .sum::<f64>();
    let cache_diameter = symmetric_planform_area / vehicle_length;
    let reference_length = 2.0 * radius;
    let mut pitch_damping_multiplier = 0.275 * cache_diameter / (reference_area * reference_length)
        * (static_cg_from_nose.powi(4) + (vehicle_length - static_cg_from_nose).powi(4));
    for stage in active_stages {
        for finset in &stage.finsets {
            let fin = fin_geometry(finset);
            let midchord =
                stage.axial_offset_m + finset.axial_offset_m + fin.mac_lead + 0.5 * fin.mac_length;
            pitch_damping_multiplier += 0.6
                * (finset.fin_count.min(4) as f64)
                * fin.exposed_area
                * (midchord - static_cg_from_nose).abs().powi(3)
                / (reference_area * reference_length);
        }
    }

    Ok(AerodynamicCoefficients {
        cp_offset_from_cg,
        cn_alpha: total_cn_alpha,
        damping_moment_sum_m2,
        pitch_damping_multiplier,
        reference_area,
        cd_table,
        cd_nonfric_table,
        friction_params,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::xml_parser::{extract_ork_xml, parse_rocket_geometry};
    use std::path::PathBuf;

    #[test]
    fn body_lift_zero_aoa_is_zero() {
        assert!(body_lift_cn(0.1, 0.00785, 0.0, 0.3).abs() < 1e-12);
    }

    #[test]
    fn body_lift_at_five_degrees_matches_galejs_formula() {
        let aoa = 5.0_f64.to_radians();
        let expected = BODY_LIFT_K * (0.1 / 0.00785) * aoa.sin().powi(2) / aoa;
        assert!((body_lift_cn(0.1, 0.00785, aoa, 0.3) - expected).abs() < 1e-12);
    }

    #[test]
    fn body_lift_low_speed_cutoff_is_quadratic() {
        let aoa = PI / 3.0;
        let normal = body_lift_cn(0.1, 0.00785, aoa, 0.3);
        let cutoff = body_lift_cn(0.1, 0.00785, aoa, 0.02);
        assert!((cutoff / normal - 0.16).abs() < 1e-12);
    }

    #[test]
    fn fin_planform_area_matches_trapezoid() {
        let derived = fin_geometry(&sustainer_stage().finsets[0]);
        let expected = derived.span * (derived.root_chord + derived.tip_chord) / 2.0;
        assert!((fin_planform_area(&derived) - expected).abs() < 1e-12);
    }

    #[test]
    fn planform_area_and_center_include_tube_offsets() {
        let mut stage = sustainer_stage();
        stage.nosecone = None;
        stage.bodytubes.truncate(1);
        stage.bodytubes[0].radius = 0.025;
        stage.bodytubes[0].length = 1.0;
        stage.bodytubes[0].axial_offset_m = 0.2;
        assert!((stage.planform_area() - 0.05).abs() < 1e-12);
        assert!((stage.planform_center() - 0.7).abs() < 1e-12);
    }

    #[test]
    fn body_wetted_area_keeps_historical_nosecone_proxy() {
        let mut stage = sustainer_stage();
        stage.bodytubes.truncate(1);
        stage.bodytubes[0].radius = 0.05;
        stage.bodytubes[0].length = 1.0;
        let nose = stage.nosecone.as_mut().expect("sustainer nosecone");
        nose.aft_radius = 0.05;
        nose.length = 0.25;

        let expected = 2.0 * PI * 0.05 * 1.0 + 2.0 * PI * 0.05 * 0.25;
        assert!((body_wetted_area_from_stages(&[&stage]) - expected).abs() < 1e-12);
    }

    #[test]
    fn fin_friction_area_includes_openrocket_edge_factor() {
        let stage = sustainer_stage();
        let fs = &stage.finsets[0];
        let fin = fin_geometry(fs);
        let expected = 2.0
            * fin.exposed_area
            * (1.0 + 2.0 * fs.thickness / fin.mac_length)
            * fs.fin_count as f64;
        assert!((fin_wetted_area_from_stages(&[&stage]) - expected).abs() < 1e-12);
    }

    #[test]
    fn compute_aero_at_aoa_includes_body_lift() {
        let stage = sustainer_stage();
        let active = [&stage];
        let zero = compute_aero_at_mach_and_aoa(
            &active,
            0.5,
            1e-6,
            crate::PhysicsMode::OpenRocketLegacy,
            0.3,
            0.0,
        )
        .expect("zero-AOA aero");
        let angled = compute_aero_at_mach_and_aoa(
            &active,
            0.5,
            1e-6,
            crate::PhysicsMode::OpenRocketLegacy,
            0.3,
            5.0_f64.to_radians(),
        )
        .expect("angled aero");
        assert!(angled.cn_alpha > zero.cn_alpha);
    }

    #[test]
    fn nosecone_cna_uses_openrocket_sinc_at_aoa() {
        let mut stage = sustainer_stage();
        stage.finsets.clear();
        let active = [&stage];
        let aoa = 10.0_f64.to_radians();
        let aero = compute_aero_at_mach_and_aoa(
            &active,
            0.5,
            1e-6,
            crate::PhysicsMode::OpenRocketLegacy,
            0.3,
            aoa,
        )
        .expect("nose and body aero");
        let ref_area = PI * reference_radius_from_stages(&active).powi(2);
        let expected =
            2.0 * aoa.sin() / aoa + body_lift_cn(stage.planform_area(), ref_area, aoa, 0.3);
        assert!((aero.cn_alpha - expected).abs() < 1e-12);
    }

    fn ork_fixture_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/L2_Hyper_Parallel_15K.ork")
    }

    fn sustainer_stage() -> StageGeometry {
        let xml = extract_ork_xml(&ork_fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        // Ignition-order-reordered: index 0 = Booster, index 1 = Sustainer.
        geometry.stages[1].clone()
    }

    fn booster_stage() -> StageGeometry {
        let xml = extract_ork_xml(&ork_fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        geometry.stages[0].clone()
    }

    /// Standard shoelace formula, duplicated locally as a test-only
    /// cross-check (independent of `fin_geometry`'s strip-integration
    /// implementation) for Test 2.
    fn shoelace_area(points: &[(f64, f64)]) -> f64 {
        let n = points.len();
        if n < 3 {
            return 0.0;
        }
        let mut sum = 0.0_f64;
        for i in 0..n {
            let (x1, y1) = points[i];
            let (x2, y2) = points[(i + 1) % n];
            sum += x1 * y2 - x2 * y1;
        }
        (sum / 2.0).abs()
    }

    /// Test 1: nosecone cn_alpha == 2.0 always; cp_offset_from_nose_tip
    /// lies strictly between 0.0 and length for the Sustainer's actual
    /// nosecone.
    #[test]
    fn test_nosecone_cp_and_cna() {
        let stage = sustainer_stage();
        let nc = stage.nosecone.as_ref().expect("sustainer has a nosecone");
        assert!((nc.length - 0.591792).abs() < 1e-6);
        assert!((nc.aft_radius - 0.054325).abs() < 1e-6);
        assert_eq!(nc.shape_parameter, 0.0);

        let (cp_offset_from_tip, cn_alpha) = nosecone_cp_and_cna(nc);
        assert_eq!(cn_alpha, 2.0);
        assert!(
            cp_offset_from_tip > 0.0 && cp_offset_from_tip < nc.length,
            "cp_offset_from_tip={cp_offset_from_tip} must lie strictly between 0.0 and length={}",
            nc.length
        );
    }

    /// Test 2: freeform-fin geometric properties via general
    /// strip-integration (N>=20 slices); exposed_area must match the direct
    /// shoelace polygon area within 0.1% for this vehicle's trapezoid-shaped
    /// fins.
    #[test]
    fn test_fin_geometry_strip_integration_matches_shoelace() {
        let stage = booster_stage();
        let fs = &stage.finsets[0];
        assert_eq!(fs.points.len(), 4);

        let derived = fin_geometry(fs);
        assert!(derived.root_chord > 0.0);
        assert!(derived.tip_chord > 0.0);
        assert!(derived.span > 0.0);
        assert!(derived.exposed_area > 0.0);

        let shoelace = shoelace_area(&fs.points);
        let relative_error = (derived.exposed_area - shoelace).abs() / shoelace;
        assert!(
            relative_error < 0.001,
            "strip-integration exposed_area={} vs shoelace={}, relative_error={relative_error}",
            derived.exposed_area,
            shoelace
        );
    }

    /// Test 3: fin cna at mach=0.5 (subsonic) via the verbatim formula,
    /// *= (1+tau), *= fin_count_interference_factor(3) == 1.0.
    #[test]
    fn test_fin_cna_subsonic_positive_finite() {
        let stage = booster_stage();
        let fs = &stage.finsets[0];
        assert_eq!(fs.fin_count, 3);
        let derived = fin_geometry(fs);
        let body_radius = stage.bodytubes[0].radius;

        let cna = fin_cna(&derived, 0.5, fs.fin_count, body_radius);
        assert!(cna.is_finite(), "cna must be finite, got {cna}");
        assert!(cna > 0.0, "cna must be positive, got {cna}");
    }

    /// Test 4: fin_count_interference_factor spot-checks.
    #[test]
    fn test_fin_count_interference_factor_table() {
        assert_eq!(fin_count_interference_factor(3), 1.0);
        assert_eq!(fin_count_interference_factor(6), 0.913);
        assert_eq!(fin_count_interference_factor(9), 0.75);
    }

    /// Test 5: fin cna at mach=5.6 (this vehicle's actual peak regime) via
    /// the documented linearized fallback; must be positive, finite, and
    /// monotonically decreasing as mach increases from 2.0 to 6.0.
    #[test]
    fn test_fin_cna_supersonic_monotonic_decreasing() {
        let stage = booster_stage();
        let fs = &stage.finsets[0];
        let derived = fin_geometry(fs);
        let body_radius = stage.bodytubes[0].radius;

        let cna_at_5_6 = fin_cna(&derived, 5.6, fs.fin_count, body_radius);
        assert!(cna_at_5_6.is_finite());
        assert!(cna_at_5_6 > 0.0);

        let mut prev = fin_cna(&derived, 2.0, fs.fin_count, body_radius);
        for mach in [2.5, 3.0, 4.0, 5.0, 6.0] {
            let cna = fin_cna(&derived, mach, fs.fin_count, body_radius);
            assert!(
                cna < prev,
                "cna must decrease monotonically: mach={mach} cna={cna} vs prev={prev}"
            );
            prev = cna;
        }
    }

    #[test]
    fn test_fin_cna_supersonic_scales_by_fin_area_over_reference_area() {
        let stage = booster_stage();
        let fs = &stage.finsets[0];
        let derived = fin_geometry(fs);
        let body_radius = stage.bodytubes[0].radius;
        let mach: f64 = 2.0;
        let ref_area = PI * body_radius.powi(2);
        let tau = body_radius / (derived.span + body_radius);
        let expected = derived.exposed_area * (2.0 / (mach.powi(2) - 1.0).sqrt()) / ref_area
            * (1.0 + tau)
            * fin_count_interference_factor(fs.fin_count);

        let actual = fin_cna(&derived, mach, fs.fin_count, body_radius);

        assert!((actual - expected).abs() < 1e-12);
    }

    #[test]
    fn openrocket_mode_charges_stagnation_drag_for_wider_booster_step() {
        let mut booster = booster_stage();
        let mut sustainer = sustainer_stage();
        booster.bodytubes[0].radius = 0.05;
        sustainer.bodytubes[0].radius = 0.03;
        sustainer.nosecone.as_mut().expect("nose").aft_radius = 0.03;
        let active = vec![&booster, &sustainer];
        let ref_area = PI * 0.05_f64.powi(2);
        let mach = 2.0;

        let actual = symmetric_step_pressure_cd_from_stages(&active, mach, ref_area);
        let expected =
            calculate_stagnation_cd(mach) * PI * (0.05_f64.powi(2) - 0.03_f64.powi(2)) / ref_area;

        assert!((actual - expected).abs() < 1e-12);
    }

    #[test]
    fn symmetric_step_drag_is_zero_for_flush_equal_radius_stack() {
        let mut booster = booster_stage();
        let mut sustainer = sustainer_stage();
        booster.bodytubes[0].radius = 0.05;
        sustainer.bodytubes[0].radius = 0.05;
        sustainer.nosecone.as_mut().expect("nose").aft_radius = 0.05;
        let active = vec![&booster, &sustainer];
        let ref_area = PI * 0.05_f64.powi(2);

        assert_eq!(
            symmetric_step_pressure_cd_from_stages(&active, 2.0, ref_area),
            0.0
        );
    }

    /// Test 6: compute_cd_table spans the required Mach range with a
    /// transonic rise and eased high-supersonic drag.
    ///
    /// [01.1-01 adjustment]: the `1.5x` rise threshold from the old hand-fit
    /// Gaussian model does not hold for the ported vonKarman+base curve.
    /// Actual computed values for this vehicle's Sustainer (roughness=1e-6):
    /// cd(0.5)=0.3333, cd(1.0)=0.4103 (ratio ~1.231x) -- the real
    /// vonKarman-table nose term is small near Mach 1.0 (shape_cd=0.027 vs.
    /// blunt_cd=1.084 at that knot, fineness-power-law-damped well below the
    /// blunt reference), so the total rise is real but more gradual than the
    /// old artificially peaky Gaussian assumed. The `1.2x` threshold below
    /// keeps meaningful margin under the actual ~1.231x ratio while still
    /// asserting a genuine transonic rise (not a hand-tuned exact match).
    /// `cd(6.0) < cd(1.2)` (easing at high supersonic Mach) is unaffected
    /// and still holds (0.1146 < 0.3865).
    #[test]
    fn test_compute_cd_table_transonic_rise_and_supersonic_ease() {
        let stage = sustainer_stage();
        let table = compute_cd_table_from_stages(&[&stage], 1e-6, crate::PhysicsMode::HyperReal);

        let required_machs = [0.0, 0.5, 0.9, 1.0, 1.1, 1.2, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0];
        for m in required_machs {
            assert!(
                table.0.iter().any(|(tm, _)| (tm - m).abs() < 1e-9),
                "Missing mach {}",
                m
            );
        }

        let cd = |m: f64| {
            table
                .0
                .iter()
                .find(|(tm, _)| (tm - m).abs() < 1e-9)
                .unwrap()
                .1
        };
        let cd_05 = cd(0.5);
        let cd_10 = cd(1.0);
        let cd_12 = cd(1.2);
        let cd_60 = cd(6.0);

        assert!(
            cd_10 > 1.2 * cd_05,
            "cd(1.0)={cd_10} must exceed 1.2x cd(0.5)={cd_05} (transonic rise)"
        );
        assert!(
            cd_60 < cd_12,
            "cd(6.0)={cd_60} must be less than cd(1.2)={cd_12} (eases at high supersonic Mach)"
        );
    }

    /// Task 1 unit test: calculate_base_cd's two-branch closed form, exact
    /// within 1e-12.
    #[test]
    fn test_calculate_base_cd() {
        assert!((calculate_base_cd(0.5) - 0.1525).abs() < 1e-12);
        assert!((calculate_base_cd(2.0) - 0.125).abs() < 1e-12);
    }

    /// Task 1 unit test: calculate_stagnation_cd's two-branch closed form,
    /// within 1e-9.
    #[test]
    fn test_calculate_stagnation_cd() {
        assert!((calculate_stagnation_cd(0.0) - 0.85).abs() < 1e-9);
        assert!((calculate_stagnation_cd(1.0) - 1.08375).abs() < 1e-9);
    }

    /// Task 1 unit test: interp_clamped hits exact knot values and clamps
    /// flat outside the VON_KARMAN_TABLE's [0.9, 3.0] domain.
    #[test]
    fn test_von_karman_interp_clamped() {
        assert_eq!(interp_clamped(&VON_KARMAN_TABLE, 1.0), 0.027);
        assert_eq!(interp_clamped(&VON_KARMAN_TABLE, 1.2), 0.081);
        assert_eq!(interp_clamped(&VON_KARMAN_TABLE, 0.5), 0.0);
        assert_eq!(interp_clamped(&VON_KARMAN_TABLE, 5.0), 0.083);
    }

    /// Task 1 unit test: nose_pressure_cd is flat above the vonKarman
    /// table's Mach-3.0 ceiling (Pitfall 2) -- no closed-form extrapolation.
    #[test]
    fn test_nose_pressure_cd_clamps_above_mach_3() {
        let above = nose_pressure_cd(6.0, 5.4477);
        let at_ceiling = nose_pressure_cd(3.0, 5.4477);
        assert!(
            (above - at_ceiling).abs() < 1e-12,
            "nose_pressure_cd(6.0)={above} must equal nose_pressure_cd(3.0)={at_ceiling}"
        );
    }

    /// Task 1 unit test: streamlining (fineness > 1) reduces nose pressure
    /// drag strictly below the blunt-body stagnation value.
    #[test]
    fn test_nose_pressure_cd_below_stagnation() {
        let nose_cd = nose_pressure_cd(2.0, 5.4477);
        let stagnation = calculate_stagnation_cd(2.0);
        assert!(
            nose_cd.is_finite(),
            "nose_pressure_cd must be finite, got {nose_cd}"
        );
        assert!(
            nose_cd > 0.0,
            "nose_pressure_cd must be positive, got {nose_cd}"
        );
        assert!(
            nose_cd < stagnation,
            "nose_pressure_cd={nose_cd} must be < stagnation_cd={stagnation} for fineness > 1"
        );
    }

    /// Test 7: compute_aero combines nosecone + all finsets and returns
    /// Ok(..) for both the Booster and Sustainer stage geometries with a
    /// positive reference_area.
    #[test]
    fn test_compute_aero_returns_ok_for_both_stages() {
        let sustainer = sustainer_stage();
        let sustainer_cg = 1.0; // representative CG position, m from nose tip
        let result = compute_aero(
            &[&sustainer],
            sustainer_cg,
            1e-6,
            crate::PhysicsMode::HyperReal,
        );
        assert!(result.is_ok(), "sustainer compute_aero failed: {result:?}");
        let coeffs = result.unwrap();
        assert!(coeffs.reference_area > 0.0);
        assert!(!coeffs.cd_table.is_empty());

        let booster = booster_stage();
        let booster_cg = 0.6;
        let result = compute_aero(&[&booster], booster_cg, 1e-6, crate::PhysicsMode::HyperReal);
        assert!(result.is_ok(), "booster compute_aero failed: {result:?}");
        let coeffs = result.unwrap();
        assert!(coeffs.reference_area > 0.0);
        assert!(!coeffs.cd_table.is_empty());
    }

    #[test]
    fn test_compute_aero_tags_friction_model_from_physics_mode() {
        let stage = sustainer_stage();
        let hyperreal = compute_aero(&[&stage], 1.0, 1e-6, crate::PhysicsMode::HyperReal)
            .expect("hyperreal aero");
        let openrocket = compute_aero(&[&stage], 1.0, 1e-6, crate::PhysicsMode::OpenRocketLegacy)
            .expect("openrocket aero");

        assert_eq!(
            hyperreal.friction_params.model,
            crate::sim_core::vehicle::FrictionModel::HyperReal
        );
        assert_eq!(
            openrocket.friction_params.model,
            crate::sim_core::vehicle::FrictionModel::OpenRocketLegacy
        );
    }

    #[test]
    fn openrocket_mode_includes_fin_nonfriction_drag() {
        let with_fins = sustainer_stage();
        let mut without_fins = with_fins.clone();
        without_fins.finsets.clear();

        let with_table =
            compute_cd_table_from_stages(&[&with_fins], 1e-6, crate::PhysicsMode::OpenRocketLegacy);
        let without_table = compute_cd_table_from_stages(
            &[&without_fins],
            1e-6,
            crate::PhysicsMode::OpenRocketLegacy,
        );

        let cd_at = |table: &[(f64, f64)], mach: f64| {
            table
                .iter()
                .find(|(tm, _)| (*tm - mach).abs() < 1e-9)
                .map(|(_, cd)| *cd)
                .expect("mach point")
        };
        let with_fins_cd = cd_at(&with_table.0, 4.0);
        let without_fins_cd = cd_at(&without_table.0, 4.0);

        assert!(
            with_fins_cd > without_fins_cd,
            "OpenRocketLegacy CD must include fin pressure/base drag: with={with_fins_cd}, without={without_fins_cd}"
        );
    }

    #[test]
    fn openrocket_mode_uses_fin_cross_section_for_base_drag() {
        let mut airfoil = sustainer_stage();
        airfoil.finsets[0].cross_section = "airfoil".to_string();
        let mut rounded = airfoil.clone();
        rounded.finsets[0].cross_section = "rounded".to_string();
        let mut square = airfoil.clone();
        square.finsets[0].cross_section = "square".to_string();

        let radius = reference_radius_from_stages(&[&airfoil]).max(1e-6);
        let ref_area = PI * radius.powi(2);
        let mach = 4.0;

        let airfoil_or = fin_pressure_and_base_drag_cd_from_stages(
            &[&airfoil],
            mach,
            ref_area,
            crate::PhysicsMode::OpenRocketLegacy,
        );
        let rounded_or = fin_pressure_and_base_drag_cd_from_stages(
            &[&rounded],
            mach,
            ref_area,
            crate::PhysicsMode::OpenRocketLegacy,
        );
        let square_or = fin_pressure_and_base_drag_cd_from_stages(
            &[&square],
            mach,
            ref_area,
            crate::PhysicsMode::OpenRocketLegacy,
        );
        let mut double_wedge = airfoil.clone();
        double_wedge.finsets[0].cross_section = "double-wedge".to_string();
        let double_wedge_or = fin_pressure_and_base_drag_cd_from_stages(
            &[&double_wedge],
            mach,
            ref_area,
            crate::PhysicsMode::OpenRocketLegacy,
        );

        assert!(
            rounded_or > airfoil_or,
            "rounded fins must carry trailing-edge base drag in OR mode"
        );
        assert!(
            square_or > rounded_or,
            "square fins must carry full base drag and stagnation pressure in OR mode"
        );
        assert_eq!(
            double_wedge_or, square_or,
            "OpenRocket loads organic double-wedge fins as its square cross-section"
        );

        let airfoil_hyper = fin_pressure_and_base_drag_cd_from_stages(
            &[&airfoil],
            mach,
            ref_area,
            crate::PhysicsMode::HyperReal,
        );
        let rounded_hyper = fin_pressure_and_base_drag_cd_from_stages(
            &[&rounded],
            mach,
            ref_area,
            crate::PhysicsMode::HyperReal,
        );
        assert!(
            (airfoil_hyper - rounded_hyper).abs() < 1e-12,
            "HyperReal fin pressure/base drag should retain the previous cross-section-agnostic behavior"
        );
    }
}
