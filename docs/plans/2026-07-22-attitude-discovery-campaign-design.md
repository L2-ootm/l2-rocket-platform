# Attitude Discovery Campaign Design

## Outcome and budgets

Build a staged campaign that spends OpenRocket authority time only on the
missing prerequisite: a rules-legal, fixed-geometry sustainer attitude window
where the central retro motor opposes descent. A design cannot enter powered
landing search until it is ascent-legal and has a motor-duration-compatible
tail-first window. A design cannot enter the official-score campaign until
both stages have persisted powered touchdown evidence below 5 m/s.

Budgets for today's run: three independent OpenRocket worker processes, one
JVM per process, 24 authority candidates per generation per worker, 12
generations maximum, and a 15-minute stale-worker timeout. Rust proposal
screening remains batched and CPU-capped. No new dependencies or services.

## Data flow

Each worker deterministically creates or breeds physical PodSet parameters,
uses the Rust AST evaluator to screen a larger proposal pool, then runs the
selected diverse candidates sequentially through OpenRocket free descent. The
authority score rewards ascent legality, positive sustainer alignment near
impact, tail-first duration, and motor-aware usable opportunities. It does not
reward official score before recovery is physically possible.

Workers append JSONL events and atomically checkpoint RNG state, generation,
parents, compact authority records, error fingerprints, and progress. The
supervisor owns worker processes, restarts only non-deterministic crashes, and
stops the whole campaign on a recovery-gate hit, repeated deterministic error,
source drift, stale heartbeat, or statistically flat evolution after two
diversity injections.

On a gate hit, the candidate receives real powered OpenRocket validation. Only
persisted sub-5 m/s evidence for both stages creates `recovery-pass.json` and
authorizes the existing official-score campaign. Otherwise the attitude
campaign continues from the powered evidence rather than widening delay
search blindly.

## Learning strategy

Backpropagation is not used in the control loop. Ground contact, stage events,
motor ignition, and attitude-window thresholds make the objective
discontinuous and non-differentiable. A small dependency-free ridge surrogate
is fitted after enough authority samples to rank Rust-screened proposals; a
fixed fraction of each batch remains diversity/exploration. This provides the
useful part of learned optimization today without adding a neural runtime or
pretending gradients through OpenRocket are meaningful.
