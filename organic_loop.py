import argparse
import atexit
import copy
import json
import math
import os
import random
import re
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ckg_memory import ContinuousKnowledgeGraph
from rocket_ast import ASTCompiler, ASTNode, MATERIALS, MOTOR_DATABASE, OCTAWEB_BODY_RADIUS_RANGE_M, create_random_ast, forward_flap_node, octaweb_ballast_rods, octaweb_motor_mounts, sanitize_ast_for_openrocket, _motor_index, _select_motor_index, _split_stages


class _AstEvalStream:
    """Serialized JSONL client for one long-lived Rust evaluator process."""

    def __init__(self, binary_path, engine_dir):
        self._lock = threading.Lock()
        self._process = subprocess.Popen(
            [str(binary_path), "--serve"],
            cwd=str(engine_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stderr = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    @property
    def pid(self):
        return self._process.pid

    def _drain_stderr(self):
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 50:
                del self._stderr[0]

    def request(self, payload):
        with self._lock:
            if self._process.poll() is not None:
                raise RuntimeError("ast_eval stream exited: " + "\n".join(self._stderr[-5:]))
            self._process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
            line = self._process.stdout.readline()
            if not line:
                raise RuntimeError("ast_eval stream closed without a response")
            response = json.loads(line)
            if "error" in response:
                raise RuntimeError(f"ast_eval stream error: {response['error']}")
            return response

    def close(self):
        with self._lock:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()


_AST_EVAL_STREAMS = {}
_AST_EVAL_STREAMS_LOCK = threading.Lock()


def _close_ast_eval_streams():
    for stream in list(_AST_EVAL_STREAMS.values()):
        stream.close()
    _AST_EVAL_STREAMS.clear()


atexit.register(_close_ast_eval_streams)


def _ast_eval_binary_is_stale(engine_dir, binary_path):
    """Return true when the release evaluator predates its Rust inputs."""
    if not binary_path.exists():
        return True
    binary_mtime = binary_path.stat().st_mtime_ns
    inputs = [engine_dir / "Cargo.toml", engine_dir / "Cargo.lock"]
    inputs.extend((engine_dir / "src").rglob("*.rs"))
    return any(path.exists() and path.stat().st_mtime_ns > binary_mtime for path in inputs)


def _ensure_ast_eval_binary(engine_dir, binary_path):
    if not _ast_eval_binary_is_stale(engine_dir, binary_path):
        return
    key = str(binary_path.resolve())
    with _AST_EVAL_STREAMS_LOCK:
        stale = _AST_EVAL_STREAMS.pop(key, None)
        if stale is not None:
            stale.close()
        subprocess.run(
            ["cargo", "build", "--quiet", "--release", "--bin", "ast_eval"],
            check=True,
            cwd=str(engine_dir),
        )


MODE_PROFILES = {
    "super-speed": {
        "promote_profile": "balanced",
        "promote_fraction": 0.05,
        "promote_every": 1,
        "or_calibrate_every": 10,
        "calibration_sample_size": 4,
    },
    "balanced": {
        "promote_profile": "authority-heavy",
        "promote_fraction": 0.10,
        "promote_every": 2,
        "or_calibrate_every": 5,
        "calibration_sample_size": 4,
    },
    "authority-heavy": {
        "promote_profile": None,
        "promote_fraction": 0.0,
        "promote_every": 0,
        "or_calibrate_every": 2,
        "calibration_sample_size": 1,
    },
}


@dataclass
class OrganicLoopConfig:
    population: int = 32
    elite_count: int = 6
    generations: int = 1
    seed: int = 42
    target_apogee_m: float = 15000.0
    mission_path: Path = None
    output_dir: Path = Path("designs/organic")
    ckg_path: Path = Path(".planning/organic_ckg.json")
    evaluator: str = "rust"
    physics_mode: str = "openrocket"
    execution_profile: str = "authority-heavy"
    divergence_model: dict = None
    divergence_model_path: Path = None
    divergence_history: list = None
    calibration_sample_size: int = 0
    rust_evaluator: object = None
    validate_openrocket: int = 0
    calibrate_every: int = 0
    seed_from: Path = None
    polish: bool = False
    or_helper: object = None
    objectives: list = None
    constraints: dict = None
    phase_machs: list = None
    progress_callback: object = None


@dataclass
class OrganicCandidate:
    ast: list
    score: float
    raw_score: float
    status: str
    reason: str
    rust_apogee_m: float = 0.0
    rust_mach: float = 0.0
    rust_min_static_margin: float = 0.0
    rust_margins: list = None
    rust_features: list = None
    rust_stage_landings: list = None
    rust_total_prop_mass_kg: float = 0.0
    screen_apogee_m: float = 0.0
    screen_mach: float = 0.0
    screen_features: list = None
    ckg_items: list = None
    or_metrics: dict = None
    rust_apogee_east_m: float = 0.0
    rust_apogee_north_m: float = 0.0


def selection_rank(candidate):
    """Sort key enforcing legal-before-illegal, regardless of score
    magnitude, then score within each tier.

    The official OSIFOG formula's fail-closed sentinel for an incomplete
    (non-landed) flight is -1e9 (`l2_engine/src/ast.rs::
    SCORING_FAILURE_SENTINEL`), and even a *genuinely computed* quadratic
    apogee-miss penalty can exceed that in magnitude for a wildly off-target
    but otherwise legal candidate. Sorting on raw `.score` alone means any
    status=="success" (ascent-legal) candidate that hasn't found a good
    landing yet scores far below a status=="failed" (never left the pad)
    candidate's floor of 0.0 -- inverting the gradient a GA needs to climb,
    since an ascent-legal airframe is strictly closer to a working design
    than an ascent-unstable one. Empirically this made an OSIFOG campaign's
    single ascent-legal candidate (apogee 512m vs 3000m target, score
    -1e9) rank behind all 23 ascent-illegal siblings (score 0.0) and get
    discarded from elitism immediately."""
    return (1 if candidate.status == "success" else 0, candidate.score)


@dataclass
class RustEvaluationResult:
    id: str
    status: str
    score: float
    apogee_m: float
    mach: float
    min_static_margin: float
    margins: list
    reason: str
    features: list = None
    stage_landings: list = None
    total_prop_mass_kg: float = 0.0
    apogee_east_m: float = 0.0
    apogee_north_m: float = 0.0


@lru_cache(maxsize=8)
def _eng_designations_cached(motors_dir):
    motors_dir = Path(motors_dir)
    designations = set()
    if not motors_dir.exists():
        return designations

    for path in motors_dir.glob("*.eng"):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            designations.add(line.split()[0])
            break
    return frozenset(designations)


def _eng_designations(motors_dir=None):
    motors_dir = (
        Path(motors_dir)
        if motors_dir
        else Path(__file__).resolve().parent / "l2_engine" / "motors"
    )
    return set(_eng_designations_cached(str(motors_dir.resolve())))


def rust_available_motor_indices(motors_dir=None):
    designations = _eng_designations(motors_dir)
    return [idx for idx, motor in enumerate(MOTOR_DATABASE) if motor[1] in designations]


def ast_to_dicts(ast_nodes):
    return [node.to_dict() for node in ast_nodes]


def ast_from_dicts(items):
    return [ASTNode.from_dict(item) for item in items]


_HEIGHT_VIOLATION_RE = re.compile(
    r"constraint_violation:max_height_m ([\d.]+) > ([\d.]+)"
)


def repair_height_violation(ast_nodes, reason, safety_margin_m=0.03):
    """Given a candidate whose evaluator `reason` reports a max_height_m
    overshoot, trims the longest BODY_TUBE (falling back to the NOSE_CONE)
    by exactly that overshoot plus a real (not micrometer-scale) safety
    margin, then returns the same list, mutated in place.

    Without this, every offspring bred from a height-violating parent
    approaches the height cap only from above -- independent per-node
    jitter mutation (see ASTNode.mutate) essentially never produces the
    coordinated multi-gene move needed to cross under the limit in one
    step, so entire campaigns converge onto a single boundary-hugging
    genome and stay illegal for hundreds of generations (see
    .planning/ultra/ULTRAREVIEW-osifog-campaign-height-stall.md). This
    repair gives offspring a real starting point on the legal side of the
    boundary, which ordinary mutation can then explore around normally.

    No-op (returns ast_nodes unchanged) if `reason` isn't a height
    violation or the AST has no trimmable BODY_TUBE/NOSE_CONE.
    """
    if not reason:
        return ast_nodes
    match = _HEIGHT_VIOLATION_RE.match(reason)
    if not match:
        return ast_nodes
    actual_height = float(match.group(1))
    limit = float(match.group(2))
    reduction_needed = (actual_height - limit) + safety_margin_m
    if reduction_needed <= 0:
        return ast_nodes

    body_tubes = [node for node in ast_nodes if node.node_type == "BODY_TUBE"]
    target = (
        max(body_tubes, key=lambda node: node.params.get("length", 0.0))
        if body_tubes
        else next((node for node in ast_nodes if node.node_type == "NOSE_CONE"), None)
    )
    if target is None:
        return ast_nodes

    minimum_length = 0.15 if target.node_type == "BODY_TUBE" else 0.12
    current_length = target.params.get("length", minimum_length)
    target.params["length"] = max(minimum_length, current_length - reduction_needed)
    return ast_nodes


# Numeric params that are discrete identifiers/counts, not continuous
# magnitudes -- must never be blended via interpolation (a "motor_index"
# of 12.7 or a "count" of 3.4 is meaningless). Everything else numeric
# is treated as a continuous gene, matching osifog_legal_stage_campaign.py's
# _inherit_stage / _is_discrete_gene convention (that function's own
# genome is an incompatible flat s0_/s1_-prefixed dict predating the AST
# pipeline, so its code can't be called directly -- this ports the same
# per-attribute uniform-crossover-with-interpolation PATTERN to AST nodes).
_CROSSOVER_DISCRETE_KEYS = {
    "motor_index", "count", "fincount", "instance_count", "multiplicity",
}


def _blend_node_params(node_a, node_b):
    """Per-attribute uniform crossover between two AST nodes of the SAME
    (node_type, role): for each shared param, 45% chance of interpolating
    a continuous numeric value (random alpha blend, not just picking one
    parent's value), otherwise a uniform 50/50 discrete pick -- exactly
    _inherit_stage's own per-key policy. A param present in only one node
    is always inherited from whichever node has it."""
    keys = set(node_a.params) | set(node_b.params)
    child_params = {}
    for key in keys:
        has_a = key in node_a.params
        has_b = key in node_b.params
        if has_a and not has_b:
            child_params[key] = node_a.params[key]
            continue
        if has_b and not has_a:
            child_params[key] = node_b.params[key]
            continue
        val_a, val_b = node_a.params[key], node_b.params[key]
        numeric = (
            key not in _CROSSOVER_DISCRETE_KEYS
            and isinstance(val_a, (int, float)) and not isinstance(val_a, bool)
            and isinstance(val_b, (int, float)) and not isinstance(val_b, bool)
        )
        if numeric and random.random() < 0.45:
            alpha = random.uniform(0.0, 1.0)
            child_params[key] = float(val_a) + alpha * (float(val_b) - float(val_a))
        else:
            child_params[key] = val_a if random.random() < 0.5 else val_b
    return ASTNode(node_a.node_type, **child_params)


def crossover_ast(parent_a, parent_b):
    """Node-level recombination between two AST candidates: for each stage
    position, match corresponding nodes (same node_type + role) between the
    two parents' variants of that stage and blend each matched pair via
    _blend_node_params; unmatched nodes are inherited as-is from whichever
    parent has more stages (the "longer" one, so a 3-stage parent's extra
    stage isn't silently dropped when crossed with a 2-stage parent).

    This was a genuine, total gap in this pipeline until this session --
    run_generation's reproduction loop only ever called
    mutate_ast(single_parent), with no mechanism anywhere to COMBINE traits
    from two different individuals. Confirmed as the actual cause of a live
    campaign's population freezing at a near-identical value
    (min_thrust_to_weight=1.199999, unchanged across 100+ generations even
    after removing the constraints that used to bind first): mutation alone
    can only slowly perturb ONE lineage, so if "good TWR" and "good margin"
    exist in different lineages, pure mutation has no way to combine them --
    it has to rediscover both improvements sequentially in the SAME
    lineage, which is exponentially harder than crossing two already-good
    parents. A first version of this function only swapped WHOLE stages
    (coarser-grained, all-or-nothing per stage) -- upgraded to this
    per-node, per-attribute, interpolation-capable version after finding
    osifog_legal_stage_campaign.py's `_inherit_stage` (a real, more
    sophisticated prior implementation of the same idea, just on an
    incompatible pre-AST genome) and porting its actual design: fine-grained
    blending finds intermediate values a coarse stage-swap or a mutation
    step alone cannot reach.

    Any inter-stage/inter-node inconsistency this introduces (mismatched
    body radius between a fin inherited from parent A and a body tube from
    parent B, a frozen octaweb cage sized for the wrong body, etc.) is
    corrected by sanitize_ast_for_openrocket's own diameter-continuity and
    octaweb-cage repair passes, which already run on every candidate
    regardless of crossover -- this was hardened earlier this same session
    specifically to be self-healing, which is what makes this safe to add
    rather than a further source of invalid geometry.
    """
    stages_a = _split_stages(parent_a)
    stages_b = _split_stages(parent_b)
    longer, shorter = (
        (stages_a, stages_b) if len(stages_a) >= len(stages_b) else (stages_b, stages_a)
    )

    child_stages = []
    for i in range(len(longer)):
        if i >= len(shorter):
            child_stages.append(longer[i])
            continue
        stage_long, stage_short = longer[i], shorter[i]
        short_by_key = {}
        for node in stage_short:
            if node.node_type in ("STAGE", "CLOSE_BODY"):
                continue
            key = (node.node_type, node.params.get("role"))
            short_by_key.setdefault(key, []).append(node)
        used = {}
        child_stage = []
        for node in stage_long:
            if node.node_type in ("STAGE", "CLOSE_BODY"):
                child_stage.append(node)
                continue
            key = (node.node_type, node.params.get("role"))
            idx = used.get(key, 0)
            candidates = short_by_key.get(key, [])
            if idx < len(candidates):
                used[key] = idx + 1
                child_stage.append(_blend_node_params(node, candidates[idx]))
            else:
                child_stage.append(node)
        child_stages.append(child_stage)
    return [node for stage in child_stages for node in stage]


def mutate_ast(ast_nodes, rate=0.35, motor_pool=None, retro_motor_pool=None, repair_space=None, allow_parachute=True):
    mutated = ast_from_dicts(ast_to_dicts(ast_nodes))

    for node in mutated:
        if random.random() < rate:
            node.mutate(rate, motor_pool, retro_motor_pool)

    if random.random() < rate * 0.45:
        _structural_mutation(mutated, motor_pool, retro_motor_pool, repair_space, allow_parachute)

    return normalize_ast(mutated)


def normalize_ast(ast_nodes):
    if not ast_nodes or ast_nodes[0].node_type != "STAGE":
        ast_nodes.insert(0, ASTNode("STAGE", name="Evolved Sustainer"))

    body_depth = 0
    normalized = []
    for node in ast_nodes:
        if node.node_type == "BODY_TUBE":
            body_depth += 1
        elif node.node_type == "CLOSE_BODY":
            if body_depth <= 0:
                continue
            body_depth -= 1
        normalized.append(node)

    while body_depth > 0:
        normalized.append(ASTNode("CLOSE_BODY"))
        body_depth -= 1

    if not any(node.node_type == "BODY_TUBE" for node in normalized):
        normalized.insert(1, ASTNode("BODY_TUBE", length=1.0, radius=0.04, material="cardboard"))
        normalized.append(ASTNode("CLOSE_BODY"))

    if not any(node.node_type == "MOTOR_MOUNT" for node in normalized):
        insert_at = max(1, len(normalized) - 1)
        normalized.insert(insert_at, ASTNode("MOTOR_MOUNT", motor_index=len(MOTOR_DATABASE) - 1, ignition="automatic"))

    if not any(node.node_type == "FIN_SET" for node in normalized):
        insert_at = max(1, len(normalized) - 1)
        normalized.insert(insert_at, ASTNode("FIN_SET", count=4, sweep=30.0, root=0.12, height=0.06))

    normalize_stage_ignition_events(normalized)
    normalized = sanitize_ast_for_openrocket(normalized)
    normalize_stage_ignition_events(normalized)
    return normalized


def normalize_stage_ignition_events(ast_nodes):
    # role="retro" mounts are landing/braking motors, not ascent motors --
    # they must ignite near touchdown (ignition="burnout" of THIS stage's
    # own main motor, plus a tuned ignition_delay -- see ASTNode.mutate()'s
    # own comment: "the load-bearing search variable for a retro burn...
    # must oppose velocity near touchdown, not near apogee"), never at
    # launch. This function used to overwrite ignition on EVERY MOTOR_MOUNT
    # (role blind) purely from stage position and unconditionally delete
    # ignition_delay -- confirmed live via a real generated candidate: every
    # bottom-stage octaweb rocket's retro motor got ignition="automatic"
    # (fires at launch, simultaneously with the main ascent motor, instead
    # of near touchdown) and lost its ignition_delay every single
    # generation, silently breaking the GA's ability to ever tune a soft
    # landing. Scoped to non-retro mounts only, which is what this
    # function's own stage-position logic was actually meant to fix (see
    # the comment below).
    # Main/ascent motors always use "automatic", regardless of stage
    # position. This function used to write "burnout" for every non-bottom
    # stage's main motor, on the assumption that OpenRocket only auto-
    # ignites the bottom stage. That assumption is wrong: OpenRocket's
    # "automatic" event is itself stage-position-aware (launch for the
    # bottom stage, lower-stage-separation for every stage above it) --
    # confirmed via a direct l2_engine ast_trace: a "burnout"-tagged upper-
    # stage main motor never ignited at all in the whole simulated flight
    # (its "burnout" ignition_delay resolves against ITS OWN burn duration,
    # a self-referential no-op in l2_engine/src/mission_adapter.rs), while
    # "automatic" fired it correctly, right at stage activation. This means
    # every 2+-stage candidate this pipeline has ever scored had its upper
    # stage's motor silently contribute nothing to Rust's fitness landscape.
    for node in ast_nodes:
        if node.node_type == "MOTOR_MOUNT" and node.params.get("role") != "retro":
            node.params["ignition"] = "automatic"
            node.params.pop("ignition_delay", None)
    return ast_nodes


def _repair_radial_offsets_after_reassignment(ast_nodes):
    """A motor reassigned above (because Rust has no curve data for the
    original pick) leaves any radial_offset_m that was DERIVED from the old
    motor's radius stale -- silently physically-invalid (overlapping or
    needlessly oversized) once the actual motor is now a different
    diameter. Confirmed live: this was firing `motor_mount_collision` on
    ~23% of a running campaign's population before this fix, entirely from
    swap-staleness, not genuine bad genomes. Recomputes radial_offset_m
    fresh from each stage's CURRENT motor selection -- both the plain
    single-retro layout (main_radius + retro_radius + clearance) and the
    octaweb 3-ring layout (via osifog_sweep._falcon_cluster_geometry, same
    formula octaweb_motor_mounts uses at generation time)."""
    stage_body_radius = None
    stage_mounts = []
    for node in ast_nodes:
        if node.node_type == "STAGE":
            stage_body_radius = None
            stage_mounts = []
        elif node.node_type == "BODY_TUBE" and stage_body_radius is None:
            stage_body_radius = node.params.get("radius", 0.04)
        elif node.node_type == "MOTOR_MOUNT":
            stage_mounts.append(node)
        elif node.node_type == "CLOSE_BODY":
            _repair_stage_motor_geometry(stage_mounts, stage_body_radius)
            stage_mounts = []


def _repair_stage_motor_geometry(mounts, body_radius_m):
    if len(mounts) != 2 or body_radius_m is None:
        return
    main = next((m for m in mounts if m.params.get("role") != "retro"), None)
    retro = next((m for m in mounts if m.params.get("role") == "retro"), None)
    if main is None or retro is None:
        return
    main_idx = _motor_index(main.params)
    retro_idx = _motor_index(retro.params)
    is_octaweb = main.params.get("multiplicity", 1) == 3 and main.params.get("cluster_configuration") == "3-ring"
    if is_octaweb:
        from osifog_sweep import _falcon_cluster_geometry
        try:
            cage = _falcon_cluster_geometry(main_idx, retro_idx, body_radius_m)
        except ValueError:
            # No legal cage for the new motor pair at this body radius --
            # leave params as-is; the Rust hard-constraint check (task 1)
            # will correctly reject it rather than silently accepting a
            # stale, invalid geometry.
            return
        chord = cage["center_distance_m"] * math.sqrt(3.0)
        if chord < 2.0 * cage["main_outer_radius_m"] + 0.002:
            return
        main.params["radial_offset_m"] = cage["center_distance_m"]
        main.params["cluster_scale"] = cage["cluster_scale"]
        main.params["main_outer_radius_m"] = cage["main_outer_radius_m"]
        main.params["retro_sleeve_outer_radius_m"] = cage["retro_sleeve_outer_radius_m"]
    else:
        main_r = MOTOR_DATABASE[main_idx][2] / 2.0
        retro_r = MOTOR_DATABASE[retro_idx][2] / 2.0
        retro.params["radial_offset_m"] = main_r + retro_r + 0.004


def prepare_ast_for_rust(ast_nodes, motor_pool=None, retro_motor_pool=None):
    ast_nodes = sanitize_ast_for_openrocket(ast_nodes)
    available = rust_available_motor_indices()
    if not available:
        return ast_nodes

    available_designations = {MOTOR_DATABASE[idx][1] for idx in available}
    main_fallback = [
        idx for idx in available if not motor_pool or MOTOR_DATABASE[idx][1] in motor_pool
    ] or available
    retro_fallback = [
        idx for idx in available
        if not retro_motor_pool or MOTOR_DATABASE[idx][1] in retro_motor_pool
    ] or available
    for node in ast_nodes:
        if node.node_type == "MOTOR_MOUNT":
            idx = int(node.params.get("motor_index", available[-1]))
            idx = min(max(idx, 0), len(MOTOR_DATABASE) - 1)
            designation = node.params.get("motor_designation") or MOTOR_DATABASE[idx][1]
            if designation not in available_designations:
                # Reassign from the role-appropriate pool -- a retro mount
                # falling back to an arbitrary Rust-available motor could
                # silently pick one outside the mission's retro_allowed_
                # designations, which is exactly the kind of drift this fix
                # is meant to close.
                pool = retro_fallback if node.params.get("role") == "retro" else main_fallback
                idx = random.choice(pool)
                designation = MOTOR_DATABASE[idx][1]
            node.params["motor_index"] = idx
            node.params["motor_designation"] = designation
    # Unconditional, not just when this function's own fallback fired above:
    # ASTNode.mutate()'s MOTOR_MOUNT branch can independently swap a motor's
    # designation (its own jitter roll, unrelated to Rust-curve availability)
    # without touching radial_offset_m -- prepare_ast_for_rust runs after
    # every mutation (organic_loop.py's per-generation loop), so this is the
    # one place that reliably sees every motor change regardless of which
    # code path caused it. Idempotent/cheap on already-consistent geometry.
    _repair_radial_offsets_after_reassignment(ast_nodes)
    return sanitize_ast_for_openrocket(ast_nodes)


def evaluate_ast(ast_nodes, ckg, target_apogee_m=15000.0):
    status, reason = validate_ast(ast_nodes)
    raw_score = heuristic_score(ast_nodes, target_apogee_m)
    if status != "success":
        raw_score -= 100.0

    score = raw_score * ckg.acceptance_multiplier(ast_nodes)
    return OrganicCandidate(
        ast=ast_nodes,
        score=score,
        raw_score=raw_score,
        status=status,
        reason=reason,
        rust_margins=[],
    )


def validate_ast(ast_nodes):
    body_depth = 0
    motor_count = 0
    for node in ast_nodes:
        if node.node_type == "BODY_TUBE":
            body_depth += 1
        elif node.node_type == "CLOSE_BODY":
            body_depth -= 1
            if body_depth < 0:
                return "failed", "body closed before open"
        elif node.node_type == "MOTOR_MOUNT":
            motor_count += 1
        elif node.node_type == "PARACHUTE" and node.params.get("deploy") == "ejection":
            return "failed", "unsafe ejection deploy"

    if body_depth != 0:
        return "failed", "unclosed body tube"
    if motor_count == 0:
        return "failed", "missing motor"
    return "success", "heuristic candidate"


def heuristic_score(ast_nodes, target_apogee_m):
    total_length = 0.0
    average_radius = 0.04
    radii = []
    payload = 0.0
    fin_area = 0.0
    motor_power = 0.0
    chute_drag = 0.0
    stage_count = 0

    for node in ast_nodes:
        params = node.params
        if node.node_type == "STAGE":
            stage_count += 1
        elif node.node_type == "BODY_TUBE":
            total_length += params.get("length", 0.8)
            radii.append(params.get("radius", 0.04))
        elif node.node_type == "MOTOR_MOUNT":
            idx = min(params.get("motor_index", 0), len(MOTOR_DATABASE) - 1)
            motor_power += 1.0 + idx / max(1, len(MOTOR_DATABASE) - 1)
        elif node.node_type == "PAYLOAD":
            payload += params.get("mass", 0.0)
        elif node.node_type == "FIN_SET":
            fin_area += params.get("root", 0.1) * params.get("height", 0.05) * params.get("count", 4)
        elif node.node_type == "PARACHUTE":
            chute_drag += params.get("diameter", 0.5) ** 2

    if radii:
        average_radius = sum(radii) / len(radii)

    slenderness = total_length / max(average_radius * 2.0, 0.001)
    stability = min(2.0, fin_area / max(average_radius * total_length, 0.001))
    mass_proxy = 2.0 + payload + total_length * average_radius * 18.0 + chute_drag * 0.4
    apogee_proxy = motor_power * 9000.0 * (1.0 + 0.18 * max(0, stage_count - 1)) / mass_proxy
    target_fit = 1.0 / (1.0 + abs(apogee_proxy - target_apogee_m) / max(target_apogee_m, 1.0))

    return target_fit * 80.0 + stability * 8.0 + min(slenderness, 20.0) * 0.6


def _resolve_stage_range(mission_data):
    """A mission's `topology.stage_count` (an exact design intent, e.g.
    OSIFOG's 2-stage octaweb design) takes precedence over the looser
    `constraints.min_stages`/`max_stages` legality range when both are
    present. Without this, `topology` was never read at all, so a mission
    declaring an exact stage count still got a randomly 2-or-3-staged
    rocket every generation -- constraints only express legality bounds,
    not design intent, and evolving extra stages the mission never wanted
    wastes search budget on a topology dimension the mission already
    answered. Falls back to the constraints range unchanged when
    `topology`/`stage_count` is absent, so missions that don't set it are
    unaffected."""
    constraints = mission_data.get("constraints", {})
    min_stages = constraints.get("min_stages", 1)
    max_stages = constraints.get("max_stages", 2)
    stage_count = mission_data.get("topology", {}).get("stage_count")
    if stage_count is not None:
        min_stages = max_stages = int(stage_count)
    return min_stages, max_stages


def _resolve_octaweb_probability(mission_data):
    """A mission's `topology.main_cluster` is the same kind of firm design
    intent as `topology.stage_count` (see `_resolve_stage_range`) -- OSIFOG's
    mission declares `{"configuration": "3-ring", "count": 3}` because the
    3-main+1-retro octaweb cluster IS the design, not an optional mutation
    to explore. Without this, `octaweb_probability` stayed 0.0 forever (its
    `create_random_ast` default) no matter what the mission declared -- this
    was confirmed live: the v7 campaign has run since 2026-07-23T18:26 and
    never generated a single octaweb candidate."""
    main_cluster = mission_data.get("topology", {}).get("main_cluster", {})
    return 1.0 if main_cluster.get("configuration") == "3-ring" else 0.0


def run_generation(config):
    random.seed(config.seed)
    ckg = ContinuousKnowledgeGraph(config.ckg_path)

    mission_data = {}
    if config.mission_path and Path(config.mission_path).exists():
        mission_data = json.loads(Path(config.mission_path).read_text())
    # Read once, threaded through both fresh generation (create_random_ast)
    # and mutation (mutate_ast) below, so a J/K/L-class restriction (or any
    # other mission's motor_pool) can never be bred out over generations.
    motor_pool_designations = mission_data.get("motor_pool", {}).get("allowed_designations")
    retro_motor_pool_designations = mission_data.get("motor_pool", {}).get("retro_allowed_designations")
    no_recovery_devices = bool(mission_data.get("constraints", {}).get("no_recovery_devices", False))
    repair_space = mission_data.get("evolution", {}).get("physical_repair_space", {})
    # A mission that defines a forward-fin repair space (e.g. OSIFOG's
    # Starship-flap descent mechanism) wants it on essentially every
    # candidate -- the whole point is that a plain aft-fin topology cannot
    # reach a legal tail-first descent at all, so this isn't an optional
    # mutation, it's close to a structural requirement.
    forward_flap_probability = 1.0 if "forward_fin_count" in repair_space else 0.0
    retro_motor_probability = 1.0 if retro_motor_pool_designations else 0.0
    octaweb_probability = _resolve_octaweb_probability(mission_data)

    population = []
    if config.seed_from:
        with open(config.seed_from) as f:
            data = json.load(f)
            for item in data.get("elite", []):
                population.append([ASTNode(n["type"], **n.get("params", {})) for n in item["ast"]])

    needed = config.population - len(population)
    if needed > 0:
        min_stages, max_stages = _resolve_stage_range(mission_data)

        population.extend([
            normalize_ast(create_random_ast(
                min_stages,
                max_stages,
                motor_pool=motor_pool_designations,
                no_recovery_devices=no_recovery_devices,
                forward_flap_probability=forward_flap_probability,
                repair_space=repair_space,
                retro_motor_pool=retro_motor_pool_designations,
                retro_motor_probability=retro_motor_probability,
                octaweb_probability=octaweb_probability,
            ))
            for _ in range(needed)
        ])
    if config.evaluator == "rust":
        population = [
            prepare_ast_for_rust(ast, motor_pool_designations, retro_motor_pool_designations)
            for ast in population
        ]

    evaluated = []
    for generation in range(config.generations):
        if config.evaluator == "rust":
            evaluated = evaluate_rust_population(population, ckg, config)
        else:
            evaluated = [evaluate_ast(ast, ckg, config.target_apogee_m) for ast in population]
        evaluated.sort(key=selection_rank, reverse=True)

        mode_profile = MODE_PROFILES[config.execution_profile]
        if (
            config.evaluator == "rust"
            and mode_profile["promote_profile"]
            and (generation + 1) % mode_profile["promote_every"] == 0
        ):
            promote_count = max(
                config.elite_count,
                math.ceil(config.population * mode_profile["promote_fraction"]),
            )
            promote_candidates(
                evaluated,
                ckg,
                config,
                mode_profile["promote_profile"],
                promote_count,
            )
            evaluated.sort(key=selection_rank, reverse=True)
        
        # Stratified OpenRocket calibration and divergence-model learning.
        if (
            config.calibrate_every > 0
            and (generation + 1) % config.calibrate_every == 0
            and config.or_helper
            and evaluated[0].status == "success"
        ):
            config.output_dir.mkdir(parents=True, exist_ok=True)
            default_size = mode_profile["calibration_sample_size"]
            sample_size = config.calibration_sample_size or default_size
            selected = select_stratified_calibration_candidates(
                evaluated, sample_size, config.constraints
            )
            config.divergence_history = config.divergence_history or []
            new_samples = []
            for sample_index, candidate in enumerate(selected):
                name = f"calibrate_G{generation + 1:03d}_S{sample_index:02d}"
                ork_path = config.output_dir / f"{name}.ork"
                write_ork_zip(ork_path, ASTCompiler().compile(candidate.ast, name=name))
                or_metrics = validate_openrocket_ork(
                    ork_path, config.or_helper, config.phase_machs
                )
                if or_metrics.get("status") != "success":
                    continue
                calibration_features = (
                    candidate.screen_features
                    if config.execution_profile == "super-speed"
                    else candidate.rust_features
                )
                raw_apogee = (
                    candidate.screen_apogee_m
                    if config.execution_profile == "super-speed"
                    else candidate.rust_apogee_m
                )
                raw_mach = (
                    candidate.screen_mach
                    if config.execution_profile == "super-speed"
                    else candidate.rust_mach
                )
                sig = extract_topological_signature(candidate.ast)
                ckg.record_calibration(
                    sig,
                    or_metrics["apogee_m"] / max(raw_apogee, 1.0),
                    or_metrics["mach"] / max(raw_mach, 1.0e-9),
                )
                if calibration_features and len(calibration_features) == 25:
                    sample = {
                        "features": calibration_features,
                        "apogee_correction_m": or_metrics["apogee_m"] - raw_apogee,
                        "mach_correction": or_metrics["mach"] - raw_mach,
                    }
                    new_samples.append(sample)
                    config.divergence_history.append(
                        {
                            "generation": generation + 1,
                            "execution_profile": config.execution_profile,
                            "topology_signature": sig,
                            "rust_apogee_m": raw_apogee,
                            "rust_mach": raw_mach,
                            "or_apogee_m": or_metrics["apogee_m"],
                            "or_mach": or_metrics["mach"],
                            "features": calibration_features,
                        }
                    )
            if new_samples:
                config.divergence_model = fit_divergence_model(
                    new_samples, config.divergence_model
                )
                persist_divergence_state(config)

        for candidate in evaluated:
            if candidate.reason == "ckg_prefilter":
                # This candidate never reached real physics evaluation -- it
                # was rejected BY the CKG's own multiplier. Recording it as
                # another "failure" on the same shared subgraph keys would
                # feed the prefilter's own output back into its input: a
                # runaway positive-feedback loop where a rough early
                # generation permanently drives the acceptance multiplier for
                # near-universal, low-information node labels (e.g. a bare
                # `STAGE`/`CLOSE_BODY` node shared by literally every
                # candidate) toward zero, prefiltering the entire population
                # regardless of genome quality thereafter. Confirmed
                # empirically: a 24-pop/4-gen verify run with zero real
                # successes recorded had 96/96 failures on `CLOSE_BODY:{}`
                # alone by generation 4, with every generation-4 elite
                # rejected by ckg_prefilter before reaching Rust. Only
                # genuinely-evaluated outcomes (Rust or OpenRocket authority)
                # should shape this memory.
                continue
            ckg.record_items(
                candidate.ckg_items or ckg.subgraph_items(candidate.ast),
                candidate.score,
                candidate.status,
                candidate.reason,
            )

        survivors = evaluated[: max(config.elite_count, config.population // 4)]
        population = [ast_from_dicts(ast_to_dicts(candidate.ast)) for candidate in survivors[: config.elite_count]]

        def _repaired_ast(candidate):
            parent = candidate.ast
            if (
                candidate.status == "failed"
                and candidate.reason
                and candidate.reason.startswith("constraint_violation:max_height_m")
            ):
                # Clone before repairing -- candidate.ast is shared (it's
                # also read by `population` above and by CKG recording a
                # few lines up); mutating it in place would corrupt state
                # other code still expects to see unchanged this generation.
                parent = repair_height_violation(
                    ast_from_dicts(ast_to_dicts(parent)), candidate.reason
                )
            return parent

        def _select_parent():
            return _repaired_ast(random.choice(survivors))

        def _constraint_type(candidate):
            reason = candidate.reason or ""
            if not reason.startswith("constraint_violation:"):
                return reason
            return reason[len("constraint_violation:"):].split()[0]

        def _select_complementary_pair():
            # Deliberately cross a specialist blocked by one constraint
            # with a specialist blocked by a DIFFERENT one -- e.g. a
            # min_thrust_to_weight-limited candidate crossed with a
            # min_static_margin-limited one -- rather than uniform random
            # pairing. Ported from osifog_legal_stage_campaign.py's
            # deliberate "ascent phenotype x recovery phenotype" crossing
            # (that function's own genome is incompatible with the AST, so
            # only the STRATEGY is ported, not the code): a single scalar
            # ranking erases exactly this kind of complementary pairing,
            # since two specialists rarely rank adjacently. Falls back to
            # uniform random for parent_b if every survivor shares the
            # same blocking constraint (e.g. early in a run, or once truly
            # close to legal) -- there's nothing complementary to find yet.
            candidate_a = random.choice(survivors)
            type_a = _constraint_type(candidate_a)
            complementary = [c for c in survivors if _constraint_type(c) != type_a]
            candidate_b = random.choice(complementary) if complementary else random.choice(survivors)
            return _repaired_ast(candidate_a), _repaired_ast(candidate_b)

        while len(population) < config.population:
            # Crossover a real fraction of new children -- see
            # crossover_ast's own docstring for why this is necessary
            # (pure mutation-of-one-parent cannot combine traits from two
            # different lineages; confirmed as the actual cause of a live
            # campaign's population freezing at an unchanged
            # min_thrust_to_weight value for 100+ generations even after
            # removing the constraints that previously bound first).
            # len(survivors) > 1 guard: nothing to cross with only 1
            # survivor (tiny population / very first generation).
            if len(survivors) > 1 and random.random() < 0.5:
                parent = crossover_ast(*_select_complementary_pair())
            else:
                parent = _select_parent()
            child = mutate_ast(
                parent,
                motor_pool=motor_pool_designations,
                retro_motor_pool=retro_motor_pool_designations,
                repair_space=repair_space,
                allow_parachute=not no_recovery_devices,
            )
            if config.evaluator == "rust":
                child = prepare_ast_for_rust(child, motor_pool_designations, retro_motor_pool_designations)
            population.append(child)

        elites = evaluated[: config.elite_count]
        # Persist proxy progress without paying the sequential OpenRocket
        # authority cost on every generation. `calibrate_every` owns periodic
        # authority sampling; ranked validation belongs to the final export.
        export_elites(elites, config, ckg, validate_openrocket=False)
        ckg.save()

        if config.progress_callback is not None:
            config.progress_callback(generation, evaluated)

    if not evaluated:
        if config.evaluator == "rust":
            evaluated = evaluate_rust_population(population, ckg, config)
        else:
            evaluated = [evaluate_ast(ast, ckg, config.target_apogee_m) for ast in population]
        evaluated.sort(key=selection_rank, reverse=True)

    elites = evaluated[: config.elite_count]
    export_elites(elites, config, ckg)
    ckg.save()
    return type("OrganicLoopResult", (), {"elites": elites, "ckg": ckg})()


def evaluate_rust_population(
    population, ckg, config, candidate_environments=None
):
    if (
        candidate_environments is not None
        and len(candidate_environments) != len(population)
    ):
        raise ValueError(
            "candidate_environments must contain one entry per AST candidate"
        )
    pending = []
    evaluated = []

    for idx, ast in enumerate(population):
        ckg_items = ckg.subgraph_items(ast)
        multiplier = ckg.acceptance_multiplier_for_items(ckg_items)
        # No hard prefilter gate here (previously: reject without evaluation
        # below multiplier<0.10). In a hard mission with a low baseline
        # legality rate (empirically ~4% from pure random sampling on this
        # genome), most candidates share common low-level features (a given
        # motor designation, a given fin count) with the many failures that
        # are normal at this rate -- a hard veto keyed on cumulative failure
        # counts on those shared features collapsed an entire fresh
        # 24-pop/6-gen run to 0 real evaluations by generation 3, regardless
        # of whether newer mutations were actually better. The soft
        # `multiplier` below (floor 0.05, applied to admitted candidates'
        # scores at line ~704) still biases selection away from
        # historically-poor structural neighborhoods without ever blocking
        # evaluation outright.
        sig = extract_topological_signature(ast)
        pending.append({
            "id": f"cand-{idx}",
            "ast": ast_to_dicts(ast),
            "multiplier": multiplier,
            "ast_nodes": ast,
            "signature": sig,
            "ckg_items": ckg_items,
            "environment": (
                candidate_environments[idx]
                if candidate_environments is not None
                else None
            ),
        })

    if not pending:
        return evaluated

    evaluator = config.rust_evaluator or run_rust_evaluator
    if config.rust_evaluator is None:
        results = evaluator(
            pending,
            config.target_apogee_m,
            config.physics_mode,
            config.objectives,
            config.constraints,
            ckg.calibrations,
            execution_profile=config.execution_profile,
            divergence_model=config.divergence_model,
        )
    else:
        # Preserve the established custom-evaluator test/plugin contract.
        results = evaluator(
            pending,
            config.target_apogee_m,
            config.physics_mode,
            config.objectives,
            config.constraints,
            ckg.calibrations,
        )
    by_id = {result.id: result for result in results}

    for candidate in pending:
        result = by_id[candidate["id"]]
        multiplier = candidate["multiplier"]
        status = result.status
        raw_score = float(result.score)
        # `result.score` is the official/proxy fitness for a legal candidate,
        # or a small [0,1] closeness-to-passing ratio for an illegal one (see
        # ast.rs::enforce_hard_constraints) -- applying it here (instead of a
        # flat 0.0) gives selection a gradient to climb even while nothing in
        # the population is legal yet. It never promotes a failed candidate
        # to legal; selection_rank still ranks every "success" above every
        # "failed" regardless of this magnitude.
        score = raw_score * multiplier
        evaluated.append(
            OrganicCandidate(
                ast=candidate["ast_nodes"],
                score=score,
                raw_score=raw_score,
                status=status,
                reason=result.reason,
                rust_apogee_m=float(result.apogee_m),
                rust_mach=float(result.mach),
                rust_min_static_margin=float(result.min_static_margin),
                rust_margins=list(result.margins or []),
                rust_features=list(result.features or []),
                rust_stage_landings=list(result.stage_landings or []),
                rust_total_prop_mass_kg=float(result.total_prop_mass_kg),
                rust_apogee_east_m=float(result.apogee_east_m),
                rust_apogee_north_m=float(result.apogee_north_m),
                screen_apogee_m=float(result.apogee_m),
                screen_mach=float(result.mach),
                screen_features=list(result.features or []),
                ckg_items=candidate["ckg_items"],
            )
        )

    return evaluated


def promote_candidates(evaluated, ckg, config, execution_profile, count):
    """Re-evaluate the leading screening candidates at the next fidelity."""
    selected = [item for item in evaluated if item.status == "success"][:count]
    if not selected:
        return
    pending = [
        {
            "id": f"promote-{index}",
            "ast": ast_to_dicts(candidate.ast),
            "signature": extract_topological_signature(candidate.ast),
        }
        for index, candidate in enumerate(selected)
    ]
    results = run_rust_evaluator(
        pending,
        config.target_apogee_m,
        config.physics_mode,
        config.objectives,
        config.constraints,
        {},
        execution_profile=execution_profile,
        divergence_model=None,
    )
    for candidate, result in zip(selected, results):
        candidate.status = result.status
        candidate.reason = f"promoted:{execution_profile}:{result.reason}"
        candidate.raw_score = float(result.score)
        multiplier = ckg.acceptance_multiplier_for_items(
            candidate.ckg_items or ckg.subgraph_items(candidate.ast)
        )
        candidate.score = candidate.raw_score * multiplier
        candidate.rust_apogee_m = float(result.apogee_m)
        candidate.rust_mach = float(result.mach)
        candidate.rust_min_static_margin = float(result.min_static_margin)
        candidate.rust_margins = list(result.margins or [])
        candidate.rust_features = list(result.features or [])
        candidate.rust_stage_landings = list(result.stage_landings or [])
        candidate.rust_total_prop_mass_kg = float(result.total_prop_mass_kg)
        candidate.rust_apogee_east_m = float(result.apogee_east_m)
        candidate.rust_apogee_north_m = float(result.apogee_north_m)


def format_score_human(value):
    """Human-readable companion for a raw score float. The official OSIFOG
    formula's penalty terms (-3000/-500/-16/-2 coefficients on squared
    deltas) can legitimately produce scores in the hundreds of trillions for
    a badly-missed candidate -- a bare float like -208590854507870.9 is
    unreadable at a glance. Comma-separates the full value and appends a
    magnitude suffix (K/M/B/T) for anything at or beyond a million so it can
    be read without counting digits. Never used for anything but display --
    the raw float stays the source of truth everywhere else."""
    if value is None:
        return None
    magnitude = abs(value)
    suffix = ""
    if magnitude >= 1e12:
        suffix = f" ({value / 1e12:.2f}T)"
    elif magnitude >= 1e9:
        suffix = f" ({value / 1e9:.2f}B)"
    elif magnitude >= 1e6:
        suffix = f" ({value / 1e6:.2f}M)"
    return f"{value:,.2f}{suffix}"


def official_score_breakdown(candidate, mission_scoring):
    """Mirror `l2_engine/src/ast.rs::evaluate_scoring_table` term-for-term
    from an already-evaluated OrganicCandidate's raw metrics, for human/
    monitoring consumption. This is NOT used for GA selection (that stays on
    `candidate.score`/`selection_rank`) -- it exists purely so a campaign's
    monitoring files can show *why* the best candidate scores what it does,
    term by term, instead of one opaque number. Reports a term as
    incomplete (value=None) rather than fabricating a number when its
    metric's data is missing (e.g. a stage that never landed) -- honest
    "cannot compute yet" beats a plausible-looking but wrong figure."""
    base_score = float(mission_scoring.get("base_score", 0.0))
    stage_landings = candidate.rust_stage_landings or []
    scalar_metrics = {
        "apogee_m": candidate.rust_apogee_m,
        "apogee_east_m": candidate.rust_apogee_east_m,
        "apogee_north_m": candidate.rust_apogee_north_m,
        "total_prop_mass_kg": candidate.rust_total_prop_mass_kg,
    }
    per_stage_metrics = {
        "stage_landing_east_m": [s.get("east_m") for s in stage_landings],
        "stage_landing_north_m": [s.get("north_m") for s in stage_landings],
        "stage_landing_total_speed_ms": [s.get("total_speed_ms") for s in stage_landings],
    }

    def _aggregate(values, aggregate):
        values = [v for v in values if v is not None]
        if not values:
            return None
        if aggregate == "sum_over_stages":
            return sum(values)
        if aggregate == "max_over_stages":
            return max(values)
        return sum(values) / len(values)  # mean_over_stages (default)

    terms_out = []
    score = base_score
    complete = bool(stage_landings)
    for term in mission_scoring.get("terms", []):
        metrics = term.get("metrics", [])
        references = term.get("reference", [])
        power = term.get("power", 1)
        coefficient = term.get("coefficient", 0.0)
        aggregate = term.get("aggregate")
        penalty_sum = 0.0
        term_complete = True
        components = []
        for metric_name, reference in zip(metrics, references):
            if metric_name in per_stage_metrics:
                aggregated = _aggregate(per_stage_metrics[metric_name], aggregate)
            else:
                aggregated = scalar_metrics.get(metric_name)
            if aggregated is None:
                term_complete = False
                components.append({"metric": metric_name, "value": None, "reference": reference})
                continue
            raw_penalty = (aggregated - reference) ** power
            components.append({
                "metric": metric_name,
                "value": aggregated,
                "reference": reference,
                "delta": aggregated - reference,
                "raw_penalty": raw_penalty,
            })
            penalty_sum += raw_penalty
        term_loss = coefficient * penalty_sum if term_complete else None
        if term_complete:
            score += term_loss
        else:
            complete = False
        terms_out.append({
            "name": term.get("name"),
            "coefficient": coefficient,
            "components": components,
            "loss": term_loss,
            "complete": term_complete,
        })

    computed_score = score if complete else None
    return {
        "base_score": base_score,
        "terms": terms_out,
        "computed_score": computed_score,
        "computed_score_display": format_score_human(computed_score),
        "complete": complete,
        "stages_landed": len(stage_landings),
    }


def load_mission_target_apogee(mission_path):
    mission = json.loads(Path(mission_path).read_text(encoding="utf-8"))
    for objective in mission.get("objectives", []):
        metric = str(objective.get("metric", "")).lower()
        if metric not in {"apogee", "apogee_m", "altitude", "max_altitude"}:
            continue
        if objective.get("kind") == "maximize" and "scale" in objective:
            return float(objective["scale"])
        for key in ("value", "target"):
            if key in objective:
                return float(objective[key])
    return 15000.0


def _build_environment(launch_environment, defaults):
    """Builds a per-candidate simulation environment from a mission's `launch`
    (+ `atmosphere.humidity`) block, falling back to `defaults` for anything
    unspecified. `azimuth_range_deg: [min, max]`, when present, samples a
    fresh launch azimuth per candidate rather than pinning one fixed value --
    this explores azimuth as an optimization variable via per-evaluation
    resampling. It is not yet a heritable genome parameter (a survivor's good
    azimuth is not preserved across generations the way its AST is); that
    requires the richer candidate representation planned for the Rust GA
    loop (Phase 3)."""
    if not launch_environment:
        return defaults
    env = dict(defaults)
    if "rod_length_m" in launch_environment:
        env["launch_rod_length_m"] = float(launch_environment["rod_length_m"])
    if "angle_from_vertical_deg" in launch_environment:
        env["launch_rod_angle_rad"] = math.radians(float(launch_environment["angle_from_vertical_deg"]))
    azimuth_range_deg = launch_environment.get("azimuth_range_deg")
    if azimuth_range_deg:
        azimuth_deg = random.uniform(float(azimuth_range_deg[0]), float(azimuth_range_deg[1]))
    else:
        azimuth_deg = launch_environment.get("azimuth_deg")
    if azimuth_deg is not None:
        env["launch_rod_direction_rad"] = math.radians(float(azimuth_deg))
    if "relative_humidity" in launch_environment:
        env["relative_humidity"] = float(launch_environment["relative_humidity"])
    return env


def run_rust_evaluator(candidates, target_apogee_m, physics_mode="openrocket", objectives=None, constraints=None, calibrations=None, execution_profile="authority-heavy", divergence_model=None):
    from rocket_ast import OPENROCKET_SIMULATION_DEFAULTS
    launch_environment = (constraints or {}).get("launch_environment")
    flattened_calibrations = {}
    for key, value in (calibrations or {}).items():
        if isinstance(value, dict):
            flattened_calibrations[key] = {
                "apogee_delta": float(value.get("avg_apogee_delta", value.get("avg_delta", 1.0))),
                "mach_delta": float(value.get("avg_mach_delta", value.get("avg_delta", 1.0))),
                "margin_delta": float(value.get("avg_margin_delta", 1.0)),
            }
        else:
            flattened_calibrations[key] = {
                "apogee_delta": float(value),
                "mach_delta": float(value),
                "margin_delta": 1.0,
            }
    payload = {
        "target_apogee_m": target_apogee_m,
        "physics_mode": physics_mode,
        "execution_profile": execution_profile,
        "divergence_model": divergence_model,
        "candidates": [
            {
                "id": c["id"],
                "ast": c["ast"],
                "signature": c.get("signature", ""),
                "environment": (
                    c.get("environment")
                    or _build_environment(
                        launch_environment, OPENROCKET_SIMULATION_DEFAULTS
                    )
                ),
            }
            for c in candidates
        ],
        "objectives": objectives or [],
        "constraints": {**(constraints or {}), "target_apogee_m": target_apogee_m},
        "phase_machs": (constraints or {}).get("phase_machs", [0.3]),
        "calibrations": flattened_calibrations,
    }
    engine_dir = Path(__file__).parent / "l2_engine"
    binary_name = "ast_eval.exe" if os.name == "nt" else "ast_eval"
    binary_path = engine_dir / "target" / "release" / binary_name
    _ensure_ast_eval_binary(engine_dir, binary_path)

    response = None
    key = str(binary_path.resolve())
    try:
        with _AST_EVAL_STREAMS_LOCK:
            stream = _AST_EVAL_STREAMS.get(key)
            if stream is None:
                probe = subprocess.run(
                    [str(binary_path), "--capabilities"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=True,
                    cwd=str(engine_dir),
                )
                if "jsonl-v1" not in json.loads(probe.stdout).get("protocols", []):
                    raise RuntimeError("ast_eval does not advertise jsonl-v1")
                stream = _AstEvalStream(binary_path, engine_dir)
                _AST_EVAL_STREAMS[key] = stream
        response = stream.request(payload)
    except Exception:
        stale = _AST_EVAL_STREAMS.pop(key, None)
        if stale is not None:
            stale.close()

    if response is None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            batch_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [str(binary_path), "--input", str(batch_path)],
                capture_output=True,
                text=True,
                check=True,
                cwd=str(engine_dir),
            )
            response = json.loads(completed.stdout)
        finally:
            try:
                batch_path.unlink()
            except OSError:
                pass

    return [
        RustEvaluationResult(
            id=item["id"],
            status=item["status"],
            score=float(item["score"]),
            apogee_m=float(item["apogee_m"]),
            mach=float(item["mach"]),
            min_static_margin=float(item["min_static_margin"]),
            margins=item.get("margins", []),
            features=item.get("features", []),
            stage_landings=item.get("stage_landings", []),
            total_prop_mass_kg=float(item.get("total_prop_mass_kg", 0.0)),
            apogee_east_m=float(item.get("apogee_east_m", 0.0)),
            apogee_north_m=float(item.get("apogee_north_m", 0.0)),
            reason=item.get("reason", ""),
        )
        for item in response.get("results", [])
    ]


def load_mission_data(path):
    return json.loads(Path(path).read_text())


def fit_divergence_model(samples, model=None):
    """Fit/update the dependency-free Rust Ridge model and return JSON state."""
    if not samples:
        return model
    engine_dir = Path(__file__).parent / "l2_engine"
    binary_name = "divergence_fit.exe" if os.name == "nt" else "divergence_fit"
    binary_path = engine_dir / "target" / "release" / binary_name
    if not binary_path.exists():
        subprocess.run(
            ["cargo", "build", "--quiet", "--release", "--bin", "divergence_fit"],
            check=True,
            cwd=str(engine_dir),
        )
    request = {"model": model, "samples": samples}
    completed = subprocess.run(
        [str(binary_path)],
        input=json.dumps(request, separators=(",", ":")),
        capture_output=True,
        text=True,
        check=True,
        cwd=str(engine_dir),
    )
    return json.loads(completed.stdout)["model"]


def select_stratified_calibration_candidates(evaluated, count, constraints):
    successful = [candidate for candidate in evaluated if candidate.status == "success"]
    if count <= 0 or not successful:
        return []
    selected = []

    def add(candidate):
        if candidate is not None and all(candidate is not item for item in selected):
            selected.append(candidate)

    add(successful[0])
    mach_limit = (constraints or {}).get("max_mach")
    if mach_limit is not None:
        add(min(successful, key=lambda item: abs(item.rust_mach - float(mach_limit))))

    seen_signatures = set()
    for candidate in successful:
        signature = extract_topological_signature(candidate.ast)
        if signature not in seen_signatures:
            add(candidate)
            seen_signatures.add(signature)
        if len(selected) >= count:
            break

    if len(selected) < count:
        for index in range(count):
            position = round(index * (len(successful) - 1) / max(count - 1, 1))
            add(successful[position])
            if len(selected) >= count:
                break
    return selected[:count]


def persist_divergence_state(config):
    if not config.divergence_model_path or config.divergence_model is None:
        return
    write_json_report(
        config.divergence_model_path,
        {
            "version": 1,
            "execution_profile": config.execution_profile,
            "model": config.divergence_model,
            "history": config.divergence_history or [],
        },
    )


def write_json_report(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def export_elites(elites, config, ckg=None, validate_openrocket=True):
    config.output_dir.mkdir(parents=True, exist_ok=True)
    fitness_def = (
        "Rust AST proxy + CKG multiplier; OpenRocket official only when or_metrics present"
        if config.evaluator == "rust"
        else "heuristic AST proxy + CKG multiplier; not OpenRocket official"
    )
    payload = {
        "generated_by": f"organic_loop v0 (pop={config.population}, gens={config.generations}, seed={config.seed})",
        "fitness_def": fitness_def,
        "evaluator": config.evaluator,
        "physics_mode": config.physics_mode,
        "execution_profile": config.execution_profile,
        "mission": str(config.mission_path) if config.mission_path else None,
        "target_apogee_m": config.target_apogee_m,
        "validate_openrocket": config.validate_openrocket,
        "elite": [],
    }

    helper = config.or_helper
    try:
        for idx, candidate in enumerate(elites):
            name = f"organic_G000_I{idx:03d}"
            ork_path = None
            if candidate.status == "success":
                compiler = ASTCompiler()
                xml = compiler.compile(candidate.ast, name=name)
                ork_path = config.output_dir / f"{name}.ork"
                write_ork_zip(ork_path, xml)
                if (
                    validate_openrocket
                    and config.validate_openrocket
                    and idx < config.validate_openrocket
                ):
                    candidate.or_metrics = validate_openrocket_ork(
                        ork_path, helper, config.phase_machs
                    )
                    if ckg is not None:
                        reason = openrocket_authority_failure_reason(
                            candidate.or_metrics, config.constraints
                        )
                        if reason:
                            ckg.record_authority(candidate.ast, 0.0, "failed", reason)
                        else:
                            ckg.record_authority(
                                candidate.ast, candidate.score, "success", "or_authority:ok"
                            )
            payload["elite"].append(
                {
                    "score": candidate.score,
                    "raw_score": candidate.raw_score,
                    "status": candidate.status,
                    "reason": candidate.reason,
                    "rust_apogee_m": candidate.rust_apogee_m,
                    "rust_mach": candidate.rust_mach,
                    "rust_min_static_margin": candidate.rust_min_static_margin,
                    "rust_margins": candidate.rust_margins or [],
                    "rust_features": candidate.rust_features or [],
                    "rust_stage_landings": candidate.rust_stage_landings or [],
                    "rust_total_prop_mass_kg": candidate.rust_total_prop_mass_kg,
                    "rust_apogee_east_m": candidate.rust_apogee_east_m,
                    "rust_apogee_north_m": candidate.rust_apogee_north_m,
                    "screen_apogee_m": candidate.screen_apogee_m,
                    "screen_mach": candidate.screen_mach,
                    "screen_features": candidate.screen_features or [],
                    "or_metrics": candidate.or_metrics,
                    "ork": str(ork_path) if ork_path else None,
                    "ast": ast_to_dicts(candidate.ast),
                }
            )
    finally:
        pass

    write_json_report(config.output_dir / "organic_elite.json", payload)


def write_ork_zip(path, xml):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("rocket.ork", xml.encode("utf-8"))


def _java_list_to_strings(items):
    if items is None:
        return []
    try:
        iterator = items.iterator()
        values = []
        while iterator.hasNext():
            values.append(str(iterator.next()))
        return values
    except Exception:
        try:
            return [str(item) for item in items]
        except Exception:
            return [str(items)]


def _extract_warning_summary(source):
    if source is None:
        return {"critical": [], "normal": [], "info": []}
    warnings = None
    for accessor in ("getWarnings", "getSimulatedWarnings", "getWarningSet"):
        if not hasattr(source, accessor):
            continue
        try:
            warnings = getattr(source, accessor)()
            break
        except Exception:
            continue
    if warnings is None:
        return {"critical": [], "normal": [], "info": []}
    return {
        "critical": _java_list_to_strings(warnings.getCriticalWarnings() if hasattr(warnings, "getCriticalWarnings") else None),
        "normal": _java_list_to_strings(warnings.getNormalWarnings() if hasattr(warnings, "getNormalWarnings") else None),
        "info": _java_list_to_strings(warnings.getInformationalWarnings() if hasattr(warnings, "getInformationalWarnings") else None),
    }


def _merge_warning_summaries(*summaries):
    merged = {"critical": [], "normal": [], "info": []}
    for summary in summaries:
        for key in merged:
            for item in summary.get(key, []):
                if item not in merged[key]:
                    merged[key].append(item)
    return merged


def run_openrocket_simulation(simulation, random_seed=16000):
    """Run OpenRocket with deterministic integrator and wind-layer states."""
    # SimulationOptions.setRandomSeed() does not reset the independently
    # constructed PinkNoiseWindModel held by each imported multilevel-wind
    # layer.  Reuse the authority runner's complete seed procedure so organic
    # promotion results replay across fresh JVMs as well as within one JVM.
    from osifog_sweep import _seed_multilevel_wind

    options = simulation.getOptions()
    options.setRandomSeed(int(random_seed))
    _seed_multilevel_wind(options, int(random_seed))
    simulation.simulate()


def _simulation_status_name(simulation):
    try:
        status = simulation.getStatus()
        return str(status.name()) if hasattr(status, "name") else str(status)
    except Exception:
        return "UNKNOWN"


def _simulation_abort_reasons(data):
    reasons = []
    if data is None or not hasattr(data, "getBranchCount"):
        return reasons
    try:
        branch_count = int(data.getBranchCount())
    except Exception:
        return reasons
    for branch_index in range(branch_count):
        try:
            events = data.getBranch(branch_index).getEvents()
        except Exception:
            continue
        for event in events:
            try:
                if event.getType().name() == "SIM_ABORT":
                    reasons.append(str(event.getData()))
            except Exception:
                continue
    return reasons


def openrocket_static_margins(doc, phase_machs=None):
    import jpype

    phase_machs = list(phase_machs or [0.3, 2.0, 3.0])
    rocket = doc.getRocket()
    config = rocket.getSelectedConfiguration()
    calculator = jpype.JClass("info.openrocket.core.aerodynamics.BarrowmanCalculator")()
    flight_conditions = jpype.JClass(
        "info.openrocket.core.aerodynamics.FlightConditions"
    )
    mass_calculator = jpype.JClass("info.openrocket.core.masscalc.MassCalculator")
    try:
        warning_set = jpype.JClass("info.openrocket.core.logging.WarningSet")
    except Exception:
        warning_set = jpype.JClass("info.openrocket.core.aerodynamics.WarningSet")

    margins = {}
    stage_count = rocket.getStageCount()
    for phase in range(stage_count):
        config.setAllStages()
        for dropped in range(phase):
            config._setStageActive(stage_count - 1 - dropped, False)
        mach = float(phase_machs[min(phase, len(phase_machs) - 1)])
        conditions = flight_conditions(config)
        conditions.setMach(mach)
        conditions.setAOA(0.0)
        cp = calculator.getCP(config, conditions, warning_set()).x
        cg = mass_calculator.calculateLaunch(config).getCenterOfMass().x
        margins[f"phase{phase}_M{mach:g}"] = (
            cp - cg
        ) / float(conditions.getRefLength())
    config.setAllStages()
    return margins


def openrocket_metrics_are_viable(metrics, constraints=None, target_apogee_m=None):
    constraints = constraints or {}
    if metrics.get("status") != "success" or int(metrics.get("critical_warning_count", 0)) != 0:
        return False
    if target_apogee_m is not None and float(metrics.get("apogee_m", 0.0)) < float(
        target_apogee_m
    ):
        return False
    max_mach = constraints.get("max_mach")
    if max_mach is not None and float(metrics.get("mach", float("inf"))) > float(
        max_mach
    ):
        return False
    # min_static_margin is NOT a numeric legality gate by default -- OSIFOG's
    # "maintain only static stability" rule (sec. 2 item 3) is a
    # control-METHOD requirement (passive aerodynamics only, no active
    # guidance), already satisfied by construction since this pipeline never
    # models active guidance. A marginal or momentarily negative-margin
    # candidate that still reaches ~3000m under the real trajectory is
    # legal; the real apogee-accuracy score term already punishes any design
    # too unstable to get there. Opt-in only, matching min_thrust_to_weight.
    min_margin = constraints.get("min_static_margin")
    if min_margin is not None and float(
        metrics.get("min_static_margin", float("-inf"))
    ) < float(min_margin):
        return False
    return True


def openrocket_authority_failure_reason(metrics, constraints=None):
    constraints = constraints or {}
    if not metrics or metrics.get("status") != "success":
        return f"or_authority:{metrics.get('reason', 'validation_failed') if metrics else 'missing_metrics'}"
    critical_count = int(metrics.get("critical_warning_count", 0))
    if critical_count:
        return f"or_authority:critical_warnings:{critical_count}"
    max_mach = constraints.get("max_mach")
    if max_mach is not None and float(metrics.get("mach", float("inf"))) > float(max_mach):
        return f"or_authority:max_mach:{metrics.get('mach')}>{max_mach}"
    # See openrocket_metrics_are_viable's matching comment: opt-in only, not
    # a real OSIFOG numeric gate.
    min_margin = constraints.get("min_static_margin")
    if min_margin is not None:
        margin = float(metrics.get("min_static_margin", float("-inf")))
        if margin < float(min_margin):
            return f"or_authority:min_static_margin:{margin}<{min_margin}"
    return ""


def validate_openrocket_ork(ork_path, helper, phase_machs=None):
    """Validate a single .ork against a JVM already started by the caller.

    JPype's JVM can only be started once per process and can never be
    restarted after shutdown (see docs/organic_loop_report.md #5), so the
    OpenRocketInstance/JVM lifecycle must be owned by the caller and shared
    across every elite in a run, not opened fresh per candidate here.
    """
    try:
        doc = helper.load_doc(str(ork_path))
        margins = openrocket_static_margins(doc, phase_machs)
        sim = doc.getSimulations().get(0)
        run_openrocket_simulation(sim)
        data = sim.getSimulatedData()
        simulation_status = _simulation_status_name(sim)
        abort_reasons = _simulation_abort_reasons(data)
        warnings = _merge_warning_summaries(
            _extract_warning_summary(doc),
            _extract_warning_summary(sim),
            _extract_warning_summary(data),
        )
        common_metrics = {
            "apogee_m": float(data.getMaxAltitude()),
            "mach": float(data.getMaxMachNumber()),
            "flight_time_s": float(data.getFlightTime()),
            "static_margins": margins,
            "min_static_margin": min(margins.values()) if margins else 0.0,
            "warnings": warnings,
            "warning_count": sum(len(items) for items in warnings.values()),
            "critical_warning_count": len(warnings["critical"]),
            "simulation_status": simulation_status,
        }
        if simulation_status == "ABORTED" or abort_reasons:
            return {
                "status": "failed",
                "reason": "openrocket_simulation_aborted",
                "abort_reasons": abort_reasons,
                **common_metrics,
            }
        return {
            "status": "success",
            **common_metrics,
        }
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}



def extract_topological_signature(ast_nodes):
    parts = []
    for node in ast_nodes:
        if node.node_type == "NOSE_CONE":
            parts.append(node.params.get("shape", "ogive"))
        elif node.node_type == "FIN_SET":
            parts.append(f"{node.params.get('count', 4)}fins")
        elif node.node_type == "MOTOR_MOUNT":
            parts.append(node.params.get("motor_designation", "motor"))
    return "_".join(parts)


def _nearest_body_radius(ast_nodes, insert_at):
    """Best-effort body radius for the stage a structural mutation is about
    to insert into: the last BODY_TUBE seen before `insert_at`. Used to keep
    mutation-inserted fins/flaps proportional to their body, same rationale
    as create_random_ast's body_radius-relative sizing."""
    radius = None
    for index, node in enumerate(ast_nodes):
        if index >= insert_at:
            break
        if node.node_type == "BODY_TUBE":
            radius = node.params.get("radius")
    return radius


def _structural_mutation(ast_nodes, motor_pool=None, retro_motor_pool=None, repair_space=None, allow_parachute=True):
    choices = ["PAYLOAD", "PARACHUTE", "FIN_SET", "FORWARD_FLAP", "RETRO_MOTOR", "OCTAWEB_CONVERT"]
    node_type = random.choice(choices)
    insert_at = max(1, len(ast_nodes) - 1)
    body_radius = _nearest_body_radius(ast_nodes, insert_at)

    if node_type == "PAYLOAD":
        ast_nodes.insert(insert_at, ASTNode("PAYLOAD", mass=random.uniform(0.05, 2.0)))
    elif node_type == "PARACHUTE" and allow_parachute and not any(node.node_type == "PARACHUTE" for node in ast_nodes):
        ast_nodes.insert(insert_at, ASTNode("PARACHUTE", deploy="apogee", diameter=random.uniform(0.25, 0.9)))
    elif node_type == "FIN_SET":
        # Every stage already gets exactly one main (non-forward_flap) fin
        # set from create_random_ast/sanitize_ast_for_openrocket -- there is
        # no mechanism anywhere that ever REMOVES one. Unlike this
        # function's PARACHUTE/FORWARD_FLAP/RETRO_MOTOR branches (all
        # explicitly guarded with `not any(...)`), this branch had no such
        # guard and unconditionally inserted ANOTHER main fin set on every
        # fire -- confirmed as a real, live-campaign bug via a user
        # screenshot showing 3 overlapping "Evolved Fins" freeformfinsets on
        # one stage, all stacked at the same position (compiler always
        # writes position type="bottom">0.0), rendering as self-intersecting
        # geometry. Scope the guard to THIS stage only (not the whole
        # multi-stage ast_nodes), matching the OCTAWEB_CONVERT branch's own
        # stage-boundary convention below, since each stage legitimately
        # needs its own fin set.
        stage_start = 0
        for index, node in enumerate(ast_nodes[: insert_at + 1]):
            if node.node_type == "STAGE":
                stage_start = index
        stage_end = len(ast_nodes)
        for index in range(stage_start + 1, len(ast_nodes)):
            if ast_nodes[index].node_type in ("STAGE", "CLOSE_BODY"):
                stage_end = index
                break
        already_has_main_fins = any(
            node.node_type == "FIN_SET" and node.params.get("role") != "forward_flap"
            for node in ast_nodes[stage_start:stage_end]
        )
        if not already_has_main_fins:
            if body_radius:
                root = body_radius * random.uniform(2.5, 5.0)
                height = body_radius * random.uniform(2.0, 4.5)
            else:
                root = random.uniform(0.06, 0.18)
                height = random.uniform(0.04, 0.12)
            ast_nodes.insert(
                insert_at,
                ASTNode(
                    "FIN_SET",
                    count=random.choice([3, 4, 6]),
                    sweep=random.uniform(15, 45),
                    root=root,
                    height=height,
                    material=random.choice(list(MATERIALS.keys())),
                ),
            )
    elif node_type == "FORWARD_FLAP" and not any(
        node.node_type == "FIN_SET" and node.params.get("role") == "forward_flap" for node in ast_nodes
    ):
        ast_nodes.insert(insert_at, forward_flap_node(repair_space, body_radius=body_radius))
    elif (
        node_type == "RETRO_MOTOR"
        and retro_motor_pool
        and not any(
            node.node_type == "MOTOR_MOUNT" and node.params.get("role") == "retro" for node in ast_nodes
        )
    ):
        main_mount = next(
            (n for n in ast_nodes if n.node_type == "MOTOR_MOUNT" and n.params.get("role") != "retro"),
            None,
        )
        retro_idx = _select_motor_index(retro_motor_pool, default_floor=0)
        main_radius_m = (
            MOTOR_DATABASE[int(main_mount.params.get("motor_index", 0))][2] / 2.0
            if main_mount is not None
            else 0.02
        )
        retro_radius_m = MOTOR_DATABASE[retro_idx][2] / 2.0
        ast_nodes.insert(insert_at, ASTNode(
            "MOTOR_MOUNT",
            role="retro",
            motor_index=retro_idx,
            motor_designation=MOTOR_DATABASE[retro_idx][1],
            ignition="burnout",
            ignition_delay=random.uniform(0.0, 30.0),
            radial_offset_m=main_radius_m + retro_radius_m + 0.004,
            radial_angle_deg=180.0,
        ))
    elif node_type == "OCTAWEB_CONVERT" and retro_motor_pool:
        # Rebuild an existing stage's single-motor mount(s) into the
        # 3-main+1-retro octaweb cluster in place, mirroring the fresh-
        # generation branch in create_random_ast (octaweb_motor_mounts +
        # octaweb_ballast_rods) instead of duplicating its geometry rules.
        # Without this the "OCTAWEB_CONVERT" choice above was a dead entry:
        # it matched no elif branch, so 1/6 of structural mutations were a
        # silent no-op.
        stage_start = 0
        for index, node in enumerate(ast_nodes[: insert_at + 1]):
            if node.node_type == "STAGE":
                stage_start = index
        stage_end = len(ast_nodes)
        for index in range(stage_start + 1, len(ast_nodes)):
            # CLOSE_BODY is a whole-AST sentinel appended after the last
            # stage's content (sanitize_ast_for_openrocket, mutate_ast) --
            # stop before it too, or the bottom stage's converted mounts
            # would get inserted after it instead of before.
            if ast_nodes[index].node_type in ("STAGE", "CLOSE_BODY"):
                stage_end = index
                break
        stage_nodes = ast_nodes[stage_start:stage_end]
        stage_body_tube = next((n for n in stage_nodes if n.node_type == "BODY_TUBE"), None)
        existing_mounts = [n for n in stage_nodes if n.node_type == "MOTOR_MOUNT"]
        already_octaweb = any(
            n.params.get("multiplicity", 1) == 3 and n.params.get("cluster_configuration") == "3-ring"
            for n in existing_mounts
        )
        is_bottom_stage = stage_end == len(ast_nodes)

        if stage_body_tube is not None and existing_mounts and not already_octaweb:
            new_body_radius = random.uniform(*OCTAWEB_BODY_RADIUS_RANGE_M)
            octaweb_mounts = octaweb_motor_mounts(motor_pool, retro_motor_pool, new_body_radius, is_bottom_stage)
            if octaweb_mounts:
                main_mount, retro_mount = octaweb_mounts
                # Widening the body radius here (rather than leaving the
                # stage narrow) reuses sanitize_ast_for_openrocket's own
                # diameter-continuity pass to reconcile sibling stages and
                # proportionally rescale this stage's fins -- same fix, same
                # code path as the fresh-generation octaweb case.
                # Tighten to what the cage actually needs (+ margin) rather
                # than keeping the independently-random new_body_radius
                # draw -- same rationale and formula as create_random_ast's
                # own post-loop tightening step (see its comment): a small
                # motor pair landing inside a body drawn near the range's
                # ceiling leaves a large, structurally pointless gap.
                required_radius = (
                    main_mount.params["radial_offset_m"] + main_mount.params["main_outer_radius_m"]
                ) * 1.2 + 0.004
                required_radius = max(required_radius, OCTAWEB_BODY_RADIUS_RANGE_M[0])
                stage_body_tube.params["radius"] = min(new_body_radius, required_radius)
                for old_mount in existing_mounts:
                    ast_nodes.remove(old_mount)
                mount_insert_at = stage_end - len(existing_mounts)
                ast_nodes.insert(mount_insert_at, main_mount)
                ast_nodes.insert(mount_insert_at + 1, retro_mount)

                ballast_mass_choices = (repair_space or {}).get("nose_ballast_mass_kg")
                if ballast_mass_choices:
                    ballast_mass_kg = random.choice(ballast_mass_choices)
                    if ballast_mass_kg > 0.0:
                        main_motor_len = MOTOR_DATABASE[main_mount.params["motor_index"]][3] + 0.02
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
                        if ballast is not None:
                            ast_nodes.insert(mount_insert_at + 2, ballast)


def insert_precision_payload(ast_nodes, mass_kg):
    """Add ballast to the forward stage while preserving its component graph."""
    ast = [copy.deepcopy(n) for n in ast_nodes]
    insert_idx = None
    seen_first_stage = False
    forward_payload_idx = None
    for idx, node in enumerate(ast):
        if node.node_type == "STAGE" and not seen_first_stage:
            seen_first_stage = True
            continue
        if seen_first_stage and node.node_type == "PAYLOAD" and forward_payload_idx is None:
            forward_payload_idx = idx
        if seen_first_stage and node.node_type == "CLOSE_BODY":
            insert_idx = idx
            break
        if seen_first_stage and idx > 0 and node.node_type == "STAGE":
            insert_idx = idx
            break
    if forward_payload_idx is not None:
        existing_mass = float(ast[forward_payload_idx].params.get("mass", 0.0))
        ast[forward_payload_idx].params["mass"] = existing_mass + float(mass_kg)
        return ast
    if insert_idx is None:
        insert_idx = max(1, len(ast) - 1)
    ast.insert(insert_idx, ASTNode("PAYLOAD", mass=float(mass_kg)))
    return ast


def polish_elite(
    best_ast,
    target_apogee_m,
    or_helper,
    output_dir,
    constraints=None,
    tolerance_m=1e-6,
    phase_machs=None,
):
    """
    Polishes the elite candidate by numerically solving for the exact payload mass
    required to hit the target_apogee_m in OpenRocket down to high precision.
    """
    import scipy.optimize
    
    base_ast = [copy.deepcopy(n) for n in best_ast]
    
    def simulate_mass(mass_kg):
        ast = insert_precision_payload(base_ast, mass_kg)
        
        compiler = ASTCompiler()
        xml = compiler.compile(ast, name="Precision Polisher")
        ork_path = output_dir / "polish_temp.ork"
        write_ork_zip(ork_path, xml)
        
        doc = or_helper.load_doc(str(ork_path))
        if doc.getSimulations().size() == 0:
            return 100000.0
            
        sim = doc.getSimulations().get(0)
        run_openrocket_simulation(sim)
        data = sim.getSimulatedData()
        apogee = float(data.getMaxAltitude())
        mach = float(data.getMaxMachNumber())
        flight_time = float(data.getFlightTime())
        return {"apogee_m": apogee, "mach": mach, "flight_time_s": flight_time, "ast": ast}

    def evaluate_mass(mass_kg):
        metrics = simulate_mass(mass_kg)
        apogee = metrics["apogee_m"]
        return abs(apogee - target_apogee_m)

    def signed_error(mass_kg):
        return simulate_mass(mass_kg)["apogee_m"] - target_apogee_m

    print(f"\n[Polisher] Initiating high-precision minimize_scalar solver for target {target_apogee_m}m...")
    try:
        baseline = simulate_mass(0.0)
        if baseline["apogee_m"] < target_apogee_m:
            print(
                "[Polisher] Refusing additive ballast polish: baseline OpenRocket apogee "
                f"{baseline['apogee_m']:.6f}m is below target {target_apogee_m:.6f}m"
            )
            return None

        upper_mass = 20.0
        upper = simulate_mass(upper_mass)
        while upper["apogee_m"] > target_apogee_m and upper_mass < 200.0:
            upper_mass *= 2.0
            upper = simulate_mass(upper_mass)
        if upper["apogee_m"] > target_apogee_m:
            print(
                "[Polisher] Refusing polish: even "
                f"{upper_mass:.3f}kg added ballast leaves OpenRocket apogee "
                f"{upper['apogee_m']:.6f}m above target {target_apogee_m:.6f}m"
            )
            return None

        def find_best_bracket(lower_mass, higher_mass, sample_count):
            sample_masses = [
                lower_mass + (higher_mass - lower_mass) * i / sample_count
                for i in range(sample_count + 1)
            ]
            samples = [
                (mass, simulate_mass(mass)["apogee_m"] - target_apogee_m)
                for mass in sample_masses
            ]
            brackets = []
            for (left_m, left_err), (right_m, right_err) in zip(samples, samples[1:]):
                if left_err == 0.0:
                    brackets.append((left_m, left_m, 0.0))
                elif left_err * right_err <= 0.0:
                    brackets.append((left_m, right_m, min(abs(left_err), abs(right_err))))
            closest = min(samples, key=lambda item: abs(item[1]))
            if not brackets:
                return None, closest
            return min(brackets, key=lambda item: item[2]), closest

        bracket, closest = find_best_bracket(0.0, upper_mass, 80)
        if bracket is None:
            closest_mass, closest_err = closest
            print(
                "[Polisher] Rejected result: no local OpenRocket altitude bracket found; "
                f"closest sampled mass {closest_mass:.6f}kg was {closest_err:+.6f}m from target"
            )
            return None

        lower_bound, upper_bound, _ = bracket
        for _ in range(3):
            bracket, _closest = find_best_bracket(lower_bound, upper_bound, 20)
            if bracket is None:
                break
            lower_bound, upper_bound, _ = bracket

        try:
            if lower_bound == upper_bound:
                optimal_mass = lower_bound
            else:
                optimal_mass = scipy.optimize.brentq(signed_error, lower_bound, upper_bound, xtol=1e-12, rtol=1e-14, maxiter=200)
        except ValueError:
            res = scipy.optimize.minimize_scalar(evaluate_mass, bounds=(lower_bound, upper_bound), method='bounded', options={'xatol': 1e-12, 'maxiter': 200})
            optimal_mass = res.x
        metrics = simulate_mass(optimal_mass)
        authority = validate_openrocket_ork(
            output_dir / "polish_temp.ork", or_helper, phase_machs
        )
        if authority.get("status") != "success":
            print(f"[Polisher] Rejected result: {authority.get('reason', 'validation failed')}")
            return None
        if abs(authority["apogee_m"] - target_apogee_m) > tolerance_m:
            print(
                "[Polisher] Rejected result: OpenRocket apogee "
                f"{authority['apogee_m']:.6f}m is outside +/-{tolerance_m:.6f}m"
            )
            return None
        if not openrocket_metrics_are_viable(authority, constraints):
            print(
                "[Polisher] Rejected result: OpenRocket authority constraints failed "
                f"(Mach={authority['mach']:.6f}, margin="
                f"{authority['min_static_margin']:.6f}, warnings="
                f"{authority['warning_count']})"
            )
            return None

        print(f"[Polisher] Converged! Optimal Ballast Mass: {optimal_mass:.8f} kg")
        
        final_ast = metrics["ast"]
        
        compiler = ASTCompiler()
        xml = compiler.compile(final_ast, name="L2 Precision Elite")
        final_ork = output_dir / "precision_polished_elite.ork"
        write_ork_zip(final_ork, xml)
        print(
            "[Polisher] Exact target hit. "
            f"OpenRocket apogee={authority['apogee_m']:.6f}m "
            f"Mach={authority['mach']:.6f} margin={authority['min_static_margin']:.6f}. "
            f"Saved tuned rocket to: {final_ork}"
        )
        return final_ast
    except Exception as e:
        print(f"[Polisher] Could not converge: {e}")
        return None


def polish_ranked_elites(
    elites,
    target_apogee_m,
    or_helper,
    output_dir,
    constraints=None,
    tolerance_m=1e-6,
    phase_machs=None,
):
    for index, candidate in enumerate(elites):
        if candidate.status != "success":
            continue
        candidate_path = output_dir / f"polish_candidate_{index:03d}.ork"
        write_ork_zip(
            candidate_path,
            ASTCompiler().compile(candidate.ast, name=f"Polish Candidate {index}"),
        )
        metrics = validate_openrocket_ork(candidate_path, or_helper, phase_machs)
        candidate.or_metrics = metrics
        if not openrocket_metrics_are_viable(
            metrics, constraints, target_apogee_m=target_apogee_m
        ):
            continue
        polished = polish_elite(
            candidate.ast,
            target_apogee_m,
            or_helper,
            output_dir,
            constraints,
            tolerance_m,
            phase_machs,
        )
        if polished is not None:
            return polished, index
    return None, None


def parse_args():
    parser = argparse.ArgumentParser(description="Run the L2 organic AST evolution loop.")
    parser.add_argument("--evaluator", choices=["rust", "heuristic"], default="rust")
    parser.add_argument("--physics", choices=["openrocket", "hyperreal"], default="openrocket")
    parser.add_argument(
        "--execution-profile",
        choices=["super-speed", "balanced", "authority-heavy"],
        default="authority-heavy",
    )
    parser.add_argument("--population", type=int, default=32)
    parser.add_argument("--elite-count", type=int, default=6)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mission", type=Path)
    parser.add_argument("--target-apogee", type=float)
    parser.add_argument("--out", type=Path, default=Path("designs/organic"))
    parser.add_argument("--ckg", type=Path, default=Path(".planning/organic_ckg.json"))
    parser.add_argument("--validate-openrocket", type=int, default=0)
    parser.add_argument(
        "--calibrate-every",
        type=int,
        default=None,
        help="OpenRocket cadence; omitted uses the execution profile, 0 disables",
    )
    parser.add_argument("--calibration-sample-size", type=int, default=0)
    parser.add_argument("--divergence-model", type=Path)
    parser.add_argument("--seed-from", type=Path)
    parser.add_argument("--polish", action="store_true", help="Run OpenRocket precision polisher on the elite candidate")
    return parser.parse_args()


def main():
    args = parse_args()
    
    or_instance = None
    helper = None
    resolved_calibrate_every = (
        MODE_PROFILES[args.execution_profile]["or_calibrate_every"]
        if args.calibrate_every is None
        else args.calibrate_every
    )
    calibration_is_due = (
        resolved_calibrate_every > 0
        and args.generations >= resolved_calibrate_every
    )
    if args.validate_openrocket or calibration_is_due or args.polish:
        import orhelper
        from orhelper import OpenRocketInstance
        or_instance = OpenRocketInstance("lib/OpenRocket-24.12.jar").__enter__()
        helper = orhelper.Helper(or_instance)
        
    try:
        target_apogee_m = args.target_apogee or 15000.0
        objectives = [{"metric": "apogee", "kind": "target", "value": target_apogee_m}]
        # No min_static_margin at all -- confirmed via a direct OSIFOG
        # ruling email (2026-07-24) plus both official rules PDFs: neither
        # specifies any minimum static margin value. "Manter APENAS
        # estabilidade estatica" (sec. 2 item 3) is a control-METHOD
        # requirement (passive aerodynamics only, no active guidance) read
        # against item 2's active-correction ban, not a numeric caliber
        # floor -- see enforce_hard_constraints in l2_engine/src/ast.rs for
        # the full reasoning. Dead in practice for the live campaign
        # (--mission is always passed, overriding this below), kept only as
        # the fallback for a mission-less invocation.
        constraints = {}
        phase_machs = [0.3, 2.0, 3.0]
        
        if args.mission:
            payload = load_mission_data(args.mission)
            objectives = payload.get("objectives", objectives)
            constraints = payload.get("constraints", constraints)
            phase_machs = payload.get("stability", {}).get(
                "phase_machs", phase_machs
            )
            constraints = {**constraints, "phase_machs": phase_machs}
            target_apogee_m = load_mission_target_apogee(args.mission)

            for key in ("target_apogee_m", "target_apogee"):
                if key in payload:
                    target_apogee_m = float(payload[key])
                    
            if "scoring" in payload:
                constraints["scoring"] = payload["scoring"]
            if "wind" in payload and payload["wind"].get("source") == "csv":
                constraints["wind_csv_path"] = payload["wind"]["path"]
            if "launch" in payload:
                constraints["launch_environment"] = payload["launch"]
            if "atmosphere" in payload and "humidity" in payload["atmosphere"]:
                constraints.setdefault("launch_environment", {})["relative_humidity"] = payload["atmosphere"]["humidity"]

        model_path = args.divergence_model
        model_payload = None
        model_history = []
        if model_path and model_path.exists():
            model_record = json.loads(model_path.read_text(encoding="utf-8"))
            if model_record.get("execution_profile") not in (None, args.execution_profile):
                raise ValueError("divergence model execution profile mismatch")
            model_payload = model_record.get("model")
            model_history = model_record.get("history", [])

        config = OrganicLoopConfig(
            population=args.population,
            elite_count=args.elite_count,
            generations=args.generations,
            seed=args.seed,
            target_apogee_m=target_apogee_m,
            mission_path=args.mission,
            output_dir=args.out,
            ckg_path=args.ckg,
            evaluator=args.evaluator,
            physics_mode=args.physics,
            execution_profile=args.execution_profile,
            divergence_model=model_payload,
            divergence_model_path=model_path,
            divergence_history=model_history,
            calibration_sample_size=args.calibration_sample_size,
            validate_openrocket=args.validate_openrocket,
            calibrate_every=resolved_calibrate_every,
            seed_from=args.seed_from,
            polish=args.polish,
            or_helper=helper,
            objectives=objectives,
            constraints=constraints,
            phase_machs=phase_machs,
        )
        result = run_generation(config)
        best = result.elites[0]
        print(f"best score={best.score:.3f} status={best.status} reason={best.reason}")
        print(f"wrote {len(result.elites)} elites to {config.output_dir}")
        
        if config.polish and helper:
            polished, polish_index = polish_ranked_elites(
                result.elites,
                target_apogee_m,
                helper,
                config.output_dir,
                config.constraints,
                phase_machs=config.phase_machs,
            )
            if polished is None:
                print("[Polisher] No ranked elite satisfied every OpenRocket authority gate.")
            else:
                print(f"[Polisher] Accepted ranked elite index {polish_index}.")
            
    finally:
        if or_instance is not None:
            or_instance.__exit__(None, None, None)


if __name__ == "__main__":
    main()
