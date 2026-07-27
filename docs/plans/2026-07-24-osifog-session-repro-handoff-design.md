# OSIFOG session reproducibility and handoff design

## Goal

Tomorrow must start from a proven authority state without mutating Candidate F
or Candidate G, confusing stored-flight reproducibility with rerun
robustness, or tuning a downstream delay before an upstream design decision.

The preparation has two outputs:

1. A human runbook explaining seed semantics, organizer-side uncertainty,
   current evidence, exact commands, optimization dependency order, and
   promotion gates.
2. A read-only machine check backed by a manifest of immutable candidates.

## Seed model

OpenRocket 24.12 constructs `SimulationOptions.randomSeed` in memory. The
RK4 integrator derives its random stream from that seed and adds small random
pitch/yaw moment perturbations at integration steps. Multilevel pink-noise
wind models use independent random state, so the OSIFOG authority path seeds
each wind level deterministically from the same requested seed.

The authority seed is 16000. It produces the exact stored flight data inside
the submission `.ork`, but OpenRocket does not serialize the random seed into
the `.ork` simulation conditions. Reopening the saved file preserves and
displays the stored results. Pressing Run creates a new realization unless
the calling code explicitly restores seed 16000 and seeds every multilevel
wind model.

Therefore:

- saved-result reproducibility means inspecting the exact submitted bytes and
  their stored flight data;
- rerun reproducibility means recreating the simulation with the same
  OpenRocket version, seed, wind seeding, timestep, extension, and inputs;
- robustness means remaining legal across intentionally different seeds and
  timesteps.

These are separate claims and must be reported separately.

## Organizer-side assurance

The governing Mission Secret requires one simulation, executed immediately
before saving, with all simulated data stored. That strongly indicates the
saved flight data is the judging artifact. It does not explicitly state
whether judges ever press Run.

The exact package bytes and SHA-256 must therefore be preserved. Required
screenshots must be captured from that package without rerunning it. Before
submission, ask OSIFOG:

> A banca calcula a pontuação lendo os dados simulados já salvos no `.ork`, ou
> executa novamente a simulação? Se houver reexecução, qual versão do
> OpenRocket e qual seed/realização aleatória serão usadas?

If OSIFOG confirms stored-data scoring, the immutable hash plus independent
saved-data reload is the equality proof. If it confirms rerunning, no
official candidate is safe until its seed/timestep robustness is improved or
the organizer specifies the exact rerun seed and software environment.

## Read-only verifier

`designs/osifog_submission/manifest.json` records immutable candidate hashes,
authority scores, checklist sizes, expected robustness counts, and authority
environment facts.

`scripts/osifog_session_check.py`:

- never writes or reruns a simulation;
- verifies artifact existence and SHA-256;
- validates ZIP/XML integrity;
- requires one up-to-date simulation, two saved branches, and the anti-tumble
  scripting extension;
- verifies report seed, score, checklist, and robustness count;
- verifies that the `.ork` contains no serialized seed and explains the
  consequence.

Any failure blocks optimization until explained. A new candidate is added to
the manifest only after full OpenRocket authority packaging, saved-data
reload, robustness measurement, and explicit promotion.

## Tomorrow workflow

Start with the read-only checker and focused tests. Preserve F/G. Implement
missing physical search dimensions in the organic AST/compiler before
searching them. Optimize the dominant touchdown-position term upstream,
re-trim apogee and rail vector after topology/mass changes, and retune
terminal burns last. Promote only a generated, saved, independently reopened
OpenRocket authority artifact.
