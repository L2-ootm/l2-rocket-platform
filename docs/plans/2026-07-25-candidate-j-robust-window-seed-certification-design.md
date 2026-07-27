# Candidate J — robust timing-window and seed certification design

Date: 2026-07-25

## Decision

Candidate I remains byte-for-byte immutable. Candidate J is a new organic
lineage that keeps the internal octaweb structure, centering rings, interstage
coupler, three-motor Booster ascent cluster, and a retro-only Sustainer
(`s0_main = null`). External motor pods are forbidden.

From Candidate J onward, retro motor models, motor-sleeve dimensions, internal
mass distribution, aerodynamic surfaces, separation state, and other legal
internal geometry are free optimization variables. The algorithm may create
later candidate letters whenever a materially different solution deserves an
immutable authority package. The competition environment remains locked,
including the OpenWind-required base pressure of 1000 hPa.

Candidate J is not promoted because one nominal simulation scores well. It is
promoted only when the algorithm proves broad, continuous landing basins and
held-out seed robustness in OpenRocket.

## Why seed 16000 is insufficient

OpenRocket has two relevant random states:

1. the RK4 simulation seed in `SimulationOptions`; and
2. an independently constructed pink-noise model in every imported
   multilevel-wind layer.

The GUI randomizes the first seed before every run. OpenRocket 24.12 does not
serialize that seed in the `.ork`. Setting only `SimulationOptions` also does
not reset the independently constructed wind-layer models. Therefore an
authority replay must explicitly set the master seed and deterministically
replace every wind-layer model.

Candidate I is direct evidence that seed 16000 is not a robustness proof. At
the selected timing center it passes only seeds 16000 and 16003; seeds 16001,
16002, and 16004 fail. A fixed-seed run can be exactly reproducible while the
rocket remains unsafe under an unknown reviewer seed.

## Optimization contract

The algorithm owns all parameter changes and authority runs. It uses a
lexicographic objective, in this order:

1. physical topology, rules, motor fit, rings, coupler, and checklist;
2. numerical convergence;
3. worst-case legality over the development seeds;
4. continuous Sustainer and Booster ignition-window width;
5. worst touchdown speed and margin below 5 m/s;
6. apogee error and horizontal apogee penalty;
7. official score.

An average score may rank candidates only after every preceding hard gate
passes.

## Timing gates

- Minimum promotion window: 50 ms continuously legal for each stage.
- Stretch target: 100 ms for each stage.
- Every point in the claimed interval must keep both stages below 5 m/s.
- The interval must survive at converged timesteps, not merely `dt = 0.05 s`.
- The final check cross-tests both delay intervals, because independently good
  stage timings need not form a good coupled basin.

The desired mechanism is a passively stable, low-vertical-speed descent
plateau followed by a forgiving terminal burn. It must not depend on a
seed-sensitive tumble phase. Burning the last gram at contact is acceptable,
but only if neighboring ignition times remain legal.

## Seed protocol

Use deterministic, versioned seed sets:

- development: 5 seeds used by the optimizer;
- promotion: 30 different seeds;
- final held-out certification: 300 different seeds selected before the final
  run and never used for tuning.

For zero failures in `N` independent seeds, the exact one-sided 95% upper
bound on failure probability is `1 - 0.05^(1/N)`. Thus zero failures in 300
seeds supports a failure probability below about 1%; it does not prove every
possible 32-bit seed. No finite sample can prove all random seeds. A universal
claim would require deterministic bounds on all perturbations, which
OpenRocket does not provide.

All authority artifacts record the `.ork` hash, OpenRocket build, Java build,
master seed, derived wind-layer seeds, timestep, motor database digest,
parameters, and result. Saved flight data is authoritative for byte-identical
inspection; rerun reproducibility is authoritative only under the recorded
environment and complete seed procedure.

## Search strategy

The Rust scorer screens large populations for physical fit, ascent, apogee,
and passive tail-first dwell. OpenRocket sequentially evaluates only elites:

1. `dt = 0.01 s` and development seeds;
2. `dt = 0.005 s` for surviving timing basins;
3. a finer timestep until landing velocity and legality stop changing
   materially;
4. 30-seed promotion;
5. 300-seed held-out certification.

Candidate J backcrosses Candidate I's required internal structure with the
more persistent dynamics seen in Candidates D, E, and F. Search variables
include coupler mass/length/overlap, internal ballast mass and position,
airframe and fin damping, separation timing, compatible real retro motors,
and launch vector. Any missing variable must be added to the AST, Rust
transport, OpenRocket compiler, geometry guards, and tests before it is
searched.

## Promotion result

The deliverable is a new `candidate_J` package plus:

- immutable hashes and topology audit;
- convergence table;
- continuous two-stage timing-window map;
- development, promotion, and held-out seed tables;
- exact replay command and environment manifest;
- score decomposition and apogee error.

Candidate I remains available unchanged regardless of Candidate J's result.
