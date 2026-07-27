# OSIFOG Candidate D — Structural Correction and Retune Design

## Intent

Preserve every existing submission artifact, especially Candidate C, while
producing a mechanically explicit successor validated by real OpenRocket
24.12. Candidate C remains the immutable score fallback. Candidate D is a new
parameter file, report, robustness matrix, and `.ork`.

The direct OpenRocket campaign remains the authority path for this deadline.
The retired GA campaigns v1–v9 must not be resumed. Existing experiment batches
must not be rerun unless a range is extended or a stated hypothesis is tested.

## Structural correction

The direct generator currently models an octaweb as four off-axis annular
rings per axial station. OpenRocket ignores `CenteringRing.radialposition`, so
that representation is physically false. On a retro-only stage it also emits
three degenerate solid discs.

Replace it with the already-proven organic-compiler convention:

- exactly two centered rings per motorized stage;
- one at the forward end of the supported mount envelope and one at the aft
  thrust plane;
- outer radius equal to the parent airframe inner radius;
- booster inner radius equal to the complete three-motor cluster envelope
  (`center_distance + main_mount_outer_radius`);
- retro-only sustainer inner radius equal to the central retro sleeve outer
  radius;
- explicit numeric radii, because OpenRocket automatic inner radius ignores
  clustered-tube radial offsets;
- 5 mm axial thickness and fiberglass material.

Legacy batch reproduction retains the opt-in `octaweb_rings` flag. Submission
verification, however, requires the four correct rings and rejects ringless or
degenerate packages.

## Validation

Validation is layered:

1. Pure-Python geometry validation checks ring dimensions, containment,
   annular thickness, support-envelope clearance, and axial placement.
2. XML contract tests check two rings per stage, unique stable IDs, zero radial
   offset, and correct explicit radii for clustered and retro-only stages.
3. OpenRocket round-trip reload confirms resolved geometry, mass, warnings, and
   simulation execution.
4. The submission checklist rejects missing or malformed rings.

## Optimization dependency order

All experiments use new tags and retain their input/output JSON pairs.

1. Correct rings with retro delays disabled at 1100 s.
2. Scan separation delay 24–28 s, extending only if the score trend remains
   monotonic and the joined descent attitude remains admissible.
3. Re-trim ballast to 3000 ±5 m with Mach below 1.
4. Re-trim the rail vector for apogee horizontal drift.
5. Tune both terminal burns last: coarse, 5 ms, then 1 ms.
6. Require burn-through-touchdown evidence and report the full
   seed `{16000, 7, 12345}` × timestep `{0.05, 0.02, 0.01}` matrix honestly.
7. Save, reopen, rerun, and package Candidate D without overwriting A/B/C.

The new candidate is accepted only if both stages touch down below 5 m/s in the
official seed/timestep run, all confirmed disqualification rules pass, rings
are mechanically valid, and the saved artifact reloads successfully.
