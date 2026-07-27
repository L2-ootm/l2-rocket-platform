use l2_engine::sim_core::vehicle::{FrictionModel, FrictionParams, Stage, StageBuilder};

fn stage_with_friction_model(model: FrictionModel) -> Stage {
    StageBuilder::new("friction-test")
        .cd(0.9)
        .cd_table(vec![(0.0, 0.4), (6.0, 0.4)])
        .cd_nonfric_table(vec![(0.0, 0.2), (6.0, 0.2)])
        .friction_params(FrictionParams {
            vehicle_length: 1.5,
            wetted_area_ratio: 12.0,
            body_wetted_area_ratio: 10.0,
            body_fineness_ratio: 10.0,
            roughness_m: 1e-6,
            model,
        })
        .build()
}

fn expected_openrocket_skin_friction_cf(mach: f64, reynolds: f64) -> f64 {
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

    if mach < 0.9 {
        cf * c1
    } else if mach < 1.1 {
        cf * (c2 * (mach - 0.9) / 0.2 + c1 * (1.1 - mach) / 0.2)
    } else {
        cf * c2
    }
}

fn expected_openrocket_component_cf(mach: f64, reynolds: f64) -> f64 {
    let smooth = expected_openrocket_skin_friction_cf(mach, reynolds);
    let roughness_correction = if mach < 0.9 {
        1.0 - 0.1 * mach.powi(2)
    } else if mach > 1.1 {
        1.0 / (1.0 + 0.18 * mach.powi(2))
    } else {
        let c1 = 1.0 - 0.1 * 0.9_f64.powi(2);
        let c2 = 1.0 / (1.0 + 0.18 * 1.1_f64.powi(2));
        c2 * (mach - 0.9) / 0.2 + c1 * (1.1 - mach) / 0.2
    };
    let roughness_limited = 0.032 * (1e-6_f64 / 1.5).powf(0.2) * roughness_correction;
    smooth.max(roughness_limited)
}

#[test]
fn openrocket_mode_runtime_cd_uses_openrocket_skin_friction_law() {
    let mach = 0.55;
    let speed = mach * 340.3;
    let kinematic_viscosity = 1.5e-5;
    let stage = stage_with_friction_model(FrictionModel::OpenRocketLegacy);
    let reynolds = speed * 1.5 / kinematic_viscosity;
    let fineness_correction = 1.0 + 1.0 / (2.0 * 10.0);
    let expected =
        0.2 + expected_openrocket_component_cf(mach, reynolds) * (2.0 + 10.0 * fineness_correction);

    let actual = stage.cd_at_conditions(mach, speed, kinematic_viscosity);

    assert!((actual - expected).abs() < 1e-12);
}

#[test]
fn fineness_correction_only_changes_legacy_body_friction() {
    let mach = 0.55;
    let speed = mach * 340.3;
    let viscosity = 1.5e-5;
    let mut corrected = stage_with_friction_model(FrictionModel::OpenRocketLegacy);
    let mut uncorrected = corrected.clone();
    uncorrected
        .friction_params
        .as_mut()
        .unwrap()
        .body_fineness_ratio = f64::INFINITY;
    let delta = corrected.cd_at_conditions(mach, speed, viscosity)
        - uncorrected.cd_at_conditions(mach, speed, viscosity);
    let reynolds = speed * 1.5 / viscosity;
    let expected_delta = expected_openrocket_component_cf(mach, reynolds) * 10.0 * 0.05;
    assert!((delta - expected_delta).abs() < 1e-12);

    corrected.friction_params.as_mut().unwrap().model = FrictionModel::HyperReal;
    uncorrected.friction_params.as_mut().unwrap().model = FrictionModel::HyperReal;
    assert_eq!(
        corrected.cd_at_conditions(mach, speed, viscosity),
        uncorrected.cd_at_conditions(mach, speed, viscosity)
    );
}

#[test]
fn hyperreal_mode_runtime_cd_keeps_distinct_skin_friction_law() {
    let mach = 0.55;
    let speed = mach * 340.3;
    let kinematic_viscosity = 1.5e-5;
    let hyperreal = stage_with_friction_model(FrictionModel::HyperReal).cd_at_conditions(
        mach,
        speed,
        kinematic_viscosity,
    );
    let openrocket = stage_with_friction_model(FrictionModel::OpenRocketLegacy).cd_at_conditions(
        mach,
        speed,
        kinematic_viscosity,
    );

    assert!((hyperreal - openrocket).abs() > 1e-9);
}
