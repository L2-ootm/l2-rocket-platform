# PodSet Authority Contract Recovery

## Decision

The previous 800k campaign is not resumed in place. Its candidates were
compiled with an invalid ascent ignition mapping and a proxy that omitted
physical support mass. It remains preserved for diagnosis, but none of its
scores or calibrations seed the replacement campaign.

The replacement uses one fail-closed contract from generated OpenRocket XML
through the campaign manifest: booster mains ignite at launch, sustainer mains
ignite at booster burnout, official retro motors use delayed launch ignition,
and diagnostic-disabled retro motors use OpenRocket's `never` event.

## Physical parity

- Booster ballast is three density-derived steel rods outside the central
  retro mount, with validated clearances and axial fit.
- Core and pod envelopes are re-derived after sampling and crossover.
- Rust accumulates radial ballast, motor, ring, and pylon masses at their real
  stations, including parallel-axis inertia.
- Pod noses and bodies count toward actual competition height.
- OpenRocket remains the only competition authority; Rust is a search proxy.

## Certification

A champion must be legal in fresh OpenRocket runs at 50, 20, 10, 5, and 1 ms.
The final two runs must converge within bounded apogee, Mach, landing speed,
landing position, and score deltas. The 1 ms file is then saved and reopened
five times; all replays must be legal and byte-equivalent at the metrics
digest level before promotion.

## Idempotency and recovery

The campaign identity hashes the mission, wind, compiler, scoring and
certification modules, watchdog, AST, OpenRocket JAR, and release Rust binary.
A lease prevents two owners, atomic checkpoints resume completed work, stale
leases are quarantined, and the watchdog restarts an absent process within a
bounded budget before emitting an operator-attention alert.

## Evidence

- Python contract/geometry suite: 62 passed.
- Rust library suite: 141 passed.
- Saved OpenRocket authority replay: two landing branches and two retro-burn
  diagnostics loaded successfully from the `.ork` artifact.
- Release benchmark: 1,000 candidates in 7.064 seconds (141.6 candidates/s).

