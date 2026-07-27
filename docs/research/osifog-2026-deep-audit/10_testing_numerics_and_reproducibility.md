# Testing, Numerics, and Reproducibility

## Test Inventory

### Python Tests (52 total)
| File | Tests | Coverage | Gap |
|------|-------|----------|-----|
| test_osifog_engine_search.py | ~15 | Scenario manifests, landing opportunity, delay candidates | Missing: powered landing validation, recombination |
| test_osifog_sweep.py | ~12 | ORK generation, scoring, hard constraints | Missing: anti-tumble invariance, wind parity |
| test_osifog_precision.py | ~10 | Delay search, trajectory polish, save/reopen | Missing: multi-seed robustness |
| test_osifog_falcon_contract.py | ~8 | Physical geometry, cage, ballast rods | Missing: fin collision, exhaust volume |
| test_physical_geometry.py | ~7 | Cylinder collision, attachment paths | Missing: annular parts, motor tube obstruction |

### Rust Tests (160 total)
| File | Tests | Coverage | Gap |
|------|-------|----------|-----|
| ast.rs tests | ~20 | AST parsing, geometry compilation, scoring | Missing: multi-stage margin comparison |
| builder.rs tests | ~10 | CG, margin, phase evaluation | Missing: OpenRocket parity |
| mass_calculator.rs tests | ~15 | Mass, CG, inertia | Missing: dynamic CG during burn |
| barrowman.rs tests | ~25 | CP, CNa, drag | Missing: transonic parity |
| motor_db.rs tests | ~10 | .eng parsing, impulse integration | Missing: cluster aggregation |
| sixdof.rs tests | ~15 | 6-DOF dynamics | Missing: wind-relative validation |
| runner.rs tests | ~10 | Simulation loop, staging | Missing: dropped-stage propagation |
| ast_bridge.rs tests | ~17 | Python-Rust JSON contract | Complete |
| validation.rs tests | ~3 | ORK validation | Missing: full round-trip |
| or_mode.rs tests | ~3 | OpenRocket mode parity | Missing: full fixture |

## Missing Test Categories

### Units (deg/rad, mm/cm/m)
- **Status**: NOT TESTED
- **Risk**: Medium — coordinate conversions are scattered across files
- **Action**: Add unit-conversion round-trip tests

### Mass Conservation
- **Status**: PARTIALLY TESTED (Rust property tests)
- **Risk**: Low — mass_calculator.rs has cross-checks
- **Action**: Add full-stack mass conservation test

### Propellant Depletion
- **Status**: TESTED in Rust (impulse-weighted model)
- **Gap**: No comparison with OpenRocket's internal model
- **Action**: Compare consumed propellant for same motor

### Frame Conventions (body/world, ENU)
- **Status**: NOT SYSTEMATICALLY TESTED
- **Risk**: High — frame mismatches cause silent sign errors
- **Action**: Add sign tests for ascent thrust, retro thrust, wind direction

### Anti-Tumble Invariance
- **Status**: NOT TESTED
- **Risk**: High — listener side-effects unquantified
- **Action**: Run Experiment 1

### Branch Identity
- **Status**: TESTED in test_osifog_engine_search.py
- **Gap**: No test for missing branches or extra branches
- **Action**: Add edge-case tests

### Event Ordering
- **Status**: NOT TESTED
- **Risk**: Medium — simultaneous events may reorder
- **Action**: Add test for burnout+separation at same time

### Score Formula
- **Status**: TESTED in test_osifog_sweep.py
- **Gap**: No cross-validation between Python and Rust implementations
- **Action**: Run Experiment 7

### Saved/Reopened Artifact
- **Status**: TESTED in test_osifog_precision.py
- **Gap**: No test for hash verification after reopen
- **Action**: Add SHA-256 verification test

### Stochastic Seed Determinism
- **Status**: PARTIALLY TESTED (single seed)
- **Gap**: No test verifying different seeds produce different results
- **Action**: Add seed-sensitivity test

## Invariants to Verify

1. **Mass never increases without ignition**: During burn, mass decreases monotonically
2. **Propellant remains bounded**: 0 ≤ consumed ≤ loaded
3. **Full-stack mass = separated branch masses + consumed propellant**: Mass conservation
4. **No stage disappears**: Every STAGE node produces exactly one branch
5. **Total impulse matches curve integration**: Motor impulse from .eng matches thrust integral
6. **Changing seed does not affect deterministic authority mode**: Fixed seed produces identical results
7. **No diagnostic result becomes authority**: Diagnostic scenarios cannot be scored
8. **Invalid geometry never reaches OpenRocket**: Physical gate rejects before JVM
9. **Saved/reopened values match fresh extraction**: Round-trip fidelity

## Numerical Methods

### Integration
- **Rust**: RK4 with adaptive stepping (0.005-0.05 s)
- **Python/OpenRocket**: RK4Simulator (OpenRocket's default)
- **Axial screener**: Midpoint (RK2) with fixed 0.05 s

### Interpolation
- **Motor thrust**: Piecewise linear
- **Wind profile**: Linear between levels
- **Touchdown**: Linear z=0 crossing
- **Stability**: Bilinear (Mach × AoA)

### Determinism
- Fixed random seed (16000) for all authority simulations
- Deterministic component IDs (UUID5)
- Deterministic motor configuration ID
- Seeded pink noise wind models

### Known Numerical Risks
1. **Stiff dynamics**: Motor ignition/burnout creates discontinuities — handled by event-aware stepping
2. **Quaternion drift**: Historical NaN bug — fixed by skipping rotation when not burning
3. **Ground-hit interpolation**: Linear interpolation may miss exact touchdown if timestep is large
4. **Mach interpolation**: Linear between table knots — may smooth transonic discontinuities
