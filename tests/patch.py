import re

with open('l2_engine/src/barrowman.rs', 'r', encoding='utf-8') as f:
    code = f.read()

cd_at_mach_old = '''fn cd_at_mach(
    stage: &StageGeometry,
    mach: f64,
    surface_roughness_m: f64,
    physics_mode: crate::PhysicsMode,
) -> f64 {
    let radius = reference_radius(stage).max(1e-6);
    let ref_area = PI * radius.powi(2);

    let length = stage_length(stage);
    let wetted_area = 2.0 * PI * radius * length + fin_wetted_area(stage);

    let cf = if mach < 0.01 {
        // Assume minimal value near 0 to avoid singularity in some logs
        0.001
    } else {
        // See PLAN 06 equations
        let mut reynolds = mach * 340.0 * length / 1.5e-5;
        if reynolds < 1e4 {
            reynolds = 1e4;
        }
        let cf_incompressible = 0.074 / reynolds.powf(0.2);
        let cf_compressible = cf_incompressible / (1.0 + 0.14 * mach.powi(2)).powf(0.58);
        let cf_roughness = 1.0 / (1.89 + 1.62 * (length / surface_roughness_m).log10()).powi(2);
        cf_compressible.max(cf_roughness)
    };

    let friction_cd = cf * (wetted_area / ref_area);

    let nc_frontal_area = stage
        .nosecone
        .as_ref()
        .map(|nc| PI * nc.aft_radius.powi(2))
        .unwrap_or(0.0);
    let fineness = stage
        .nosecone
        .as_ref()
        .map(|nc| nc.length / (2.0 * nc.aft_radius))
        .unwrap_or(1.0);

    let nose_pressure_cd_term = if mach < 0.9 {
        0.0
    } else if mach < 1.1 {
        // Subsonic-to-supersonic transition (Plan 06 linear ramp)
        let cd_supersonic = 0.15 * (1.0 / fineness.powi(2));
        cd_supersonic * (mach - 0.9) / 0.2
    } else {
        0.15 * (1.0 / fineness.powi(2))
    };

    let base_cd = calculate_base_cd(mach, physics_mode);
    let fin_cd = fin_wave_drag_cd(stage, mach, ref_area, physics_mode);

    println!("Mach: {:.1} | Total CD: {:.4} | Friction CD: {:.4} | Base CD: {:.4} | Nose CD: {:.4} | Fin CD: {:.4}", mach, friction_cd + nose_pressure_cd_term + base_cd + fin_cd, friction_cd, base_cd, nose_pressure_cd_term, fin_cd);

    // Sum friction + pressure + base
    friction_cd + nose_pressure_cd_term + base_cd + fin_cd
}'''

cd_at_mach_new = '''fn cd_at_mach(
    active_stages: &[&StageGeometry],
    mach: f64,
    surface_roughness_m: f64,
    physics_mode: crate::PhysicsMode,
) -> f64 {
    let radius = reference_radius_from_stages(active_stages).max(1e-6);
    let ref_area = PI * radius.powi(2);

    let length = stage_length(active_stages);
    let wetted_area = 2.0 * PI * radius * length + fin_wetted_area(active_stages);

    let cf = if mach < 0.01 {
        0.001
    } else {
        let mut reynolds = mach * 340.0 * length / 1.5e-5;
        if reynolds < 1e4 {
            reynolds = 1e4;
        }
        let cf_incompressible = 0.074 / reynolds.powf(0.2);
        let cf_compressible = cf_incompressible / (1.0 + 0.14 * mach.powi(2)).powf(0.58);
        let cf_roughness = 1.0 / (1.89 + 1.62 * (length / surface_roughness_m).log10()).powi(2);
        cf_compressible.max(cf_roughness)
    };

    let friction_cd = cf * (wetted_area / ref_area);

    let top_nc = active_stages.iter().find_map(|s| s.nosecone.as_ref());
    let nc_frontal_area = top_nc.map(|nc| PI * nc.aft_radius.powi(2)).unwrap_or(0.0);
    let fineness = top_nc.map(|nc| nc.length / (2.0 * nc.aft_radius)).unwrap_or(1.0);

    let nose_pressure_cd_term = if mach < 0.9 {
        0.0
    } else if mach < 1.1 {
        let cd_supersonic = 0.15 * (1.0 / fineness.powi(2));
        cd_supersonic * (mach - 0.9) / 0.2
    } else {
        0.15 * (1.0 / fineness.powi(2))
    };

    let base_cd = calculate_base_cd(mach, physics_mode);
    let fin_cd = fin_wave_drag_cd(active_stages, mach, ref_area, physics_mode);

    println!("Mach: {:.1} | Total CD: {:.4} | Friction CD: {:.4} | Base CD: {:.4} | Nose CD: {:.4} | Fin CD: {:.4}", mach, friction_cd + nose_pressure_cd_term + base_cd + fin_cd, friction_cd, base_cd, nose_pressure_cd_term, fin_cd);

    friction_cd + nose_pressure_cd_term + base_cd + fin_cd
}'''
code = code.replace(cd_at_mach_old, cd_at_mach_new)

cd_table_old = '''fn compute_cd_table(stage: &StageGeometry, roughness_m: f64, physics_mode: crate::PhysicsMode) -> Vec<(f64, f64)> {
    // Basic table: from Mach 0.0 to 6.0 in 0.1 increments
    let mut table = Vec::with_capacity(61);
    for i in 0..=60 {
        let m = (i as f64) / 10.0;
        table.push((m, cd_at_mach(stage, m, roughness_m, physics_mode)));
    }
    table
}'''
cd_table_new = '''fn compute_cd_table(active_stages: &[&StageGeometry], roughness_m: f64, physics_mode: crate::PhysicsMode) -> Vec<(f64, f64)> {
    let mut table = Vec::with_capacity(61);
    for i in 0..=60 {
        let m = (i as f64) / 10.0;
        table.push((m, cd_at_mach(active_stages, m, roughness_m, physics_mode)));
    }
    table
}'''
code = code.replace(cd_table_old, cd_table_new)

with open('l2_engine/src/barrowman.rs', 'w', encoding='utf-8') as f:
    f.write(code)

print("Patch 3 done")
