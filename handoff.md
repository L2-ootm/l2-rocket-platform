# Handoff - OSIFOG Level 3 physical Falcon

**STALE as of 2026-07-24 -- this file was last updated 2026-07-23 18:34,
before an entire day's worth of critical fixes and an OSIFOG ruling email.
`.planning/HANDOFF.json` is the current, actively-maintained source of
truth (schema_version 9+ as of this note) -- read that first. Two
corrections from a 2026-07-24 OSIFOG ruling email that this whole file
predates: (1) there is no minimum static margin requirement (any
"1.5 cal" / margin-threshold language below is describing a since-fixed
internal bug, not a real rule), (2) stage separation/retro timing may
occur at any point in the trajectory. This file is kept as a historical
session log -- do not treat its "current state"/"next steps" framing as
current; only `.planning/HANDOFF.json` is updated live.**

## 2026-07-23 (fifth session) — Fin-size widening (v3→v4), octaweb cluster built (tasks 1-4), motor-swap staleness bug fixed live (v4→v5)

Continuation of the same-day margin-plateau session below. Three things
happened, in order:

**1. v3 plateaued at the exact margin boundary for ~500 generations**
(checked at gen 236/490/735, all showing `min_static_margin 1.499999 <
1.500000` on the top elite, bit-identical leading-stage geometry across all
12 elites). Diagnosed as correct elitism behavior on a genuinely narrow
threshold (thousands of fresh-random candidates tried per campaign, none
crossing), not a stuck/broken GA. Nudged the search space: widened main-fin
generation range (`rocket_ast.py::create_random_ast` root/height
multipliers 2.5-4.0x/2.0-3.5x body radius -> 2.5-5.0x/2.0-4.5x) and matched
the sanitizer ceiling (`_sanitize_fin` max_root/max_height 0.45/0.25 ->
0.55/0.4) and mutation jitter bounds (`ASTNode.mutate()` main-fin upper
bounds 0.35/0.22 -> 0.55/0.4) so mutation doesn't clamp a freshly-larger fin
back down. Stopped v3, relaunched as `osifog_campaign_v4`.

**2. Built the octaweb internal 3+1 motor cluster (tasks 1-4 of
`OSIFOG/PLAN_INTERNAL_OCTAWEB_CLUSTER.md`)** — full technical detail is in
that doc, not duplicated here. Summary: added a real Rust hard-constraint
check (`enforce_motor_mount_clearance` in `l2_engine/src/ast.rs`, the one
gap in an engine that previously had exactly one collision check anywhere);
discovered `osifog_sweep.py` already had a complete, validated "3+1
continuous-contact cage" implementation (`_falcon_cluster_geometry`,
`_centering_ring_xml`, `_octaweb_rings_xml`) and ported it into
`rocket_ast.py` rather than rebuilding; added `create_random_ast(...,
octaweb_probability=0.0)` (default off, not yet wired into any live
campaign -- needs OpenRocket JVM validation first, same bar the
forward-flap work cleared). 171/171 Rust tests passing, zero regressions.

**3. Found and fixed a live bug affecting the ALREADY-RUNNING campaign**,
discovered while testing octaweb: `organic_loop.py::prepare_ast_for_rust`
reassigns a motor whenever the original pick has no Rust curve data, but
was leaving `radial_offset_m` stale (derived from the *old* motor's
diameter) -- not octaweb-specific, the plain single-retro-motor topology
has the exact same staleness path. Checked `osifog_campaign_v4` (running at
the time): **22/96 (~23%) of every generation was failing on phantom
`motor_mount_collision` from this staleness alone**, unrelated to genome
quality. Fixed with `_repair_radial_offsets_after_reassignment` +
`_repair_stage_motor_geometry` in `organic_loop.py`, recomputing offset
geometry fresh from the stage's current motor selection whenever a
reassignment happens. Verified: 22/96 -> 0/96 in a matched re-test. Stopped
v4, relaunched as `osifog_campaign_v5` with the fix.

Also worth noting for future sessions: mid-Rust-edit, `organic_campaign.py`'s
own source-drift auto-rebuild detected the in-progress `ast.rs` change
(briefly referencing a not-yet-defined function between two sequential Edit
calls) and hit 2 consecutive compile-failure retries on the then-running
campaign -- self-recovered once the edit completed, no manual intervention
needed. Good live proof the retry/backoff design holds up, but batch
multi-part Rust edits into single tool calls when a campaign is live, to
minimize the exposure window.

### Live status at end of session

`designs/osifog_level3/osifog_campaign_v5/` running (watchdog-supervised,
same launch shape as v3/v4, `--seed 16180`). `osifog_campaign_v1` through
`v4` left on disk for reference/audit, not for resuming. No candidate has
reached a computed official score yet -- that remains the open problem.

## 2026-07-23 (fourth session) — v2 still 0% legal after 330+ generations: fins/flap sized independently of body radius, fixed, relaunched as v3

The user asked why v2 (previous session, below) was still stuck at 0%
legality after 330+ generations even with the constraints-wiring bug fixed
and the closeness-ratio gradient in place. `population_stats` showed
`min_static_margin` dominating failures (69/96, ~72%). Rather than assume
"just needs more generations," pulled actual margin values out of the
`reason` strings (`enforce_hard_constraints`'s error text) across a 60-
candidate direct sample: median margin was **-0.865**, i.e. most random
candidates aren't marginally short of the 1.5-caliber requirement, they're
deeply aerodynamically unstable (CP at or ahead of CG).

### Root cause: fin and forward-flap sizing were independent random draws, uncorrelated with body radius

`rocket_ast.py::create_random_ast`: `BODY_TUBE.radius` was drawn from
`random.uniform(0.02, 0.08)` and immediately discarded (not stored in a
variable); the main `FIN_SET`'s `root`/`height` were then drawn from a
*separate* fixed absolute range (`random.uniform(0.05, 0.2)` /
`random.uniform(0.04, 0.15)`) with no relation to whatever body radius that
candidate happened to get. Worse, `forward_flap_node()`'s root/height came
from the mission's `evolution.physical_repair_space` (`forward_fin_root_m:
[0.12, 0.15, 0.18, 0.2]`), fixed absolute meters tuned against
`starship_best_genome.json`'s ~0.08-0.09m reference body -- but
`create_random_ast` regularly generates body radii as small as 0.02m, so
the "flap" was routinely *larger than the body tube it was mounted on*: a
full second finset stapled to the nose, not the modest destabilizing nudge
the tail-first-descent mechanism needs. `_sanitize_fin` does clamp
non-flap fins to a body-radius-relative floor already
(`min_root = max(0.03, body_radius*1.2)`), but that only prevents
absurdly-small fins on a large body -- it does nothing to correlate size
*upward* for a given body, and the forward-flap branch has no body-radius
awareness in the sanitizer at all.

Verified with a controlled test (`create_random_ast`-style generation, same
seed logic, isolating one variable at a time): scaling main fins to
2-4x body radius alone still left median margin around -0.5 with the flap
present; removing the flap (all else equal) improved median margin and
roughly tripled how often candidates got past margin/mach/height to reach
the landing-requirement check. The flap's disproportionate size (independent
of body radius) is a real, substantial contributor on top of the general
fin/body decorrelation.

### Fix

- `rocket_ast.py::create_random_ast`: `body_radius` is now captured in a
  variable and used to scale the main `FIN_SET` (`root = body_radius *
  uniform(2.5, 4.0)`, `height = body_radius * uniform(2.0, 3.5)`) instead of
  independent absolute draws.
- `rocket_ast.py::forward_flap_node(repair_space, body_radius=None)`: new
  optional `body_radius` param. When given, sizes the flap proportionally
  (`root = body_radius * uniform(0.6, 1.1)`, `height = body_radius *
  uniform(0.5, 0.9)`) instead of using the mission's fixed absolute
  `forward_fin_root_m`/`height_m` choices, which stay as the fallback when
  `body_radius` isn't known (e.g. some historical callers).
  `create_random_ast` now passes its `body_radius` through.
- `organic_loop.py::_structural_mutation`: added `_nearest_body_radius(
  ast_nodes, insert_at)` (walks back to the last `BODY_TUBE` before the
  insertion point) and threads it into both the `FIN_SET` and
  `FORWARD_FLAP` mutation branches, so structurally-mutated-in fins/flaps
  get the same proportional treatment as freshly generated ones.
- `rocket_ast.py::ASTNode.mutate()`: the `FIN_SET` jitter branch didn't
  distinguish `role="forward_flap"` from main fins, so repeated mutation
  could drift an already-correctly-sized flap back up toward main-fin bounds
  (up to 0.35m root). Now jitters forward-flap fins within
  `_sanitize_fin`'s discriminated bounds (root 0.04-0.20m, height
  0.03-0.15m, tighter step) instead of the main-fin bounds.

### Verification

Direct sample of 80 freshly-generated candidates through the real (patched)
`create_random_ast` + Rust evaluator: median margin-failure moved from
**-0.865 to -0.082** -- close enough to the 1.5-caliber requirement that
mutation now has a real gradient to close, instead of needing to climb out
of a deep hole. Max margin-failure value observed: 1.465 (just under the
1.5 bar). `not_all_stages_landed` failures (meaning margin/mach/height all
passed) went from ~3% to ~11% of the population. Test suite: same 89
passed / 1 pre-existing-unrelated-failure result as prior sessions (see
below); no regressions.

### v2 stopped, v3 launched

Killed v2's full process tree (watchdog -> `organic_campaign.py` ->
`ast_eval.exe --serve`). Relaunched fresh into
`designs/osifog_level3/osifog_campaign_v3/` (same launch shape as v2/v1,
`--seed 31415`) -- v2's checkpoint isn't a valid seed since its population
was generated under the old decorrelated sizing. Confirmed running
(`health.json` PID present, `status: running`) immediately after launch.
`osifog_campaign_v1/` and `osifog_campaign_v2/` left on disk for
reference/audit, not for resuming.

### Not done / open

- Fin/flap sizing is now proportional but still uses fairly wide random
  multiplier ranges (e.g. main fin root 2.5-4.0x radius) -- if v3 still
  struggles to clear 1.5-caliber margin after many generations, narrowing
  those ranges toward the higher end (more margin-safe by default, let
  mutation shrink from there) is the next lever, along with checking
  whether the CG-aft-pulling mass of the retro motor + its ballast is also
  a material contributor (not isolated in this session's testing).
- `_structural_mutation`'s `insert_at = max(1, len(ast_nodes) - 1)` targets
  the end of the whole (possibly multi-stage) AST, not necessarily the
  "current" stage in any principled way -- `_nearest_body_radius` works
  around this by taking whatever `BODY_TUBE` precedes the insertion point,
  but the insertion-point logic itself wasn't redesigned this session.

## 2026-07-23 (third session) — Campaign v1 invalidated by a constraints-wiring bug; fixed, widened, relaunched as v2

The user asked to check on the running v1 campaign (from the second session
below). Its `health.json` looked healthy (97% "legality", 286+ generations,
no crashes), but digging into `best-candidate.json` showed the "best"
candidate had apogee 6459m against a 3000m target with `stages_landed: 0`
and `official_score_breakdown.complete: False` -- i.e. after ~290
generations, nothing had ever landed, and the official 900000-scale formula
score was `null` for every candidate ever evaluated. The population
`score_max` (~80) the user asked about is **not** the official formula --
it's `ast.rs::score_summary`'s internal proxy fitness
(`(apogee-target-closeness*100 + mach) * margin_factor`), used only for GA
ranking.

### Root cause: `organic_campaign.py` never passed the mission's `constraints`

`organic_campaign.py:240`'s `OrganicLoopConfig(...)` call never set
`constraints=`. The dataclass default is `None`, and that `None` flows
straight through `run_rust_evaluator` into the Rust evaluator's JSON payload
(`organic_loop.py:967`: `{**(constraints or {}), "target_apogee_m": ...}` --
with `constraints=None` this collapses to just `{"target_apogee_m": ...}`).
Every check in `l2_engine/src/ast.rs::enforce_hard_constraints` (min
static margin, max mach, max height, `require_all_stages_land`, max
touchdown speed) is `constraints.get(key)`-gated, so **all of them were
silently disabled** for the entire v1 campaign. Only structural/motor-
adequacy checks and the AST-generation-time stage count (read directly from
the mission file elsewhere in `run_generation`, not through
`config.constraints`) were ever active. This is why "success" candidates
existed with zero landings and wildly off-target apogee -- the physics
evaluator was never told any of that mattered. v1's checkpoint
(`osifog_campaign_v1/organic_elite.json`) and all its progress are invalid
and were not reused.

### Fix 1 -- wire the real constraints through

`organic_campaign.py:240`: added `constraints=mission_data.get("constraints",
{})` and `objectives=mission_data.get("objectives", [])` to the
`OrganicLoopConfig(...)` call. Verified live: a 48-pop/3-generation smoke run
immediately dropped `legality_rate` to 0.0% with real failure reasons
(`min_static_margin` 41/48, `max_height_m` 6/48, `max_mach` 1/48) -- proof
the gate is now actually biting instead of rubber-stamping.

### Fix 2 -- widened the stage-count search space

`missions/osifog_l3_precision.json`: `constraints.max_stages` was `2`
(exactly, no exploration), even though the official disqualifier only bans
`< 2` stages -- there's no stated ceiling. This matches the user's original
ask ("double stage or more stages") and the user's later explicit request to
let the algorithm "generate more freely... within the rules and constraints
we have." Changed to `max_stages: 3`. Confirmed `evolution.physical_repair_space`'s
`s0_*`/`s1_*`/`topology` keys are dead config -- not read anywhere in
`rocket_ast.py` or `organic_loop.py` (only `forward_fin_*` keys are
consumed) -- so widening stage count doesn't collide with any stage-indexed
config. Verified: smoke-test best candidate had 3 STAGE nodes.

### Fix 3 -- closeness-ratio tiebreaking for illegal candidates

Hard-gating landing outcomes (`require_all_stages_land`,
`max_touchdown_speed_ms`) as pass/fail -- necessary for correctness, since
the official rules really do require it -- means the entire population fails
until something lands by chance. Empirically confirmed this produced a
**flat `score=0.0` for every failed candidate** (all 48/48 in the smoke
test), i.e. zero selection gradient, pure random-walk mutation. Fixed in
`l2_engine/src/ast.rs`:
- `enforce_hard_constraints` now embeds a `[0,1]` closeness-to-passing ratio
  into each constraint-violation error string via a new `violation()` helper
  and `CLOSENESS_SEPARATOR` (`\u{0}`, a NUL byte -- safe since it can't
  appear in the formatted reason text). E.g. for `max_height_m`:
  `ratio = limit / total_height_m`; for `min_static_margin`:
  `ratio = min_margin.max(0.0) / required`; for `require_all_stages_land`:
  `ratio = stages_landed / stages_required`.
- `evaluate_ast_with_profile`'s `Err` branch now splits the error on
  `CLOSENESS_SEPARATOR`, uses the ratio as `AstEvalResult.score` (was
  hardcoded `0.0`), and strips it back out of `reason` before returning.
  Errors with no embedded ratio (structural/parse failures that occur before
  any constraint check -- unbuildable, not "almost legal") fall back to
  `0.0` as before.
- `organic_loop.py`: both places that computed
  `score = raw_score * multiplier if status == "success" else 0.0`
  (the main per-generation evaluation loop and `promote_candidates`) now
  just do `score = raw_score * multiplier` unconditionally -- `raw_score` is
  always a meaningful signal now (proxy fitness for legal, closeness ratio
  for illegal). **This never changes what counts as legal** --
  `selection_rank` still ranks every `status="success"` above every
  `status="failed"` regardless of this magnitude; it only breaks ties within
  the "failed" bucket so mutation has something to climb.
- Rebuilt `l2_engine` release binary; `cargo test --release hard_constraints`
  (2 tests, both check `enforce_hard_constraints`'s `Err` reason via
  `starts_with(...)`, which still holds since the ratio is appended, not
  prepended) passes.
- Re-verified live: same smoke-test shape now shows
  `score_min=0.0, score_median=0.25, score_max=0.56` -- a real distribution
  -- and 4/48 candidates got far enough to hit the
  `not_all_stages_landed` check specifically (meaning margin/mach/height all
  passed for them), which never happened before this fix.

### Verification

`pytest tests/test_organic_campaign.py tests/test_campaign_infra.py
tests/test_forward_flap_genome.py tests/test_organic_evolution.py` --
89 passed, 1 failed. The failure
(`test_run_rust_evaluator_batch_defaults_to_openrocket`) is a pre-existing
cross-file test-isolation issue (passes standalone: `pytest
tests/test_organic_evolution.py` is 48/48 green; only fails when run
combined with the other 3 files, reproducible on the pre-fix code too via
`git stash`) -- not a regression from this session's changes.

### Campaign v1 stopped, v2 launched

Killed the full v1 process tree (watchdog PID 22592 -> `organic_campaign.py`
PID 3120 -> `ast_eval.exe --serve` PID 3088) via `Stop-Process`. Relaunched
fresh into a new output directory (not resuming v1's checkpoint, since its
elites were selected under the broken landscape and aren't valid seeds):

```
python osifog_campaign_watchdog.py \
  --root designs/osifog_level3/osifog_campaign_v2 \
  --interval 20 --max-restarts 40 \
  -- organic_campaign.py \
     --mission missions/osifog_l3_precision.json \
     --out designs/osifog_level3/osifog_campaign_v2 \
     --population 96 --elite-count 12 --generations-per-cycle 5 \
     --execution-profile super-speed --calibrate-every 20 \
     --validate-openrocket 4 --target-score 0.0 --max-hours 48 --seed 9001
```

Confirmed running (PID 14212 at launch), `alert.json` clear,
`legality_rate: 0.0` in `health.json` -- which is now the *honest* number,
not a hidden bug. `designs/osifog_level3/osifog_campaign_v1/` was left on
disk for reference/audit but should not be resumed from.

### Not done / open

- No landing-progress fitness term beyond the closeness-ratio tiebreak was
  added (e.g. rewarding descent stability or partial retro-burn execution
  before touchdown) -- if v2 goes many generations without a single legal
  candidate, that's the next lever to pull.
- `max_stages` was widened to 3, not left unbounded -- if 3 also doesn't
  converge, consider widening further, but each extra stage adds real
  search-space and simulation-cost overhead.

## 2026-07-23 (second session) — Long campaign launched, with 3 critical fitness-landscape bugs fixed first

Continuation of the same-day forward-flap session below. The user asked to
prepare and launch a long, idempotent, antifragile campaign with rich
self-monitoring -- but **verifying the generation workflow first** (per
their explicit instruction) surfaced three previously-undiscovered, severe
bugs in `organic_loop.py`/`ckg_memory.py` that would have silently sabotaged
any long run. All three are fixed, tested, and empirically re-verified
before the campaign was launched.

### Bugs found and fixed (in the order discovered)

1. **CKG prefilter runaway positive-feedback loop** (`organic_loop.py`,
   `run_generation`'s per-generation loop): candidates rejected by the CKG's
   own `acceptance_multiplier_for_items` gate (`reason="ckg_prefilter"`,
   never reached real Rust evaluation) were still being fed back into
   `ckg.record_items(...)` as another recorded failure on the same shared
   subgraph keys -- i.e. the prefilter's own output was being recorded as
   new evidence for itself. A fresh 24-population/4-generation verify run
   with **zero** real successes recorded collapsed to 100% `ckg_prefilter`
   rejections by generation 4. Fix: skip `record_items` entirely for
   `ckg_prefilter` candidates -- only genuinely-evaluated outcomes may shape
   this memory.
2. **CKG penalizes zero-information node labels** (`ckg_memory.py::
   subgraph_items`): `STAGE` and `CLOSE_BODY` nodes carry no discriminating
   geometric/physical information (every candidate has exactly one of each
   per stage, near-constant params), so in a hard mission with a naturally
   low early-generation success rate, EVERY candidate accumulates
   "failures" on these two near-universal keys regardless of actual
   quality -- even after fixing bug 1, a 6-generation run still collapsed
   because generation 1's honest ~4% legality rate alone pushed these keys'
   penalty over threshold. Fix: exclude `STAGE`/`CLOSE_BODY` from
   `subgraph_items`.
3. **The official formula's fail-closed sentinel inverted the GA's
   selection gradient** (most severe; likely the deepest root cause of this
   project's long "7 million evaluated, none good" history): a
   status="success" (ascent-legal) candidate whose flight never completes a
   legal landing gets `SCORING_FAILURE_SENTINEL = -1e9` from
   `l2_engine/src/ast.rs::evaluate_scoring_table` (and even a genuinely
   *computed* quadratic apogee-miss penalty can exceed -1e9 in magnitude).
   Sorting on raw `.score` meant any ascent-legal-but-not-yet-landed
   candidate (which is objectively *closer* to a working design) scored far
   below a `status="failed"` (never left the pad) candidate's floor of
   `0.0` -- the GA was structurally rewarded for staying illegal on ascent
   over reaching descent without landing yet. Confirmed empirically: a
   24-candidate generation-1 batch had 1 real ascent-legal success at score
   -1e9, ranked *last* out of 24. Fix: new `organic_loop.py::selection_rank`
   sort key -- `(status=="success", score)` -- so legal always ranks above
   illegal regardless of score magnitude; wired into all three
   `evaluated.sort(...)` call sites. **This also required updating/rewriting
   one pre-existing test** (`test_organic_loop_ckg_prefilters_repeated_rust_
   failures` -> `test_organic_loop_repeated_rust_failures_never_block_
   evaluation`) that had asserted the old (harmful) ckg_prefilter behavior
   as correct.

Empirical re-verification after all three fixes (direct `evaluate_rust_
population` loop, bypassing the CLI, mirroring `run_generation`'s exact
selection logic): ascent-legal candidate count grew **1 -> 3 -> 11 -> 18**
across 5 generations of a 24-population run (screening-fidelity count; the
promoted/"balanced"-fidelity full-mission check is stricter and correctly
still fails candidates that haven't found a landing yet -- that remains the
genuinely hard, unsolved part, not a bug). At 96 population, one candidate
already reached the landing-telemetry phase (`stage_landings` populated) by
generation 5. **Full test suite for the changed files: 95 passing
(organic_evolution, forward_flap_genome, or_mode_ast_sweep,
campaign_infra, organic_campaign)**, no regressions.

### Campaign infrastructure built (new files)

- `campaign_infra.py` -- shared, topology-agnostic idempotency/antifragility
  primitives (atomic JSON write with temp-file+fsync+rename, PID liveness
  check, `campaign_lease()` crash-recoverable exclusive lock via
  `O_CREAT|O_EXCL`, append-only `events.jsonl`). Consolidated from the two
  near-duplicate implementations already living in `osifog_engine_search.py`
  and `osifog_campaign_watchdog.py` so this is one tested implementation,
  not a third copy.
- `organic_loop.py::official_score_breakdown()` (new) -- mirrors
  `l2_engine/src/ast.rs::evaluate_scoring_table` term-for-term in Python
  from an already-evaluated candidate's raw metrics, for monitoring/human
  consumption. Reports a term as incomplete (`value: null`) rather than
  fabricating a number when data is missing (e.g. a stage that never
  landed) -- verified against hand-computed values in
  `tests/test_forward_flap_genome.py`. `OrganicLoopConfig` also gained a
  `progress_callback` hook, invoked by `run_generation` after every single
  generation (not just every N-generation cycle) with the full evaluated
  population, giving monitoring per-generation granularity.
- `organic_campaign.py` (new) -- the actual long-running, resumable
  campaign runner wrapping `run_generation` in a retry-hardened outer loop.
  Design documented in its own module docstring; summary: one campaign =
  one `--out` directory, protected by `campaign_lease()`; resumes
  automatically from `organic_elite.json` (already written every generation
  by `export_elites`, in the exact shape `seed_from` reads back) instead of
  restarting from scratch; a cycle's exception is retried with backoff up
  to `--max-consecutive-failures` (default 3) before escalating to a
  terminal `"blocked"` state with `alert.json` as the recovery artifact;
  writes `best-candidate.json` after every generation with the best
  candidate's full metrics **and** the official-formula term-by-term
  breakdown, plus population-level stats (legality rate, score
  min/median/max, failure-reason histogram).
- `osifog_campaign_watchdog.py` (pre-existing, reused unmodified) -- external
  process guardian; only needs `campaign.lease.json`'s `pid` and
  `campaign-state.json`'s `status`, both of which `organic_campaign.py`
  produces, so no changes were needed there.
- Tests: `tests/test_campaign_infra.py` (6, lease exclusivity/reclaim,
  atomic-write correctness), `tests/test_organic_campaign.py` (2, monitoring
  file completeness, cross-restart cumulative-progress resumption -- this
  test's first draft caught a real design ambiguity: `--max-cycles` is a
  cumulative lifetime budget across restarts, not a per-invocation count).

### Campaign launched and currently running

```powershell
python osifog_campaign_watchdog.py --root designs/osifog_level3/osifog_campaign_v1 --interval 20 --max-restarts 40 -- organic_campaign.py --mission missions/osifog_l3_precision.json --out designs/osifog_level3/osifog_campaign_v1 --population 96 --elite-count 12 --generations-per-cycle 5 --execution-profile super-speed --calibrate-every 20 --validate-openrocket 4 --target-score 0.0 --max-hours 48 --seed 4242
```

Strategy rationale: `--execution-profile super-speed` evaluates the full
population cheaply (ascent-only screen), and `MODE_PROFILES["super-speed"]`
already auto-promotes ~5%/generation to the `"balanced"` (full-mission,
landing-inclusive) fidelity -- broad cheap exploration, expensive checks
only on the most promising slice. `--target-score 0.0` matches the user's
"reach legal/~0 first, optimize further only after" directive; raise it and
relaunch (it will resume, not restart) once 0 is reached.

**Status at launch** (first ~3 minutes): 16 cycles / 80 generations
completed, zero real failures, ~97% ascent-legality rate, one candidate
already reached landing telemetry at population 96. Live status:
`designs/osifog_level3/osifog_campaign_v1/health.json` (heartbeat, updated
every generation) and `best-candidate.json` (best candidate's full metrics
+ official score breakdown + population stats, also every generation).
`campaign-state.json`'s `status` reaches one of `goal_reached` /
`budget_exhausted` / `blocked` when the campaign stops on its own;
`alert.json` shows the latest issue if any; `events.jsonl` is the full
append-only history. **Do not delete `designs/osifog_level3/
osifog_campaign_v1/organic_elite.json` or `campaign_ckg.json`** -- resuming
depends on both.

To resume after an intentional stop or a machine restart, rerun the exact
same watchdog command above (same `--out`) -- it will auto-detect the
checkpoint and continue `cumulative_generations` from where it left off,
not from zero.

## 2026-07-23 — Forward-flap genome wired into the AST/GA pipeline (rocket_ast.py/organic_loop.py)

Prior state (see 2026-07-22 section below): the Starship-forward-flap descent
mechanism (nose flaps force passive tail-first fall, no active control) was
proven promising in a one-off side experiment
(`designs/osifog_level3/starship_best_genome.json`, q=0.995 tail-first
alignment) but was built in a different pipeline
(`osifog_engine_search.py`/`osifog_podset.py`) and never reached the pipeline
`organic_loop.py`/`rocket_ast.py`/`l2_engine` (the AST/GA + Rust proxy loop)
actually runs -- that generator had zero flap parameterization, no physical
collision gate, and (a separately discovered, more serious bug) its default
`create_random_ast()` path added a `PARACHUTE` unconditionally, an automatic
OSIFOG disqualifier, and `sanitize_ast_for_openrocket()` re-injected a
fallback chute regardless even when the caller avoided the initial node.

User decision this session (after a deadline-risk tradeoff discussion): keep
building on this pipeline (not the more-complete-but-separate
`osifog_engine_search.py` track), because it's the intended long-term
architecture, even though it required more net-new plumbing.

**What changed (Python only -- no Rust source touched; Rust already
supported everything needed: `position_from_top_m` for FIN_SET,
`role`/`ignition_delay`/`radial_offset_m` for MOTOR_MOUNT, all confirmed by
reading `l2_engine/src/ast.rs` and `l2_engine/src/mission_adapter.rs`):**

1. **Fin-position parity bug fixed** (`rocket_ast.py::_fin_xml`/`_sanitize_fin`):
   the OpenRocket XML compiler hardcoded `<position type="bottom">0.0</position>`
   for every fin set, silently discarding the `position_from_top_m` param
   Rust already read -- exactly the "Rust and OpenRocket score different
   vehicles" bug class that killed a prior campaign
   (`.planning/ultra/ULTRAREVIEW_campaign-v2-no-results.md`). Now emits
   `<position type="top">`.
2. **Forward-flap genome** (`rocket_ast.py::forward_flap_node`,
   `DEFAULT_FORWARD_FLAP_SPACE`): a `FIN_SET(role="forward_flap")` pinned to
   0-0.15m from the nose, ranges sourced from
   `missions/osifog_l3_precision.json`'s existing (previously unused)
   `evolution.physical_repair_space.forward_fin_*` keys. Wired into
   `create_random_ast(forward_flap_probability=...)`,
   `ASTNode.mutate()`, and `organic_loop.py::_structural_mutation()`.
   `run_generation()` now reads the mission's repair space and sets
   `forward_flap_probability=1.0` automatically whenever a mission declares
   one (OSIFOG's whole premise is that a plain aft-fin topology cannot reach
   legal tail-first descent at all, so this isn't an optional mutation).
3. **Retro motor mount genome**: `create_random_ast(retro_motor_pool=...,
   retro_motor_probability=...)` adds a second, `role="retro"` `MOTOR_MOUNT`
   per stage (`ignition="burnout"`, `ignition_delay` searchable 0-30s --
   confirmed via `mission_adapter.rs:538-540` that Rust interprets
   `ignition="burnout"` as "N seconds after this stage's own main-motor
   burnout", which is the retro-delay search semantics needed). Restricted
   to the mission's `motor_pool.retro_allowed_designations`, and that
   restriction now survives mutation and the Rust-availability fallback path
   (`ASTNode.mutate`, `prepare_ast_for_rust`) -- previously mutation could
   silently re-roll a motor outside its declared pool.
4. **Motor-mount radial collision guard** (`rocket_ast.py::_sanitize_body`):
   a second same-stage motor mount left at the implicit centerline
   (`radial_offset_m=0`) would have physically overlapped the main motor.
   `_sanitize_body` now enforces `radial_offset_m >= centerline_radius +
   own_radius + clearance` for any off-centerline motor and sizes the body
   bore to actually contain it -- generation seeds a safe offset already,
   this is the authoritative enforcement.
5. **Disqualifying-parachute bug fixed**: `create_random_ast(
   no_recovery_devices=True)` both skips the initial `PARACHUTE` node and
   sets `STAGE(recovery="retro_only")`, which is what
   `sanitize_ast_for_openrocket`'s existing (previously always-false) opt-out
   check needed to actually suppress its own fallback chute injection.
   `run_generation()` sets this automatically from the mission's
   `constraints.no_recovery_devices`.

**Verification:**
- New `tests/test_forward_flap_genome.py` (32 cases): no-parachute-leak
  across generation/mutation/compile, flap position/size bounds, retro motor
  pool integrity across 25 rounds of mutation, radial clearance per stage.
- Existing `tests/test_organic_evolution.py` (48), `tests/test_or_mode_ast_sweep.py`
  (5), `tests/test_orhelper.py` (1, real JVM) all still pass -- no regressions.
- **Real OpenRocket JVM check** (ad hoc, not yet a committed test): compiled
  a 2-stage flap+retro candidate and loaded it in the actual OpenRocket
  24.12 JVM via `orhelper`. Confirmed both `Forward Flap` fin sets and both
  `Retro Motor Mount`s survive parsing intact (`FIN_SETS: ['Evolved Fins',
  'Forward Flap', 'Evolved Fins', 'Forward Flap']`, `MOTOR_MOUNTS: ['Motor
  Mount', 'Retro Motor Mount', 'Motor Mount', 'Retro Motor Mount']`) --
  directly refutes the "OpenRocket silently drops evolved forward fins"
  failure mode from the prior campaign. A full `validate_openrocket_ork` run
  completed without a parse/geometry crash (this specific random-seed
  candidate aborted on ascent stability, `-1.48 cal` margin -- expected for
  an untuned single random draw, not a defect; the GA's selection pressure
  is what's supposed to filter this out over generations, not raw sampling).

**Explicitly NOT done this session (still open):**
- No actual GA campaign has been run yet against
  `missions/osifog_l3_precision.json` with these new generation defaults --
  next session should start with a real `organic_loop.py --mission
  missions/osifog_l3_precision.json --evaluator rust` run and look at
  whether touchdown-speed/score trends improve over generations now that
  flap+retro candidates are actually being generated, instead of assuming it
  will work from unit tests alone.
- The mission-legality hard gate (retro opposing-velocity-fraction >=90%,
  stage-separation timing, anti-tumble script integrity from
  `osifog_sweep.py::validate_hard_constraints`) is still **not** ported into
  `l2_engine/src/ast.rs::enforce_hard_constraints` for this pipeline --
  today, legality is only implicitly pressured through the official score's
  existing `touchdown_speed`/`apogee`/`propellant` terms (already present in
  the mission's `scoring.terms` and already wired through Rust's
  `ScoringTable`), not hard-gated. Whether that's sufficient selection
  pressure on its own is unverified -- an empirical question for the next
  GA run, not yet an engineering fact.
- POD/pylon-based 3-motor-cluster generation (the doctrine's "3 main + 1
  central retro" cage) is still not activated -- the retro motor here is a
  second direct `MOTOR_MOUNT` sibling of a single main motor, not a
  `PODSET`-based cluster. Still a real gap (no pylon geometry exists in Rust
  at all) -- classified stretch/Day-3 scope per this session's plan.
- A translated seed population from `starship_best_genome.json` (to
  jump-start convergence instead of relying on `forward_flap_probability=1.0`
  random sampling alone) was scoped but not built this session.

Deadline unchanged: **2026-07-26 23:59 BRT**.

## 2026-07-22 — Starship Forward-Flap Concept (new track, superseding 2026-07-21 PodSet track)

The PodSet search campaign (v2-v7) reached a dead end: all 24 candidates per
generation failed the stability gate because a stable sustainer (SM >= 1.5 cal)
never achieves tail-first attitude during descent. The user identified the
fundamental physics insight: forward-mounted flaps (Starship-style) create
aerodynamic drag at the nose during broadside fall, forcing natural tail-first
rotation without active control.

**Key changes implemented:**

1. **Continuous genome** — all parameters are smooth sliders (uniform ranges)
   instead of discrete rng.choice() menus. Enables smooth optimizer navigation.

2. **Starship forward flaps** — 3 large fins near the nose (0.05-0.15m root,
   0.04-0.12m height, position 0.02-0.15m from nose). These create the
   nose-up moment during broadside descent.

3. **Corrected stability gate** — sustainer stability now measured only from
   separation to sustainer motor burnout (not end of flight). This allows
   designs where the sustainer is stable during powered flight but unstable
   after burnout (enabling tail-first descent).

4. **Lowered stability threshold** — MIN_STATIC_MARGIN = 0.3 cal (was 1.5).
   Mission requires SM > 0 (stable), 0.3 provides safety margin.

5. **Fixed delay selection** — window midpoints promoted to vertical_priority
   in `_delay_candidates`. Limit increased from 15 to 25 per branch.

6. **Geometry fixes** — pod axial offset starts at 0.0 (was negative), core
   length includes pod_nose_length, core fin root clamped to 95% of body.

**First results (8 candidates tested):**
- All 8 pass `powered_trials_completed` gate (was 0% before)
- Booster retro at 100% opposing velocity (candidate #0)
- Sustainer retro at 17% opposing (needs timing tuning)
- 913/913 descent samples tail-first (q > 0 throughout)
- Best alignment_q: 0.995 (near-perfect tail-first)

**Remaining work:**
- Retro timing: motor must burn at touchdown (< 5 m/s)
- Apogee targeting: current 1000-2100m, need ~3000m
- Evolutionary search with new genome
- Landing speed < 5 m/s for both stages

**Files saved:**
- `designs/osifog_level3/starship_best_genome.json` — best candidate params
- `OSIFOG/MISSION_STATUS.md` — mission status and technical plan
- `OSIFOG/SOURCE_MAP.md` — codebase navigation guide

**Next session:** Start with the evolutionary search campaign, focusing on
retro timing calibration (motor must be on at touchdown) and apogee targeting.

## 2026-07-21 — New track: PodSet external 3+1 architecture (separate from all tracks below)

A same-day session found the previously-reported "LEGAL BOOSTER BRANCH
RECOVERED" claim (phase4c/phase5a below) is **FALSE** -- a numerical
convergence audit (fixed a real `.3f`-precision-truncation bug in
`osifog_sweep.py`'s timestep serialization along the way) showed the
touchdown was only ever legal at the official 0.05s timestep and reverses
to ~10-15 m/s at every finer timestep, with a 100% reversal rate across the
9 delays x 5 timesteps tested. Full detail:
`artifacts/autoevo/phase5b/booster-numerical-convergence-classification.json`,
`.planning/current-authority.md`.

Given that, the user redirected the session to a new vehicle topology:
externally-podded 3+1 (3 side pods + 1 central retro motor per stage, via
OpenRocket's native `PodSet` -- not `ParallelStage`, which would create
illegal extra flight branches). This is now a **separate, new track**
(`osifog_podset.py`), not a continuation of the autoevo/phase5a-5b booster/
sustainer work described further below in this file -- that track's status
is unchanged (still no legal booster branch, still no legal sustainer
branch, both now additionally shadowed by the timestep finding above).

**Architecture decisions, full technical findings, and both a Rust-engine-
integration path and an OpenRocket-only-brute-force continuation path are
written up in full in `.planning/PODSET-EXTERNAL-3PLUS1-ARCHITECTURE.md`
and `OSIFOG/OSIFOG_Level3_PodSet_Findings.md` -- read those before
continuing this track.** Summary: `PodSet` verified (against the actual
bundled OpenRocket 24.12 jar) to have no separation semantics; two real
bugs found and fixed (dead nose-ballast parameter, pod fins able to punch
through the core due to a fin-height-vs-clearance gap no one had checked);
real physical pylon struts added (pods were simulation-correct but
visually/structurally floating before that); motor selection widened from
the 38-motor curated `MOTOR_DATABASE` to the full 1458-motor local
OpenRocket catalog via live SQLite lookup.

**UPDATE, same session: the "1999 m apogee, Mach 0.795, margin 2.05 cal"
result above is RETRACTED.** The user opened the candidate in the
OpenRocket GUI and found three more real defects visual inspection caught
that the numbers alone didn't: (1) the central retro motor mount rendered
**empty** -- `resolve_motor()` was picking a motor-catalog row that exists
in the raw SQLite file but is NOT actually loaded in OpenRocket's own live
motor database, so the file loader silently dropped the motor reference
(real reproduced error: `IllegalArgumentException: empty MotorInstance ...
ignoring`); (2) nose ballast **physically overflowed 13.7 cm past the end
of the nose cone** into the core tube -- 2.5 kg of steel doesn't fit in
that small a cavity, and nothing checked; (3) pylons rendered as **fat,
disconnected-looking cylinders** instead of recognizable struts (correct
on paper -- verified to touch both surfaces -- but a tube's thickness is
tied to its radius, so a strut spanning a 3.8 cm gap looked like a stubby
3.8 cm-diameter blob). All three are fixed: motors now resolve from
OpenRocket's own live `MotorSetDatabase` with a real digest computed via
its own `MotorDigest.digestMotor()`; ballast now hard-rejects if it doesn't
fit its cavity; pylons are now single-fin `freeformfinset` blades
(`fincount=1` + `<rotation>`) instead of tubes. Full detail in both docs
above (updated).

**Honest current state: no legal ascent candidate is confirmed.** The
"win" above was resting on the ballast overflow (that extra mass was
being counted by the physics even though its geometry was invalid) --
once fixed, the same search budget's closest miss is Mach 1.31 / margin
0.31 (both illegal). The user's own conclusion: "we will need real physics
behind and verifiers to build real rockets" -- i.e. a proper hard-gated
physical validator is worth building as its own deliverable. Retro/landing
motors are still disabled in every candidate tested (ascent-legality-only
session). File (now stale/invalid, kept for reference only):
`designs/osifog_level3/octaweb_experiment/podset_best_candidate.ork`.
Search code: `scripts/podset_full_search.py` (wide genome + analytic
pre-filter before spending an OpenRocket run), `scripts/podset_brute_search.py`
(first quick pass). Deadline: 2026-07-26 23:59 BRT.

## 2026-07-20 — Phase 5A: booster hardening + sustainer search start (fourth pass, same day)

A fourth same-day session corrected two errors in the Phase 4C record (the
"29.862-29.868s legal window" claim below was false -- only 29.862s/29.864s
are actually confirmed legal; the stage/event ignition map had s0/s1
backwards, now verified directly against the saved .ork's XML), classified
the booster as RULE-LEGAL NOMINAL / ENGINEERING-FRAGILE, characterized the
delay basin at 0.5ms resolution (12/25 legal, non-monotonic, repeatability
confirmed), built and validated a coupled two-branch candidate evaluator, and
ran a first 6-candidate slice of the sustainer search (Family S0-A only, out
of a 96-candidate Stage 1 budget) -- no legal sustainer branch found yet, no
full-vehicle authority run attempted. Full detail:
`artifacts/autoevo/phase5a/phase5a-summary.json` (read first), plus
`corrected-phase4c-record.json`, `corrected-stage-event-map.json`,
`booster-delay-basin.json`, `booster-relative-timing-model.json`,
`booster-regression-fixture.json`, `sustainer-family-results.json`.
Continuation state: `.planning/.continue-here.md`.

**Current classification: NO LEGAL TWO-STAGE RECOVERY.** The booster branch
remains the only legal branch; the sustainer branch is still unsolved. The
839,696.05-point artifact below remains quarantined and out of scope (see
`.planning/current-authority.md`) -- this track is separate from that one.

## 2026-07-20 — historical branch recovery: the 3.5135 m/s claim vs Phase 4B (third pass, same day)

A third same-day session resolved the contradiction between this file's own
"H180W at 33.104s -> 3.5135 m/s, verified opposing thrust" claim below and
the corrective-loop sessions' "no legal booster branch, ~58 m/s" conclusion.
Full detail: `artifacts/autoevo/phase4c-summary.json` (read first),
`phase4c-stage-branch-map.json`, `historical-3p5135-candidate.json`,
`phase4c-current-rerun.json`, `phase4c-regression-bisect.json`,
`phase4c-k550-diameter-control.json`.

**The candidate was recovered** from `sustainer-q-probe.json[0]` (proven by
its unique s1_grid_fin_count=8/H180W-retro geometry, its exact
q=0.9006139860749303 sort key matching every citation below, and its
56.14 m/s unpowered booster speed matching Phase 4B's ~58 m/s finding).

**Rerun through today's authority: the literal 33.104s delay does NOT
reproduce** — the retro ignites at an absolute mission time, and this
candidate's natural (unpowered) booster ground-hit is t=30.342s, 2.76s
*before* 33.104s, so the motor never fires (touchdown identical to the
unpowered 56.14 m/s case). Confirmed across the full ±0.02s robustness
window. **A corrected delay (29.864s) DOES reproduce it**: 3.589 m/s
touchdown (2.2% off the historical 3.5135 m/s), 100% opposing-thrust
fraction, motor still burning at contact — reproducible after save/reopen.
The legal window is only ~2ms wide, explaining why Phase 4B's much coarser
9.3-10.3s delay search never found it (that search was also in a completely
different, wrong region of the delay axis).

Regression classification: `INSUFFICIENT ARTIFACTS TO RECONSTRUCT` (a
delay/geometry precision gap, not an engine bug or data regression — every
other layer checked out with zero discrepancy). The K550W-vs-H73J
motor-casing-diameter hypothesis (flagged as a next step in the corrective
loop below) was tested on this candidate with byte-identical external
geometry and found **no aerodynamic mechanism** — small residual deltas are
attributable to imperfect mass-matching, not aerodynamics. This does not
settle the separate, still-open E8_8-topology version of that same question.

**Current classification: LEGAL BOOSTER BRANCH RECOVERED** (single-branch —
the sustainer branch of this same run is still illegal, 19.6-21.2 m/s). Per
the recovery directive, the general booster morphology search stops here;
next work is recovering a legal sustainer branch for this same vehicle.
Saved proof: `artifacts/autoevo/best-legal-booster-branch.ork`
(sha256 `923076029d29ac1d5fecd01e1d909c6b60e38448c2c9c6369b4487bfd8cdc086`).

## 2026-07-20 — corrective loop on the Phase 4B/autoevo track (second pass, same day)

A same-day corrective audit of the Phase 4B session directly below found and
fixed three bugs in the DIAGNOSTIC SCRIPTS (`scripts/descent_gates.py`,
`scripts/strake_batch.py`, `scripts/flip_diagnosis.py`) — not in
`osifog_sweep.py` or OpenRocket itself. Most importantly: **Finding 4 below
("narrow ignition-arming-window gap") is refuted** — it was an artifact of
an apex-detection bug, not a real engine issue; 11/11 delays 8.50-9.50s
ignite exactly on schedule once fixed. The prior session's strake-batch
report also undercounted its own powered runs (claimed 4, actual 16, now
correctly 6 after the fix) and mislabeled 4 ascent-illegal candidates as
"not tested" when they had real (if corrupted-telemetry) powered probes.
New controlled experiments this pass found the q sign change is velocity-
vector-overshoot-dominant (not body-rotation-dominant) for the two cases
tested, and that K550W's passive stabilization is NOT reproduced by
matching its mass/CG/pitch-inertia via ballast on a lighter motor —
classified unresolved, likely driven by motor-casing diameter instead.
Still no legal branch; still diagnostic-only; no 850k or legal-vehicle claim
changes. Full detail: `artifacts/autoevo/diagnostic-integrity-audit.json`,
`artifacts/autoevo/phase4b-summary.json`, `.planning/.continue-here.md`.

## 2026-07-20 — separate track: organic-evolution flip diagnosis (Phase 4B/autoevo, first pass)

This section is about a **different pipeline** than the rest of this file:
the organic AST/OpenRocket-authority track (`osifog_sweep.py` Falcon
topology driven by `scripts/phase4a_direct_search.py` and this session's
`scripts/flip_diagnosis.py` / `scripts/strake_batch.py`), not the hand-tuned
`osifog_precision.py` physical-Falcon submission track the rest of this file
covers. It does not change the 839k-artifact quarantine status below.

Full findings: `artifacts/autoevo/flip-diagnosis-report.md`. Continuation
state: `.planning/.continue-here.md`. Summary: the Phase 4A "motor flip"
causal story (thrust-line moment) was disproven (moment is zero by
construction for this axisymmetric topology); the real mechanism is that
firing the retro motor — any motor, any delay tested — triggers an
irreversible aerodynamic reorientation to nose-first within ~0.1-0.3s of
ignition, with opposing-impulse fraction never exceeding 0.25. A new
descent-only ranking + admission/early-stop gate system
(`scripts/descent_gates.py`) and a first Family C strake/keel generator
(`osifog_sweep.py::_strake_xml`) were added and tested (33 new tests, 159
total passing); a first gated strake batch did not clear the bar either
(ascent-illegal at the tested span, or still flips under power). Classified
as **physics-limited within the tested space** per mission section 18, with
two concretely un-exercised legal dimensions (dense near-apex ignition
sweep; wider strake span/placement sweep) identified as the next session's
starting point. No legal branch found. No claims of 850k or a legal branch
apply to this track.

## Pause update - 2026-07-20 autonomous engine session

The user explicitly stopped the autonomous search because interactive
investigation was consuming too many tokens. Do not resume automatically.
The verified 839,696.05-point artifact below remains the authority.

The separate engine experiment is checkpointed under
`designs/osifog_autonomous_hour/`. Seven full Rust -> medium proxy ->
OpenRocket cycles completed, but none produced a new legal full vehicle.
Cycle 7's best authority result was 2954.94 m, Mach 0.9186 and 1.506 cal;
touchdown speeds were 21.25 and 50.56 m/s.

The descent root cause was isolated. The booster briefly became tail-first
after apogee and then fell nose-first, so the previous delay selector ignited
in an unusable early window. A passive eight-forward-fin booster topology plus
an H180W at 33.104 s subsequently landed at 3.5135 m/s in OpenRocket with
verified opposing thrust. This is only a branch result, not a complete rocket.
The sustainer remains unresolved: stable candidates reached alignment
`q = 0.9006`, but reached impact broadside and did not achieve a legal landing.

Latest probe files:

- `designs/osifog_autonomous_hour/sustainer-structure-probe.json`
- `designs/osifog_autonomous_hour/sustainer-q-probe.json`

Latest code adds compact authority alignment samples, near-impact sample
retention, and conditional 10 ms / 1 ms delay refinement. The final
near-impact-retention edit has not yet been retested. Start the next session
with:

```powershell
python -m pytest tests/test_osifog_engine_search.py tests/test_osifog_precision.py tests/test_osifog_falcon_contract.py -q
```

No autonomous optimization process was left running. Do not reset the dirty
working tree. Read `.planning/.continue-here.md` for the concise decision point.

## Authority artifact

Resume from `designs/osifog_level3/osifog_physical_839k_falcon.ork`.

- SHA-256: `7118214A6DFF2B06C164B02D0574786E133601B1502CED9F24532F20FB86EB38`
- saved OpenRocket simulation: 1
- saved branches: 2
- anti-tumbling extension: present
- passive recovery: none
- legal violations: none
- saved score: **839,696.05**

The older `osifog_850k_falcon.ork` remains quarantined. Its aft ballast disk
intersects the booster motor mounts and must never be submitted or used as the
physical baseline.

## Verified mission result

| Metric | Saved value |
|---|---:|
| Apogee | 3000.031 m |
| Apogee East / North | -2.483 / +2.154 m |
| Maximum Mach | 0.943 |
| Minimum initial-ascent stability | 1.502 cal |
| Sustainer touchdown | 2.648 m/s at E +58.164 / N +109.440 m |
| Booster touchdown | 2.459 m/s at E +70.282 / N +52.926 m |
| Actual propellant consumed | 4.725 kg |

Score losses from the saved file:

- altitude: 2.883
- apogee horizontal: 172.880
- touchdown position: 21,430.546
- touchdown speed: 3,260.136
- propellant: 35,437.500

## Physical architecture now enforced

- Two stages; 148 mm maximum diameter; 2.190 m total length.
- Each stage has three J510W ascent motors around one central K550W retro motor.
- Motor tubes have 1.0 mm walls and 0.25 mm insertion clearance.
- Main tubes are tangent to the central tube, producing a bonded 3+1 cage.
- Native fiberglass centering rings support the cage without blocking motors.
- A native 50 mm internal TubeCoupler spans the stage joint by 25 mm per side.
- The 2.725 kg booster ballast is three explicit solid steel InnerTube rods,
  radius 14 mm, bonded tangent to the central mount; no invisible mass disk.
- Nose ballast is a real 1.260 kg steel Bulkhead at 0.450 m inside the Haack nose.
- All solids are finite, mass/volume checked, contained, and collision checked.

## Engine changes completed

Python/OpenRocket:

- fail-closed internal cylinder collision and containment gate;
- real steel ballast serialization and deterministic component IDs;
- native centering-ring and interstage-coupler generation;
- manifest-driven official score table and physical search spaces;
- delay search rejects motors scheduled after touchdown;
- separation candidates are ranked only after both stages are calibrated;
- stability is measured only during initial ascent up to first apogee;
- saved-file inspector uses the same corrected stability interval;
- exact OpenWind CSV, AGL reference, 30.1 C, 1000 hPa, launch coordinates and
  fixed seed remain embedded in every authority simulation.

Rust:

- dynamic horizontal trajectory with wind;
- per-stage touchdown time, East/North, distance and total speed;
- exact ground interpolation and consumed-propellant accounting;
- official data-driven 900k formula with fail-closed incomplete landings;
- 152 Rust tests passed in the implementation session.

Known Rust limitation: the AST bridge still selects one motor curve per stage.
Independent main and retro motors with delayed post-separation ignition remain
OpenRocket-authority-only until the mission adapter models motor roles.

## Bugs found and fixed

1. Impossible ballast disks could intersect sibling motor mounts.
2. Motor mounts and rods could be visually present but structurally floating.
3. The optimizer ranked separation using only stage 0.
4. A no-op retro delay after touchdown could win delay search.
5. Post-apogee retro climb contaminated the initial-ascent stability gate.
6. Coarse delay searches miss millisecond-scale legal islands; final candidates
   require a local fine sweep after adaptive calibration.

## Next experiment - start here

The interrupted experiment was a transported-lead local delay sweep across
other exact-apogee nose bulkheads. It was stopped at the user's request before
producing results. Do not assume it completed.

Candidates already proven ascent-legal and control-branch-compatible:

- mass 1.260 kg, position 0.450 m: apogee 3000.031 m (current authority)
- mass 1.270 kg, position 0.470 m: apogee 3000.134 m
- mass 1.280 kg, position 0.500 m: apogee 2999.861 m
- mass 1.280 kg, position 0.450 m: apogee 2999.827 m
- mass 1.280 kg, position 0.470 m: apogee 2999.820 m
- mass 1.280 kg, position 0.460 m: apogee 2999.797 m

For each candidate:

1. simulate with both retro delays at 200 s to obtain its own free-impact times;
2. transport ignition leads from the current authority candidate;
3. sweep each delay locally at 1-2 ms resolution;
4. reopen the saved ORK and rank the complete score, not live telemetry;
5. keep only collision-free, initial-ascent-stable, subsonic and <5 m/s results.

The current theoretical score with zero altitude error but unchanged other
terms is about 839.7k, not 851k, because touchdown-position loss is 21.4k.
Crossing 850k requires reducing the signed mean touchdown displacement while
retaining the exact apogee and both legal landings. Do not claim 850k until a
saved file is reopened and independently scored.

## Verification commands

```powershell
python -m pytest tests/test_physical_geometry.py tests/test_osifog_falcon_contract.py tests/test_osifog_precision.py -q
cd l2_engine
cargo test
```

To regenerate the authority artifact:

```powershell
python osifog_precision.py --output designs/osifog_level3/osifog_physical_839k_falcon.ork --min-score 839000 --report designs/osifog_level3/osifog_physical_839k_falcon.json --openearth-dir designs/osifog_level3/openearth/physical_839k
```

## Rust Engine Upgrade (Blocker Resolution)

The Python-based Genetic Algorithm (using OpenRocket JVM) is currently hitting a structural blocker due to the extreme precision required for a parachute-less propulsive landing ("hoverslam"). Finding the exact millisecond ignition delay combined with the precise terminal velocity (mass + passive aero) is mathematically improbable at JVM speeds (1 gen / 20s). We must leverage the Rust engine (`l2_engine`) which can run 100+ sims/sec, allowing us to brute-force the ignition window instantly for every evaluated geometry.

However, `l2_engine` currently only supports inline staging. The following upgrades are required:
1. **AST & Geometry**: Add support for `POD` / `STRAP_ON` parallel staging nodes in `rocket_ast.py` and `l2_engine/src/ast.rs`.
2. **Physics (Mass & Inertia)**: Implement 3D CG tracking (`cg_y`, `cg_z`) and apply the parallel axis theorem for radial pods.
3. **Dynamics (Thrust & Torque)**: Update 6-DOF dynamics to handle off-axis thrust, summing torque from multiple simultaneously firing lateral motors.
4. **Aerodynamics**: Add heuristic or OpenRocket-proxy hooks to account for PodSet parasitic drag and CP shifts.

For full technical details, see: `docs/engine/podset_upgrade_plan.md`
