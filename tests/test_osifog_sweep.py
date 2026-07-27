import math
import json
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import osifog_precision
import osifog_sweep


def _candidate_f_parameters():
    report = Path("designs/osifog_submission/candidate_F_report.json")
    return json.loads(report.read_text(encoding="utf-8"))["params"]


def _candidate_k_parameters():
    return json.loads(
        Path("designs/osifog_submission/candidate_K_report.json").read_text(
            encoding="utf-8"
        )
    )["params"]


def test_livery_is_opt_in_and_does_not_change_historical_xml():
    parameters = _candidate_k_parameters()
    historical = osifog_sweep.generate_ork(parameters)

    explicit_none = dict(parameters)
    explicit_none["livery"] = None
    assert osifog_sweep.generate_ork(explicit_none) == historical
    assert "<appearance>" not in historical


def test_livery_serializes_only_render_appearance_on_external_components():
    parameters = _candidate_k_parameters()
    parameters["livery"] = {
        "components": {
            "Nose Cone": {"paint": "#7F00FF", "shine": 0.35},
            "Sustainer Airframe": {
                "paint": "#050505",
                "shine": 0.25,
                "decal": {
                    "name": "decals/l2-topographic-sustainer.png",
                    "edgemode": "REPEAT",
                    "scale": {"x": 1.0, "y": 1.0},
                },
            },
            "Booster Airframe": {"paint": [10, 10, 10, 255]},
            "Booster Fins": {"paint": "#7F00FF"},
            "Booster Forward Grid Fins": {"paint": "#00F0FF"},
        }
    }

    xml = osifog_sweep.generate_ork(parameters)
    root = ET.fromstring(xml)

    assert xml.count("<finish>normal</finish>") == 3
    assert xml.count("<appearance>") == 5
    decal = root.find(
        ".//bodytube[name='Sustainer Airframe']/appearance/decal"
    )
    assert decal is not None
    assert decal.get("name") == "decals/l2-topographic-sustainer.png"
    assert decal.get("edgemode") == "REPEAT"
    assert root.find(
        ".//freeformfinset[name='Booster Fins']/appearance/paint"
    ).get("red") == "127"
    assert root.find(
        ".//freeformfinset[name='Booster Forward Grid Fins']/appearance/paint"
    ).get("green") == "240"


def test_canonicalized_ork_keeps_rocket_xml_as_first_zip_entry(tmp_path):
    package = tmp_path / "visual.ork"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("decals/l2.png", b"image")
        archive.writestr(
            "rocket.ork",
            b"<?xml version='1.0'?><openrocket></openrocket>",
        )

    osifog_sweep._canonicalize_saved_ork(package)

    with zipfile.ZipFile(package) as archive:
        assert archive.namelist() == ["rocket.ork", "decals/l2.png"]


def test_descent_alignment_retains_near_impact_candidates_after_global_peaks():
    times = [0.5 * index for index in range(101)]
    altitudes = [1000.0 - 10.0 * index for index in range(101)]
    positions = [0.0] * len(times)
    velocities_z = [-20.0] * len(times)
    velocities_xy = [0.0] * len(times)
    orientations_phi = [0.0] * len(times)
    orientations_theta = [
        math.pi / 2 if time <= 10.0
        else math.asin(0.1) if time < 40.0
        else math.asin(0.8)
        for time in times
    ]

    diagnostic = osifog_sweep._descent_alignment_diagnostic(
        times,
        altitudes,
        positions,
        positions,
        velocities_z,
        velocities_xy,
        orientations_theta,
        orientations_phi,
    )

    retained = diagnostic["alignment_candidates"]
    assert len(retained) == 32
    assert any(candidate["time_s"] >= 40.0 for candidate in retained)
    assert max(candidate["time_s"] for candidate in retained) == 49.5


def test_compiler_serializes_exactly_one_official_anti_tumble_extension():
    parameters = osifog_precision.falcon_850k_candidate()
    inspection = osifog_sweep.inspect_anti_tumble_xml(
        osifog_sweep.generate_ork(parameters)
    )

    assert inspection["valid"] is True
    assert inspection["simulation_count"] == 1
    assert inspection["simulations"][0]["extension_count"] == 1
    assert inspection["script_digest"] == osifog_sweep.ANTI_TUMBLE_SCRIPT_DIGEST


def test_anti_tumble_validator_fails_closed_on_missing_or_altered_script():
    valid, missing = osifog_sweep.validate_anti_tumble_extensions([])
    assert valid is False
    assert missing

    valid, altered = osifog_sweep.validate_anti_tumble_extensions(
        [
            {
                "extensionid": (
                    "info.openrocket.core.simulation.extension.impl."
                    "ScriptingExtension"
                ),
                "script": osifog_sweep.ANTI_TUMBLE_SCRIPT.replace(
                    "return false", "return true"
                ),
            }
        ]
    )
    assert valid is False
    assert altered == [
        "anti-tumble script differs from official normalized script"
    ]


def test_booster_owned_coupler_clears_motors_and_moves_only_sustainer_aft_ring():
    parameters = _candidate_f_parameters()
    parameters.update(
        {
            "interstage_coupler": True,
            "interstage_coupler_length_m": 0.050,
            "interstage_coupler_wall_m": 0.001,
            "interstage_coupler_sustainer_overlap_m": 0.025,
        }
    )

    xml = osifog_sweep.generate_ork(parameters)
    root = ET.fromstring(xml)

    assert osifog_sweep.validate_compiled_interstage_coupler(
        xml, required=True
    ) == []
    assert osifog_sweep.validate_compiled_centering_rings(xml) == []
    assert osifog_sweep.validate_upper_stage_ignition_after_separation(xml) == []

    coupler = root.find(
        ".//rocket/subcomponents/stage[name='Booster']/"
        "subcomponents/bodytube/subcomponents/tubecoupler"
    )
    assert coupler is not None
    assert float(coupler.findtext("position")) == -0.025
    assert float(coupler.findtext("length")) == 0.050
    assert float(coupler.findtext("outerradius")) == 0.080
    assert float(coupler.findtext("thickness")) == 0.001

    sustainer_mount = root.find(
        ".//rocket/subcomponents/stage[name='Sustainer']/"
        "subcomponents/bodytube/subcomponents/innertube[motormount]"
    )
    assert sustainer_mount is not None
    assert float(sustainer_mount.findtext("outerradius")) < 0.079

    sustainer_aft_ring = root.find(
        ".//centeringring[name='Sustainer Motor Cage Ring Aft (Thrust)']"
    )
    booster_aft_ring = root.find(
        ".//centeringring[name='Booster Octaweb Ring Aft (Thrust)']"
    )
    assert float(sustainer_aft_ring.findtext("position")) == 0.670
    assert float(booster_aft_ring.findtext("position")) == 0.995


def test_coupler_validator_rejects_old_aft_ring_position_and_early_upper_ignition():
    parameters = _candidate_f_parameters()
    parameters.update(
        {
            "interstage_coupler": True,
            "interstage_coupler_length_m": 0.050,
            "interstage_coupler_wall_m": 0.001,
            "interstage_coupler_sustainer_overlap_m": 0.025,
        }
    )
    xml = osifog_sweep.generate_ork(parameters)

    overlapping = xml.replace(
        "<position type=\"top\">0.670000000</position>",
        "<position type=\"top\">0.695000000</position>",
        1,
    )
    assert any(
        "overlaps inserted coupler region" in violation
        for violation in osifog_sweep.validate_compiled_interstage_coupler(
            overlapping, required=True
        )
    )

    early_ignition = xml.replace(
        "<ignitiondelay>49.293000</ignitiondelay>",
        "<ignitiondelay>20.000000</ignitiondelay>",
        1,
    )
    assert osifog_sweep.validate_upper_stage_ignition_after_separation(
        early_ignition
    ) == [
        "Sustainer Structural Retro Sleeve: cannot prove ignition "
        "launch+20.000000s occurs after separation launch+23.593000s"
    ]


def test_coupler_is_opt_in_and_historical_ring_station_is_preserved():
    parameters = _candidate_f_parameters()
    xml = osifog_sweep.generate_ork(parameters)
    root = ET.fromstring(xml)

    assert root.find(".//tubecoupler") is None
    aft_ring = root.find(
        ".//centeringring[name='Sustainer Motor Cage Ring Aft (Thrust)']"
    )
    assert float(aft_ring.findtext("position")) == 0.695
    assert osifog_sweep.validate_compiled_interstage_coupler(
        xml, required=False
    ) == []
