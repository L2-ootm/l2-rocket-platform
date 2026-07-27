# Optimizer Evolution and CKG Research

## Current Search Behavior Audit

### Population Initialization
- `osifog_engine_search.py::_sample_valid_parameters()`: Random sampling from mission manifest ranges
- Validates physical cage geometry before accepting
- Max 250 attempts per candidate (high rejection rate for constrained topologies)

### Selection
- Top-N scoring candidates become parents
- `parent_count = max(8, min(len(successful), size // 5))`
- No tournament selection — direct truncation

### Mutation
- `_breed_valid_parameters()`: Uniform crossover between two parents + random donor injection
- 12% random gene injection per parameter
- Body radius re-derived from motor fit (not freely mutated)
- Physical geometry validation before acceptance

### Crossover
- Single-point uniform crossover on parameter dictionary
- No topology-aware crossover (e.g., swapping entire fin sets)

### Elitism
- Top `elite_count` candidates survive unchanged to next generation
- No niching or diversity pressure

### Repair
- `validate_candidate_geometry()` rejects illegal configurations
- No constructive repair — rejection only
- Body radius forced to fit motor cage

### Scoring
- Rust proxy score via `evaluate_rust_population()`
- CKG multiplier applied to raw score
- Status-based: "success" candidates scored, "failed" get 0

### OpenRocket Promotion
- `promote_candidates()`: Re-evaluate top-N at higher fidelity
- `authority-heavy` profile: no further promotion (final fidelity)
- Calibration every N generations via `select_stratified_calibration_candidates()`

### Resume
- `_atomic_json()` writes candidate results atomically
- `--no-resume` flag to start fresh
- Cache via `EvolutionEngine._cache` (in-memory, not persisted across sessions)

### CKG (Continuous KnowledgeGraph)
- Records topology signature → score/status/reason
- Records authority results (OpenRocket pass/fail)
- Records calibration ratios (Rust/OR apogee and Mach ratios)
- Acceptance multiplier: penalizes previously failed topologies
- **Storage**: JSON file at `.planning/organic_ckg.json`
- **Concurrency**: Not thread-safe (single-writer assumed)
- **Staleness risk**: If mission parameters change, old calibration ratios are stale
- **Negative transfer**: A topology that failed under old parameters may be unfairly penalized

## Historical Candidate Data Analysis

From the checkpoint documents:
- **Gate 4 search**: 4 structural families, 32 free-descent candidates
- **All 32 failed** the exposed-sustainer 1.5-caliber ascent gate
- **0 powered finalists** were legitimately promoted
- **Booster descent**: H180W at 33.104s achieved 3.5135 m/s in OpenRocket (legal branch)
- **Sustainer descent**: q=0.9006 but reached impact broadside (no legal landing)

### Rejection Rate by Constraint
- **Ascent stability**: 100% rejection (32/32)
- **Mach**: 0% rejection (all subsonic)
- **Geometry**: ~5% rejection during sampling

### Topology Diversity
- Only 4 fin families tested (3, 4, 6, 8 fins)
- All used forward grid fins + aft main fins
- No other topology variants explored (e.g., no rear-only fins, no body transitions, no distributed area)

### Premature Convergence
- The GA converges onto the dominant topology family quickly
- Forward grid fin + aft main fin is the only surviving pattern
- No diversity mechanism to explore alternative descent strategies

## Algorithm Comparison

| Algorithm | Fit for Discrete Topology | Fit for Continuous Geometry | Constraint Handling | Sample Efficiency | Parallelism | Best Role in L2 |
|-----------|--------------------------|---------------------------|--------------------|--------------------|-------------|-----------------|
| Standard GA | YES | YES | Rejection only | LOW | HIGH | Current baseline |
| NSGA-II | PARTIAL | YES | Domination | MEDIUM | HIGH | Multi-objective |
| MAP-Elites | YES | YES | Feasibility archive | HIGH | HIGH | **Recommended primary** |
| CVT-MAP-Elites | YES | YES | Feasibility archive | HIGH | HIGH | Better descriptor coverage |
| CMA-ES | NO | YES | Repair/projection | HIGH | LOW | Per-cell refinement |
| Differential Evolution | NO | YES | Bounds only | MEDIUM | HIGH | Rugged landscapes |
| Bayesian Optimization | NO | YES | Acquisition function | VERY HIGH | LOW | Expensive evaluation |
| TuRBO | NO | YES | Trust region | HIGH | LOW | Local refinement |

## Recommended Architecture

```
Grammar-constrained AST generation
  → hard schema/geometry/mission filters
  → feasibility archive (reject illegal candidates, track failure modes)
  → constrained MAP-Elites archive
    → behavior descriptors:
      1. min phase-aware static margin (ascent)
      2. tail-first window duration (descent)
      3. burn-weighted vertical opposition fraction
      4. forward/aft aerodynamic area ratio
      5. landing impulse margin
      6. dry mass class
      7. motor impulse class
    → archive resolution: 10×10×10×10×10×5×5 = 250,000 cells (sparse)
  → per-cell continuous optimizer (CMA-ES for fin geometry, DE for motor selection)
  → multi-fidelity promotion (Rust → OpenRocket)
  → calibrated uncertainty (divergence model)
  → OpenRocket authority (saved/reopened)
  → local delay/apogee polish
```

## CKG Migration Plan

**Current**: JSON file with flat calibration records
**Target**: SQLite database with:
- Context-specific calibration (per topology family, per motor pair)
- Negative evidence tracking (what failed and why)
- Temporal decay (recent calibrations weighted higher)
- Concurrency-safe writes

**Migration steps**:
1. Add `ckg_version` field to JSON
2. Implement SQLite backend alongside JSON
3. Import existing JSON records
4. Add context-specific calibration keys
5. Switch organic_loop.py to SQLite backend
6. Remove JSON backend after validation
