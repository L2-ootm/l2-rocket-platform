# Motor-on attitude flip — moment decomposition (mission section 11)

Date: 2026-07-20
Script: `scripts/flip_diagnosis.py` (reusable; reruns any candidate through
OpenRocket and decomposes descent into pre-ignition / burn-window samples
using the existing tested `_retro_burn_diagnostic` helper from
`osifog_sweep.py`).
Raw data: `flip-diagnosis-summary.json`, `flip-diagnosis-traces.json`.

## Candidate under test

Family E8 seed `E8_8` (8 aft fins, h=0.70, sweep=5°, `s1_body_len=1.0`), the
best-known free-descent family from Phase 4A. Retro motors tested: J350W,
H180W. Same fixed OpenWind wind CSV, seed, and anti-tumble listener as the
Phase 4A campaign. All conclusions below are about this shared aft-fin /
aft-motor topology family, not one specific candidate.

## What the previous causal story claimed (and why it was untested)

`.planning/.continue-here.md` attributed the ~57 m/s powered result to "the
motor's thrust line creates a pitching moment that overcomes the fin
restoring torque" — i.e., mechanism 1 in the mission brief (direct thrust
moment). This was never checked against geometry or against a motor-off
baseline over the same time window.

## Finding 1 — Thrust-line moment is zero by construction, not measurement

The airframe is axisymmetric: nose ballast on the centerline, 3 main motors
in a symmetric ring, one retro motor on the centerline
(`_falcon_cluster_geometry` in `osifog_sweep.py:234`). For any single-axis
retro burn, `r_thrust` (mount position → CG) and `F_thrust` are both purely
along the body axis, so `M_thrust = r × F = 0` exactly, independent of
attitude, velocity, or motor choice. **Mechanism 1 (direct thrust-line
moment) is ruled out by geometry, not evidence** — no amount of "aligning
thrust with CG" can be the fix, because it is already aligned.

## Finding 2 — The vehicle re-orients nose-first in pure free-fall, with the motor off

Running the retro motor at a delay long enough that it never ignites before
ground impact (`s1_retro_delay=25`, motor never fires — `thrust_n=0` for the
entire trace) still produces the flip:

| t (s) | q (alignment) | speed (m/s) | note |
|---|---:|---:|---|
| 8.485 (apogee) | 0.661 | 3.7 | tail-first, rising |
| 8.885 | **0.999** | 5.5 | peak alignment |
| 11.24 | ~0.0 | 14.6 | crosses zero |
| 12.5 | -0.94 | 18.7 | flip essentially complete |
| 19.8 (impact) | -0.999 | 61.8 | locked nose-first for the rest of the fall |

This is a **passive aerodynamic re-orientation with the motor off**. The
tail-first attitude is a transient that decays to zero within ~2.8 s of
apogee; nose-first is the true, long-lived, dynamically stable free-fall
equilibrium for this fin-aft / CG-forward (ascent-required) airframe — the
same weathervane physics that gives ascent stability drives the body
nose-first once meaningful dynamic pressure builds after apogee. Tail-first
"good free descent" numbers reported by Phase 4A and Phase 3a are whole-
window arithmetic means that mix a short tail-first phase with a much longer
nose-first phase; they do not mean the vehicle is tail-first stable for the
whole descent. **This contradicts `artifacts/phase3a/bidirectional-stability-
analysis.md`'s conclusion that the 8-fin/no-ballast configuration is stable
in both directions — it is not, past ~3 s post-apogee, in this campaign's
wind/seed conditions.**

## Finding 3 — Firing the retro motor accelerates the same transition; it does not cause a new one

Every tested ignition (delays 9.5, 9.8, 10, 15 s; both J350W and H180W)
flips q from strongly positive to strongly negative within **0.13–0.29 s**
of ignition, and the burn-window-only direction cosine (thrust vector vs.
velocity, from `_retro_burn_diagnostic`, isolating only `thrust_n > 1 N`
samples — not diluted by the free-fall portion) is **positive** (thrust
aligned *with* velocity → accelerating, not braking) for 74–94% of the burn:

| motor | delay | q pre-ignition | flip delay after ignition | burn-window mean cos | fraction truly braking |
|---|---:|---:|---:|---:|---:|
| J350W | 9.5 | +0.919 | 0.13 s | +0.871 | 6.5% |
| J350W | 9.8 | +0.894 | 0.13 s | +0.868 | 6.3% |
| J350W | 10.0 | +0.872 | 0.13 s | +0.869 | 6.1% |
| H180W | 9.5 | +0.881 | 0.29 s | +0.518 | 25% |
| H180W | 10.0 | +0.795 | 0.19 s | +0.640 | 18% |

Lower thrust (H180W vs J350W) measurably slows the flip and roughly triples
the fraction of the burn that is genuine braking — but does **not** prevent
the reorientation in any tested case. **Mechanism tested and only partially
supported: "lower thrust solves the flip" is false as a binary claim; it is
true as a matter of degree.** The likely causal path is mechanism 2 in the
mission brief (aerodynamic moment change under acceleration): igniting
thrust abruptly changes the velocity vector while attitude lags, which
increases the effective angle of attack and amplifies the same destabilizing
aerodynamic torque already driving the free-fall reorientation in Finding 2.

## Finding 4 (engine gap, not a physics conclusion) — narrow ignition-arming window

Delays 8.5–9.2 s silently never ignite (thrust stays at 0 for the whole
flight, identical to the never-fires baseline); delays ≥9.5 s ignite almost
exactly at the requested time. The boundary is inside the single ~0.3 s
window where free-fall q is still above ~0.7 (see Finding 2 table). This
looks like a real engine/config quantization or arming-condition gap in
`osifog_sweep.py`'s retro-delay wiring, not a physical result — it is
flagged here for the next engineering session rather than investigated
further in this one (out of scope for a physics diagnosis).

## Classification (mission section 18)

**Physics-limited within the tested space**, for the family-H/E8/D/S shared
topology (single aft motor cluster, aft fins only, no TVC, no active
control, CG fixed forward for ascent stability):

- Quantitative bound: the tail-first free-fall equilibrium has a measured
  half-life of ~2.8 s post-apogee under this mission's wind/seed/launch
  conditions before the vehicle irreversibly weathervanes nose-first: at
  that point the aft-mounted retro motor's thrust is co-aligned with the
  direction of travel rather than opposed to it, so it cannot serve as a
  legal braking stage regardless of motor class or delay tuning.
- Untested legal dimension: an ignition inside the ~2.8 s tail-first window
  (delay ≈ 8.5–9.5 s in this seed) was not successfully exercised due to
  Finding 4's engine gap, so "ignite the instant the vehicle is at its
  post-apogee peak alignment" remains an open, cheap, high-value experiment
  once the arming-window bug is fixed.
- The mission's own Family C (forward strakes/keels, "mild ascent influence,
  high descent/reverse-flow influence") is the only proposed morphology
  that could shift the *descent-only* aerodynamic equilibrium toward
  tail-first without weakening ascent stability (which requires CG forward
  of CP in nose-first flow). Building it requires new AST-grammar and
  OpenRocket-geometry-compiler support that does not exist today
  (`osifog_sweep.py` only emits fins/ballast/motors, no body strakes) — this
  is real new engineering, not a parameter tweak, and was not attempted in
  this session to avoid shipping an untested half-built compiler.

## Finding 5 — Inert tail mass, not motor choice, sets the passive tail-first duration

A live user correction mid-session caught a confound in Findings 2-3: those
runs swapped the retro motor to J350W/H180W even for the "motor never fires"
free-fall baseline, silently changing the inert mass sitting at the tail
relative to the campaign's actual default retro motor (K550W, `MOTOR_DATABASE`
index 19 -- what every Phase 4A family seed and this session's own
`strake_batch.py` baseline actually carries during free descent). Redoing the
free-fall baseline with the K550W-mass retro sleeve left in place (never
ignited) gives a **qualitatively different** result:

| retro motor (inert) | apex→hit duration | whole-window mean q | sustained q>0.3 window |
|---|---:|---:|---:|
| J350W (0.038 m, lighter) | 11.3 s | -0.554 | ~2.8 s before irreversible flip |
| K550W (0.054 m, heavier, campaign default) | 28.3 s | +0.557 | 28.3 s (never flips before impact) |

A heavier inert tail mass genuinely prolongs (here, for the full flight)
passive tail-first stability -- likely by increasing pitch moment of inertia
enough that the same destabilizing aerodynamic torque cannot complete the
reorientation before ground impact. **This means Finding 2's "nose-first is
the only stable free-fall equilibrium" is too strong as stated: it depends on
tail mass, and the campaign's actual default topology (K550W inert) is
passively tail-first-stable for the whole flight.**

That said, firing the *same* K550W motor as the active retro burn (so there is
no mass-swap confound at all) still reproduces the flip: at delay ≈ apex+2s
and apex+4s, burn-window mean q is -0.85 to -0.91 and I_opp/I_total is only
0.015-0.022 (worse than the lighter motors in Finding 3, consistent with
Finding 3's "more thrust flips faster" pattern). **The reorientation is
triggered by the act of firing thrust, not by which motor provides it or by
insufficient static tail mass** -- a heavy tail delays the *passive* flip
indefinitely, but does not stop thrust from triggering it.

## Finding 6 — First strake/keel batch (Family C) does not clear the bar either

`scripts/strake_batch.py` ran the E8 baseline plus 6 strake/hybrid seeds
(3-fold and 4-fold; triangular, tapered, clipped-delta planforms; strake-only
and strake+small-aft-fin hybrids; span 0.03-0.04 m, length 0.85 m) through the
new descent-only ranking and hard admission gate (`scripts/descent_gates.py`).
Raw results: `artifacts/autoevo/strake-batch-results.json`.

| candidate | ascent margin (cal) | ascent legal | descent-admitted | powered result |
|---|---:|:--:|:--:|---|
| E8_baseline (8 fins, no strakes) | 1.738 | yes | yes | both probes worse than free descent -> early-stopped |
| ST3_triangular (strake-only) | 1.162 | **no (<1.5)** | yes | not tested (ascent-illegal) |
| ST4_triangular (strake-only) | 1.164 | **no** | yes | not tested |
| ST3_tapered (strake-only) | 1.166 | **no** | yes | not tested |
| ST4_clipped_delta (strake-only) | 1.162 | **no** | yes | not tested |
| ST4_hybrid_small_fin (4 small fins + strakes) | 1.747 | yes | yes | both probes worse than free descent -> early-stopped |
| ST4_hybrid_full_e8 (8 fins + strakes) | 1.739 | yes | **no (mean q=-0.42)** | not tested (descent-rejected) |

Two findings from this batch:

1. **All strake-only variants (zero conventional aft fins) fail ascent
   legality** at the span/planform tested (0.03-0.04 m radial projection is
   not enough restoring moment for nose-first ascent once the conventional
   fins are removed entirely). Strakes need to be a supplement to, not a
   replacement for, conventional aft fins at this span -- or need a larger
   span than tested here.
2. **Adding strakes to the full 8-fin E8 booster made descent worse**
   (mean q_total flips from +0.56 with no strakes to -0.42 with strakes
   added), and the one hybrid that stayed both ascent-legal and
   descent-admitted (`ST4_hybrid_small_fin`) still flips within 2 powered
   probes exactly like the baseline. **The watchdog worked as designed**:
   this batch used 4 powered OpenRocket evaluations total (2 per admitted
   candidate, both suspended by `PoweredEarlyStop` after 2 runs each) versus
   Phase 4A's 312 -- a measured ~78x reduction in wasted authority
   evaluations for the same qualitative outcome.

This is negative evidence about the *specific* strake sizes/placements
tried, not proof that Family C cannot work: span was deliberately modest
("shallow" per the mission brief) and only one axial placement (near the
nose end of the booster, position 0.05 m) and one length (0.85 m) were
tested. A wider span/placement sweep is the natural next step before
concluding Family C is exhausted.

## Recommended next actions, ranked (updated after Finding 6)

1. **Sweep strake span and axial placement**, not just planform. Every
   tested strake config used span 0.03-0.04 m at a fixed near-nose axial
   position; a systematic sweep (span up to ~0.5x body radius, aft-biased
   placement closer to the fins/CP region, and length as an independent
   variable from span) is un-exercised and directly implied by the mission's
   own Family C rationale ("mild ascent, influential in high-AoA descent") --
   the tested spans may simply have been too mild to move the reverse-flow
   moment at all.
2. Fix Finding 4's arming-window gap, then sweep ignition delay densely
   across 8.5-9.5 s on the K550W-inert (campaign-default) topology to test
   the one un-exercised legal hypothesis (ignite at/before the peak-alignment
   instant, before dynamic pressure builds enough to trigger the flip).
3. If (1) and (2) do not close the gap to <5 m/s, treat further delay/motor
   sweeps within families H/E8/D/S/strake-as-tested as futile (per the
   early-warning watchdog, which already measured a ~78x reduction in wasted
   OpenRocket evaluations this session: 4 powered runs vs. Phase 4A's 312 for
   the same qualitative result) and escalate to a genuinely different
   descent strategy within the legal ruleset (e.g. a much larger strake span,
   or accepting a narrow near-apex ignition window as the only legal braking
   opportunity rather than a late high-speed one).
3. Re-audit `artifacts/phase3a/bidirectional-stability-analysis.md`'s q_mean
   table against a full-descent trace before relying on it again — its
   numbers appear to reflect the same whole-window-averaging blind spot
   this diagnosis found in Phase 4A's methodology.
