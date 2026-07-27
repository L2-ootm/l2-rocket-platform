# High-Throughput Engine Design

## Goal

Exceed 100 representative organic rocket simulations per second while retaining
the existing calibrated 6-DOF/OpenRocket authority ladder.

## Architecture

Execution profile is orthogonal to physics identity:

- `super-speed`: summary-only bulk screening. Start with optimized 6-DOF;
  enable reduced 3-DOF only if the benchmark gate remains unmet.
- `balanced`: calibrated summary-only 6-DOF promotion and ranking authority.
- `authority-heavy`: strict 6-DOF with optional/decimated trace plus frequent
  OpenRocket sampling.

All accepted designs must pass live OpenRocket validation. Fast modes can only
rank or promote candidates.

## Data flow

`AST batch → preflight gates → super-speed summary → stratified promotion →
balanced 6-DOF summary → scheduled OpenRocket sample → final OR validation/polish`

Preflight rejects motor-fit, motor-adequacy, no-liftoff and static-margin failures.
Streaming summaries retain scalar extrema and event state in O(1) memory.
Promotion includes leaders, near-constraint candidates, uncertainty outliers and
topology-diverse sentinels. Rank or hard-gate drift expands promotion or triggers
automatic fallback.

## Profiles and cadence

| Profile | Rust promotion | OpenRocket cadence | Acceptance |
|---|---|---|---|
| super-speed | top/diverse/uncertain ~5% each generation | every 10 generations + final top 8 | OR only |
| balanced | strict 6-DOF top ~10% every 2 generations | every 5 generations + final top 8 | OR only |
| authority-heavy | strict 6-DOF for all | every 1–2 generations + final top 8 | OR only |

## Failure handling

- Never reward no-liftoff/zero-flight candidates.
- Never accept a fast-mode hard-gate pass without balanced/OR confirmation.
- Namespace calibration by mode, physics version, topology, motor digest and
  mission envelope.
- Detect result-count/checksum drift in the persistent worker.
- Fall back to one-shot strict evaluation if worker health or parity fails.

## Verification

- Mixed immutable 256/1024-candidate corpus; repeated median/p95 measurement.
- Fast mode >100/s median and >90/s p95 repetition throughput.
- Fast ranking: top-quartile Spearman >=0.95, top-10 recall >=90%, zero false
  hard-gate passes.
- Balanced fidelity: mean apogee <=1%, max <=2%; mean Mach abs <=0.02,
  max <=0.05; top-10 recall >=95%.
- Authority-heavy preserves the five-seed calibrated envelope and mandatory live
  OpenRocket final validation.

