# Authority Rules and Data Lineage

## Rule Matrix

| Rule ID | Source | Interpretation | Hard/Soft | Engine Enforcement | Gap |
|---------|--------|---------------|-----------|-------------------|-----|
| R-001 | ProjetoFalcon §3 | 2 stages minimum | HARD | validate_hard_constraints: len(stages)==2 | None |
| R-002 | ProjetoFalcon §4 | Stage separation before apogee | HARD | validate_hard_constraints: min(separations) < min(apogees) | None |
| R-003 | ProjetoFalcon §5 | Each stage touchdown < 5 m/s total speed | HARD | validate_hard_constraints: per-stage speed check | None |
| R-004 | ProjetoFalcon §6 | No parachute/streamer/passive recovery | HARD | No recovery devices in OSIFOG topology | None |
| R-005 | ProjetoFalcon §7 | No active thrust-vector control | HARD | NoOpController in Rust; no TVC in OR XML | None |
| R-006 | ProjetoFalcon §8 | No active fin/wing control | HARD | Fixed fins in all topologies | None |
| R-007 | ProjetoFalcon §9 | No throttle control | HARD | Single-burn solid motors only | None |
| R-008 | ProjetoFalcon §10 | Static ascent stability ≥ 1.5 calibers | HARD | validate_hard_constraints + Rust enforce_hard_constraints | None |
| R-009 | ProjetoFalcon §11 | Subsonic flight (Mach < 1.0) | HARD | MAX_MACH=0.95 (conservative margin) | None |
| R-010 | MissaoSecreta §2 | Launch site: 28.5621N, 80.5772W, 3.0m MSL | FIXED | Hardcoded in osifog_sweep.py constants | None |
| R-011 | MissaoSecreta §3 | Temperature: 30.1°C, Pressure: 1000 hPa | FIXED | Hardcoded in osifog_sweep.py constants | None |
| R-012 | OpenWind_File.csv | 28-level AGL wind profile | FIXED | parse_wind_csv() + XML multilevel wind | None |
| R-013 | ProjetoFalcon §12 | Launch rod: max 6.0 m | FIXED | LAUNCH_ROD_M=6.0 | None |
| R-014 | ProjetoFalcon §13 | Material density 170-11340 kg/m³ | HARD | physical_geometry.py + Rust material_density_checked | None |
| R-015 | ProjetoFalcon §14 | Minimum dimension 0.1 cm (1 mm) | HARD | MIN_DIMENSION_M=0.001 | None |
| R-016 | ProjetoFalcon §15 | Max rocket height 4.0 m | HARD | MAX_HEIGHT_M=4.0 | None |
| R-017 | ProjetoFalcon §16 | No arbitrary mass/CG/Cd overrides | SOFT | Not enforced in Rust proxy (point masses allowed) | Gap: Rust allows BALLAST as point mass |
| R-018 | ProjetoFalcon §17 | No floating/intersecting solids | HARD | physical_geometry.py validate_cylinders | Gap: Rust proxy has no equivalent |
| R-019 | MissaoSecreta §4 | Anti-tumbling listener required | HARD | validate_anti_tumble_extensions | None |
| R-020 | MissaoSecreta §5 | One final simulation, run immediately before save | HARD | save_simulated_ork() runs sim before save | None |
| R-021 | MissaoSecreta §6 | Save all simulation data | HARD | StorageOptions.setSaveSimulationData(True) | None |
| R-022 | ProjetoFalcon §18 | Score = 900000 - penalties | FIXED | score_official() matches formula | None |

## Data Lineage: Target Apogee

```
Official PDF → 3000 m target
  → missions/osifog_l3_precision.json::target_apogee = 3000.0
  → osifog_sweep.py::TARGET_APOGEE = 3000.0
  → osifog_precision.py::score_from_mission_contract() reads from JSON
  → Rust AstEvalBatch.target_apogee_m = 3000.0 (from constraints)
```

## Data Lineage: Score Coefficients

```
Official PDF → base=900000, terms with specific coefficients
  → missions/osifog_l3_precision.json::scoring
  → osifog_sweep.py::score_official() [hardcoded formula, NOT reading JSON]
  → osifog_precision.py::score_from_mission_contract() [reads JSON dynamically]
  → Rust ast.rs::evaluate_scoring_table() [reads JSON dynamically]
```

**CONTRADICTION**: `osifog_sweep.py::score_official()` hardcodes the formula. `osifog_precision.py::score_from_mission_contract()` reads from the mission JSON. These should agree but are maintained independently. If the JSON changes, the hardcoded version becomes stale.

## Data Lineage: Wind Profile

```
OSIFOG/OpenWind_File.csv (28 levels, AGL, degrees)
  → osifog_sweep.py::parse_wind_csv() → list of (alt, spd, dir_deg, std) tuples
  → embedded in ORK XML as <wind model="multilevel">
  → OpenRocket interpolates internally
  → Rust WindProfile::from_csv() reads same CSV
  → WindProfile::wind_vector_at() linear interpolation
```

**GAP**: Rust wind model does not use turbulence (std_dev). OpenRocket's multilevel model does use the standard deviation for stochastic perturbation (seeded by simulation seed).

## Data Lineage: Motor Curves

```
OpenRocket 24.12 motor database
  → extract_motors.py → l2_engine/motors/*.eng files
  → rocket_forge.py::MOTOR_DATABASE (digest matches .eng header)
  → Rust motor_db.rs::parse_eng() reads .eng files
  → Python osifog_engine_search.py::_load_motor_curve() reads .eng files
  → Both paths use same source data
```

## Source Divergence Detection

| Value | Python | Rust | Match? |
|-------|--------|------|--------|
| Material densities | rocket_forge.py MATERIALS dict | ast.rs material_density() | CHECK NEEDED |
| Motor dimensions | MOTOR_DATABASE tuple | .eng file headers | Should match by construction |
| Body tube thickness | 0.002 m (hardcoded) | 0.002 m (default) | Yes |
| Fin cross-section factor | N/A | 0.85 airfoil, 1.0 otherwise | N/A |
| Gravity | 9.80665 m/s² | 9.80665 m/s² | Yes |
| Reference diameter | "maximum" in OR XML | body radius from geometry | Check needed |
