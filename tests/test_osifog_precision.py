import xml.etree.ElementTree as ET
import math
import pytest

import osifog_precision as precision
import osifog_sweep as sweep
from mission_evolution import EvolutionEngine


def test_retro_diagnostic_accepts_nose_up_thrust_during_descent():
    diagnostic = sweep._retro_burn_diagnostic(
        times=[0.0, 1.0, 2.0],
        positions_x=[0.0, 0.0, 0.0],
        positions_y=[0.0, 0.0, 0.0],
        velocities_z=[-20.0, -10.0, -2.0],
        orientations_theta=[math.pi / 2] * 3,
        orientations_phi=[0.0] * 3,
        thrust_forces=[100.0] * 3,
    )

    assert diagnostic["retro_braking_verified"]
    assert diagnostic["fraction_opposing_velocity"] == pytest.approx(1.0)
    assert diagnostic["mean_direction_cosine"] == pytest.approx(-1.0)
    assert diagnostic["peak_thrust_sample"]["vertical_power_w"] < 0.0


def test_retro_diagnostic_rejects_nose_down_thrust_during_descent():
    diagnostic = sweep._retro_burn_diagnostic(
        times=[0.0, 1.0, 2.0],
        positions_x=[0.0, 0.0, 0.0],
        positions_y=[0.0, 0.0, 0.0],
        velocities_z=[-20.0, -30.0, -40.0],
        orientations_theta=[-math.pi / 2] * 3,
        orientations_phi=[0.0] * 3,
        thrust_forces=[100.0] * 3,
    )

    assert not diagnostic["retro_braking_verified"]
    assert diagnostic["fraction_opposing_velocity"] == pytest.approx(0.0)
    assert diagnostic["mean_direction_cosine"] == pytest.approx(1.0)
    assert diagnostic["peak_thrust_sample"]["vertical_power_w"] > 0.0


def test_retro_diagnostic_counts_adverse_relaunch_after_braking():
    diagnostic = sweep._retro_burn_diagnostic(
        times=[0.0, 1.0, 2.0],
        positions_x=[0.0, 0.0, 0.0],
        positions_y=[0.0, 0.0, 0.0],
        velocities_z=[-5.0, 5.0, 10.0],
        orientations_theta=[math.pi / 2] * 3,
        orientations_phi=[0.0] * 3,
        thrust_forces=[100.0] * 3,
        apogee_time_s=0.0,
    )

    assert diagnostic["sample_count"] == 3
    assert diagnostic["fraction_opposing_velocity"] == pytest.approx(1 / 3)
    assert not diagnostic["retro_braking_verified"]


def test_descent_alignment_finds_tail_first_ignition_window():
    diagnostic = sweep._descent_alignment_diagnostic(
        times=[0.0, 1.0, 2.0],
        altitudes=[100.0, 80.0, 50.0],
        positions_x=[0.0, 0.0, 0.0],
        positions_y=[0.0, 0.0, 0.0],
        velocities_z=[-10.0, -20.0, -30.0],
        velocities_xy=[0.0, 0.0, 0.0],
        orientations_theta=[math.pi / 2] * 3,
        orientations_phi=[0.0] * 3,
    )

    assert diagnostic["best_alignment_q"] == pytest.approx(1.0)
    assert diagnostic["tail_first_windows"][0]["duration_s"] == pytest.approx(2.0)


def test_submission_candidate_compiles_to_three_plus_one_topology():
    params = precision.falcon_850k_candidate()
    root = ET.fromstring(sweep.generate_ork(params))
    designations = [node.text for node in root.findall(".//motor/designation")]

    assert designations.count("949J150-P") == 1
    assert designations.count("J360") == 1
    assert designations.count("K550W") == 2
    motor_mounts = [
        tube for tube in root.findall(".//innertube")
        if tube.find("motormount") is not None
    ]
    assert [tube.findtext("clusterconfiguration") for tube in motor_mounts].count("3-ring") == 2
    assert root.findtext(".//launchintowind") == "false"
    assert float(root.findtext(".//launchrodangle")) == pytest.approx(3.85)
    assert root.findall(".//parachute") == []
    assert root.findall(".//streamer") == []


def test_final_candidate_preserves_official_environment_and_geometry_limits():
    params = precision.falcon_850k_candidate()
    root = ET.fromstring(sweep.generate_ork(params))

    assert root.findtext(".//basetemperature") == "303.25"
    assert root.findtext(".//basepressure") == "100000.0"
    assert root.find(".//wind[@model='multilevel']").attrib["altituderef"] == "agl"
    assert len(root.findall(".//windlevel")) == 28
    first_level = root.find(".//windlevel")
    assert math.degrees(float(first_level.attrib["direction"])) == pytest.approx(215.0)
    assert len(root.findall(".//stage")) == 2
    assert len(root.findall(".//simulation")) == 1
    assert params["s0_body_len"] + params["s1_body_len"] + max(0.25, params["s0_body_rad"] * 10) < 4.0


def test_every_serialized_wind_level_matches_official_csv():
    root = ET.fromstring(sweep.generate_ork(precision.falcon_850k_candidate()))
    actual = root.findall(".//windlevel")
    expected = sweep.parse_wind_csv("OSIFOG/OpenWind_File.csv")

    assert len(actual) == len(expected) == 28
    for level, (altitude, speed, direction, stddev) in zip(actual, expected):
        assert float(level.attrib["altitude"]) == pytest.approx(altitude)
        assert float(level.attrib["speed"]) == pytest.approx(speed)
        assert math.degrees(float(level.attrib["direction"])) == pytest.approx(
            direction
        )
        assert float(level.attrib["standarddeviation"]) == pytest.approx(
            stddev
        )


def test_json_scoring_table_matches_official_formula_term_by_term():
    params = precision.falcon_850k_candidate()
    params.update(
        plugged_motors=True,
        octaweb_rings=True,
        nose_ballast_attachment="nose_shell_bonded",
    )
    metrics = {
        "status": "SIMULATION_COMPLETE",
        "apogee_m": 3001.0,
        "apogee_east_m": 2.0,
        "apogee_north_m": -3.0,
        "mach": 0.8,
        "min_static_margin": 2.0,
        "m_prop_kg_actual": 1.5,
        "stage_landings": [
            {
                "east_m": 4.0,
                "north_m": 6.0,
                "total_speed": 2.0,
                "orientation_theta_deg": 75.0,
            },
            {
                "east_m": 8.0,
                "north_m": 10.0,
                "total_speed": 4.0,
                "orientation_theta_deg": 82.0,
            },
        ],
    }

    table = precision.score_from_mission_contract(metrics, params)
    legacy = sweep.score_official(metrics, params)

    assert table["raw_score"] == pytest.approx(legacy["raw_score"])
    assert table["is_legal"]
    assert table["score"] == pytest.approx(table["raw_score"])
    assert table["terms"] == {
        "apogee_altitude": pytest.approx(-3000.0),
        "apogee_horizontal": pytest.approx(-208.0),
        "touchdown_position": pytest.approx(-200.0),
        "touchdown_speed": pytest.approx(-4500.0),
        "propellant_used": pytest.approx(-11250.0),
    }


def test_physical_search_space_is_mission_data_not_algorithm_constants():
    contract = precision.load_mission_contract()
    space = contract["evolution"]["physical_repair_space"]

    assert "s1_separation_delay_s" in space
    assert "nose_ballast_mass_kg" in space
    assert "s1_aft_ballast_rod_radius_m" in space
    assert "s1_aft_ballast_attachment" in space
    assert set(space["s0_retro_designations"]).issubset(
        set(contract["motor_pool"]["retro_allowed_designations"])
    )


def test_delay_search_rejects_motor_ignition_after_touchdown():
    def evaluator(params):
        delay = float(params["s0_retro_delay"])
        touchdown = 10.0
        return {
            "mach": 0.8,
            "min_static_margin": 2.0,
            "stage_landings": [{
                "time_s": touchdown,
                "vz_ms": -1.0,
                "total_speed": 0.0 if delay >= touchdown else abs(delay - 9.0) + 1.0,
            }],
        }

    winner, metrics = precision.adaptive_delay_search(
        EvolutionEngine(evaluator),
        {"s0_retro_delay": 12.0},
        stage_index=0,
        low=8.0,
        high=10.5,
        direct_time_limit=11.0,
        rounds=2,
        samples_per_round=6,
    )

    assert winner["s0_retro_delay"] < metrics["stage_landings"][0]["time_s"]
    assert winner["s0_retro_delay"] == pytest.approx(9.0)
