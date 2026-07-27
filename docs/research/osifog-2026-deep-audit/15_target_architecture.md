# Target Architecture

## Module Structure

```
mission/
  official_inputs     # PDFs, CSV, official digests
  manifest            # missions/osifog_l3_precision.json
  rules               # Hard/soft constraints, rule matrix
  scoring             # Data-driven scoring table
  scenario_policy     # OFFICIAL/DIAGNOSTIC/POLISH scenario selection

model/
  ast_schema          # ASTNode types, params, validation
  component_ontology  # STAGE, BODY_TUBE, MOTOR_MOUNT, FIN_SET, BALLAST, etc.
  units               # SI enforcement, conversion utilities
  ids                 # Deterministic UUID generation
  stage_graph         # Stage ownership, separation config
  motor_graph         # Motor roles, clusters, ignition events

physics/
  materials           # Density database, allowed range
  mass                # Component mass, total mass, dynamic CG
  cg                  # Center of gravity with propellant depletion
  inertia             # Principal moments, parallel-axis theorem
  motors              # .eng parser, thrust curve, impulse-weighted mass
  atmosphere          # ISA model with launch conditions
  wind                # Multi-level wind profile, turbulence placeholder
  aerodynamics        # Barrowman CP/CNa, drag decomposition
  stability           # Phase-aware margins, exposed-stage evaluation
  6dof                # Quaternion dynamics, RK4 integration
  events              # Ignition, burnout, separation, ground-hit

geometry/
  primitives          # AxialCylinder, NoseCone, FinSet, BodyTube
  containment         # Radial/axial containment checks
  collision           # Pairwise cylinder overlap
  contact             # Tangent contact graph
  load_paths          # Attachment path to airframe
  exhaust             # Exhaust swept volume (MISSING)
  separation          # Stage separation plane (MISSING)

openrocket/
  component_ir        # Intermediate representation for ORK generation
  compiler            # parameters → ORK XML
  loader              # ORK → RocketGeometry
  runtime             # JVM lifecycle, simulation execution
  scenario_builder    # Scenario manifest construction
  extension_validator # Anti-tumble script validation
  extractor           # Flight data → metrics dict
  round_trip_validator# Save → reopen → compare

search/
  grammar             # Topology generation rules
  mutations           # Parameter mutation operators
  constraints         # Hard/soft constraint evaluation
  qd_archive          # MAP-Elites implementation
  continuous_optimizers # CMA-ES, DE per cell
  promotion           # Multi-fidelity promotion policy
  uncertainty         # Divergence model, calibration
  cache               # Evaluation memoization

data/
  motors              # .eng file registry
  materials           # Material density database
  wind                # Wind profile cache
  result_store        # Candidate results database
  cache               # Evaluation cache
  ckg                 # Continuous Knowledge Graph (SQLite)

verification/
  parity              # Rust ↔ OpenRocket comparison fixtures
  invariants          # Physics conservation checks
  authority           # Legal/illegal classification
  saved_reopened      # Save → reopen → re-extract pipeline
  submission          # Final artifact packaging

cli/
  research            # Diagnostic/experiment scripts
  search              # Population search entry point
  validate            # Candidate validation
  polish              # Delay/apogee refinement
  package             # Submission artifact creation
```

## Current File Mapping

| Target Module | Current Files | Migration Risk |
|--------------|---------------|----------------|
| mission/manifest | missions/osifog_l3_precision.json | LOW |
| mission/rules | osifog_sweep.py::validate_hard_constraints | MEDIUM — inline in sweep |
| mission/scoring | osifog_sweep.py::score_official + ast.rs::ScoringTable | LOW — already data-driven |
| model/ast_schema | rocket_ast.py::ASTNode + ast.rs::AstNode | LOW |
| physics/mass | physical_geometry.py + mass_calculator.rs | MEDIUM — parallel implementations |
| physics/aerodynamics | barrowman.rs | LOW — already comprehensive |
| physics/6dof | sixdof.rs + runner.rs | LOW — already comprehensive |
| physics/motors | motor_db.rs + rocket_forge.py::MOTOR_DATABASE | LOW |
| geometry/ | physical_geometry.py + geometry.rs | MEDIUM — parallel implementations |
| openrocket/ | osifog_sweep.py (init_or, run_sim, generate_ork) | HIGH — large monolith |
| search/ | organic_loop.py + osifog_engine_search.py | HIGH — intertwined |
| data/ckg | ckg_memory.py | LOW |
| verification/ | tests/ + inspect_saved_submission | MEDIUM |

## Retirement Plan

| Current File | Action | Replacement |
|-------------|--------|-------------|
| rocket_forge.py::RocketArchitect | RETIRE | osifog_sweep.py::generate_ork |
| rocket_ast.py::ASTCompiler | RETIRE (for OSIFOG) | osifog_sweep.py::generate_ork |
| l2_engine/src/bin/evolve.rs | RETIRE | ast_eval.rs |
| l2_engine/src/bin/optimize.rs | RETIRE | ast_eval.rs |
| l2_hyper/ | RETIRE | organic_loop.py |
| legacy/ | RETIRE | Current pipeline |
| osifog_sweep.py (sweep functions) | RETIRE | osifog_engine_search.py |
| osifog_sweep.py (main) | RETIRE | osifog_engine_search.py |
