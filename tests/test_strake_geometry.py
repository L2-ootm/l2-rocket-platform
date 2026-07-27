"""Regression tests for the strake/keel freeform-fin family (mission Family C).

No OpenRocket JVM is required: `_strake_xml` and `generate_ork` only build the
XML string; JVM/jpype is only needed to actually simulate it.
"""
import pytest

from osifog_sweep import _strake_xml, STRAKE_PLANFORMS, generate_ork


def test_strake_zero_count_is_noop():
    assert _strake_xml(0, "tapered", 0.5, 0.02, 0.75) == ""


@pytest.mark.parametrize("planform", STRAKE_PLANFORMS)
def test_strake_all_planforms_produce_legal_freeformfinset(planform):
    xml = _strake_xml(4, planform, 0.5, 0.02, 0.75, position_from_top_m=0.1)
    assert "<freeformfinset>" in xml
    assert "<fincount>4</fincount>" in xml
    assert "<finpoints>" in xml


@pytest.mark.parametrize("count", [1, 2, 5, 6])
def test_strake_rejects_non_3_or_4_fold_counts(count):
    with pytest.raises(ValueError, match="3-fold or 4-fold"):
        _strake_xml(count, "tapered", 0.5, 0.02, 0.75)


def test_strake_rejects_unknown_planform():
    with pytest.raises(ValueError, match="unknown strake planform"):
        _strake_xml(4, "not_a_real_planform", 0.5, 0.02, 0.75)


def test_strake_rejects_below_minimum_thickness():
    with pytest.raises(ValueError, match="thickness"):
        _strake_xml(4, "tapered", 0.5, 0.02, 0.75, thickness=0.0001)


def test_strake_rejects_unapproved_material():
    with pytest.raises(ValueError, match="approved legal fin material"):
        _strake_xml(4, "tapered", 0.5, 0.02, 0.75, material_key="unobtainium")


def test_strake_rejects_axial_overflow_past_body_tube():
    with pytest.raises(ValueError, match="does not fit inside"):
        _strake_xml(4, "tapered", 0.9, 0.02, body_length_m=0.75)


def test_strake_rejects_axial_overflow_with_nonzero_start():
    with pytest.raises(ValueError, match="does not fit inside"):
        _strake_xml(4, "tapered", 0.5, 0.02, body_length_m=0.75, position_from_top_m=0.4)


def test_strake_rejects_nonpositive_length_or_span():
    with pytest.raises(ValueError, match="positive length and span"):
        _strake_xml(4, "tapered", 0.0, 0.02, 0.75)
    with pytest.raises(ValueError, match="positive length and span"):
        _strake_xml(4, "tapered", 0.5, 0.0, 0.75)


def _base_falcon_params():
    from scripts.flip_diagnosis import E8_8
    return dict(E8_8)


def test_generate_ork_strake_only_booster_variant():
    """Family C 'strake-only' variant: zero conventional aft fins."""
    p = _base_falcon_params()
    p["s1_fin_count"] = 0
    p["s1_strake_count"] = 4
    p["s1_strake_planform"] = "clipped_delta"
    p["s1_strake_length_m"] = 0.55
    p["s1_strake_span_m"] = 0.02
    p["s1_strake_position_m"] = 0.1
    xml = generate_ork(p)
    assert "Booster Strakes" in xml
    assert "<fincount>0</fincount>" not in xml.split("Booster Fins")[1][:200] if "Booster Fins" in xml else True


def test_generate_ork_strake_plus_aft_fin_hybrid():
    """Family C 'hybrid' variant: strakes plus small conventional aft fins."""
    p = _base_falcon_params()
    p["s1_strake_count"] = 3
    p["s1_strake_planform"] = "triangular"
    p["s1_strake_length_m"] = 0.5
    p["s1_strake_span_m"] = 0.015
    xml = generate_ork(p)
    assert "Booster Strakes" in xml
    assert "Booster Fins" in xml


def test_generate_ork_default_params_have_no_strakes():
    """Strakes must be strictly opt-in; existing candidates are unaffected."""
    p = _base_falcon_params()
    xml = generate_ork(p)
    assert "Strakes" not in xml
