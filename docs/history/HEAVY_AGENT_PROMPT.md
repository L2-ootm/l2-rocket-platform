# Prompt for the heavy-load agent

*(Prepared 2026-07-24. Copy the section below the line into the agent's initial prompt. This file's job is to make sure that agent spends its budget on the actual problem, not on rediscovering context that already exists.)*

---

## Goal

Your job is to increase the real OSIFOG competition score this codebase can produce, by making its genetic self-evolution algorithm actually good — not by hand-tuning one rocket design. The scoring formula and the legality rules it must respect are both already correctly implemented and verified; what's suspected to be weak is the evolutionary search itself. The person running you believes the genetic algorithm is not being used to anything like its full potential, and wants that specifically investigated and improved. Do not treat this as a rocket-engineering problem to solve by hand — treat it as a search/optimization problem: is this GA actually searching well?

**Do not start by exploring the repository.** Read the three documents listed immediately below, in order, before touching anything else. They already contain the current state, the current architecture, the current blocking issue, and an explicit list of what is confirmed vs. genuinely unknown. Re-deriving any of that from scratch is wasted budget.

1. `.planning/HANDOFF.json` — the single actively-maintained source of truth. Read the whole thing; the most recent `fixes_round_N` entries are the most relevant.
2. `docs/PROJECT_STATUS.md` — current pipeline architecture, the real scoring formula, the real legality rules (verified against the organizer's own PDFs — do not re-derive these from guesswork or from older docs, they were wrong before and got corrected multiple times), the current live campaign's status, and — critically — §6 "Open, NOT root-caused" and §5 "What has been fixed" are separated for a reason. §5 items are confirmed with evidence; treat them as fact. §6 items are genuinely unknown; if you investigate one, report what you actually found, not a plausible-sounding guess. The person running you has explicitly said: do not force an explanation for why something is wrong if you don't actually know — an invented root cause is worse than admitting it's unknown, because someone will act on it as if it were true.
3. `docs/architecture/source-map.md` — which files are the active pipeline, which are retired/legacy, and known repo-hygiene debt (large committed files, untracked entry-point scripts, stale duplicate docs). Do not trust `OSIFOG/SOURCE_MAP.md` or treat `osifog_engine_search.py`/`osifog_podset.py`/`osifog_sweep.py`/`osifog_precision.py` as current — they are retired predecessor pipelines. The active pipeline is `rocket_ast.py` → `l2_engine` (Rust) → `organic_loop.py` → `organic_campaign.py` → `osifog_campaign_watchdog.py`.
4. `docs/DESIGN_REFERENCE_AND_PROPOSED_ARCHITECTURE.md` — a real-simulation-verified diagnosis of a reference design the person running you was considering as a seed, plus a new rocket topology (4 side pods strap-on to the sustainer stage) they proposed afterward. §3 of that document lists genuinely open engineering questions about the new proposal — evaluate them with real aerospace-engineering judgment if you have budget for it; do not treat the proposal as something you must implement, and do not invent answers to §3's open questions without real analysis.

## Where the actual GA logic lives

Once you've read the three documents above, the genetic algorithm itself is implemented across:
- `organic_loop.py::run_generation` — the reproduction loop: elitism, selection, mutation, crossover.
- `organic_loop.py::crossover_ast`, `_blend_node_params`, `_select_complementary_pair` — the crossover implementation, added this session (previously there was none — pure single-parent mutation only, for the entire prior history of this project). This is new and has not been rigorously evaluated for whether it actually improves search quality, only observed to run without crashing.
- `organic_loop.py::mutate_ast` and `rocket_ast.py::ASTNode.mutate` — the mutation operators.
- `rocket_ast.py::sanitize_ast_for_openrocket` and its sibling repair functions — the self-healing repair passes that run on every candidate every generation (motor/body consistency, fin de-duplication, octaweb cage tightening). These exist because past sessions found that mutation/crossover can produce physically-inconsistent candidates, and rather than reject them, the pipeline repairs them in place. Whether this repair-vs-reject design choice is actually good for search quality (does it waste evaluation budget on repaired-but-still-bad candidates? does it bias the population toward a narrow repaired shape?) has not been evaluated.
- `l2_engine/src/ast.rs::enforce_hard_constraints`, `evaluate_scoring_table`, `score_summary` — the fitness function and hard-legality gates the population is actually selected against.

## Specific things worth investigating (not a complete list — use judgment)

- Population diversity over generations: is the population actually exploring different topologies/materials/motors, or converging prematurely onto one structural pattern? No diversity metric currently exists in this codebase — you may need to add instrumentation to answer this.
- Whether `_select_complementary_pair()`'s "cross a specialist blocked by constraint A with one blocked by constraint B" strategy is actually finding meaningfully different specialists in practice, or degenerating to near-random pairing whenever the population converges on one dominant blocking constraint (which the current live campaign's failure histogram suggests may be happening — see `docs/PROJECT_STATUS.md` §4).
- Whether mutation rates/operators are adaptive to search progress at all, or fixed regardless of how close the population is to a legal/high-scoring region.
- Whether the fitness signal the GA actually selects on (`candidate.score`/`raw_score`, the constraint-closeness-ratio system in `l2_engine/src/ast.rs`'s `violation()` helper) gives a genuinely useful gradient once a candidate clears the currently-dominant blocking constraint, or flattens out.
- Whether elitism (`elite_count`) is sized appropriately relative to population size and mutation/crossover rates — too much elitism can stall exploration, too little can lose good structure.
- Whether the self-healing repair passes described above are net-positive or net-negative for search efficiency.

## Constraints on your work

- Do not lose existing functionality. This pipeline works today (produces real, physically-simulated candidates and correctly enforces real OSIFOG rules) — any change must be verified not to regress that, via the existing test suites (`python -m pytest tests/test_organic_evolution.py tests/test_or_mode_ast_sweep.py tests/test_or_mode_calibrate.py -q` and `cd l2_engine && cargo test --release`), both of which must stay fully green.
- The deadline is 2026-07-26 23:59 BRT. Check current time against that before committing to large architectural changes versus targeted, high-confidence improvements.
- If you find yourself forming a theory about why something is wrong, verify it against real evidence (reproduce it, trace the code, check real data) before reporting it as a finding — the same standard `docs/PROJECT_STATUS.md` was written to.
- Secondary, lower priority: this repository also has known cleanup/repo-hygiene debt (see `docs/architecture/source-map.md`) — a 1.1GB file committed to git history, untracked entry-point scripts, ~7.3GB of retired campaign output, stale duplicate documentation. Only spend budget on this if the primary goal (search quality) is in good shape and time remains — do not let repo cleanup crowd out the actual optimization work.
