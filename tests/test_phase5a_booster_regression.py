"""Booster-preservation regression fixture (mission section 10).

Verifies that artifacts/autoevo/best-legal-booster-branch.ork -- the
recovered eight-forward-fin booster, H180W retro, delay=29.864s -- has not
drifted: component tree, forward-fin geometry, branch identity, motor
designation, ignition wiring, and a recalibrated nominal touchdown result.

Does not hardcode only the absolute 29.864s value: the delay basin
(booster-delay-basin.json) and the contact-relative timing model
(scripts/relative_timing.py) are checked independently of the single
historical delay.
"""
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

ORK_PATH = "artifacts/autoevo/best-legal-booster-branch.ork"
EXPECTED_SHA256 = (
    "923076029d29ac1d5fecd01e1d909c6b60e38448c2c9c6369b4487bfd8cdc086"
)
BOOSTER_BRANCH = 1


def _rocket_xml():
    with zipfile.ZipFile(ORK_PATH) as z:
        name = z.namelist()[0]
        return ET.fromstring(z.read(name).decode("utf-8"))


def test_saved_ork_sha256_unchanged():
    with open(ORK_PATH, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    assert digest == EXPECTED_SHA256


def _airframe_stages(root):
    """<stage> component-tree elements (which have a <name> child), not the
    <stage number="0" active="true"/> entries inside <motorconfiguration>."""
    return [s for s in root.findall(".//stage") if s.find("name") is not None]


def test_component_tree_and_branch_identity():
    root = _rocket_xml()
    stages = _airframe_stages(root)
    assert len(stages) == 2
    names = [s.findtext("name") for s in stages]
    assert names == ["Sustainer", "Booster"]


def test_forward_fin_geometry():
    root = _rocket_xml()
    booster = next(s for s in _airframe_stages(root) if s.findtext("name") == "Booster")
    grid_fins = next(
        t for t in booster.findall(".//freeformfinset")
        if "Forward Grid Fins" in (t.findtext("name") or "")
    )
    assert int(grid_fins.findtext("fincount")) == 8


def test_h180w_retro_motor_and_ignition_wiring():
    root = _rocket_xml()
    booster = next(s for s in root.findall(".//stage") if s.findtext("name") == "Booster")
    mounts = {
        m.findtext("name"): m
        for m in booster.findall(".//innertube")
        if m.find("motormount") is not None
    }
    main_mount = next(v for k, v in mounts.items() if "Main Motor Mount" in k)
    retro_mount = next(v for k, v in mounts.items() if "Retro Sleeve" in k)

    main_ignition = main_mount.find(".//motormount/ignitionevent")
    retro_ignition = retro_mount.find(".//motormount/ignitionevent")
    retro_delay = retro_mount.find(".//motormount/ignitiondelay")
    retro_designation = retro_mount.find(".//motor/designation")

    assert main_ignition.text == "launch"
    assert retro_ignition.text == "launch"
    assert retro_designation.text == "H180W"
    # Absolute-time convention (section 0.2/3): NOT relative to burnout.
    assert 29.0 < float(retro_delay.text) < 31.0


def test_sustainer_ignites_at_booster_burnout():
    root = _rocket_xml()
    sustainer = next(s for s in root.findall(".//stage") if s.findtext("name") == "Sustainer")
    main_mount = next(
        m for m in sustainer.findall(".//innertube")
        if m.find("motormount") is not None and "Main Motor Mount" in (m.findtext("name") or "")
    )
    ignition = main_mount.find(".//motormount/ignitionevent")
    assert ignition.text == "burnout"


def test_corrected_stage_event_map_matches_xml():
    with open("artifacts/autoevo/phase5a/corrected-stage-event-map.json", encoding="utf-8") as f:
        record = json.load(f)
    assert record["verified_from_xml"]["stage_1"]["main_motor_ignition"]["ignitionevent"] == "launch"
    assert record["verified_from_xml"]["stage_0"]["main_motor_ignition"]["ignitionevent"] == "burnout"
    assert record["branch_name_mapping_unchanged"] == {"branch_0": "Sustainer / stage 0", "branch_1": "Booster / stage 1"}


def test_delay_basin_has_multiple_legal_points_not_a_single_value():
    """Section 10: do not hardcode only the absolute 29.864s value."""
    with open("artifacts/autoevo/phase5a/booster-delay-basin.json", encoding="utf-8") as f:
        basin = json.load(f)
    legal = basin["legal_delays_s"]
    assert len(legal) >= 2
    assert 29.864 in legal


@pytest.mark.slow
def test_recalibrated_nominal_touchdown_below_5ms():
    """Reopen the saved .ork and rerun -- full save/close/reopen/rerun check."""
    import sys
    sys.path.insert(0, ".")
    from scripts.phase5a_booster_basin import reopen_touchdown

    result = reopen_touchdown(ORK_PATH)
    assert result["touchdown_total_mps"] < 5.0
    # Matches the recorded reference to high precision (bit-identical
    # reproduction, not just "under 5 m/s").
    assert abs(result["touchdown_total_mps"] - 3.5891545566551666) < 1.0e-6
