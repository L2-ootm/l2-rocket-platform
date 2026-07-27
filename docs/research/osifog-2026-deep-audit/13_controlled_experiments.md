# Controlled Experiments

## Experiment 1: Anti-Tumble Pre-Event Invariance

**Hypothesis**: The anti-tumble listener does not mutate simulation state before the TUMBLE event.
**Scenario**: STAGE_FREE_DESCENT_DIAGNOSTIC
**Independent variables**: Listener present (yes/no)
**Controlled variables**: Same candidate, same seed, same ORK XML, same JVM
**Authority level**: DIAGNOSTIC ONLY
**Success criterion**: All pre-TUMBLE samples match to machine precision (relative tolerance 1e-12)
**Failure criterion**: Any pre-TUMBLE sample differs by more than 1e-9 in position, velocity, or orientation
**Budget**: 2 OpenRocket simulations
**Artifact path**: `docs/research/osifog-2026-deep-audit/experiments/antitumble_invariance.json`

### Procedure
1. Generate a deterministic ORK for the current authority candidate
2. Run simulation with listener → save all branch telemetry
3. Run simulation without listener → save all branch telemetry
4. Find first TUMBLE event time in listener-off run
5. Compare all samples before that time
6. Verify listener-on run continues past TUMBLE to ground contact

## Experiment 2: Phase-Margin Rust/OpenRocket Parity

**Hypothesis**: Rust exposed-stage margins agree with OpenRocket Barrowman margins within 0.1 calibers.
**Scenario**: EXPOSED_SUSTAINER_ASCENT
**Independent variables**: Engine (Rust vs OpenRocket)
**Controlled variables**: Same geometry, same motor curves, same mass state, same Mach values
**Authority level**: RUST_PROXY vs OPENROCKET_AUTHORITY
**Success criterion**: Margin agreement < 0.1 cal at all 5 phases
**Failure criterion**: Margin disagreement > 0.2 cal at any phase
**Budget**: 1 Rust evaluation + 1 OpenRocket simulation
**Artifact path**: `docs/research/osifog-2026-deep-audit/experiments/parity_fixture.json`

### Procedure
1. Fix dry_mass offset (Task U1)
2. Select a candidate near the 1.5-cal boundary
3. Generate ORK XML
4. Run OpenRocket simulation, extract Barrowman margins at 5 phases
5. Run Rust evaluator with matching parameters, extract phase margins
6. Compare at each phase
7. Document discrepancies with root cause analysis

## Experiment 3: Motor Curve Integration Comparison

**Hypothesis**: Rust and Python motor curve integration produce the same total impulse and mass loss.
**Scenario**: N/A (offline comparison)
**Independent variables**: Motor (all 38 motors in database)
**Controlled variables**: Same .eng file, same integration method (trapezoidal)
**Authority level**: RUST_PROXY
**Success criterion**: Total impulse agreement < 0.1 Ns; mass loss agreement < 0.001 kg
**Failure criterion**: Disagreement exceeds thresholds
**Budget**: 38 comparisons (one per motor)
**Artifact path**: `docs/research/osifog-2026-deep-audit/experiments/motor_integration.json`

## Experiment 4: Wind Parity

**Hypothesis**: Rust and OpenRocket wind vectors agree at selected altitudes.
**Scenario**: OFFICIAL_FULL_MISSION
**Independent variables**: Altitude (0, 100, 500, 1000, 2000, 3000 m)
**Controlled variables**: Same wind profile, same interpolation method
**Authority level**: RUST_PROXY vs OPENROCKET_AUTHORITY
**Success criterion**: Wind vector agreement < 0.1 m/s at all altitudes
**Failure criterion**: Disagreement > 0.5 m/s at any altitude
**Budget**: 6 altitude comparisons
**Artifact path**: `docs/research/osifog-2026-deep-audit/experiments/wind_parity.json`

## Experiment 5: Landing Screen Calibration

**Hypothesis**: The landing opportunity metric correctly predicts whether a powered landing is feasible.
**Scenario**: POWERED_STAGE_LANDING_VALIDATION
**Independent variables**: Ignition delay (sweep around predicted optimum)
**Controlled variables**: Same candidate, same motor, same free-descent trajectory
**Authority level**: OPENROCKET_AUTHORITY
**Success criterion**: Metric's "usable" prediction matches OpenRocket's actual landing speed < 5 m/s
**Failure criterion**: Metric predicts "usable" but landing > 5 m/s, or vice versa
**Budget**: 20 OpenRocket simulations (10 per stage)
**Artifact path**: `docs/research/osifog-2026-deep-audit/experiments/landing_calibration.json`

## Experiment 6: Expanded Stability Search

**Hypothesis**: At least one configuration in the expanded search space passes the 1.5-cal gate.
**Scenario**: EXPOSED_SUSTAINER_ASCENT
**Independent variables**: Fin height [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50], nose ballast [0.5, 1.0, 1.5, 2.0], fin root [0.15, 0.18, 0.20, 0.25]
**Controlled variables**: Same motor pair, same body diameter, same cluster config
**Authority level**: RUST_PROXY screening → OPENROCKET_AUTHORITY validation
**Success criterion**: At least one candidate with margin ≥ 1.5 cal in OpenRocket
**Failure criterion**: All candidates fail in OpenRocket
**Budget**: 140 Rust evaluations + 10 OpenRocket validations
**Artifact path**: `docs/research/osifog-2026-deep-audit/experiments/expanded_stability.json`

## Experiment 7: Score Formula Cross-Validation

**Hypothesis**: Python and Rust score formulas produce the same result for the same flight data.
**Scenario**: OFFICIAL_FULL_MISSION
**Independent variables**: Flight data (from saved simulation)
**Controlled variables**: Same metrics, same formula coefficients
**Authority level**: RUST_PROXY
**Success criterion**: Score agreement within 1 point (floating-point tolerance)
**Failure criterion**: Score disagreement > 10 points
**Budget**: 1 comparison
**Artifact path**: `docs/research/osifog-2026-deep-audit/experiments/score_crossvalidation.json`
