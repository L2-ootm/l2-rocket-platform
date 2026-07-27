# Evidence and Reading Ledger

## Git State
- **Branch**: `research/osifog-2026-deep-audit` (created from `master`)
- **HEAD commit**: `d7d4bc8` (wip: OSIFOG maximum-score optimization paused at 5/6)
- **Modified files**: 30+ tracked, 40+ untracked

## Official Input Hashes (SHA-256)
| File | Hash |
|------|------|
| OSIFOG/OSIFOG_Nivel3_ProjetoFalcon.pdf | `5A1840A5694B49B87799D1DED026AFBE517BDDB50184DF7958D3C51692102935` |
| OSIFOG/OSIFOG_Missao_Secreta_2026.pdf | `E9F266FE3BF16CD8354042C223708C8DA352066257A7C4E256E5B9CC8B57FBE1` |
| OSIFOG/OpenWind_File.csv | `B3624A4B769A3890CE9BE8E7EE2DADE0A5370D1D2ABAF1C56AC03C2B844BE588` |
| missions/osifog_l3_precision.json | `E9DA32F12675E6576D0E3078C36739D4144841D574F78305330A5AC04EA66291` |

## Test Baseline
- **Python**: 52 passed (test_osifog_engine_search, test_osifog_sweep, test_osifog_precision, test_osifog_falcon_contract, test_physical_geometry)
- **Rust library**: 137 passed
- **Rust bridge**: 17 passed
- **Rust integration**: 3 + 3 passed
- **Total**: 212 tests, 0 failures

## Reading Ledger

### Python Active Files
| File | Lines | Purpose | Active | Key Symbols |
|------|-------|---------|--------|-------------|
| osifog_sweep.py | 2490 | OR authority, XML gen, scoring, sweep | YES | `generate_ork`, `run_sim`, `score_official`, `validate_hard_constraints`, `save_simulated_ork`, `_get_anti_tumble_listener`, `_descent_alignment_diagnostic`, `_retro_burn_diagnostic` |
| osifog_precision.py | 1014 | Mission adapter, delay search, polish | YES | `optimize_physical_falcon`, `calibrate_genuine_landing`, `adaptive_delay_search`, `inspect_saved_submission`, `save_verified_submission`, `export_openearth_csvs` |
| osifog_engine_search.py | 1377+ | Unattended two-authority optimizer | YES | `_default_openrocket_evaluator`, `_landing_opportunity`, `_delay_candidates`, `_authority_recombinations`, `build_scenario_manifest`, `validate_scenario_manifest` |
| organic_loop.py | 1363+ | GA loop, Rust evaluator, CKG | YES | `evaluate_rust_population`, `run_rust_evaluator`, `mutate_ast`, `promote_candidates` |
| rocket_ast.py | 573 | AST schema, mutation, compilation | YES | `ASTNode`, `ASTCompiler`, `create_random_ast`, `sanitize_ast_for_openrocket` |
| physical_geometry.py | 350 | Cylinder collision, attachment | YES | `AxialCylinder`, `validate_cylinders`, `validate_attachment_paths`, `falcon_cluster_cylinders`, `falcon_ballast_rods` |
| rocket_forge.py | 488 | Motor DB, materials, legacy builder | PARTIAL | `MOTOR_DATABASE` (active), `RocketArchitect` (legacy) |
| mission_evolution.py | 128 | Phase-by-phase evolution engine | YES | `EvolutionEngine`, `bisect_transition` |
| ckg_memory.py | ~200 | Continuous Knowledge Graph | YES | `ContinuousKnowledgeGraph` |

### Rust Files
| File | Lines | Purpose | Active | Key Findings |
|------|-------|---------|--------|-------------|
| ast.rs | 1815 | AST parsing, evaluation, scoring | YES | Clean JSON contract, data-driven scoring table, correct constraint enforcement |
| bin/ast_eval.rs | 153 | CLI entry, JSONL serve | YES | Clean, well-structured |
| builder.rs | 310 | CG/margin calculation | YES | 5-phase exposed-stage margins, correct wet CG |
| geometry.rs | 216 | Type contracts | YES | Clean data definitions |
| mass_calculator.rs | 637 | Mass, CG, inertia | YES | Full principal inertia, correct Haack profile |
| mission_adapter.rs | 797 | Mission assembly, OR env | YES | **BUG**: hardcoded 0.4 kg dry_mass offset line 374 |
| barrowman.rs | 1662 | Full Barrowman aero | YES | Comprehensive: subsonic/transonic/supersonic CNa, CP, drag |
| motor_db.rs | 308 | .eng parser, mass model | YES | Impulse-weighted mass (correct) |
| sixdof.rs | 478 | Full 6-DOF dynamics | YES | Quaternion, TVC, wind-relative aero |
| runner.rs | 977 | Simulation loop | YES | RK4, staging, touchdown interpolation |
| axial.rs | 426 | 1-D axial screener | YES | SuperSpeed profile; single-motor limitation |
| wind.rs | 124 | Wind profile | YES | Linear interpolation; std_dev unused |
| stage.rs | 687 | Stage/motor data | YES | Multi-motor thrust, dynamic CG |
| mission.rs | 201 | Mission builder | YES | Clean |
| event.rs | 203 | Event detection | YES | Clean |
| aerodynamics.rs | 81 | Aero helpers | LEGACY | Duplicated in sixdof.rs |
| guidance.rs | 106 | Pitch guidance | PLACEHOLDER | Unused by production |
| json.rs | 266 | FlightSummary JSON | YES | Clean contract |
| xml_parser.rs | 699 | .ork XML parser | YES | Missing mass component bottom/middle methods |

### Mission and Config
| File | Lines | Purpose | Verified |
|------|-------|---------|----------|
| missions/osifog_l3_precision.json | 119 | Official scoring, constraints, search space | YES |
| handoff.md | 167 | Current authority state | YES (stale in places) |
| README.md | 211 | Project overview | YES |
| docs/engine/constrained-quality-diversity-roadmap.md | 158 | Target optimizer architecture | YES |

### Artifact Status
| Artifact | Classification | Score | Reason |
|----------|---------------|-------|--------|
| osifog_physical_839k_falcon.ork | QUARANTINED | 839,696 | Separation after apogee |
| osifog_850k_falcon.ork | QUARANTINED | N/A | Ballast/motor-mount collision |
| osifog_genuine_supported.ork | DIAGNOSTIC | N/A | No saved/reopened proof |
| osifog_supported_candidate.ork | DIAGNOSTIC | N/A | No saved/reopened proof |
| falcon_best.ork | DIAGNOSTIC | N/A | Superseded |
