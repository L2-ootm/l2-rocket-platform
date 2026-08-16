# System State

> **`handoff.md` at the repository root is the current source of truth.** This
> file is the technical status ledger behind it. Everything below the 2026-08-16
> section is dated history, kept for the reasoning it records.

## Current Phase (2026-08-16)

**Publication. Engine development is frozen.** No engine behavior has changed
since the 2026-07-26 open-source baseline. The OSIFOG 2026 Level 3 entry was
submitted at its deadline and is closed. The repository is being published as
`L2-ootm/l2-rocket-platform` under GPL-3.0-or-later; see `LICENSE`, `NOTICE`,
and `docs/maintenance/open-source-readiness.md`.

Verified 2026-08-16: `cargo test` 175 passed; the three README-documented
pytest files 110 passed.

The next technical blocker is unchanged and still open: **OpenRocket proxy
parity.** The Rust core overestimates apogee by roughly 13.9% relative to
OpenRocket authority runs (measured 2026-07-04, not since re-measured). Close it
with source-backed curve comparisons, not coefficient guesses.

## Operational Status (2026-07-10)

The active authority is OpenRocket 24.12 and the active generator is the
organic AST pipeline. The repaired five-seed gate is recorded in
`designs/or_mode_sweep_or24_12_final_5seed/report.json`: 5/5 successful,
four mission-scale cases within 1.17% apogee, and one 236 m low-altitude case
with 23.80 m absolute / 10.08% relative error. See
`docs/or_mode_checkpoint_2026-07-10.md` for root causes and the next boundary.

*(The note that once stood here about an intentionally dirty worktree is no
longer true. History was squashed to a single audited commit on 2026-07-26.)*

### Superseded status below

Phase 2: Hyper Evolution (Dual Engine Architecture) — now self-contained
- **Rust Engine (`l2_engine`)**: High-speed proxy physics and GA exploration (110+ sims/s). As of
  this session, fully native — `rocket-sim` is ported into `l2_engine/src/sim_core/` and
  `l2_engine_base/` has been removed. No published-repo dependency on external base repos.
- **Python Engine (`l2_hyper`)**: Ground-truth OpenRocket 23.09 physics for finalizing candidate
  designs via micro GA polishing.

## Previous Operational Status (2026-07-06)

The active framework is the organic AST pipeline:
`rocket_ast.py` -> Rust `ast_eval` -> CKG -> sequential OpenRocket 23.09 validation.

The July 1 `.planning/STATE.md` blocker about `rocket-sim` is stale relative to the
July 5 OR-mode checkpoint and this session's verification. The immediate framework
blocker was contract drift between Rust/Python tests and the richer AST evaluation
API; that is now repaired. The next real technical blocker is OpenRocket proxy
parity: keep using `designs/or_mode_sweep_after_launch_guide_wind_5seed/report.json`
as the retained baseline and investigate residual cases with source-backed curve
comparisons before changing drag/atmosphere coefficients.

Legacy fixed-topology entry points such as `evolve.rs`/`optimize.rs` may still exist
in the dirty workspace, but they are not the active design-generation workflow.
Do not use them for new optimization work unless they are explicitly revived and
documented; the organic AST path is authoritative.

## Session Changes (2026-07-04)
1. **Self-contained port**: `rocket-sim` (2,769 lines, MIT/ZenAlexa) fully ported into
   `l2_engine/src/sim_core/`. `l2_engine_base/` (rocket-sim + OpenTsiolkovsky reference +
   analysis doc) removed entirely — nothing outside the publishable tree is required to build.
   Attribution preserved in `l2_engine/THIRD_PARTY_NOTICES.md`. 80/80 tests pass.
2. **Bug fix — motor/airframe fitment** (`docs/organic_loop_report.md` #3): both the AST-evolution
   path and the parametric/declarative-mission path now reject genomes where motor diameter +
   1mm radial clearance exceeds the airframe's inner diameter, centrally enforced in
   `mission_adapter.rs::build_mission`. Regression test in `l2_engine/tests/ast_bridge.rs`
   reproduces the exact N5800-in-36mm-tube exploit from the report.
3. **Bug fix — JPype JVM-restart crash** (`docs/organic_loop_report.md` #5): `organic_loop.py`'s
   `export_elites` now starts the JVM once and shares the `OpenRocketInstance`/`Helper` across
   all elite validations in a run instead of opening a fresh instance per candidate.
4. **Surfaced + fixed a hidden clearance bug, then removed the hardcoding entirely**: enforcing
   the fitment check above revealed that `builder.rs`'s `K_RADIUS`/`S_RADIUS`/`B_RADIUS` were
   hand-picked constants sitting at exactly 1mm (Sustainer) or **0mm** (Booster) motor clearance —
   a knife-edge/impossible fit. Rather than just bumping the magic numbers, `build_geometry` was
   refactored to *derive* each stage's radius + wall thickness directly from the real motor's
   `.eng` diameter (`derive_radius_and_wall`, 3mm design clearance above OpenRocket's 1mm physical
   minimum, wall-thickness break point matching `l2_hyper/generator.py`'s own formula) — this
   makes it structurally impossible for the template to fall out of sync with whatever motor is
   configured, instead of relying on someone remembering to hand-tune a constant. Synced the
   matching `body_radius` fields (0.0420/0.0535/0.0855) across all 8 affected mission JSONs so the
   Rust GA and the `l2_hyper` OpenRocket ground truth build the same physical vehicle. Confirmed:
   the *AST/organic-evolution* pipeline (`rocket_ast.py`/`organic_loop.py`/`ast.rs`) never had this
   problem — body tube radius there is genuinely GA-mutated (bounded jitter, no fixed value), and
   `MOTOR_DATABASE`/`MATERIALS` in `rocket_forge.py` are real OpenRocket-sourced motors (real
   manufacturer/designation/diameter, cross-checked against `.eng` fixtures) and real material
   densities, not fabricated values. `builder.rs` is the separate, intentionally-parametric
   "hyper-evolution" fixed-topology GA (per the locked brute-force-sweep decision), not the
   organic-from-zero system — it was the one with the hardcoding problem, now fixed.
5. **Calibration measured, not yet closed**: ran `or_mode_calibrate.py` against `karman_m6`
   elites (measured just before the radius-derivation refactor in point 4, using the
   then-current 0.0535/0.0845 radii — numbers will shift slightly now that Booster derives to
   0.0855, re-run `or_mode_calibrate.py` next session to refresh). Rust proxy systematically
   overestimates apogee by ~13.9% and Mach by ~0.35 (consistent direction/magnitude across all
   3 candidates); static margin error is smaller and inconsistent in sign (likely noise). Root
   cause is presumed to be `barrowman.rs`'s supersonic/transonic drag modeling being too lenient
   around Mach 6 — needs a broader Mach-sweep calibration dataset before tuning drag tables, not
   a single-point guess fix.

## Session Changes (2026-07-04, continued): retired the fixed-topology GA, made the organic engine dynamic

Prompted by a direct question: "is builder.rs's hardcoding actually gone, and is the organic
engine truly using real OpenRocket parts?" Investigation found the fixed-topology parametric GA
(`l2_engine evolve`) had a deeper problem than the earlier radius fix addressed, and that the
*organic* AST engine had a silent, severe gap of its own. Fixed both:

1. **Retired the fixed-topology pipeline from the active workflow** (confirmed decision: AST
   engine becomes the only design-generation path). The dirty workspace may still contain legacy
   `evolve.rs`/`optimize.rs` entry points, but they are deprecated and excluded from new
   optimization work. `builder.rs` now contains only shared CG/static-margin math
   (`stack_wet_cg`, `static_margins[_with_mode]`) rather than the active topology generator.
2. **Found and fixed the real motor-pool gap**: `ast.rs`'s `motor_designation()` only recognized 3
   hardcoded substrings (O8000/N5800/M2245); every other motor index from `rocket_forge.py`'s
   34-real-motor `MOTOR_DATABASE` silently resolved to an unmatchable placeholder and always
   failed `missing_motor_curve` — meaning the organic GA had, in practice, only ever been able to
   evaluate N5800. Fixed by having `rocket_ast.py` always emit the resolved real designation
   string alongside `motor_index`, and simplifying `ast.rs` to require that string directly (no
   more hardcoded substring matching, no placeholder fallback).
3. **Sourced real motor data directly from OpenRocket**, not hand-transcribed: wrote
   `extract_motors.py` (scratchpad) to pull thrust curves for all 36 real motors (34 from
   `MOTOR_DATABASE` + M2245/O8000, both newly added to the table) straight from OpenRocket
   23.09's own bundled `initial_motors.db` (SQLite, `openrocket/core/.../datafiles/thrustcurves/`).
   This caught and fixed real transcription errors already in the repo: `O8000.eng` had diameter
   161mm hand-typed, the real motor is **150mm** (matched exactly against every other field);
   `rocket_forge.py`'s K510/K1050W/N5800 length entries were also off (fixed against the DB).
   `.eng` filenames stayed as short human labels; each file's internal header designation now
   matches `MOTOR_DATABASE`'s exact designation string (the same string OpenRocket resolves
   `.ork` motors by), so there's one designation per motor everywhere, not a Rust-side alias.
4. **Made the Rust motor loader dynamic**: `ast_eval.rs` now scans `l2_engine/motors/*.eng`
   instead of a hardcoded 3-name list — adding a motor is "drop a `.eng` file in," zero Rust
   changes. Added `motor_db::parse_eng_file` (designation-agnostic parse) to support this.
5. **Fixed a second hardcoded-topology leak**: `stack_wet_cg` (used by both pipelines) placed
   motor wet-mass CG using a hardcoded 3-element `MOTOR_LENGTHS` array assuming the old fixed
   motor family — silently wrong for any AST rocket using a different motor. `ThrustCurve` now
   carries a real `length_m` (parsed from `.eng`), and CG placement derives from the actual
   motor's real length.
6. **Fixed a JSON-serialization bug this surfaced**: `ast.rs`'s failed-candidate result used
   `f64::NEG_INFINITY` for `min_static_margin`, which `serde_json` silently emits as `null` (JSON
   has no Infinity) — crashed `organic_loop.py`'s parser the moment enough real candidates failed
   for genuinely varied reasons (rather than 100% failing the same old way). Now uses a finite
   `-1.0e9` sentinel.
7. **Verified end-to-end**: `organic_loop.py --evaluator rust` now produces elites using a
   genuinely varied real motor pool (observed N2000W, M650W, I218R, L1500T, N2000W in test runs,
   not just N5800), and a top elite's OpenRocket ground-truth polish pass (`--validate-openrocket
   1`) resolved and simulated the AST-compiled `.ork` successfully end to end.

Net effect: the organic evolution engine is now the sole design-generation path, runs on real
OpenRocket-sourced parts/materials/motors with no hardcoded template, and both the Rust CPU
scorer (`ast_eval`, JSON-batch subprocess contract) and the OpenRocket polish pass are
topology-agnostic — the same shape a future GPU evaluator (`docs/l2_gpu_engine.md`) or web UI
would need to plug into.

## Next Actions
- [ ] Compare Rust trajectory curves against OpenRocket `FlightData` for the worst retained
      residual case, especially seed `2026070408`, before changing coefficients. Use
      `or_curve_compare.py` plus the Rust `ast_trace` bin to generate the comparison report.
- [ ] Close the remaining OpenRocket proxy gap only with source-backed evidence measured against
      the retained five-seed baseline.
- [ ] Defer Phase 3 GPU/WGSL work until CPU proxy parity is stable across the five-seed suite.
- [ ] Implement `l2_hyper` unit tests (pytest for genome, mission, generator, orkit).
- [ ] Add integration tests for Rust-to-Python handoff.
