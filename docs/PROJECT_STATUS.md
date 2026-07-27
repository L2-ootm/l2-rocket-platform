# Project Status

**Written 2026-07-24, mid-session. This document reports what is currently true and what is currently unknown. Where a root cause has been empirically confirmed (reproduced, traced through code, verified against real data) it is stated as such. Where something is failing and the cause has NOT been confirmed, this document says so explicitly instead of guessing — a plausible-sounding but unverified explanation is worse than no explanation, because it gets treated as fact by whoever reads it next.**

## What this project is

The immediate, deadline-bound goal is the OSIFOG 2026 Level 3 "Foguete Falcon" competition: design a multi-stage rocket in OpenRocket simulation that performs a SpaceX-Falcon-style retro-propulsive landing (no parachutes), scored by the organizer's own formula (see §2). Submission deadline: 2026-07-26 23:59 BRT.

The broader, standing goal — the actual reason this codebase exists past this one competition — is a personal platform for developing self-evolving/genetic algorithms. OSIFOG Level 3 is the current proving ground and forcing function, not the sole purpose. This distinction matters for how the codebase should be read and cleaned up: code that only serves the OSIFOG-specific competition mechanics should stay scoped and replaceable; code that implements the actual genetic-evolution machinery (mutation, crossover, selection, fitness scoring, self-healing repair) is the part meant to outlive this specific competition and generalize.

## 1. The active pipeline, as it actually runs today

```
rocket_ast.py          -- generates/mutates/sanitizes/compiles rocket designs as ASTs
       |
l2_engine/src/bin/ast_eval.rs  -- Rust batch physics scorer (JSON-in/JSON-out subprocess)
       |
organic_loop.py         -- the GA itself: population, mutation, crossover, CKG memory,
       |                    calls the Rust evaluator, exports elites, optional OR validation/polish
organic_campaign.py     -- wraps run_generation in a resumable, crash-hardened, idempotent
       |                    long-running campaign (checkpoints to organic_elite.json every generation)
osifog_campaign_watchdog.py -- process supervisor; restarts organic_campaign.py if it dies
```

See `SOURCE_MAP.md` for exact file locations, git-tracking status (several of these are currently untracked — see that document), and what NOT to trust (the old `OSIFOG/SOURCE_MAP.md`, the current `README.md` — both stale, cleanup pending).

## 2. The actual scoring formula (verified against OSIFOG's own PDF, not assumed)

```
SCORE = 900000
  - 3000 * (apogee_m - 3000)^2
  - 16   * (apogee_east_m)^2
  - 16   * (apogee_north_m)^2
  - 2    * (mean_touchdown_east_m)^2
  - 2    * (mean_touchdown_north_m)^2
  - 500  * (mean_touchdown_speed_ms)^2
  - 7500 * (total_propellant_mass_kg)
```

This is implemented in `l2_engine/src/ast.rs::evaluate_scoring_table`, driven by `missions/osifog_l3_precision.json`'s `scoring.terms` block, and was verified line-by-line against `OSIFOG/OSIFOG_Nivel3_ProjetoFalcon.pdf` section 3 earlier this session (confirmed correct, including the mean-then-square aggregation semantics for the touchdown terms). A separate bug that was silently corrupting this correctly-computed score with a legacy penalty multiplier was found and fixed this session (see `.planning/HANDOFF.json` fixes_round_8) — mentioned here only because it means any score data from before that fix (essentially all of campaigns v1–v8) should not be trusted as reflecting the real formula.

Negative scores are an explicitly valid, expected outcome per a direct ruling email from OSIFOG organizers (not an error state to guard against).

## 3. Real OSIFOG legality rules (verified against both organizer PDFs directly, not from memory or a prior session's summary)

From `OSIFOG_Nivel3_ProjetoFalcon.pdf` §2 (the six numbered "rigorous restrictions that define the boundary of the challenge") and `OSIFOG_Missao_Secreta_2026.pdf`'s 15-item checklist:

- All stages land under 5 m/s, no passive recovery devices (parachute/streamer/banner) — real, hard.
- Return via retro-propulsion braking only, no active guidance correction — satisfied by construction (this pipeline never models active guidance at all).
- Static stability maintained during ascent — a control-METHOD requirement (passive aerodynamics only), NOT a numeric caliber minimum. This was a significant correction made mid-session: earlier attempts at enforcing this as a hard `min_static_margin >= X` gate (X = 1.5, then 0.1, then 0.0) were all self-imposed conventions not actually required by OSIFOG. It is currently implemented as fully opt-in (skippable), matching how `min_thrust_to_weight` (also confirmed non-required) is handled.
- Multi-stage required.
- Real OpenWind atmospheric data required.
- OpenEarth trajectory capture required (submission-time requirement, not a simulation-time constraint).
- Max height 4m, max launch rod 6m, supersonic (Mach ≥ 1) banned, minimum component dimension 0.1cm, material density 0.17–11.34 g/cm³, no interpenetrating solids, nothing behind the motor's exhaust except fin tips — all confirmed real via the Missão Secreta document.

`min_thrust_to_weight` appears in neither document and has been removed as a legality gate entirely (a design too weak to lift off simply scores catastrophically on the real apogee term — no separate gate is needed or wanted).

## 4. Current live campaign: v9

Fresh start (not resumed from v7/v8) launched 2026-07-24, deliberately not warm-started from any prior campaign's population — v8's entire history had its selection pressure computed against a corrupted score formula and a legality-rule set that changed multiple times within the same session, so its "best" individuals carried no reliable signal forward. `--max-hours 43`, seed 314159.

**As of this document's last check** (2026-07-24T18:42 UTC, 611 cumulative generations):

```
legality_rate: 0.0  (no candidate has yet passed every constraint)
n_reached_landing_phase: 3   (candidates with at least one real touchdown recorded)
n_success: 0
failure_reason_histogram:
  not_all_stages_landed:  50/96
  max_height_m:            41/96
  max_mach:                 1/96
  dropped_stage_0_simulation_diverged: 2/96
  no_liftoff:               2/96
best candidate: raw_score 0.9395, failing on max_height_m (4.258m > 4.000m limit)
```

This snapshot is a point-in-time fact, not a trend — check `designs/osifog_level3/osifog_campaign_v9/health.json` and `best-candidate.json` directly for current numbers.

## 5. What has been fixed this session, with confirmed root causes

Each of these was empirically reproduced and verified, not inferred. Full detail with code references in `.planning/HANDOFF.json` (`fixes_round_7` through `fixes_round_11`):

1. **No crossover existed** in the GA's reproduction loop — pure single-parent mutation only. Added node-level crossover with per-attribute interpolation and complementary-specialist pairing, ported from design patterns found in two pre-existing but genome-incompatible legacy implementations.
2. **`min_thrust_to_weight` and `min_static_margin`** were self-imposed numeric legality gates not actually required by OSIFOG — removed / made opt-in.
3. **The real scoring formula was being silently corrupted** by a legacy penalty multiplier that ran even when the formula's own scoring-table path was used — fixed to return the real formula's result directly.
4. **Post-simulation constraint failures discarded their own simulated flight data** (apogee, touchdown telemetry) even when a real flight had run — meaning no real negative score could ever be computed for a near-miss. Fixed to preserve telemetry for any failure that occurs after a simulation actually ran.
5. **The Rust proxy's simulation time budget was 600s; OSIFOG's own reference OpenRocket configuration uses 1200s** — confirmed via the organizer's own screenshot. Fixed.
6. **The dominant failure mode (`motor_mount_collision`, ~74% of the population) was a genuine geometric overlap caused by stale cage data surviving a motor swap that made the cage physically infeasible for its current airframe** — root-caused by reconstructing a real failing candidate's exact geometry and matching the reported collision numbers to within 6 decimal places against a specific motor's real diameter. Fixed by widening the airframe when a motor swap makes the existing cage infeasible, instead of leaving inconsistent data in place.

## 6. Open, NOT root-caused — genuinely unknown, do not assume

- **Why `not_all_stages_landed` is currently the dominant failure (50/96 this snapshot).** Landing detection is confirmed to be purely position-based (crosses z=0), independent of speed — so this is not simply "landing too fast" mislabeled. Whether these are genuinely bad/unstable designs that would never land regardless of time budget, or designs that need more than the now-corrected 1200s to come down, or something else entirely, has not been distinguished. The 1200s fix just went live; its actual effect on this failure category has not yet been measured over enough generations to draw a conclusion.
- **Whether the genetic algorithm is actually exploring the design space well.** The user has directly raised this concern more than once this session. Crossover was only added this session (previously totally absent) and its practical effect (does `_select_complementary_pair()` actually find meaningfully different specialists to cross, or does it degenerate to near-random pairing when the population converges on one dominant constraint type?) has not been rigorously evaluated — only observed anecdotally in passing. No systematic audit of mutation operator diversity, selection pressure, population diversity metrics, or convergence behavior has been done.
- **The reconstructed 839k historical design's Rust-vs-OpenRocket discrepancy** (apogee ~2858m/margin 0.86 in the Rust proxy vs. 754m/margin 0.049/tumble in real OpenRocket, from an earlier session). Attributed at the time to an untuned retro-ignition-delay guess specific to that one reconstruction, but this was never confirmed against a controlled comparison — it remains an open question whether it reveals something more systemic about proxy-vs-authority divergence.
- **Fin/body proportionality after the new octaweb-widening fix.** The widening fix (§5.6) can grow a stage's body radius without re-scaling that stage's fins to match, for the specific case where only this new widening path (not the earlier general diameter-continuity pass) causes the growth. Whether this matters in practice (produces a measurably worse fin/body ratio closeness score) has not been checked.
- **`dropped_stage_0_simulation_diverged` and `no_liftoff`** (2 each, this snapshot) — new/newly-visible failure categories now that the TWR gate no longer filters them out earlier. Not investigated.

## 7. Known housekeeping/cleanup needs (see `SOURCE_MAP.md` for full detail)

- A 1.1GB file is currently committed to git history — will block any push to GitHub as-is.
- The actual campaign-orchestration entry points (`organic_campaign.py`, `osifog_campaign_watchdog.py`, `campaign_infra.py`) are not tracked by git at all.
- ~7.3GB of retired campaign output (v1–v8) sitting in `designs/`.
- Two stale source-of-truth documents (`OSIFOG/SOURCE_MAP.md`, `README.md`) actively point at the wrong (retired) pipeline as current.
- Large amount of root-level debug/patch/scratch script clutter and several competing, likely-superseded planning documents in `.planning/`.

## 8. How to get current data instead of trusting this document's numbers

This document's §4 snapshot will be stale almost immediately. For current truth:
- `designs/osifog_level3/osifog_campaign_v9/health.json` — liveness, generation count, legality rate.
- `designs/osifog_level3/osifog_campaign_v9/best-candidate.json` — current best candidate, full population failure histogram.
- `.planning/HANDOFF.json` — the single most current, actively-maintained document in this repository; read this first, always.
