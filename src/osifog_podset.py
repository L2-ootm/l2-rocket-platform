#!/usr/bin/env python3
"""OSIFOG Level 3 -- PodSet-based external 3+1 vehicle compiler (new architecture).

Fixed contract per stage (Sustainer, Booster):
  - 1 core body tube
  - exactly 3 identical side pods at 120 deg (ascent motor each), via native
    OpenRocket PodSet -- NOT ParallelStage. PodSet has no separation
    semantics (verified directly against the OpenRocket 24.12 jar bundled in
    this repo: no separation-related methods on PodSet.class, and its XML
    schema has no <separationevent>/<separationdelay> tags), so the pods
    stay attached through the whole flight and do not create extra flight
    branches -- exactly two branches (Sustainer, Booster) as required.
  - 1 central retro motor, independent ignition, on the core axis
  - 2 internal centering rings connecting the central retro motor tube to
    the core body tube (real material/density, real structural path --
    these ARE invoked, unlike osifog_sweep.py's dead _centering_ring_xml)

Everything else (motor choice, dimensions, materials, pod nose/fin geometry,
core fins, ring density/position/thickness, ballast, ignition timing) is
free per the mission's evolutionary genome.

Reuses osifog_sweep.py's motor database, materials, fin builder, wind/
atmosphere/anti-tumble templates, and OpenRocket JVM bootstrap/runner so the
official environment (launch site, wind, seed, anti-tumble extension) is
identical to the existing pipeline. Deliberately a SEPARATE module from
osifog_sweep.py's generate_ork(): this is a different, still-experimental
vehicle topology, and keeping it out of the heavily-tested legacy 3+1-via-
native-cluster code path avoids any risk of regressing that pipeline.

Known simplification (not yet implemented): explicit pylon/strut geometry
bridging each pod to the core. PodSet itself already guarantees rigid
attachment in OpenRocket's simulation model (no mass/CG override needed --
this is not the same gap osifog_sweep.py's dead centering-ring code left),
so this is a visual/mass-realism refinement, not a physics correctness gap.
"""
import math
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from osifog_sweep import (
    MOTOR_DATABASE, MATERIALS, MOTOR_TUBE_WALL_M, MOTOR_INSERTION_CLEARANCE_M,
    MIN_DIMENSION_M, LAUNCH_LAT, LAUNCH_LON, LAUNCH_ALT, LAUNCH_ROD_M,
    TEMP_K, PRESSURE_PA, HUMIDITY, SIM_SEED, ANTI_TUMBLE_SCRIPT,
    _component_id, _fin_xml, _motor_mount_xml, _centering_ring_xml,
    _haack_radius, init_or, run_sim, save_simulated_ork,
)

NOSE_SHAPES = ("ogive", "conical", "ellipsoid", "parabolic", "haack", "power")

# ---------------------------------------------------------------------------
# Full local motor catalog (OpenRocket 24.12's own bundled DB, 1458 motors)
# instead of the 38-motor curated MOTOR_DATABASE (which only covers motors
# someone previously ran scripts/extract_motors.py for, for the Rust proxy's
# .eng files -- OpenRocket itself needs no such extraction, it already has
# the full catalog loaded natively). Motor selection in this module accepts
# EITHER an int index into MOTOR_DATABASE OR a string designation resolved
# live from this DB, so the search isn't restricted to the curated 38.
# ---------------------------------------------------------------------------
_MOTOR_DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "openrocket/core/src/main/resources/datafiles/thrustcurves/initial_motors.db"
)
_motor_db_cache = {}
_live_motor_index = None  # lazily built: designation -> [live ThrustCurveMotor, ...]


def _build_live_motor_index():
    """Index OpenRocket's OWN in-memory motor database (1088 loaded motor
    sets at last check), NOT the raw SQLite catalog file (1458 rows). These
    differ: a real, reproduced bug came from resolving a motor
    (designation="F50T") that exists as a ROW in the SQLite file under a
    second manufacturer ("Public Missiles, Ltd.") but is NOT actually loaded
    into OpenRocket's runtime database at all. Serializing that row as a
    <motor> XML block then produces an "empty MotorInstance" at load time --
    OpenRocket silently drops the reference, leaving the mount visibly empty
    in the rendered rocket (exactly the "no motor inside the main tubes"
    defect). Resolving from the live database instead guarantees whatever we
    write can actually be found again."""
    global _live_motor_index
    if _live_motor_index is not None:
        return _live_motor_index
    app = init_or()
    index = {}
    for motor_set in app.getMotorSetDatabase().getMotorSets():
        for m in motor_set.getMotors():
            index.setdefault(str(m.getDesignation()), []).append(m)
    _live_motor_index = index
    return index


def resolve_motor(motor_ref):
    """MOTOR_DATABASE-compatible tuple (mfr, designation, diam_m, length_m,
    delay, digest) for either an int index (curated list, untouched) or a
    string designation resolved from OpenRocket's own live motor database.
    The digest is computed via OpenRocket's own
    `info.openrocket.core.motor.MotorDigest.digestMotor()` -- not left
    blank -- which is what lets the file loader deterministically re-find
    the exact right thrust curve on reopen."""
    if isinstance(motor_ref, int):
        return MOTOR_DATABASE[motor_ref]
    if motor_ref in _motor_db_cache:
        return _motor_db_cache[motor_ref]
    import jpype
    MotorDigest = jpype.JClass("info.openrocket.core.motor.MotorDigest")
    candidates = _build_live_motor_index().get(motor_ref)
    if not candidates:
        raise ValueError(
            f"motor designation not found in OpenRocket's own LIVE motor "
            f"database: {motor_ref!r} (may exist in the raw catalog file "
            f"without being loaded at runtime, or is misspelled)"
        )
    # Multiple thrust-curve/delay-grain variants can share one designation;
    # pick the highest-total-impulse variant deterministically.
    best = max(candidates, key=lambda m: float(m.getTotalImpulseEstimate()))
    mfr = str(best.getManufacturer())
    diam_m = float(best.getDiameter())
    length_m = float(best.getLength())
    delays = [float(d) for d in best.getStandardDelays()]
    delay = max(delays) if delays else 0.0
    digest = str(MotorDigest.digestMotor(best))
    tup = (mfr, motor_ref, diam_m, length_m, delay, digest)
    _motor_db_cache[motor_ref] = tup
    _motor_db_cache[motor_ref + "__stats"] = {
        "total_impulse_ns": float(best.getTotalImpulseEstimate()),
        "total_kg": float(best.getLaunchMass()),
        "propellant_kg": float(best.getLaunchMass() - best.getBurnoutMass()),
        "burn_time_s": float(best.getBurnTimeEstimate()),
    }
    return tup


def motor_stats(motor_ref):
    """(total_impulse_ns, total_kg, propellant_kg, burn_time_s) for the
    analytic pre-filter -- works for both curated and DB-resolved motors,
    always sourced from the live database for consistency."""
    if isinstance(motor_ref, int):
        designation = MOTOR_DATABASE[motor_ref][1]
        resolve_motor(designation)
        stats = _motor_db_cache[designation + "__stats"]
    else:
        resolve_motor(motor_ref)
        stats = _motor_db_cache[motor_ref + "__stats"]
    return (stats["total_impulse_ns"], stats["total_kg"],
            stats["propellant_kg"], stats["burn_time_s"])

_MATERIAL_DENSITY_KG_M3 = {
    "cardboard": 680.0, "kraft": 680.0, "fiberglass": 1800.0, "balsa": 170.0,
    "legal_balsa": 170.0, "aluminum": 2700.0, "lead": 11340.0, "steel": 7900.0,
}


def _pod_motor_mount_xml(motor_ref, config_id, ignition_event, ignition_delay,
                         position_bottom=0.0, cluster="single", cluster_scale=1.0,
                         radius_m=0.0, component_name=None,
                         wall_thickness_m=0.001, motor_clearance_m=0.0,
                         mount_length_m=None):
    """Same XML shape as osifog_sweep.py's _motor_mount_xml, but resolves
    motor_ref (int index OR string designation) via resolve_motor() instead
    of indexing the curated 38-motor MOTOR_DATABASE directly -- this is what
    actually unlocks the full 1458-motor local catalog for pod/retro motor
    selection. Kept as a local duplicate rather than modifying the shared,
    tested osifog_sweep.py function."""
    mfr, designation, diam, length, delay, digest = resolve_motor(motor_ref)
    mount_or = diam / 2.0 + wall_thickness_m + motor_clearance_m
    mount_length = float(mount_length_m) if mount_length_m is not None else length + 0.02
    if mount_length + 1.0e-9 < length + 0.02:
        raise ValueError(f"{component_name or designation} mount is shorter than its motor")
    # OSIFOG prohibits recovery devices/ejection events.  Every motor in this
    # mission-specific compiler is therefore serialized as plugged regardless
    # of the delay variants available in OpenRocket's source database.
    delay_str = "none"
    digest_xml = f"<digest>{digest}</digest>" if digest else ""
    cluster_xml = f"""<clusterconfiguration>{cluster}</clusterconfiguration>
             <clusterscale>{cluster_scale:.6f}</clusterscale>
             <clusterrotation>0.0</clusterrotation>"""
    component_name = component_name or f"Motor Mount ({designation})"
    return f'''
          <innertube>
            <name>{component_name}</name>
            <id>{_component_id(component_name)}</id>
            <position type="bottom">{position_bottom:.6f}</position>
            {MATERIALS["kraft"]}
            <length>{mount_length:.6f}</length>
            <radialposition>{radius_m:.6f}</radialposition>
            <radialdirection>0.0</radialdirection>
            <outerradius>{mount_or:.6f}</outerradius>
            <thickness>{wall_thickness_m:.6f}</thickness>
            {cluster_xml}
            <motormount>
              <ignitionevent>{ignition_event}</ignitionevent>
              <ignitiondelay>{ignition_delay:.6f}</ignitiondelay>
              <overhang>0.005</overhang>
              <motor configid="{config_id}">
                <manufacturer>{mfr}</manufacturer>
                {digest_xml}
                <designation>{designation}</designation>
                <diameter>{diam}</diameter>
                <length>{length}</length>
                <delay>{delay_str}</delay>
              </motor>
            </motormount>
          </innertube>'''


def _solid_ballast_xml(name, mass_kg, position_top_m, max_radius_m,
                       material_key="steel", available_length_m=None,
                       radial_position_m=0.0, radial_direction_rad=0.0):
    """A real, mass/volume-consistent solid cylinder ballast bulkhead
    (radius = max_radius_m, length derived from mass/density) -- never a
    mass override, per the mission's density-as-ballast requirement.

    available_length_m (if given) hard-gates the computed length against
    the real cavity it has to fit in: a real candidate was found rendering
    with its nose ballast extending 13.7 cm PAST the end of the nose cone
    and into the core tube (2.5 kg of steel does not fit in a narrow nose
    at a plausible radius without an absurdly long rod) -- nothing checked
    this before, so it silently overflowed instead of being rejected."""
    density = _MATERIAL_DENSITY_KG_M3[material_key]
    volume_m3 = mass_kg / density
    length_m = volume_m3 / (math.pi * max_radius_m ** 2)
    if length_m < MIN_DIMENSION_M:
        length_m = MIN_DIMENSION_M
    if available_length_m is not None and length_m > available_length_m + 1.0e-9:
        raise ValueError(
            f"{name}: {mass_kg:.3f} kg of {material_key} at radius "
            f"{max_radius_m:.4f} m needs a {length_m:.4f} m rod, which "
            f"does not fit in the {available_length_m:.4f} m available -- "
            f"use a denser material, less mass, or a wider/repositioned cavity"
        )
    return f'''
            <bulkhead>
              <name>{name}</name>
              <id>{_component_id(name)}</id>
              <position type="top">{position_top_m:.9f}</position>
              {MATERIALS[material_key]}
              <length>{length_m:.9f}</length>
              <radialposition>{radial_position_m:.9f}</radialposition>
              <radialdirection>{radial_direction_rad:.9f}</radialdirection>
              <outerradius>{max_radius_m:.9f}</outerradius>
            </bulkhead>'''


def ballast_rod_layout(retro_ref, core_radius_m, core_length_m, mass_kg,
                       position_top_m, rod_radius_m, attachment,
                       material_key="steel", count=3):
    """Return a collision-checked symmetric ballast-rod layout.

    Rods occupy the annulus outside the full-length central retro tube.  They
    are real density/volume components; mass is never overridden.  Symmetric
    placement keeps lateral CG centered while still contributing radial
    inertia.
    """
    if mass_kg <= 0.0:
        return None
    if count < 3:
        raise ValueError("ballast rod layout requires at least three symmetric rods")
    if rod_radius_m < MIN_DIMENSION_M / 2.0:
        raise ValueError("ballast rod diameter is below the mission minimum dimension")
    ring_geo = _retro_ring_geometry(retro_ref, core_radius_m, 0.004)
    retro_outer = ring_geo["retro_tube_outer_radius_m"]
    core_inner = ring_geo["ring_outer_radius_m"]
    if attachment == "central_bonded":
        center_radius = retro_outer + rod_radius_m
        outer_clearance = core_inner - (center_radius + rod_radius_m)
        if outer_clearance < MIN_DIMENSION_M - 1.0e-9:
            raise ValueError(
                "central-bonded ballast rods do not clear the core wall by 1mm"
            )
    elif attachment == "airframe_bonded":
        center_radius = core_inner - rod_radius_m
        inner_clearance = (center_radius - rod_radius_m) - retro_outer
        if inner_clearance < MIN_DIMENSION_M - 1.0e-9:
            raise ValueError(
                "airframe-bonded ballast rods do not clear the central retro tube by 1mm"
            )
    else:
        raise ValueError(f"unsupported ballast attachment {attachment!r}")
    chord = 2.0 * center_radius * math.sin(math.pi / count)
    if chord < 2.0 * rod_radius_m + MIN_DIMENSION_M - 1.0e-9:
        raise ValueError("adjacent ballast rods overlap or lack 1mm clearance")
    density = _MATERIAL_DENSITY_KG_M3[material_key]
    per_rod_mass_kg = mass_kg / count
    rod_length_m = max(
        MIN_DIMENSION_M,
        per_rod_mass_kg / (density * math.pi * rod_radius_m ** 2),
    )
    if position_top_m < 0.0 or position_top_m + rod_length_m > core_length_m + 1.0e-9:
        raise ValueError(
            f"ballast rods span {position_top_m:.4f}.."
            f"{position_top_m + rod_length_m:.4f}m outside the "
            f"{core_length_m:.4f}m core"
        )
    return {
        "count": count,
        "mass_kg": mass_kg,
        "per_rod_mass_kg": per_rod_mass_kg,
        "rod_radius_m": rod_radius_m,
        "rod_length_m": rod_length_m,
        "center_radius_m": center_radius,
        "position_top_m": position_top_m,
        "axial_center_m": position_top_m + rod_length_m / 2.0,
        "attachment": attachment,
        "material_key": material_key,
    }


def minimum_core_radius_for_ballast(retro_ref, rod_radius_m):
    """Smallest outer core radius for three legal rods and a 2mm core wall."""
    retro_radius = resolve_motor(retro_ref)[2] / 2.0
    retro_outer = retro_radius + MOTOR_TUBE_WALL_M + MOTOR_INSERTION_CLEARANCE_M
    return retro_outer + 2.0 * rod_radius_m + MIN_DIMENSION_M + 0.002


def ballast_rod_length_m(mass_kg, rod_radius_m, material_key="steel", count=3):
    density = _MATERIAL_DENSITY_KG_M3[material_key]
    return max(
        MIN_DIMENSION_M,
        (mass_kg / count) / (density * math.pi * rod_radius_m ** 2),
    )


def _ballast_rods_xml(name, layout):
    blocks = []
    for index in range(layout["count"]):
        angle = math.tau * index / layout["count"]
        blocks.append(
            _solid_ballast_xml(
                f"{name} Rod {index + 1}",
                layout["per_rod_mass_kg"],
                layout["position_top_m"],
                layout["rod_radius_m"],
                material_key=layout["material_key"],
                available_length_m=layout["rod_length_m"],
                radial_position_m=layout["center_radius_m"],
                radial_direction_rad=angle,
            )
        )
    return "\n".join(blocks)


def _pod_radial_layout(core_radius_m, pod_outer_radius_m, pod_radial_offset_m):
    """Validate the pod placement does not intersect the core or itself."""
    min_offset = core_radius_m + pod_outer_radius_m + MIN_DIMENSION_M
    if pod_radial_offset_m < min_offset - 1.0e-9:
        raise ValueError(
            f"pod_radial_offset {pod_radial_offset_m:.4f} m is too small to clear "
            f"the core (min {min_offset:.4f} m for core_radius={core_radius_m:.4f}, "
            f"pod_outer_radius={pod_outer_radius_m:.4f})"
        )
    # 3 pods at 120 deg: adjacent-pod center-to-center chord must clear
    # 2x pod outer radius (pods must not touch/intersect each other).
    chord = 2.0 * pod_radial_offset_m * math.sin(math.radians(60.0))
    min_chord = 2.0 * pod_outer_radius_m + MIN_DIMENSION_M
    if chord < min_chord - 1.0e-9:
        raise ValueError(
            f"pod_radial_offset {pod_radial_offset_m:.4f} m puts adjacent pods "
            f"{chord:.4f} m apart (center-to-center chord), less than the "
            f"{min_chord:.4f} m needed to clear pod_outer_radius={pod_outer_radius_m:.4f}"
        )


def _pylon_blade_xml(name, position_top_m, core_radius_m, pod_radius_m,
                     pod_radial_offset_m, angle_deg,
                     chord_m=0.025, thickness_m=0.003):
    """A real physical pylon bridging one pod to the core at one axial
    station, shaped as a thin rectangular blade (a single-fin
    `freeformfinset`, fincount=1, rotated to point at that pod's own
    angle) instead of a cylindrical rod.

    An earlier version used a small InnerTube spanning the gap radially --
    correct on paper (verified to touch both the core and pod surfaces
    exactly), but rendered as a short FAT cylinder (radius = half the gap,
    e.g. 19mm for a 38mm gap) because a tube's own thickness is tied to its
    radius. A real strut is thin in cross-section and long in span; a
    freeform fin (span = the gap, chord = short, thickness = a few mm,
    independent of each other) is what actually reads as a strut rather
    than a stray floating cylinder. `FreeformFinSet` supports fincount=1
    (verified directly against the class -- no OpenRocket-side minimum),
    and `FinSet.setBaseRotation()`/the `<rotation>` XML tag (degrees, per
    DocumentConfig.java's `FinSet:rotation` setter) aims a single fin at an
    arbitrary angle, unlike the >=3-fin symmetric aerodynamic sets
    `_fin_xml` builds. Attached to the CORE (not the pod) so the angle
    reference is unambiguous and absolute."""
    gap = pod_radial_offset_m - core_radius_m - pod_radius_m
    if gap <= MIN_DIMENSION_M:
        raise ValueError(
            f"{name}: pod-to-core gap {gap:.4f} m is too small for a real "
            f"pylon (need > {MIN_DIMENSION_M:.3f} m)"
        )
    pts = f'<point x="0.0" y="0.0"/><point x="0.0" y="{gap:.6f}"/>' \
          f'<point x="{chord_m:.6f}" y="{gap:.6f}"/><point x="{chord_m:.6f}" y="0.0"/>'
    return f'''
          <freeformfinset>
            <name>{name}</name>
            <id>{_component_id(name)}</id>
            <position type="top">{position_top_m:.9f}</position>
            <rotation>{angle_deg:.6f}</rotation>
            {MATERIALS["aluminum"]}
            <fincount>1</fincount>
            <thickness>{thickness_m:.6f}</thickness>
            <crosssection>square</crosssection>
            <finpoints>{pts}</finpoints>
          </freeformfinset>'''


def pylon_stations_m(core_length_m, pod_length_m, station_count=2,
                     pod_axial_top_m=0.0, pod_nose_length_m=0.0,
                     chord_m=0.025):
    """Evenly spaced stations inside the real core/pod-body overlap."""
    count = max(2, int(station_count))
    body_top = float(pod_axial_top_m) + float(pod_nose_length_m)
    body_bottom = body_top + float(pod_length_m)
    overlap_top = max(0.0, body_top)
    overlap_bottom = min(float(core_length_m), body_bottom)
    margin = max(0.005, float(chord_m) * 0.25)
    start = overlap_top + margin
    end = overlap_bottom - float(chord_m) - margin
    if end <= start:
        raise ValueError(
            "pod body/core overlap is too short for two distinct pylon stations"
        )
    return [start + (end - start) * index / (count - 1) for index in range(count)]


def podset_buildability_violations(p, prefix):
    """Auditable minimum load-path gate for the radial cage.

    This is not a certification calculation.  It prevents the optimizer from
    receiving effectively massless, arbitrarily slender aluminum pylons.
    Detailed loads remain an engineering-review gate for finalists.
    """
    core_radius = float(p[f"{prefix}_core_radius"])
    pod_radius = float(p[f"{prefix}_pod_radius"])
    offset = float(p[f"{prefix}_pod_radial_offset"])
    gap = offset - core_radius - pod_radius
    chord = float(p.get(f"{prefix}_pylon_chord_m", 0.025))
    thickness = float(p.get(f"{prefix}_pylon_thickness_m", 0.003))
    stations = int(p.get(f"{prefix}_pylon_station_count", 2))
    violations = []
    if stations < 2:
        violations.append(f"{prefix}: each pod requires at least two pylon stations")
    if chord <= 0.0 or gap / chord > 12.0 + 1.0e-9:
        violations.append(
            f"{prefix}: pylon span/chord {gap / max(chord, 1e-12):.1f} exceeds 12"
        )
    if thickness <= 0.0 or gap / thickness > 120.0 + 1.0e-9:
        violations.append(
            f"{prefix}: pylon span/thickness {gap / max(thickness, 1e-12):.1f} exceeds 120"
        )
    return violations


def _pylons_xml(name_prefix, core_length_m, core_radius_m, pod_radius_m,
               pod_radial_offset_m, pod_length_m, chord_m=0.025,
               thickness_m=0.003, station_count=2, pod_axial_top_m=0.0,
               pod_nose_length_m=0.0, angle_offset_deg=0.0):
    """Load-transfer blades for all three pods at two or more stations."""
    blocks = []
    for i, angle_deg in enumerate(
        (angle_offset_deg, angle_offset_deg + 120.0, angle_offset_deg + 240.0)
    ):
        for station_index, station in enumerate(
            pylon_stations_m(
                core_length_m, pod_length_m, station_count,
                pod_axial_top_m, pod_nose_length_m, chord_m,
            ), start=1
        ):
            blocks.append(_pylon_blade_xml(
                f"{name_prefix} Station {station_index} (Pod {i + 1})", station,
                core_radius_m, pod_radius_m, pod_radial_offset_m, angle_deg,
                chord_m=chord_m, thickness_m=thickness_m,
            ))
    return "\n".join(blocks)


def _retro_ring_geometry(retro_idx, core_radius_m, ring_thickness_m):
    """Central retro motor tube outer radius and the two rings that connect
    it to the core (inner radius = tube outer radius, outer radius = core
    inner radius, i.e. the ring spans the full annular gap)."""
    retro_tube_outer = (
        resolve_motor(retro_idx)[2] / 2.0
        + MOTOR_TUBE_WALL_M + MOTOR_INSERTION_CLEARANCE_M
    )
    core_inner = core_radius_m - 0.002
    if core_inner <= retro_tube_outer + MIN_DIMENSION_M:
        raise ValueError(
            f"core_radius {core_radius_m:.4f} m leaves no room for a legal "
            f"centering ring around the central retro motor (tube outer "
            f"radius {retro_tube_outer:.4f} m)"
        )
    return {
        "retro_tube_outer_radius_m": retro_tube_outer,
        "ring_inner_radius_m": retro_tube_outer,
        "ring_outer_radius_m": core_inner,
    }


def stage_support_layout(p, prefix):
    """Shared ring/ballast layout consumed by OR and the Rust AST proxy."""
    retro_idx = p[f"{prefix}_retro"]
    core_radius = float(p[f"{prefix}_core_radius"])
    core_length = float(p[f"{prefix}_core_length"])
    ring_thickness = float(p.get(f"{prefix}_ring_thickness_m", 0.004))
    ring_geo = _retro_ring_geometry(retro_idx, core_radius, ring_thickness)
    ballast_layout = None
    ballast_kg = float(
        p.get(f"{prefix}_aft_ballast_kg", p.get(f"{prefix}_ballast_kg", 0.0))
    )
    if ballast_kg > 0.0:
        ballast_layout = ballast_rod_layout(
            retro_idx,
            core_radius,
            core_length,
            ballast_kg,
            float(p.get(
                f"{prefix}_aft_ballast_pos_m",
                p.get(f"{prefix}_ballast_position_m", core_length * 0.2),
            )),
            float(p.get(f"{prefix}_aft_ballast_rod_radius_m", 0.014)),
            p.get(f"{prefix}_aft_ballast_attachment", "central_bonded"),
            material_key=p.get(f"{prefix}_ballast_material", "steel"),
        )
    if ballast_layout is not None:
        default_fwd_ring = max(
            0.01,
            ballast_layout["position_top_m"] - ring_thickness - 0.005,
        )
        default_aft_ring = min(
            core_length - ring_thickness,
            ballast_layout["position_top_m"]
            + ballast_layout["rod_length_m"]
            + 0.005,
        )
    else:
        default_fwd_ring = core_length * 0.15
        default_aft_ring = core_length - 0.02
    ring_fwd_pos = float(p.get(f"{prefix}_ring_fwd_position_m", default_fwd_ring))
    ring_aft_pos = float(p.get(f"{prefix}_ring_aft_position_m", default_aft_ring))
    if ballast_layout is not None:
        ballast_start = ballast_layout["position_top_m"]
        ballast_end = ballast_start + ballast_layout["rod_length_m"]
        for ring_name, ring_pos in (("forward", ring_fwd_pos), ("aft", ring_aft_pos)):
            if ring_pos < ballast_end - 1.0e-9 and ring_pos + ring_thickness > ballast_start + 1.0e-9:
                raise ValueError(
                    f"{prefix} {ring_name} centering ring intersects the ballast rods"
                )
    return {
        "ring_thickness_m": ring_thickness,
        "ring_geometry": ring_geo,
        "ring_fwd_position_m": ring_fwd_pos,
        "ring_aft_position_m": ring_aft_pos,
        "ring_fwd_material": p.get(f"{prefix}_ring_fwd_material", "fiberglass"),
        "ring_aft_material": p.get(f"{prefix}_ring_aft_material", "aluminum"),
        "ballast": ballast_layout,
    }


def podset_structural_point_masses(p, prefix):
    """Mass-equivalent support components omitted from the aerodynamic AST."""
    support = stage_support_layout(p, prefix)
    ring_geo = support["ring_geometry"]
    thickness = support["ring_thickness_m"]
    annulus_volume = math.pi * (
        ring_geo["ring_outer_radius_m"] ** 2
        - ring_geo["ring_inner_radius_m"] ** 2
    ) * thickness
    records = []
    for station, material in (
        (support["ring_fwd_position_m"], support["ring_fwd_material"]),
        (support["ring_aft_position_m"], support["ring_aft_material"]),
    ):
        records.append({
            "mass": annulus_volume * _MATERIAL_DENSITY_KG_M3[material],
            "axial_offset_m": station + thickness / 2.0,
            "radial_offset_m": 0.0,
            "instance_count": 1,
            "material": material,
        })
    core_radius = float(p[f"{prefix}_core_radius"])
    pod_radius = float(p[f"{prefix}_pod_radius"])
    pod_offset = float(p[f"{prefix}_pod_radial_offset"])
    gap = pod_offset - core_radius - pod_radius
    chord = float(p.get(f"{prefix}_pylon_chord_m", 0.025))
    pylon_thickness = float(p.get(f"{prefix}_pylon_thickness_m", 0.003))
    station_count = int(p.get(f"{prefix}_pylon_station_count", 2))
    pylon_single_mass = 2700.0 * gap * chord * pylon_thickness
    for station in pylon_stations_m(
        p[f"{prefix}_core_length"], p[f"{prefix}_pod_length"], station_count,
        float(p.get(f"{prefix}_pod_axial_offset_m", 0.0)),
        float(p.get(f"{prefix}_pod_nose_length", 0.0)), chord,
    ):
        records.append({
            "mass": pylon_single_mass * 3.0,
            "axial_offset_m": station + chord / 2.0,
            "radial_offset_m": core_radius + gap / 2.0,
            "instance_count": 3,
            "material": "aluminum",
        })
    return records


def _nose_xml(name, shape, length_m, aft_radius_m, material_key="fiberglass",
             thickness_m=0.002):
    if shape not in NOSE_SHAPES:
        raise ValueError(f"unsupported nose shape {shape!r}")
    shape_param_xml = "<shapeparameter>1.0</shapeparameter>" if shape in (
        "ellipsoid", "parabolic", "power", "haack") else ""
    return f'''
          <nosecone>
            <name>{name}</name>
            <id>{_component_id(name)}</id>
            <finish>normal</finish>
            {MATERIALS[material_key]}
            <length>{length_m:.6f}</length>
            <thickness>{thickness_m:.6f}</thickness>
            <shape>{shape}</shape>
            {shape_param_xml}
            <aftradius>{aft_radius_m:.6f}</aftradius>
            <aftshoulderlength>0.0</aftshoulderlength>
            <aftshoulderradius>0.0</aftshoulderradius>
            <aftshoulderthickness>0.0</aftshoulderthickness>
            <aftshouldercapped>false</aftshouldercapped>
          </nosecone>'''


def _pod_xml(pod_name, main_idx, cid, ignition_event, ignition_delay,
            pod_len_m, pod_radius_m, nose_len_m, nose_shape,
            fin_count=0, fin_sweep=0.0, fin_root=0.0, fin_height=0.0,
            fin_thickness_m=0.003, fin_material="fiberglass",
            max_fin_height_m=None):
    """One side pod: nose + body (containing the ascent motor mount) +
    optional fins. Built as PodSet children (siblings, like a mini stage) --
    NoseCone cannot be nested inside a BodyTube in OpenRocket's component
    model (verified empirically: raises IllegalStateException), it must be a
    sibling positioned first, exactly like a normal Stage's own layout.

    max_fin_height_m (if given) hard-gates fin_height against the real
    pod-to-core clearance: a freeformfinset's 3 fins are evenly spaced
    around the POD's own local axis with no "avoid the core" awareness, so a
    fin taller than the actual gap can and will punch into the core tube
    whenever it lands pointing inward (observed directly in a rendered
    candidate -- this is not a hypothetical)."""
    mount_or = resolve_motor(main_idx)[2] / 2.0 + MOTOR_TUBE_WALL_M + MOTOR_INSERTION_CLEARANCE_M
    if pod_radius_m - 0.002 < mount_or + MIN_DIMENSION_M:
        raise ValueError(
            f"{pod_name}: pod_radius {pod_radius_m:.4f} m too small for its "
            f"own ascent motor (needs >= {mount_or + 0.002 + MIN_DIMENSION_M:.4f} m)"
        )
    if max_fin_height_m is not None and fin_count and fin_height > max_fin_height_m - 1.0e-9:
        raise ValueError(
            f"{pod_name}: fin_height {fin_height:.4f} m exceeds the real "
            f"pod-to-core clearance ({max_fin_height_m:.4f} m) -- a fin this "
            f"tall will intersect the core regardless of its rotation angle"
        )
    nose = _nose_xml(f"{pod_name} Nose", nose_shape, nose_len_m, pod_radius_m)
    mount = _pod_motor_mount_xml(
        main_idx, cid, ignition_event, ignition_delay, 0.0,
        cluster="single", cluster_scale=1.0,
        component_name=f"{pod_name} Motor Mount",
        wall_thickness_m=MOTOR_TUBE_WALL_M,
        motor_clearance_m=MOTOR_INSERTION_CLEARANCE_M,
    )
    fins = _fin_xml(
        fin_count, fin_sweep, fin_root, fin_height, f"{pod_name} Fins",
        thickness=fin_thickness_m, material_key=fin_material,
    )
    body = f'''
          <bodytube>
            <name>{pod_name} Body</name>
            <id>{_component_id(pod_name + " Body")}</id>
            <finish>normal</finish>
            {MATERIALS["fiberglass"]}
            <length>{pod_len_m:.6f}</length>
            <thickness>0.002</thickness>
            <radius>{pod_radius_m:.6f}</radius>
            <subcomponents>
              {mount}
              {fins}
            </subcomponents>
          </bodytube>'''
    return nose + "\n" + body


def _pod_set_xml(name, instance_count, radial_offset_m, pod_inner_xml,
                 position_top_m=0.0, angle_offset_deg=0.0):
    return f'''
          <podset>
            <name>{name}</name>
            <id>{_component_id(name)}</id>
            <instancecount>{int(instance_count)}</instancecount>
            <radiusoffset method="free">{radial_offset_m:.6f}</radiusoffset>
            <angleoffset method="relative">{math.radians(angle_offset_deg):.9f}</angleoffset>
            <position type="top">{position_top_m:.6f}</position>
            <subcomponents>
              {pod_inner_xml}
            </subcomponents>
          </podset>'''


def _stage_xml(stage_name, p, prefix, cid, main_ignition_event,
               retro_ignition_event="launch", separation_xml=""):
    """Build one stage (Sustainer or Booster) of the external 3+1 vehicle."""
    core_radius = p[f"{prefix}_core_radius"]
    core_length = p[f"{prefix}_core_length"]
    main_idx = p[f"{prefix}_main"]
    retro_idx = p[f"{prefix}_retro"]

    pod_radius = p[f"{prefix}_pod_radius"]
    pod_length = p[f"{prefix}_pod_length"]
    pod_radial_offset = p[f"{prefix}_pod_radial_offset"]
    pod_nose_length = p.get(f"{prefix}_pod_nose_length", max(0.03, pod_radius * 3.0))
    pod_nose_shape = p.get(f"{prefix}_pod_nose_shape", "ogive")
    pod_axial_top = float(p.get(f"{prefix}_pod_axial_offset_m", 0.0))
    pod_angle_offset = float(p.get(f"{prefix}_pod_angle_offset_deg", 0.0))

    _pod_radial_layout(core_radius, pod_radius, pod_radial_offset)

    pod_core_gap = pod_radial_offset - core_radius - pod_radius
    pod_inner = _pod_xml(
        f"{stage_name} Pod", main_idx, cid, main_ignition_event, 0.0,
        pod_length, pod_radius, pod_nose_length, pod_nose_shape,
        fin_count=p.get(f"{prefix}_pod_fin_count", 0),
        fin_sweep=p.get(f"{prefix}_pod_fin_sweep", 0.0),
        fin_root=p.get(f"{prefix}_pod_fin_root", 0.0),
        fin_height=p.get(f"{prefix}_pod_fin_height", 0.0),
        fin_thickness_m=p.get(f"{prefix}_pod_fin_thickness_m", 0.003),
        fin_material=p.get(f"{prefix}_pod_fin_material", "fiberglass"),
        max_fin_height_m=pod_core_gap,
    )
    pod_set = _pod_set_xml(
        f"{stage_name} Side Pods", 3, pod_radial_offset, pod_inner,
        position_top_m=pod_axial_top, angle_offset_deg=pod_angle_offset,
    )
    pylons_xml = _pylons_xml(
        f"{stage_name} Pylon", core_length, core_radius, pod_radius,
        pod_radial_offset, pod_length,
        chord_m=p.get(f"{prefix}_pylon_chord_m", 0.025),
        thickness_m=p.get(f"{prefix}_pylon_thickness_m", 0.003),
        station_count=p.get(f"{prefix}_pylon_station_count", 2),
        pod_axial_top_m=pod_axial_top,
        pod_nose_length_m=pod_nose_length,
        angle_offset_deg=pod_angle_offset,
    )

    retro_delay = p[f"{prefix}_retro_delay"]
    retro_mount = _pod_motor_mount_xml(
        retro_idx, cid, retro_ignition_event, retro_delay, 0.0,
        cluster="single", cluster_scale=1.0,
        component_name=f"{stage_name} Central Retro Mount",
        wall_thickness_m=MOTOR_TUBE_WALL_M,
        motor_clearance_m=MOTOR_INSERTION_CLEARANCE_M,
        mount_length_m=core_length,
    )

    support = stage_support_layout(p, prefix)
    ring_thickness = support["ring_thickness_m"]
    ring_geo = support["ring_geometry"]
    ring_fwd_material = support["ring_fwd_material"]
    ring_aft_material = support["ring_aft_material"]
    ring_fwd_pos = support["ring_fwd_position_m"]
    ring_aft_pos = support["ring_aft_position_m"]
    ballast_layout = support["ballast"]

    fwd_ring = _centering_ring_xml(
        f"{stage_name} Forward Centering Ring", ring_fwd_pos,
        outer_radius_m=ring_geo["ring_outer_radius_m"],
        inner_radius_m=ring_geo["ring_inner_radius_m"],
        length_m=ring_thickness,
    ).replace(MATERIALS["fiberglass"], MATERIALS[ring_fwd_material])
    aft_ring = _centering_ring_xml(
        f"{stage_name} Aft Centering Ring", ring_aft_pos,
        outer_radius_m=ring_geo["ring_outer_radius_m"],
        inner_radius_m=ring_geo["ring_inner_radius_m"],
        length_m=ring_thickness,
    ).replace(MATERIALS["fiberglass"], MATERIALS[ring_aft_material])

    core_fins = _fin_xml(
        p.get(f"{prefix}_core_fin_count", 4),
        p.get(f"{prefix}_core_fin_sweep", 15.0),
        p.get(f"{prefix}_core_fin_root", max(0.10, core_radius * 4.0)),
        p.get(f"{prefix}_core_fin_height", max(0.05, core_radius * 2.0)),
        f"{stage_name} Core Fins",
        thickness=p.get(f"{prefix}_core_fin_thickness_m", 0.003),
        material_key=p.get(f"{prefix}_core_fin_material", "fiberglass"),
        rotation_deg=p.get(f"{prefix}_core_fin_angle_offset_deg", 0.0),
    )

    forward_fins = _fin_xml(
        p.get(f"{prefix}_grid_fin_count", 0),
        p.get(f"{prefix}_grid_fin_sweep", 0.0),
        p.get(f"{prefix}_grid_fin_root", 0.06),
        p.get(f"{prefix}_grid_fin_height", 0.06),
        f"{stage_name} Forward Fins",
        thickness=p.get(f"{prefix}_grid_fin_thickness_m", 0.003),
        material_key=p.get(f"{prefix}_grid_fin_material", "fiberglass"),
        position_from_top_m=p.get(f"{prefix}_grid_fin_position_m", 0.10),
        rotation_deg=p.get(f"{prefix}_grid_fin_angle_offset_deg", 0.0),
    )

    ballast_xml = (
        _ballast_rods_xml(f"{stage_name} Core Ballast", ballast_layout)
        if ballast_layout is not None
        else ""
    )

    return f'''
      <stage>
        <name>{stage_name}</name>
        <id>{_component_id(stage_name)}</id>
        {separation_xml}
        <subcomponents>
          <bodytube>
            <name>{stage_name} Core</name>
            <id>{_component_id(stage_name + " Core")}</id>
            <finish>normal</finish>
            {MATERIALS["cardboard"]}
            <length>{core_length:.6f}</length>
            <thickness>0.002</thickness>
            <radius>{core_radius:.6f}</radius>
            <subcomponents>
              {pod_set}
              {pylons_xml}
              {retro_mount}
              {fwd_ring}
              {aft_ring}
              {core_fins}
              {forward_fins}
              {ballast_xml}
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>'''


def podset_stage_length_m(p, prefix):
    """Actual axial envelope of one stage, including its radial pod nose."""
    core = float(p[f"{prefix}_core_length"])
    pod = float(p[f"{prefix}_pod_length"])
    pod_nose = float(
        p.get(
            f"{prefix}_pod_nose_length",
            max(0.03, float(p[f"{prefix}_pod_radius"]) * 3.0),
        )
    )
    pod_top = float(p.get(f"{prefix}_pod_axial_offset_m", 0.0))
    return max(core, pod_top + pod + pod_nose) - min(0.0, pod_top)


def podset_total_height_m(p):
    nose = float(p.get("nose_length_m", max(0.2, p["s0_core_radius"] * 8)))
    return nose + podset_stage_length_m(p, "s0") + podset_stage_length_m(p, "s1")


def generate_podset_ork(p):
    """Full 2-stage external-3+1-pod vehicle. Same official environment
    (launch site, wind, atmosphere, seed, anti-tumble) as osifog_sweep.py's
    generate_ork(), different structural compiler."""
    import uuid
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, "l2-osifog/external3plus1/official"))

    nose_len = float(p.get("nose_length_m", max(0.2, p["s0_core_radius"] * 8)))
    nose_ballast_pos = float(p.get("nose_ballast_pos_m", nose_len * 0.75))
    nose_mass_kg = p.get("nose_mass_kg", 0.05)
    # The bulkhead must touch the inside of the 2 mm nose shell.  The former
    # extra 1 mm assembly clearance left the ballast floating and made the
    # generated topology fail the same rigid-attachment rule repaired in
    # Candidate E.
    nose_inner_radius = (
        _haack_radius(nose_ballast_pos, nose_len, p["s0_core_radius"]) - 0.002
    )
    nose_ballast_xml = (
        _solid_ballast_xml("Nose Ballast", nose_mass_kg, nose_ballast_pos, nose_inner_radius,
                          material_key=p.get("nose_ballast_material", "steel"),
                          available_length_m=nose_len - nose_ballast_pos)
        if nose_mass_kg > 0 else ""
    )
    nose_xml = f'''
        <nosecone>
          <name>Nose Cone</name>
          <id>{_component_id("Nose Cone")}</id>
          <finish>normal</finish>
          {MATERIALS["fiberglass"]}
          <length>{nose_len:.6f}</length>
          <thickness>0.002</thickness>
          <shape>haack</shape>
          <shapeclipped>false</shapeclipped>
          <aftradius>{p["s0_core_radius"]:.6f}</aftradius>
          <aftshoulderlength>0.03</aftshoulderlength>
          <aftshoulderradius>{p["s0_core_radius"] - 0.003:.6f}</aftshoulderradius>
          <aftshoulderthickness>0.002</aftshoulderthickness>
          <aftshouldercapped>false</aftshouldercapped>
          <subcomponents>
            {nose_ballast_xml}
          </subcomponents>
        </nosecone>'''

    # OpenRocket lists stages nose-to-tail.  The lower Booster fires at launch;
    # the upper Sustainer fires when that lower stage burns out.
    stage0 = _stage_xml(
        "Sustainer", p, "s0", cid, "burnout",
        retro_ignition_event=p.get("s0_retro_ignition_event", "launch"),
    )
    separation_xml = (
        f'<separationevent>burnout</separationevent>\n'
        f'        <separationdelay>{p.get("s1_separation_delay", 0.0):.3f}</separationdelay>'
    )
    stage1 = _stage_xml(
        "Booster", p, "s1", cid, "launch",
        retro_ignition_event=p.get("s1_retro_ignition_event", "launch"),
        separation_xml=separation_xml,
    )

    # Nose is prepended to stage0's own subcomponents by wrapping the stage
    # string is awkward -- instead splice it in directly via string surgery
    # on the already-built stage0 (keeps _stage_xml a single-purpose builder
    # shared by both stages).
    stage0 = stage0.replace(
        "<subcomponents>\n          <bodytube>",
        f"<subcomponents>\n          {nose_xml}\n          <bodytube>",
        1,
    )

    launch_azimuth = p.get("launch_azimuth", 270.0)
    launch_angle_deg = p.get("launch_angle_deg", 0.0)
    wl = p["wind_levels"]
    ws, wd, wstd = wl[0][1], wl[0][2], wl[0][3]
    wl_xml = "\n".join(
        f'          <windlevel altitude="{a}" speed="{s}" '
        f'direction="{math.radians(d):.15f}" standarddeviation="{sd}"/>'
        for a, s, d, sd in wl
    )
    wind_block = f'''
        <wind model="average">
          <speed>{ws}</speed>
          <direction>{wd}</direction>
          <standarddeviation>{wstd}</standarddeviation>
        </wind>
        <wind model="multilevel" altituderef="agl">
{wl_xml}
        </wind>
        <windmodeltype>multilevel</windmodeltype>'''

    return f'''<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.6" creator="L2-OSIFOG-PodSet">
  <rocket>
    <name>OSIFOG Level 3 External 3+1</name>
    <id>{_component_id("OSIFOG Level 3 External 3+1")}</id>
    <designer>L2 Systems AI</designer>
    <motorconfiguration configid="{cid}" default="true"/>
    <referencetype>maximum</referencetype>
    <subcomponents>
      {stage0}
      {stage1}
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="notsimulated">
      <name>OSIFOG Level 3 External 3+1</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{cid}</configid>
        <launchrodlength>{LAUNCH_ROD_M}</launchrodlength>
        <launchintowind>false</launchintowind>
        <launchrodangle>{launch_angle_deg:.6f}</launchrodangle>
        <launchroddirection>{launch_azimuth:.6f}</launchroddirection>
{wind_block}
        <launchaltitude>{LAUNCH_ALT}</launchaltitude>
        <launchlatitude>{LAUNCH_LAT}</launchlatitude>
        <launchlongitude>{LAUNCH_LON}</launchlongitude>
        <geodeticmethod>spherical</geodeticmethod>
        <atmosphere model="extendedisa">
          <basetemperature>{TEMP_K:.2f}</basetemperature>
          <basepressure>{PRESSURE_PA:.1f}</basepressure>
          <baserelativehumidity>{HUMIDITY}</baserelativehumidity>
        </atmosphere>
        <timestep>{float(p.get("timestep_s", 0.05)):.7f}</timestep>
      </conditions>
      <extension extensionid="info.openrocket.core.simulation.extension.impl.ScriptingExtension">
        <entry key="language" type="string">JavaScript</entry>
        <entry key="script" type="string">{ANTI_TUMBLE_SCRIPT}</entry>
        <entry key="enabled" type="boolean">true</entry>
      </extension>
    </simulation>
  </simulations>
</openrocket>'''
