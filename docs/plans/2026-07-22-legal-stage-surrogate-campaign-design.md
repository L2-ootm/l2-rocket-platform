# Legal Stage-Surrogate Campaign

## Decision

The legal search must separate the two different design problems instead of
continuing coupled score campaigns. Historical OpenRocket evidence contains
525 genuine pre-apogee flights. The booster stage already has many strong
phenotypes (greater than 3.4-cal ascent margin and 70–105 s passive tail-first
windows). The sustainer is the sole remaining topology blocker: the best
observed legal tradeoff is 1.906 cal with a 2.43 s tail-first window. Therefore
future authority budget is spent only on improving the sustainer, while a
known-good booster stage is recombined as a complete stage genome.

The campaign trains a local Extra-Trees surrogate on current, non-quarantined
OpenRocket records. Inputs are real AST compiler parameters; outputs are
exposed-sustainer margin, passive tail-first duration, apogee, and Mach. Each
cycle generates thousands of stage-wise crossovers, runs shared physical
repair, rejects invalid geometry, and uses the surrogate only to prioritize
novel candidates. Rust then performs the hard ascent screen in one batch.
Only a small diverse Pareto set reaches isolated OpenRocket free-descent
authority. A candidate advances to powered landing only when genuine
pre-apogee separation, Mach below 0.95, every ascent segment at least 1.5
calibers, and motor-aware landing opportunity are all measured—not predicted.

The loop is resumable and fail-closed. It atomically records the training
corpus digest, proposal IDs, Rust results, authority records, health, and stop
reason. It stops on a verified recovery basin, repeated lack of joint-gate
improvement, excessive authority failures, source drift, or exhausted budget.
Post-apogee separation and every quarantined delayed-separation artifact are
excluded at ingestion. No score campaign starts until both stages pass the
motor-aware recovery opportunity gate.

After the first 36-candidate authority run, the learner also models the
quantities used by that gate directly: opposing delta-v surplus,
burn-weighted velocity alignment, and usable-duration/burn-duration ratio for
each stage. Passive-window duration remains useful telemetry, but it is no
longer allowed to stand in for actual braking authority.
