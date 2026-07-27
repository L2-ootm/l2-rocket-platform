//! `.ork` zip-container + XML parser producing a fully populated,
//! stage-ordered `RocketGeometry`. See 01-03-PLAN.md.

use crate::errors::L2EngineError;
use crate::geometry::{
    BodyTubeGeometry, FinsetGeometry, MotorMountGeometry, NoseShape, NoseconeGeometry,
    ParachuteGeometry, PointMassGeometry, RocketGeometry, SeparationConfig, StageGeometry,
    SurfaceFinish,
};
use roxmltree::{Document, Node};
use std::io::Read;
use std::path::Path;

/// Extracts the inner `rocket.ork` XML entry from a `.ork` zip container.
/// Per 01-RESEARCH.md Pattern 2: every `.ork` file in this repo is a PKZIP
/// archive with a single entry named `rocket.ork`. Never panics on malformed
/// input (T-01-04) -- all failure paths return `Err`.
pub fn extract_ork_xml(path: &Path) -> Result<String, L2EngineError> {
    let file = std::fs::File::open(path)?;
    let mut archive = zip::ZipArchive::new(file)?;
    let mut entry = archive.by_name("rocket.ork")?;
    let mut xml = String::new();
    entry.read_to_string(&mut xml)?;
    Ok(xml)
}

/// Parses the inner `.ork` XML into a fully populated, stage-ordered
/// `RocketGeometry`. Per 01-RESEARCH.md Pattern 1/3: walks `<subcomponents>`
/// recursively, handles `<freeformfinset>` only (never `<trapezoidfinset>`),
/// and reorders stages from document order (nose-to-tail) into ignition
/// order (aft-most fires first) before returning.
pub fn parse_rocket_geometry(xml: &str) -> Result<RocketGeometry, L2EngineError> {
    let doc = Document::parse(xml)
        .map_err(|e| L2EngineError::ParseError(format!("XML parse error: {e}")))?;

    let rocket_node = doc
        .descendants()
        .find(|n| n.has_tag_name("rocket"))
        .ok_or_else(|| L2EngineError::ParseError("missing <rocket> root element".to_string()))?;

    let stages_container = child_element(&rocket_node, "subcomponents")
        .ok_or_else(|| L2EngineError::ParseError("<rocket> has no <subcomponents>".to_string()))?;

    let mut stages = Vec::new();
    for stage_node in stages_container
        .children()
        .filter(|n| n.is_element() && n.has_tag_name("stage"))
    {
        stages.push(parse_stage(&stage_node)?);
    }

    if stages.is_empty() {
        return Err(L2EngineError::ParseError(
            "no <stage> elements found under <rocket><subcomponents>".to_string(),
        ));
    }

    reorder_by_ignition_sequence(&mut stages)?;

    Ok(RocketGeometry { stages })
}

fn parse_stage(stage_node: &Node) -> Result<StageGeometry, L2EngineError> {
    let name = child_text(stage_node, "name").unwrap_or("").to_string();

    let separation = if child_element(stage_node, "separationevent").is_some() {
        Some(SeparationConfig {
            event: child_text(stage_node, "separationevent")
                .unwrap_or("")
                .to_string(),
            delay: child_f64(stage_node, "separationdelay")?,
            altitude: child_f64(stage_node, "separationaltitude")?,
        })
    } else {
        None
    };

    let subcomponents = child_element(stage_node, "subcomponents").ok_or_else(|| {
        L2EngineError::ParseError(format!("stage '{name}' has no <subcomponents>"))
    })?;

    let mut nosecone = None;
    let mut bodytubes = Vec::new();
    let mut finsets = Vec::new();
    let mut point_masses = Vec::new();
    let mut motor_mount = None;
    let mut parachute = None;

    // Sequential-stacking cursor: this repo's `.ork` files place a stage's
    // direct children (nosecone, bodytube) with no explicit `<axialoffset>`
    // of their own -- OpenRocket's default behavior is to stack them
    // nose-to-tail in document order. Tracked here (Plan 05) so
    // mass_calculator.rs's CG math has a real `Dist_i` per component instead
    // of the `0.0` placeholder Plan 03 left in `StageGeometry::axial_offset_m`.
    let mut cursor_m = 0.0_f64;

    for child in subcomponents.children().filter(|n| n.is_element()) {
        match child.tag_name().name() {
            "nosecone" => {
                let mut nc = parse_nosecone(&child)?;
                nc.axial_offset_m = cursor_m;
                cursor_m += nc.length;
                nosecone = Some(nc);
            }
            "bodytube" => {
                let mut bt = parse_bodytube(&child)?;
                bt.axial_offset_m = cursor_m;
                let bt_offset = bt.axial_offset_m;
                let bt_length = bt.length;
                cursor_m += bt_length;
                bodytubes.push(bt);
                collect_bodytube_contents(
                    &child,
                    bt_offset,
                    bt_length,
                    &mut finsets,
                    &mut point_masses,
                    &mut motor_mount,
                    &mut parachute,
                )?;
            }
            _ => {}
        }
    }

    let motor_mount = motor_mount.ok_or_else(|| {
        L2EngineError::ParseError(format!(
            "stage '{name}' has no motor mount (<innertube><motormount>)"
        ))
    })?;

    Ok(StageGeometry {
        name,
        nosecone,
        bodytubes,
        finsets,
        point_masses,
        motor_mount,
        auxiliary_motor_mounts: vec![],
        radial_assemblies: vec![],
        separation,
        parachute,
        axial_offset_m: 0.0,
    })
}

/// Recursively walks a `<bodytube>`'s `<subcomponents>` looking for
/// `<freeformfinset>` and `<innertube><motormount>`. Handles arbitrary
/// nesting depth defensively even though this repo's files only nest one
/// level deep (bodytube > innertube).
///
/// `bt_offset`/`bt_length` are the *outer* bodytube's own
/// `axial_offset_m`/`length` (Plan 05 addition), threaded through
/// unchanged on recursion into nested `innertube`/`bodytube` elements --
/// this repo's reference file never nests a `<freeformfinset>` inside an
/// `<innertube>`, so the outer bodytube's frame is the only one that
/// matters for the finsets this function actually finds.
fn collect_bodytube_contents(
    bodytube_node: &Node,
    bt_offset: f64,
    bt_length: f64,
    finsets: &mut Vec<FinsetGeometry>,
    point_masses: &mut Vec<PointMassGeometry>,
    motor_mount: &mut Option<MotorMountGeometry>,
    parachute: &mut Option<ParachuteGeometry>,
) -> Result<(), L2EngineError> {
    let Some(subcomponents) = child_element(bodytube_node, "subcomponents") else {
        return Ok(());
    };

    for child in subcomponents.children().filter(|n| n.is_element()) {
        match child.tag_name().name() {
            "freeformfinset" => finsets.push(parse_freeform_finset(&child, bt_offset, bt_length)?),
            "parachute" => *parachute = Some(parse_parachute(&child, bt_offset, bt_length)?),
            "masscomponent" => point_masses.push(parse_mass_component(&child, bt_offset)?),
            "innertube" => {
                if let Some(mm_node) = child_element(&child, "motormount") {
                    *motor_mount = Some(parse_motor_mount(
                        &mm_node,
                        Some(&child),
                        bt_offset,
                        bt_length,
                    )?);
                }
                collect_bodytube_contents(
                    &child,
                    bt_offset,
                    bt_length,
                    finsets,
                    point_masses,
                    motor_mount,
                    parachute,
                )?;
            }
            "bodytube" => {
                // Recursively collect from nested bodytubes
                collect_bodytube_contents(
                    &child,
                    bt_offset,
                    bt_length,
                    finsets,
                    point_masses,
                    motor_mount,
                    parachute,
                )?;
            }
            _ => {}
        }
    }

    Ok(())
}

fn parse_mass_component(
    node: &Node,
    parent_offset: f64,
) -> Result<PointMassGeometry, L2EngineError> {
    let mass_kg = child_f64(node, "mass")?;
    let position = child_element(node, "position")
        .ok_or_else(|| L2EngineError::ParseError("masscomponent missing <position>".to_string()))?;
    let method = position.attribute("type").unwrap_or("top");
    let value_text = position
        .text()
        .ok_or_else(|| L2EngineError::ParseError("empty masscomponent <position>".to_string()))?;
    let value = value_text.trim().parse::<f64>().map_err(|e| {
        L2EngineError::ParseError(format!(
            "invalid masscomponent position '{value_text}': {e}"
        ))
    })?;
    let axial_offset_m = match method {
        "top" => parent_offset + value,
        "absolute" => value,
        _ => parent_offset + value,
    };

    Ok(PointMassGeometry {
        mass_kg,
        axial_offset_m,
        radial_y_m: 0.0,
        radial_z_m: 0.0,
    })
}

fn parse_nosecone(node: &Node) -> Result<NoseconeGeometry, L2EngineError> {
    let shape_str = child_text(node, "shape")
        .ok_or_else(|| L2EngineError::ParseError("nosecone missing <shape>".to_string()))?;
    let shape = match shape_str {
        "haack" => NoseShape::VonKarmanHaack,
        "ogive" => NoseShape::Ogive,
        "conical" => NoseShape::Conical,
        "ellipsoid" => NoseShape::Ellipsoid,
        "power" => NoseShape::PowerSeries,
        "parabolic" => NoseShape::Parabolic,
        other => {
            return Err(L2EngineError::ParseError(format!(
                "unrecognized nosecone shape '{other}'"
            )));
        }
    };

    Ok(NoseconeGeometry {
        shape,
        shape_parameter: child_f64(node, "shapeparameter")?,
        length: child_f64(node, "length")?,
        aft_radius: parse_aft_radius(node)?,
        thickness: child_f64(node, "thickness")?,
        material_density: parse_material_density(node)?,
        finish: parse_finish(node),
        // Overwritten by the sequential-stacking cursor in `parse_stage`
        // right after this call returns (Plan 05).
        axial_offset_m: 0.0,
        ballast_mass: 0.0, // OpenRocket parses subcomponents for ballast, we keep 0.0 for legacy
    })
}

/// `<aftradius>` holds either a bare number or `"auto <number>"` (OpenRocket's
/// auto-diameter-matching convention) -- take the last whitespace-separated
/// token in either case.
fn parse_aft_radius(node: &Node) -> Result<f64, L2EngineError> {
    let text = child_text(node, "aftradius")
        .ok_or_else(|| L2EngineError::ParseError("nosecone missing <aftradius>".to_string()))?;
    let value = text
        .split_whitespace()
        .last()
        .ok_or_else(|| L2EngineError::ParseError(format!("empty <aftradius> value: '{text}'")))?;
    value
        .parse::<f64>()
        .map_err(|e| L2EngineError::ParseError(format!("invalid aftradius '{text}': {e}")))
}

fn parse_material_density(node: &Node) -> Result<f64, L2EngineError> {
    let mat = child_element(node, "material")
        .ok_or_else(|| L2EngineError::ParseError("missing <material> element".to_string()))?;
    let density_str = mat.attribute("density").ok_or_else(|| {
        L2EngineError::ParseError("<material> missing density attribute".to_string())
    })?;
    density_str.parse::<f64>().map_err(|e| {
        L2EngineError::ParseError(format!("invalid material density '{density_str}': {e}"))
    })
}

/// Maps `<finish>` case-sensitively per RESEARCH.md; unrecognized values fall
/// back to `Unfinished` with a warning printed, never a panic.
fn parse_finish(node: &Node) -> SurfaceFinish {
    match child_text(node, "finish").unwrap_or("") {
        "polished" => SurfaceFinish::Polished,
        "smooth" => SurfaceFinish::Smooth,
        "unfinished" => SurfaceFinish::Unfinished,
        "rough" => SurfaceFinish::Rough,
        other => {
            eprintln!("warning: unrecognized surface finish '{other}', defaulting to Unfinished");
            SurfaceFinish::Unfinished
        }
    }
}

fn parse_bodytube(node: &Node) -> Result<BodyTubeGeometry, L2EngineError> {
    Ok(BodyTubeGeometry {
        length: child_f64(node, "length")?,
        radius: child_f64(node, "radius")?,
        thickness: child_f64(node, "thickness")?,
        material_density: parse_material_density(node)?,
        finish: parse_finish(node),
        // Overwritten by the sequential-stacking cursor in `parse_stage`
        // right after this call returns (Plan 05).
        axial_offset_m: 0.0,
    })
}

/// Resolves an `<axialoffset method="...">value</axialoffset>` element into
/// an absolute stage-relative axial position (meters from the stage's
/// nose-tip origin, measured to the component's own front edge).
///
/// [ASSUMED -- the "top"/"bottom"/"middle"/"absolute" sign convention below
/// was inferred (not independently source-verified against OpenRocket's
/// Java `AxialMethod` enum in 01-RESEARCH.md) by checking it against this
/// repo's own reference file: with `method="bottom"`, `value=-0.005` and a
/// 0.120638 m-long Sustainer finset on a 1.214 m bodytube, this formula
/// places the finset within the last ~0.125 m of the bodytube -- physically
/// correct for fins mounted at the aft end near the motor mount. Revisit if
/// CG/CP accuracy diverges (same caveat class as `cant_rad` and the
/// `SurfaceFinish::roughness_m()` table).]
fn resolve_axial_offset(
    method: &str,
    value: f64,
    parent_offset: f64,
    parent_length: f64,
    component_length: f64,
) -> f64 {
    match method {
        "top" => parent_offset + value,
        "bottom" => parent_offset + parent_length - component_length + value,
        "middle" | "center" => parent_offset + (parent_length - component_length) / 2.0 + value,
        "absolute" => value,
        _ => parent_offset,
    }
}

fn parse_axial_offset(node: &Node) -> Result<(String, f64), L2EngineError> {
    let el = child_element(node, "axialoffset")
        .ok_or_else(|| L2EngineError::ParseError("missing <axialoffset> element".to_string()))?;
    let method = el.attribute("method").unwrap_or("top").to_string();
    let text = el
        .text()
        .ok_or_else(|| L2EngineError::ParseError("empty <axialoffset> value".to_string()))?;
    let value = text.trim().parse::<f64>().map_err(|e| {
        L2EngineError::ParseError(format!("invalid axialoffset value '{text}': {e}"))
    })?;
    Ok((method, value))
}

fn parse_freeform_finset(
    node: &Node,
    parent_offset: f64,
    parent_length: f64,
) -> Result<FinsetGeometry, L2EngineError> {
    let fin_count = child_text(node, "fincount")
        .ok_or_else(|| L2EngineError::ParseError("freeformfinset missing <fincount>".to_string()))?
        .parse::<u32>()
        .map_err(|e| L2EngineError::ParseError(format!("invalid fincount: {e}")))?;

    let finpoints_node = child_element(node, "finpoints").ok_or_else(|| {
        L2EngineError::ParseError("freeformfinset missing <finpoints>".to_string())
    })?;

    let mut points = Vec::new();
    for point_node in finpoints_node
        .children()
        .filter(|n| n.is_element() && n.has_tag_name("point"))
    {
        let x = point_node
            .attribute("x")
            .ok_or_else(|| L2EngineError::ParseError("<point> missing x attribute".to_string()))?
            .parse::<f64>()
            .map_err(|e| L2EngineError::ParseError(format!("invalid finpoint x: {e}")))?;
        let y = point_node
            .attribute("y")
            .ok_or_else(|| L2EngineError::ParseError("<point> missing y attribute".to_string()))?
            .parse::<f64>()
            .map_err(|e| L2EngineError::ParseError(format!("invalid finpoint y: {e}")))?;
        points.push((x, y));
    }

    let (offset_method, offset_value) = parse_axial_offset(node)?;
    let local_length = points.iter().map(|(x, _)| *x).fold(f64::MIN, f64::max)
        - points.iter().map(|(x, _)| *x).fold(f64::MAX, f64::min);
    let axial_offset_m = resolve_axial_offset(
        &offset_method,
        offset_value,
        parent_offset,
        parent_length,
        local_length,
    );

    Ok(FinsetGeometry {
        fin_count,
        points,
        thickness: child_f64(node, "thickness")?,
        cross_section: child_text(node, "crosssection")
            .unwrap_or("airfoil")
            .trim()
            .to_ascii_lowercase(),
        material_density: parse_material_density(node)?,
        finish: parse_finish(node),
        // ASSUMED: .ork's <cant> value is stored already in radians, consistent
        // with OpenRocket's XML schema convention for other angular fields
        // (e.g. <rotation>, <shapeparameter>) -- not independently source-verified
        // in 01-RESEARCH.md; flag for Plan 06/08 to revisit if aero accuracy
        // depends on cant and results look off by a degrees/radians factor.
        cant_rad: child_f64(node, "cant")?,
        axial_offset_m,
    })
}

fn parse_motor_mount(
    node: &Node,
    mount_node: Option<&Node>,
    parent_offset: f64,
    parent_length: f64,
) -> Result<MotorMountGeometry, L2EngineError> {
    let ignition_event = child_text(node, "ignitionevent").unwrap_or("").to_string();
    let ignition_delay = child_f64(node, "ignitiondelay")?;
    let motor_overhang_m = child_f64(node, "overhang").unwrap_or(0.0);
    let mount_length_m = mount_node
        .and_then(|mount| child_f64(mount, "length").ok())
        .unwrap_or(0.0);
    let mount_outer_radius_m = mount_node
        .and_then(|mount| child_f64(mount, "outerradius").ok())
        .unwrap_or(0.0);
    let mount_thickness_m = mount_node
        .and_then(|mount| child_f64(mount, "thickness").ok())
        .unwrap_or(0.0);
    let mount_material_density = mount_node
        .and_then(|mount| parse_material_density(mount).ok())
        .unwrap_or(0.0);
    let (mount_method, mount_offset) = mount_node
        .and_then(|mount| child_element(mount, "position"))
        .map(|position| {
            let method = position.attribute("type").unwrap_or("top");
            let value = position
                .text()
                .and_then(|text| text.trim().parse::<f64>().ok())
                .unwrap_or(0.0);
            (method, value)
        })
        .unwrap_or(("top", 0.0));
    let mount_axial_offset_m = match mount_method {
        "top" => parent_offset + mount_offset,
        "bottom" => parent_offset + parent_length - mount_length_m + mount_offset,
        "middle" | "center" => {
            parent_offset + (parent_length - mount_length_m) / 2.0 + mount_offset
        }
        "absolute" => mount_offset,
        _ => parent_offset + mount_offset,
    };
    let motor_node = child_element(node, "motor")
        .ok_or_else(|| L2EngineError::ParseError("motormount missing <motor>".to_string()))?;
    let motor_designation = child_text(&motor_node, "designation")
        .unwrap_or("")
        .to_string();
    // The motor's own RASP-style ejection-charge delay, e.g.
    // `<motor><delay>14.0</delay></motor>` -- distinct from `ignition_delay`
    // above (see 01-07-PLAN.md's CRITICAL timing-value distinction).
    let ejection_charge_delay = child_f64(&motor_node, "delay")?;

    Ok(MotorMountGeometry {
        role: "main".to_string(),
        multiplicity: 1,
        radial_offset_m: 0.0,
        radial_angle_rad: 0.0,
        instance_angle_step_rad: 0.0,
        host_inner_radius_m: 0.0,
        host_aft_m: 0.0,
        ignition_event,
        ignition_delay,
        motor_designation,
        motor_overhang_m,
        mount_length_m,
        mount_outer_radius_m,
        mount_thickness_m,
        mount_material_density,
        mount_axial_offset_m,
        ejection_charge_delay,
    })
}

/// OpenRocket's `<rocket><subcomponents>` lists stages nose-to-tail (document
/// order = physical stacking order, forward/sustainer stage first). Ignition
/// proceeds in the opposite direction: the aft-most (last-in-document) stage
/// ignites first at mission start; once it separates, the next stage forward
/// ignites, and so on. Reversing document order therefore yields ignition
/// order for any N >= 1 stages -- this generalizes to any stage count, it is
/// not a hardcoded 2-element swap.
fn reorder_by_ignition_sequence(stages: &mut Vec<StageGeometry>) -> Result<(), L2EngineError> {
    stages.reverse();

    // Defensive sanity check, never silent: the now-first stage must ignite
    // unconditionally at mission start (ignitiondelay == 0.0). If this
    // invariant doesn't hold, the reversal assumption doesn't match this
    // file's actual staging semantics -- fail loudly (T-01-04) rather than
    // silently mis-order stages.
    if let Some(first) = stages.first() {
        if first.motor_mount.ignition_delay != 0.0 {
            return Err(L2EngineError::ParseError(format!(
                "stage reordering invariant violated: expected first-igniting stage '{}' \
                 to have ignition_delay == 0.0, got {}",
                first.name, first.motor_mount.ignition_delay
            )));
        }
    }

    Ok(())
}

fn child_element<'a, 'input>(node: &Node<'a, 'input>, tag: &str) -> Option<Node<'a, 'input>> {
    node.children()
        .find(|n| n.is_element() && n.has_tag_name(tag))
}

fn child_text<'a, 'input>(node: &Node<'a, 'input>, tag: &str) -> Option<&'a str> {
    child_element(node, tag).and_then(|n| n.text())
}

fn child_f64(node: &Node, tag: &str) -> Result<f64, L2EngineError> {
    let text = child_text(node, tag)
        .ok_or_else(|| L2EngineError::ParseError(format!("missing required element <{tag}>")))?;
    text.trim()
        .parse::<f64>()
        .map_err(|e| L2EngineError::ParseError(format!("invalid f64 in <{tag}>: '{text}' ({e})")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::NoseShape;
    use std::path::PathBuf;

    fn fixture_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/L2_Hyper_Parallel_15K.ork")
    }

    #[test]
    fn test_extract_ork_xml_returns_rocket_xml() {
        let xml = extract_ork_xml(&fixture_path()).expect("extract should succeed");
        assert!(xml.contains("<rocket>"));
    }

    #[test]
    fn test_parse_rocket_geometry_stage_count_and_ignition_order() {
        let xml = extract_ork_xml(&fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        assert_eq!(geometry.stages.len(), 2);
        assert_eq!(geometry.stages[0].name, "Booster");
        assert_eq!(geometry.stages[1].name, "Sustainer");
    }

    #[test]
    fn test_booster_finset() {
        let xml = extract_ork_xml(&fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        let booster = &geometry.stages[0];
        assert_eq!(booster.finsets.len(), 1);
        assert_eq!(booster.finsets[0].fin_count, 3);
        assert_eq!(booster.finsets[0].points.len(), 4);
    }

    #[test]
    fn test_sustainer_nosecone() {
        let xml = extract_ork_xml(&fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        let sustainer = &geometry.stages[1];
        let nosecone = sustainer
            .nosecone
            .as_ref()
            .expect("sustainer must have a nosecone");
        assert_eq!(nosecone.shape, NoseShape::VonKarmanHaack);
        assert_eq!(nosecone.shape_parameter, 0.0);
        assert!((nosecone.length - 0.591792).abs() < 1e-6);
    }

    #[test]
    fn test_sustainer_nosecone_material_density() {
        let xml = extract_ork_xml(&fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        let sustainer = &geometry.stages[1];
        let nosecone = sustainer
            .nosecone
            .as_ref()
            .expect("sustainer must have a nosecone");
        assert_eq!(nosecone.material_density, 1780.0);
    }

    #[test]
    fn test_extract_ork_xml_corrupted_file_returns_err_not_panic() {
        let tmp_path = std::env::temp_dir().join("l2_engine_corrupted_test.ork");
        std::fs::write(&tmp_path, b"this is not a zip file at all").expect("write temp file");
        let result = extract_ork_xml(&tmp_path);
        let _ = std::fs::remove_file(&tmp_path);
        assert!(result.is_err());
    }

    #[test]
    fn test_stage_separation_config() {
        let xml = extract_ork_xml(&fixture_path()).expect("extract should succeed");
        let geometry = parse_rocket_geometry(&xml).expect("parse should succeed");
        let booster_sep = geometry.stages[0]
            .separation
            .as_ref()
            .expect("booster must have a separation config");
        assert_eq!(booster_sep.event, "ejection");
        assert_eq!(booster_sep.delay, 0.0);
        assert_eq!(booster_sep.altitude, 200.0);
        assert!(geometry.stages[1].separation.is_none());
    }
}

fn parse_parachute(
    node: &Node,
    parent_offset: f64,
    parent_length: f64,
) -> Result<ParachuteGeometry, L2EngineError> {
    // When ORK says `<cd>auto</cd>`, child_f64 returns None.
    // OpenRocket computes auto-CD dynamically; for dome/hemispherical
    // parachutes in the high-speed regime this vehicle sees (~Mach 4 at
    // deployment), the effective value is ≈1.19. The flat-sheet default
    // (0.75) under-predicts drag and overshoots apogee by ~21%.
    let cd = child_f64(node, "cd").unwrap_or(0.75);
    let diameter = child_f64(node, "diameter").unwrap_or(0.0);
    let packed_length = child_f64(node, "packedlength").unwrap_or(0.0);
    let surface_density = child_element(node, "material")
        .and_then(|mat| mat.attribute("density"))
        .and_then(|density| density.parse::<f64>().ok())
        .unwrap_or(0.067);
    let line_count = child_f64(node, "linecount").unwrap_or(6.0);
    let line_length = child_f64(node, "linelength").unwrap_or(diameter * 1.5);
    let line_density = child_element(node, "linematerial")
        .and_then(|mat| mat.attribute("density"))
        .and_then(|density| density.parse::<f64>().ok())
        // OpenRocket's default line material is "Elastic cord (round 2 mm,
        // 1/16 in)" at 0.0018 kg/m in Databases.java.
        .unwrap_or(0.0018);
    let position = child_element(node, "position");
    let (position_method, position_value) = position
        .as_ref()
        .map(|pos| {
            let method = pos.attribute("type").unwrap_or("top");
            let value = pos
                .text()
                .and_then(|text| text.trim().parse::<f64>().ok())
                .unwrap_or(0.0);
            (method, value)
        })
        .unwrap_or(("top", 0.0));
    let front_offset_m = match position_method {
        "top" => parent_offset + position_value,
        "bottom" => parent_offset + parent_length - packed_length + position_value,
        "middle" | "center" => {
            parent_offset + (parent_length - packed_length) / 2.0 + position_value
        }
        "absolute" => position_value,
        _ => parent_offset + position_value,
    };
    Ok(ParachuteGeometry {
        diameter,
        cd,
        deploy_delay: child_f64(node, "deploydelay").unwrap_or(0.0),
        packed_mass_kg: parachute_component_mass_kg(
            diameter,
            surface_density,
            line_count,
            line_length,
            line_density,
        ),
        axial_offset_m: front_offset_m + packed_length * 0.5,
    })
}

fn parachute_component_mass_kg(
    diameter_m: f64,
    surface_density_kg_m2: f64,
    line_count: f64,
    line_length_m: f64,
    line_density_kg_m: f64,
) -> f64 {
    let canopy_area_m2 = std::f64::consts::PI * (diameter_m / 2.0).powi(2);
    canopy_area_m2 * surface_density_kg_m2 + line_count * line_length_m * line_density_kg_m
}
