import math
import random
import xml.etree.ElementTree as ET

import pytest

import osifog_engine_search as search
import osifog_reversal_gate as gate


def _delayed_parameters() -> dict:
    parameters = search._sample_valid_parameters(
        random.Random(220726), [(0.0, 1.0, 0.0, 0.0)]
    )
    parameters["s1_separation_delay"] = 12.0
    parameters["s0_pod_angle_offset_deg"] = 60.0
    parameters["s1_pod_angle_offset_deg"] = 0.0
    search._repair_podset_derived_geometry(parameters)
    return parameters


def test_delayed_repair_staggers_cages_and_proves_plume_clearance():
    parameters = _delayed_parameters()
    parameters["s0_pod_angle_offset_deg"] = 0.0
    parameters["s1_pod_angle_offset_deg"] = 0.0

    search._repair_podset_derived_geometry(parameters)

    assert parameters["s0_pod_angle_offset_deg"] == 60.0
    assert parameters["s1_pod_angle_offset_deg"] == 0.0
    assert search._podset_geometry_violations(parameters) == []


def test_openrocket_and_rust_share_delayed_cage_azimuths_without_pod_separation():
    parameters = _delayed_parameters()
    root = ET.fromstring(search.osifog_podset.generate_podset_ork(parameters))
    podsets = {
        item.findtext("name"): float(item.findtext("angleoffset"))
        for item in root.findall(".//podset")
    }
    assert podsets["Sustainer Side Pods"] == pytest.approx(math.radians(60.0))
    assert podsets["Booster Side Pods"] == pytest.approx(0.0)

    pylon_rotations = {
        float(item.findtext("rotation"))
        for item in root.findall(".//freeformfinset")
        if "Sustainer Pylon" in (item.findtext("name") or "")
    }
    assert {round(value % 360.0, 6) for value in pylon_rotations} == {
        60.0, 180.0, 300.0,
    }

    pods = [node for node in search.parameters_to_ast(parameters) if node.node_type == "POD"]
    by_name = {pod.params["name"]: pod for pod in pods}
    assert by_name["Sustainer Ascent Pods"].params["angle_offset_deg"] == 60.0
    assert by_name["Booster Ascent Pods"].params["angle_offset_deg"] == 0.0
    assert all("separation" not in key for pod in pods for key in pod.params)


def test_delayed_gate_requires_both_tail_windows_and_pre_apogee_separation():
    metrics = {
        "mach": 0.80,
        "branch_event_times": [{
            "APOGEE": [15.0],
            "STAGE_SEPARATION": [14.9],
        }],
    }
    record = {
        "metrics": metrics,
        "ascent_margin_cal": 1.7,
        "s0_tail_window_s": 8.0,
        "s1_tail_window_s": 6.0,
        "separates_before_apogee": gate._separates_before_apogee(metrics),
    }
    assert gate._record_passes(record, delayed=True)

    metrics["branch_event_times"][0]["STAGE_SEPARATION"] = [15.1]
    assert not gate._separates_before_apogee(metrics)
    metrics["branch_event_times"][0]["STAGE_SEPARATION"] = [14.9]

    record["s1_tail_window_s"] = 0.0
    assert not gate._record_passes(record, delayed=True)
    record["s1_tail_window_s"] = 6.0
    record["separates_before_apogee"] = False
    assert not gate._record_passes(record, delayed=True)


def test_delayed_variants_only_delay_axial_joint_and_remain_buildable():
    base = _delayed_parameters()
    variants = gate._delayed_variants(base)

    assert len(variants) == 25
    assert all(search._podset_geometry_violations(item) == [] for item in variants)
    assert all(item["s1_separation_delay"] >= 10.0 for item in variants)
    for item in variants:
        pods = [node for node in search.parameters_to_ast(item) if node.node_type == "POD"]
        assert len(pods) == 2
        assert all("separation_delay" not in pod.params for pod in pods)


def test_delayed_chord_slice_stays_on_real_body_and_is_buildable():
    variants = gate._delayed_chord_variants(_delayed_parameters())

    assert variants
    assert all(
        item["s1_core_fin_root"] <= item["s1_core_length"]
        and search._podset_geometry_violations(item) == []
        for item in variants
    )
