import math

import pytest

from physical_geometry import (
    AxialCylinder,
    CenteringRingSpec,
    cylinders_overlap,
    falcon_ballast_rods,
    falcon_cluster_cylinders,
    total_geometric_mass,
    validate_attachment_paths,
    validate_centering_ring_pair,
    validate_cylinders,
)


def _motors():
    return falcon_cluster_cylinders(
        body_length_m=0.75,
        main_mount_length_m=0.604,
        main_mount_radius_m=0.02025,
        retro_mount_length_m=0.650,
        retro_mount_radius_m=0.02825,
        center_distance_m=0.0485,
    )


def test_legacy_full_disk_through_cluster_is_rejected():
    disk = AxialCylinder(
        name="legacy disk",
        role="ballast",
        axial_start_m=0.58,
        length_m=0.021780751,
        radius_m=0.071,
        density_kg_m3=7900.0,
        declared_mass_kg=2.725,
    )

    violations = validate_cylinders(0.75, 0.072, _motors() + (disk,))

    assert sum("physical collision" in item for item in violations) == 4


def test_visible_steel_rods_preserve_mass_centroid_and_clear_every_motor():
    old_centroid = 0.58 + 0.021780751 / 2.0
    rods = falcon_ballast_rods(
        name="S1 Aft Ballast",
        total_mass_kg=2.725,
        axial_centroid_m=old_centroid,
        body_length_m=0.75,
        body_inner_radius_m=0.072,
        obstacles=_motors(),
    )

    assert total_geometric_mass(rods) == pytest.approx(2.725)
    assert all(rod.axial_centroid_m == pytest.approx(old_centroid) for rod in rods)
    assert all(rod.radius_m == pytest.approx(0.020) for rod in rods)
    assert all(rod.radial_position_m == pytest.approx(0.04825) for rod in rods)
    assert validate_cylinders(0.75, 0.072, _motors() + rods) == []
    assert not any(
        cylinders_overlap(motor, rod, clearance_m=0.0)
        for motor in _motors()
        for rod in rods
    )


def test_airframe_bonded_rods_are_tangent_to_body_and_collision_free():
    rods = falcon_ballast_rods(
        name="S1 Aft Ballast",
        total_mass_kg=2.725,
        axial_centroid_m=0.5908903755,
        body_length_m=0.75,
        body_inner_radius_m=0.072,
        obstacles=_motors(),
        rod_radius_m=0.014,
        attachment="airframe_bonded",
    )

    assert all(rod.radial_position_m + rod.radius_m == pytest.approx(0.072) for rod in rods)
    assert validate_cylinders(0.75, 0.072, _motors() + rods) == []


def test_mass_volume_mismatch_and_outside_bore_fail_closed():
    invalid = AxialCylinder(
        name="bad ballast",
        role="ballast",
        axial_start_m=0.1,
        length_m=0.1,
        radius_m=0.02,
        radial_position_m=0.06,
        density_kg_m3=7900.0,
        declared_mass_kg=99.0,
    )

    violations = validate_cylinders(0.75, 0.072, (invalid,))

    assert any("radial envelope" in item for item in violations)
    assert any("mass mismatch" in item for item in violations)


def test_tangent_cylinders_are_not_reported_as_intersecting():
    first = AxialCylinder("a", "motor", 0.0, 0.1, 0.01)
    second = AxialCylinder(
        "b",
        "ballast",
        0.0,
        0.1,
        0.01,
        radial_position_m=0.021,
    )

    assert math.isclose(first.radius_m + second.radius_m + 0.001, 0.021)
    assert not cylinders_overlap(first, second)


def test_continuous_contact_cage_and_ballast_have_airframe_load_paths():
    body_inner = 0.072
    main_radius = 0.02025
    center_distance = body_inner - main_radius
    sleeve_radius = center_distance - main_radius
    motors = falcon_cluster_cylinders(
        body_length_m=0.75,
        main_mount_length_m=0.604,
        main_mount_radius_m=main_radius,
        retro_mount_length_m=0.430,
        retro_mount_radius_m=sleeve_radius,
        center_distance_m=center_distance,
    )
    rods = falcon_ballast_rods(
        name="supported ballast",
        total_mass_kg=2.725,
        axial_centroid_m=0.59,
        body_length_m=0.75,
        body_inner_radius_m=body_inner,
        obstacles=motors,
        rod_radius_m=0.014,
    )

    assert validate_cylinders(0.75, body_inner, motors + rods) == []
    assert validate_attachment_paths(body_inner, motors + rods) == []


def test_floating_motor_cage_fails_attachment_gate():
    assert validate_attachment_paths(0.072, _motors())


def test_centering_ring_pair_spans_support_envelope_to_airframe():
    rings = (
        CenteringRingSpec("forward", 0.0, 0.005, 0.080, 0.02825),
        CenteringRingSpec("aft", 0.695, 0.005, 0.080, 0.02825),
    )

    assert validate_centering_ring_pair(
        0.700,
        0.080,
        0.02825,
        rings,
        (0.0, 0.695),
    ) == []


def test_degenerate_or_local_motor_rings_fail_structural_gate():
    rings = (
        CenteringRingSpec("degenerate", 0.0, 0.005, 0.003, 0.0),
        CenteringRingSpec("local", 0.695, 0.005, 0.03125, 0.02825),
    )

    violations = validate_centering_ring_pair(
        0.700,
        0.080,
        0.02825,
        rings,
        (0.0, 0.695),
    )

    assert any("does not reach body bore" in item for item in violations)
    assert any("inner radius" in item for item in violations)
