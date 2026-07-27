# Revised Executive Findings (v2)

**Audit date**: 2026-07-20 (revision)
**Status**: NO LEGAL BRANCH — DIAGNOSTIC ONLY

---

## Corrected Top Blockers

| # | Blocker | Severity | Confidence | Evidence |
|---|---------|----------|------------|----------|
| 1 | All 32 tested sustainer candidates failed 1.5-cal stability gate | BLOCKER | CONFIRMED | Gate 4 search data |
| 2 | Rust/OpenRocket phase-margin parity never established | BLOCKER | CONFIRMED | No parity fixture exists |
| 3 | Touchdown penalties are the binding score constraint | HIGH | CONFIRMED | Score arithmetic (Experiment D) |
| 4 | MOTOR_PROPELLANT_KG has wrong values for key motors | HIGH | CONFIRMED | .eng file comparison (Experiment C) |
| 5 | _motor_burn_time() uses stale approximations | HIGH | CONFIRMED | .eng curve comparison |
| 6 | 0.4 kg dry_mass offset in mission_adapter.rs | MEDIUM | CONFIRMED | Source code inspection |

## Corrected Topology Conclusion

**Original**: "3+1 motor cage topology produces unstable exposed sustainer" (CONFIRMED)
**Revised**: "The tested constrained 3+1 design families failed the current exposed-sustainer stability screen." The topology itself is not proven inherently unstable. Only 32 candidates across 4 fin-count families were tested, all with forward grid fins + aft main fins. Body transitions, aft-only fins, different radii/lengths, broader ballast distributions, and other passive topologies were not tested. Rust/OpenRocket stability parity is not established.
**Confidence**: HIGH (narrow evidence), not CONFIRMED

## Corrected 6-DOF Assessment

**Original**: "6-DOF is real and production-grade"
**Revised**: "The Rust 6-DOF implementation includes quaternion dynamics, wind-relative aerodynamics, Mach/AOA-dependent normal force, and RK4 integration. Validation status: NOT ESTABLISHED against OpenRocket authority."
**Implementation**: CONFIRMED (code inspection)
**Accuracy**: UNVERIFIED (no parity fixture)

## Corrected Central Motor Torque Claim

**Original**: "The central retro motor creates asymmetric torque during descent" (MEDIUM)
**Revised**: "A perfectly centered, coaxial motor whose thrust line passes through the instantaneous CG does not create a lever-arm torque. Whether the actual generated geometry has off-axis thrust requires measurement from the specific component positions and CG location." (UNVERIFIED)

## Corrected Score Arithmetic

**Largest absolute penalty**: Propellant (35,437.50 points)
**Largest realistically reducible penalty**: Touchdown speed (3,260.18 points) — reducible to ~500 by achieving <1 m/s landings
**Binding constraint for 850k**: Touchdown penalties (position + speed = 24,690.73 points total)

**Score ceilings** (from quarantined 839k artifact data):
- With zero touchdown penalties: 864,387
- With zero all non-propellant penalties: 864,563
- Current: 839,696

**To exceed 850k**: Reduce combined touchdown penalties from 24,691 to ≤21,435 (reduce by ~3,256 points). This means reducing mean touch speed from 2.55 m/s to ~2.13 m/s AND reducing mean touch displacement from ~73 m to ~68 m, while keeping current propellant and apogee.

## Corrected Motor Data

| Motor | Burn Time (s) | Propellant (kg) | Python Approx | Error |
|-------|---------------|-----------------|---------------|-------|
| J510W | 2.500 | 0.6620 | 0.310 | 113% |
| K550W | 3.356 | 0.9197 | 0.620 | 48% |
| H180W | 1.313 | 0.1210 | 0.130 | 7% |
| J360 | 2.130 | 0.4090 | 0.290 | 41% |

## Confirmed Bugs (all with source-code proof)

1. `mission_adapter.rs:374` — `let dry_mass = mass + 0.4;` (CONFIRMED)
2. `osifog_sweep.py:2077-2110` — Stale burn-time table (CONFIRMED by .eng comparison)
3. `osifog_sweep.py:145-194` — Wrong propellant masses for J510W, K550W, J360 (CONFIRMED by .eng comparison)

## Experiments Completed

| Experiment | Status | Key Finding |
|-----------|--------|-------------|
| A: Anti-tumble invariance | PASS | Zero differences between with/without listener |
| C: Motor data parity | PASS | Corrected 4 motor values; naming mismatch found |
| D: Score cross-validation | PASS | Touchdown penalties are the binding constraint |
| E: Wind parity | PASS | 28-level profile, correct AGL/radian/from conventions |

## Experiments Not Run

| Experiment | Status | Reason |
|-----------|--------|--------|
| B: Rust/OpenRocket parity | BLOCKED | Requires fixing dry_mass offset first |
| F: Branch identity | NOT RUN | Requires parity fixture |
| G: Landing calibration | NOT RUN | Requires parity fixture |

## Optimizer Recommendation (Revised)

**Original**: "Constrained MAP-Elites with 250,000-cell archive"
**Revised**: "The MAP-Elites recommendation is plausible but not yet empirically justified. No offline replay or comparative evaluation has been performed. The 250,000-cell archive may be too sparse. Recommend running a small empirical comparison before committing to an architecture."
**Confidence**: LOW (no empirical evidence)

## Urgent Path (Revised)

1. Fix motor/mass data (replace approximations with .eng values)
2. Fix 0.4 kg dry_mass offset
3. Establish Rust/OpenRocket parity
4. Validate anti-tumble invariance (DONE — confirmed)
5. Test a diverse stability topology matrix (expand search space)
6. Validate one powered landing branch
7. Recombine stages
8. Optimize score
