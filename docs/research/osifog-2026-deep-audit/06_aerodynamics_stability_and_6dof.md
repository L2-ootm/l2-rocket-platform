# Aerodynamics, Stability, and 6-DOF Physics

## Rust 6-DOF Assessment

**Verdict: Real and production-grade.** The 6-DOF implementation includes:
- Full quaternion-based attitude dynamics (Euler's equation, quaternion kinematics)
- TVC gimbal with thrust vector rotation through body-to-inertial transform
- Mach/AOA-dependent normal force via precomputed stability tables
- Nonlinear pitch/yaw aerodynamic damping capped by corrective moment
- Wind-relative airspeed for all aero calculations
- Launch guide constraint enforcement

**Limitation**: The `NoOpController` means the 6-DOF rotational dynamics are effectively passive (weathercocking only). No active attitude control or TVC guidance is engaged in the production scoring path.

## Barrowman Aerodynamics

**Verdict: Comprehensive.** The `barrowman.rs` (1662 lines) implements:
- Nosecone CP via volume integration; CNa = 2.0
- Fin geometry via 40-slice strip integration
- Subsonic/transonic/supersonic CNa with body-fin and fin-count interference
- Fin CP with Mach-dependent fifth-order polynomial shift
- Body lift (Galejs multiplier K=1.1)
- Full drag decomposition: friction, nose pressure (von Karman table), base drag, fin pressure/base, fin wave drag, symmetric step pressure
- 12-point Mach drag table

**Gaps**:
1. Transonic fin CNa uses linear blend instead of OpenRocket's polynomial interpolator
2. OpenRocket 24.12 has a more sophisticated compressibility model for body lift

## Phase-Aware Stability Margins

The Rust `builder.rs::exposed_stage_phase_margins()` evaluates at 5 phases:
1. **Separation/ignition** — initial exposed-sustainer state
2. **25% burn** — early powered ascent
3. **50% burn** — maximum dynamic pressure region
4. **Burnout** — motor depleted
5. **Post-burn coast** — ballistic ascent to apogee

Each phase uses impulse-weighted motor mass depletion. The margin formula is `(CP - CG) / reference_diameter` in calibers.

**Known limitation**: The phase margins are computed from a static aerodynamic model (Barrowman) at discrete Mach/AOA snapshots. They do not account for:
- Angle-of-attack excursions during weathercocking
- Transient ignition dynamics
- Wind-induced sideslip

## OpenRocket Parity Status

**NOT ESTABLISHED.** The Rust and OpenRocket aerodynamic models have not been compared on the same candidate. The Mach proxy blocked the attempted parity comparison by rejecting the candidate before OpenRocket could provide a reference.

**Required fixtures for parity**:
1. **Stable throughout**: Candidate with margin > 2.0 cal at all phases
2. **Margin crossing**: Candidate margin < 1.5 cal at one phase but > 1.5 at others
3. **Unstable throughout**: Candidate margin < 1.5 cal at all phases

Each fixture must be compared at matching:
- Mach values (0.3, 0.5, 0.9)
- Body diameter (reference length)
- Fin geometry (freeform points)
- Mass state (wet, 50% burn, dry)

## Descent Alignment Physics

The `_descent_alignment_diagnostic()` in osifog_sweep.py computes:
- `alignment_q = -cosine(body_axis · velocity_vector)` — positive means tail-first
- `vertical_alignment_q = sin(theta) if vz < 0 else -sin(theta)` — vertical component

The metric `alignment_q >= 0.5` defines "tail-first window" where the body nose is within ~60° of the velocity vector.

**Key insight**: The alignment depends on:
1. Fin normal force at high angle of attack (not captured by linear Barrowman)
2. CG/CP offset driving passive weathercocking
3. Aerodynamic damping during tumble
4. Body cross-section drag symmetry

**Gap**: The alignment trace is computed from the free-descent (no motor) simulation. When the retro motor fires, the thrust vector creates additional torque that may change the alignment. This is the "powered descent orientation" gap.

## Tumble Physics

The anti-tumble listener suppresses the TUMBLE event in OpenRocket. Physically, tumble occurs when:
- The vehicle's angular rate exceeds a threshold
- The aerodynamic restoring moment can no longer maintain stability
- The vehicle begins to rotate chaotically

For the OSIFOG mission, tumble is expected after motor burnout when:
- The motor is depleted (no thrust vector)
- The vehicle may be in a low-stability configuration
- Gravity and residual aerodynamic forces dominate

**The search for tail-first descent windows relies on the vehicle naturally achieving a tail-first attitude during descent.** This is a passive process driven by:
1. CG position (aft ballast helps)
2. Fin geometry (larger aft fins provide more restoring moment)
3. Body shape (axisymmetric drag symmetry)
4. Initial conditions at separation (angular rate, orientation)

## Mach Proxy Concerns

The Rust axial model (`axial.rs`) uses only the first motor's curve for multi-motor stages. For the 3+1 cluster, this means:
- The axial screener sees one motor's thrust instead of three
- The predicted apogee is lower than actual
- The predicted Mach is lower than actual
- **Result**: False negatives (candidates rejected that would pass OpenRocket)

**Mitigation**: The `Balanced` and `AuthorityHeavy` profiles use the full 6-DOF with correct multi-motor thrust summation.
