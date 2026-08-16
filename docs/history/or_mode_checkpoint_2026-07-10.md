# OR-mode checkpoint — 2026-07-10

## Decision

OpenRocket 24.12 remains ground truth. Rust `openrocket` mode is the fast proxy.
HyperReal remains frozen until the OR-mode gate is accepted.

## Where work had stopped

Development stopped after a July 5 OpenRocket 23.09 five-seed result (about
1.78% mean absolute apogee error), followed by an incomplete July 6 migration
to OpenRocket 24.12. The validator, scripts, state files, motor provenance, and
physics proxy no longer described one reproducible authority contract. Cleanup
also moved scripts without repairing every import or operator command.

## Root causes repaired

- staging ignition order and aborted-simulation handling;
- launch environment parity (rod and wind);
- body-step pressure drag, Mach/AoA stability, dynamic CG, stack mass/inertia,
  aerodynamic torque, and OpenRocket pitch/yaw damping;
- nose-shape default parameters and fin instance scaling;
- removal of the unexplained `+0.4 kg` dry-mass adjustment;
- duplicate M2500T curve ambiguity by pinning the certified 24.12 digest;
- organic `double-wedge` mapping to OpenRocket's square fin cross-section;
- active diagnostics restored under `scripts/`, with authority hashes/seeds.

## Current evidence

Authoritative report: `designs/or_mode_sweep_or24_12_final_5seed/report.json`.

- OpenRocket jar SHA-256: `4959b72f52f5f607941e9722abbb7b7f0c4a38ebbbf84204a329db9f31c4f897`
- 5/5 simulations successful.
- Four mission-scale cases: 0.05%, 1.17%, 0.37%, and 0.90% absolute apogee error.
- Low-altitude case: 23.80 m absolute error (10.08% of a 236.07 m flight).
- All-case mean absolute apogee error: 2.51%; mean absolute Mach error: 0.0158.

Percentage error is misleading for the low-altitude case. The proxy is now
near parity for the intended high-power mission scale, but the remaining
23.8 m low-altitude discrepancy should stay visible as a separate regression.

## Next boundary

Do not tune HyperReal. First add a durable parity matrix spanning altitude
bands, motor duplicates, fin cross-sections, and one-to-three-stage stacks.
Accept OR mode only with both relative and absolute-error gates so small flights
cannot dominate the metric and large flights cannot hide large meter errors.
