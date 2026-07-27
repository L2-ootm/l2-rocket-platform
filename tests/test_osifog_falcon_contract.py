import hashlib
import xml.etree.ElementTree as ET
import zipfile

import pytest

import osifog_sweep as sweep


def _params():
    main = 16  # J510W, 38 mm
    retro = 19  # K550W, 54 mm
    radius = 0.074
    return {
        "s0_main": main,
        "s0_retro": retro,
        "s1_main": main,
        "s1_retro": retro,
        "main_cluster_count": 3,
        "s0_body_rad": radius,
        "s1_body_rad": radius,
        "s0_body_len": sweep._body_len(main, retro, main_cluster_count=3),
        "s1_body_len": sweep._body_len(main, retro, main_cluster_count=3),
        "s0_retro_delay": 120.0,
        "s1_retro_delay": 90.0,
        "nose_mass_kg": 0.05,
        "s0_mid_ballast_kg": 2.0,
        "s0_aft_ballast_kg": 0.0,
        "s1_mid_ballast_kg": 2.0,
        "s1_aft_ballast_kg": 0.0,
        "launch_azimuth": 288.0,
        "launch_angle_deg": 0.0,
        "wind_levels": sweep.parse_wind_csv(sweep.WIND_CSV),
    }


def test_saved_ork_canonicalization_removes_runtime_only_entropy(tmp_path):
    first = tmp_path / "first.ork"
    second = tmp_path / "second.ork"
    templates = [
        (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            "0.017",
            (2026, 7, 24, 12, 0, 0),
        ),
        (
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "0.029",
            (2026, 7, 24, 13, 0, 0),
        ),
    ]
    for path, (warning_id, event_id, computation_time, timestamp) in zip(
        (first, second), templates
    ):
        xml = (
            "<openrocket><warning><id>%s</id></warning>"
            "<event id=\"%s\" warnid=\"%s\"/>"
            "<datapoint>1,2,0.01,%s,0</datapoint></openrocket>"
            % (warning_id, event_id, warning_id, computation_time)
        ).encode()
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("rocket.ork", date_time=timestamp)
            archive.writestr(info, xml)
        sweep._canonicalize_saved_ork(path)

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(
        second.read_bytes()
    ).digest()


def test_falcon_cluster_is_three_outer_motors_plus_one_center_retro_per_stage():
    root = ET.fromstring(sweep.generate_ork(_params()))
    motor_mounts = [
        tube for tube in root.findall(".//innertube")
        if tube.find("motormount") is not None
    ]
    configurations = [tube.findtext("clusterconfiguration") for tube in motor_mounts]

    assert configurations.count("3-ring") == 2
    assert configurations.count("single") == 2
    assert root.findall(".//clustercount") == []
    assert len(root.findall(".//stage")) == 2
    assert len(root.findall(".//simulation")) == 1
    for stage_name in ("Sustainer", "Booster"):
        stage = next(
            item
            for item in root.findall(".//stage")
            if item.findtext("name") == stage_name
        )
        mounts = [
            tube
            for tube in stage.findall(".//innertube")
            if tube.find("motormount") is not None
        ]
        main = next(tube for tube in mounts if "Main Motor Mount" in tube.findtext("name"))
        retro = next(
            tube for tube in mounts if "Structural Retro Sleeve" in tube.findtext("name")
        )
        assert main.findtext("clusterconfiguration") == "3-ring"
        assert retro.findtext("clusterconfiguration") == "single"
        assert float(retro.findtext("motormount/ignitiondelay")) > 0.0


def test_fin_thickness_is_a_real_rule_bounded_geometry_variable():
    params = _params()
    params["s0_fin_thickness_m"] = 0.001
    params["s0_fin_material"] = "cardboard"
    root = ET.fromstring(sweep.generate_ork(params))
    sustainer_fins = next(
        fins
        for fins in root.findall(".//freeformfinset")
        if fins.findtext("name") == "Sustainer Fins"
    )
    assert float(sustainer_fins.findtext("thickness")) == pytest.approx(0.001)
    assert sustainer_fins.findtext("material") == "Cardboard"

    params["s0_fin_thickness_m"] = 0.0009
    with pytest.raises(ValueError, match="mission minimum"):
        sweep.generate_ork(params)

    params["s0_fin_thickness_m"] = 0.001
    params["s0_fin_material"] = "balsa"
    with pytest.raises(ValueError, match="approved legal fin material"):
        sweep.generate_ork(params)

    params["s0_fin_material"] = "legal_balsa"
    legal_root = ET.fromstring(sweep.generate_ork(params))
    assert legal_root.findtext(".//freeformfinset/material") == (
        "Selected Balsa (0.17 g/cm3)"
    )


def test_sustainer_forward_grid_fins_are_native_visible_geometry():
    params = _params()
    params.update(
        s0_grid_fin_count=4,
        s0_grid_fin_root=0.05,
        s0_grid_fin_height=0.04,
        s0_grid_fin_thickness_m=0.001,
        s0_grid_fin_position_m=0.03,
    )
    root = ET.fromstring(sweep.generate_ork(params))
    grid_fins = next(
        fins
        for fins in root.findall(".//freeformfinset")
        if fins.findtext("name") == "Sustainer Forward Grid Fins"
    )
    assert grid_fins.findtext("fincount") == "4"
    assert float(grid_fins.findtext("position")) == pytest.approx(0.03)
    assert float(grid_fins.findtext("thickness")) == pytest.approx(0.001)


def test_submission_xml_has_official_environment_and_no_passive_recovery():
    root = ET.fromstring(sweep.generate_ork(_params()))

    assert root.findtext(".//basetemperature") == "303.25"
    assert root.findtext(".//basepressure") == "100000.0"
    assert root.find(".//wind[@model='multilevel']").attrib["altituderef"] == "agl"
    assert len(root.findall(".//windlevel")) == 28
    assert root.findall(".//parachute") == []
    assert root.findall(".//streamer") == []


def test_stage_separation_trigger_is_selectable_and_rejects_unknown_values():
    params = _params()
    params.update(
        s1_separation_event="launch",
        s1_separation_delay=23.593,
    )
    root = ET.fromstring(sweep.generate_ork(params))
    booster = next(
        stage
        for stage in root.findall(".//stage")
        if stage.findtext("name") == "Booster"
    )

    assert booster.findtext("separationevent") == "launch"
    assert float(booster.findtext("separationdelay")) == pytest.approx(23.593)

    params["s1_separation_event"] = "not-an-openrocket-event"
    with pytest.raises(ValueError, match="s1_separation_event"):
        sweep.generate_ork(params)


def test_saved_flight_event_reference_gate_rejects_gui_save_crash_shape():
    broken = """
    <openrocket>
      <simulations><simulation>
        <warnings><warning><id>warning-1</id></warning></warnings>
        <databranch name="Booster">
          <event id="warning-event" warnid="warning-1"
                 eventid="missing-separation-event"/>
        </databranch>
      </simulation></simulations>
    </openrocket>
    """
    valid = broken.replace(
        '<event id="warning-event"',
        '<event id="missing-separation-event"/><event id="warning-event"',
    )

    assert sweep.validate_serialized_flight_event_references(valid) == []
    assert sweep.validate_serialized_flight_event_references(broken) == [
        "Booster: warning event references missing eventid "
        "missing-separation-event"
    ]


def test_candidate_identity_and_physical_ballast_are_deterministic():
    params = _params()
    first = ET.fromstring(sweep.generate_ork(params))
    second = ET.fromstring(sweep.generate_ork(params))

    assert first.find(".//motorconfiguration").attrib["configid"] == second.find(
        ".//motorconfiguration"
    ).attrib["configid"]
    ballast = next(
        tube for tube in first.findall(".//innertube")
        if tube.findtext("name") == "S0 Mid Ballast rod 1"
    )
    density = float(ballast.find("material").attrib["density"])
    length = float(ballast.findtext("length"))
    radius = float(ballast.findtext("outerradius"))
    assert density * 3 * 3.141592653589793 * radius**2 * length == pytest.approx(2.0)
    assert ballast.findtext("thickness") == ballast.findtext("outerradius")
    assert ballast.findtext("clusterconfiguration") == "single"


def test_legacy_batch_can_still_omit_centering_rings():
    params = _params()
    root = ET.fromstring(sweep.generate_ork(params))

    assert root.findall(".//centeringring") == []
    assert root.findall(".//tubecoupler") == []
    sleeves = [
        tube
        for tube in root.findall(".//innertube")
        if "Structural Retro Sleeve" in (tube.findtext("name") or "")
    ]
    assert len(sleeves) == 2
    for sleeve in sleeves:
        outer = float(sleeve.findtext("outerradius"))
        wall = float(sleeve.findtext("thickness"))
        assert outer == pytest.approx(0.0315)
        assert wall == pytest.approx(0.00425)
        expected_length = (
            params["s0_body_len"]
            if sleeve.findtext("name").startswith("Sustainer")
            else params["s1_body_len"]
        )
        assert float(sleeve.findtext("length")) == pytest.approx(expected_length)

    main_mounts = [
        tube
        for tube in root.findall(".//innertube")
        if "Main Motor Mount" in (tube.findtext("name") or "")
    ]
    assert len(main_mounts) == 2
    for mount in main_mounts:
        main_outer = float(mount.findtext("outerradius"))
        center_distance = (
            2.0
            * main_outer
            / 3.0**0.5
            * float(mount.findtext("clusterscale"))
        )
        assert center_distance + main_outer == pytest.approx(0.072)
        assert center_distance - main_outer == pytest.approx(0.0315)


def test_submission_ring_mode_uses_two_airframe_spanning_rings_per_stage():
    params = _params()
    params.update(
        s0_main=None,
        s0_body_rad=0.082,
        s1_body_rad=0.082,
        octaweb_rings=True,
    )
    root = ET.fromstring(sweep.generate_ork(params))
    rings = root.findall(".//centeringring")

    assert len(rings) == 4
    assert len({ring.findtext("id") for ring in rings}) == 4
    assert all(float(ring.findtext("radialposition")) == 0.0 for ring in rings)
    assert all(float(ring.findtext("outerradius")) == pytest.approx(0.080) for ring in rings)
    assert all(
        float(ring.findtext("outerradius")) - float(ring.findtext("innerradius"))
        >= sweep.MIN_DIMENSION_M
        for ring in rings
    )

    stages = {
        stage.findtext("name"): stage
        for stage in root.findall(".//stage")
    }
    sustainer_rings = stages["Sustainer"].findall(".//centeringring")
    booster_rings = stages["Booster"].findall(".//centeringring")
    assert len(sustainer_rings) == 2
    assert len(booster_rings) == 2

    sustainer_sleeve = next(
        tube
        for tube in stages["Sustainer"].findall(".//innertube")
        if "Structural Retro Sleeve" in (tube.findtext("name") or "")
    )
    sleeve_outer = float(sustainer_sleeve.findtext("outerradius"))
    assert all(
        float(ring.findtext("innerradius")) == pytest.approx(sleeve_outer)
        for ring in sustainer_rings
    )
    assert sorted(float(ring.findtext("position")) for ring in sustainer_rings) == pytest.approx(
        [0.0, params["s0_body_len"] - 0.005]
    )

    booster_main = next(
        tube
        for tube in stages["Booster"].findall(".//innertube")
        if "Main Motor Mount" in (tube.findtext("name") or "")
    )
    main_outer = float(booster_main.findtext("outerradius"))
    cluster_envelope = (
        2.0
        * main_outer
        / 3.0**0.5
        * float(booster_main.findtext("clusterscale"))
        + main_outer
    )
    assert all(
        float(ring.findtext("innerradius")) == pytest.approx(cluster_envelope)
        for ring in booster_rings
    )
    assert not sweep.validate_compiled_centering_rings(
        ET.tostring(root, encoding="unicode")
    )


def test_saved_retro_only_ring_auto_radius_is_valid_but_cluster_auto_is_not():
    params = _params()
    params.update(
        s0_main=None,
        s0_body_rad=0.082,
        s1_body_rad=0.082,
        octaweb_rings=True,
    )
    root = ET.fromstring(sweep.generate_ork(params))
    stages = {
        stage.findtext("name"): stage
        for stage in root.findall(".//stage")
    }
    for ring in stages["Sustainer"].findall(".//centeringring"):
        ring.find("innerradius").text = "auto"

    xml = ET.tostring(root, encoding="unicode")
    assert not sweep.validate_compiled_centering_rings(xml)

    stages["Booster"].find(".//centeringring/innerradius").text = "auto"
    violations = sweep.validate_compiled_centering_rings(
        ET.tostring(root, encoding="unicode")
    )
    assert any("must be explicit, not auto" in item for item in violations)


def test_legacy_nose_ballast_is_contained_but_fails_attachment_gate():
    params = __import__("osifog_precision").falcon_submission_candidate()
    root = ET.fromstring(sweep.generate_ork(params))
    nose = root.find(".//nosecone")
    ballast = nose.find(".//bulkhead")
    position = float(ballast.findtext("position"))
    radius = float(ballast.findtext("outerradius"))
    available = sweep._haack_radius(
        position, float(nose.findtext("length")), float(nose.findtext("aftradius"))
    ) - 0.003

    assert radius <= available + 1.0e-9
    assert radius >= sweep.MIN_DIMENSION_M
    violations = sweep.validate_compiled_nose_ballast_attachment(
        ET.tostring(root, encoding="unicode"), params["nose_mass_kg"]
    )
    assert any("nose ballast floats" in item for item in violations)


def test_shell_bonded_aluminum_nose_bulkhead_touches_wall_at_exact_mass():
    params = __import__("osifog_precision").falcon_submission_candidate()
    params.update(
        nose_length_m=0.12,
        nose_ballast_pos_m=0.09,
        nose_mass_kg=0.05,
        s0_body_rad=0.082,
        nose_ballast_attachment="nose_shell_bonded",
        nose_ballast_material="aluminum",
    )
    root = ET.fromstring(sweep.generate_ork(params))
    nose = root.find(".//nosecone")
    ballast = nose.find("./subcomponents/bulkhead")
    position = float(ballast.findtext("position"))
    radius = float(ballast.findtext("outerradius"))
    length = float(ballast.findtext("length"))
    density = float(ballast.find("material").get("density"))
    inner_wall = (
        sweep._haack_radius(
            position,
            float(nose.findtext("length")),
            float(nose.findtext("aftradius")),
        )
        - float(nose.findtext("thickness"))
    )

    assert radius == pytest.approx(inner_wall, abs=1.0e-9)
    assert length >= sweep.MIN_DIMENSION_M
    assert density == pytest.approx(2700.0)
    assert density * 3.141592653589793 * radius**2 * length == pytest.approx(
        params["nose_mass_kg"], rel=1.0e-6
    )
    assert not sweep.validate_compiled_nose_ballast_attachment(
        ET.tostring(root, encoding="unicode"), params["nose_mass_kg"]
    )


def test_shell_bonded_steel_nose_ballast_rejects_subminimum_thickness():
    params = __import__("osifog_precision").falcon_submission_candidate()
    params.update(
        nose_length_m=0.12,
        nose_ballast_pos_m=0.09,
        nose_mass_kg=0.05,
        s0_body_rad=0.082,
        nose_ballast_attachment="nose_shell_bonded",
        nose_ballast_material="steel",
    )

    with pytest.raises(ValueError, match="shell-bonded thickness"):
        sweep.generate_ork(params)


def test_nose_body_joint_has_matching_outer_diameter_and_shoulder_clearance():
    params = __import__("osifog_precision").falcon_submission_candidate()
    root = ET.fromstring(sweep.generate_ork(params))
    nose = root.find(".//nosecone")
    sustainer = next(
        body for body in root.findall(".//bodytube")
        if body.findtext("name") == "Sustainer Airframe"
    )
    body_radius = float(sustainer.findtext("radius"))
    body_inner_radius = body_radius - float(sustainer.findtext("thickness"))
    shoulder_radius = float(nose.findtext("aftshoulderradius"))

    assert float(nose.findtext("aftradius")) == body_radius
    assert body_inner_radius - shoulder_radius >= 0.001 - 1.0e-9


def test_oversized_three_plus_one_cluster_is_rejected():
    params = _params()
    params.update(s0_main=17, s1_main=17, s0_retro=19, s1_retro=19)
    params["s0_body_rad"] = params["s1_body_rad"] = 0.074

    try:
        sweep.generate_ork(params)
    except ValueError as error:
        assert "insufficient room for a legal central support sleeve" in str(error)
    else:
        raise AssertionError("overlapping motor cluster was accepted")


def test_ascent_stability_excludes_post_apogee_retro_climb():
    minimum = sweep._minimum_initial_ascent_stability(
        altitudes=[0.0, 100.0, 200.0, 150.0, 20.0, 30.0],
        vertical_speeds=[50.0, 30.0, 0.0, -20.0, -5.0, 4.0],
        stabilities=[2.0, 1.6, 1.55, 1.2, 0.8, -0.2],
    )

    assert minimum == pytest.approx(1.6)


def test_booster_ascent_stability_stops_at_stage_separation():
    minimum = sweep._minimum_initial_ascent_stability(
        altitudes=[0.0, 100.0, 150.0, 180.0, 170.0],
        vertical_speeds=[50.0, 30.0, 20.0, 5.0, -5.0],
        stabilities=[2.0, 1.7, 1.6, -0.4, -0.8],
        times=[0.0, 1.0, 2.4, 2.5, 4.0],
        end_time_s=2.5,
    )

    assert minimum == pytest.approx(1.6)


def test_post_apogee_stage_separation_fails_genuine_staging_gate():
    params = _params()
    params["s1_separation_delay"] = 36.5
    params["plugged_motors"] = True
    params["octaweb_rings"] = True
    params["nose_ballast_attachment"] = "nose_shell_bonded"
    metrics = {
        "status": "SIMULATION_COMPLETE",
        "mach": 0.8,
        "min_static_margin": 2.0,
        "event_times": {"APOGEE": [23.25], "STAGE_SEPARATION": [39.0]},
        "stage_landings": [
            {"total_speed": 1.0, "orientation_theta_deg": 80.0},
            {"total_speed": 1.0, "orientation_theta_deg": 82.0},
        ],
    }

    legal, violations = sweep.validate_hard_constraints(metrics, params)
    official_legal, official_violations = sweep.validate_official_constraints(
        metrics, params
    )

    assert not legal
    assert any("Genuine staging gate" in item for item in violations)
    assert official_legal, official_violations


def test_official_gate_enforces_confirmed_rules_not_retired_conventions():
    params = _params()
    params.update(
        plugged_motors=True,
        octaweb_rings=True,
        nose_ballast_attachment="nose_shell_bonded",
        s1_separation_delay=20.0,
    )
    metrics = {
        "status": "Up To Date",
        "mach": 0.999,
        "event_times": {"STAGE_SEPARATION": [23.6]},
        "stage_landings": [
            {"total_speed": 4.999, "orientation_theta_deg": 75.0},
            {"total_speed": 2.0, "orientation_theta_deg": 82.0},
        ],
    }

    legal, violations = sweep.validate_official_constraints(metrics, params)

    assert legal, violations

    metrics["mach"] = 1.0
    metrics["stage_landings"][0]["total_speed"] = 5.0
    params["octaweb_rings"] = False
    params["nose_ballast_attachment"] = "free"
    legal, violations = sweep.validate_official_constraints(metrics, params)

    assert not legal
    assert any("Subsonic gate" in item for item in violations)
    assert any("Stage 0 crash" in item for item in violations)
    assert any("centering rings" in item for item in violations)
    assert any("Nose ballast" in item for item in violations)


def test_central_retro_motors_must_ignite_only_during_descent():
    params = _params()
    metrics = {
        "status": "SIMULATION_COMPLETE",
        "mach": 0.8,
        "min_static_margin": 2.0,
        "event_times": {
            "BURNOUT": [2.5, 5.0],
            "STAGE_SEPARATION": [2.5],
            "APOGEE": [10.0, 25.0],
            "IGNITION": [0.0, 2.5, 30.0, 40.0],
        },
        "branch_event_times": [
            {"APOGEE": [25.0], "IGNITION": [0.0, 2.5, 30.0]},
            {"APOGEE": [10.0], "IGNITION": [0.0, 20.0]},
        ],
        "stage_landings": [
            {"branch": 0, "time_s": 31.0, "total_speed": 1.0},
            {"branch": 1, "time_s": 21.0, "total_speed": 1.0},
        ],
        "retro_burn_diagnostics": [
            {
                "branch": 0,
                "retro_braking_verified": True,
                "fraction_opposing_velocity": 1.0,
            },
            {
                "branch": 1,
                "retro_braking_verified": True,
                "fraction_opposing_velocity": 1.0,
            },
        ],
    }

    legal, violations = sweep.validate_hard_constraints(metrics, params)

    assert legal, violations


def test_supersonic_and_disconnected_stage_geometry_fail_closed():
    params = _params()
    params["s1_body_rad"] += 0.001
    metrics = {
        "status": "SIMULATION_COMPLETE",
        "mach": 0.95,
        "min_static_margin": 1.5,
        "stage_landings": [
            {"total_speed": 0.0},
            {"total_speed": 0.0},
        ],
    }

    legal, violations = sweep.validate_hard_constraints(metrics, params)

    assert not legal
    assert any("Supersonic safety gate" in item for item in violations)
    assert any("radii differ" in item for item in violations)
