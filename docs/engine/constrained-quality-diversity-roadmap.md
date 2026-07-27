# Constrained Quality-Diversity Roadmap

## Why the current GA is insufficient

The current scalar genetic search mixes discrete topology with correlated
continuous geometry. Most mutations fail hard physical or mission constraints,
while the few surviving families can dominate selection before a different
ascent/descent compromise is explored. OpenRocket is too expensive to serve as
the inner loop, and a single score cannot preserve behaviorally distinct
near-solutions.

## Target architecture

```text
Grammar-constrained AST generation
  -> hard schema/geometry/mission filters
  -> constrained MAP-Elites archive
  -> per-cell continuous optimization
  -> uncertainty-aware OpenRocket promotion
  -> authority polishing and robustness
```

Hard constraints remain outside fitness. An illegal candidate never occupies
or dominates a legal archive cell.

## Topology grammar

The AST grammar should generate only:

- components with deterministic stage ownership;
- contained motors with supports, load paths, and open exhaust volumes;
- legal serial/clustered/podded stage boundaries;
- legal material and dimensional ranges;
- attached ballast with physical volume and inertia;
- event graphs whose ignition, burnout, separation, and ground-contact order
  is possible.

Generation should be constructive. Repair is limited to local normalization
that is reported and cannot change candidate meaning silently.

## MAP-Elites descriptors

Initial sustainer descriptors:

- minimum phase-aware exposed-stage static margin;
- tail-first window duration below a configured altitude;
- burn-weighted vertical-opposition fraction;
- angular-rate or rotational-period class;
- forward/aft aerodynamic-area ratio;
- normalized wet and dry CG;
- normalized CP at representative Mach;
- landing-impulse margin;
- dry-mass and motor-impulse classes.

Descriptor bins must be versioned with mission, engine, and motor digests.

## Constraints and ranking

Constraint domination order:

1. parser and finite numerical state;
2. physical geometry, containment, attachment, and exhaust;
3. mission legality;
4. valid branch and event sequence;
5. exposed-stage phase-aware stability;
6. subsonic flight;
7. powered landing legality;
8. official score;
9. delay robustness;
10. simplicity.

## Per-cell optimization

- CMA-ES: correlated fin, body, and ballast geometry in a fixed topology.
- Differential Evolution: bounded rugged variables and mixed motor choices
  within a narrow family.
- Bounded one/two-dimensional search: ignition delay and final apogee polish.
- Trust-region Bayesian optimization: only when OpenRocket evaluations are
  exceptionally scarce and the cell is already fixed.

No global Gaussian-process model should own the mixed topology search.

## Fidelity ladder

1. AST schema and units.
2. Geometry, contact, load path, motor fit, and exhaust.
3. Analytic phase-aware mass/CG/CP and impulse feasibility.
4. Rust ascent and branch trajectory.
5. OpenRocket stage free-descent diagnostic.
6. OpenRocket powered landing validation.
7. Delay robustness.
8. Saved/reopened full-mission authority.

Promotion stops as soon as a lower level fails.

## OpenRocket promotion policy

Promote diverse archive cells, not merely the highest scalar score. Prefer
cells with high proxy uncertainty, disagreement with prior OpenRocket labels,
or materially new topology descriptors. Every promotion records the explicit
scenario type and fail-closed scenario manifest.

OpenRocket results calibrate ranking and uncertainty; they never weaken hard
rules or cease to be authority.

## Result data schema

Each evaluation record contains:

```yaml
candidate_id:
topology_signature:
topology_family:
descriptor_schema_version:
descriptors:
scenario_type:
fidelity:
mission_digest:
wind_digest:
motor_curve_digests:
engine_version:
predicted:
authority:
classification:
failure_mode:
hard_violations:
artifact_paths:
artifact_hashes:
```

Failures are contextual. A motor, fin count, or component is not globally
blacklisted from a few family-specific failures.

## Migration from the current engine

1. Stabilize scenario manifests and Anti-Tumbling serialization checks.
2. Finish OpenRocket parity for the exposed-stage phase-aware margin vector.
3. Add a descriptor extractor beside existing candidate evaluation.
4. Introduce a read-only MAP-Elites archive fed by the current GA.
5. Compare archive diversity and promotion yield against scalar selection.
6. Make the archive the parent selector only after parity and replay tests.
7. Add per-cell CMA-ES/DE behind an interface; keep current mutation as a
   fallback during migration.
8. Version and migrate CKG failure records into contextual archive labels.

## Minimum next-session implementation sequence

1. Validate the five Rust phase margins against one OpenRocket exposed-stage
   reference at matching events and Mach values.
2. Expose the phase vector in `ast_eval` JSON, not only its minimum.
3. Add replay fixtures for stable-throughout, burnout-unstable, and
   unstable-throughout stages.
4. Implement descriptor extraction and an in-memory archive with deterministic
   cell replacement.
5. Replay the 32 Gate 4 candidates without launching new authority searches.
6. Promote at most the smallest diverse set needed to test archive usefulness.

This document is a next-session roadmap, not authorization for a broad rewrite.
