#!/usr/bin/env python3
"""
OSIFOG Level 3 — Two-Stage Retro-Propulsive Rocket Sweep
Sweeps motor combinations, geometry, ballast, launch direction and ignition
delays against real OpenRocket 24.12 physics with multi-level wind.

CORRECTED: 2026-07-19
  - Scoring formula: 900K start, correct terms (not 1M, not wrong penalties)
  - Touchdown: total speed (not vertical only)
  - Apogee position: East/North extracted from sim
  - Launch rod: 6.0m (max allowed), not 2.0m
  - Launch azimuth: optimization variable (0-360°)
  - Propellant: actual kg from motor data, not class-estimate
  - 3-point ballast: nose, mid-CG, aft
  - Burnout target: 0-0.3s before impact (not 1-2s)
  - Hard constraint validator before scoring
"""

import csv
import math
import uuid
import os
import sys
import json
import time
import tempfile
import itertools
import hashlib
import re
import zipfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from xml.sax.saxutils import escape as _xml_escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rocket_forge import MOTOR_DATABASE
from physical_geometry import (
    ASSEMBLY_CLEARANCE_M,
    AxialCylinder,
    CenteringRingSpec,
    falcon_ballast_rods,
    falcon_cluster_cylinders,
    validate_attachment_paths,
    validate_centering_ring_pair,
    validate_cylinders,
)

# ═══════════════════════════════════════════════════════════════
# Mission constants (OSIFOG Level 3 — Cape Canaveral SLC-40)
# ═══════════════════════════════════════════════════════════════
TARGET_APOGEE = 3000.0
LAUNCH_LAT    = 28.5621
LAUNCH_LON    = -80.5772
LAUNCH_ALT    = 3.0
TEMP_K        = 30.1 + 273.15   # 303.25 K — 2024-09-28 17:17 UTC launch site
PRESSURE_PA   = 1000.0 * 100.0  # 100000 Pa
HUMIDITY      = 0.82            # OpenWind surface diagnostic (not a GUI input)
WIND_CSV      = "OSIFOG/OpenWind_File.csv"
LAUNCH_ROD_M  = 6.0             # Maximum allowed — no penalty for using max
SIM_SEED      = 16000
MAX_MACH      = 0.95             # Deliberate margin below the banned Mach 1 boundary
MAX_HEIGHT_M  = 4.0
MIN_STATIC_MARGIN = 0.3          # Sustainer-only stability during powered ascent; mission requires SM > 0 (stable), 0.3 provides safety margin
MIN_FULL_STACK_MARGIN = 0.5      # Full-stack minimum during boost phase (2-stage physics)
MIN_DIMENSION_M = 0.001          # Mission rule: no dimension below 0.1 cm
MOTOR_TUBE_WALL_M = 0.001
MOTOR_INSERTION_CLEARANCE_M = 0.00025
NOSE_SHELL_THICKNESS_M = 0.002
BALLAST_MATERIAL_DENSITY_KG_M3 = {
    "steel": 7900.0,
    "aluminum": 2700.0,
}

ANTI_TUMBLE_SCRIPT = """function handleFlightEvent(status, event) {
  if (event.getType().name() === "TUMBLE") {
    return false;
  }
  return true;
}"""


def normalize_anti_tumble_script(script: str) -> str:
    """Normalize insignificant whitespace while preserving JS tokens."""
    return re.sub(r"\s+", "", str(script))


ANTI_TUMBLE_SCRIPT_DIGEST = hashlib.sha256(
    normalize_anti_tumble_script(ANTI_TUMBLE_SCRIPT).encode("utf-8")
).hexdigest()


def validate_anti_tumble_extensions(extensions) -> tuple[bool, list[str]]:
    """Fail closed unless exactly one extension is the official listener."""
    violations = []
    if len(extensions) != 1:
        violations.append(
            f"exactly one simulation extension required, found {len(extensions)}"
        )
        return False, violations
    extension = extensions[0]
    extension_id = str(extension.get("extensionid", ""))
    if not extension_id.endswith("ScriptingExtension"):
        violations.append(f"unauthorized extension: {extension_id}")
    script = str(extension.get("script", ""))
    if normalize_anti_tumble_script(script) != normalize_anti_tumble_script(
        ANTI_TUMBLE_SCRIPT
    ):
        violations.append("anti-tumble script differs from official normalized script")
    return not violations, violations


def inspect_anti_tumble_xml(ork_xml: str) -> dict:
    """Inspect the serialized simulation extension without trusting intent."""
    root = ET.fromstring(ork_xml)
    simulations = root.findall(".//simulation")
    results = []
    for simulation in simulations:
        extensions = []
        for extension in simulation.findall("extension"):
            entries = {
                entry.get("key"): entry.text or ""
                for entry in extension.findall("entry")
            }
            extensions.append(
                {
                    "extensionid": extension.get("extensionid", ""),
                    "script": entries.get("script", ""),
                    "enabled": entries.get("enabled", ""),
                }
            )
        valid, violations = validate_anti_tumble_extensions(extensions)
        if extensions and extensions[0].get("enabled") != "true":
            violations.append("official anti-tumble extension is not enabled")
            valid = False
        results.append(
            {
                "valid": valid,
                "violations": violations,
                "extension_count": len(extensions),
            }
        )
    return {
        "valid": len(simulations) == 1 and all(item["valid"] for item in results),
        "simulation_count": len(simulations),
        "script_digest": ANTI_TUMBLE_SCRIPT_DIGEST,
        "simulations": results,
    }


def validate_serialized_flight_event_references(ork_xml: str) -> list[str]:
    """Reject stored flight-data references that cannot survive a reload.

    OpenRocket 24.12 reconstructs ``EventAfterLanding`` warnings by resolving
    the warning event's ``eventid`` against events in the same data branch.
    If the target event was not serialized, the warning retains a null event
    and OpenRocket's next Save/Save As dereferences it.  Validate both warning
    and event references from the final packaged XML so this fails before a
    submission reaches the GUI.
    """
    root = ET.fromstring(ork_xml)
    warning_ids = {
        warning.findtext("id")
        for warning in root.findall(".//warning")
        if warning.findtext("id")
    }
    violations = []
    for branch_index, branch in enumerate(root.findall(".//databranch")):
        branch_name = branch.get("name") or f"branch {branch_index}"
        events = branch.findall("event")
        event_ids = {event.get("id") for event in events if event.get("id")}
        for event in events:
            warning_id = event.get("warnid")
            if warning_id and warning_id not in warning_ids:
                violations.append(
                    f"{branch_name}: warning event references missing warnid "
                    f"{warning_id}"
                )
            target_id = event.get("eventid")
            if target_id and target_id not in event_ids:
                violations.append(
                    f"{branch_name}: warning event references missing eventid "
                    f"{target_id}"
                )
    return violations


SEPARATION_EVENT_XML_NAMES = {
    "launch",
    "ignition",
    "burnout",
    "ejection",
    "upperignition",
    "altitudeascending",
    "apogee",
    "altitudedescending",
    "never",
}

# ───────────────────────────────────────────────────────────────
# Motor propellant masses — now served by canonical motor_data module.
# Legacy MOTOR_PROPELLANT_KG dict removed. Use motor_data.propellant_kg().
import motor_data as _motor_data


def propellant_kg(motor_idx: int) -> float:
    """Return exact propellant mass for motor by index from .eng file header."""
    return _motor_data.propellant_kg(motor_idx)


MATERIALS = {
    "cardboard":   '<material type="bulk" density="600">Cardboard</material>',
    "kraft":       '<material type="bulk" density="700">Kraft phenolic</material>',
    "fiberglass":  '<material type="bulk" density="1800">Fiberglass</material>',
    "balsa":       '<material type="bulk" density="160">Balsa</material>',
    "legal_balsa": (
        '<material type="bulk" density="170">'
        'Selected Balsa (0.17 g/cm3)</material>'
    ),
    "aluminum":    '<material type="bulk" density="2700">Aluminum</material>',
    "lead":        '<material type="bulk" density="11340">Lead</material>',
    "steel":       '<material type="bulk" density="7900">Steel</material>',
}


# ═══════════════════════════════════════════════════════════════
# Wind CSV parser
# ═══════════════════════════════════════════════════════════════
def parse_wind_csv(path):
    levels = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",")
            if len(parts) >= 4:
                try:
                    levels.append((
                        float(parts[0].strip()),
                        float(parts[1].strip()),
                        float(parts[2].strip()),
                        float(parts[3].strip()),
                    ))
                except ValueError:
                    continue
    return levels


# ═══════════════════════════════════════════════════════════════
# .ORK XML component builders
# ═══════════════════════════════════════════════════════════════

def _component_id(name: str) -> str:
    """Stable OpenRocket component UUID for deterministic load/simulation."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"l2-osifog/component/{name}"))


def _minimum_initial_ascent_stability(
    altitudes,
    vertical_speeds,
    stabilities,
    times=None,
    start_time_s=None,
    end_time_s=None,
):
    """Minimum finite stability before the branch's first apogee.

    A retro motor can create a second positive-vertical-velocity segment near
    touchdown.  That is powered landing, not the mission's initial ascent, and
    must not contaminate the ascent stability gate.
    """
    count = min(len(altitudes), len(vertical_speeds), len(stabilities))
    if count == 0:
        return None
    apex_index = max(range(count), key=lambda index: float(altitudes[index]))
    values = [
        float(stabilities[index])
        for index in range(apex_index + 1)
        if float(vertical_speeds[index]) > 0.01
        and math.isfinite(float(stabilities[index]))
        and (
            start_time_s is None
            or times is None
            or float(times[index]) >= float(start_time_s) - 1.0e-9
        )
        and (
            end_time_s is None
            or times is None
            or float(times[index]) < float(end_time_s) - 1.0e-9
        )
    ]
    return min(values) if values else None

def _falcon_cluster_geometry(
    main_idx,
    retro_idx,
    body_radius_m,
    ring_annulus_m=0.0,
):
    """Derive a continuous-contact 3+1 cage inside the selected airframe.

    Historical ringless batches expanded the cage to the body wall.
    Structurally complete builds reserve ``ring_annulus_m`` between the
    cluster envelope and body bore while keeping the central sleeve tangent
    to all three main mounts.
    """

    main_outer = (
        MOTOR_DATABASE[main_idx][2] / 2.0
        + MOTOR_TUBE_WALL_M
        + MOTOR_INSERTION_CLEARANCE_M
    )
    retro_cavity = (
        MOTOR_DATABASE[retro_idx][2] / 2.0 + MOTOR_INSERTION_CLEARANCE_M
    )
    body_inner = float(body_radius_m) - 0.002
    ring_annulus_m = float(ring_annulus_m)
    if ring_annulus_m < -1.0e-9:
        raise ValueError("centering-ring annulus cannot be negative")
    if 0.0 < ring_annulus_m < MIN_DIMENSION_M - 1.0e-9:
        raise ValueError("centering-ring annulus is below the mission minimum")
    center_distance = body_inner - ring_annulus_m - main_outer
    sleeve_outer = center_distance - main_outer
    sleeve_wall = sleeve_outer - retro_cavity
    if sleeve_wall < MIN_DIMENSION_M - 1.0e-9:
        raise ValueError(
            "3+1 motor cage has insufficient room for a legal central support sleeve"
        )
    scale = center_distance * math.sqrt(3.0) / (2.0 * main_outer)
    return {
        "cluster_scale": scale,
        "center_distance_m": center_distance,
        "main_outer_radius_m": main_outer,
        "retro_sleeve_outer_radius_m": sleeve_outer,
        "retro_sleeve_wall_m": sleeve_wall,
        "body_inner_radius_m": body_inner,
    }


def _min_octaweb_body_radius_m(main_idx, retro_idx):
    """The smallest body_radius_m for which `_falcon_cluster_geometry(main_idx,
    retro_idx, body_radius_m)` does NOT raise ValueError -- i.e. the actual
    physical floor a body tube must clear to legally host this exact motor
    pair as a 3+1 octaweb cage. Direct algebraic inverse of that function's
    own sleeve_wall >= MIN_DIMENSION_M requirement (solve body_inner for
    sleeve_wall == MIN_DIMENSION_M exactly, then add the same 0.002 body-wall
    term back). Exists so a caller whose (main_idx, retro_idx, body_radius_m)
    combination is currently infeasible can WIDEN body_radius_m to a
    known-good value instead of leaving stale/inconsistent cage geometry in
    place (see rocket_ast.py::sanitize_ast_for_openrocket's octaweb repair
    pass, which does exactly this after a mutation/crossover motor swap made
    the cage no longer fit its stage's current body radius)."""
    main_outer = (
        MOTOR_DATABASE[main_idx][2] / 2.0
        + MOTOR_TUBE_WALL_M
        + MOTOR_INSERTION_CLEARANCE_M
    )
    retro_cavity = (
        MOTOR_DATABASE[retro_idx][2] / 2.0 + MOTOR_INSERTION_CLEARANCE_M
    )
    return 2.0 * main_outer + retro_cavity + MIN_DIMENSION_M + 0.002


def _falcon_cluster_scale(main_idx, retro_idx, body_radius_m):
    return _falcon_cluster_geometry(main_idx, retro_idx, body_radius_m)[
        "cluster_scale"
    ]

def _motor_mount_xml(motor_idx, config_id, ignition_event, ignition_delay,
                     position_bottom=0.0, cluster="single", cluster_scale=1.0,
                     radius_m=0.0, component_name=None,
                     wall_thickness_m=0.001, motor_clearance_m=0.0,
                     mount_length_m=None, plugged=False):
    """Build an inner tube (motor mount) XML block.

    `plugged=True` writes <delay>none</delay> instead of the catalog's listed
    ejection delay. This vehicle carries NO recovery device, and the ascent
    motors are plugged hardware in reality (e.g. 949J150-**P**), so the catalog
    delay would make OpenRocket fire an EJECTION_CHARGE at burnout+delay on a
    rocket with nothing to deploy -- trajectory-neutral, but a physical
    misstatement and the source of "Flight Event occurred after landing"
    warnings in the saved file.
    """
    mfr, designation, diam, length, delay, digest = MOTOR_DATABASE[motor_idx]
    if plugged:
        delay = 0.0
    mount_or = diam / 2.0 + wall_thickness_m + motor_clearance_m
    mount_length = (
        float(mount_length_m) if mount_length_m is not None else length + 0.02
    )
    if mount_length + 1.0e-9 < length + 0.02:
        raise ValueError(
            f"{component_name or designation} mount is shorter than its motor"
        )
    delay_str = "none" if delay <= 0 else str(int(delay))
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


def _centering_ring_xml(name, position_top_m, outer_radius_m, inner_radius_m,
                        length_m=0.005, radial_position_m=0.0,
                        radial_direction_rad=0.0):
    """Serialize one centered native annulus with explicit resolved radii."""
    return f'''
          <centeringring>
            <name>{name}</name>
            <id>{_component_id(name)}</id>
            <position type="top">{position_top_m:.9f}</position>
            {MATERIALS["fiberglass"]}
            <length>{length_m:.9f}</length>
            <radialposition>{radial_position_m:.9f}</radialposition>
            <radialdirection>{radial_direction_rad:.9f}</radialdirection>
            <outerradius>{outer_radius_m:.9f}</outerradius>
            <innerradius>{inner_radius_m:.9f}</innerradius>
          </centeringring>'''


def _octaweb_ring_specs(
    cage,
    name_prefix,
    body_length_m,
    forward_position_top_m,
    ring_length_m=0.005,
    aft_position_top_m=None,
):
    """Build the two centered annuli that retain one stage's complete cage.

    OpenRocket ignores ``CenteringRing.radialposition`` and its automatic
    inner-radius calculation ignores clustered tubes' radial offsets.  The
    booster therefore uses an explicit bore around the complete three-motor
    envelope.  A retro-only stage uses the central sleeve's outer radius.
    """
    center_distance = cage["center_distance_m"]
    main_outer = cage["main_outer_radius_m"]
    sleeve_outer = cage["retro_sleeve_outer_radius_m"]
    support_envelope = (
        center_distance + main_outer if main_outer > 0.0 else sleeve_outer
    )
    body_inner = cage["body_inner_radius_m"]
    aft_position = (
        float(aft_position_top_m)
        if aft_position_top_m is not None
        else float(body_length_m) - ring_length_m
    )
    rings = (
        CenteringRingSpec(
            name=f"{name_prefix} Forward",
            axial_start_m=float(forward_position_top_m),
            length_m=ring_length_m,
            outer_radius_m=body_inner,
            inner_radius_m=support_envelope,
        ),
        CenteringRingSpec(
            name=f"{name_prefix} Aft (Thrust)",
            axial_start_m=aft_position,
            length_m=ring_length_m,
            outer_radius_m=body_inner,
            inner_radius_m=support_envelope,
        ),
    )
    violations = validate_centering_ring_pair(
        float(body_length_m),
        body_inner,
        support_envelope,
        rings,
        (float(forward_position_top_m), aft_position),
    )
    if violations:
        raise ValueError("physical centering-ring gate: " + "; ".join(violations))
    return rings


def _octaweb_rings_xml(
    cage,
    name_prefix,
    body_length_m,
    forward_position_top_m,
    ring_length_m=0.005,
    aft_position_top_m=None,
):
    """Serialize the validated forward/aft cage-retention ring pair."""
    return "\n".join(
        _centering_ring_xml(
            ring.name,
            ring.axial_start_m,
            outer_radius_m=ring.outer_radius_m,
            inner_radius_m=ring.inner_radius_m,
            length_m=ring.length_m,
        )
        for ring in _octaweb_ring_specs(
            cage,
            name_prefix,
            body_length_m,
            forward_position_top_m,
            ring_length_m,
            aft_position_top_m,
        )
    )


def _tube_coupler_xml(name, position_top_m, outer_radius_m, inner_radius_m,
                      length_m=0.050, appearance=""):
    """Native internal interstage coupler spanning both sides of the joint."""
    return f'''
          <tubecoupler>
            <name>{name}</name>
            <id>{_component_id(name)}</id>
            {appearance}
            <position type="top">{position_top_m:.9f}</position>
            {MATERIALS["fiberglass"]}
            <length>{length_m:.9f}</length>
            <radialposition>0.0</radialposition>
            <radialdirection>0.0</radialdirection>
            <outerradius>{outer_radius_m:.9f}</outerradius>
            <thickness>{outer_radius_m - inner_radius_m:.9f}</thickness>
          </tubecoupler>'''


def _paint_rgba(paint):
    """Return an OpenRocket-compatible RGBA tuple from hex or channel values."""
    if isinstance(paint, str):
        value = paint.strip().lstrip("#")
        if len(value) not in (6, 8):
            raise ValueError("livery paint hex must contain 6 or 8 digits")
        try:
            channels = tuple(
                int(value[index:index + 2], 16)
                for index in range(0, len(value), 2)
            )
        except ValueError as exc:
            raise ValueError(f"invalid livery paint {paint!r}") from exc
        if len(channels) == 3:
            channels += (255,)
    else:
        channels = tuple(int(channel) for channel in paint)
        if len(channels) == 3:
            channels += (255,)
        if len(channels) != 4:
            raise ValueError("livery paint must contain 3 or 4 channels")
    if any(channel < 0 or channel > 255 for channel in channels):
        raise ValueError("livery paint channels must be within 0..255")
    return channels


def _appearance_xml(paint, shine=0.3, decal=None):
    """Serialize cosmetic-only OpenRocket appearance settings.

    This helper must never emit ``<finish>``. Surface finish is aerodynamic;
    appearance paint, shine and decals are render-only.
    """
    red, green, blue, alpha = _paint_rgba(paint)
    shine = float(shine)
    if not 0.0 <= shine <= 1.0:
        raise ValueError("livery shine must be within 0..1")
    lines = [
        "<appearance>",
        (
            f'  <paint red="{red}" green="{green}" blue="{blue}" '
            f'alpha="{alpha}"/>'
        ),
        f"  <shine>{shine:.6f}</shine>",
    ]
    if decal is not None:
        name = _xml_escape(str(decal["name"]), {'"': "&quot;"})
        rotation = float(decal.get("rotation", 0.0))
        edge_mode = str(decal.get("edgemode", "CLAMP")).strip().upper()
        if edge_mode not in ("REPEAT", "CLAMP", "MIRROR"):
            raise ValueError("livery decal edgemode must be REPEAT, CLAMP or MIRROR")
        center = decal.get("center", {})
        offset = decal.get("offset", {})
        scale = decal.get("scale", {})
        lines.extend(
            [
                (
                    f'  <decal name="{name}" rotation="{rotation:.6f}" '
                    f'edgemode="{edge_mode}">'
                ),
                (
                    f'    <center x="{float(center.get("x", 0.0)):.6f}" '
                    f'y="{float(center.get("y", 0.0)):.6f}"/>'
                ),
                (
                    f'    <offset x="{float(offset.get("x", 0.0)):.6f}" '
                    f'y="{float(offset.get("y", 0.0)):.6f}"/>'
                ),
                (
                    f'    <scale x="{float(scale.get("x", 1.0)):.6f}" '
                    f'y="{float(scale.get("y", 1.0)):.6f}"/>'
                ),
                "  </decal>",
            ]
        )
    lines.append("</appearance>")
    return "\n".join(lines)


def _component_appearance(livery, component_name):
    """Return opt-in appearance XML for one named external component."""
    if not livery:
        return ""
    components = livery.get("components", {})
    spec = components.get(component_name)
    if spec is None:
        return ""
    return _appearance_xml(
        spec["paint"],
        shine=spec.get("shine", 0.3),
        decal=spec.get("decal"),
    )


def _interstage_coupler_geometry(p, physical_layouts):
    """Compile the optional booster-owned coupler and prove internal clearance."""
    if not bool(p.get("interstage_coupler", False)):
        return None

    length = float(p.get("interstage_coupler_length_m", 0.050))
    wall = float(p.get("interstage_coupler_wall_m", 0.001))
    sustainer_overlap = float(
        p.get("interstage_coupler_sustainer_overlap_m", length / 2.0)
    )
    booster_overlap = length - sustainer_overlap
    for value, label in (
        (length, "interstage coupler length"),
        (wall, "interstage coupler wall"),
        (sustainer_overlap, "interstage coupler sustainer overlap"),
        (booster_overlap, "interstage coupler booster overlap"),
    ):
        if value < MIN_DIMENSION_M - 1.0e-9:
            raise ValueError(
                f"{label} {value:.9f} m is below the mission's "
                f"{MIN_DIMENSION_M:.3f} m minimum"
            )

    s0_inner = float(p["s0_body_rad"]) - 0.002
    s1_inner = float(p["s1_body_rad"]) - 0.002
    if not math.isclose(s0_inner, s1_inner, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError(
            "interstage coupler requires equal stage bores without a transition"
        )
    outer_radius = s0_inner
    inner_radius = outer_radius - wall
    if inner_radius < MIN_DIMENSION_M:
        raise ValueError("interstage coupler wall leaves no legal internal bore")

    stage_regions = {
        "s0": (
            float(p["s0_body_len"]) - sustainer_overlap,
            float(p["s0_body_len"]),
        ),
        "s1": (0.0, booster_overlap),
    }
    for stage, (region_start, region_end) in stage_regions.items():
        layout = physical_layouts[stage]
        solids = list(layout["motors"])
        solids.extend(
            rod
            for _, rods in layout["rod_groups"]
            for rod in rods
        )
        for solid in solids:
            axial_overlap = (
                max(region_start, solid.axial_start_m)
                < min(region_end, solid.axial_end_m) - 1.0e-9
            )
            if not axial_overlap:
                continue
            envelope = solid.radial_position_m + solid.radius_m
            if envelope > inner_radius + 1.0e-9:
                raise ValueError(
                    f"interstage coupler bore collision: {solid.name} reaches "
                    f"{envelope:.6f} m inside {inner_radius:.6f} m bore"
                )

    return {
        "length_m": length,
        "wall_m": wall,
        "outer_radius_m": outer_radius,
        "inner_radius_m": inner_radius,
        "sustainer_overlap_m": sustainer_overlap,
        "booster_overlap_m": booster_overlap,
        # The component belongs to the booster.  A negative top offset inserts
        # its forward portion into the sustainer before stage separation.
        "booster_position_top_m": -sustainer_overlap,
    }


def _fin_xml(
    count,
    sweep_deg,
    root,
    height,
    name="Fins",
    thickness=0.003,
    material_key="fiberglass",
    position_from_top_m=None,
    rotation_deg=0.0,
    appearance="",
):
    """Build a freeform fin set."""
    if int(count) == 0:
        return ""
    if int(count) < 3:
        raise ValueError("OpenRocket fin sets require either zero or at least three fins")
    if float(thickness) < MIN_DIMENSION_M:
        raise ValueError(
            f"{name} thickness {float(thickness):.6f} m is below the "
            f"{MIN_DIMENSION_M:.3f} m mission minimum"
        )
    if material_key not in ("legal_balsa", "cardboard", "fiberglass"):
        raise ValueError(
            f"{name} material {material_key!r} is not an approved legal fin material"
        )
    sweep_rad = math.radians(sweep_deg)
    tip = root * 0.35
    sw_off = height * math.tan(sweep_rad)
    pts = [(0, 0), (sw_off, height), (sw_off + tip, height), (root, 0)]
    pts_s = "\n".join(f'<point x="{p[0]:.6f}" y="{p[1]:.6f}"/>' for p in pts)
    position_xml = (
        f'<position type="top">{float(position_from_top_m):.6f}</position>'
        if position_from_top_m is not None
        else '<position type="bottom">0.0</position>'
    )
    return f'''
          <freeformfinset>
            <name>{name}</name>
            <id>{_component_id(name)}</id>
            {appearance}
            {position_xml}
            <rotation>{float(rotation_deg):.6f}</rotation>
            {MATERIALS[material_key]}
            <fincount>{count}</fincount>
            <thickness>{thickness:.4f}</thickness>
            <crosssection>airfoil</crosssection>
            <finpoints>{pts_s}</finpoints>
          </freeformfinset>'''


STRAKE_PLANFORMS = ("tapered", "triangular", "clipped_delta", "shallow_sweep")


def _strake_xml(
    count,
    planform,
    length_m,
    span_m,
    body_length_m,
    name="Strakes",
    thickness=0.003,
    material_key="fiberglass",
    position_from_top_m=0.0,
):
    """Build a long, shallow-span symmetric strake/keel freeform fin set.

    Unlike `_fin_xml` (short-chord, tall trapezoidal aft fins), strakes run
    most of the body length with a shallow radial projection. The intent
    (mission Family C) is a surface that is aerodynamically mild in the
    low-AoA nose-first ascent regime, but still contributes a moment during
    the high-AoA/reverse-flow regime the vehicle passes through when
    transitioning between tail-first and nose-first free descent -- see
    `artifacts/autoevo/flip-diagnosis-report.md` for why aft fins alone
    cannot hold that transition (thrust line has zero moment by
    construction; the true blocker is the aerodynamic re-orientation).
    """
    if int(count) == 0:
        return ""
    if int(count) not in (3, 4):
        raise ValueError("strakes must be 3-fold or 4-fold symmetric (count in {3, 4})")
    if float(thickness) < MIN_DIMENSION_M:
        raise ValueError(
            f"{name} thickness {float(thickness):.6f} m is below the "
            f"{MIN_DIMENSION_M:.3f} m mission minimum"
        )
    if material_key not in ("legal_balsa", "cardboard", "fiberglass"):
        raise ValueError(
            f"{name} material {material_key!r} is not an approved legal fin material"
        )
    length_m = float(length_m)
    span_m = float(span_m)
    position_from_top_m = float(position_from_top_m)
    if length_m <= 0.0 or span_m <= 0.0:
        raise ValueError(f"{name} requires positive length and span")
    if position_from_top_m < 0.0 or position_from_top_m + length_m > float(body_length_m) + 1.0e-9:
        raise ValueError(
            f"{name} (start {position_from_top_m:.3f} m, length {length_m:.3f} m) "
            f"does not fit inside a {float(body_length_m):.3f} m body tube"
        )

    if planform == "tapered":
        pts = [(0, 0), (0, span_m), (length_m, span_m * 0.4), (length_m, 0)]
    elif planform == "triangular":
        pts = [(0, 0), (0, span_m), (length_m, 0)]
    elif planform == "clipped_delta":
        pts = [(0, 0), (0, span_m), (length_m * 0.7, span_m), (length_m, 0)]
    elif planform == "shallow_sweep":
        sw_off = length_m * 0.3
        pts = [(0, 0), (sw_off, span_m), (length_m, span_m), (length_m, 0)]
    else:
        raise ValueError(
            f"unknown strake planform {planform!r}; expected one of {STRAKE_PLANFORMS}"
        )

    pts_s = "\n".join(f'<point x="{x:.6f}" y="{y:.6f}"/>' for x, y in pts)
    return f'''
          <freeformfinset>
            <name>{name}</name>
            <id>{_component_id(name)}</id>
            <position type="top">{position_from_top_m:.6f}</position>
            {MATERIALS[material_key]}
            <fincount>{count}</fincount>
            <thickness>{thickness:.4f}</thickness>
            <crosssection>airfoil</crosssection>
            <finpoints>{pts_s}</finpoints>
          </freeformfinset>'''


def _haack_radius(x_m, length_m, base_radius_m):
    """Radius of the generated LV-Haack nose at axial station ``x_m``."""
    ratio = min(1.0, max(0.0, x_m / length_m))
    theta = math.acos(1.0 - 2.0 * ratio)
    return base_radius_m * math.sqrt(
        max(0.0, theta - 0.5 * math.sin(2.0 * theta)) / math.pi
    )


def _ballast_cylinder(
    name,
    base_mass_kg,
    pos_from_top_m,
    body_rad_m,
    density=7900,
    max_radius_m=None,
    max_length_m=0.15,
    fixed_radius_m=None,
):
    """Compile requested ballast into a real, material-bearing solid."""
    if base_mass_kg <= 0.001:
        raise ValueError(f"{name} mass must exceed 0.001 kg")
        
    # Use the available airframe cross-section.  The former arbitrary 10 mm
    # radius cap silently truncated every requested ballast above ~0.37 kg,
    # so the optimizer was not evaluating the genome it was given.
    max_pkg_r = body_rad_m - 0.003
    if max_radius_m is not None:
        max_pkg_r = min(max_pkg_r, float(max_radius_m))
    if max_pkg_r < MIN_DIMENSION_M:
        raise ValueError(f"{name} has no legal radial clearance at its axial position")
    if fixed_radius_m is not None:
        pkg_r = float(fixed_radius_m)
        if pkg_r > max_pkg_r + 1.0e-9:
            raise ValueError(
                f"{name} fixed radius {pkg_r:.6f} m exceeds available "
                f"radius {max_pkg_r:.6f} m"
            )
        pkg_l = base_mass_kg / (density * math.pi * pkg_r**2)
        if pkg_l < MIN_DIMENSION_M - 1.0e-9:
            raise ValueError(
                f"{name} shell-bonded thickness {pkg_l:.9f} m is below "
                f"the mission's {MIN_DIMENSION_M:.3f} m minimum"
            )
        if pkg_l > float(max_length_m) + 1.0e-9:
            raise ValueError(
                f"{name} shell-bonded thickness {pkg_l:.6f} m exceeds "
                f"available length {float(max_length_m):.6f} m"
            )
    else:
        # Use the largest available radius unless that would make the cylinder
        # thinner than the legal 1 mm.  In that case reduce radius, not mass.
        ideal_l = base_mass_kg / (density * math.pi * max_pkg_r**2)
        pkg_l = min(max(MIN_DIMENSION_M, ideal_l), float(max_length_m))
        pkg_r = math.sqrt(base_mass_kg / (density * math.pi * pkg_l))
    if pkg_r > max_pkg_r + 1.0e-9:
        raise ValueError(
            f"{name} ({base_mass_kg:.3f} kg) does not fit inside radius {body_rad_m:.3f} m"
        )
    if pkg_r < MIN_DIMENSION_M:
        raise ValueError(f"{name} radius is below the mission's 1 mm minimum")

    return AxialCylinder(
        name=name,
        role="ballast",
        axial_start_m=float(pos_from_top_m),
        length_m=pkg_l,
        radius_m=pkg_r,
        density_kg_m3=float(density),
        declared_mass_kg=float(base_mass_kg),
    )


def _ballast_xml(cylinder, material_key="steel"):
    """Serialize centered ballast as an inspectable structural bulkhead."""
    if not isinstance(cylinder, AxialCylinder) or cylinder.role != "ballast":
        raise TypeError("ballast XML requires a compiled ballast cylinder")
    if material_key not in BALLAST_MATERIAL_DENSITY_KG_M3:
        raise ValueError(f"unsupported ballast material {material_key!r}")
    expected_density = BALLAST_MATERIAL_DENSITY_KG_M3[material_key]
    if not math.isclose(
        float(cylinder.density_kg_m3 or 0.0),
        expected_density,
        rel_tol=0.0,
        abs_tol=1.0e-9,
    ):
        raise ValueError(
            f"{cylinder.name} density does not match {material_key}"
        )
    return f'''
            <bulkhead>
              <name>{cylinder.name}</name>
              <id>{_component_id(cylinder.name)}</id>
              <position type="top">{cylinder.axial_start_m:.9f}</position>
              {MATERIALS[material_key]}
              <length>{cylinder.length_m:.9f}</length>
              <radialposition>{cylinder.radial_position_m:.9f}</radialposition>
              <radialdirection>{cylinder.radial_direction_deg:.9f}</radialdirection>
              <outerradius>{cylinder.radius_m:.9f}</outerradius>
            </bulkhead>'''


def _ballast_rods_xml(name, rods):
    """Serialize three independent rods so OpenRocket preserves their inertia."""
    rods = tuple(rods)
    if len(rods) != 3:
        raise ValueError("Falcon ballast rod cluster requires exactly three rods")
    return "\n".join(
        f'''
            <innertube>
              <name>{rod.name}</name>
              <id>{_component_id(rod.name)}</id>
              <position type="top">{rod.axial_start_m:.9f}</position>
              {MATERIALS["steel"]}
              <length>{rod.length_m:.9f}</length>
              <radialposition>{rod.radial_position_m:.9f}</radialposition>
              <radialdirection>{rod.radial_direction_deg:.9f}</radialdirection>
              <outerradius>{rod.radius_m:.9f}</outerradius>
              <thickness>{rod.radius_m:.9f}</thickness>
              <clusterconfiguration>single</clusterconfiguration>
              <clusterscale>1.0</clusterscale>
              <clusterrotation>0.0</clusterrotation>
            </innertube>'''
        for rod in rods
    )


def _falcon_stage_layout(p, stage):
    """Compile one stage's motors and body ballast into validated solids.

    A stage whose ``{stage}_main`` is None carries no ascent cluster at all —
    just the single central retro/landing motor. Physically this is a stage
    that never flies under its own power during ascent (it rides the stack)
    and only burns its central motor for the landing.
    """
    body_length = float(p[f"{stage}_body_len"])
    body_radius = float(p[f"{stage}_body_rad"])
    body_inner_radius = body_radius - 0.002
    rings_enabled = bool(p.get("octaweb_rings", False))
    ring_annulus = (
        float(p.get("octaweb_ring_width_m", 0.003)) if rings_enabled else 0.0
    )

    if p.get(f"{stage}_main") is None:
        retro_mount_radius = (
            MOTOR_DATABASE[p[f"{stage}_retro"]][2] / 2.0
            + MOTOR_TUBE_WALL_M
            + MOTOR_INSERTION_CLEARANCE_M
        )
        cage = {
            "cluster_scale": 1.0,
            "center_distance_m": 0.0,
            "main_outer_radius_m": 0.0,
            "retro_sleeve_outer_radius_m": retro_mount_radius,
            "retro_sleeve_wall_m": MOTOR_TUBE_WALL_M,
            "body_inner_radius_m": body_inner_radius,
        }
        motors = (
            AxialCylinder(
                name="Central retro motor mount",
                role="motor_mount",
                axial_start_m=0.0,
                length_m=body_length,
                radius_m=retro_mount_radius,
            ),
        )
    else:
        if int(p.get("main_cluster_count", 1)) != 3:
            raise ValueError(
                "physical geometry gate: the current dual-purpose motor topology "
                "requires an explicit 3+1 cluster"
            )
        main = MOTOR_DATABASE[p[f"{stage}_main"]]
        cage = _falcon_cluster_geometry(
            p[f"{stage}_main"],
            p[f"{stage}_retro"],
            body_radius,
            ring_annulus_m=ring_annulus,
        )
        main_mount_radius = cage["main_outer_radius_m"]
        retro_mount_radius = cage["retro_sleeve_outer_radius_m"]
        center_distance = cage["center_distance_m"]
        motors = falcon_cluster_cylinders(
            body_length_m=body_length,
            main_mount_length_m=main[3] + 0.02,
            main_mount_radius_m=main_mount_radius,
            retro_mount_length_m=body_length,
            retro_mount_radius_m=retro_mount_radius,
            center_distance_m=center_distance,
        )

    rod_groups = []
    prior_solids = list(motors)
    for location, default_fraction in (("mid", 0.55), ("aft", 0.88)):
        mass = float(p.get(f"{stage}_{location}_ballast_kg", 0.0))
        if mass <= 0.001:
            continue
        position = float(
            p.get(f"{stage}_{location}_ballast_pos_m", body_length * default_fraction)
        )
        # Preserve legacy target mass and axial centroid, but replace the
        # impossible full disk with three native, visible structural rods.
        legacy_envelope = _ballast_cylinder(
            f"{stage.upper()} {location.title()} Ballast",
            mass,
            position,
            body_radius,
        )
        rods = falcon_ballast_rods(
            name=legacy_envelope.name,
            total_mass_kg=mass,
            axial_centroid_m=legacy_envelope.axial_centroid_m,
            body_length_m=body_length,
            body_inner_radius_m=body_inner_radius,
            obstacles=tuple(prior_solids),
            rod_radius_m=p.get(f"{stage}_{location}_ballast_rod_radius_m"),
            radial_position_m=None,
            attachment=p.get(
                f"{stage}_{location}_ballast_attachment", "central_bonded"
            ),
        )
        rod_groups.append((legacy_envelope.name, rods))
        prior_solids.extend(rods)

    violations = validate_cylinders(
        body_length,
        body_inner_radius,
        tuple(prior_solids),
    )
    if violations:
        raise ValueError("physical geometry gate: " + "; ".join(violations))
    attachment_violations = validate_attachment_paths(
        body_inner_radius,
        tuple(prior_solids),
        support_ring_inner_radius_m=(
            (
                cage["center_distance_m"] + cage["main_outer_radius_m"]
                if cage["main_outer_radius_m"] > 0.0
                else cage["retro_sleeve_outer_radius_m"]
            )
            if rings_enabled
            else None
        ),
    )
    if attachment_violations:
        raise ValueError(
            "physical attachment gate: " + "; ".join(attachment_violations)
        )
    return {
        "motors": motors,
        "rod_groups": tuple(rod_groups),
        "cage": cage,
    }


def compile_falcon_physical_geometry(p):
    """Return the validated physical layout consumed by XML generation."""
    layouts = {
        "s0": _falcon_stage_layout(p, "s0"),
        "s1": _falcon_stage_layout(p, "s1"),
    }

    nose_length = float(
        p.get("nose_length_m", max(0.25, float(p["s0_body_rad"]) * 10.0))
    )
    nose_position = float(p.get("nose_ballast_pos_m", nose_length * 0.75))
    nose_profile_radius = _haack_radius(
        nose_position, nose_length, float(p["s0_body_rad"])
    )
    attachment = str(p.get("nose_ballast_attachment", "free"))
    material_key = str(p.get("nose_ballast_material", "steel"))
    if material_key not in BALLAST_MATERIAL_DENSITY_KG_M3:
        raise ValueError(f"unsupported nose ballast material {material_key!r}")
    if attachment == "nose_shell_bonded":
        nose_inner_radius = nose_profile_radius - NOSE_SHELL_THICKNESS_M
        fixed_radius = nose_inner_radius
    elif attachment == "free":
        # Historical candidates retain a 1 mm assembly gap beyond the shell.
        nose_inner_radius = nose_profile_radius - (
            NOSE_SHELL_THICKNESS_M + ASSEMBLY_CLEARANCE_M
        )
        fixed_radius = None
    else:
        raise ValueError(
            f"unsupported nose ballast attachment {attachment!r}"
        )
    nose = _ballast_cylinder(
        "Nose Ballast Bulkhead",
        float(p.get("nose_mass_kg", 0.050)),
        nose_position,
        float(p["s0_body_rad"]),
        density=BALLAST_MATERIAL_DENSITY_KG_M3[material_key],
        max_radius_m=nose_inner_radius,
        max_length_m=nose_length - nose_position - 0.01,
        fixed_radius_m=fixed_radius,
    )
    nose_violations = validate_cylinders(
        nose_length,
        (
            nose_inner_radius
            if attachment == "nose_shell_bonded"
            else nose_inner_radius + ASSEMBLY_CLEARANCE_M
        ),
        (nose,),
    )
    if nose_violations:
        raise ValueError("physical nose geometry gate: " + "; ".join(nose_violations))
    layouts["nose"] = nose
    return layouts


def validate_compiled_nose_ballast_attachment(xml, expected_mass_kg=None):
    """Validate a centered ballast bulkhead bonded to the Haack nose shell."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"invalid OpenRocket XML: {exc}"]

    violations = []
    nose = root.find(".//nosecone")
    if nose is None:
        return ["nose cone is missing"]
    bulkheads = nose.findall("./subcomponents/bulkhead")
    if len(bulkheads) != 1:
        return [f"expected exactly one nose ballast bulkhead, found {len(bulkheads)}"]
    ballast = bulkheads[0]
    try:
        nose_length = float(nose.findtext("length"))
        base_radius = float(nose.findtext("aftradius"))
        shell_thickness = float(nose.findtext("thickness"))
        position = float(
            ballast.findtext("position")
            or ballast.findtext("axialoffset")
        )
        length = float(ballast.findtext("length"))
        radius = float(ballast.findtext("outerradius"))
        radial_position = float(ballast.findtext("radialposition") or 0.0)
        material = ballast.find("material")
        density = float(material.get("density"))
    except (AttributeError, TypeError, ValueError) as exc:
        return [f"nose ballast geometry is incomplete: {exc}"]

    if (nose.findtext("shape") or "").strip().lower() != "haack":
        violations.append("nose ballast attachment validator requires a Haack nose")
    inner_wall_radius = (
        _haack_radius(position, nose_length, base_radius) - shell_thickness
    )
    if not math.isclose(radius, inner_wall_radius, rel_tol=0.0, abs_tol=1.0e-6):
        violations.append(
            f"nose ballast floats: radius {radius:.6f} m does not reach "
            f"local inner wall {inner_wall_radius:.6f} m"
        )
    if not math.isclose(radial_position, 0.0, rel_tol=0.0, abs_tol=1.0e-9):
        violations.append("nose ballast bulkhead is not centered")
    if length < MIN_DIMENSION_M - 1.0e-9:
        violations.append(
            f"nose ballast thickness {length:.9f} m is below "
            f"{MIN_DIMENSION_M:.3f} m"
        )
    if position < 0.0 or position + length > nose_length + 1.0e-9:
        violations.append("nose ballast extends outside the nose cone")
    geometric_mass = density * math.pi * radius**2 * length
    if expected_mass_kg is not None and not math.isclose(
        geometric_mass,
        float(expected_mass_kg),
        rel_tol=1.0e-6,
        abs_tol=1.0e-9,
    ):
        violations.append(
            f"nose ballast mass mismatch: geometry {geometric_mass:.9f} kg, "
            f"expected {float(expected_mass_kg):.9f} kg"
        )
    return violations


def validate_candidate_geometry(p):
    """Return static physical violations without invoking OpenRocket."""
    try:
        compile_falcon_physical_geometry(p)
    except (KeyError, TypeError, ValueError) as exc:
        return [str(exc)]
    return []


def validate_compiled_centering_rings(xml):
    """Return structural ring violations found in compiled OpenRocket XML."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"invalid OpenRocket XML: {exc}"]

    violations = []
    seen_ids = set()
    coupler = root.find(
        ".//rocket/subcomponents/stage[name='Booster']/"
        "subcomponents/bodytube/subcomponents/tubecoupler"
    )
    coupler_sustainer_overlap = 0.0
    if coupler is not None:
        position = coupler.find("position")
        axial = coupler.find("axialoffset")
        try:
            coupler_sustainer_overlap = max(
                0.0,
                -float(
                    (position.text if position is not None else None)
                    or (axial.text if axial is not None else None)
                    or 0.0
                ),
            )
        except ValueError:
            violations.append("Booster: interstage coupler position is not numeric")
    for stage in root.findall(".//rocket/subcomponents/stage"):
        stage_name = stage.findtext("name") or "unnamed stage"
        body = stage.find("./subcomponents/bodytube")
        if body is None:
            violations.append(f"{stage_name}: no primary body tube")
            continue
        body_length = float(body.findtext("length"))
        body_inner = float(body.findtext("radius")) - float(body.findtext("thickness"))
        components = body.find("subcomponents")
        rings = components.findall("centeringring") if components is not None else []
        if len(rings) != 2:
            violations.append(
                f"{stage_name}: expected exactly 2 centering rings, got {len(rings)}"
            )
            continue

        mounts = [
            tube
            for tube in components.findall("innertube")
            if tube.find("motormount") is not None
        ]
        main = next(
            (
                tube
                for tube in mounts
                if tube.findtext("clusterconfiguration") == "3-ring"
            ),
            None,
        )
        retro = next(
            (
                tube
                for tube in mounts
                if "Structural Retro Sleeve" in (tube.findtext("name") or "")
            ),
            None,
        )
        if retro is None:
            violations.append(f"{stage_name}: central structural retro sleeve missing")
            continue

        if main is None:
            support_envelope = float(retro.findtext("outerradius"))
            forward_position = 0.0
        else:
            main_outer = float(main.findtext("outerradius"))
            cluster_scale = float(main.findtext("clusterscale"))
            center_distance = 2.0 * main_outer / math.sqrt(3.0) * cluster_scale
            support_envelope = center_distance + main_outer
            mount_length = float(main.findtext("length"))
            position = main.find("position")
            bottom_offset = float(position.text or 0.0)
            forward_position = body_length - bottom_offset - mount_length

        specs = []
        aft_position = body_length - 0.005
        if stage_name == "Sustainer" and coupler_sustainer_overlap > 0.0:
            aft_position -= coupler_sustainer_overlap
        expected_positions = (forward_position, aft_position)
        for ring in rings:
            ring_id = (ring.findtext("id") or "").strip()
            if not ring_id:
                violations.append(f"{stage_name}: centering ring has no stable id")
            elif ring_id in seen_ids:
                violations.append(f"{stage_name}: duplicate centering ring id {ring_id}")
            seen_ids.add(ring_id)
            outer_text = (ring.findtext("outerradius") or "").strip().lower()
            inner_text = (ring.findtext("innerradius") or "").strip().lower()
            outer_radius = (
                body_inner if outer_text == "auto" else float(outer_text)
            )
            if inner_text == "auto":
                # OpenRocket legitimately serializes a ring around one
                # centered tube this way.  Clustered tubes are different:
                # automatic sizing ignores their radial offsets, so those
                # rings must remain explicit.
                inner_radius = support_envelope
                if main is not None:
                    violations.append(
                        f"{stage_name}: clustered centering ring "
                        "inner radius must be explicit, not auto"
                    )
            else:
                inner_radius = float(inner_text)
            specs.append(
                CenteringRingSpec(
                    name=ring.findtext("name") or f"{stage_name} centering ring",
                    axial_start_m=float(ring.findtext("position")),
                    length_m=float(ring.findtext("length")),
                    outer_radius_m=outer_radius,
                    inner_radius_m=inner_radius,
                    radial_position_m=float(ring.findtext("radialposition") or 0.0),
                )
            )
        violations.extend(
            f"{stage_name}: {item}"
            for item in validate_centering_ring_pair(
                body_length,
                body_inner,
                support_envelope,
                specs,
                expected_positions,
                tolerance_m=1.0e-6,
            )
        )
    return violations


def validate_compiled_interstage_coupler(xml, required=False):
    """Validate the saved booster-owned coupler, ring gap, and motor bore."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"invalid OpenRocket XML: {exc}"]

    stages = {
        (stage.findtext("name") or "").strip(): stage
        for stage in root.findall(".//rocket/subcomponents/stage")
    }
    sustainer = stages.get("Sustainer")
    booster = stages.get("Booster")
    if sustainer is None or booster is None:
        return ["interstage coupler validator requires Sustainer and Booster stages"]

    all_couplers = root.findall(".//tubecoupler")
    if not required:
        return [] if not all_couplers else [
            f"interstage coupler is disabled but {len(all_couplers)} coupler(s) exist"
        ]
    if len(all_couplers) != 1:
        return [f"expected exactly one interstage coupler, found {len(all_couplers)}"]

    s0_body = sustainer.find("./subcomponents/bodytube")
    s1_body = booster.find("./subcomponents/bodytube")
    if s0_body is None or s1_body is None:
        return ["interstage coupler validator requires one body tube per stage"]
    owner_couplers = s1_body.findall("./subcomponents/tubecoupler")
    if len(owner_couplers) != 1 or owner_couplers[0] is not all_couplers[0]:
        return ["interstage coupler must be owned by the Booster body tube"]

    coupler = all_couplers[0]
    violations = []

    def component_position(component):
        position = component.find("position")
        if position is not None:
            return float(position.text or 0.0), (
                position.get("type") or "top"
            ).strip().lower()
        axial = component.find("axialoffset")
        if axial is not None:
            return float(axial.text or 0.0), (
                axial.get("method") or "top"
            ).strip().lower()
        return 0.0, "top"

    try:
        s0_length = float(s0_body.findtext("length"))
        s1_length = float(s1_body.findtext("length"))
        s0_inner = float(s0_body.findtext("radius")) - float(
            s0_body.findtext("thickness")
        )
        s1_inner = float(s1_body.findtext("radius")) - float(
            s1_body.findtext("thickness")
        )
        position, position_type = component_position(coupler)
        length = float(coupler.findtext("length"))
        thickness = float(coupler.findtext("thickness"))
        outer_text = (coupler.findtext("outerradius") or "").strip().lower()
        outer_radius = s1_inner if outer_text == "auto" else float(outer_text)
    except (TypeError, ValueError) as exc:
        return [f"interstage coupler geometry is incomplete: {exc}"]

    if position_type != "top":
        violations.append("interstage coupler position must be measured from booster top")
    if length < MIN_DIMENSION_M - 1.0e-9:
        violations.append(
            f"interstage coupler length {length:.9f} m is below {MIN_DIMENSION_M:.3f} m"
        )
    if thickness < MIN_DIMENSION_M - 1.0e-9:
        violations.append(
            f"interstage coupler wall {thickness:.9f} m is below {MIN_DIMENSION_M:.3f} m"
        )
    inner_radius = outer_radius - thickness
    if not math.isclose(outer_radius, s0_inner, rel_tol=0.0, abs_tol=1.0e-6):
        violations.append(
            f"coupler outer radius {outer_radius:.6f} m does not touch "
            f"sustainer bore {s0_inner:.6f} m"
        )
    if not math.isclose(outer_radius, s1_inner, rel_tol=0.0, abs_tol=1.0e-6):
        violations.append(
            f"coupler outer radius {outer_radius:.6f} m does not touch "
            f"booster bore {s1_inner:.6f} m"
        )

    sustainer_overlap = -position
    booster_overlap = position + length
    if sustainer_overlap < MIN_DIMENSION_M - 1.0e-9:
        violations.append(
            f"coupler sustainer overlap {sustainer_overlap:.9f} m is below "
            f"{MIN_DIMENSION_M:.3f} m"
        )
    if booster_overlap < MIN_DIMENSION_M - 1.0e-9:
        violations.append(
            f"coupler booster overlap {booster_overlap:.9f} m is below "
            f"{MIN_DIMENSION_M:.3f} m"
        )

    s0_region = (s0_length - sustainer_overlap, s0_length)
    s1_region = (0.0, booster_overlap)

    def validate_body_bore(body, body_length, region, stage_name):
        for tube in body.findall("./subcomponents/innertube"):
            try:
                tube_position, tube_position_type = component_position(tube)
                tube_length = float(tube.findtext("length"))
                if tube_position_type == "bottom":
                    tube_start = body_length - tube_position - tube_length
                else:
                    tube_start = tube_position
                tube_end = tube_start + tube_length
                axial_overlap = (
                    max(region[0], tube_start)
                    < min(region[1], tube_end) - 1.0e-9
                )
                if not axial_overlap:
                    continue
                tube_outer = float(tube.findtext("outerradius"))
                cluster = (tube.findtext("clusterconfiguration") or "single").strip()
                if cluster == "3-ring":
                    scale = float(tube.findtext("clusterscale") or 1.0)
                    center_distance = 2.0 * tube_outer / math.sqrt(3.0) * scale
                    envelope = center_distance + tube_outer
                else:
                    envelope = (
                        float(tube.findtext("radialposition") or 0.0) + tube_outer
                    )
            except (TypeError, ValueError) as exc:
                violations.append(
                    f"{stage_name}: cannot resolve internal tube clearance: {exc}"
                )
                continue
            if envelope > inner_radius + 1.0e-6:
                violations.append(
                    f"{stage_name}: {tube.findtext('name') or 'inner tube'} reaches "
                    f"{envelope:.6f} m into coupler bore {inner_radius:.6f} m"
                )

    validate_body_bore(s0_body, s0_length, s0_region, "Sustainer")
    validate_body_bore(s1_body, s1_length, s1_region, "Booster")

    for ring in s0_body.findall("./subcomponents/centeringring"):
        try:
            ring_position, ring_position_type = component_position(ring)
            ring_length = float(ring.findtext("length"))
            ring_start = (
                s0_length - ring_position - ring_length
                if ring_position_type == "bottom"
                else ring_position
            )
            ring_end = ring_start + ring_length
        except (TypeError, ValueError) as exc:
            violations.append(f"cannot resolve sustainer ring position: {exc}")
            continue
        if max(ring_start, s0_region[0]) < min(ring_end, s0_region[1]) - 1.0e-9:
            violations.append(
                f"{ring.findtext('name') or 'sustainer ring'} overlaps inserted "
                f"coupler region [{s0_region[0]:.6f}, {s0_region[1]:.6f}] m"
            )

    return violations


def validate_upper_stage_ignition_after_separation(xml):
    """Prove that no sustainer motor can ignite while the stages are coupled."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"invalid OpenRocket XML: {exc}"]

    stages = {
        (stage.findtext("name") or "").strip(): stage
        for stage in root.findall(".//rocket/subcomponents/stage")
    }
    sustainer = stages.get("Sustainer")
    booster = stages.get("Booster")
    if sustainer is None or booster is None:
        return ["ignition-order validator requires Sustainer and Booster stages"]

    separation_event = (booster.findtext("separationevent") or "").strip().lower()
    try:
        separation_delay = float(booster.findtext("separationdelay") or 0.0)
    except ValueError:
        return ["booster separation delay is not numeric"]

    violations = []
    mounts = sustainer.findall("./subcomponents/bodytube/subcomponents/innertube")
    for mount in mounts:
        motor_mount = mount.find("motormount")
        if motor_mount is None:
            continue
        name = mount.findtext("name") or "sustainer motor"
        ignition_event = (
            motor_mount.findtext("ignitionevent") or ""
        ).strip().lower()
        try:
            ignition_delay = float(motor_mount.findtext("ignitiondelay") or 0.0)
        except ValueError:
            violations.append(f"{name}: ignition delay is not numeric")
            continue

        proven_after = False
        if separation_event == "launch" and ignition_event == "launch":
            proven_after = ignition_delay > separation_delay + 1.0e-9
        elif separation_event == ignition_event and separation_event in {
            "burnout",
            "ignition",
            "apogee",
        }:
            proven_after = ignition_delay > separation_delay + 1.0e-9
        elif ignition_event == "never":
            proven_after = True

        if not proven_after:
            violations.append(
                f"{name}: cannot prove ignition {ignition_event}+"
                f"{ignition_delay:.6f}s occurs after separation "
                f"{separation_event}+{separation_delay:.6f}s"
            )
    return violations


# ═══════════════════════════════════════════════════════════════
# Full .ORK XML generator — 2-stage retro, 2 motors per stage
# ═══════════════════════════════════════════════════════════════
def generate_ork(p):
    """Generate complete .ork XML string for a 2-stage retro rocket.

    Parameters (dict p):
      s0_main, s0_retro       — sustainer motor indices
      s1_main, s1_retro       — booster motor indices
      s0_body_len, s0_body_rad  — sustainer body (m)
      s1_body_len, s1_body_rad  — booster body (m)
      s0_retro_delay          — sustainer retro ignition delay from launch (s)
      s1_retro_delay          — booster retro ignition delay from launch (s)
      s0_fin_count, s0_fin_sweep, s0_fin_root, s0_fin_height
      s1_fin_count, s1_fin_sweep, s1_fin_root, s1_fin_height
      nose_mass_kg            — nose cone ballast mass (kg)
      s0_mid_ballast_kg       — sustainer mid-body ballast (for CG tuning)
      s0_aft_ballast_kg       — sustainer aft ballast (for tumble orientation)
      s1_mid_ballast_kg       — booster mid-body ballast
      s1_aft_ballast_kg       — booster aft ballast
      launch_azimuth          — launch direction degrees (0=North, 90=East)
      launch_angle_deg        — launch rod inclination from vertical (0=vertical)
      s1_separation_event     — OpenRocket stage-separation trigger
      s1_separation_delay     — delay after the selected trigger (s)
      wind_levels             — list of (alt, spd, dir, std)
    """
    # Keep the motor-configuration identity fixed across the entire search.
    # OpenRocket mixes this ID into stochastic model state; deriving it from
    # candidate parameters made adjacent ballast values sample different wind
    # realizations even with the official fixed random seed.
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, "l2-osifog/falcon/official"))

    s0_main_len  = MOTOR_DATABASE[p["s0_main"]][3] if p.get("s0_main") is not None else 0.0
    s0_retro_len = MOTOR_DATABASE[p["s0_retro"]][3]
    s1_main_len  = MOTOR_DATABASE[p["s1_main"]][3]
    s1_retro_len = MOTOR_DATABASE[p["s1_retro"]][3]

    s0r = p["s0_body_rad"]
    s1r = p["s1_body_rad"]
    main_cluster_count = int(p.get("main_cluster_count", 1))
    if main_cluster_count not in (1, 3):
        raise ValueError("main_cluster_count must be 1 or 3")
    if main_cluster_count == 3:
        for stage_name, body_radius, main_index, retro_index in (
            ("sustainer", s0r, p.get("s0_main"), p["s0_retro"]),
            ("booster", s1r, p["s1_main"], p["s1_retro"]),
        ):
            if main_index is None:
                continue
            try:
                _falcon_cluster_geometry(
                    main_index,
                    retro_index,
                    body_radius,
                    ring_annulus_m=(
                        float(p.get("octaweb_ring_width_m", 0.003))
                        if p.get("octaweb_rings", False)
                        else 0.0
                    ),
                )
            except ValueError as exc:
                raise ValueError(f"{stage_name} {exc}") from exc
    cluster_name = "3-ring" if main_cluster_count == 3 else "single"
    physical_layouts = compile_falcon_physical_geometry(p)
    coupler = _interstage_coupler_geometry(p, physical_layouts)

    nose_mass_kg      = p.get("nose_mass_kg", 0.050)
    s0_mid_ballast_kg = p.get("s0_mid_ballast_kg", 0.0)
    s0_aft_ballast_kg = p.get("s0_aft_ballast_kg", 0.0)
    s1_mid_ballast_kg = p.get("s1_mid_ballast_kg", 0.0)
    s1_aft_ballast_kg = p.get("s1_aft_ballast_kg", 0.0)

    launch_azimuth    = p.get("launch_azimuth", 270.0)   # Default: into WNW wind
    launch_angle_deg  = p.get("launch_angle_deg", 0.0)   # 0 = vertical

    # Opt-in: emit every motor as plugged (<delay>none</delay>). Default False
    # preserves reproducibility of every batch run before 2026-07-24 evening.
    # Set True for submission builds -- see _motor_mount_xml's docstring.
    plugged_motors    = bool(p.get("plugged_motors", False))
    livery = p.get("livery")

    # ── Nose cone (sustainer only) ──
    nose_len = float(p.get("nose_length_m", max(0.25, s0r * 10)))
    nose_ballast_pos = float(p.get("nose_ballast_pos_m", nose_len * 0.75))
    nose_ballast_material = str(p.get("nose_ballast_material", "steel"))
    nose_xml = f'''
        <nosecone>
          <name>Nose Cone</name>
          <id>{_component_id("Nose Cone")}</id>
          <finish>normal</finish>
          {_component_appearance(livery, "Nose Cone")}
          {MATERIALS["fiberglass"]}
          <length>{nose_len:.6f}</length>
          <thickness>0.002</thickness>
          <shape>haack</shape>
          <shapeclipped>false</shapeclipped>
          <aftradius>{s0r:.6f}</aftradius>
          <aftshoulderlength>0.03</aftshoulderlength>
          <aftshoulderradius>{s0r - 0.003:.6f}</aftshoulderradius>
          <aftshoulderthickness>0.002</aftshoulderthickness>
          <aftshouldercapped>false</aftshouldercapped>
          <subcomponents>
            {_ballast_xml(physical_layouts["nose"], nose_ballast_material)}
          </subcomponents>
        </nosecone>'''

    # ── Stage 0 (sustainer) ──
    # Falcon topology: three symmetric ascent motors around one central retro
    # motor, all nozzles on the aft plane.  Radial separation means these
    # mounts share an axial station without occupying the same volume.
    s0_retro_pos = 0.0
    s0_cage = physical_layouts["s0"]["cage"]
    s0_cluster_scale = s0_cage["cluster_scale"]
    # OpenRocket has no "stage-separation" ignition event.  For an upper
    # stage, BURNOUT means burnout of the immediately lower stage.
    # s0_main None => sustainer carries no ascent cluster at all (it rides
    # the stack unpowered and only burns its central motor for landing).
    if p.get("s0_main") is None:
        s0_main_xml = ""
    else:
        # Default "burnout"+0 is the historical hot-staging ascent cluster.
        # Setting s0_main_ignition_event="launch" with a late s0_main_delay
        # instead repurposes the sustainer's octaweb as a second, independently
        # timed retro cluster: a high-thrust burn can kill most of the ~176 m/s
        # descent, leaving the low-thrust central motor to make the final
        # arrest with a wide ignition window.  All motors thrust nose-ward, so
        # a cluster motor brakes exactly like the central retro does.
        s0_main_xml = _motor_mount_xml(
            p["s0_main"], cid,
            p.get("s0_main_ignition_event", "burnout"),
            float(p.get("s0_main_delay", 0.0)), 0.0,
            cluster=cluster_name,
            cluster_scale=s0_cluster_scale if main_cluster_count == 3 else 1.0,
            component_name="Sustainer Main Motor Mount",
            wall_thickness_m=MOTOR_TUBE_WALL_M,
            motor_clearance_m=MOTOR_INSERTION_CLEARANCE_M,
            plugged=plugged_motors,
        )
    s0_retro_xml = _motor_mount_xml(p["s0_retro"], cid, "launch",
                                     p["s0_retro_delay"], s0_retro_pos,
                                     component_name="Sustainer Structural Retro Sleeve",
                                     wall_thickness_m=s0_cage["retro_sleeve_wall_m"],
                                     motor_clearance_m=MOTOR_INSERTION_CLEARANCE_M,
                                     mount_length_m=p["s0_body_len"],
                                     plugged=plugged_motors)

    s0_fins = _fin_xml(
        p.get("s0_fin_count", 4), p.get("s0_fin_sweep", 25),
        p.get("s0_fin_root", max(0.10, s0r * 3.0)),
        p.get("s0_fin_height", max(0.05, s0r * 1.5)),
        "Sustainer Fins",
        thickness=p.get("s0_fin_thickness_m", 0.003),
        material_key=p.get("s0_fin_material", "fiberglass"),
        position_from_top_m=p.get("s0_fin_position_m"),
        appearance=_component_appearance(livery, "Sustainer Fins"),
    )
    s0_grid_fins = _fin_xml(
        p.get("s0_grid_fin_count", 0),
        p.get("s0_grid_fin_sweep", 0.0),
        p.get("s0_grid_fin_root", 0.06),
        p.get("s0_grid_fin_height", 0.06),
        "Sustainer Forward Grid Fins",
        thickness=p.get("s0_grid_fin_thickness_m", 0.001),
        material_key=p.get("s0_grid_fin_material", "fiberglass"),
        position_from_top_m=p.get("s0_grid_fin_position_m", 0.03),
        appearance=_component_appearance(livery, "Sustainer Forward Grid Fins"),
    )
    s0_strakes = _strake_xml(
        p.get("s0_strake_count", 0),
        p.get("s0_strake_planform", "tapered"),
        p.get("s0_strake_length_m", 0.0),
        p.get("s0_strake_span_m", 0.0),
        p["s0_body_len"],
        "Sustainer Strakes",
        thickness=p.get("s0_strake_thickness_m", 0.003),
        material_key=p.get("s0_strake_material", "fiberglass"),
        position_from_top_m=p.get("s0_strake_position_m", 0.0),
    )
    # Ballast: mid at 60% of body length from nose, aft at 90%
    s0_body_len = p["s0_body_len"]
    s0_ballast_xml = "\n".join(
        _ballast_rods_xml(name, rods)
        for name, rods in physical_layouts["s0"]["rod_groups"]
    )

    # Two centered airframe-spanning support rings, opt-in so historical batch
    # inputs remain reproducible. Submission builds require this flag.
    s0_octaweb_xml = ""
    if main_cluster_count == 3 and p.get("octaweb_rings", False):
        s0_forward_ring_position = (
            s0_body_len - (s0_main_len + 0.02)
            if p.get("s0_main") is not None
            else 0.0
        )
        s0_aft_ring_position = (
            s0_body_len - coupler["sustainer_overlap_m"] - 0.005
            if coupler is not None
            else s0_body_len - 0.005
        )
        s0_octaweb_xml = _octaweb_rings_xml(
            s0_cage,
            "Sustainer Motor Cage Ring",
            s0_body_len,
            s0_forward_ring_position,
            aft_position_top_m=s0_aft_ring_position,
        )

    stage0 = f'''
      <stage>
        <name>Sustainer</name>
        <id>{_component_id("Sustainer")}</id>
        <subcomponents>
          {nose_xml}
          <bodytube>
            <name>Sustainer Airframe</name>
            <id>{_component_id("Sustainer Airframe")}</id>
            <finish>normal</finish>
            {_component_appearance(livery, "Sustainer Airframe")}
            {MATERIALS["cardboard"]}
            <length>{s0_body_len:.6f}</length>
            <thickness>0.002</thickness>
            <radius>{s0r:.6f}</radius>
            <subcomponents>
              {s0_main_xml}
              {s0_retro_xml}
              {s0_fins}
              {s0_grid_fins}
              {s0_strakes}
              {s0_ballast_xml}
              {s0_octaweb_xml}
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>'''

    # ── Stage 1 (booster) ──
    s1_body_len  = p["s1_body_len"]
    s1_retro_pos = 0.0
    s1_cage = physical_layouts["s1"]["cage"]
    s1_cluster_scale = s1_cage["cluster_scale"]
    s1_main_xml  = _motor_mount_xml(
        p["s1_main"], cid, "launch", 0.0, 0.0,
        cluster=cluster_name,
        cluster_scale=s1_cluster_scale if main_cluster_count == 3 else 1.0,
        component_name="Booster Main Motor Mount",
        wall_thickness_m=MOTOR_TUBE_WALL_M,
        motor_clearance_m=MOTOR_INSERTION_CLEARANCE_M,
        plugged=plugged_motors,
    )
    s1_retro_xml = _motor_mount_xml(p["s1_retro"], cid, "launch",
                                     p["s1_retro_delay"], s1_retro_pos,
                                     component_name="Booster Structural Retro Sleeve",
                                     wall_thickness_m=s1_cage["retro_sleeve_wall_m"],
                                     motor_clearance_m=MOTOR_INSERTION_CLEARANCE_M,
                                     mount_length_m=p["s1_body_len"],
                                     plugged=plugged_motors)

    s1_fins = _fin_xml(
        p.get("s1_fin_count", 4), p.get("s1_fin_sweep", 25),
        p.get("s1_fin_root", max(0.15, s1r * 5.5)),
        p.get("s1_fin_height", max(0.08, s1r * 2.8)),
        "Booster Fins",
        thickness=p.get("s1_fin_thickness_m", 0.003),
        material_key=p.get("s1_fin_material", "fiberglass"),
        position_from_top_m=p.get("s1_fin_position_m"),
        appearance=_component_appearance(livery, "Booster Fins"),
    )
    s1_grid_fins = _fin_xml(
        p.get("s1_grid_fin_count", 0),
        p.get("s1_grid_fin_sweep", 0.0),
        p.get("s1_grid_fin_root", 0.10),
        p.get("s1_grid_fin_height", 0.08),
        "Booster Forward Grid Fins",
        thickness=p.get("s1_grid_fin_thickness_m", 0.003),
        material_key=p.get("s1_grid_fin_material", "fiberglass"),
        position_from_top_m=p.get("s1_grid_fin_position_m", 0.03),
        appearance=_component_appearance(livery, "Booster Forward Grid Fins"),
    )

    s1_strakes = _strake_xml(
        p.get("s1_strake_count", 0),
        p.get("s1_strake_planform", "tapered"),
        p.get("s1_strake_length_m", 0.0),
        p.get("s1_strake_span_m", 0.0),
        p["s1_body_len"],
        "Booster Strakes",
        thickness=p.get("s1_strake_thickness_m", 0.003),
        material_key=p.get("s1_strake_material", "fiberglass"),
        position_from_top_m=p.get("s1_strake_position_m", 0.0),
    )
    s1_ballast_xml = "\n".join(
        _ballast_rods_xml(name, rods)
        for name, rods in physical_layouts["s1"]["rod_groups"]
    )
    s1_coupler_xml = ""
    if coupler is not None:
        s1_coupler_xml = _tube_coupler_xml(
            "Booster-Retained Interstage Coupler",
            coupler["booster_position_top_m"],
            coupler["outer_radius_m"],
            coupler["inner_radius_m"],
            length_m=coupler["length_m"],
            appearance=_component_appearance(
                livery, "Booster-Retained Interstage Coupler"
            ),
        )

    s1_octaweb_xml = ""
    if main_cluster_count == 3 and p.get("octaweb_rings", False):
        s1_ascent_mount_len = s1_main_len + 0.02
        s1_octaweb_xml = _octaweb_rings_xml(
            s1_cage,
            "Booster Octaweb Ring",
            s1_body_len,
            s1_body_len - s1_ascent_mount_len,
        )

    separation_event = str(
        p.get("s1_separation_event", "burnout")
    ).strip().lower()
    if separation_event not in SEPARATION_EVENT_XML_NAMES:
        raise ValueError(
            "s1_separation_event must be one of "
            + ", ".join(sorted(SEPARATION_EVENT_XML_NAMES))
        )

    stage1 = f'''
      <stage>
        <name>Booster</name>
        <id>{_component_id("Booster")}</id>
        <separationevent>{separation_event}</separationevent>
        <separationdelay>{p.get("s1_separation_delay", 0.0):.3f}</separationdelay>
        <subcomponents>
          <bodytube>
            <name>Booster Airframe</name>
            <id>{_component_id("Booster Airframe")}</id>
            <finish>normal</finish>
            {_component_appearance(livery, "Booster Airframe")}
            {MATERIALS["cardboard"]}
            <length>{s1_body_len:.6f}</length>
            <thickness>0.002</thickness>
            <radius>{s1r:.6f}</radius>
            <subcomponents>
              {s1_coupler_xml}
              {s1_main_xml}
              {s1_retro_xml}
              {s1_fins}
              {s1_grid_fins}
              {s1_strakes}
              {s1_ballast_xml}
              {s1_octaweb_xml}
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>'''

    # ── Wind XML ──
    wl = p["wind_levels"]
    ws, wd, wstd = wl[0][1], wl[0][2], wl[0][3]
    wl_xml = "\n".join(
        f'          <windlevel altitude="{a}" speed="{s}" '
        # OpenRocket's CSV importer presents degrees to the user but its .ork
        # persistence format stores angles in radians.  Serializing the CSV
        # degree value directly silently rotates every wind level.
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

    # Identity metadata only -- name/id/designer/comment never reach the physics.
    # Defaults reproduce every pre-2026-07-26 artifact byte-for-byte.
    rocket_name = str(p.get("rocket_name", "OSIFOG Level 3 Falcon"))
    rocket_designer = str(p.get("rocket_designer", "L2 Systems AI"))
    rocket_comment = str(p.get("rocket_comment", "")).strip()
    comment_xml = (
        f"\n    <comment>{_xml_escape(rocket_comment)}</comment>"
        if rocket_comment else ""
    )

    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.6" creator="L2-OSIFOG-Sweep">
  <rocket>
    <name>{_xml_escape(rocket_name)}</name>
    <id>{_component_id(rocket_name)}</id>
    <designer>{_xml_escape(rocket_designer)}</designer>{comment_xml}
    <motorconfiguration configid="{cid}" default="true"/>
    <referencetype>maximum</referencetype>
    <subcomponents>
      {stage0}
      {stage1}
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="notsimulated">
      <name>OSIFOG Level 3</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{cid}</configid>
        <launchrodlength>{LAUNCH_ROD_M}</launchrodlength>
        <launchintowind>false</launchintowind>
        <!-- VERIFIED 2026-07-24 by round-trip probe: unlike <windlevel
             direction=...> (radians), OpenRocket 24.12 persists these two rod
             fields in DEGREES. Writing radians here silently shrank every
             commanded rail vector by 57x (4 deg became 0.0698 deg), which is
             why an azimuth sweep produced identical trajectories. -->
        <launchrodangle>{launch_angle_deg:.9f}</launchrodangle>
        <launchroddirection>{launch_azimuth:.9f}</launchroddirection>
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
    if p.get("octaweb_rings", False):
        ring_violations = validate_compiled_centering_rings(xml)
        if ring_violations:
            raise ValueError(
                "compiled centering-ring gate: " + "; ".join(ring_violations)
            )
    return xml


# ═══════════════════════════════════════════════════════════════
# OpenRocket 24.12 runner (direct JPype — no orhelper dependency)
# orhelper targets OR 15.03 (net.sf.openrocket) which is incompatible
# with OR 24.12 (info.openrocket). We initialize directly via JPype.
# ═══════════════════════════════════════════════════════════════
_or_initialized = False
_or_Application = None


def init_or():
    """Initialize OpenRocket 24.12 JVM via direct JPype (not orhelper).
    Returns an opaque handle (the Application class) that run_sim() uses.
    Safe to call multiple times — JVM is only started once.
    """
    global _or_initialized, _or_Application
    if _or_initialized:
        return _or_Application

    import jpype
    import jpype.imports

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    jar = os.path.join(repo_root, "lib", "OpenRocket-24.12.jar")
    if not os.path.exists(jar):
        raise FileNotFoundError(f"OpenRocket JAR not found: {jar}")

    if not jpype.isJVMStarted():
        jpype.startJVM(
            jpype.getDefaultJVMPath(),
            "-ea",
            "-Djava.awt.headless=true",
            "-Dpolyglot.engine.WarnInterpreterOnly=false",
            f"-Djava.class.path={jar}",
            convertStrings=False,
        )

    # Batch execution should emit only actionable failures.  OpenRocket's
    # default DEBUG/INFO output otherwise dominates optimizer I/O.
    LoggerFactory = jpype.JClass("org.slf4j.LoggerFactory")
    Logger = jpype.JClass("ch.qos.logback.classic.Logger")
    Level = jpype.JClass("ch.qos.logback.classic.Level")
    LoggerFactory.getLogger(Logger.ROOT_LOGGER_NAME).setLevel(Level.ERROR)

    # OpenRocket 24.12's CoreModule is not self-sufficient in the distributed
    # jar (its plugin multibindings are incomplete), so use the same service
    # module as orhelper while forcing AWT headless.  Crucially, block on both
    # database loaders before touching Application; polling the blocking motor
    # provider while the preset loader was still active caused startup hangs.
    guice = jpype.JClass("com.google.inject.Guice")
    GuiModule = jpype.JClass("info.openrocket.swing.startup.GuiModule")
    PluginModule = jpype.JClass("info.openrocket.core.plugin.PluginModule")
    Application = jpype.JClass("info.openrocket.core.startup.Application")
    gui_module = GuiModule()
    injector = guice.createInjector(gui_module, PluginModule())
    Application.setInjector(injector)
    gui_module.startLoader()

    from orhelper._orhelper import _get_private_field
    _get_private_field(gui_module, "presetLoader").blockUntilLoaded()
    _get_private_field(gui_module, "motorLoader").blockUntilLoaded()

    _or_Application = Application
    _or_initialized = True
    try:
        n_motor_sets = sum(1 for _ in Application.getMotorSetDatabase().getMotorSets())
    except Exception:
        n_motor_sets = -1
    print(f"  [OR] OpenRocket 24.12 JVM started. Motor sets: {n_motor_sets}")
    return _or_Application



def _get_anti_tumble_listener():
    """Create a fresh listener that ignores TUMBLE events.

    ``ScriptingSimulationListener`` carries per-run engine state and must not
    be shared across candidate simulations.  Reusing one listener made an
    otherwise identical rocket depend on which candidate ran immediately
    before it.

    Official Anti-Tumbling Extension from OSIFOG Level 3 mission PDF.
    """
    import jpype
    # Use OpenRocket's own Graal factory.  The generic javax manager creates a
    # sandboxed engine without Java host access, so the official script cannot
    # call FlightEvent.getType().
    factory_class = jpype.JClass(
        "info.openrocket.core.scripting.GraalJSScriptEngineFactory"
    )
    engine = factory_class().getScriptEngine()
    engine.eval(ANTI_TUMBLE_SCRIPT)
    SSL = jpype.JClass(
        "info.openrocket.core.simulation.extension.impl.ScriptingSimulationListener"
    )
    return SSL(engine)


def _load_ork_doc(path: str):
    """Load an .ork file using OR 24.12 GeneralRocketLoader."""
    import jpype
    GeneralRocketLoader = jpype.JClass("info.openrocket.core.file.GeneralRocketLoader")
    File = jpype.JClass("java.io.File")
    loader = GeneralRocketLoader(File(path))
    return loader.load()


def _seed_multilevel_wind(options, seed: int) -> None:
    """Deterministically seed every OpenRocket multilevel pink-noise model.

    OpenRocket 24.12's ``SimulationOptions.setRandomSeed`` seeds the flight
    integrator but not the independently constructed PinkNoiseWindModel at
    each imported wind level.  Seed those models from the same simulation
    seed so identical XML produces identical official-wind results.
    """
    import jpype

    levels = options.getMultiLevelWindModel().getLevels()
    pink_noise_model = jpype.JClass(
        "info.openrocket.core.models.wind.PinkNoiseWindModel"
    )
    for index in range(int(levels.size())):
        level = levels.get(index)
        model_field = level.getClass().getDeclaredField("model")
        model_field.setAccessible(True)
        model = model_field.get(level)
        level_seed = int(seed) ^ ((index + 1) * 0x45D9F3B)
        # Replace the model instead of mutating its final seed.  HotSpot may
        # treat a final field as stable after the first run; changing it by
        # reflection made identical inputs alternate between trajectories.
        deterministic = pink_noise_model(level_seed)
        deterministic.setAverage(model.getAverage())
        deterministic.setDirection(model.getDirection())
        deterministic.setStandardDeviation(model.getStandardDeviation())
        model_field.set(level, deterministic)


def _finite_difference(values, times, index):
    """Return a centered derivative, falling back to a one-sided endpoint."""
    count = len(values)
    if count < 2:
        return 0.0
    lo = max(0, index - 1)
    hi = min(count - 1, index + 1)
    if lo == hi:
        return 0.0
    dt = float(times[hi]) - float(times[lo])
    if abs(dt) < 1.0e-12:
        return 0.0
    return (float(values[hi]) - float(values[lo])) / dt


def _retro_burn_diagnostic(
    times,
    positions_x,
    positions_y,
    velocities_z,
    orientations_theta,
    orientations_phi,
    thrust_forces,
    apogee_time_s=None,
):
    """Measure whether powered-descent thrust actually opposes velocity.

    OpenRocket's positive body-Z axis points through the nose.  Theta is its
    elevation above the horizontal (+90 degrees nose-up), and phi is azimuth
    clockwise from north.  A genuine retro burn therefore has a negative dot
    product between that body axis and the world-frame velocity vector.
    """
    samples = []
    count = min(
        len(times),
        len(positions_x),
        len(positions_y),
        len(velocities_z),
        len(orientations_theta),
        len(orientations_phi),
        len(thrust_forces),
    )
    for index in range(count):
        thrust_n = float(thrust_forces[index])
        vz = float(velocities_z[index])
        if thrust_n <= 1.0 or (
            apogee_time_s is not None
            and float(times[index]) < float(apogee_time_s) - 1.0e-9
        ):
            continue

        theta = float(orientations_theta[index])
        phi = float(orientations_phi[index])
        vx = _finite_difference(positions_x, times, index)
        vy = _finite_difference(positions_y, times, index)
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        if speed <= 1.0e-9:
            continue

        horizontal_axis = math.cos(theta)
        axis_x = horizontal_axis * math.sin(phi)
        axis_y = horizontal_axis * math.cos(phi)
        axis_z = math.sin(theta)
        axis_velocity = axis_x * vx + axis_y * vy + axis_z * vz
        direction_cosine = axis_velocity / speed
        vertical_thrust_n = thrust_n * axis_z

        samples.append(
            {
                "time_s": float(times[index]),
                "thrust_n": thrust_n,
                "theta_deg": math.degrees(theta),
                "phi_deg": math.degrees(phi),
                "vx_ms": vx,
                "vy_ms": vy,
                "vz_ms": vz,
                "speed_ms": speed,
                "direction_cosine": direction_cosine,
                "thrust_velocity_power_w": thrust_n * axis_velocity,
                "vertical_thrust_n": vertical_thrust_n,
                "vertical_power_w": vertical_thrust_n * vz,
            }
        )

    if not samples:
        return {
            "sample_count": 0,
            "retro_braking_verified": False,
            "fraction_opposing_velocity": 0.0,
            "fraction_vertical_braking": 0.0,
        }

    opposing = [sample for sample in samples if sample["direction_cosine"] < 0.0]
    vertical_braking = [
        sample for sample in samples if sample["vertical_power_w"] < 0.0
    ]
    peak = max(samples, key=lambda sample: sample["thrust_n"])
    fraction_opposing = len(opposing) / len(samples)
    return {
        "sample_count": len(samples),
        "start_time_s": samples[0]["time_s"],
        "end_time_s": samples[-1]["time_s"],
        "mean_direction_cosine": sum(
            sample["direction_cosine"] for sample in samples
        )
        / len(samples),
        "fraction_opposing_velocity": fraction_opposing,
        "fraction_vertical_braking": len(vertical_braking) / len(samples),
        "peak_thrust_sample": peak,
        "retro_braking_verified": fraction_opposing >= 0.90,
    }


def _descent_alignment_diagnostic(
    times,
    altitudes,
    positions_x,
    positions_y,
    velocities_z,
    velocities_xy,
    orientations_theta,
    orientations_phi,
):
    """Find naturally tail-first windows available for delayed ignition."""
    samples = []
    count = min(
        len(times),
        len(altitudes),
        len(positions_x),
        len(positions_y),
        len(velocities_z),
        len(velocities_xy),
        len(orientations_theta),
        len(orientations_phi),
    )
    for index in range(count):
        altitude = float(altitudes[index])
        vz = float(velocities_z[index])
        if altitude <= LAUNCH_ALT or vz >= 0.0:
            continue
        vx = _finite_difference(positions_x, times, index)
        vy = _finite_difference(positions_y, times, index)
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        if speed <= 1.0e-9:
            continue
        theta = float(orientations_theta[index])
        phi = float(orientations_phi[index])
        horizontal_axis = math.cos(theta)
        axis_velocity_cosine = (
            horizontal_axis * math.sin(phi) * vx
            + horizontal_axis * math.cos(phi) * vy
            + math.sin(theta) * vz
        ) / speed
        samples.append(
            {
                "index": index,
                "time_s": float(times[index]),
                "altitude_m": altitude,
                "speed_ms": speed,
                "vertical_speed_ms": vz,
                "horizontal_speed_ms": math.hypot(vx, vy),
                "theta_deg": math.degrees(theta),
                "alignment_q": -axis_velocity_cosine,
                "vertical_alignment_q": (
                    math.sin(theta) if vz < 0.0 else -math.sin(theta)
                ),
            }
        )

    if not samples:
        return {
            "sample_count": 0,
            "best_alignment_q": -1.0,
            "alignment_candidates": [],
            "alignment_trace": [],
            "tail_first_windows": [],
        }

    # Retain a compact set of authority samples for the delay optimizer.  A
    # single window start/mid/end loses the tumble phase: the useful attitude
    # can last only a few integration samples even inside a long q >= 0.5
    # window.  Keep the strongest time-separated samples without exporting the
    # full OpenRocket trajectory.
    alignment_candidates = []

    def retain_candidates(pool, limit):
        for sample in sorted(
            pool,
            key=lambda item: (
                item["alignment_q"],
                -item["altitude_m"],
            ),
            reverse=True,
        ):
            if sample["alignment_q"] < 0.5:
                break
            if any(
                abs(sample["time_s"] - retained["time_s"]) < 0.05
                for retained in alignment_candidates
            ):
                continue
            alignment_candidates.append(
                {
                    "time_s": sample["time_s"],
                    "altitude_m": sample["altitude_m"],
                    "speed_ms": sample["speed_ms"],
                    "theta_deg": sample["theta_deg"],
                    "alignment_q": sample["alignment_q"],
                }
            )
            if len(alignment_candidates) >= limit:
                break

    # Preserve both globally strongest phases and the best phases close enough
    # to impact for a solid motor burn to matter.  Otherwise an excellent
    # high-altitude attitude can consume the compact telemetry budget.
    retain_candidates(samples, 16)
    final_time = samples[-1]["time_s"]
    retain_candidates(
        [sample for sample in samples if sample["time_s"] >= final_time - 30.0],
        32,
    )

    # Preserve a bounded trace for motor-aware full-burn integration.  The
    # uniform backbone describes long windows; every near-impact sample is
    # retained because ignition/burnout feasibility is most sensitive there.
    stride = max(1, math.ceil(len(samples) / 512))
    trace_indices = set(range(0, len(samples), stride))
    trace_indices.add(len(samples) - 1)
    trace_indices.update(
        index
        for index, sample in enumerate(samples)
        if sample["time_s"] >= final_time - 30.0
    )
    alignment_trace = [
        {
            key: sample[key]
            for key in (
                "time_s",
                "altitude_m",
                "speed_ms",
                "vertical_speed_ms",
                "horizontal_speed_ms",
                "theta_deg",
                "alignment_q",
                "vertical_alignment_q",
            )
        }
        for index in sorted(trace_indices)
        for sample in (samples[index],)
    ]

    windows = []
    active = []
    for sample in samples:
        if sample["alignment_q"] >= 0.5:
            if active and sample["index"] != active[-1]["index"] + 1:
                windows.append(active)
                active = []
            active.append(sample)
        elif active:
            windows.append(active)
            active = []
    if active:
        windows.append(active)

    return {
        "sample_count": len(samples),
        "best_alignment_q": max(
            sample["alignment_q"] for sample in samples
        ),
        "best_sample": max(samples, key=lambda sample: sample["alignment_q"]),
        "alignment_candidates": alignment_candidates,
        "alignment_trace": alignment_trace,
        "tail_first_windows": [
            {
                "start_time_s": window[0]["time_s"],
                "end_time_s": window[-1]["time_s"],
                "duration_s": window[-1]["time_s"] - window[0]["time_s"],
                "start_altitude_m": window[0]["altitude_m"],
                "end_altitude_m": window[-1]["altitude_m"],
                "best_alignment_q": max(
                    sample["alignment_q"] for sample in window
                ),
            }
            for window in windows
        ],
    }


def run_sim(
    ork_xml,
    helper=None,
    anti_tumble=True,
    seed=SIM_SEED,
    wind_seed="match_simulation_seed",
):
    """Write .ork to temp, simulate, return metrics dict.

    Extracts per-branch landing data and apogee East/North position.
    Returns dict with all metrics needed for correct OSIFOG scoring.
    helper param is kept for backward compatibility but unused.

    CORRECTED (2026-07-19):
    - Touchdown uses TOTAL speed (sqrt(vz^2 + vxy^2)), not vertical only
    - Apogee East/North position extracted for horizontal penalty term
    - Per-branch East/North touchdown position for signed mean
    """
    fd, path = tempfile.mkstemp(suffix=".ork")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(seed)
        if wind_seed == "match_simulation_seed":
            effective_wind_seed = seed
        else:
            effective_wind_seed = wind_seed
        if effective_wind_seed is not None:
            _seed_multilevel_wind(sim.getOptions(), int(effective_wind_seed))

        if anti_tumble:
            listener = _get_anti_tumble_listener()
            if listener is None:
                raise RuntimeError("Official anti-tumble JavaScript engine unavailable")
            # Simulation.simulate takes additional listeners as varargs.  The
            # former SimulationOptions listener-list call does not exist in
            # OpenRocket 24.12 and silently disabled the required extension.
            sim.simulate(listener)
        else:
            sim.simulate()
        data = sim.getSimulatedData()

        m = {
            "apogee_m":      float(data.getMaxAltitude()),
            "mach":          float(data.getMaxMachNumber()),
            "flight_time_s": float(data.getFlightTime()),
            "status":        str(sim.getStatus()),
            "seed":          seed,
            "wind_seed":     effective_wind_seed,
        }

        try:
            import jpype
            fdt    = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
            FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
            TYPE_ALT = fdt.TYPE_ALTITUDE
            TYPE_PX  = fdt.TYPE_POSITION_X
            TYPE_PY  = fdt.TYPE_POSITION_Y
            TYPE_VZ  = fdt.TYPE_VELOCITY_Z
            TYPE_VXY = fdt.TYPE_VELOCITY_XY
            TYPE_MASS = fdt.TYPE_MASS
            TYPE_TIME = fdt.TYPE_TIME
            TYPE_THETA = fdt.TYPE_ORIENTATION_THETA
            TYPE_PHI = fdt.TYPE_ORIENTATION_PHI
            TYPE_AOA = fdt.TYPE_AOA
            TYPE_STABILITY = fdt.TYPE_STABILITY
            TYPE_THRUST = fdt.TYPE_THRUST_FORCE

            n_branches = int(data.getBranchCount())
            stage_landings = []
            ascent_stability = []
            ascent_stability_segments = []
            retro_burn_diagnostics = []
            descent_alignment_diagnostics = []
            branch_apogee_states = []
            separation_states = []
            final_masses_kg = 0.0
            event_times = {}
            branch_event_times = []
            for branch_index in range(n_branches):
                branch_events = {}
                for event in data.getBranch(branch_index).getEvents():
                    event_name = str(event.getType().name())
                    times = event_times.setdefault(event_name, [])
                    branch_times = branch_events.setdefault(event_name, [])
                    event_time = float(event.getTime())
                    if not any(abs(existing - event_time) < 1.0e-9 for existing in times):
                        times.append(event_time)
                    if not any(
                        abs(existing - event_time) < 1.0e-9
                        for existing in branch_times
                    ):
                        branch_times.append(event_time)
                for times in branch_events.values():
                    times.sort()
                branch_event_times.append(branch_events)
            for times in event_times.values():
                times.sort()
            m["event_times"] = event_times
            m["branch_event_times"] = branch_event_times

            # ── Initial Mass (for propellant calc) ──
            br0 = data.getBranch(0)
            if int(br0.getLength()) > 0:
                initial_mass_kg = float(br0.get(TYPE_MASS)[0])
            else:
                initial_mass_kg = 0.0

            # ── Extract apogee East/North from branch 0 ──
            br0 = data.getBranch(0)
            alt0 = br0.get(TYPE_ALT)
            px0  = br0.get(TYPE_PX)
            py0  = br0.get(TYPE_PY)
            n0   = int(br0.getLength())
            if n0 > 0:
                apex_idx = max(range(n0), key=lambda i: float(alt0[i]))
                m["apogee_east_m"]  = float(px0[apex_idx])
                m["apogee_north_m"] = float(py0[apex_idx])
            else:
                m["apogee_east_m"]  = 0.0
                m["apogee_north_m"] = 0.0

            # ── Extract per-branch landing data precisely ──
            branch_identities = []
            for bi in range(n_branches):
                br = data.getBranch(bi)
                branch_name = str(br.getName())
                normalized_branch_name = branch_name.strip().lower()
                if "sustainer" in normalized_branch_name:
                    stage_key = "s0"
                elif "booster" in normalized_branch_name:
                    stage_key = "s1"
                else:
                    stage_key = None
                branch_identities.append(
                    {
                        "branch": bi,
                        "branch_name": branch_name,
                        "stage_key": stage_key,
                    }
                )
                n  = int(br.getLength())
                if n < 2:
                    continue
                
                # Find the actual GROUND_HIT event
                events = br.getEvents()
                hit_time = None
                for ev in events:
                    if ev.getType() == FlightEvent.Type.GROUND_HIT:
                        hit_time = float(ev.getTime())
                        break

                t_arr   = br.get(TYPE_TIME)
                alt_arr = br.get(TYPE_ALT)
                px_arr  = br.get(TYPE_PX)
                py_arr  = br.get(TYPE_PY)
                vz_arr  = br.get(TYPE_VZ)
                vxy_arr = br.get(TYPE_VXY)
                mass_arr = br.get(TYPE_MASS)
                theta_arr = br.get(TYPE_THETA)
                phi_arr = br.get(TYPE_PHI)
                aoa_arr = br.get(TYPE_AOA)
                stability_arr = br.get(TYPE_STABILITY)
                thrust_arr = br.get(TYPE_THRUST)

                burn_diagnostic = _retro_burn_diagnostic(
                    t_arr,
                    px_arr,
                    py_arr,
                    vz_arr,
                    theta_arr,
                    phi_arr,
                    thrust_arr,
                    (
                        branch_event_times[bi].get("APOGEE", [None])[0]
                        if branch_event_times[bi].get("APOGEE")
                        else None
                    ),
                )
                burn_diagnostic["branch"] = bi
                burn_diagnostic["branch_name"] = branch_name
                burn_diagnostic["stage_key"] = stage_key
                retro_burn_diagnostics.append(burn_diagnostic)
                alignment_diagnostic = _descent_alignment_diagnostic(
                    t_arr,
                    alt_arr,
                    px_arr,
                    py_arr,
                    vz_arr,
                    vxy_arr,
                    theta_arr,
                    phi_arr,
                )
                alignment_diagnostic["branch"] = bi
                alignment_diagnostic["branch_name"] = branch_name
                alignment_diagnostic["stage_key"] = stage_key
                descent_alignment_diagnostics.append(alignment_diagnostic)
                branch_apex_index = max(
                    range(n), key=lambda index: float(alt_arr[index])
                )
                branch_apogee_states.append(
                    {
                        "branch": bi,
                        "branch_name": branch_name,
                        "stage_key": stage_key,
                        "time_s": float(t_arr[branch_apex_index]),
                        "altitude_m": float(alt_arr[branch_apex_index]),
                        "theta_deg": math.degrees(
                            float(theta_arr[branch_apex_index])
                        ),
                        "phi_deg": math.degrees(float(phi_arr[branch_apex_index])),
                        "vz_ms": float(vz_arr[branch_apex_index]),
                        "vxy_ms": float(vxy_arr[branch_apex_index]),
                    }
                )

                branch_stability = _minimum_initial_ascent_stability(
                    alt_arr,
                    vz_arr,
                    stability_arr,
                    t_arr,
                    end_time_s=(
                        min(event_times.get("STAGE_SEPARATION", []))
                        if bi > 0 and event_times.get("STAGE_SEPARATION")
                        else None
                    ),
                )
                separation_times = event_times.get("STAGE_SEPARATION", [])
                separation_time = (
                    min(separation_times) if separation_times else None
                )
                if bi == 0 and separation_time is not None:
                    separation_index = min(
                        range(n),
                        key=lambda index: abs(
                            float(t_arr[index]) - float(separation_time)
                        ),
                    )
                    separation_states.append(
                        {
                            "branch": bi,
                            "time_s": float(t_arr[separation_index]),
                            "altitude_m": float(alt_arr[separation_index]),
                            "theta_deg": math.degrees(
                                float(theta_arr[separation_index])
                            ),
                            "phi_deg": math.degrees(
                                float(phi_arr[separation_index])
                            ),
                            "vz_ms": float(vz_arr[separation_index]),
                            "vxy_ms": float(vxy_arr[separation_index]),
                            "aoa_deg": math.degrees(
                                float(aoa_arr[separation_index])
                            ),
                        }
                    )
                if bi == 0 and separation_time is not None:
                    # Find the sustainer motor burnout: the LAST burnout event
                    # after separation.  This defines the powered-ascent window.
                    # After burnout the sustainer is unpowered and coasting;
                    # instability during the coast is DESIRED for Starship-style
                    # tail-first descent (forward-flap rotation).
                    burnout_times = event_times.get("BURNOUT", [])
                    sustainer_burnout = None
                    if burnout_times:
                        post_sep_burnouts = [
                            float(bt) for bt in burnout_times
                            if float(bt) > float(separation_time) + 1.0e-6
                        ]
                        if post_sep_burnouts:
                            sustainer_burnout = max(post_sep_burnouts)
                    for segment_name, start_time, end_time in (
                        ("full_stack", None, separation_time),
                        ("sustainer", separation_time, sustainer_burnout),
                    ):
                        segment_margin = _minimum_initial_ascent_stability(
                            alt_arr,
                            vz_arr,
                            stability_arr,
                            t_arr,
                            start_time,
                            end_time,
                        )
                        if segment_margin is not None:
                            ascent_stability_segments.append(
                                {
                                    "segment": segment_name,
                                    "branch": bi,
                                    "min_calibers": segment_margin,
                                }
                            )
                elif bi > 0:
                    ascent_stability_segments.append(
                        {
                            "segment": "booster",
                            "branch": bi,
                            "min_calibers": branch_stability,
                        }
                    )
                if branch_stability is not None:
                    ascent_stability.append(
                        {"branch": bi, "min_calibers": branch_stability}
                    )

                if hit_time is None:
                    # Fallback if no event but it reached ground level
                    if float(alt_arr[n - 1]) <= LAUNCH_ALT + 10:
                        hit_time = float(t_arr[n - 1])
                    else:
                        continue # Stage did not land
                
                # Find index just at or after hit_time
                idx = 1
                for i in range(1, n):
                    if float(t_arr[i]) >= hit_time:
                        idx = i
                        break

                t1, t2 = float(t_arr[idx-1]), float(t_arr[idx])
                dt = t2 - t1
                # Interpolate precisely to hit_time
                if dt > 0 and t2 >= hit_time >= t1:
                    f = (hit_time - t1) / dt
                    final_px  = float(px_arr[idx-1])  + f * (float(px_arr[idx])  - float(px_arr[idx-1]))
                    final_py  = float(py_arr[idx-1])  + f * (float(py_arr[idx])  - float(py_arr[idx-1]))
                    final_vz  = float(vz_arr[idx-1])  + f * (float(vz_arr[idx])  - float(vz_arr[idx-1]))
                    final_vxy = float(vxy_arr[idx-1]) + f * (float(vxy_arr[idx]) - float(vxy_arr[idx-1]))
                    final_mass= float(mass_arr[idx-1])+ f * (float(mass_arr[idx])- float(mass_arr[idx-1]))
                else:
                    final_px  = float(px_arr[idx])
                    final_py  = float(py_arr[idx])
                    final_vz  = float(vz_arr[idx])
                    final_vxy = float(vxy_arr[idx])
                    final_mass= float(mass_arr[idx])

                final_masses_kg += final_mass
                total_speed = math.sqrt(final_vz**2 + final_vxy**2)
                h_dist = math.sqrt(final_px**2 + final_py**2)

                stage_landings.append({
                    "branch":      bi,
                    "branch_name": branch_name,
                    "stage_key": stage_key,
                    "time_s":      hit_time,
                    "east_m":      final_px,
                    "north_m":     final_py,
                    "dist_m":      h_dist,
                    "vz_ms":       final_vz,
                    "vxy_ms":      final_vxy,
                    "total_speed": total_speed,
                    "mass_kg":      final_mass,
                    "orientation_theta_deg": math.degrees(float(theta_arr[idx])),
                    "orientation_phi_deg": math.degrees(float(phi_arr[idx])),
                    "aoa_deg": math.degrees(float(aoa_arr[idx])),
                })

            m["stage_landings"] = stage_landings
            m["branch_identities"] = branch_identities
            m["ascent_static_margins"] = ascent_stability
            m["ascent_stability_segments"] = ascent_stability_segments
            m["retro_burn_diagnostics"] = retro_burn_diagnostics
            m["descent_alignment_diagnostics"] = (
                descent_alignment_diagnostics
            )
            m["branch_apogee_states"] = branch_apogee_states
            m["separation_states"] = separation_states
            m["min_static_margin"] = min(
                (item["min_calibers"] for item in ascent_stability),
                default=float("-inf"),
            )
            
            # Robust propellant calculation: Initial Mass - Sum of landed final masses
            if len(stage_landings) > 0:
                m["m_prop_kg_actual"] = max(0.0, initial_mass_kg - final_masses_kg)
            else:
                m["m_prop_kg_actual"] = 0.0

            if len(stage_landings) >= 2:
                s0 = stage_landings[0]
                s1 = stage_landings[1]
                m["s0_landing_speed"] = s0["total_speed"]
                m["s0_landing_dist"]  = s0["dist_m"]
                m["s0_east_m"]        = s0["east_m"]
                m["s0_north_m"]       = s0["north_m"]
                m["s1_landing_speed"] = s1["total_speed"]
                m["s1_landing_dist"]  = s1["dist_m"]
                m["s1_east_m"]        = s1["east_m"]
                m["s1_north_m"]       = s1["north_m"]
            elif len(stage_landings) == 1:
                s0 = stage_landings[0]
                m["s0_landing_speed"] = s0["total_speed"]
                m["s0_landing_dist"]  = s0["dist_m"]
                m["s0_east_m"]        = s0["east_m"]
                m["s0_north_m"]       = s0["north_m"]

        except Exception as e:
            m["telemetry_err"] = str(e)

        try:
            import jpype
            jpype.java.lang.System.gc()
        except:
            pass

        return m

    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def save_simulated_ork(ork_xml, path, seed=SIM_SEED, extra_entries=None):
    """Execute the sole simulation and save a submission-ready ZIP .ork.

    The mission requires the anti-tumbling extension, exactly one executed
    simulation, and all simulated data stored in the file.  The optimizer's
    in-memory listener is also attached for this final execution because an
    untrusted script extension is intentionally disabled when a document is
    loaded from disk by OpenRocket's security policy.
    """
    import jpype

    init_or()
    extra_entries = list(extra_entries or [])
    fd, source_path = tempfile.mkstemp(suffix=".ork")
    os.close(fd)
    try:
        if extra_entries:
            with zipfile.ZipFile(
                source_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as source:
                source.writestr("rocket.ork", ork_xml.encode("utf-8"))
                seen_names = {"rocket.ork"}
                for declaration in extra_entries:
                    source_file = os.path.abspath(str(declaration["path"]))
                    zip_name = (
                        str(declaration["zip_name"])
                        .replace("\\", "/")
                        .lstrip("/")
                    )
                    if not zip_name.startswith("decals/"):
                        raise ValueError(
                            "extra OpenRocket decal entries must live under decals/"
                        )
                    if zip_name in seen_names:
                        raise ValueError(f"duplicate OpenRocket entry {zip_name!r}")
                    if not os.path.isfile(source_file):
                        raise FileNotFoundError(
                            f"OpenRocket decal entry not found: {source_file}"
                        )
                    source.write(source_file, zip_name)
                    seen_names.add(zip_name)
        else:
            # Preserve the historical plain-XML loader path byte-for-byte when
            # no cosmetic assets are requested.
            with open(source_path, "w", encoding="utf-8") as source:
                source.write(ork_xml)
        doc = _load_ork_doc(source_path)
        simulations = doc.getSimulations()
        if int(simulations.size()) != 1:
            raise ValueError(f"submission must contain exactly one simulation, found {simulations.size()}")

        sim = simulations.get(0)
        sim.getOptions().setRandomSeed(int(seed))
        _seed_multilevel_wind(sim.getOptions(), int(seed))
        listener = _get_anti_tumble_listener()
        if listener is None:
            raise RuntimeError("JavaScript anti-tumbling listener is unavailable")
        sim.simulate(listener)

        extensions = sim.getSimulationExtensions()
        serialized_extensions = []
        for index in range(int(extensions.size())):
            extension = extensions.get(index)
            serialized_extensions.append(
                {
                    "extensionid": str(extension.getClass().getName()),
                    "script": (
                        str(extension.getScript())
                        if hasattr(extension, "getScript") else ""
                    ),
                }
            )
        valid, violations = validate_anti_tumble_extensions(serialized_extensions)
        if not valid:
            raise ValueError("anti-tumble extension gate: " + "; ".join(violations))

        storage_options = jpype.JClass(
            "info.openrocket.core.document.StorageOptions"
        )()
        storage_options.setSaveSimulationData(True)
        saver = jpype.JClass("info.openrocket.core.file.GeneralRocketSaver")()
        File = jpype.JClass("java.io.File")
        saver.save(File(os.path.abspath(path)), doc, storage_options)
        _canonicalize_saved_ork(path)
    finally:
        try:
            os.unlink(source_path)
        except OSError:
            pass


_RUNTIME_UUID_RE = re.compile(
    rb"\b[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    rb"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_DATAPOINT_RE = re.compile(rb"(<datapoint>)([^<]*)(</datapoint>)")


def _canonicalize_saved_ork(path):
    """Remove runtime-only entropy from a saved OpenRocket ZIP.

    OpenRocket generates random UUIDv4 values for warnings/events and records
    wall-clock computation time in every datapoint. ZIP timestamps add a third
    source of byte drift. None affects flight physics or saved telemetry.
    """
    path = os.path.abspath(path)
    with zipfile.ZipFile(path, "r") as source:
        entries = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if not info.is_dir()
        }

    rocket_name = next(
        (name for name in entries if name.lower().endswith(".ork")),
        None,
    )
    if rocket_name is None:
        raise ValueError("saved OpenRocket ZIP has no .ork XML entry")
    xml = entries[rocket_name]

    runtime_ids = {}

    def stable_runtime_id(match):
        original = match.group(0).lower()
        if original not in runtime_ids:
            runtime_ids[original] = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"l2-osifog/saved-runtime/{len(runtime_ids)}",
            )).encode("ascii")
        return runtime_ids[original]

    xml = _RUNTIME_UUID_RE.sub(stable_runtime_id, xml)

    def zero_computation_time(match):
        values = match.group(2).split(b",")
        if len(values) >= 2:
            # OpenRocket writes computation time immediately before Coriolis
            # acceleration in the persisted FlightDataType order.
            values[-2] = b"0"
        return match.group(1) + b",".join(values) + match.group(3)

    entries[rocket_name] = _DATAPOINT_RE.sub(zero_computation_time, xml)

    fd, temp_path = tempfile.mkstemp(
        suffix=".ork",
        dir=os.path.dirname(path),
    )
    os.close(fd)
    try:
        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as target:
            # GeneralRocketLoader treats the first ZIP entry as the document
            # payload. Keep the .ork XML first, then sort cosmetic assets for
            # deterministic packaging.
            ordered_names = [rocket_name] + sorted(
                name for name in entries if name != rocket_name
            )
            for name in ordered_names:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                target.writestr(info, entries[name], compresslevel=9)
        os.replace(temp_path, path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════
# Hard Constraint Validator
# ═══════════════════════════════════════════════════════════════
def validate_hard_constraints(m, p) -> tuple[bool, list[str]]:
    """Return the retired internal gate retained for historical diagnostics.

    New scoring and submission code must use
    :func:`validate_official_constraints`. This function intentionally
    preserves old campaign assumptions so archived experiments remain
    explainable.
    """
    native_podset = all(
        key in p
        for key in (
            "s0_core_radius", "s1_core_radius",
            "s0_core_length", "s1_core_length",
            "s0_pod_length", "s1_pod_length",
        )
    )
    violations = [] if native_podset else validate_candidate_geometry(p)

    status = m.get("status", "")
    if "SIMULATIONABORTED" in status.upper() or "ABORTED" in status.upper():
        violations.append(f"Simulation aborted: {status}")

    if m.get("mach", 0) >= MAX_MACH:
        violations.append(
            f"Supersonic safety gate: Mach={m['mach']:.3f} >= {MAX_MACH:.2f}"
        )

    # For 2-stage rockets, check stability per-segment:
    # full_stack must be >= MIN_FULL_STACK_MARGIN during boost,
    # sustainer must be >= MIN_STATIC_MARGIN after staging.
    # Fall back to flat min_static_margin check for single-stage.
    stability_segments = m.get("ascent_stability_segments", [])
    if stability_segments:
        seg_map = {s["segment"]: s["min_calibers"] for s in stability_segments}
        full_stack_margin = seg_map.get("full_stack", float("-inf"))
        sustainer_margin = seg_map.get("sustainer", float("-inf"))
        if full_stack_margin < MIN_FULL_STACK_MARGIN:
            violations.append(
                f"Full-stack boost stability: {full_stack_margin:.3f} cal < {MIN_FULL_STACK_MARGIN:.1f} cal"
            )
        if sustainer_margin < MIN_STATIC_MARGIN:
            violations.append(
                f"Sustainer ascent stability: {sustainer_margin:.3f} cal < {MIN_STATIC_MARGIN:.1f} cal"
            )
    else:
        if float(m.get("min_static_margin", float("-inf"))) < MIN_STATIC_MARGIN:
            violations.append(
                "Static stability during ascent: "
                f"{float(m.get('min_static_margin', float('-inf'))):.3f} cal < "
                f"{MIN_STATIC_MARGIN:.1f} cal"
            )

    if native_podset:
        import osifog_podset
        total_height = osifog_podset.podset_total_height_m(p)
    else:
        nose_length = float(
            p.get("nose_length_m", max(0.25, float(p["s0_body_rad"]) * 10.0))
        )
        total_height = nose_length + float(p["s0_body_len"]) + float(p["s1_body_len"])
    if total_height > MAX_HEIGHT_M:
        violations.append(
            f"Rocket height: {total_height:.3f} m > {MAX_HEIGHT_M:.1f} m"
        )

    radius_keys = (
        ("s0_core_radius", "s1_core_radius")
        if native_podset
        else ("s0_body_rad", "s1_body_rad")
    )
    if abs(float(p[radius_keys[0]]) - float(p[radius_keys[1]])) > 1.0e-9:
        violations.append("Stage radii differ without a physical transition")

    separation_delay = float(p.get("s1_separation_delay", 0.0))
    if separation_delay < 0.0 or separation_delay > 1.0:
        violations.append(
            "Genuine staging gate: booster separation delay must be within "
            f"[0, 1] s after burnout, got {separation_delay:.3f} s"
        )
    event_times = m.get("event_times", {})
    separations = list(event_times.get("STAGE_SEPARATION", []))
    apogees = list(event_times.get("APOGEE", []))
    manifest = m.get("scenario_manifest")
    if isinstance(manifest, dict) and manifest.get("scenario_type") == "OFFICIAL_FULL_MISSION":
        ignition_contract = manifest.get("ignition_events", {})
        if ignition_contract.get("s0_main") != "burnout" or ignition_contract.get("s1_main") != "launch":
            violations.append("Genuine staging gate: compiled main-motor ignition contract is invalid")
        ignition_times = sorted(float(value) for value in event_times.get("IGNITION", []))
        burnout_times = sorted(float(value) for value in event_times.get("BURNOUT", []))
        ascent_limit = min(apogees) if apogees else float("inf")
        ascent_ignitions = [value for value in ignition_times if value < ascent_limit]
        if not ascent_ignitions or abs(ascent_ignitions[0]) > 1.0e-6:
            violations.append("Genuine staging gate: booster did not ignite at launch")
        positive_ascent_ignitions = [value for value in ascent_ignitions if value > 1.0e-6]
        if not positive_ascent_ignitions or not burnout_times:
            violations.append("Genuine staging gate: sustainer burnout-triggered ignition is missing")
        elif abs(positive_ascent_ignitions[0] - burnout_times[0]) > max(
            0.002, float(p.get("timestep_s", 0.05)) * 1.1
        ):
            violations.append(
                "Genuine staging gate: sustainer ignition does not coincide with booster burnout"
            )
    if event_times:
        if not separations:
            violations.append("Genuine staging gate: no stage-separation event")
        elif apogees and min(separations) >= min(apogees):
            violations.append(
                "Genuine staging gate: separation occurred at or after apogee"
            )
        branch_events = list(m.get("branch_event_times", []))
        landings_by_branch = {
            int(stage.get("branch", index)): float(stage["time_s"])
            for index, stage in enumerate(m.get("stage_landings", []))
            if "time_s" in stage
        }
        if branch_events and len(landings_by_branch) == 2:
            for branch_index, timeline in enumerate(branch_events[:2]):
                branch_apogees = list(timeline.get("APOGEE", []))
                branch_ignitions = list(timeline.get("IGNITION", []))
                touchdown = landings_by_branch.get(branch_index)
                if not branch_apogees or touchdown is None or not any(
                    max(branch_apogees) < ignition < touchdown
                    for ignition in branch_ignitions
                ):
                    violations.append(
                        "Retro ignition gate: branch "
                        f"{branch_index} has no central-motor ignition during descent"
                    )

    for stage in (() if native_podset else ("s0", "s1")):
        radius = float(p[f"{stage}_body_rad"])
        body_length = float(p[f"{stage}_body_len"])
        retro = MOTOR_DATABASE[p[f"{stage}_retro"]]
        if p.get(f"{stage}_main") is None:
            # retro-only stage: just the central motor must fit
            if radius < retro[2] / 2.0 + 0.003:
                violations.append(
                    f"{stage} motor fit: radius {radius:.3f} m too small for retro"
                )
            if body_length < retro[3] + 0.03:
                violations.append(
                    f"{stage} motor fit: length {body_length:.3f} m too short for retro"
                )
            continue
        main = MOTOR_DATABASE[p[f"{stage}_main"]]
        if int(p.get("main_cluster_count", 1)) == 3:
            try:
                _falcon_cluster_geometry(
                    p[f"{stage}_main"], p[f"{stage}_retro"], radius
                )
            except ValueError as exc:
                violations.append(f"{stage} physical motor cage: {exc}")
            required_radius = radius
            required_length = max(main[3], retro[3]) + 0.03
        else:
            required_radius = max(main[2], retro[2]) / 2.0 + 0.003
            required_length = main[3] + retro[3] + 0.03
        if radius < required_radius:
            violations.append(
                f"{stage} motor fit: radius {radius:.3f} m < {required_radius:.3f} m"
            )
        if body_length < required_length:
            violations.append(
                f"{stage} motor stack: length {body_length:.3f} m < {required_length:.3f} m"
            )
        if min(radius, body_length) < MIN_DIMENSION_M:
            violations.append(f"{stage} contains a dimension below {MIN_DIMENSION_M:.3f} m")

    stages = m.get("stage_landings", [])
    if len(stages) != 2:
        violations.append(f"Exactly 2 stage landings are required, found {len(stages)}")

    for i, s in enumerate(stages):
        spd = s["total_speed"]
        if spd >= 5.0:
            violations.append(f"Stage {i} crash: {spd:.2f} m/s >= 5.0 m/s (total speed limit)")

    burn_diagnostics = list(m.get("retro_burn_diagnostics", []))
    if len(burn_diagnostics) != 2:
        violations.append(
            "Retro braking telemetry gate: exactly two branch diagnostics "
            f"required, found {len(burn_diagnostics)}"
        )
    else:
        for diagnostic in burn_diagnostics:
            branch = int(diagnostic.get("branch", -1))
            if not diagnostic.get("retro_braking_verified", False):
                violations.append(
                    "Retro braking telemetry gate: branch "
                    f"{branch} opposed velocity for only "
                    f"{float(diagnostic.get('fraction_opposing_velocity', 0.0)):.1%} "
                    "of its post-apogee powered samples"
                )

    if "telemetry_err" in m:
        violations.append(f"Telemetry extraction failed: {m['telemetry_err']}")

    is_legal = len(violations) == 0
    return is_legal, violations


def validate_official_constraints(m, p) -> tuple[bool, list[str]]:
    """Validate the confirmed organizer rules available from metrics/params.

    XML-only package checks (recovery tags, overrides, ring geometry, material
    densities, and simulation count) remain in ``osifog_submit.checklist``.
    """
    native_podset = all(
        key in p
        for key in (
            "s0_core_radius", "s1_core_radius",
            "s0_core_length", "s1_core_length",
            "s0_pod_length", "s1_pod_length",
        )
    )
    violations = []
    status = str(m.get("status", ""))
    if "ABORT" in status.upper():
        violations.append(f"Simulation aborted: {status}")

    if "telemetry_err" in m:
        violations.append(f"Telemetry extraction failed: {m['telemetry_err']}")

    mach = float(m.get("mach", float("inf")))
    if not math.isfinite(mach) or mach >= 1.0:
        violations.append(f"Subsonic gate: Mach={mach:.4f} >= 1.0")

    stages = list(m.get("stage_landings", []))
    if len(stages) != 2:
        violations.append(
            f"Exactly 2 stage landings are required, found {len(stages)}"
        )
    for index, landing in enumerate(stages):
        speed = float(landing.get("total_speed", float("inf")))
        if not math.isfinite(speed) or speed >= 5.0:
            violations.append(
                f"Stage {index} crash: {speed:.3f} m/s >= 5.0 m/s"
            )
        theta = float(
            landing.get("orientation_theta_deg", float("-inf"))
        )
        if not math.isfinite(theta) or theta <= 45.0:
            violations.append(
                f"Stage {index} attitude: theta={theta:.3f} deg <= 45 deg"
            )

    try:
        if native_podset:
            import osifog_podset
            total_height = osifog_podset.podset_total_height_m(p)
        else:
            total_height = (
                float(p.get("nose_length_m", 0.0))
                + float(p["s0_body_len"])
                + float(p["s1_body_len"])
            )
        if total_height > 4.0:
            violations.append(
                f"Rocket height: {total_height:.3f} m > 4.0 m"
            )
        radius_keys = (
            ("s0_core_radius", "s1_core_radius")
            if native_podset
            else ("s0_body_rad", "s1_body_rad")
        )
        if abs(float(p[radius_keys[0]]) - float(p[radius_keys[1]])) > 1.0e-9:
            violations.append(
                "Stage radii differ without a physical transition"
            )
    except (KeyError, TypeError, ValueError) as exc:
        violations.append(f"Candidate dimensions unavailable: {exc}")

    if LAUNCH_ROD_M > 6.0:
        violations.append(f"Launch rod: {LAUNCH_ROD_M:.3f} m > 6.0 m")

    ejection_events = list(
        m.get("event_times", {}).get("EJECTION_CHARGE", [])
    )
    if ejection_events:
        violations.append(
            f"Ejection charges are prohibited: events at {ejection_events}"
        )
    if native_podset:
        manifest = m.get("scenario_manifest")
        if isinstance(manifest, dict):
            if not manifest.get("motors_plugged"):
                violations.append("Motors must be serialized as plugged")
            if manifest.get("centering_rings_per_stage") != {"s0": 2, "s1": 2}:
                violations.append(
                    "Submission requires two centering rings per stage"
                )
            if not manifest.get("nose_ballast_shell_bonded"):
                violations.append(
                    "Nose ballast must be rigidly bonded to the nose shell"
                )
    else:
        if not bool(p.get("plugged_motors", False)):
            violations.append("Motors must be serialized as plugged")
        if not bool(p.get("octaweb_rings", False)):
            violations.append(
                "Submission requires two centering rings per stage"
            )
        if p.get("nose_ballast_attachment") != "nose_shell_bonded":
            violations.append(
                "Nose ballast must be rigidly bonded to the nose shell"
            )

    return not violations, violations


# ═══════════════════════════════════════════════════════════════
# Official OSIFOG Level 3 Scoring Formula
# ═══════════════════════════════════════════════════════════════
def score_official(m, p) -> dict:
    """Score using the CORRECT OSIFOG Level 3 formula.

    S = 900,000
      - 3000 × (h_ap - 3000)²
      - 16 × (E_ap² + N_ap²)
      - 2 × (E_touch_mean² + N_touch_mean²)
      - 500 × V_touch_mean²
      - 7500 × m_prop

    Where:
      h_ap             = apogee altitude (m)
      E_ap, N_ap       = apogee East, North from launch (m)
      E_touch_mean     = arithmetic mean of all stage touchdown East (m)
      N_touch_mean     = arithmetic mean of all stage touchdown North (m)
      V_touch_mean     = arithmetic mean of all stage touchdown TOTAL speed (m/s)
      m_prop           = total propellant consumed (kg)

    CORRECTED (2026-07-19):
      - Starting score: 900,000 (NOT 1,000,000)
      - Position penalty: 2 × (E² + N²) using signed East/North (NOT 45000×dist)
      - Velocity penalty: 500 × V_mean² (NOT 80000/(v-5)²)
      - Uses total speed (NOT vertical only)
      - Includes horizontal apogee displacement (16 × E_ap² + N_ap²)
    """
    apogee    = m.get("apogee_m", 0.0)
    E_ap      = m.get("apogee_east_m", 0.0)
    N_ap      = m.get("apogee_north_m", 0.0)

    stages = m.get("stage_landings", [])
    if stages:
        mean_E = sum(s["east_m"]      for s in stages) / len(stages)
        mean_N = sum(s["north_m"]     for s in stages) / len(stages)
        mean_V = sum(s["total_speed"] for s in stages) / len(stages)
    else:
        # No landing data — catastrophic penalty
        mean_E = 9999.0
        mean_N = 9999.0
        mean_V = 999.0

    # Total propellant consumed (robust extraction via mass difference)
    m_prop = m.get("m_prop_kg_actual", 0.0)
    if m_prop <= 0.01:
        # Fallback if simulation aborted or extraction failed
        main_count = int(p.get("main_cluster_count", 1))
        m_prop = (
            (main_count * propellant_kg(p["s0_main"]) if p.get("s0_main") is not None else 0.0)
            + propellant_kg(p["s0_retro"])
            + (main_count * propellant_kg(p["s1_main"]) if p.get("s1_main") is not None else 0.0)
            + propellant_kg(p["s1_retro"])
        )

    # Penalty terms
    apogee_alt_pen  = 3000.0 * (apogee - TARGET_APOGEE) ** 2
    apogee_horiz_pen = 16.0 * (E_ap**2 + N_ap**2)
    touch_pos_pen   = 2.0   * (mean_E**2 + mean_N**2)
    touch_vel_pen   = 500.0 * mean_V**2
    prop_pen        = 7500.0 * m_prop

    raw_score = (900_000.0
                 - apogee_alt_pen
                 - apogee_horiz_pen
                 - touch_pos_pen
                 - touch_vel_pen
                 - prop_pen)

    is_legal, violations = validate_official_constraints(m, p)
    if not is_legal:
        effective_score = -1_000_000.0  # Illegal: rank below all legal candidates
    else:
        effective_score = raw_score

    return {
        "score":            effective_score,
        "raw_score":        raw_score,
        "is_legal":         is_legal,
        "violations":       violations,
        "apogee_m":         apogee,
        "apogee_err_m":     apogee - TARGET_APOGEE,
        "E_ap":             E_ap,
        "N_ap":             N_ap,
        "mean_E":           mean_E,
        "mean_N":           mean_N,
        "mean_V":           mean_V,
        "m_prop_kg":        m_prop,
        "apogee_alt_pen":   apogee_alt_pen,
        "apogee_horiz_pen": apogee_horiz_pen,
        "touch_pos_pen":    touch_pos_pen,
        "touch_vel_pen":    touch_vel_pen,
        "prop_pen":         prop_pen,
    }


# Keep old name as alias for backward compatibility
def score(m, p):
    return score_official(m, p)


# ═══════════════════════════════════════════════════════════════
# Auto-detect retro timing from free-flight impact time
# ═══════════════════════════════════════════════════════════════
def estimate_impact_time(m, stage_idx=0) -> float | None:
    """Return estimated ground-contact time for stage_idx from sim data."""
    stages = m.get("stage_landings", [])
    if stage_idx < len(stages):
        # flight_time_s is overall, we need branch-specific — approximate
        # from branch order: stage 0 lands later (sustainer), stage 1 earlier
        return m.get("flight_time_s")
    return None


def compute_retro_delay(impact_time_s: float, motor_idx: int,
                        buffer_s: float = 0.15) -> float:
    """Compute retro ignition delay so burnout is buffer_s before impact.

    target: motor still burning at touchdown, or burnout 0-0.3s before
    buffer_s = 0.15 is the midpoint of [0, 0.3] window
    """
    burn_time = _motor_burn_time(motor_idx)
    delay = impact_time_s - burn_time - buffer_s
    return max(0.0, delay)


def _motor_burn_time(motor_idx: int) -> float:
    """Exact burn duration from the .eng thrust-curve time domain."""
    return _motor_data.burn_duration(motor_idx)


# ═══════════════════════════════════════════════════════════════
# Parameter grid builder
# ═══════════════════════════════════════════════════════════════
def _body_len(main_idx, retro_idx, margin=0.12, main_cluster_count=1):
    ml = MOTOR_DATABASE[main_idx][3]
    rl = MOTOR_DATABASE[retro_idx][3]
    motor_envelope = max(ml, rl) if main_cluster_count == 3 else ml + rl
    return max(0.55, motor_envelope + margin)


RETRO_MOTORS = [0, 1, 2]  # F50T, F67W, G71R — small retro options

# Motor indices for sweeping — OSIFOG competition motors
# Sustainer: J/K class, 38-54mm
SUSTAINER_MAINS = [14, 15, 16, 17, 18, 19]  # J350W..K550W (38-54mm)
# Booster: K/L class, 54-75mm
BOOSTER_MAINS   = [19, 20, 21, 23, 24]       # K550W..L1150


def build_motor_grid(wind_levels, retro_idx=0):
    """Coarse motor × delay × azimuth grid.
    Focuses on J-class sustainer and K/L-class booster.
    """
    s0_delays = [160, 170, 174, 178]
    s1_delays = [20, 30, 40, 50]
    # Keep azimuth fixed at 288 in Phase 1 to reduce grid size (Phase 2 will sweep it)
    azimuths  = [288]
    nose_masses = [0.3, 0.8, 1.5, 2.5]

    grid = []
    for s0m, s1m, d0, d1, az, nm in itertools.product(
        SUSTAINER_MAINS, BOOSTER_MAINS,
        s0_delays, s1_delays, azimuths, nose_masses
    ):
        grid.append({
            "s0_main":  s0m,  "s0_retro": retro_idx,
            "s1_main":  s1m,  "s1_retro": retro_idx,
            "s0_body_len": _body_len(s0m, retro_idx),
            "s0_body_rad": 0.034,
            "s1_body_len": _body_len(s1m, retro_idx),
            "s1_body_rad": max(0.038, MOTOR_DATABASE[s1m][2] / 2 + 0.006),
            "s0_retro_delay": float(d0),
            "s1_retro_delay": float(d1),
            "nose_mass_kg":   nm,
            "s0_mid_ballast_kg": 0.0,
            "s0_aft_ballast_kg": 0.0,
            "s1_mid_ballast_kg": 0.0,
            "s1_aft_ballast_kg": 0.0,
            "launch_azimuth": float(az),
            "launch_angle_deg": 0.0,
            "wind_levels": wind_levels,
        })
    return grid


def build_fine_grid(base_p, wind_levels):
    """Fine grid around a promising candidate — refine timing + ballast."""
    d0_base = base_p["s0_retro_delay"]
    d1_base = base_p["s1_retro_delay"]
    # Fine sweep: ±30° around best, 5° increments
    az_base = base_p.get("launch_azimuth", 288.0)
    d0_range  = [d0_base + x for x in range(-8, 9, 2)]
    d1_range  = [d1_base + x for x in range(-8, 9, 2)]
    az_range  = [az_base + x for x in range(-30, 31, 5)]
    nm_range  = [0.050, 0.100, 0.150, 0.200, 0.250, 0.300]
    aft_range = [0.0, 0.050, 0.100, 0.150]

    grid = []
    for d0, d1, az, nm, aft in itertools.product(
        d0_range, d1_range, az_range, nm_range, aft_range
    ):
        pp = {**base_p}
        pp["s0_retro_delay"] = max(0.0, d0)
        pp["s1_retro_delay"] = max(0.0, d1)
        pp["launch_azimuth"] = az % 360.0
        pp["nose_mass_kg"]   = nm
        pp["s0_aft_ballast_kg"] = aft
        pp["s1_aft_ballast_kg"] = aft * 0.5
        pp["wind_levels"]    = wind_levels
        grid.append(pp)
    return grid


def build_precision_grid(base_p, wind_levels):
    """Ultra-fine grid for final timing precision at 0.5s increments."""
    d0_base = base_p["s0_retro_delay"]
    # Precision sweep: ±5° around best, 1° increments
    az_base = base_p.get("launch_azimuth", 288.0)
    d0_range = [d0_base + x * 0.5 for x in range(-6, 7)]
    d1_range = [d1_base + x * 0.5 for x in range(-6, 7)]
    az_range = [az_base + x for x in range(-5, 6, 1)]

    grid = []
    for d0, d1, az in itertools.product(d0_range, d1_range, az_range):
        pp = {**base_p}
        pp["s0_retro_delay"] = max(0.0, d0)
        pp["s1_retro_delay"] = max(0.0, d1)
        pp["launch_azimuth"] = az % 360.0
        pp["wind_levels"] = wind_levels
        grid.append(pp)
    return grid


# ═══════════════════════════════════════════════════════════════
# Sweep runner
# ═══════════════════════════════════════════════════════════════
def run_sweep(grid, helper, label="sweep", verbose=True,
              multi_seed=False, seeds=None):
    """Run a sweep grid, return sorted results list."""
    if seeds is None:
        seeds = [SIM_SEED]
    results = []
    t0 = time.time()
    for i, p in enumerate(grid):
        s0n = MOTOR_DATABASE[p["s0_main"]][1]
        s1n = MOTOR_DATABASE[p["s1_main"]][1]
        if verbose:
            sys.stdout.write(
                f"\r  [{i+1:4d}/{len(grid)}] {label} "
                f"S0={s0n:7s} S1={s1n:7s} "
                f"d0={p['s0_retro_delay']:6.1f}s d1={p['s1_retro_delay']:6.1f}s "
                f"az={p.get('launch_azimuth',90):.0f}°   "
            )
            sys.stdout.flush()
        try:
            if i > 0 and i % 100 == 0:
                import jpype
                jpype.java.lang.System.gc()

            ork = generate_ork(p)
            if multi_seed and len(seeds) > 1:
                metrics_all = []
                for sd in seeds:
                    m = run_sim(ork, helper, seed=sd)
                    metrics_all.append(m)
                # Use mean of scores for robustness
                scores = [score_official(m, p) for m in metrics_all]
                mean_sc = sum(s["score"] for s in scores) / len(scores)
                # Use median sim result for reporting
                metrics_all.sort(key=lambda m: m.get("apogee_m", 0))
                m = metrics_all[len(metrics_all) // 2]
                s = score_official(m, p)
                s["score"] = mean_sc  # override with mean
                s["multi_seed_scores"] = [sc["score"] for sc in scores]
            else:
                m = run_sim(ork, helper)
                s = score_official(m, p)
            flat_p = {k: v for k, v in p.items() if k != "wind_levels"}
            results.append({"params": flat_p, "metrics": m, "score": s})
        except Exception as e:
            flat_p = {k: v for k, v in p.items() if k != "wind_levels"}
            results.append({
                "params": flat_p,
                "metrics": {"status": "error", "error": str(e)},
                "score": {"score": -2_000_000.0, "violations": [str(e)]},
            })
    elapsed = time.time() - t0
    # Sort by legal score first, but if all failed (-1,000,000), fallback to raw_score to find the "least illegal" configs
    results.sort(key=lambda r: (r["score"]["score"], r["score"].get("raw_score", -float('inf'))), reverse=True)
    if verbose:
        print(f"\n  {label}: {len(results)} sims in {elapsed:.1f}s "
              f"({len(results)/max(0.01,elapsed):.1f} sims/s)")
    return results


def print_top_results(results, n=15, label="TOP CANDIDATES"):
    print()
    print("=" * 80)
    print(f"  {label}")
    print("=" * 80)
    for rank, r in enumerate(results[:n], 1):
        pp = r["params"]
        mm = r["metrics"]
        ss = r["score"]
        s0n = MOTOR_DATABASE[pp["s0_main"]][1]
        s1n = MOTOR_DATABASE[pp["s1_main"]][1]
        legal = "✓" if ss.get("is_legal", False) else "✗ ILLEGAL"
        print(f"\n  #{rank:2d}  [{legal}]  score={ss['score']:10.0f}  "
              f"apogee={mm.get('apogee_m', 0):8.1f}m  "
              f"err={ss.get('apogee_err_m', 9999):+7.2f}m")
        print(f"       S0={s0n}  S1={s1n}  "
              f"d0={pp['s0_retro_delay']:.1f}s  d1={pp['s1_retro_delay']:.1f}s  "
              f"az={pp.get('launch_azimuth', 90):.0f}°  "
              f"nm={pp.get('nose_mass_kg', 0)*1000:.0f}g")
        print(f"       apogee_E={ss.get('E_ap', 0):+7.1f}m  N={ss.get('N_ap', 0):+7.1f}m")
        print(f"       touch_E={ss.get('mean_E', 0):+7.1f}m  N={ss.get('mean_N', 0):+7.1f}m  "
              f"V_mean={ss.get('mean_V', 0):.2f}m/s  "
              f"prop={ss.get('m_prop_kg', 0):.3f}kg")
        print(f"       penalties: "
              f"apogee_alt={ss.get('apogee_alt_pen', 0):8.0f}  "
              f"apogee_h={ss.get('apogee_horiz_pen', 0):8.0f}  "
              f"touch_pos={ss.get('touch_pos_pen', 0):8.0f}  "
              f"touch_vel={ss.get('touch_vel_pen', 0):8.0f}  "
              f"prop={ss.get('prop_pen', 0):6.0f}")
        if ss.get("violations"):
            for v in ss["violations"]:
                print(f"       ⚠ {v}")
    print()


def save_results(results, label, out_dir="designs/osifog_level3"):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{label}_results.json")
    with open(path, "w") as f:
        json.dump(results[:50], f, indent=2, default=str)
    print(f"  Results saved: {path}")
    return path


def save_ork(p, wind, label, out_dir="designs/osifog_level3"):
    os.makedirs(out_dir, exist_ok=True)
    pp = {**p, "wind_levels": wind}
    ork = generate_ork(pp)
    path = os.path.join(out_dir, f"{label}.ork")
    save_simulated_ork(ork, path)
    print(f"  .ork saved: {path}")
    return path


# ═══════════════════════════════════════════════════════════════
# Main — 3-phase sweep pipeline
# ═══════════════════════════════════════════════════════════════
def main():
    print("=" * 80)
    print("  OSIFOG Level 3 — Retro-Propulsive Sweep  [CORRECTED SCORING]")
    print("  Target: 900,000 points | 3000m apogee | Both stages < 5 m/s")
    print("=" * 80)
    print()

    wind = parse_wind_csv(WIND_CSV)
    print(f"  Wind levels loaded: {len(wind)}")
    print(f"  Surface wind: {wind[0][1]:.1f} m/s from {wind[0][2]:.0f}°")
    print(f"  Launch rod: {LAUNCH_ROD_M}m (maximum allowed)")
    print()

    helper = init_or()

    # ── Phase 1: Coarse motor + direction sweep ──
    print("─" * 60)
    print("  PHASE 1: Coarse motor + launch direction sweep")
    print("─" * 60)
    grid1 = build_motor_grid(wind, retro_idx=0)  # F50T retro
    print(f"  Grid size: {len(grid1)} candidates")
    print(f"  Sustainer motors: {[MOTOR_DATABASE[i][1] for i in SUSTAINER_MAINS]}")
    print(f"  Booster motors:   {[MOTOR_DATABASE[i][1] for i in BOOSTER_MAINS]}")
    print()

    results1 = run_sweep(grid1, helper, label="Phase1")
    print_top_results(results1, n=10, label="PHASE 1 TOP 10")
    save_results(results1, "phase1")

    # Filter to legal, apogee within 200m of target
    legal_r1 = [r for r in results1
                if r["score"].get("is_legal", False)
                and abs(r["score"].get("apogee_err_m", 9999)) < 200]

    if not legal_r1:
        print("  No legal candidates with apogee near 3000m in Phase 1!")
        print("  Relaxing to all legal results...")
        legal_r1 = [r for r in results1 if r["score"].get("is_legal", False)]

    if not legal_r1:
        print("  No legal results at all. Check motor selection and geometry.")
        return results1

    # ── Phase 2: Fine timing + ballast sweep ──
    print("─" * 60)
    print("  PHASE 2: Fine retro timing + ballast optimization")
    print("─" * 60)
    top5 = legal_r1[:5]
    all_results2 = []
    for rank, r in enumerate(top5, 1):
        bp = r["params"]
        print(f"\n  Refining candidate #{rank}: "
              f"S0={MOTOR_DATABASE[bp['s0_main']][1]}  "
              f"S1={MOTOR_DATABASE[bp['s1_main']][1]}  "
              f"score={r['score']['score']:.0f}")
        grid2 = build_fine_grid(bp, wind)
        print(f"  Fine grid: {len(grid2)} candidates")
        res2 = run_sweep(grid2, helper, label=f"P2-cand{rank}")
        all_results2.extend(res2)

    all_results2.sort(key=lambda r: r["score"]["score"], reverse=True)
    print_top_results(all_results2, n=10, label="PHASE 2 TOP 10")
    save_results(all_results2, "phase2")

    legal_r2 = [r for r in all_results2
                if r["score"].get("is_legal", False)
                and abs(r["score"].get("apogee_err_m", 9999)) < 50]

    if not legal_r2:
        legal_r2 = [r for r in all_results2 if r["score"].get("is_legal", False)]

    if not legal_r2:
        legal_r2 = all_results2

    # ── Phase 3: Precision timing ──
    print("─" * 60)
    print("  PHASE 3: Precision timing (0.5s increments)")
    print("─" * 60)
    top3 = legal_r2[:3]
    all_results3 = []
    for rank, r in enumerate(top3, 1):
        bp = r["params"]
        print(f"\n  Precision sweep candidate #{rank}: score={r['score']['score']:.0f}")
        grid3 = build_precision_grid(bp, wind)
        print(f"  Precision grid: {len(grid3)} candidates")
        res3 = run_sweep(grid3, helper, label=f"P3-cand{rank}")
        all_results3.extend(res3)

    all_results3.sort(key=lambda r: r["score"]["score"], reverse=True)
    print_top_results(all_results3, n=10, label="PHASE 3 TOP 10")
    save_results(all_results3, "phase3")

    # ── Save winner ──
    final_winner = all_results3[0] if all_results3 else (
        all_results2[0] if all_results2 else results1[0]
    )

    best_p = final_winner["params"]
    print()
    print("=" * 80)
    print("  FINAL WINNER")
    print("=" * 80)
    ss = final_winner["score"]
    mm = final_winner["metrics"]
    print(f"  Score:          {ss['score']:,.0f} / 900,000")
    print(f"  Apogee:         {mm.get('apogee_m', 0):.2f}m  (err={ss.get('apogee_err_m',0):+.2f}m)")
    print(f"  Apogee pos:     E={ss.get('E_ap',0):+.1f}m  N={ss.get('N_ap',0):+.1f}m")
    print(f"  Touch mean:     E={ss.get('mean_E',0):+.1f}m  N={ss.get('mean_N',0):+.1f}m  V={ss.get('mean_V',0):.2f}m/s")
    print(f"  Propellant:     {ss.get('m_prop_kg',0):.3f} kg")
    print(f"  Legal:          {ss.get('is_legal', False)}")
    print()

    ork_path = save_ork(best_p, wind, "falcon_winner")

    # Save config
    config = {
        "winner": {
            "sustainer": {
                "main_motor":    MOTOR_DATABASE[best_p["s0_main"]][1],
                "retro_motor":   MOTOR_DATABASE[best_p["s0_retro"]][1],
                "body_radius_mm": int(best_p["s0_body_rad"] * 1000),
                "body_length_m":  best_p["s0_body_len"],
                "nose_mass_kg":   best_p.get("nose_mass_kg", 0.1),
                "mid_ballast_kg": best_p.get("s0_mid_ballast_kg", 0.0),
                "aft_ballast_kg": best_p.get("s0_aft_ballast_kg", 0.0),
                "retro_delay_s":  best_p["s0_retro_delay"],
            },
            "booster": {
                "main_motor":    MOTOR_DATABASE[best_p["s1_main"]][1],
                "retro_motor":   MOTOR_DATABASE[best_p["s1_retro"]][1],
                "body_radius_mm": int(best_p["s1_body_rad"] * 1000),
                "body_length_m":  best_p["s1_body_len"],
                "mid_ballast_kg": best_p.get("s1_mid_ballast_kg", 0.0),
                "aft_ballast_kg": best_p.get("s1_aft_ballast_kg", 0.0),
                "retro_delay_s":  best_p["s1_retro_delay"],
            },
            "launch": {
                "rod_length_m":  LAUNCH_ROD_M,
                "azimuth_deg":   best_p.get("launch_azimuth", 270.0),
                "angle_deg":     best_p.get("launch_angle_deg", 0.0),
            },
        },
        "score": {k: v for k, v in ss.items() if not isinstance(v, list)},
        "metrics": {k: v for k, v in mm.items()
                    if k not in ("stage_landings",) and not isinstance(v, (dict, list))},
    }
    config_path = os.path.join("designs", "osifog_level3", "falcon_winner_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  Config saved: {config_path}")

    return all_results3 or all_results2 or results1


if __name__ == "__main__":
    main()
