# Native PodSet Physics Design

## Outcome

Extend the existing organic AST and Rust proxy so an external radial assembly is data, not a mission-specific template. The first production use is the OSIFOG two-stage vehicle where each stage has three permanent side pods containing ascent motors and one central delayed retro motor. OpenRocket remains the authority; Rust must be fast enough to search geometry and millisecond ignition windows before sequential authority promotion.

## Chosen shape

`POD` and `STRAP_ON` are composite AST nodes. Their `children` contain the same `NOSE_CONE`, `BODY_TUBE`, `FIN_SET`, `MOTOR_MOUNT`, `BALLAST`, and `CLOSE_BODY` instructions used by an inline stage. The node also carries instance count, radial offset, angular offset, axial offset, and an optional calibrated aerodynamic-interference factor. `STRAP_ON` is accepted only as a permanent assembly for this mission; separable radial stages fail closed because they would require additional flight branches.

The Rust geometry model stores radial assemblies as compact records and lifts their motor mounts into the stage's existing multi-motor path with explicit instance poses. This keeps the hot path contiguous and avoids a recursive runtime object graph. Existing inline ASTs remain byte-for-byte compatible.

## Physics contract

- Mass properties are accumulated in three dimensions. Symmetric pods naturally cancel lateral CG; asymmetric assemblies produce non-zero lateral CG.
- Each motor instance has an axial/radial position and thrust direction. The 6-DOF solver sums force and `r x F` for every active motor independently.
- Principal inertias include each radial assembly and motor through the parallel-axis theorem. Motor curves remain independent, so a 0.1 s early flame-out automatically produces the correct unbalanced wrench.
- Pod aerodynamic interference is explicit data. Absent calibration, a conservative geometry-derived factor is used and labeled proxy-only; OpenRocket samples may override it without changing code.

## Safety and failure handling

The parser rejects non-positive counts, invalid radii, nested radial assemblies, unsupported separable strap-ons, missing children, motor/pod fit violations, overlapping core/pod envelopes, non-finite calibration values, and physically impossible minimum dimensions. The autonomous loop writes atomic checkpoints plus a heartbeat/alert record, resumes completed authority evaluations, and stops on repeated engine failures rather than silently burning compute.

## Verification and budgets

- No new dependencies.
- Existing inline results must retain their regression tests.
- Unit tests cover symmetric lateral-CG cancellation, asymmetric CG, radial inertia, symmetric torque cancellation, asymmetric flame-out torque, and AST parsing.
- End-to-end batch smoke must parse a real 3+1 AST and return finite telemetry.
- Release throughput target: at least 100 completed proxy simulations/second on the current host for the documented smoke batch, with Rayon capped to 70% of logical CPUs.
- OpenRocket parity is measured on promoted candidates; no Rust-only result is called competition-valid.

## Implemented and measured (2026-07-21)

- `POD`/permanent `STRAP_ON` parsing, OpenRocket sanitation/XML emission, radial motor poses, 3-D CG, diagonal inertia, per-motor wrench summation, flame-out behavior, pod fin authority, and calibrated frontal-area drag are live.
- Staging is driven by ascent-motor completion. A detached branch retains its delayed retro motor and unburned propellant and is propagated in an independent one-stage mission.
- Mission-clock `launch` delays are converted to the stage activation clock deterministically; stage-relative events remain relative.
- The population screen uses full 6-DOF at 25 ms, a legal subsonic stability grid, 250 ms event-aware unpowered coast steps, and immediate termination when a candidate crosses the immutable Mach 0.95 gate. Every surviving candidate still propagates both branches to touchdown.
- Measured release throughput on this host: **110.80 candidates/s** for 1,000 varied full-mission PodSet candidates (64 fully propagated, 936 terminated at the hard Mach crossing). A surviving candidate differed from balanced 20 ms evaluation by **0.00195% apogee** in the recorded spot check.
- End-to-end authority smoke: 200 Rust candidates plus two balanced promotions and two OpenRocket 24.12 runs completed without evaluator errors. The best authority result reached 2,902.78 m but was rejected as supersonic; this is a search result, not a legal design.
- `health.json`, `alert.json`, `checkpoint.json`, and `autopilot.json` are written atomically. The autopilot retries with a new deterministic seed and stops after three consecutive failures with an operator-readable alert.
