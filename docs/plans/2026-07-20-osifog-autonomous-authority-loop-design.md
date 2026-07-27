# OSIFOG Autonomous Authority Loop

## Goal

Move optimization work out of the conversational layer and into a repeatable
engine loop. Rust owns population-scale exploration; OpenRocket owns physical
authority, legality, descent, retro-braking, and final score. The loop stops
only on a legal score of at least 850,000 or its configured cycle budget.

## Data flow

1. Load mission scoring, constraints, atmosphere, launch guide, and full wind
   profile from mission inputs.
2. Generate collision-free 3+1 AST candidates.
3. Evaluate ascent in Rust with the ascent-compatible subset of the same
   mission scoring table.
4. Rank finalists and run sequential OpenRocket authority polish.
5. Persist official score terms, stage telemetry, violations, and Rust/OR
   pairs.
6. Learn robust median apogee and Mach correction factors from the authority
   batch and apply them before the next Rust cycle's gates and scoring.
7. Repeat with a new deterministic seed.

## Decisions

- The score formula remains mission data, not a Rust competition branch.
- Stage landing terms are omitted only from the ascent screen because no
  landing exists in that phase; OpenRocket evaluates the complete table.
- Calibration requires at least eight paired samples and uses medians plus MAD
  diagnostics so one failed simulation cannot steer the population.
- Full multilevel wind, temperature, pressure, humidity, launch altitude, rod
  length, angle, and azimuth cross the JSON evaluator boundary per candidate.
- No candidate is hand-tuned. Missing physics or telemetry becomes an engine
  capability with tests and documentation.

## Failure handling

Every cycle writes an atomic checkpoint. Illegal candidates remain auditable.
If calibration leaves no viable population, the engine must expand or
stratify its topology search; it must not relax OpenRocket gates. JVM work is
sequential and reuses one process, while Rust retains bounded Rayon parallelism.

