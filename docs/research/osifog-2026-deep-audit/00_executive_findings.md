# Executive Findings — OSIFOG Level 3 Deep Audit

**Audit date**: 2026-07-20
**Branch**: `research/osifog-2026-deep-audit`
**Status**: `NO LEGAL BRANCH — DIAGNOSTIC ONLY`
**Authority artifact**: None active (839,696 quarantined)

---

## Top 10 Blockers

| # | Blocker | Severity | Confidence |
|---|---------|----------|------------|
| 1 | No legal sustainer branch — all 32 tested candidates failed 1.5-cal exposed-sustainer ascent gate | BLOCKER | CONFIRMED |
| 2 | Mach proxy blocks diagnostic parity — Rust/OpenRocket phase-margin comparison never completed | BLOCKER | CONFIRMED |
| 3 | 3+1 motor cage topology produces unstable exposed sustainer — 3 ascent motors burning out leave CG shift that no tested fin family compensates | BLOCKER | CONFIRMED |
| 4 | No powered landing validation at OpenRocket authority — landing opportunity metric uses stale alignment traces, never confirmed with real powered descent | CRITICAL | HIGH |
| 5 | `_motor_burn_time()` uses hardcoded approximation table, not actual .eng curve integration — stale delay calculations | CRITICAL | CONFIRMED |
| 6 | `MOTOR_PROPELLANT_KG` dictionary uses approximate values — consumed propellant in scoring formula may be wrong | HIGH | CONFIRMED |
| 7 | `mission_adapter.rs:374` adds hardcoded `+0.4 kg` dry_mass offset — systematic mass error for organic-evolution candidates | HIGH | CONFIRMED |
| 8 | OpenRocket phase parity not established — Rust exposed-stage margins vs OpenRocket Barrowman never compared on same fixture | HIGH | CONFIRMED |
| 9 | Anti-tumble pre-event trajectory equivalence not empirically proven — listener side-effect risk unquantified | MEDIUM | HIGH |
| 10 | Search architecture uses scalar GA — cannot preserve behaviorally distinct near-solutions, collapses onto dominant topology families | MEDIUM | CONFIRMED |

## Most Likely Root Cause for No Legal Sustainer Branch

**The 3+1 motor cage topology with current motor/ballast distribution is inherently unstable after booster separation.** When the three ascent motors (J510W × 3) burn out and the booster separates, the exposed sustainer carries:
- Three empty motor mount tubes (dead mass, aft)
- One wet retro motor (K550W, ~0.62 kg propellant + case, aft-center)
- Nose ballast (1.26 kg steel bulkhead, forward)
- Fins

The CG moves aft as propellant depletes, but the CP also shifts. The 1.5-caliber margin requires the CP to remain 1.5 body diameters aft of CG throughout ascent. With the current geometry (74mm diameter), this means 111mm of margin. The four fin families tested (32 candidates) could not achieve this with any tested combination of nose ballast, aft ballast, fin root, and fin height.

**The search space in the mission manifest may be too narrow** — the fin height range [0.33, 0.35] m for stage 1 and the ballast constraints may not span a stable configuration.

## What the System Actually Does Today

1. **Python orchestration** (`osifog_sweep.py`, `osifog_precision.py`, `osifog_engine_search.py`): Generates ORK XML, runs OpenRocket 24.12 via JPype, scores with official formula, searches delay/azimuth/ballast space
2. **Rust proxy** (`l2_engine/`): Real 6-DOF simulation, Barrowman aerodynamics, motor curve integration, mass/CG/inertia, phase-aware margins — fast screening only
3. **AST layer** (`rocket_ast.py`): Flat node-list representation of rocket topology, mutation, sanitization, compilation to ORK XML
4. **Physical geometry** (`physical_geometry.py`): Cylinder-based collision detection with attachment path validation
5. **Search** (`organic_loop.py`): GA with Rust screening → OpenRocket authority promotion, CKG calibration
6. **Anti-tumble**: Official JavaScript listener suppressing TUMBLE events, serialized into ORK XML

## Authority Classification

| Component | Classification |
|-----------|---------------|
| Official PDFs (ProjetoFalcon, MissaoSecreta) | OFFICIAL_RULE |
| OpenWind_File.csv | OFFICIAL_RULE |
| OpenRocket 24.12 flight simulation | OPENROCKET_AUTHORITY |
| Rust 6-DOF proxy | RUST_PROXY |
| Python scoring formula | RUST_PROXY (mirrors official) |
| Physical geometry checks | RUST_PROXY |
| Anti-tumble script | OFFICIAL_RULE |
| Mission manifest JSON | RUST_PROXY |
| Historical artifacts (839k, 850k) | HISTORICAL / QUARANTINED |

## Controlled Simulation Results

| Experiment | Status | Key Finding |
|-----------|--------|-------------|
| Anti-tumble pre-event invariance | NOT RUN | Pending |
| Phase-margin Rust/OR parity | BLOCKED | Mach proxy rejected candidate before comparison |
| Branch identity/event ordering | NOT RUN | Pending |
| Wind parity | NOT RUN | Pending |
| Landing-screen calibration | PARTIAL | 7 full OR cycles, 0 legal landings |
| Motor/mass parity | NOT RUN | Pending |

## Score Potential Analysis

The quarantined 839,696 artifact had:
- Apogee: 3000.031 m (loss: 2.88 points)
- Apogee horizontal: E=-2.48, N=+2.15 (loss: 172.88 points)
- Touchdown position: mean E=+64.22, N=+81.18 (loss: 21,430.55 points)
- Touchdown speed: mean 2.55 m/s (loss: 3,260.14 points)
- Propellant: 4.725 kg (loss: 35,437.50 points)

The touchdown position loss (21.4k) is the dominant term. Even with perfect apogee and zero-speed landings, the score would be ~878k. To reach 850k+, both touchdown displacement and speed must be minimized simultaneously.

## Confirmed Fixes Needed

1. Remove hardcoded `+0.4 kg` dry_mass offset in `mission_adapter.rs:374`
2. Replace `_motor_burn_time()` approximation with actual .eng curve integration
3. Replace `MOTOR_PROPELLANT_KG` approximate values with exact `.eng` header extraction
4. Establish Rust/OpenRocket phase-margin parity on a shared fixture
5. Run anti-tumble pre-event equivalence experiment
6. Expand the search space for exposed-sustainer stability (fin geometry, ballast distribution)
7. Migrate from scalar GA to MAP-Elites with behavior descriptors

## Optimizer Recommendation

**Constrained MAP-Elites** with:
- Behavior descriptors: min phase-aware margin, tail-first window duration, vertical opposition fraction, normalized CG
- Per-cell CMA-ES for continuous geometry refinement
- Multi-fidelity promotion: Rust proxy → OpenRocket authority
- Uncertainty-aware calibration with divergence model

## Shortest Credible Path

### One Legal Sustainer Branch (1-3 days)
1. Expand fin height search space to [0.20, 0.60] m for stage 1
2. Add ballast mass range [0.5, 3.0] kg for nose
3. Run targeted search with relaxed touchdown constraint (accept >5 m/s initially)
4. Validate one candidate at OpenRocket authority

### One Legal Full Vehicle (3-7 days)
1. Complete the above
2. Calibrate powered landing delays for both stages
3. Run trajectory polish
4. Save/reopen verification

### Saved/Reopened Result Above 850k (7-14 days)
1. Complete the above
2. Optimize touchdown displacement via launch azimuth/angle
3. Minimize propellant consumption
4. Full saved/reopen/OpenEarth export pipeline

## Do Not Do

- Never reactivate quarantined 839,696 artifact
- Never claim score from live telemetry or unsaved simulation
- Never use diagnostic scenario results as authority
- Never bypass the fail-closed anti-tumble validation
- Never use stale `_motor_burn_time()` values for final delay calculation
- Never skip the saved/reopened verification step
