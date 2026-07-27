# L2-OSIFOG Codebase Analysis

**Date**: 2026-07-04  
**Scope**: Full codebase audit — architecture, correctness, test coverage, documentation fidelity

---

## Table of Contents

1. [What This Project Is](#1-what-this-project-is)
2. [Architecture Overview](#2-architecture-overview)
3. [Rust Engine Deep Dive](#3-rust-engine-deep-dive)
4. [Python Engine Deep Dive](#4-python-engine-deep-dive)
5. [Physics Model Fidelity](#5-physics-model-fidelity)
6. [Critical Issues](#6-critical-issues)
7. [What's Working Well](#7-whats-working-well)
8. [Test Coverage Assessment](#8-test-coverage-assessment)
9. [Documentation-State Drift](#9-documentation-state-drift)
10. [Recommendations](#10-recommendations)

---

## 1. What This Project Is

**L2-OSIFOG** is a rocket design optimizer for OSIFOG/BIRST 2026 (Olimpíada Brasileira de Simulação de Foguetes — Brazilian Rocket Simulation Olympiad). All designs are evaluated purely in OpenRocket 23.09 — no physical launches.

**Competition format**: Teams submit `.ork` files (OpenRocket design format) that are scored against mission objectives — precision altitude, max altitude, max speed, or Karman line breach. Designs must be 100% OpenRocket-compatible.

**Mission profiles pursued**:
- Precision 350m (single-stage, H133 motor)
- 15km precision (3-stage, M2245/N5800/O8000)
- Max altitude (single-stage ballistic, O8000: 15.28 km / Mach 3.12)
- Speed demonstration (target Mach 3 at 15km altitude)
- Karman line breach + Mach 6 (3-stage, O8000 → N5800 → M2245)

**Best validated design**: `L2_Hyper_100K_M6.ork` — 236.4 km apogee, Mach 6.72, margins [2.70, 2.80, 1.56] calibers.

**Team**: L2 Systems 1024.

---

## 2. Architecture Overview

### Two-Tier Optimization Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│  Tier 1: Fast Exploration (Rust, ~110 sims/s)                  │
│                                                                 │
│  rocket_ast.py ──► JSON batch ──► ast_eval.rs ──► sim_core     │
│  (AST generator)   (subcontract)  (Rayon parallel)  (6DOF RK4) │
│                                                                 │
│  Output: elite.json (top-performing designs)                    │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tier 2: Ground Truth Polish (OpenRocket 23.09, ~1 sim/5s)     │
│                                                                 │
│  elite.json ──► organic_loop.py ──► JPype ──► OpenRocket JVM   │
│                                                 (headless)     │
│                                                                 │
│  Output: validated .ork files with confirmed metrics            │
└─────────────────────────────────────────────────────────────────┘
```

### Code Layout

| Component | Location | Purpose |
|-----------|----------|---------|
| `l2_engine/` | Rust crate | Physics proxy, AST evaluator, motor DB, sim_core |
| `l2_hyper/` | Python package | OpenRocket-native GA (mission-driven) |
| `rocket_ast.py` | Root Python | AST-based rocket topology generator |
| `organic_loop.py` | Root Python | Orchestrates Rust eval + OR polish |
| `rocket_forge.py` | Root Python | Motor/material databases, .ork XML builder |
| `missions/*.json` | JSON specs | Declarative mission definitions |
| `l2_engine/motors/*.eng` | RASP format | 36 real OpenRocket-sourced thrust curves |
| `.planning/` | Markdown | Phase plans, ADRs, issues, roadmap |

### Data Flow End-to-End

1. **AST Generation** (`rocket_ast.py`): `create_random_ast()` produces a flat list of `ASTNode` objects (STAGE, NOSE_CONE, BODY_TUBE, MOTOR_MOUNT, FIN_SET, PARACHUTE, PAYLOAD). Mutations are type-specific jitter functions. 30% chance of 2-stage.

2. **Rust Evaluation** (`ast_eval.rs`): `ast_to_geometry()` translates AST nodes into `RocketGeometry`. `build_mission()` constructs a `Mission` with thrust curves, aerodynamic coefficients, and mass properties. `sim_core::sim::simulate_with()` runs a 6DOF RK4 integrator. `score_summary()` produces fitness: `(target_fit * 100 + max_mach) * margin_factor`.

3. **CKG Pre-filter** (`ckg_memory.py`): Subgraph hashes of AST nodes are cached with failure/success counts. Candidates with acceptance multiplier < 0.10 are skipped entirely.

4. **Elite Export** (`organic_loop.py`): Each elite AST is compiled to XML via `ASTCompiler`, written as a `.ork` ZIP, and optionally validated in OpenRocket via a shared JPype JVM instance.

5. **Mission-Driven GA** (`l2_hyper/`): `run_mission.py` loads a mission JSON, derives a genome from the motor stack, runs a BLX-alpha crossover GA against OpenRocket directly (no Rust proxy).

---

## 3. Rust Engine Deep Dive

### Module Map

| Module | Lines | Purpose |
|--------|-------|---------|
| `barrowman.rs` | ~1165 | Aerodynamic model — CP, CNa, Cd (Mach-dependent table at 12 points) |
| `mass_calculator.rs` | ~400 | Structural mass + CG (body tubes, nosecones, fins) |
| `mission_adapter.rs` | ~400 | Converts geometry + thrust curves into sim_core Mission |
| `motor_db.rs` | ~350 | RASP `.eng` file parser, impulse-weighted mass model |
| `ast.rs` | ~500 | AST genome → RocketGeometry translation |
| `geometry.rs` | ~150 | StageGeometry, RocketGeometry structs |
| `xml_parser.rs` | ~600 | `.ork` ZIP extraction + XML → StageGeometry |
| `builder.rs` | ~100 | `stack_wet_cg`, `static_margins` (post-refactor, genome code deleted) |
| `openrocket_nose.rs` | ~300 | Shape-specific nose pressure drag tables (6 shapes) |
| `sim_core/` | ~2000 | 6DOF integrator, ISA atmosphere, staging, recovery |

### Physics Model

**Integration**: RK4 at dt=0.005s (200 Hz), quaternion normalization per step, NoOpController (ballistic only).

**Atmosphere**: Full ISA 1976 with 7 layers to 86km. Sutherland's law for viscosity. OpenRocket's linear sound speed approximation: `a = 165.77 + 0.606*T`.

**Aerodynamics** (`barrowman.rs`):
- **CP**: Volume-based Barrowman method. 200-slice numerical integration for nosecone volume. Classical fin CP formula.
- **CNa**: Subsonic (verbatim from OpenRocket's `FinSetCalc.java`), linear transonic blend (0.9–1.2), supersonic Busemann K1. Body-fin and fin-count interference factors.
- **Cd**: Two-component model (friction + non-friction). Friction uses OpenRocket's explicit formula with compressibility correction. Nose pressure drag has HyperReal mode (von Karman tables) and OpenRocketLegacy mode (shape-specific tables). Base drag: `0.12 + 0.13*M²` subsonic, `0.25/M` supersonic. Fin pressure, base, and wave drag included.

**Mass**: Thin-wall cylinder for bodytubes, Haack-series shell integration for nosecones, flat-plate shoelace for fins. Impulse-weighted mass loss (not time-linear) — critical correctness point.

**Staging**: Propellant depletion via mass comparison. Post-depletion coast (ejection charge delay) before mass drop. Ignition delay gating per stage. Parachute drag after separation.

### Motor Database

36 real motors (F50T through N4800T) as `.eng` files in `l2_engine/motors/`. Parsed by `motor_db::parse_eng()`. Dynamic loading at startup — adding a motor is dropping a file. Designations match OpenRocket's authoritative strings.

**Key correctness detail**: `mass_at(t)` uses impulse-weighted mass loss: `mass(t) = total_mass - propellant_mass * (cumulative_impulse(t) / total_impulse)`. This is physically correct — mass loss rate is proportional to thrust, not time.

### JSON Subcontract

**Input** (to `ast_eval`):
```json
{
  "target_apogee_m": 100000,
  "physics_mode": "OpenRocketLegacy",
  "objectives": [{"kind": "atleast", "metric": "apogee_m", "value": 100000}],
  "candidates": [{"id": "abc123", "ast": [...]}]
}
```

**Output** (from `ast_eval`):
```json
{
  "results": [{
    "id": "abc123",
    "status": "evaluated",
    "score": 0.87,
    "apogee_m": 95000,
    "mach": 5.8,
    "min_static_margin": 2.1,
    "margins": [2.1, 2.3, 1.8],
    "reason": null
  }]
}
```

---

## 4. Python Engine Deep Dive

### `l2_hyper/` — Mission-Driven GA

| Module | Purpose |
|--------|---------|
| `run_mission.py` | CLI entry point, GA loop, seed management |
| `mission.py` | Mission JSON loader, `compile_fitness()` — closure-based scoring |
| `genome.py` | Gene definitions derived from motor stack, BLX-alpha crossover |
| `generator.py` | `.ork` XML builder — UUID rules, clearance checks, recovery sizing |
| `orkit.py` | OpenRocket session manager, JPype interface, motor resolution |
| `ga.py` | Tournament selection, elitism, generation history |

**Fitness function** (`compile_fitness`): Five objective kinds — `atleast`, `atmost`, `target`, `maximize`, `minimize`. Stability penalty: tumble/late-ignition multiplier. Margin penalty: graded function with 1.5 cal default (absorbs 0.55 cal CG bias between OR 23.09 headless and 24.12 GUI).

**Genome derivation**: Per-stage fin span (1.3r–3.2r of body radius) and root chord (3.0r–7.5r). Stage 0 adds nose length and ballast. Non-bottom stages add ignition delay. One global `sep_delay`.

**`.ork` generation** (`generator.py`): Enforces one shared UUID across `motorconfiguration/motor/simulation-conditions`. Minimum-diameter construction (motor + 1mm clearance must fit airframe). Nose/interstage transitions with 0.3mm slip-fit shoulders. Every stage gets drogue (28 m/s) + main (6.5 m/s at 500m). Separation hardware, motor retention, avionics modeled as mass.

### `rocket_ast.py` — AST Topology Generator

Flat AST with node types: STAGE, NOSE_CONE, BODY_TUBE, CLOSE_BODY, MOTOR_MOUNT, FIN_SET, PARACHUTE, PAYLOAD. `ASTCompiler` state machine converts to hierarchical XML. Single `config_id` UUID per compiler instance (shared across all stages — correct for OpenRocket).

**Known code smell**: ASTCompiler wraps motors in `<innertube>` with nested `<motormount>`, while `l2_hyper/generator.py` uses `<motormount>` directly. The innertube wrapper adds unnecessary mass.

### `rocket_forge.py` — Motor/Material Databases

33 motors in `MOTOR_DATABASE` (F through O class), each with manufacturer, designation, diameter, length, delay, digest. 10 materials with OpenRocket names and densities. `RocketArchitect` builds complete `.ork` XML from parameter dicts.

**Verified 2026-07-04**: O8000 diameter corrected from 161mm to 150mm (matching OpenRocket's SQLite DB). K510, N5800 lengths also corrected.

### `organic_loop.py` — Orchestration

`run_generation()` creates random population, evaluates (Rust or heuristic), records to CKG, selects survivors, breeds next generation. CKG persistence via JSON keyed by SHA-256 subgraph hashes. `acceptance_multiplier()` returns `exp(-penalty)` where penalty accumulates 0.35 per failure and loses 0.08 per success — Bayesian prior against structurally doomed patterns.

### `missions/*.json` — Mission Specs

| Mission | Stages | Strategy |
|---------|--------|----------|
| `precision_350m.json` | 1 (H133) | target 350m, minimize flight_time |
| `15k_precision.json` | 3 | target 15000m (0.001% tolerance) |
| `karman_m6.json` | 3 | atleast 100km, atleast Mach 6, maximize apogee |
| `karman_push_alt_v4.json` | 3 | Real launch site, wind 4 m/s, spherical geodetic |
| `speed_max_m3.json` | 3 | atmost Mach 3 (weight 100), maximize Mach |
| `weird_speed_demon.json` | 3 | target 15km, maximize Mach |
| `ballistic_max_ss.json` | 1 (O8000) | maximize apogee + Mach |

---

## 5. Physics Model Fidelity

### What's Accurate

- **Friction drag**: OpenRocket's explicit formula with Reynolds number and compressibility correction. Wetted area includes fin planform (both surfaces).
- **Base drag**: Bytecode-verified from `BarrowmanCalculator.class`.
- **Mass model**: Impulse-weighted mass loss, thin-wall structural masses, Haack-series nosecone integration.
- **Atmosphere**: ISA 1976, 7 layers, Sutherland viscosity.
- **CP calculation**: Volume-based Barrowman with 200-slice numerical integration.

### What's Approximated

- **Transonic CNa**: Linear blend (Mach 0.9–1.2) instead of OpenRocket's polynomial interpolator. Affects stability predictions in the transonic regime.
- **Nose pressure drag**: Linear clamped above Mach 3.0 (documented "Pitfall 2"). No hypersonic real-gas effects.
- **Fin wave drag**: Biconvex airfoil theory (`4*(t/c)²/√(M²-1)`), simplified from OpenRocket's component-buildup method.
- **Inertia**: Hardcoded constants `Vector3::new(1.0, 1.0, 0.1)` — only affects rotational dynamics, not apogee/Mach.
- **No wind**: ISA standard atmosphere with zero wind. Real flights have significant wind effects on apogee.

### Measured Proxy Bias

From `or_mode_calibrate.py` (3 karman_m6 elites):

| Metric | Mean Error | Direction |
|--------|-----------|-----------|
| Apogee | +13.9% | Overestimate |
| Mach | +0.35 | Overestimate |
| Static margin | ~0.1 cal | Inconsistent sign |

**Root cause**: `barrowman.rs`'s supersonic/transonic drag modeling is too lenient around Mach 6. The nose pressure drag tables are clamped flat above Mach 3.0, and the transonic CNa blend is a linear approximation.

---

## 6. Critical Issues

### P0: Proxy Overestimate (13.9% apogee, 0.35 Mach)

**Impact**: The GA optimizes against a distorted objective function. Designs that appear optimal in the proxy may underperform in OpenRocket truth. The polish pass can correct individual candidates, but cannot fix exploration in the wrong design region.

**Root cause**: `barrowman.rs` drag tables — supersonic nose pressure drag clamped, transonic CNa linear blend, simplified fin wave drag.

**Fix path**: Run `or_mode_calibrate.py` across 5+ missions spanning Mach 0.5–6.0. Build correction lookup table. Apply in `cd_at_mach_from_stages()`.

### P1: Documentation-State Drift

| Claimed in STATE.md | Reality |
|---------------------|---------|
| `evolve.rs` DELETED | File exists (352 lines) |
| `l2_engine_base/` removed | Directories still on disk |
| 80/80 tests pass | `validation.rs` failing (153% apogee error) |
| System operational | `.planning/STATE.md` says Phase 1 "Blocked" |

### P2: Zero Python Tests

No unit tests for:
- `compile_fitness()` — five objective kinds, penalty interactions, negative-score division
- `build_rocket_xml()` — UUID sharing, clearance checks, shoulder sizing, recovery sizing
- `ASTCompiler` — multi-stage state machine, open/close body tracking
- `genome.py` operators — crossover, mutation, tournament, clamp
- `ckg_memory.py` — subgraph hashing, penalty accumulation
- Motor database regression (O8000 diameter was wrong for unknown duration)

### P3: Three Parallel Evolution Systems

| System | Interface | Fitness Source | Status |
|--------|-----------|---------------|--------|
| `forge_mega.py` | Parameter GA | OpenRocket direct | Legacy |
| `organic_loop.py` | AST GA | Rust proxy + OR polish | Active |
| `l2_hyper/` | Genome GA | OpenRocket direct | Active |

No shared base class or interface. Maintenance burden multiplied. Bugs can exist in one path but not others.

### P4: Uniform Failure Scoring

`ast_eval` returns `score: 0.0` for all failures — NaN trajectory, missing motor, fitment violation all look identical to the GA. Hides useful failure information.

### P5: `haack_profile_radius()` Duplicated

Identical implementation in both `mass_calculator.rs` (private) and `barrowman.rs` (private). Should be extracted to a shared utility.

### P6: Vestigial `optimize.rs`

Uses a completely different parametric approach (3 scalar multipliers on a cloned .ork). References `L2_Hyper_Parallel_15K_Fixed.ork` which may not exist. Superseded by AST evolution but still in `Cargo.toml` bins.

---

## 7. What's Working Well

- **Motor data sourcing**: Pulling thrust curves from OpenRocket's SQLite DB via `extract_motors.py` caught real transcription errors (O8000 diameter, K510/N5800 lengths).
- **Dynamic motor loading**: `ast_eval.rs` scanning `motors/*.eng` — adding a motor is zero code changes.
- **`sim_core` physics**: 6DOF RK4 with ISA atmosphere, impulse-weighted mass flow, staging logic. The mass-flow bug fix was critical.
- **OpenRocket `.ork` generation**: UUID sharing rule correctly enforced in both pipelines. Hard-won knowledge.
- **Static margin fitness penalty**: Graded function with 1.5 cal default absorbs the 0.55 cal CG bias between OR versions.
- **Motor fitment enforcement**: `mission_adapter.rs::build_mission` rejecting physically impossible designs.
- **CKG pre-filtering**: Bayesian prior against structurally doomed AST patterns — genuine differentiator.
- **Rust test suite**: ~60+ tests covering physics pipeline, AST bridge, motor parsing, sim_core dynamics.

---

## 8. Test Coverage Assessment

### Rust (`l2_engine/`)

| Area | Coverage | Notes |
|------|----------|-------|
| `barrowman.rs` | Good (12 inline tests) | CP, CNa, Cd for both physics modes |
| `mass_calculator.rs` | Good (6 tests) | Bodytube, nosecone, fin mass, CG |
| `motor_db.rs` | Good (5 tests) | Parsing, interpolation, impulse-weighted mass |
| `mission_adapter.rs` | Moderate (2 tests) | End-to-end build, OR mode parachute |
| `sim_core/` | Good (22 tests) | Dynamics, atmosphere, gravity, events, runner |
| `ast.rs` | Moderate (6 tests in ast_bridge.rs) | Geometry compilation, fitment regression |
| `xml_parser.rs` | None | Only tested indirectly |
| `gnc/` modules | None | TVC, PID, guidance untested |
| `orbital/` modules | None | Keplerian elements, maneuvers untested |
| `optimize.rs` | None | Vestigial binary |

### Python

| Area | Coverage | Notes |
|------|----------|-------|
| `l2_hyper/*` | Zero | No unit tests for any module |
| `rocket_ast.py` | Zero | ASTCompiler state machine untested |
| `organic_loop.py` | Zero | Orchestration, CKG integration untested |
| `rocket_forge.py` | Zero | Motor database regression untested |
| `or_mode_calibrate.py` | Zero | Calibration workflow untested |

---

## 9. Documentation-State Drift

The root `STATE.md` and `.planning/STATE.md` contradict each other:

- Root STATE.md describes a post-cleanup system with fixed bugs and working organic evolution.
- `.planning/STATE.md` marks Phase 1 as "Blocked" with validation test failing at 153% apogee error.
- Root STATE.md claims `evolve.rs` was deleted and `l2_engine_base/` was removed — both still exist on disk.
- Root STATE.md claims "80/80 tests pass" — the gold-standard `validation.rs` test is failing.

**Risk**: Future sessions will read STATE.md as ground truth and waste time debugging problems that are already documented as known/unsolved, or assume capabilities that don't exist.

---

## 10. Recommendations

### Immediate (This Session)

1. **Calibrate `barrowman.rs` drag tables.** Run `or_mode_calibrate.py` across 5+ missions spanning Mach 0.5–6.0. Build correction lookup table. Apply in `cd_at_mach_from_stages()`. This is the highest-leverage single task.

2. **Reconcile STATE.md with reality.** Either delete `evolve.rs` and `l2_engine_base/`, or update STATE.md to reflect their actual status. Mark Phase 1 validation clearly as blocked/failing.

### Short-Term (This Week)

3. **Write Python tests for the critical path.** Priority: `compile_fitness()`, `build_rocket_xml()` UUID sharing, `ASTCompiler` multi-stage XML, motor database regression (O8000 == 0.150m).

4. **Differentiate failure modes in `ast_eval`.** Return distinct error codes for NaN/divergence vs. missing motor vs. fitment violation. Let the GA apply targeted penalties.

5. **Extract `haack_profile_radius()`** to a shared utility module.

6. **Delete or archive `optimize.rs`** and `forge_mega.py`. They add maintenance burden with no active use.

### Medium-Term (Before Phase 3)

7. **Consolidate evolution systems.** Pick `l2_hyper/` for OpenRocket-native missions and `organic_loop.py` for Rust-proxy exploration. Deprecate `forge_mega.py` and `hyper_100k_pipeline.py`.

8. **Add `xml_parser.rs` unit tests.** The `.ork` XML → StageGeometry path is currently only tested indirectly.

9. **Run `or_mode_ast_sweep.py`** to measure proxy variance across random seeds, not just systematic bias.

### Before GPU Engine

10. **Close the proxy accuracy gap to <5% apogee.** The GPU engine plan ports `barrowman.rs` line-by-line into WGSL — any bugs there get amplified by 10,000x parallelism.

11. **Establish a regression test suite** that blocks PRs breaking physics accuracy. The `validation.rs` test (match OR within 2%) should pass before any new physics code merges.

---

## Appendix: Key File Paths

| File | Purpose |
|------|---------|
| `l2_engine/src/barrowman.rs` | Aerodynamic model (CP, CNa, Cd) |
| `l2_engine/src/sim_core/` | 6DOF flight simulator |
| `l2_engine/src/ast.rs` | AST genome → geometry |
| `l2_engine/src/motor_db.rs` | .eng file parser |
| `l2_engine/src/mission_adapter.rs` | Geometry + thrust → Mission |
| `l2_engine/src/bin/ast_eval.rs` | Batch evaluation binary |
| `l2_engine/motors/*.eng` | 36 real thrust curves |
| `l2_hyper/run_mission.py` | Mission GA entry point |
| `l2_hyper/generator.py` | .ork XML builder |
| `l2_hyper/orkit.py` | OpenRocket interface |
| `rocket_ast.py` | AST topology generator |
| `organic_loop.py` | Rust eval + OR polish orchestrator |
| `rocket_forge.py` | Motor/material databases |
| `missions/*.json` | Mission specifications |
| `or_mode_calibrate.py` | Proxy vs. truth calibration |
| `STATE.md` | Project state (needs reconciliation) |
| `.planning/` | Phase plans, ADRs, roadmap |
