# Repository and Active-Path Map

## Active Entry Points

| Entry Point | Role | Authority | Active Tests |
|------------|------|-----------|-------------|
| `osifog_precision.py` | OSIFOG mission adapter + submission pipeline | OPENROCKET_AUTHORITY | test_osifog_precision.py |
| `osifog_engine_search.py` | Unattended two-authority optimizer | RUST_PROXY + OPENROCKET_AUTHORITY | test_osifog_engine_search.py |
| `osifog_sweep.py` | ORK generation, OR runner, scoring, hard gates | OPENROCKET_AUTHORITY | test_osifog_sweep.py |
| `physical_geometry.py` | Cylinder collision + attachment validation | RUST_PROXY | test_physical_geometry.py |
| `organic_loop.py` | GA population evaluation, Rust bridge, CKG | RUST_PROXY | test_organic_evolution.py |
| `mission_evolution.py` | Phase-by-phase evolution primitives | NONE (pure logic) | test_mission_evolution.py |
| `rocket_ast.py` | AST schema, mutation, sanitization | RUST_PROXY | (via organic_loop tests) |
| `rocket_forge.py` | MOTOR_DATABASE, MATERIALS constants | REFERENCE | (imported by osifog_sweep) |
| `ckg_memory.py` | Continuous Knowledge Graph | RUST_PROXY | (via organic_loop tests) |
| `l2_engine/src/bin/ast_eval.rs` | Rust JSONL batch evaluator | RUST_PROXY | 137 lib + 17 bridge tests |
| `l2_engine/src/bin/divergence_fit.rs` | Ridge regression model fitter | RUST_PROXY | (via ast_bridge tests) |

## Legacy/Retired Entry Points

| Entry Point | Status | Reason |
|------------|--------|--------|
| `rocket_forge.py::RocketArchitect` | Legacy | Replaced by osifog_sweep.py |
| `rocket_ast.py::ASTCompiler` | Legacy | Used only by organic_loop for non-OSIFOG missions |
| `l2_engine/src/bin/evolve.rs` | Legacy | Replaced by ast_eval.rs |
| `l2_engine/src/bin/optimize.rs` | Legacy | Not called by production |
| `l2_hyper/` | Retired | Fixed-template system |
| `legacy/` | Retired | Historical scripts |
| `generate_winner.py`, `generate_final.py` | Retired | One-off scripts |
| `debug_*.py`, `test_antitumble.py` | Debug | Diagnostic only |

## Data Flow: Candidate → Score

```
parameters (dict)
  → validate_candidate_geometry() [physical_geometry.py]
  → generate_ork() [osifog_sweep.py]
  → run_sim() [osifog_sweep.py → OpenRocket 24.12 via JPype]
  → extract metrics (apogee, Mach, stability, landings, events)
  → validate_hard_constraints() [osifog_sweep.py]
  → score_official() [osifog_sweep.py]
  → save_simulated_ork() → inspect_saved_submission() [osifog_precision.py]
```

## Data Flow: Rust Proxy

```
AST nodes (list[ASTNode])
  → parameters_to_ast() [osifog_engine_search.py]
  → serialize to JSON
  → ast_eval.rs (JSONL serve or batch)
    → ast_to_geometry() [ast.rs]
    → enrich_ast_motor_mounts_multi() [ast.rs]
    → prepare_ascent_screen() [ast.rs] (retro motors → inert mass)
    → static_margins_with_mode_at_machs() [builder.rs]
    → build_mission_with_motor_clusters() [mission_adapter.rs]
    → simulate_summary_with_mode() [runner.rs]
    → score_summary() [ast.rs]
  → FlightSummary JSON back to Python
```

## Parallel Implementations (High Risk)

| Function | Python Location | Rust Location | Parity Status |
|----------|----------------|---------------|---------------|
| Score formula | osifog_sweep.py::score_official() | ast.rs::evaluate_scoring_table() | Formula matches; Rust has data-driven table |
| Static margin | osifog_sweep.py::_minimum_initial_ascent_stability() | builder.rs::exposed_stage_phase_margins() | NOT COMPARED |
| Mass calculation | physical_geometry.py (cylinders) | mass_calculator.rs | NOT COMPARED |
| Motor burn duration | osifog_sweep.py::_motor_burn_time() [STALE] | motor_db.rs::ThrustCurve | Rust is correct; Python is approximate |
| Wind interpolation | osifog_sweep.py (inline in run_sim) | wind.rs::WindProfile::wind_vector_at() | NOT COMPARED |
| Event extraction | osifog_sweep.py::run_sim() [JPype] | runner.rs::check_staging() | NOT COMPARED |
| Landing speed | osifog_sweep.py::run_sim() [interpolation] | runner.rs::interpolate_touchdown() | NOT COMPARED |
