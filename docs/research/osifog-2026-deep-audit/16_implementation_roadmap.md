# Implementation Roadmap

## Urgent OSIFOG Roadmap

### Day 1: Bug Fixes + Parity Foundation
| Task | Why Blocking | Effort | Proof of Done |
|------|-------------|--------|---------------|
| Remove 0.4 kg dry_mass offset | Systematic mass error | 30 min | cargo test passes |
| Replace _motor_burn_time() | Stale delay calculations | 30 min | Values match .eng curves |
| Replace MOTOR_PROPELLANT_KG | Approximate propellant masses | 30 min | Values match .eng headers |
| Create parity fixture | Cannot trust Rust proxy without validation | 3 hours | Test shows <0.1 cal discrepancy |

### Day 2: Search Space Expansion
| Task | Why Blocking | Effort | Proof of Done |
|------|-------------|--------|---------------|
| Expand fin height range to [0.20, 0.50] | Current range too narrow | 1 hour | Manifest updated, tests pass |
| Expand nose ballast to [0.5, 2.0] kg | Need more CG forward options | 1 hour | Manifest updated, tests pass |
| Add aft-only fin configuration | Forward fins may hurt descent | 2 hours | Test generates valid candidate |

### Day 3-4: Targeted Search
| Task | Why Blocking | Effort | Proof of Done |
|------|-------------|--------|---------------|
| Run expanded stability search | Find ascent-legal candidates | 4 hours | At least one passes 1.5-cal |
| Calibrate landing delays | No legal landings exist | 4 hours | Both stages <5 m/s |
| Run trajectory polish | Minimize touchdown displacement | 2 hours | Score improvement |

### Day 5-7: Authority Verification
| Task | Why Blocking | Effort | Proof of Done |
|------|-------------|--------|---------------|
| Save/reopen verification | Submission requires saved artifact | 2 hours | Saved file matches live |
| OpenEarth CSV export | Submission requires trajectory data | 1 hour | CSVs valid |
| Score optimization | Push toward 850k | 4 hours | Score >840k |

## Long-Term L2 Engine Roadmap

### Immediate Corrections (Week 1)
1. Remove 0.4 kg dry_mass offset
2. Replace stale approximations with data-driven values
3. Create parity fixture suite
4. Anti-tumble invariance test

### Architectural Refactors (Weeks 2-4)
1. Unify scoring formula (single source: mission JSON)
2. Unify physical geometry (Python + Rust → shared schema)
3. Add body transition support to AST
4. Add exhaust volume checking

### Optimizer Migration (Weeks 4-8)
1. Implement MAP-Elites archive
2. Add behavior descriptor extraction
3. Implement per-cell CMA-ES
4. Port CKG to SQLite

### Data Store Migration (Weeks 6-10)
1. CKG → SQLite with context-specific calibration
2. Result store → SQLite with full audit trail
3. Evaluation cache → persistent disk cache

### Performance Work (Weeks 8-12)
1. Profile and optimize Rust batch scoring
2. Add OpenRocket JVM connection pooling
3. Implement lazy motor loading
4. Cache wind profile interpolation

### Advanced Physics (Weeks 12-16)
1. Powered descent simulation in Rust
2. Body transition aerodynamics
3. Multi-body separation dynamics
4. Stochastic wind model
