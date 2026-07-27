# Corrections and Errata

## C1: Motor Burn Duration Confusion

**Original claim**: J510W burn time "5.84 s", K550W burn time "4.10 s"
**Why wrong**: Confused motor casing length (mm) with burn duration (s). J510W is 584mm long; its burn duration from the .eng curve is 2.500 s. K550W is 410mm long; its burn duration is 3.356 s.
**Corrected values** (from actual .eng files):

| Motor | Diameter (mm) | Length (mm) | Propellant (kg) | Loaded (kg) | Burn Time (s) | Total Impulse (Ns) |
|-------|---------------|-------------|-----------------|-------------|---------------|---------------------|
| J510W | 38 | 584 | 0.6620 | 1.0800 | 2.500 | ~1655 |
| K550W | 54 | 410 | 0.9197 | 1.4874 | 3.356 | ~2480 |
| H180W | 29 | 238 | 0.1210 | 0.2464 | 1.313 | ~281 |
| J360 | 38 | 419 | 0.4090 | 0.7092 | 2.130 | ~1140 |
| F50T | 29 | 98 | 0.0336 | 0.0898 | 1.430 | ~50 |

**Evidence**: Parsed directly from `l2_engine/motors/*.eng` RASP header fields.
**Documents updated**: 05_mass_motors_and_inertia.md, 00_executive_findings.md, 13_controlled_experiments.md, 16_implementation_roadmap.md, 17_urgent_osifog_path.md, research-summary.json

## C2: MOTOR_PROPELLANT_KG Approximation Error

**Original claim**: J510W propellant ≈ 0.310 kg (from osifog_sweep.py MOTOR_PROPELLANT_KG[16])
**Why wrong**: Hand-estimated value. Actual .eng header: 0.6620 kg.
**Corrected**: J510W propellant = 0.6620 kg (from .eng file header field 5).
**Impact**: The scoring fallback in `score_official()` uses these approximate values when `m_prop_kg_actual` is unavailable. For J510W, the approximation underestimates propellant by 53%.

## C3: Score Arithmetic Errors

**Original claim**: "touchdown position loss is 21.4k... With perfect altitude and zero speed, the score would be ~878k"
**Why wrong**: Did not subtract the propellant penalty from the ceiling.
**Corrected arithmetic** for the quarantined 839k artifact:
- Base: 900,000
- Apogee altitude penalty: -2.88
- Apogee horizontal penalty: -172.88
- Touchdown position penalty: -21,430.55
- Touchdown speed penalty: -3,260.14
- Propellant penalty: -35,437.50
- **Total**: 839,696.05

**Largest absolute penalty**: Propellant (35,437.50)
**Largest realistically reducible penalty**: Touchdown speed (3,260.14) — reducible to ~500 by achieving <1 m/s landings
**Second largest reducible**: Touchdown position (21,430.55) — reducible by optimizing launch azimuth/angle

**Score ceilings**:
- With zero touchdown speed AND zero touchdown position, same propellant and apogee: 842,959.34
- With zero touchdown speed, zero touchdown position, AND zero apogee error: 842,962.22
- To exceed 850,000: need to reduce propellant consumption from 4.725 kg to ≤3.93 kg (saves 5,963 points) AND reduce touchdown penalties to near zero.

**Trade-off**: Propellant reduction requires lower-impulse motors, which may not achieve 3000m apogee. Touchdown optimization requires precise delay timing and favorable descent attitude. These objectives may conflict.

**Documents updated**: 00_executive_findings.md, 17_urgent_osifog_path.md

## C4: Topology Stability Overclaim

**Original claim**: "3+1 motor cage topology produces unstable exposed sustainer" (CONFIRMED)
**Why wrong**: Only 32 candidates across 4 fin-count families were tested, all with forward grid fins + aft main fins. Body transitions, aft-only fins, different radii/lengths, broader ballast distributions, and other passive topologies were not tested. Rust/OpenRocket parity is not established.
**Corrected**: "The tested constrained 3+1 design families failed the current exposed-sustainer stability screen." The topology itself is not proven inherently unstable.
**Confidence**: DOWNGRADED from CONFIRMED to HIGH (based on narrow evidence)
**Documents updated**: 00_executive_findings.md, 14_risk_register.md, 17_urgent_osifog_path.md

## C5: Central Motor Torque Overclaim

**Original claim**: "The central retro motor creates asymmetric torque during descent"
**Why wrong**: A perfectly centered, coaxial motor whose thrust line passes through the instantaneous CG does not create a lever-arm torque. The claim lacked a measured moment arm and force.
**Corrected**: The central motor's thrust application point, the instantaneous CG location, and any lateral CG offset determine whether torque exists. This requires measurement from the actual generated geometry, not assumption.
**Confidence**: DOWNGRADED from MEDIUM to UNVERIFIED
**Documents updated**: 18_adversarial_review.md

## C6: 6-DOF "Production-Grade" Overclaim

**Original claim**: "6-DOF is real and production-grade"
**Why wrong**: "Production-grade" implies validated accuracy. The Rust 6-DOF has not been validated against OpenRocket for: CP/CG/static-margin parity, wind-vector parity, frame/sign conventions, branch state inheritance, powered-descent accuracy, high-angle-of-attack behavior, transonic behavior, or motor/mass parity.
**Corrected**: "The Rust 6-DOF implementation includes quaternion dynamics, wind-relative aerodynamics, Mach/AOA-dependent normal force, and RK4 integration. Validation status: NOT ESTABLISHED against OpenRocket authority."
**Confidence**: Implementation CONFIRMED, accuracy UNVERIFIED
**Documents updated**: 06_aerodynamics_stability_and_6dof.md, 00_executive_findings.md

## C7: Stale _motor_burn_time() Values

**Original claim**: _motor_burn_time() was described as "stale approximation table"
**Status**: CONFIRMED — the table uses hand-estimated values. For J510W the table says 4.5s but the actual .eng curve shows 2.500s. For K550W the table says 3.2s but actual is 3.356s.
**Impact**: Delay calibration uses wrong burn durations, affecting landing timing.
**Documents updated**: 05_mass_motors_and_inertia.md

## C8: dry_mass Offset

**Original claim**: mission_adapter.rs:374 adds hardcoded 0.4 kg
**Status**: CONFIRMED — `let dry_mass = mass + 0.4;` on line 374
**Impact**: Systematic mass error for organic-evolution candidates
**Documents updated**: 05_mass_motors_and_inertia.md
