# Rust-Native Scoring Tables, OSIFOG Topology, and In-Process GA

Status: approved, implementation in progress
Owner: L2 OSIFOG mission

## Problem

`l2_engine/src/ast.rs` has a hardcoded `score_osifog_2026()` function dispatched by a
string tag (`constraints.scoring.formula == "osifog_2026"`). It reproduces the
official OSIFOG formula correctly, but every future competition or scoring
tweak requires a new Rust function and a new if-branch — not the modular,
long-term-maintainable system the engine needs. Separately, `rocket_ast.py`'s
AST node set (`STAGE`, `NOSE_CONE`, `BODY_TUBE`, `MOTOR_MOUNT`, `FIN_SET`,
`PARACHUTE`, `PAYLOAD`) cannot express a main+retro motor pair, ballast mass,
or a retro ignition delay at all — a bigger blocker than the scoring formula
for shifting generation fully to Rust. The GA loop itself (`organic_loop.py`)
also pays a Python↔Rust subprocess round-trip per generation, capping
throughput below what "millions of configs in seconds" requires.

## Design

### 1. Modular scoring-table system

Mission JSON's `scoring` block becomes a generic weighted-term table, no
hardcoded formula names:

```json
{
  "base_score": 900000.0,
  "terms": [
    {"name": "apogee_altitude", "metrics": ["apogee_m"], "reference": [3000.0], "power": 2, "coefficient": -3000.0, "aggregate": "scalar"},
    {"name": "apogee_horizontal", "metrics": ["apogee_east_m", "apogee_north_m"], "reference": [0,0], "power": 2, "coefficient": -16.0, "aggregate": "scalar"},
    {"name": "touchdown_position", "metrics": ["stage_landing_east_m", "stage_landing_north_m"], "reference": [0,0], "power": 2, "coefficient": -2.0, "aggregate": "mean_over_stages"},
    {"name": "touchdown_speed", "metrics": ["stage_landing_total_speed_ms"], "reference": [0], "power": 2, "coefficient": -500.0, "aggregate": "mean_over_stages"},
    {"name": "propellant_used", "metrics": ["total_prop_mass_kg"], "reference": [0], "power": 1, "coefficient": -7500.0, "aggregate": "scalar"}
  ]
}
```

`score = base_score + Σ term.coefficient × Σ_i (metric_i - reference_i)^power`,
metrics aggregated across stages per `aggregate` (`scalar`, `mean_over_stages`,
`sum_over_stages`, `max_over_stages`) before the per-term sum. Negative
coefficients are penalties, positive are bonuses — sign lives in the data, not
in a "kind" enum branch.

Verified equivalent to `score_osifog_2026` (`ast.rs:714-742`) term-for-term
against the handoff.md formula. `score_osifog_2026` and the `formula` string
dispatch are deleted once the generic evaluator passes the equivalence test.

Implementation:
- `MetricResolver`: extends the existing `get_metric` match arm in
  `score_summary` to resolve both scalar metrics (`apogee_m`,
  `total_prop_mass_kg`, ...) and per-stage metrics (`stage_landing_east_m`,
  ...) to `Vec<f64>` before aggregation.
- `ScoringTable { base_score: f64, terms: Vec<ScoringTerm> }`,
  `ScoringTerm { name: String, metrics: Vec<String>, reference: Vec<f64>, power: f64, coefficient: f64, aggregate: Aggregate }`.
- Old `constraints.scoring` (bare coefficients keyed to `osifog_2026`) is not
  kept as a back-compat shim — mission JSON is updated to the new schema
  directly since there are no external consumers of the old shape.

### 2. AST topology extensions (revised 2026-07-19 after reading the actual code)

Three of the four items turned out to be far smaller than first scoped —
existing infrastructure already covers them:

- **Ballast** (small, do first): new `BALLAST` AST node type reusing the
  existing `point_masses: Vec<PointMassGeometry>` field already on
  `PendingStage`/`StageGeometry` (`geometry.rs:19,70-75`). Python emits it;
  Rust adds one `"BALLAST" => { ... }` match arm in `ast_to_geometry`. Add
  generic density-range validation (170-11,340 kg/m³) at the point where any
  component's material density is set, not a ballast-specific check.
- **Motor-class / pool constraints** (trivial, Python-only): motor selection
  already happens entirely in `rocket_ast.py`'s candidate generator against
  `MOTOR_DATABASE` — a mission-JSON `motor_pool: { allowed_designations: [...] }`
  (or diameter/impulse bounds) is just a filter applied there. **No Rust
  change at all.**
- **Launch azimuth as a genome variable** (trivial, Python-only):
  `OrkSimulationEnvironment.launch_rod_direction_rad` (`mission_adapter.rs:36`)
  already exists and is already consumed by the sim (`apply_openrocket_environment`).
  It's fixed today only because `rocket_ast.py`/the mission JSON pins one
  value (`launch.azimuth_deg: 288.0`) instead of sampling a range per
  candidate. **No Rust change at all** — just vary it per-candidate in the
  Python generator within a mission-JSON-configured range.
- **Multi-motor stages / retro motor** (the one real physics-core change):
  `MotorMountGeometry` already carries its own `ignition_event`/`ignition_delay`
  (`geometry.rs:167-168`), but the physics layer bakes in exactly one motor
  per stage: `Stage.thrust_curve: Vec<(f64,f64)>`, `propellant_mass: f64`,
  and `ignition_delay: f64` are all scalar (`sim_core/vehicle/stage.rs:13,26,33`),
  and the integrators that read them (`sixdof.rs`, `axial.rs`, `event.rs`,
  `sim/runner.rs`) all assume a single curve/single depletion. A retro motor
  needs `Stage` to carry `Vec<MotorBurn>` (`{ thrust_curve, propellant_mass,
  ignition_delay }` per motor), with total instantaneous thrust = sum of
  thrust from every motor whose `[ignition_delay, ignition_delay+burn_duration]`
  window contains `t - stage_activated_at`, and total instantaneous mass =
  `dry_mass + Σ remaining_propellant_i(t)`. This is the highest-risk change
  in the whole plan because it touches the exact integrator code the running
  OSIFOG campaign's scores depend on. Sequencing: implement behind the
  existing single-motor call sites first (a `Vec` of length 1 must reproduce
  today's behavior bit-for-bit — regression-tested against the existing
  sixdof/axial/event/runner test suite), then add the second-motor case with
  its own hand-computed-fixture tests, before wiring a second `MOTOR_MOUNT`
  AST node into `ast_to_geometry`.

### 3. Rust-native GA loop

New `l2_engine/src/bin/ast_ga.rs`: takes one `GenomeSpec` (stage count range,
per-stage motor pools, ballast pool, retro pool, azimuth range, dimensional/
material ranges — the data-driven mirror of what `rocket_ast.py::create_random_ast`
currently hardcodes) plus a `ScoringTable`, and runs generate → score → select
→ mutate for N generations entirely in-process, emitting only the final elite
set as JSON. `organic_loop.py` shrinks to: load mission JSON, invoke the GA
binary once, take the elite JSON, and reuse the existing `ASTCompiler`
(unchanged) to serialize `.ork` files for OpenRocket validation — Python
keeps that role permanently since OR interop is JPype-only.

## Execution order

1. **DONE** — Scoring-table engine (`ast.rs`): `ScoringTable`/`ScoringTerm`
   evaluator, `score_osifog_2026` deleted, 4 new tests, mission JSON +
   `docs/engine_integration_spec.md` migrated to the new schema. Also fixed
   an unrelated pre-existing Rust test-compile break (`wind_profile` field)
   and added `pytest.ini` so the Python suite collects. Baseline: 120/120
   Rust tests, 44/44 Python tests.
2. AST topology extensions — in risk order:
   2a. **DONE** — `BALLAST` AST node: `geometry.rs`/`ast.rs` match arm reusing
       `point_masses`, mass/position validation, generic
       `material_density_checked` choke point (also retrofitted onto
       `NOSE_CONE`/`BODY_TUBE`/`FIN_SET`, which previously called the
       unchecked `material_density`). Python: `_sanitize_ballast` +
       `ASTCompiler` `<masscomponent>` emission, `steel`/`lead` added to
       `rocket_forge.MATERIALS` for parity with the Rust table. 9 new tests
       (5 Rust, 2 Python compiler, plus the density-range unit tests).
   2b. **DONE** — Motor-pool constraints: `rocket_ast.motor_pool_indices()` +
       `_select_motor_index()`, threaded through `create_random_ast(...,
       motor_pool=...)` and `ASTNode.mutate(..., motor_pool=...)` /
       `mutate_ast(..., motor_pool=...)`, sourced from mission JSON
       `motor_pool.allowed_designations` in `organic_loop.run_generation`.
       Fails loudly (`ValueError`) on a pool matching zero motors rather than
       silently falling back to the full database. Zero Rust changes, as
       predicted. 5 new tests.
   2c. **DONE, with a caveat** — while wiring this, found `mission.launch`
       (rod length, azimuth, angle-from-vertical) and
       `atmosphere.humidity` were **never read** by the Rust batch path —
       every candidate silently used `OPENROCKET_SIMULATION_DEFAULTS`
       (2m rod, 90° azimuth) regardless of what the mission JSON declared
       (6m rod, 288° azimuth for OSIFOG). Fixed via `organic_loop._build_environment()`,
       fed from a new `constraints["launch_environment"]` populated in `main()`.
       Added `azimuth_range_deg: [min, max]` — when present, each candidate's
       azimuth is freshly sampled from that range at evaluation time. This is
       azimuth-as-search-variable via **per-evaluation resampling, not a
       heritable genome parameter**: a surviving elite's good azimuth is not
       preserved across generations the way its AST is, because `population`
       is a plain `List[List[AstNode]]` with no slot for non-AST per-candidate
       state. True heredity needs a richer candidate wrapper — deferred to
       Phase 3, where the GA loop's data model is being redesigned anyway.
       4 new tests.
   2d. **Not started** — multi-motor stages / retro motor (`Stage` →
       `Vec<MotorBurn>`, integrator changes in `sixdof.rs`/`axial.rs`/
       `event.rs`/`sim/runner.rs`) — highest risk, the one piece that touches
       the physics core the running campaign's scores depend on. Single-motor
       case must regression-test bit-for-bit against the existing 125-test
       baseline before the second-motor case is added.
3. Rust-native GA loop (`ast_ga.rs`) — depends on (1) and (2); its candidate
   representation should also resolve 2c's heredity gap (azimuth, and any
   other non-AST per-candidate parameters) as a first-class genome field.

## Baseline after this round

125/125 Rust tests, 54/54 Python tests (`cargo test --lib` in `l2_engine/`,
`pytest tests/` at repo root — `pytest.ini` added so root-level modules
resolve during collection). `missions/osifog_l3_precision.json` updated to
the new scoring/motor_pool/azimuth_range_deg schema; `docs/engine_integration_spec.md`
updated to match. Nothing in this round has been committed to git — the
working tree already carried ~1500 uncommitted lines in these same files
before this session started.

Each phase lands as its own commit with its own tests so the existing
pipeline (`organic_loop.py` → `ast_eval` → OpenRocket validation) stays
working end-to-end after every step.

## Testing

- Rust unit tests: `ScoringTable` evaluator reproduces `score_osifog_2026`
  bit-for-bit on the same `FlightSummary` fixtures; multi-motor ignition
  timing; ballast mass/CG contribution; density-range rejection.
- Python: `tests/test_organic_evolution.py` gets new coverage for the updated
  mission JSON schema (`scoring.terms`, `motor_pool`, ballast/retro AST
  emission) replacing the old `osifog_2026` formula-string tests.
