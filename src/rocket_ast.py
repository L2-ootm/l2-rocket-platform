import math
import uuid
import random
import xml.etree.ElementTree as ET

# Reuse the database from rocket_forge
from rocket_forge import MOTOR_DATABASE, MATERIALS, NOSE_SHAPES, FIN_CROSS_SECTIONS

# Legal structural materials for load-bearing tubes (motor mounts,
# centering rings) that need real strength/heat resistance, not just any
# MATERIALS entry within the mission-wide legal density range (170-11340
# kg/m3, l2_engine/src/ast.rs::is_density_in_allowed_range). Every entry
# here is a plausible real motor-mount/ring material; excludes balsa/
# cardboard (too weak to hold a firing motor) and lead (nonsensical mass
# for a structural tube even though its density is technically legal).
# "kraft" first/default to match the validated 839k reference (its own
# "Sustainer/Booster Main Motor Mount" both use "Kraft phenolic"), the
# rest give real design-space freedom the generator wasn't previously
# exploring at all (motor mount material was hardcoded, not a parameter).
MOUNT_MATERIAL_CHOICES = ["kraft", "fiberglass", "carbon", "aluminum"]

# One shared launch-environment contract feeds both the generated OpenRocket
# XML and the Rust AST evaluator.  Keeping these values in one place prevents
# parity runs from silently comparing a windy 6-DOF OR flight with a vertical,
# zero-wind Rust flight.
OPENROCKET_SIMULATION_DEFAULTS = {
    "launch_rod_length_m": 2.0,
    "launch_rod_angle_rad": 0.0,
    "launch_rod_direction_rad": math.pi / 2.0,
    "wind_speed_mps": 2.0,
    "wind_direction_rad": math.pi / 2.0,
    # relative_humidity feeds Rust's own atmosphere model only. OpenRocket
    # 24.12's real, saved <atmosphere model="extendedisa"> block never
    # includes a <baserelativehumidity> child at all (confirmed by
    # inspecting designs/osifog_level3/osifog_physical_839k_falcon.ork's
    # actual saved XML directly) -- a prior version of this comment
    # claimed extendedisa "accepts" humidity and emitted the tag anyway,
    # which reproduced a real, user-visible "Unknown text in element
    # 'baserelativehumidity', ignoring" warning on load. ASTCompiler.compile
    # no longer emits it; keep this key only for Rust's use.
    "relative_humidity": 0.0,
    "base_temperature_k": 288.15,
    "base_pressure_pa": 101325.0,
}

OPENROCKET_COMPONENT_TAGS = {
    "stage",
    "podset",
    "nosecone",
    "bodytube",
    "innertube",
    "parachute",
    "masscomponent",
    "freeformfinset",
    "trapezoidfinset",
    "ellipticalfinset",
    "launchlug",
    "shockcord",
    "transition",
    "tubecoupler",
    "centeringring",
}


def _anti_tumble_script():
    """The official OSIFOG anti-tumble simulation-extension script (blocks
    a TUMBLE flight event from silently aborting the sim). Single source
    of truth is `osifog_sweep.ANTI_TUMBLE_SCRIPT` -- lazily imported (same
    pattern as octaweb_motor_mounts' `_falcon_cluster_geometry` import,
    confirmed side-effect-free / no eager JVM startup) rather than
    duplicated, so this pipeline can never silently drift from the
    official script `osifog_sweep.validate_anti_tumble_extensions` checks
    saved .ork files against.

    This was missing entirely from ASTCompiler's output -- confirmed via
    grep, `rocket_ast.py`/`organic_loop.py` never referenced
    ScriptingExtension/ANTI_TUMBLE_SCRIPT/TUMBLE before this fix, even
    though the retired legacy pipeline (`osifog_sweep.py`) that this one
    replaced always included it. Every .ork this pipeline has ever
    compiled was missing the listener that keeps a tumble event from
    aborting the simulation."""
    from osifog_sweep import ANTI_TUMBLE_SCRIPT
    return ANTI_TUMBLE_SCRIPT


def _add_stable_component_ids(xml):
    root = ET.fromstring(xml)
    counters = {}
    for element in root.iter():
        if element.tag not in OPENROCKET_COMPONENT_TAGS:
            continue
        counters[element.tag] = counters.get(element.tag, 0) + 1
        identity = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"l2-osifog/ast/{element.tag}/{counters[element.tag]}",
        )
        component_id = ET.Element("id")
        component_id.text = str(identity)
        element.insert(1 if len(element) and element[0].tag == "name" else 0, component_id)
    serialized = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + serialized

def motor_pool_indices(allowed_designations):
    """Resolves a mission's `motor_pool.allowed_designations` list to indices
    into MOTOR_DATABASE. Returns None (unrestricted) when no pool is given.
    Raises if a pool is given but matches nothing -- a typo'd designation
    should fail loudly, not silently fall back to the full motor database."""
    if not allowed_designations:
        return None
    allowed = set(allowed_designations)
    indices = [i for i, motor in enumerate(MOTOR_DATABASE) if motor[1] in allowed]
    if not indices:
        raise ValueError(f"motor_pool {sorted(allowed)!r} matches no motor in MOTOR_DATABASE")
    return indices


def _select_motor_index(motor_pool, default_floor=0):
    pool_indices = motor_pool_indices(motor_pool)
    if pool_indices:
        return random.choice(pool_indices)
    return random.randint(default_floor, len(MOTOR_DATABASE) - 1)


class ASTNode:
    def __init__(self, node_type, **kwargs):
        self.node_type = node_type
        self.params = kwargs

    def mutate(self, rate=0.2, motor_pool=None, retro_motor_pool=None):
        if self.node_type == "NOSE_CONE":
            self.params["length"] = _jitter(self.params.get("length", 0.3), 0.08, 0.12, 0.9)
            if random.random() < rate:
                self.params["shape"] = random.choice(NOSE_SHAPES)
            if random.random() < rate:
                self.params["material"] = random.choice(list(MATERIALS.keys()))
        elif self.node_type == "BODY_TUBE":
            self.params["length"] = _jitter(self.params.get("length", 0.8), 0.18, 0.2, 2.0)
            self.params["radius"] = _jitter(self.params.get("radius", 0.04), 0.012, 0.018, 0.11)
            if random.random() < rate:
                self.params["material"] = random.choice(list(MATERIALS.keys()))
            # Same dead-parameter class as the fin-material bug fixed in a
            # prior session: create_random_ast never set an explicit
            # "thickness" here either, and NOTHING here ever varied it, so
            # every candidate this pipeline has ever generated silently fell
            # through to _sanitize_body's 0.002m (2mm) default -- the GA had
            # no way to trade wall thickness for mass, directly relevant to
            # a user-reported "why is the tube always thick cardboard/paper
            # when it could be thinner to let the motor lift it" complaint.
            # Bounds match _sanitize_body's own clamp (0.001 to
            # min(0.008, body_radius*0.4)) so mutation and sanitize agree;
            # the body_radius-relative upper bound is enforced there, not
            # here, since mutate() doesn't always know the final radius.
            self.params["thickness"] = _jitter(self.params.get("thickness", 0.002), 0.0012, 0.001, 0.008)
        elif self.node_type == "MOTOR_MOUNT":
            is_retro = self.params.get("role") == "retro"
            pool = retro_motor_pool if is_retro else motor_pool
            if random.random() < rate:
                self.params["motor_index"] = _select_motor_index(pool, default_floor=0)
                self.params["motor_designation"] = MOTOR_DATABASE[self.params["motor_index"]][1]
            if is_retro:
                # Ignition delay is the load-bearing search variable for a
                # retro burn (must oppose velocity near touchdown, not near
                # apogee) -- jitter it every mutation, not just occasionally.
                self.params["ignition_delay"] = _jitter(
                    self.params.get("ignition_delay", 5.0), 2.5, 0.0, 60.0
                )
                if "radial_offset_m" in self.params and random.random() < rate:
                    self.params["radial_offset_m"] = _jitter(
                        self.params["radial_offset_m"], 0.01, 0.001, 0.3
                    )
            elif random.random() < rate * 0.5:
                self.params["ignition"] = random.choice(["automatic", "burnout"])
        elif self.node_type == "FIN_SET":
            is_forward_flap = self.params.get("role") == "forward_flap"
            self.params["sweep"] = _jitter(self.params.get("sweep", 30.0), 8.0, 0.0, 65.0)
            if is_forward_flap:
                # Matches _sanitize_fin's discriminated forward_flap bounds --
                # without this, jitter could drift a flap toward main-fin-
                # sized bounds (up to 0.35m root), re-introducing the
                # oversized-flap static-margin problem mutation was supposed
                # to explore around, not undo.
                self.params["root"] = _jitter(self.params.get("root", 0.09), 0.02, 0.04, 0.20)
                self.params["height"] = _jitter(self.params.get("height", 0.06), 0.015, 0.03, 0.15)
            else:
                # Upper bounds match _sanitize_fin's widened main-fin ceiling
                # (0.55/0.4) -- keeping them in sync prevents mutation from
                # silently clamping a freshly-generated large fin back down
                # the moment it's touched.
                self.params["root"] = _jitter(self.params.get("root", 0.1), 0.05, 0.03, 0.55)
                self.params["height"] = _jitter(self.params.get("height", 0.06), 0.035, 0.02, 0.4)
            if random.random() < rate:
                self.params["count"] = random.choice([3, 4, 5, 6])
            if random.random() < rate:
                self.params["cross_section"] = random.choice(FIN_CROSS_SECTIONS)
            if random.random() < rate:
                # Same per-part material freedom as motor mounts/rings --
                # was never mutated here, so every fin stayed permanently
                # stuck at _sanitize_fin's "fiberglass" fallback (set at
                # creation only as of this same fix; nothing before it ever
                # varied fin material at all).
                self.params["material"] = random.choice(list(MATERIALS.keys()))
            if random.random() < rate:
                self.params["thickness"] = _jitter(self.params.get("thickness", 0.003), 0.0015, 0.001, 0.012)
            if "position_from_top_m" in self.params:
                self.params["position_from_top_m"] = _jitter(
                    self.params["position_from_top_m"], 0.02, 0.0, 0.5
                )
        elif self.node_type == "PARACHUTE":
            self.params["diameter"] = _jitter(self.params.get("diameter", 0.5), 0.18, 0.15, 2.0)
            self.params["deploy"] = "apogee"
        elif self.node_type == "PAYLOAD":
            self.params["mass"] = _jitter(self.params.get("mass", 0.5), 0.35, 0.0, 10.0)

    def to_dict(self):
        return {"type": self.node_type, "params": self.params}

    @classmethod
    def from_dict(cls, data):
        return cls(data["type"], **data["params"])


def _jitter(value, step, lower, upper):
    return min(upper, max(lower, value + random.uniform(-step, step)))


def _finite_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _clamp_float(value, default, lower, upper):
    return min(upper, max(lower, _finite_float(value, default)))


def _clamp_int(value, default, lower, upper):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(upper, max(lower, parsed))


def _copy_node(node):
    return ASTNode(node.node_type, **dict(node.params))


def _motor_index(params, default=None):
    designation = params.get("motor_designation")
    if "motor_index" not in params and designation:
        for index, motor in enumerate(MOTOR_DATABASE):
            if motor[1] == designation:
                return index
        raise ValueError(f"unknown motor designation {designation!r}")
    fallback = len(MOTOR_DATABASE) - 1 if default is None else default
    return _clamp_int(params.get("motor_index", fallback), fallback, 0, len(MOTOR_DATABASE) - 1)


def _sanitize_motor(node, default=None):
    sanitized = _copy_node(node)
    idx = _motor_index(sanitized.params, default)
    sanitized.params["motor_index"] = idx
    sanitized.params["motor_designation"] = MOTOR_DATABASE[idx][1]
    ignition = sanitized.params.get("ignition", "automatic")
    sanitized.params["ignition"] = "burnout" if ignition in {"burnout", "ignitiondelay"} else "automatic"
    sanitized.params["ignition_delay"] = _clamp_float(
        sanitized.params.get("ignition_delay", 0.0), 0.0, 0.0, 1000.0
    )
    # mount_material/ring_material are only ever set by this pipeline from
    # MOUNT_MATERIAL_CHOICES, but sanitize defensively anyway (e.g. a
    # future mutation path touching these directly) -- fall back to
    # "kraft" (a real structural material, matching the 839k reference),
    # not _mat_xml's generic "cardboard" fallback which would be a poor
    # structural choice for a motor mount.
    if sanitized.params.get("mount_material") not in MOUNT_MATERIAL_CHOICES:
        sanitized.params["mount_material"] = "kraft"
    sanitized.params["mount_material_density"] = MATERIALS[sanitized.params["mount_material"]][2]
    if "ring_material" in sanitized.params and sanitized.params["ring_material"] not in MOUNT_MATERIAL_CHOICES:
        sanitized.params["ring_material"] = "kraft"
    return sanitized


def _sanitize_radial_assembly(node):
    """Sanitize one parallel-axis pod template without flattening its topology."""
    sanitized = _copy_node(node)
    raw_children = sanitized.params.get("children", [])
    children = [
        child if isinstance(child, ASTNode) else ASTNode.from_dict(child)
        for child in raw_children
    ]
    motors = [_sanitize_motor(child) for child in children if child.node_type == "MOTOR_MOUNT"]
    if not motors:
        raise ValueError(f"{node.node_type} requires a MOTOR_MOUNT child")
    body_source = next(
        (child for child in children if child.node_type == "BODY_TUBE"),
        ASTNode("BODY_TUBE", length=1.0, radius=0.04, material="fiberglass"),
    )
    body = _sanitize_body(body_source, motors)
    radius = body.params["radius"]
    nose_source = next(
        (child for child in children if child.node_type == "NOSE_CONE"),
        ASTNode("NOSE_CONE", shape="ogive", length=max(0.12, radius * 3.0), material="fiberglass"),
    )
    fins = [_sanitize_fin(child, radius, body.params["length"]) for child in children if child.node_type == "FIN_SET"]
    template = [_sanitize_nose(nose_source, radius), body, *fins, *motors, ASTNode("CLOSE_BODY")]
    sanitized.params["children"] = [child.to_dict() for child in template]
    sanitized.params["instance_count"] = _clamp_int(
        sanitized.params.get("instance_count", 1), 1, 1, 16
    )
    sanitized.params["radial_offset_m"] = _clamp_float(
        sanitized.params.get("radial_offset_m", 0.1), 0.1, 0.001, 1.0
    )
    sanitized.params["angle_offset_deg"] = _finite_float(
        sanitized.params.get("angle_offset_deg", 0.0), 0.0
    )
    sanitized.params["aero_interference_factor"] = _clamp_float(
        sanitized.params.get("aero_interference_factor", 1.1), 1.1, 1.0, 3.0
    )
    return sanitized


def _sanitize_body(node, motors):
    """Sanitize the body tube and, critically, push any off-centerline motor
    (e.g. a role="retro" mount added alongside a centerline "main" mount) far
    enough out that its tube cannot physically overlap the centerline motor's
    tube -- then size the bore to actually contain the farthest one. Silent
    radial overlap between two motor mounts is exactly the kind of
    non-physical geometry the mission bans."""
    sanitized = _copy_node(node)
    motor_radii = [MOTOR_DATABASE[_motor_index(m.params)][2] / 2.0 for m in motors]
    max_motor_length = max((MOTOR_DATABASE[_motor_index(m.params)][3] for m in motors), default=0.12)
    clearance = 0.002

    centerline_radius = max(
        (r for m, r in zip(motors, motor_radii) if m.params.get("radial_offset_m", 0.0) <= 0.0),
        default=0.0,
    )
    required_bore = centerline_radius
    for m, r in zip(motors, motor_radii):
        offset = _finite_float(m.params.get("radial_offset_m", 0.0), 0.0)
        if offset > 0.0:
            min_offset = centerline_radius + r + clearance
            offset = max(offset, min_offset)
            m.params["radial_offset_m"] = offset
            required_bore = max(required_bore, offset + r)
        else:
            required_bore = max(required_bore, r)

    thickness = _clamp_float(sanitized.params.get("thickness", 0.002), 0.002, 0.001, 0.008)
    required_radius = required_bore + 0.001 + thickness + 0.002
    sanitized.params["thickness"] = thickness
    sanitized.params["radius"] = max(_clamp_float(sanitized.params.get("radius", 0.05), 0.05, 0.018, 0.18), required_radius)
    sanitized.params["length"] = max(_clamp_float(sanitized.params.get("length", 1.0), 1.0, 0.25, 3.0), max_motor_length + 0.02)
    if sanitized.params.get("material") not in MATERIALS:
        sanitized.params["material"] = "cardboard"
    return sanitized


def _sanitize_nose(node, body_radius):
    sanitized = _copy_node(node)
    sanitized.params["length"] = _clamp_float(sanitized.params.get("length", 0.35), 0.35, 0.12, 0.9)
    sanitized.params["thickness"] = _clamp_float(sanitized.params.get("thickness", 0.002), 0.002, 0.001, min(0.008, body_radius * 0.4))
    if sanitized.params.get("shape") not in NOSE_SHAPES:
        sanitized.params["shape"] = "haack"
    if sanitized.params.get("material") not in MATERIALS:
        sanitized.params["material"] = "fiberglass"
    return sanitized


def _sanitize_fin(node, body_radius, body_length=None):
    """Sanitize a FIN_SET. `role="forward_flap"` (Starship-style nose flap,
    used to force passive tail-first descent -- see designs/osifog_level3/
    starship_best_genome.json) gets its own, much smaller/nose-pinned bounds
    instead of the tail-fin envelope; every other role keeps prior behavior."""
    sanitized = _copy_node(node)
    is_forward_flap = sanitized.params.get("role") == "forward_flap"

    if is_forward_flap:
        min_root, max_root, default_root = 0.04, 0.20, 0.09
        min_height, max_height, default_height = 0.03, 0.15, 0.06
        default_count, default_sweep = 3, 5.0
    else:
        min_root = max(0.03, body_radius * 1.2)
        max_root, default_root = 0.55, body_radius * 2.0
        min_height = max(0.02, body_radius * 0.7)
        max_height, default_height = 0.4, body_radius * 1.2
        default_count, default_sweep = 4, 30.0

    sanitized.params["count"] = _clamp_int(sanitized.params.get("count", default_count), default_count, 3, 6)
    sanitized.params["sweep"] = _clamp_float(sanitized.params.get("sweep", default_sweep), default_sweep, 0.0, 65.0)
    sanitized.params["root"] = _clamp_float(sanitized.params.get("root", default_root), default_root, min_root, max_root)
    sanitized.params["height"] = _clamp_float(sanitized.params.get("height", default_height), default_height, min_height, max_height)
    sanitized.params["tip"] = _clamp_float(sanitized.params.get("tip", sanitized.params["root"] * 0.35), sanitized.params["root"] * 0.35, 0.01, sanitized.params["root"])
    sanitized.params["thickness"] = _clamp_float(sanitized.params.get("thickness", 0.003), 0.003, 0.001, 0.012)
    if sanitized.params.get("cross_section") not in FIN_CROSS_SECTIONS:
        sanitized.params["cross_section"] = "airfoil"
    if sanitized.params.get("material") not in MATERIALS:
        sanitized.params["material"] = "fiberglass"

    if is_forward_flap:
        # Pinned near the nose (0-0.15 m from body top) so the flap creates a
        # forward drag/moment center -- the mechanism that forces tail-first
        # descent. `body_length` bounds the clamp when known.
        nose_margin = 0.15 if body_length is None else max(0.0, min(0.15, body_length))
        default_position = min(0.05, nose_margin)
        sanitized.params["position_from_top_m"] = _clamp_float(
            sanitized.params.get("position_from_top_m", default_position),
            default_position, 0.0, nose_margin,
        )
        sanitized.params["role"] = "forward_flap"
    elif "position_from_top_m" in sanitized.params:
        upper_bound = body_length if body_length is not None else 3.0
        sanitized.params["position_from_top_m"] = _clamp_float(
            sanitized.params["position_from_top_m"], 0.0, 0.0, max(upper_bound, 0.0)
        )

    return sanitized


def _sanitize_parachute(node):
    sanitized = _copy_node(node)
    sanitized.params["deploy"] = "apogee"
    sanitized.params["diameter"] = _clamp_float(sanitized.params.get("diameter", 0.6), 0.6, 0.15, 2.0)
    sanitized.params["altitude"] = _clamp_float(sanitized.params.get("altitude", 300.0), 300.0, 50.0, 1500.0)
    sanitized.params["delay"] = 0.0
    return sanitized


def _sanitize_payload(node):
    sanitized = _copy_node(node)
    sanitized.params["mass"] = _clamp_float(sanitized.params.get("mass", 0.5), 0.5, 0.0, 200.0)
    return sanitized


def _sanitize_ballast(node):
    sanitized = _copy_node(node)
    sanitized.params["mass"] = _clamp_float(sanitized.params.get("mass", 0.1), 0.1, 0.001, 50.0)
    position = sanitized.params.get("position", "forward")
    sanitized.params["position"] = position if position in ("forward", "aft") else "forward"
    axial_offset_m = sanitized.params.get("axial_offset_m")
    if axial_offset_m is not None:
        sanitized.params["axial_offset_m"] = _finite_float(axial_offset_m, 0.0)
    material = sanitized.params.get("material")
    if material is not None and material not in MATERIALS:
        sanitized.params["material"] = "kraft"
    # Shaped-rod fields (octaweb ballast rods filling the gaps between an
    # octaweb cluster's 3 main motors) -- absent for the plain lumped-mass
    # ballast this node type has always supported, so every clamp here is
    # presence-checked and a no-op for that existing usage.
    radius = sanitized.params.get("radius")
    if radius is not None:
        sanitized.params["radius"] = _clamp_float(radius, 0.02, 0.005, 0.3)
    length = sanitized.params.get("length")
    if length is not None:
        sanitized.params["length"] = _clamp_float(length, 0.1, 0.02, 2.0)
    instance_count = sanitized.params.get("instance_count")
    if instance_count is not None:
        sanitized.params["instance_count"] = _clamp_int(instance_count, 1, 1, 8)
    radial_offset_m = sanitized.params.get("radial_offset_m")
    if radial_offset_m is not None:
        sanitized.params["radial_offset_m"] = _clamp_float(radial_offset_m, 0.0, 0.0, 1.0)
    angle_offset_deg = sanitized.params.get("angle_offset_deg")
    if angle_offset_deg is not None:
        sanitized.params["angle_offset_deg"] = _finite_float(angle_offset_deg, 0.0)
    return sanitized


def _split_stages(ast_nodes):
    stages = []
    current = None
    for node in ast_nodes:
        if node.node_type == "STAGE":
            if current is not None:
                stages.append(current)
            current = [_copy_node(node)]
        elif current is None:
            current = [ASTNode("STAGE", name="Evolved Sustainer"), _copy_node(node)]
        else:
            current.append(_copy_node(node))
    if current is not None:
        stages.append(current)
    return stages or [[ASTNode("STAGE", name="Evolved Sustainer")]]


def sanitize_ast_for_openrocket(ast_nodes):
    """Return a stage-aware AST that fits motors and avoids known OR warnings."""
    sanitized = []
    # Tracks (body_tube_node, original_radius, this_stage's_fin_nodes) so
    # the widening pass below can rescale fins proportionally when their
    # own stage's body radius changes -- without this, a fin sized
    # relative to its stage's ORIGINAL (pre-widen) radius silently ends up
    # undersized relative to the body once widening bumps that stage's
    # radius up to match a bigger sibling stage, which can violate the
    # fin/body proportionality every fin is supposed to keep for adequate
    # static margin (see the "Fins and Motor" comment in create_random_ast).
    stage_fin_tracking = []
    # Tracks (body_tube_node, this_stage's_motors) so the repair pass below
    # can re-tighten an octaweb stage's cage geometry against its FINAL
    # (post-widening) body radius -- see that pass's own comment for why
    # this is necessary (frozen/stale cage data surviving many generations
    # of elitism, and the diameter-continuity widening above never having
    # touched motor mounts at all).
    stage_motor_tracking = []
    for stage_idx, stage in enumerate(_split_stages(ast_nodes)):
        stage_node = _copy_node(stage[0])
        stage_node.params["name"] = stage_node.params.get("name") or ("Evolved Sustainer" if stage_idx == 0 else "Evolved Booster")
        components = stage[1:]

        motors = [_sanitize_motor(node) for node in components if node.node_type == "MOTOR_MOUNT"]
        if not motors:
            motors = [_sanitize_motor(ASTNode("MOTOR_MOUNT", motor_index=len(MOTOR_DATABASE) - 1, ignition="automatic"))]

        body_source = next((node for node in components if node.node_type == "BODY_TUBE"), ASTNode("BODY_TUBE", length=1.0, radius=0.05, material="cardboard"))
        body = _sanitize_body(body_source, motors)
        body_radius = body.params["radius"]

        sanitized.append(stage_node)
        if stage_idx == 0:
            nose_source = next((node for node in components if node.node_type == "NOSE_CONE"), ASTNode("NOSE_CONE", shape="haack", length=0.35, material="fiberglass"))
            sanitized.append(_sanitize_nose(nose_source, body_radius))

        sanitized.append(body)

        if stage_idx == 0 and stage_node.params.get("recovery") != "retro_only":
            chute_source = next((node for node in components if node.node_type == "PARACHUTE"), ASTNode("PARACHUTE", deploy="apogee", diameter=0.6))
            sanitized.append(_sanitize_parachute(chute_source))

        sanitized.extend(_sanitize_payload(node) for node in components if node.node_type == "PAYLOAD")
        sanitized.extend(_sanitize_ballast(node) for node in components if node.node_type == "BALLAST")

        body_length = body.params["length"]
        fins = [_sanitize_fin(node, body_radius, body_length) for node in components if node.node_type == "FIN_SET"]
        if not fins:
            fins = [_sanitize_fin(ASTNode("FIN_SET", count=4, sweep=30.0, root=max(0.12, body_radius * 1.6), height=max(0.06, body_radius * 0.8)), body_radius, body_length)]
        # De-duplicate down to at most one main fin set and one forward_flap
        # per stage -- a stale AST that already accumulated duplicates
        # BEFORE _structural_mutation's FIN_SET branch gained its
        # not-already-present guard (see that fix's own comment) carries
        # them forward through mutation/crossover regardless of the guard,
        # since the guard only stops NEW duplicates, it doesn't remove
        # existing ones. Confirmed as a real live-campaign case: a candidate
        # exported right after that mutation fix still had 3 overlapping
        # "Evolved Fins" on one stage, inherited from before the fix went
        # live. Keep the first of each role -- arbitrary but deterministic,
        # and no worse than whichever one happened to be first in the AST.
        seen_main_fin = False
        seen_forward_flap = False
        deduped_fins = []
        for fin in fins:
            is_forward_flap = fin.params.get("role") == "forward_flap"
            if is_forward_flap:
                if seen_forward_flap:
                    continue
                seen_forward_flap = True
            else:
                if seen_main_fin:
                    continue
                seen_main_fin = True
            deduped_fins.append(fin)
        fins = deduped_fins
        sanitized.extend(fins)
        stage_fin_tracking.append((body, body_radius, fins))
        stage_motor_tracking.append((body, motors))
        sanitized.extend(motors)
        sanitized.append(ASTNode("CLOSE_BODY"))
        sanitized.extend(
            _sanitize_radial_assembly(node)
            for node in components
            if node.node_type in {"POD", "STRAP_ON"}
        )

    # Each stage's BODY_TUBE was sanitized independently above -- a stage
    # whose own motors need a bigger bore gets its radius bumped up in
    # isolation, which can silently reintroduce a diameter discontinuity
    # between stages even when create_random_ast started them all equal
    # (e.g. mutation later swaps one stage to a larger motor). OpenRocket
    # flags this as "diameter discontinuity" and it has no physical
    # transition-section component in this pipeline to make it a sensible
    # design, so every stage is widened to match the largest bore any stage
    # actually needs, keeping the whole airframe one constant diameter.
    body_tubes = [node for node in sanitized if node.node_type == "BODY_TUBE"]
    if len(body_tubes) > 1:
        max_radius = max(node.params["radius"] for node in body_tubes)
        for node in body_tubes:
            node.params["radius"] = max_radius

        # Widening a stage's body radius without touching its fins leaves
        # them sized relative to the OLD (smaller) radius -- confirmed as
        # a real, reproduced case (root/radius ratio dropping as low as
        # 0.74, well under the 1.2 minimum every fin is supposed to keep
        # for adequate static margin). Rescale root/height by the same
        # factor the body radius grew by, for every stage that was
        # actually widened, so fin-to-body proportionality survives the
        # widening pass instead of only holding for the biggest stage.
        for body_node, original_radius, fins_for_stage in stage_fin_tracking:
            if original_radius <= 0:
                continue
            new_radius = body_node.params["radius"]
            scale = new_radius / original_radius
            if abs(scale - 1.0) < 1e-9:
                continue
            for fin in fins_for_stage:
                # forward_flap fins use their own ABSOLUTE (not
                # body-radius-relative) size envelope -- see
                # _sanitize_fin's is_forward_flap branch -- so they are
                # not subject to the fin/body proportionality this
                # rescale exists to preserve, and must not be scaled.
                if fin.params.get("role") == "forward_flap":
                    continue
                fin.params["root"] *= scale
                fin.params["height"] *= scale
                if "tip" in fin.params:
                    fin.params["tip"] *= scale
                # Re-clamp to _sanitize_fin's own (radius-relative) bounds
                # for the NEW radius -- a fin already near the absolute
                # 0.55m/0.4m ceiling before widening could otherwise be
                # scaled past it.
                min_root = max(0.03, new_radius * 1.2)
                min_height = max(0.02, new_radius * 0.7)
                fin.params["root"] = min(max(fin.params["root"], min_root), 0.55)
                fin.params["height"] = min(max(fin.params["height"], min_height), 0.4)
                fin.params["tip"] = min(max(fin.params.get("tip", 0.01), 0.01), fin.params["root"])

    # Repair octaweb (3-ring) cage geometry against each stage's FINAL body
    # radius, unconditionally, on every candidate, every generation --
    # confirmed via a live-campaign user screenshot as a real bug distinct
    # from (and found after) today's earlier octaweb-vs-body-radius fixes:
    # a main mount's radial_offset_m/cluster_scale/main_outer_radius_m/
    # retro_sleeve_outer_radius_m are set ONCE at creation time
    # (octaweb_motor_mounts) and never touched again by any mutation
    # operator or by the diameter-continuity widening pass just above --
    # they are frozen, dead data from whatever body radius existed at
    # creation. A candidate that survives many generations via elitism
    # (the whole point of elitism) while its BODY_TUBE radius changes for
    # any reason (this widening pass, or a sibling BODY_TUBE mutation on a
    # later restart with different code) keeps its ORIGINAL cage numbers
    # forever, producing exactly the visible symptom reported: 3 main
    # motors sitting near the (now much bigger) body wall while a stale,
    # small, unrelated retro_offset leaves the retro motor stranded far
    # from them. Recomputing fresh here, every time, makes cage geometry
    # self-healing regardless of mutation/widening history instead of
    # relying on it having been correct at creation and never touched
    # since. Also re-centers the retro mount (radial_offset_m=0.0) --
    # `ASTNode.mutate()` jitters a retro mount's radial_offset_m for the
    # legitimate plain-single-retro-motor topology (opposite-side offset is
    # valid there), but that same code path also touches octaweb retro
    # mounts, which must stay centered inside the sleeve by design.
    from osifog_sweep import _falcon_cluster_geometry, _min_octaweb_body_radius_m

    octaweb_stages = []
    for body_node, motors_for_stage in stage_motor_tracking:
        main_mount = next(
            (
                m for m in motors_for_stage
                if m.params.get("multiplicity") == 3
                and m.params.get("cluster_configuration") == "3-ring"
            ),
            None,
        )
        retro_mount = next(
            (m for m in motors_for_stage if m.params.get("role") == "retro"), None
        )
        if main_mount is None or retro_mount is None:
            continue
        octaweb_stages.append((body_node, main_mount, retro_mount))

    # ROOT CAUSE of a live campaign's persistent (74% of population)
    # motor_mount_collision near-misses, root-caused this session: a
    # mutation/crossover motor swap can change a stage's main motor to
    # something physically BIGGER than what that stage's CURRENT body
    # radius can host as a legal 3+1 cage (_falcon_cluster_geometry raises
    # ValueError in that case). The previous behavior was to `continue`,
    # leaving the STALE cage (radial_offset_m/main_outer_radius_m etc, sized
    # for whatever smaller motor was there before the swap) untouched.
    # l2_engine's enrich_ast_motor_mounts_multi then independently derives
    # the REAL mount_outer_radius_m from the new motor's actual thrust-curve
    # diameter (correctly, via `.max(stale_radius, real_diameter/2+wall)`)
    # -- so Rust ends up checking the REAL (bigger) motor radius against the
    # STALE (smaller) spacing, producing a genuine, guaranteed geometric
    # overlap that is NOT a formula bug in the collision check itself, just
    # self-inconsistent input data. Confirmed empirically: a live v9 elite
    # candidate's exact reported collision (dist=0.098871 < needed=0.102000)
    # matches L1500T's real 98mm diameter (needed = 2*(0.098/2+0.001)+0.002
    # = 0.102000 EXACTLY) against a stale cage cached from a smaller motor.
    #
    # FIXED in two phases so widening one stage never strands another
    # stage's already-tightened cage (the diameter-continuity pass above
    # already forced every BODY_TUBE to one shared radius -- widening only
    # the offending stage's body_node in a single pass would silently
    # reintroduce that exact discontinuity bug for whichever OTHER octaweb
    # stage gets processed before or after it):
    #   Phase 1 -- for every octaweb stage, check whether its motor pair
    #   fits its CURRENT (already-continuity-matched) body radius; if not,
    #   widen ALL body tubes (not just this stage's) to the minimum radius
    #   this pair actually needs, keeping the whole airframe one constant
    #   diameter throughout. This keeps the GA's freedom to explore bigger
    #   motors (useful for the min_thrust_to_weight/apogee search) by
    #   growing the airframe to match, rather than silently producing
    #   guaranteed-illegal candidates.
    #   Phase 2 -- re-tighten every octaweb stage's cage fresh against the
    #   now-FINAL (fully settled) body radius, so no stage is left stranded
    #   by a sibling stage's widening in phase 1.
    for body_node, main_mount, retro_mount in octaweb_stages:
        main_idx = main_mount.params["motor_index"]
        retro_idx = retro_mount.params["motor_index"]
        min_radius = _min_octaweb_body_radius_m(main_idx, retro_idx) + 0.001
        if min_radius > body_node.params["radius"]:
            for shared_body_node in body_tubes:
                shared_body_node.params["radius"] = max(
                    shared_body_node.params["radius"], min_radius
                )

    for body_node, main_mount, retro_mount in octaweb_stages:
        main_idx = main_mount.params["motor_index"]
        retro_idx = retro_mount.params["motor_index"]
        try:
            cage = _falcon_cluster_geometry(
                main_idx, retro_idx, body_node.params["radius"]
            )
        except ValueError:
            # Should not happen (phase 1 already widened every body tube to
            # at least this pair's algebraic floor) -- defensive fallback
            # only. Leave stale params; Rust's own checks will reject this
            # candidate on its actual current geometry regardless.
            continue
        cage = _tighten_octaweb_cage(cage)
        main_mount.params["radial_offset_m"] = cage["center_distance_m"]
        main_mount.params["cluster_scale"] = cage["cluster_scale"]
        main_mount.params["main_outer_radius_m"] = cage["main_outer_radius_m"]
        main_mount.params["retro_sleeve_outer_radius_m"] = cage["retro_sleeve_outer_radius_m"]
        retro_mount.params["radial_offset_m"] = 0.0

    return sanitized


class ASTCompiler:
    """Compiles an AST (list of structural nodes) into an OpenRocket XML."""
    
    def __init__(self):
        self.config_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, "l2-osifog/ast/default-flight-configuration")
        )

    def _mat_xml(self, mat_key):
        name, mtype, density = MATERIALS.get(mat_key, MATERIALS["cardboard"])
        return f'<material type="{mtype}" density="{density}">{name}</material>'

    @staticmethod
    def _resolved_motor(params):
        designation = params.get("motor_designation")
        if designation:
            for motor in MOTOR_DATABASE:
                if motor[1] == designation:
                    return motor
        return MOTOR_DATABASE[_motor_index(params)]

    def _motor_mount_xml(self, params, name="Motor Mount"):
        mfr, designation, motor_diam, motor_len, delay, digest = self._resolved_motor(params)
        ignition = params.get("ignition", "automatic")
        if ignition == "ignitiondelay":
            ignition = "burnout"
        # Octaweb-style ring mounts (see octaweb_motor_mounts) set these to
        # "3-ring"/a real clusterscale so OpenRocket's own native clustering
        # reproduces the same 3-motor ring Rust simulates via
        # multiplicity+instance_angle_step_deg -- ported from
        # osifog_sweep.py's already-validated "3+1" cage XML.
        cluster_configuration = params.get("cluster_configuration", "single")
        cluster_scale = params.get("cluster_scale", 1.0)
        # CRITICAL: radialposition/radialdirection and native OpenRocket
        # clustering are two DIFFERENT, mutually exclusive placement
        # mechanisms on the SAME <innertube> -- clusterscale alone derives
        # the ring radius from outerradius (confirmed against
        # osifog_sweep.py's own proven caller, which never passes a
        # nonzero radius_m for its 3-ring calls: `_motor_mount_xml(...,
        # cluster=cluster_name, cluster_scale=..., ...)` with radius_m left
        # at its 0.0 default). `radial_offset_m`/`radial_angle_deg` are
        # Rust-physics-only for a clustered mount (Rust independently
        # expands multiplicity via those params -- see
        # enrich_ast_motor_mounts_multi in ast.rs) and must NOT also drive
        # <radialposition>/<radialdirection> here, or OpenRocket compounds
        # the manual offset on top of its own auto-computed ring, shoving
        # every instance out of the body tube. Confirmed as a real,
        # reproduced bug (not a hypothesis) via a live-generated .ork's own
        # XML: <radialposition>0.069517</radialposition> stacked on
        # clusterconfiguration=3-ring/clusterscale=2.973027 for the SAME
        # <innertube> produced visibly broken geometry when opened in real
        # OpenRocket. Only "single"-configuration mounts (the plain
        # topology's retro motor, octaweb's own center retro sleeve) use
        # radialposition/radialdirection for real, deliberate offsets.
        is_clustered = cluster_configuration != "single"
        radial_offset_m = 0.0 if is_clustered else params.get("radial_offset_m", 0.0)
        # <radialdirection>/<clusterrotation> are in DEGREES, not radians --
        # confirmed empirically (getComponentLocations() against a real
        # OpenRocket JVM: a value computed as math.radians(60.0)=1.0472 and
        # written unconverted rendered at 1.047 degrees, not 60) and
        # independently via the 839k reference's own saved ballast rods
        # (<radialdirection> values of -90.0/30.0/150.0 -- unambiguously
        # degree-scale, 120 degrees is a meaningful spacing, 120 radians is
        # not). A prior version of this code called math.radians() here,
        # which silently shrank every nonzero angular offset to about
        # 1/57th of its intended value. See
        # .planning/ultra/ULTRAREVIEW-octaweb-ballast-radialdirection-units.md.
        radial_angle_deg = 0.0 if is_clustered else params.get("radial_angle_deg", 0.0)
        cluster_rotation_deg = params.get("radial_angle_deg", 0.0) if is_clustered else 0.0
        outer_radius = motor_diam / 2.0 + 0.001
        mount_length_m = motor_len + 0.02
        return f'''
              <innertube>
                <name>{name}</name>
                <position type="bottom">0.0</position>
                {self._mat_xml(params.get("mount_material", "kraft"))}
                <length>{mount_length_m:.6f}</length>
                <radialposition>{radial_offset_m:.6f}</radialposition>
                <radialdirection>{radial_angle_deg:.9f}</radialdirection>
                <outerradius>{outer_radius:.6f}</outerradius>
                <thickness>0.001</thickness>
                <clusterconfiguration>{cluster_configuration}</clusterconfiguration>
                <clusterscale>{cluster_scale:.6f}</clusterscale>
                <clusterrotation>{cluster_rotation_deg:.9f}</clusterrotation>
                <motormount>
                  <ignitionevent>{ignition}</ignitionevent>
                  <ignitiondelay>{params.get("ignition_delay", 0.0):.6f}</ignitiondelay>
                  <overhang>0.005</overhang>
                  <motor configid="{self.config_id}">
                    <manufacturer>{mfr}</manufacturer>
                    {f'<digest>{digest}</digest>' if digest else ''}
                    <designation>{designation}</designation>
                    <diameter>{motor_diam}</diameter>
                    <length>{motor_len}</length>
                    <delay>{delay}</delay>
                  </motor>
                </motormount>
              </innertube>'''

    def _octaweb_circumscribing_rings_xml(self, params, body_radius_m, body_length_m):
        """Emits the axial ring pair (one near the forward/ascent end of the
        main motor mount, one near its aft/thrust end) that holds the 3
        outer motors, per the user's reference design: the CENTER (retro)
        motor is held by mutual tangency with the 3 outer motors alone (no
        ring of its own), and the 3 outer motors are held by two rings --
        each ring's bore circumscribes the OUTSIDE of the whole 3-motor
        cluster, snug against the body tube's own inner wall.

        This replaced two earlier, both-wrong approaches: (1) sibling rings
        with their own <radialposition> -- confirmed via
        getComponentLocations() that OpenRocket's CenteringRing ignores
        radialposition entirely, rendering at (0,0,0) regardless; (2) rings
        NESTED per-tube (one snug ring per motor, including the retro) --
        geometrically correct positioning (nesting genuinely works, verified
        the same way), but the wrong DESIGN: it added a dedicated ring
        around the retro motor the user's reference does not have, and
        4 small per-tube rings instead of 2 rings encompassing the cluster.
        Centered on the body axis (radialposition=0) is not a workaround
        here -- it is what the actual design calls for, since a single
        ring is meant to touch the OUTSIDE of all 3 outer motors at once,
        not one individual tube.
        """
        center_distance = params["radial_offset_m"]
        main_outer = params["main_outer_radius_m"]
        cluster_envelope_radius = center_distance + main_outer
        # Snug against the body tube's own inner wall (2mm wall allowance,
        # matching _falcon_cluster_geometry's own body_inner convention),
        # never thinner than the cluster envelope itself even if the body
        # has generous extra room.
        ring_outer_radius = max(cluster_envelope_radius + 0.003, body_radius_m - 0.002)
        motor_len = MOTOR_DATABASE[_motor_index(params)][3]
        mount_length_m = motor_len + 0.02
        ring_length_m = 0.005
        blocks = []
        for name, position_top_m in (
            ("Forward", body_length_m - mount_length_m),
            ("Aft (Thrust)", body_length_m - ring_length_m),
        ):
            blocks.append(f'''
              <centeringring>
                <name>Octaweb Ring ({name})</name>
                <position type="top">{position_top_m:.9f}</position>
                {self._mat_xml(params.get("ring_material", "fiberglass"))}
                <length>{ring_length_m:.9f}</length>
                <radialposition>0.0</radialposition>
                <radialdirection>0.0</radialdirection>
                <outerradius>{ring_outer_radius:.9f}</outerradius>
                <innerradius>{cluster_envelope_radius:.9f}</innerradius>
              </centeringring>''')
        return "\n".join(blocks)

    def _fin_xml(self, params, name="Evolved Fins"):
        count = params.get("count", 4)
        sweep = math.radians(params.get("sweep", 30.0))
        height = params.get("height", 0.05)
        root = params.get("root", 0.1)
        tip = params.get("tip", root * 0.3)
        sweep_offset = height * math.tan(sweep)
        points = ((0.0, 0.0), (sweep_offset, height), (sweep_offset + tip, height), (root, 0.0))
        points_xml = "".join(
            f'<point x="{x:.6f}" y="{y:.6f}"/>' for x, y in points
        )
        position_from_top_m = params.get("position_from_top_m")
        position_xml = (
            f'<position type="top">{position_from_top_m:.6f}</position>'
            if position_from_top_m is not None
            else '<position type="bottom">0.0</position>'
        )
        # OpenRocket's real FinSet.CrossSection enum only has SQUARE/ROUNDED/
        # AIRFOIL -- "double-wedge" is a legitimate, richer drag-model
        # category Rust's own barrowman.rs computes for the sharp-edged
        # biconvex case (see its own test:
        # "OpenRocket loads organic double-wedge fins as its square cross-
        # section"), but writing the literal string "double-wedge" into
        # <crosssection> can never match any OpenRocket enum constant --
        # confirmed via a live OpenRocket JVM load producing a genuine
        # Warning.FILE_INVALID_PARAMETER ("Parametro invalido encontrado,
        # ignorando") on every candidate using it, silently falling back to
        # whatever cross-section OpenRocket defaults an unmatched value to.
        # Write the value OpenRocket actually collapses it to (confirmed by
        # that same Rust test) explicitly, so the compiled XML round-trips
        # clean instead of relying on undocumented silent-fallback behavior.
        # Rust's own AST/scoring is unaffected -- only this XML output
        # string changes, not `cross_section` param itself.
        cross_section = params.get("cross_section", "airfoil")
        or_cross_section = "square" if cross_section == "double-wedge" else cross_section
        return f'''
              <freeformfinset>
                <name>{name}</name>
                {position_xml}
                {self._mat_xml(params.get("material", "fiberglass"))}
                <fincount>{count}</fincount>
                <thickness>{params.get("thickness", 0.003):.6f}</thickness>
                <crosssection>{or_cross_section}</crosssection>
                <finpoints>{points_xml}</finpoints>
              </freeformfinset>'''

    def _radial_assembly_xml(self, params, kind):
        if kind == "STRAP_ON" and params.get("separable", False):
            raise ValueError("separable STRAP_ON requires an additional flight branch")
        children = [ASTNode.from_dict(child) for child in params["children"]]
        nose = next(child for child in children if child.node_type == "NOSE_CONE")
        body = next(child for child in children if child.node_type == "BODY_TUBE")
        motors = [child for child in children if child.node_type == "MOTOR_MOUNT"]
        fins = [child for child in children if child.node_type == "FIN_SET"]
        radius = body.params["radius"]
        nose_xml = f'''
              <nosecone>
                <name>{params.get("name", "Radial Assembly")} Nose</name>
                <finish>polished</finish>
                {self._mat_xml(nose.params.get("material", "fiberglass"))}
                <length>{nose.params.get("length", 0.2):.6f}</length>
                <thickness>{nose.params.get("thickness", 0.002):.6f}</thickness>
                <shape>{nose.params.get("shape", "ogive")}</shape>
                <shapeclipped>false</shapeclipped>
                <aftradius>{radius:.6f}</aftradius>
                <aftshoulderlength>0.0</aftshoulderlength>
                <aftshoulderradius>0.0</aftshoulderradius>
                <aftshoulderthickness>0.0</aftshoulderthickness>
                <aftshouldercapped>false</aftshouldercapped>
              </nosecone>'''
        internals = "".join(
            [self._motor_mount_xml(motor.params, "Pod Motor Mount") for motor in motors]
            + [self._fin_xml(fin.params, "Pod Fins") for fin in fins]
        )
        body_xml = f'''
              <bodytube>
                <name>{params.get("name", "Radial Assembly")} Body</name>
                <finish>polished</finish>
                {self._mat_xml(body.params.get("material", "fiberglass"))}
                <length>{body.params.get("length", 0.8):.6f}</length>
                <thickness>{body.params.get("thickness", 0.002):.6f}</thickness>
                <radius>{radius:.6f}</radius>
                <subcomponents>{internals}</subcomponents>
              </bodytube>'''
        angle_rad = math.radians(params.get("angle_offset_deg", 0.0))
        return f'''
          <podset>
            <name>{params.get("name", "Radial Assembly")}</name>
            <instancecount>{int(params.get("instance_count", 1))}</instancecount>
            <radiusoffset method="free">{params["radial_offset_m"]:.6f}</radiusoffset>
            <angleoffset method="relative">{angle_rad:.9f}</angleoffset>
            <position type="top">{params.get("axial_offset_m", 0.0):.6f}</position>
            <subcomponents>{nose_xml}{body_xml}</subcomponents>
          </podset>'''

    def compile(self, ast_nodes, name="L2 Evolved Rocket"):
        """Compiles the sequence of AST nodes into a valid OpenRocket XML string."""
        ast_nodes = sanitize_ast_for_openrocket(ast_nodes)
        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.6" creator="L2-OSIFOG-AST-Compiler">
  <rocket>
    <name>{name}</name>
    <designer>L2 Systems AI</designer>
    <motorconfiguration configid="{self.config_id}" default="true"/>
    <referencetype>maximum</referencetype>
    <subcomponents>
'''
        
        # We will parse the AST sequence into hierarchical stages
        # A simple state machine to track the current stage and body tube
        stages_xml = []
        current_stage = ""
        current_components = []
        stage_index = 0
        main_radius = 0.04
        current_body_length = 0.5
        current_body_radius = 0.04
        for n in ast_nodes:
            if n.node_type == "BODY_TUBE":
                main_radius = n.params.get("radius", 0.04)
                break
        
        # In a fully unhardcoded system, the AST translates directly to subcomponents!
        for node in ast_nodes:
            t = node.node_type
            p = node.params
            
            if t == "STAGE":
                if current_stage:
                    # Close previous stage
                    stages_xml.append(current_stage + "".join(current_components) + "</subcomponents></stage>")
                    current_components = []
                separation_xml = ""
                if stage_index > 0:
                    separation_xml = "<separationevent>burnout</separationevent><separationdelay>0.0</separationdelay>"
                current_stage = f'''<stage><name>{p.get("name", "Stage")}</name>{separation_xml}<subcomponents>'''
                stage_index += 1
                
            elif t == "NOSE_CONE":
                nose_thick = p.get("thickness", 0.002)
                current_components.append(f'''
          <nosecone>
            <name>Evolved Nose</name>
            <finish>polished</finish>
            {self._mat_xml(p.get("material", "fiberglass"))}
            <length>{p.get("length", 0.3):.6f}</length>
            <thickness>{nose_thick:.6f}</thickness>
            <shape>{p.get("shape", "haack")}</shape>
            <shapeclipped>false</shapeclipped>
            <aftradius>{main_radius:.6f}</aftradius>
            <aftshoulderlength>0.03</aftshoulderlength>
            <aftshoulderradius>{main_radius - nose_thick:.6f}</aftshoulderradius>
            <aftshoulderthickness>{nose_thick:.6f}</aftshoulderthickness>
            <aftshouldercapped>false</aftshouldercapped>
          </nosecone>''')
                
            elif t == "BODY_TUBE":
                rad = p.get("radius", 0.02)
                current_body_radius = rad
                current_body_length = p.get("length", 0.5)
                current_components.append(f'''
          <bodytube>
            <name>Evolved Airframe</name>
            <finish>polished</finish>
            {self._mat_xml(p.get("material", "kraft"))}
            <length>{p.get("length", 0.5):.6f}</length>
            <thickness>{p.get("thickness", 0.002):.6f}</thickness>
            <radius>{rad:.6f}</radius>
            <subcomponents>''') # Note: Leaves subcomponents open, we need a POP instruction or assume flat hierarchy for now

            elif t == "CLOSE_BODY":
                current_components.append('''</subcomponents></bodytube>''')

            elif t in {"POD", "STRAP_ON"}:
                current_components.append(self._radial_assembly_xml(p, t))

            elif t == "MOTOR_MOUNT":
                is_octaweb_main = p.get("multiplicity", 1) == 3 and p.get("cluster_configuration") == "3-ring"
                mount_name = "Octaweb Ascent Motors" if is_octaweb_main else (
                    "Retro Motor Mount" if p.get("role") == "retro" else "Motor Mount"
                )
                current_components.append(self._motor_mount_xml(p, name=mount_name))
                if is_octaweb_main:
                    current_components.append(
                        self._octaweb_circumscribing_rings_xml(p, current_body_radius, current_body_length)
                    )

            elif t == "FIN_SET":
                fin_name = "Forward Flap" if p.get("role") == "forward_flap" else "Evolved Fins"
                current_components.append(self._fin_xml(p, name=fin_name))

            elif t == "PARACHUTE":
                current_components.append(f'''
              <parachute>
                <name>Evolved Chute</name>
                <position type="middle">0.0</position>
                <packedlength>0.06</packedlength>
                <packedradius>0.01</packedradius>
                <cd>1.5</cd>
                <material type="surface" density="0.067">Ripstop nylon</material>
                <deployevent>{p.get("deploy", "apogee")}</deployevent>
                <deployaltitude>{p.get("altitude", 300.0)}</deployaltitude>
                <deploydelay>{p.get("delay", 0.0)}</deploydelay>
                <diameter>{p.get("diameter", 0.5):.4f}</diameter>
                <linecount>6</linecount>
                <linelength>{p.get("diameter", 0.5)*1.1:.4f}</linelength>
              </parachute>''')

            elif t == "PAYLOAD":
                current_components.append(f'''
              <masscomponent>
                <name>Evolved Payload</name>
                <position type="top">0.05</position>
                <packedlength>0.05</packedlength>
                <packedradius>0.02</packedradius>
                <mass>{p.get("mass", 1.0):.6f}</mass>
              </masscomponent>''')

            elif t == "BALLAST":
                axial_offset_m = p.get("axial_offset_m")
                if axial_offset_m is not None:
                    position_xml = f'<position type="absolute">{axial_offset_m:.6f}</position>'
                elif p.get("position", "forward") == "aft":
                    position_xml = '<position type="bottom">-0.05</position>'
                else:
                    position_xml = '<position type="top">0.05</position>'
                radius = p.get("radius")
                length = p.get("length")
                if radius is not None and length is not None:
                    # Shaped rod(s), not a lumped point mass -- octaweb
                    # ballast rods filling the gaps between the 3 main
                    # motors (see octaweb_ballast_rods). Real innertube per
                    # instance, one per ring position, thickness=radius for
                    # a SOLID rod (matches the 839k reference's own
                    # "S1 Aft Ballast rod" pattern, not a hollow mount
                    # tube) so a centering ring sized to the same radius is
                    # genuinely tangent to it, not floating around a hollow
                    # bore no motor occupies.
                    # No explicit <mass> here on purpose: unlike
                    # masscomponent, innertube has no mass override --
                    # OpenRocket derives it from material density * volume,
                    # which octaweb_ballast_rods already solved the AST's
                    # own `mass` param to match exactly (see its docstring).
                    instance_count = int(p.get("instance_count", 1))
                    radial_offset_m = p.get("radial_offset_m", 0.0)
                    # DEGREES, not radians -- see _motor_mount_xml's own
                    # comment and .planning/ultra/ULTRAREVIEW-octaweb-
                    # ballast-radialdirection-units.md for the empirical
                    # proof (this exact line was the reproduced bug).
                    angle_offset_deg = p.get("angle_offset_deg", 0.0)
                    for index in range(instance_count):
                        angle_deg = angle_offset_deg + (360.0 * index / instance_count)
                        current_components.append(f'''
              <innertube>
                <name>Evolved Ballast Rod {index}</name>
                {position_xml}
                {self._mat_xml(p.get("material", "steel"))}
                <length>{length:.6f}</length>
                <radialposition>{radial_offset_m:.6f}</radialposition>
                <radialdirection>{angle_deg:.9f}</radialdirection>
                <outerradius>{radius:.6f}</outerradius>
                <thickness>{radius:.6f}</thickness>
                <clusterconfiguration>single</clusterconfiguration>
                <clusterscale>1.0</clusterscale>
                <clusterrotation>0.0</clusterrotation>
              </innertube>''')
                else:
                    current_components.append(f'''
              <masscomponent>
                <name>Evolved Ballast</name>
                {position_xml}
                <packedlength>0.03</packedlength>
                <packedradius>0.015</packedradius>
                <mass>{p.get("mass", 0.1):.6f}</mass>
              </masscomponent>''')
                
        # Close the last stage
        if current_stage:
            stages_xml.append(current_stage + "".join(current_components) + "</subcomponents></stage>")

        xml += "".join(stages_xml)
        
        sim = OPENROCKET_SIMULATION_DEFAULTS
        xml += f'''
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="notsimulated">
      <name>AST Evolution Simulation</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{self.config_id}</configid>
        <launchrodlength>{sim["launch_rod_length_m"]}</launchrodlength>
        <launchrodangle>{sim["launch_rod_angle_rad"]}</launchrodangle>
        <launchroddirection>{sim["launch_rod_direction_rad"]}</launchroddirection>
        <windaverage>{sim["wind_speed_mps"]}</windaverage>
        <winddirection>{sim["wind_direction_rad"]}</winddirection>
        <windturbulence>0.0</windturbulence>
        <launchaltitude>0.0</launchaltitude>
        <launchlatitude>-23.55</launchlatitude>
        <launchlongitude>-46.63</launchlongitude>
        <geodeticmethod>spherical</geodeticmethod>
        <atmosphere model="extendedisa">
          <basetemperature>{sim.get("base_temperature_k", 288.15)}</basetemperature>
          <basepressure>{sim.get("base_pressure_pa", 101325.0)}</basepressure>
        </atmosphere>
        <timestep>0.05</timestep>
      </conditions>
      <extension extensionid="info.openrocket.core.simulation.extension.impl.ScriptingExtension">
        <entry key="language" type="string">JavaScript</entry>
        <entry key="script" type="string">{_anti_tumble_script()}</entry>
        <entry key="enabled" type="boolean">true</entry>
      </extension>
    </simulation>
  </simulations>
</openrocket>
'''
        return _add_stable_component_ids(xml)


def validate_compiled_geometry(xml):
    """Geometric/structural sanity checks on a compiled .ork XML, run
    BEFORE spending real OpenRocket JVM time on it. Exists because
    OpenRocket's own simulation status ("success", no ABORT, no
    collision/floating-part warning) is NOT proof the geometry is sane --
    confirmed for real: a candidate with every 3-ring motor cluster
    shoved 7cm outside the body tube (a real, reproduced bug -- see
    `_motor_mount_xml`'s radialposition/clusterconfiguration comment)
    still reported simulation_status="success" with zero critical
    warnings, because OpenRocket doesn't itself know the DESIGN INTENT
    was a tangent-to-body-wall cluster, only that it can still fly
    something (badly-shaped) mathematically. These checks encode intent
    this pipeline actually cares about, not just "did it crash".

    Returns a list of violation strings (empty means clean). Does not
    raise -- callers decide how to treat violations (reject the
    candidate, log it, block a live campaign from using it, etc).
    """
    violations = []
    root = ET.fromstring(xml)

    for stage in root.iter("stage"):
        stage_name = stage.findtext("name") or "<unnamed stage>"
        body_tubes = stage.findall(".//bodytube")
        body_radius = (
            float(body_tubes[0].findtext("radius", "0"))
            if body_tubes
            else None
        )
        for inner in stage.findall(".//innertube"):
            name = inner.findtext("name") or "<unnamed innertube>"
            cluster_config = inner.findtext("clusterconfiguration", "single")
            radial_position = float(inner.findtext("radialposition", "0"))
            if cluster_config != "single" and abs(radial_position) > 1e-9:
                violations.append(
                    f"{stage_name}/{name}: clusterconfiguration={cluster_config!r} "
                    f"with nonzero radialposition={radial_position:.6f} -- native "
                    "clustering and manual radial offset are mutually exclusive on "
                    "the same innertube; this doubles the effective offset and "
                    "shoves the whole cluster outside the body tube"
                )
            if body_radius is not None:
                outer_radius = float(inner.findtext("outerradius", "0"))
                # Native clustering derives its own ring radius from
                # clusterscale (not directly observable here without
                # reimplementing OpenRocket's formula), so this bound-check
                # only applies to "single"-configuration tubes, where
                # radialposition IS the true center-to-center offset.
                if cluster_config == "single":
                    farthest_extent = radial_position + outer_radius
                    if farthest_extent > body_radius + 1e-6:
                        violations.append(
                            f"{stage_name}/{name}: farthest extent "
                            f"{farthest_extent:.6f}m (radialposition="
                            f"{radial_position:.6f} + outerradius={outer_radius:.6f}) "
                            f"exceeds body tube radius {body_radius:.6f}m -- "
                            "component sticks outside the airframe"
                        )

    extensions = root.findall(".//simulation/extension")
    anti_tumble_extensions = [
        ext for ext in extensions
        if str(ext.get("extensionid", "")).endswith("ScriptingExtension")
        and any(
            entry.get("key") == "script" and "TUMBLE" in (entry.text or "")
            for entry in ext.findall("entry")
        )
    ]
    if not anti_tumble_extensions:
        violations.append(
            "no anti-tumble ScriptingExtension found on the saved simulation -- "
            "a TUMBLE flight event will abort the sim instead of being handled"
        )
    elif len(anti_tumble_extensions) > 1:
        violations.append(
            f"{len(anti_tumble_extensions)} anti-tumble extensions found, expected exactly 1"
        )

    # Component <id> collisions: OpenRocket's UI tracks/highlights
    # components by id, not name or tree position -- confirmed for real,
    # a 2-stage octaweb .ork had all 8 centering-ring ids byte-identical
    # between stages (osifog_sweep._octaweb_rings_xml's _component_id(name)
    # hashes the name string alone, and both stages used the same
    # stage-unqualified name), which made selecting one stage's ring in
    # the OpenRocket tree also highlight the other stage's ring in the 3D
    # view. `_add_stable_component_ids` only assigns ids to
    # OPENROCKET_COMPONENT_TAGS (document-order counter, inherently
    # unique); centeringring isn't in that set, so nothing else catches a
    # collision introduced upstream of it.
    seen_ids = {}
    for element in root.iter():
        id_node = element.find("id")
        if id_node is None or not (id_node.text or "").strip():
            continue
        component_id = id_node.text.strip()
        name_node = element.find("name")
        label = (name_node.text if name_node is not None else element.tag)
        if component_id in seen_ids:
            violations.append(
                f"duplicate component id {component_id}: {seen_ids[component_id]!r} "
                f"and {label!r} share the same id -- selecting one in OpenRocket's "
                "UI will highlight both"
            )
        else:
            seen_ids[component_id] = label

    return violations


# Default forward-flap envelope, mirroring missions/osifog_l3_precision.json's
# `evolution.physical_repair_space.forward_fin_*` and the proven Starship
# genome (designs/osifog_level3/starship_best_genome.json). A mission's own
# repair_space overrides these via create_random_ast(repair_space=...).
DEFAULT_FORWARD_FLAP_SPACE = {
    "forward_fin_count": [2, 3],
    "forward_fin_root_m": [0.12, 0.15, 0.18, 0.20],
    "forward_fin_height_m": [0.10, 0.12, 0.14, 0.15],
    "forward_fin_position_range_m": [0.02, 0.15],
}


def forward_flap_node(repair_space, body_radius=None):
    """`body_radius`, when known, sizes the flap proportionally (root/height
    as a fraction of body radius) instead of using repair_space's fixed
    absolute meters. Those absolute defaults were tuned against a ~0.08-0.09m
    reference body (starship_best_genome.json); create_random_ast's body
    radius ranges much smaller (0.02-0.08m), so an untouched fixed-size flap
    is often *larger than the body tube itself* -- a full second finset
    stapled to the nose rather than a modest destabilizing flap, which tanks
    ascent static margin far more than the tail-first-descent mechanism
    needs. Proportional sizing keeps the flap's effect consistent across the
    whole body-radius range instead of only being sane at one radius."""
    space = {**DEFAULT_FORWARD_FLAP_SPACE, **(repair_space or {})}
    lo, hi = space["forward_fin_position_range_m"]
    if body_radius is not None and body_radius > 0.0:
        root = body_radius * random.uniform(0.6, 1.1)
        height = body_radius * random.uniform(0.5, 0.9)
    else:
        root = random.choice(space["forward_fin_root_m"])
        height = random.choice(space["forward_fin_height_m"])
    return ASTNode(
        "FIN_SET",
        role="forward_flap",
        count=random.choice(space["forward_fin_count"]),
        sweep=random.uniform(0.0, 10.0),
        root=root,
        height=height,
        position_from_top_m=random.uniform(lo, hi),
        material=random.choice(list(MATERIALS.keys())),
    )


# Body-tube radius range for an octaweb-style stage: 3 main motors tangent
# to the body's own inner wall need real room, unlike the default
# single-motor stage's 0.02-0.08m range. Ceiling widened from the
# original 0.15m: the mission's own largest legal motor (L1500T, 0.098m
# diameter) used as BOTH main and retro needs a cluster envelope radius
# of ~0.148m -- right at the old 0.15m ceiling with essentially zero
# margin, meaning octaweb_motor_mounts would fail for its own biggest
# legal combos whenever a large body_radius_m draw didn't happen to land
# exactly right. 0.20m gives real headroom for the full legal pool plus
# future larger motor pools.
OCTAWEB_BODY_RADIUS_RANGE_M = (0.06, 0.20)


def _tighten_octaweb_cage(cage):
    """Re-tighten a `_falcon_cluster_geometry` result to true motor-to-motor
    tangency, independent of how much extra body_radius_m room was
    available. `_falcon_cluster_geometry` sizes the ring from the BODY
    TUBE'S available room outward, not from true tangency -- confirmed as a
    real, user-visible bug: a randomly-picked or later-widened body_radius_m
    usually has slack beyond what the chosen motor pair needs, leaving a
    visible GAP between the 3 outer motors and the central retro motor once
    rendered in real OpenRocket, even though the whole point of this
    cluster (PLAN_INTERNAL_OCTAWEB_CLUSTER.md's Item 8 rigid-coupling
    argument) is that every motor is mutually TANGENT. This is exactly the
    R = r_retro + r_main formula PLAN_INTERNAL_OCTAWEB_CLUSTER.md's own
    geometry section already verified against all 480 real main/retro motor
    combos (0 overlaps). Extracted into its own function (originally inline
    in `octaweb_motor_mounts`) so `sanitize_ast_for_openrocket`'s repair
    pass can call the exact same math when re-tightening an EXISTING
    candidate's cage against a body radius that changed since creation
    (diameter-continuity widening, or simply stale data carried forward
    many generations by elitism with nothing to ever re-validate it) --
    duplicating this formula in two places risks exactly the kind of drift
    that caused the original bug."""
    from osifog_sweep import MIN_DIMENSION_M

    retro_cavity_radius_m = cage["retro_sleeve_outer_radius_m"] - cage["retro_sleeve_wall_m"]
    tight_sleeve_outer_radius_m = retro_cavity_radius_m + MIN_DIMENSION_M
    main_outer = cage["main_outer_radius_m"]
    tangent_center_distance_m = main_outer + tight_sleeve_outer_radius_m
    # Sitting exactly on the retro-tangency minimum does NOT guarantee the
    # 3 outer (main) motors clear EACH OTHER -- l2_engine's
    # enforce_motor_mount_clearance independently requires
    # chord=center_distance*sqrt(3) >= 2*main_outer+0.002. For a large-main/
    # small-retro motor pair these two constraints nearly coincide right at
    # the boundary, which is exactly the persistent near-miss
    # motor_mount_collision failures observed across a live campaign for
    # hundreds of generations (dist within 2-3% of needed, every elite,
    # every cycle). Take whichever constraint needs more room, per a real
    # OpenRocket-provided octaweb reference design confirming centering
    # rings auto-size to the body/center-tube boundary and are NOT
    # individually tangent to each clustered motor (the ring visibly
    # passes through the motors in that reference) -- there was never a
    # physical reason to sit exactly on the bare-legal minimum in the
    # first place. Add a real safety margin beyond whichever bound binds,
    # rather than the previous zero-margin exact-tangent formula.
    outer_outer_min_center_distance_m = (2.0 * main_outer + 0.002) / math.sqrt(3.0)
    SAFETY_MARGIN_M = 0.005
    tight_center_distance_m = (
        max(tangent_center_distance_m, outer_outer_min_center_distance_m)
        + SAFETY_MARGIN_M
    )
    return {
        **cage,
        "center_distance_m": tight_center_distance_m,
        "cluster_scale": tight_center_distance_m * math.sqrt(3.0) / (2.0 * cage["main_outer_radius_m"]),
        "retro_sleeve_outer_radius_m": tight_sleeve_outer_radius_m,
        "retro_sleeve_wall_m": MIN_DIMENSION_M,
    }


def octaweb_motor_mounts(main_motor_pool, retro_motor_pool, body_radius_m, ignition_bottom, max_attempts=6):
    """Builds a physically-consistent 3-outer-main + 1-central-retro motor
    cluster for a single shared BODY_TUBE (no external pod/pylon needed),
    per OSIFOG/PLAN_INTERNAL_OCTAWEB_CLUSTER.md.

    Reuses `osifog_sweep.py::_falcon_cluster_geometry` (already validated
    against real OpenRocket in that pipeline) rather than re-deriving the
    cage math: main motors are sized tangent to `body_radius_m`'s own inner
    wall, and the function raises ValueError if the remaining central space
    can't legally fit the retro motor with adequate wall thickness. Tries a
    handful of random (main, retro) motor pairs -- not every pair fits a
    given body radius -- and returns `None` if none of them do, so the
    caller can fall back to the normal single-motor topology instead of
    crashing generation.

    Returns `(main_mount_node, retro_mount_node)` on success, where the main
    mount carries `multiplicity=3`, `instance_angle_step_deg=120.0`, and the
    cage's derived `radial_offset_m`/`cluster_scale`/`main_outer_radius_m`/
    `retro_sleeve_outer_radius_m` stored directly on its params so the XML
    compiler never has to re-derive or guess them.
    """
    from osifog_sweep import _falcon_cluster_geometry

    main_indices = motor_pool_indices(main_motor_pool)
    retro_indices = motor_pool_indices(retro_motor_pool)
    if not main_indices or not retro_indices:
        return None

    for _ in range(max_attempts):
        main_idx = random.choice(main_indices)
        retro_idx = random.choice(retro_indices)
        try:
            cage = _falcon_cluster_geometry(main_idx, retro_idx, body_radius_m)
        except ValueError:
            continue

        cage = _tighten_octaweb_cage(cage)

        # _falcon_cluster_geometry only guards retro-vs-outer clearance (the
        # central sleeve fitting the retro motor); it does not check the 3
        # outer motors against EACH OTHER. Since center_distance here is
        # driven by the (randomly chosen) body radius rather than the motor
        # pair, a small body + large main motor can still produce outer-
        # outer overlap -- confirmed empirically via
        # l2_engine's enforce_motor_mount_clearance (task 1) rejecting ~7%
        # of naively-generated candidates on exactly this. Reject here too
        # so generation doesn't waste evaluation cycles on candidates Rust
        # will reject anyway.
        center_distance = cage["center_distance_m"]
        main_outer = cage["main_outer_radius_m"]
        chord = center_distance * math.sqrt(3.0)
        if chord < 2.0 * main_outer + 0.002:
            continue

        # The tight-tangent recompute above sizes the cage purely from the
        # (main, retro) motor pair's own physical dimensions, no longer
        # bounded by the randomly-drawn body_radius_m at all -- confirmed as
        # a real, live-campaign bug: a large-enough main motor paired with a
        # small body_radius_m draw produces center_distance + main_outer
        # exceeding the body tube's own inner wall, i.e. the 3 main motors
        # render OUTSIDE the airframe. Reject and retry another (motor,
        # body) draw here, matching the same fit check l2_engine's
        # build_mission now enforces (radial_offset_m + motor_radius vs
        # host_inner_radius), so generation doesn't waste evaluation cycles
        # on candidates Rust will reject anyway.
        body_inner = float(body_radius_m) - 0.002
        if center_distance + main_outer + 0.001 > body_inner:
            continue

        # Independent material choice per structural part (main mount,
        # retro mount, rings), not one shared pick -- per user direction:
        # the GA needs freedom to vary material per part so it can trade
        # off weight distribution (CG/stability), not just pick one
        # material for the whole cluster. Was hardcoded to "kraft"/
        # "fiberglass" everywhere before this; now genuinely explores the
        # mission's legal material space (density 170-11340 kg/m3,
        # l2_engine/src/ast.rs) via MOUNT_MATERIAL_CHOICES.
        # mount_material_density is set explicitly on both mounts (not
        # left to Rust's 700.0 kraft-ish default) so Rust's inert-mount
        # point-mass calculation (l2_engine/src/ast.rs:878) and
        # OpenRocket's own density*volume calculation for the compiled
        # XML agree with each other.
        main_mount_material = random.choice(MOUNT_MATERIAL_CHOICES)
        retro_mount_material = random.choice(MOUNT_MATERIAL_CHOICES)
        ring_material = random.choice(MOUNT_MATERIAL_CHOICES)

        main_mount = ASTNode(
            "MOTOR_MOUNT",
            role="main",
            motor_index=main_idx,
            motor_designation=MOTOR_DATABASE[main_idx][1],
            multiplicity=3,
            radial_offset_m=cage["center_distance_m"],
            radial_angle_deg=0.0,
            instance_angle_step_deg=120.0,
            # See create_random_ast's identical fix: "burnout" on a stage's
            # own sole main motor was confirmed (via l2_engine ast_trace) to
            # make the motor never ignite at all in Rust's simulation --
            # "automatic" is correct for every stage, matching OpenRocket's
            # own stage-position-aware ignition semantics.
            ignition="automatic",
            cluster_configuration="3-ring",
            cluster_scale=cage["cluster_scale"],
            main_outer_radius_m=cage["main_outer_radius_m"],
            retro_sleeve_outer_radius_m=cage["retro_sleeve_outer_radius_m"],
            mount_material=main_mount_material,
            mount_material_density=MATERIALS[main_mount_material][2],
            ring_material=ring_material,
        )
        retro_mount = ASTNode(
            "MOTOR_MOUNT",
            role="retro",
            motor_index=retro_idx,
            motor_designation=MOTOR_DATABASE[retro_idx][1],
            multiplicity=1,
            radial_offset_m=0.0,
            # See create_random_ast's existing retro-motor comment:
            # "burnout" means N seconds after THIS stage's own main-motor
            # burnout (l2_engine/src/mission_adapter.rs), not OpenRocket's
            # XML-only "ignitiondelay" vocabulary.
            ignition="burnout",
            ignition_delay=random.uniform(0.0, 30.0),
            mount_material=retro_mount_material,
            mount_material_density=MATERIALS[retro_mount_material][2],
        )
        return main_mount, retro_mount
    return None


def _closest_legal_material_for_density(target_density_kg_m3):
    """Picks the MATERIALS entry whose density is nearest `target_density_kg_m3`.
    Every entry in MATERIALS is already within the mission-wide legal
    170-11340 kg/m3 range (l2_engine/src/ast.rs::is_density_in_allowed_range),
    so this never needs its own bounds check."""
    return min(MATERIALS.items(), key=lambda item: abs(item[1][2] - target_density_kg_m3))[0]


def _ballast_clears_main_motors(radius_m, radial_offset_m, main_outer, center_distance):
    """True if a ballast rod (radius_m, at radial_offset_m, bisecting the
    60-degree gap between two adjacent main motors) does not overlap
    either neighbor. Chord length between the ballast position and a
    neighboring main motor (both measured from the centerline, 60 degrees
    apart) via the law of cosines."""
    chord = math.sqrt(
        radial_offset_m**2 + center_distance**2
        - radial_offset_m * center_distance * math.cos(math.radians(60.0))
    )
    return chord >= radius_m + main_outer + 0.002


def octaweb_ballast_rods(cage, target_mass_kg, length_m, main_angle_deg=0.0):
    """Builds a BALLAST node for 3 solid cylindrical rods TANGENT TO THE
    CENTER (retro) MOTOR, positioned in the gaps between the 3 outer main
    motors -- matching the real reference design exactly, confirmed by
    directly inspecting `osifog_physical_839k_falcon.ork`'s own saved
    "S1 Aft Ballast rod" components: `radialposition (0.0455) -
    retro_sleeve_outer_radius (0.0315) == outerradius (0.014)` there,
    i.e. tangent to the retro motor's own edge, with generous (not
    boundary-tight) clearance from the main motors (~2.5cm in that
    reference, verified numerically).

    Two earlier attempts at this were wrong: (1) same ring radius as the
    main motors, same size as the main motors -- zero room in a truly
    tangent cluster; (2) pushed OUTSIDE the cluster's own envelope --
    passes straight through the circumscribing centering rings' solid
    material (a ring's bore starts exactly at the envelope radius and
    extends to the body wall). Tangent to the CENTER motor instead, sized
    to comfortably clear the two neighboring main motors (verified
    numerically via `_ballast_clears_main_motors`, shrinking from an
    initial candidate size until it fits, rather than solving a fragile
    closed-form boundary case) is what the actual reference does.

    `target_mass_kg` is the TOTAL ballast mass across all 3 rods (matching
    `nose_ballast_mass_kg`'s existing total-added-mass convention). Density
    is the free variable, solved per-rod from the fixed radius/length and
    target_mass_kg/3 -- picks the nearest legal material, then recomputes
    the node's stored `mass` from that material's REAL density (density *
    pi * r^2 * length, summed over all 3 rods), not the raw requested
    value. This matters: BALLAST's Rust handling (`ast.rs`) splits `mass`
    evenly across `instance_count` point masses, and each compiled
    <innertube> independently gets its OWN mass from OpenRocket's own
    material-density * volume calculation -- if the AST's stored `mass`
    disagreed with that, Rust and OpenRocket would silently diverge on
    this component's mass, exactly the proxy-vs-authority mismatch class
    of bug this project has been burned by before. This computation keeps
    them exactly consistent by construction. Achieved mass is typically
    within one discrete material step of the request, same as picking a
    real motor or material off a catalog always is.

    `length_m` should be the main mount's own motor-mount length (the
    caller already knows this from sizing the main motor's MOTOR_MOUNT --
    `cage` itself has no length field, only radii) so the ballast rods
    span the same axial extent as the 3 main motors, a coherent cluster
    rather than rods of arbitrary length sticking out.

    Returns a single ASTNode("BALLAST", instance_count=3, ...) on success,
    or `None` if no rod size down to a 3mm floor clears the main motors
    (same silent-fallback contract as `octaweb_motor_mounts`).
    """
    main_outer = cage["main_outer_radius_m"]
    retro_sleeve_outer = cage["retro_sleeve_outer_radius_m"]
    center_distance = cage["center_distance_m"]

    radius_m = min(main_outer, retro_sleeve_outer)
    while radius_m >= 0.003:
        radial_offset_m = retro_sleeve_outer + radius_m
        if _ballast_clears_main_motors(radius_m, radial_offset_m, main_outer, center_distance):
            break
        radius_m *= 0.85
    else:
        return None
    if not _ballast_clears_main_motors(radius_m, radial_offset_m, main_outer, center_distance):
        return None

    rod_volume_m3 = math.pi * radius_m * radius_m * length_m
    per_rod_target_mass_kg = target_mass_kg / 3.0
    target_density = per_rod_target_mass_kg / max(rod_volume_m3, 1e-12)
    material = _closest_legal_material_for_density(target_density)
    achieved_density = MATERIALS[material][2]
    achieved_total_mass_kg = achieved_density * rod_volume_m3 * 3.0

    return ASTNode(
        "BALLAST",
        mass=achieved_total_mass_kg,
        material=material,
        radius=radius_m,
        length=length_m,
        instance_count=3,
        radial_offset_m=radial_offset_m,
        # The 3 main motors DO NOT render at main_angle_deg + {0,120,240}
        # (the naive AST-param reading) -- OpenRocket's native 3-ring
        # clustering has its own inherent +90 degree starting rotation
        # when clusterrotation=0 (which octaweb_motor_mounts always uses
        # here), confirmed empirically: a cluster with radial_angle_deg=0
        # renders its first instance at 90 degrees, not 0. Gap midpoints
        # are therefore at (main_angle_deg + 90) - 60 = main_angle_deg +
        # 30, not main_angle_deg + 60 -- verified directly against live
        # OpenRocket component positions (mains at {90,210,330}, correct
        # gap midpoints at {30,150,270}, NOT the {60,180,300} an earlier
        # version of this line produced). See
        # .planning/ultra/ULTRAREVIEW-octaweb-ballast-radialdirection-units.md.
        angle_offset_deg=main_angle_deg + 30.0,
        position="aft",
    )


def create_random_ast(
    min_stages=1,
    max_stages=2,
    motor_pool=None,
    no_recovery_devices=False,
    forward_flap_probability=0.0,
    repair_space=None,
    retro_motor_pool=None,
    retro_motor_probability=0.0,
    octaweb_probability=0.0,
):
    """Generates a fully random rocket AST from scratch with a dynamic number of stages.

    `motor_pool`, when given, is a list of allowed motor designations (e.g.
    OSIFOG's J/K/L class restriction) -- generation never picks a motor
    outside it. `None` keeps the unrestricted default range.

    `no_recovery_devices=True` skips the default PARACHUTE (OSIFOG and any
    retro-landing-only mission bans passive recovery devices -- generating
    one is an automatic disqualifier, not just a wasted candidate).

    `forward_flap_probability` is the per-stage chance of adding a nose-
    mounted `FIN_SET(role="forward_flap")` -- the Starship-style mechanism
    that forces passive tail-first descent (see starship_best_genome.json).
    `repair_space` overrides DEFAULT_FORWARD_FLAP_SPACE's sampling ranges,
    typically sourced from a mission's `evolution.physical_repair_space`.

    `retro_motor_pool`/`retro_motor_probability` add a second, role="retro"
    MOTOR_MOUNT per stage so the GA has something to actually brake with --
    without it, a forward flap alone only produces a fast tail-first impact.

    `octaweb_probability` is the per-stage chance of using the internal
    3-main + 1-central-retro shared-body-tube cluster (see
    OSIFOG/PLAN_INTERNAL_OCTAWEB_CLUSTER.md) instead of the default single
    main motor + optional single retro motor. Requires `retro_motor_pool`;
    silently falls back to the default single-motor topology if no
    (main, retro) motor pair fits the stage's body radius after a few
    attempts, so this never crashes generation.
    """
    ast = []

    num_stages = random.randint(min_stages, max_stages)

    # Body radius is decided ONCE for the whole rocket, not redrawn per
    # stage. A real multi-stage airframe keeps a constant diameter --
    # independent per-stage radii produced visible discontinuities at every
    # stage joint (OpenRocket's own "diameter discontinuity" warning,
    # confirmed against a real exported design) and no tapered-transition
    # component exists in this pipeline to make a differing-diameter joint
    # physically sensible. If ANY stage will use octaweb, the whole rocket
    # adopts the larger octaweb-sized radius (a stage needing room for 3
    # main motors can't be narrower than its neighbors anyway).
    octaweb_rolls = [
        bool(retro_motor_pool) and random.random() < octaweb_probability
        for _ in range(num_stages)
    ]
    if any(octaweb_rolls):
        body_radius = random.uniform(*OCTAWEB_BODY_RADIUS_RANGE_M)
    else:
        body_radius = random.uniform(0.02, 0.08)

    for stage_idx in range(num_stages):
        is_top_stage = (stage_idx == 0)
        is_bottom_stage = (stage_idx == num_stages - 1)
        use_octaweb = octaweb_rolls[stage_idx]

        stage_name = "Evolved Sustainer" if is_top_stage else f"Evolved Booster {stage_idx}"
        stage_kwargs = {"recovery": "retro_only"} if no_recovery_devices else {}
        ast.append(ASTNode("STAGE", name=stage_name, **stage_kwargs))

        if is_top_stage:
            ast.append(ASTNode("NOSE_CONE", shape=random.choice(NOSE_SHAPES), length=random.uniform(0.2, 0.6), material=random.choice(list(MATERIALS.keys()))))

        ast.append(ASTNode(
            "BODY_TUBE", length=random.uniform(0.3, 1.5), radius=body_radius,
            material=random.choice(list(MATERIALS.keys())),
            thickness=random.uniform(0.001, 0.006),
        ))

        if is_top_stage:
            if not no_recovery_devices:
                ast.append(ASTNode("PARACHUTE", deploy="apogee", diameter=random.uniform(0.3, 1.2)))
            if random.random() < 0.5:
                ast.append(ASTNode("PAYLOAD", mass=random.uniform(0.5, 8.0)))
        elif random.random() < 0.2:
            ast.append(ASTNode("PAYLOAD", mass=random.uniform(0.1, 2.0)))

        # Fins and Motor. Root/height are scaled relative to body_radius
        # (not drawn independently) -- an uncorrelated draw regularly
        # produced fins too small to give adequate static margin for
        # whatever body radius was picked, which was the dominant cause of
        # near-universal min_static_margin failures in early campaigns.
        # Material is a genuine per-fin free choice, same rationale as the
        # per-part motor-mount materials above -- root/height trade off
        # static margin against mass, and material trades off structural
        # mass against density, so the GA needs both to actually search a
        # weight-distribution optimum instead of always defaulting to
        # _sanitize_fin's fallback ("fiberglass") because nothing here or
        # in ASTNode.mutate() ever set it otherwise.
        ast.append(ASTNode(
            "FIN_SET",
            count=random.choice([3, 4, 6]),
            sweep=random.uniform(10, 50),
            root=body_radius * random.uniform(2.5, 5.0),
            height=body_radius * random.uniform(2.0, 4.5),
            material=random.choice(list(MATERIALS.keys())),
        ))
        if random.random() < forward_flap_probability:
            ast.append(forward_flap_node(repair_space, body_radius=body_radius))

        octaweb_mounts = (
            octaweb_motor_mounts(motor_pool, retro_motor_pool, body_radius, is_bottom_stage)
            if use_octaweb
            else None
        )
        if octaweb_mounts:
            main_mount, retro_mount = octaweb_mounts
            ast.append(main_mount)
            ast.append(retro_mount)
            ballast_mass_choices = (repair_space or {}).get("nose_ballast_mass_kg")
            if ballast_mass_choices:
                ballast_mass_kg = random.choice(ballast_mass_choices)
                if ballast_mass_kg > 0.0:
                    main_motor_len = (
                        MOTOR_DATABASE[main_mount.params["motor_index"]][3] + 0.02
                    )
                    cage = {
                        "main_outer_radius_m": main_mount.params["main_outer_radius_m"],
                        "center_distance_m": main_mount.params["radial_offset_m"],
                        "retro_sleeve_outer_radius_m": main_mount.params["retro_sleeve_outer_radius_m"],
                    }
                    ballast = octaweb_ballast_rods(
                        cage,
                        ballast_mass_kg,
                        main_motor_len,
                        main_angle_deg=main_mount.params["radial_angle_deg"],
                    )
                    # None when the retro motor isn't enough bigger than
                    # the tangent minimum to leave a real gap -- see
                    # octaweb_ballast_rods' own docstring. Sits inside the
                    # cluster's own envelope (same body-room budget as the
                    # cluster itself), so no separate body-radius fit
                    # check is needed here.
                    if ballast is not None:
                        ast.append(ballast)
        else:
            motor_idx = _select_motor_index(motor_pool, default_floor=10)
            # Independent material choice per mount -- see
            # octaweb_motor_mounts' own comment on the same change. Was
            # hardcoded to "kraft" via _motor_mount_xml before today.
            main_mount_material = random.choice(MOUNT_MATERIAL_CHOICES)
            ast.append(ASTNode(
                "MOTOR_MOUNT", role="main", motor_index=motor_idx,
                motor_designation=MOTOR_DATABASE[motor_idx][1],
                # "automatic" for every stage, not just the bottom one --
                # see the same fix's comment on octaweb_motor_mounts below,
                # confirmed via a direct l2_engine ast_trace: a "burnout"-
                # tagged upper-stage main motor never ignites at all (its
                # own ignition_delay resolves against ITS OWN burn duration,
                # a self-referential no-op), while OpenRocket's "automatic"
                # is natively stage-position-aware (launch for the bottom
                # stage, lower-stage-separation for every stage above it).
                ignition="automatic",
                mount_material=main_mount_material,
                mount_material_density=MATERIALS[main_mount_material][2],
            ))
            if retro_motor_pool and random.random() < retro_motor_probability:
                retro_idx = _select_motor_index(retro_motor_pool, default_floor=0)
                # Explicit nonzero radial offset: two motor mounts both left at
                # offset 0.0 would sit on the same centerline and overlap. This
                # seed value is re-verified/enlarged as needed in _sanitize_body.
                main_radius_m = MOTOR_DATABASE[motor_idx][2] / 2.0
                retro_radius_m = MOTOR_DATABASE[retro_idx][2] / 2.0
                retro_mount_material = random.choice(MOUNT_MATERIAL_CHOICES)
                ast.append(ASTNode(
                    "MOTOR_MOUNT",
                    role="retro",
                    motor_index=retro_idx,
                    motor_designation=MOTOR_DATABASE[retro_idx][1],
                    radial_offset_m=main_radius_m + retro_radius_m + 0.004,
                    radial_angle_deg=180.0,
                    # "burnout": Rust's mission_adapter interprets this as
                    # ignition_delay seconds after THIS stage's own main-motor
                    # burnout (l2_engine/src/mission_adapter.rs:538-540) -- exactly
                    # the post-burnout retro-delay search the doctrine docs use.
                    # ("ignitiondelay" is an OpenRocket-XML-only vocabulary word;
                    # Rust's ignition-event matcher does not recognize it.)
                    ignition="burnout",
                    ignition_delay=random.uniform(0.0, 30.0),
                    mount_material=retro_mount_material,
                    mount_material_density=MATERIALS[retro_mount_material][2],
                ))

        ast.append(ASTNode("CLOSE_BODY"))

    # `body_radius` above is drawn ONCE, independently of whatever motor
    # pair octaweb_motor_mounts ends up choosing, and only checked for
    # "does the cage fit inside it" (rocket_ast.py's earlier body-fit
    # rejection), never "is this body actually SIZED for this cage" --
    # confirmed as a real, user-reported visual issue: a small motor pair
    # (e.g. H180W/J350W, ~36mm tight-tangent radius) landing inside a body
    # drawn near OCTAWEB_BODY_RADIUS_RANGE_M's own ceiling (200mm) leaves a
    # large, structurally pointless gap between the tight cluster and the
    # body wall -- extra unneeded mass and drag with no benefit (ballast
    # rods sit INSIDE the cluster's own envelope, not against the body
    # wall, so they don't need the extra room either). Tighten every
    # BODY_TUBE down to whichever octaweb stage's cage actually needs the
    # most room (both stages must share one constant-diameter airframe --
    # sanitize_ast_for_openrocket's diameter-continuity pass would force
    # them equal to the LARGER one anyway, so using the larger requirement
    # here matches, rather than fights, that pass), plus a real margin for
    # wall thickness/attachment/future ballast growth via mutation. Only
    # tightens (never widens) -- a body_radius that's already appropriately
    # sized, or non-octaweb stages entirely, are left untouched.
    octaweb_main_mounts = [
        node for node in ast
        if node.node_type == "MOTOR_MOUNT"
        and node.params.get("role") == "main"
        and node.params.get("cluster_configuration") == "3-ring"
    ]
    if octaweb_main_mounts:
        required_radius = max(
            node.params["radial_offset_m"] + node.params["main_outer_radius_m"]
            for node in octaweb_main_mounts
        ) * 1.2 + 0.004
        required_radius = max(required_radius, OCTAWEB_BODY_RADIUS_RANGE_M[0])
        for node in ast:
            if node.node_type == "BODY_TUBE" and required_radius < node.params["radius"]:
                node.params["radius"] = required_radius

    return sanitize_ast_for_openrocket(ast)

if __name__ == "__main__":
    ast = create_random_ast()
    print("[*] Generated AST Genome:")
    for node in ast:
        print(f"  -> {node.node_type}: {node.params}")
        
    compiler = ASTCompiler()
    xml = compiler.compile(ast)
    with open("designs/optimized/L2_AST_Test.ork", "w") as f:
        f.write(xml)
    print("\\n[*] Compiled AST into valid ORK XML: designs/optimized/L2_AST_Test.ork")
