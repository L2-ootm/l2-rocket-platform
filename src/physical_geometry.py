"""Fail-closed physical geometry checks for generated OpenRocket designs.

OpenRocket is a flight simulator, not a CAD collision checker.  This module
models the internal parts that matter for manufacturability as finite axial
cylinders and rejects intersecting or uncontained solids before an ``.ork``
file is allowed to reach the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence


MIN_DIMENSION_M = 0.001
ASSEMBLY_CLEARANCE_M = 0.001
GEOMETRY_TOLERANCE_M = 1.0e-9
MASS_REL_TOLERANCE = 1.0e-6


@dataclass(frozen=True, slots=True)
class AxialCylinder:
    """A real cylindrical solid or reserved cylindrical passage.

    ``axial_start_m`` is measured from the top face of the containing stage or
    component.  ``radial_direction_deg`` follows OpenRocket's persisted
    convention: zero lies on +Y and positive angles rotate toward +Z.
    """

    name: str
    role: str
    axial_start_m: float
    length_m: float
    radius_m: float
    radial_position_m: float = 0.0
    radial_direction_deg: float = 0.0
    density_kg_m3: float | None = None
    declared_mass_kg: float | None = None

    @property
    def axial_end_m(self) -> float:
        return self.axial_start_m + self.length_m

    @property
    def axial_centroid_m(self) -> float:
        return self.axial_start_m + self.length_m / 2.0

    @property
    def radial_center(self) -> tuple[float, float]:
        angle = math.radians(self.radial_direction_deg)
        return (
            self.radial_position_m * math.cos(angle),
            self.radial_position_m * math.sin(angle),
        )

    @property
    def geometric_mass_kg(self) -> float | None:
        if self.density_kg_m3 is None:
            return None
        return self.density_kg_m3 * math.pi * self.radius_m**2 * self.length_m


@dataclass(frozen=True, slots=True)
class CenteringRingSpec:
    """A centered annular support spanning a motor envelope to the airframe."""

    name: str
    axial_start_m: float
    length_m: float
    outer_radius_m: float
    inner_radius_m: float
    radial_position_m: float = 0.0

    @property
    def axial_end_m(self) -> float:
        return self.axial_start_m + self.length_m


def _finite_positive(value: float, label: str, minimum: float = MIN_DIMENSION_M) -> str | None:
    if not math.isfinite(value):
        return f"{label} is not finite"
    if value < minimum - GEOMETRY_TOLERANCE_M:
        return f"{label}={value:.9f} m is below {minimum:.3f} m"
    return None


def cylinders_overlap(
    first: AxialCylinder,
    second: AxialCylinder,
    clearance_m: float = ASSEMBLY_CLEARANCE_M,
) -> bool:
    """Return whether two finite cylinders violate the requested clearance."""

    axial_overlap = (
        max(first.axial_start_m, second.axial_start_m)
        < min(first.axial_end_m, second.axial_end_m) - GEOMETRY_TOLERANCE_M
    )
    if not axial_overlap:
        return False

    ay, az = first.radial_center
    by, bz = second.radial_center
    center_distance = math.hypot(ay - by, az - bz)
    required = first.radius_m + second.radius_m + clearance_m
    return center_distance < required - GEOMETRY_TOLERANCE_M


def cylinders_touch(
    first: AxialCylinder,
    second: AxialCylinder,
    tolerance_m: float = 1.0e-7,
) -> bool:
    """Return whether two non-overlapping axial cylinders are tangent."""

    axial_overlap = (
        max(first.axial_start_m, second.axial_start_m)
        < min(first.axial_end_m, second.axial_end_m) - GEOMETRY_TOLERANCE_M
    )
    if not axial_overlap:
        return False
    ay, az = first.radial_center
    by, bz = second.radial_center
    center_distance = math.hypot(ay - by, az - bz)
    return math.isclose(
        center_distance,
        first.radius_m + second.radius_m,
        rel_tol=0.0,
        abs_tol=tolerance_m,
    )


def validate_attachment_paths(
    body_inner_radius_m: float,
    cylinders: Sequence[AxialCylinder],
    tolerance_m: float = 1.0e-7,
    support_ring_inner_radius_m: float | None = None,
) -> list[str]:
    """Require every internal solid to have a rigid path to the airframe.

    A centered cage ring attaches cylinders whose outer radial envelope meets
    its inner bore. Contact then propagates through tangent cage members.
    """

    attached = {
        index
        for index, cylinder in enumerate(cylinders)
        if (
            math.isclose(
                cylinder.radial_position_m + cylinder.radius_m,
                body_inner_radius_m,
                rel_tol=0.0,
                abs_tol=tolerance_m,
            )
            or (
                support_ring_inner_radius_m is not None
                and math.isclose(
                    cylinder.radial_position_m + cylinder.radius_m,
                    support_ring_inner_radius_m,
                    rel_tol=0.0,
                    abs_tol=tolerance_m,
                )
            )
        )
    }
    changed = True
    while changed:
        changed = False
        for index, cylinder in enumerate(cylinders):
            if index in attached:
                continue
            if any(
                cylinders_touch(cylinder, cylinders[other], tolerance_m)
                for other in attached
            ):
                attached.add(index)
                changed = True

    return [
        f"{cylinder.name} has no rigid tangent-contact path to the airframe"
        for index, cylinder in enumerate(cylinders)
        if index not in attached
    ]


def validate_centering_ring_pair(
    body_length_m: float,
    body_inner_radius_m: float,
    support_envelope_radius_m: float,
    rings: Sequence[CenteringRingSpec],
    expected_axial_starts_m: Sequence[float],
    tolerance_m: float = GEOMETRY_TOLERANCE_M,
) -> list[str]:
    """Validate the two annuli that transfer a motor cage into the airframe.

    Tangency between cylindrical solids is not enough to represent a retained
    motor assembly.  The Falcon topology requires one centered ring at each
    end of the supported mount envelope.  Explicit radii are intentional:
    OpenRocket's automatic centering-ring inner radius ignores clustered
    tubes' radial offsets.
    """

    violations: list[str] = []
    if len(rings) != 2:
        violations.append(f"expected exactly 2 centering rings, got {len(rings)}")
        return violations
    if len(expected_axial_starts_m) != 2:
        raise ValueError("expected_axial_starts_m must contain exactly two stations")

    for ring, expected_start in zip(
        sorted(rings, key=lambda item: item.axial_start_m),
        sorted(float(item) for item in expected_axial_starts_m),
    ):
        for value, label in (
            (ring.length_m, f"{ring.name} length"),
            (ring.outer_radius_m, f"{ring.name} outer radius"),
            (ring.inner_radius_m, f"{ring.name} inner radius"),
        ):
            violation = _finite_positive(value, label)
            if violation:
                violations.append(violation)

        if not math.isclose(
            ring.radial_position_m,
            0.0,
            rel_tol=0.0,
            abs_tol=tolerance_m,
        ):
            violations.append(f"{ring.name} must be centered on the airframe axis")
        if ring.axial_start_m < -tolerance_m or (
            ring.axial_end_m > body_length_m + tolerance_m
        ):
            violations.append(
                f"{ring.name} axial envelope "
                f"[{ring.axial_start_m:.6f}, {ring.axial_end_m:.6f}] m "
                f"is outside body [0, {body_length_m:.6f}] m"
            )
        if not math.isclose(
            ring.axial_start_m,
            expected_start,
            rel_tol=0.0,
            abs_tol=tolerance_m,
        ):
            violations.append(
                f"{ring.name} starts at {ring.axial_start_m:.6f} m, "
                f"expected support station {expected_start:.6f} m"
            )
        if not math.isclose(
            ring.outer_radius_m,
            body_inner_radius_m,
            rel_tol=0.0,
            abs_tol=tolerance_m,
        ):
            violations.append(
                f"{ring.name} outer radius {ring.outer_radius_m:.6f} m does not "
                f"reach body bore {body_inner_radius_m:.6f} m"
            )
        if not math.isclose(
            ring.inner_radius_m,
            support_envelope_radius_m,
            rel_tol=0.0,
            abs_tol=tolerance_m,
        ):
            violations.append(
                f"{ring.name} inner radius {ring.inner_radius_m:.6f} m does not "
                f"match support envelope {support_envelope_radius_m:.6f} m"
            )
        annular_width = ring.outer_radius_m - ring.inner_radius_m
        violation = _finite_positive(annular_width, f"{ring.name} annular width")
        if violation:
            violations.append(violation)

    return violations


def validate_cylinders(
    body_length_m: float,
    body_inner_radius_m: float,
    cylinders: Sequence[AxialCylinder],
    clearance_m: float = ASSEMBLY_CLEARANCE_M,
) -> list[str]:
    """Validate dimensions, containment, mass truth and pairwise collisions."""

    violations: list[str] = []
    for cylinder in cylinders:
        for value, label in (
            (cylinder.length_m, f"{cylinder.name} length"),
            (cylinder.radius_m, f"{cylinder.name} radius"),
        ):
            violation = _finite_positive(value, label)
            if violation:
                violations.append(violation)

        if cylinder.axial_start_m < -GEOMETRY_TOLERANCE_M or (
            cylinder.axial_end_m > body_length_m + GEOMETRY_TOLERANCE_M
        ):
            violations.append(
                f"{cylinder.name} axial envelope "
                f"[{cylinder.axial_start_m:.6f}, {cylinder.axial_end_m:.6f}] m "
                f"is outside body [0, {body_length_m:.6f}] m"
            )

        # Motor mounts and ballast longerons may be deliberately bonded
        # tangent to the airframe. Other parts retain installation clearance.
        body_clearance = (
            0.0
            if cylinder.role in {"ballast", "motor_mount"}
            else clearance_m
        )
        occupied_radius = cylinder.radial_position_m + cylinder.radius_m + body_clearance
        if occupied_radius > body_inner_radius_m + GEOMETRY_TOLERANCE_M:
            violations.append(
                f"{cylinder.name} radial envelope {occupied_radius:.6f} m exceeds "
                f"body bore {body_inner_radius_m:.6f} m"
            )

        geometric_mass = cylinder.geometric_mass_kg
        if cylinder.declared_mass_kg is not None:
            if geometric_mass is None:
                violations.append(f"{cylinder.name} declares mass without material density")
            elif not math.isclose(
                geometric_mass,
                cylinder.declared_mass_kg,
                rel_tol=MASS_REL_TOLERANCE,
                abs_tol=1.0e-9,
            ):
                violations.append(
                    f"{cylinder.name} mass mismatch: declared "
                    f"{cylinder.declared_mass_kg:.9f} kg, geometry "
                    f"{geometric_mass:.9f} kg"
                )

    # The Falcon motor cage intentionally uses bonded tangent contacts:
    # the three ascent mounts touch the central retro mount, and ballast rods
    # are bonded to that same central tube.  Those interfaces require zero
    # clearance but still reject any actual interpenetration.  Every other
    # pair retains the normal assembly clearance.
    bonded_contact_pairs = {
        frozenset(("motor_mount", "motor_mount")),
        frozenset(("motor_mount", "ballast")),
    }
    for index, first in enumerate(cylinders):
        for second in cylinders[index + 1 :]:
            pair_clearance = (
                0.0
                if frozenset((first.role, second.role)) in bonded_contact_pairs
                else clearance_m
            )
            if cylinders_overlap(first, second, pair_clearance):
                violations.append(
                    f"physical collision: {first.name} ({first.role}) intersects "
                    f"{second.name} ({second.role})"
                )

    return violations


def falcon_cluster_cylinders(
    *,
    body_length_m: float,
    main_mount_length_m: float,
    main_mount_radius_m: float,
    retro_mount_length_m: float,
    retro_mount_radius_m: float,
    center_distance_m: float,
) -> tuple[AxialCylinder, ...]:
    """Expand OpenRocket's zero-rotation ``3-ring`` plus center topology."""

    # OpenRocket ClusterConfiguration.java defines the unrotated 3-ring at
    # -150, -30 and +90 degrees after scaling to ``center_distance_m``.
    outer_angles = (-150.0, -30.0, 90.0)
    main_start = body_length_m - main_mount_length_m
    retro_start = body_length_m - retro_mount_length_m
    mains = tuple(
        AxialCylinder(
            name=f"Main motor mount {index}",
            role="motor_mount",
            axial_start_m=main_start,
            length_m=main_mount_length_m,
            radius_m=main_mount_radius_m,
            radial_position_m=center_distance_m,
            radial_direction_deg=angle,
        )
        for index, angle in enumerate(outer_angles, start=1)
    )
    retro = AxialCylinder(
        name="Central retro motor mount",
        role="motor_mount",
        axial_start_m=retro_start,
        length_m=retro_mount_length_m,
        radius_m=retro_mount_radius_m,
    )
    return mains + (retro,)


def falcon_ballast_rods(
    *,
    name: str,
    total_mass_kg: float,
    axial_centroid_m: float,
    body_length_m: float,
    body_inner_radius_m: float,
    obstacles: Sequence[AxialCylinder],
    density_kg_m3: float = 7900.0,
    radial_position_m: float | None = None,
    rod_radius_m: float | None = None,
    attachment: str = "central_bonded",
    clearance_m: float = ASSEMBLY_CLEARANCE_M,
) -> tuple[AxialCylinder, ...]:
    """Compile three solid steel rods into the gaps of a Falcon 3+1 cluster.

    The rods map to three separate non-motor OpenRocket ``InnerTube``
    components with solid wall thickness.  They must not use OpenRocket's
    cluster shorthand because that collapses radial inertia at the averaged
    component CG even though the renderer shows multiple instances.
    """

    if total_mass_kg <= 0 or density_kg_m3 <= 0:
        raise ValueError("ballast mass and density must be positive")
    count = 3
    directions = (-90.0, 30.0, 150.0)
    central = [item for item in obstacles if item.radial_position_m <= GEOMETRY_TOLERANCE_M]
    if not central:
        raise ValueError("Falcon ballast layout requires a central motor envelope")
    central_radius = max(item.radius_m for item in central)
    if rod_radius_m is None:
        rod_radius_m = 0.020
    if rod_radius_m < MIN_DIMENSION_M:
        raise ValueError("no manufacturable radial gap exists for ballast rods")
    if radial_position_m is None:
        if attachment == "central_bonded":
            radial_position_m = central_radius + rod_radius_m
        elif attachment == "airframe_bonded":
            radial_position_m = body_inner_radius_m - rod_radius_m
        else:
            raise ValueError(
                "ballast attachment must be central_bonded or airframe_bonded"
            )
    length_m = total_mass_kg / (
        density_kg_m3 * count * math.pi * rod_radius_m**2
    )
    axial_start_m = axial_centroid_m - length_m / 2.0
    mass_per_rod = total_mass_kg / count
    rods = tuple(
        AxialCylinder(
            name=f"{name} rod {index}",
            role="ballast",
            axial_start_m=axial_start_m,
            length_m=length_m,
            radius_m=rod_radius_m,
            radial_position_m=radial_position_m,
            radial_direction_deg=direction,
            density_kg_m3=density_kg_m3,
            declared_mass_kg=mass_per_rod,
        )
        for index, direction in enumerate(directions, start=1)
    )
    violations = validate_cylinders(
        body_length_m,
        body_inner_radius_m,
        tuple(obstacles) + rods,
        clearance_m,
    )
    if violations:
        raise ValueError("; ".join(violations))
    return rods


def total_geometric_mass(cylinders: Iterable[AxialCylinder]) -> float:
    """Return mass of all material-bearing cylinders."""

    return sum(c.geometric_mass_kg or 0.0 for c in cylinders)
