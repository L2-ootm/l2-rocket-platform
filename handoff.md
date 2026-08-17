# HANDOFF — L2 Rocket Platform

**Updated:** 2026-08-16
**Scope:** whole repository — publication, licensing posture, repository layout,
and the frozen state of engine development
**Status:** active — **published**; engine work frozen; follow-ups in §8

---

## 1. Executive state

The platform is **published and feature-frozen**, not actively developed.

- **Live at `L2-ootm/l2-rocket-platform`, public since 2026-08-16.**
  GPL-3.0-or-later, five commits, nine topics. GitHub's license detector reports
  `gpl-3.0` (verified via `gh repo view --json licenseInfo`). The local directory
  is still named `L2-OSIFOG`; only the remote carries the new name.

Three things finished:

- **The OSIFOG 2026 Level 3 competition entry is done.** It was submitted at the
  2026-07-26 deadline. Nothing in this repository is waiting on it. The full
  engineering narrative is archived at
  `docs/history/session-log-through-2026-07-26.md`.
- **Open-source licensing is resolved.** The 2026-07-26 audit
  (`docs/maintenance/open-source-readiness.md`) returned `BLOCK PUBLICATION`
  over GPL-derived OpenRocket code carrying no license grant. Option A was
  adopted: the whole repository is GPL-3.0-or-later, with OpenRocket and
  rocket-sim provenance recorded. That audit file now carries a resolution
  header; read it before touching licensing.
- **The repository layout was reorganized** on 2026-08-16 and a fresh-clone test
  abort was fixed. Root went from 65 files to 34. See §3.

The reusable product is the engine and its organic topology-evolution workflow.
The competition was the forcing function, not the deliverable.

## 2. Current objective

Land a public repository a technical stranger can read in a few minutes and
verify without contacting the author. This is a **documentation and packaging
task.** It is not an engine task.

## 3. What changed (session of 2026-08-16)

Publication hygiene, all in the working tree:

| Change | Files |
|---|---|
| Stripped absolute local paths (`C:\Users\<user>\...`, ~50 occurrences) | `artifacts/autoevo/replay-corpus.json`, `artifacts/phase1/motor-parity.json`, `designs/osifog_finalization/archive_previous_607k/seed_search_500.json`, `docs/single_stage_missions.md` |
| Untracked an unrelated project's script (a slide-deck generator carrying a third-party contact address; nothing referenced it) | `create_batteryhive_deck.py` — removed from the index, preserved outside the repo |
| Added a resolution header so the audit's `BLOCK PUBLICATION` verdict is not mistaken for current state | `docs/maintenance/open-source-readiness.md` |
| Archived the 997-line historical session log and replaced it with this file | `handoff.md` → `docs/history/session-log-through-2026-07-26.md` |
| Marked superseded operational status as historical | `STATE.md` |
| Deleted 5 spent one-off codemods. They rewrite source in place; `patch_stage.py` would have corrupted `l2_engine/src/sim_core/vehicle/stage.rs` if run | `patch_stage.py`, `patch_tests.py`, `patch_usages.py`, `unbreak.py`, `inspect_phase1.py` |
| Moved 13 zero-importer scripts out of root and fixed their `sys.path` bootstraps so they run from anywhere | → `tools/`, `tools/debug/`, `tools/reports/`, `tools/checks/` |
| Moved 10 loose root docs into `docs/`, and 9 dated analyses from `docs/` into `docs/history/`; updated every live reference | `docs/`, `docs/architecture/`, `docs/history/`, `docs/plans/` |
| Fixed a fresh-clone test abort (see §4) | `tests/conftest.py` (new) |
| Documented the resulting layout | `README.md` |

Root went from 65 files to 34. Every `.py` remaining at root is a real module.

Deliberately untouched: `l2_engine/`, `rocket_ast.py`, `organic_loop.py`,
`organic_campaign.py`, `missions/`, `designs/`, and every test's content. No
engine behavior changed this session.

**Why the importable modules are still at root** — this is the change that would
actually empty it, and it was deferred on evidence, not taste:

- `motor_data.py:24` and `organic_loop.py:245,1349,1426` build
  `Path(__file__).parent / "l2_engine"` — they assume their own directory *is*
  the repository root.
- `osifog_engine_search.py:2677` resolves its worker with
  `Path(__file__).with_name("osifog_authority_worker.py")`, so that worker must
  stay a sibling. `osifog_campaign_watchdog.py` is likewise named by
  `campaign_infra.py`, `organic_campaign.py`, and `osifog_engine_search.py`.
- Several modules spawn subprocesses with `cwd=Path(__file__).resolve().parent`.

Each is a one-line fix, but a wrong `MOTORS_DIR` yields "no motor curves found",
not a crash. See §8.

## 4. Verification evidence

Run on 2026-08-16 against the working tree described above.

```powershell
cargo test --manifest-path l2_engine/Cargo.toml
# 175 passed, 10 suites, 11.87s, exit 0

python -m pytest tests/test_organic_evolution.py tests/test_or_mode_ast_sweep.py tests/test_or_mode_calibrate.py -q
# 110 passed
```

Publication audit, re-verified this session by direct inspection:

- Largest reachable Git blob: **10,762,910 bytes**
  (`l2_engine/tests/fixtures/ork_extracted/rocket.ork`). The 1,078,954,394-byte
  `.planning/` blob that blocked any GitHub push is absent from every ref.
- `.git` is 44 MB; tracked worktree is 32 MB; 867 tracked files.
- Zero tracked files match `.env`, `secret`, `credential`, `.key`, or `token`.
- No regulation PDFs, no third-party Wildman Rocketry design, no OpenRocket
  gitlinks, no `.gitmodules` — all confirmed removed by the squash.
- Root `LICENSE` (GPL-3.0-or-later), `NOTICE`, `DATA_LICENSES.md`,
  `licenses/OpenRocket-LICENSE.txt`, and
  `l2_engine/Cargo.toml`'s `license = "GPL-3.0-or-later"` are all present and
  mutually consistent.

### The fresh-clone abort, and how it was measured

`OSIFOG/` is gitignored competition material, so it does not exist in a clone.
Much of the suite reaches `OSIFOG/OpenWind_File.csv`, sometimes several calls
deep through a helper such as `osifog_precision.falcon_submission_candidate()`.
Thirteen modules read it at *import* time, and a collection error aborts the
whole run — so **a stranger cloning this repository ran zero tests.**

Measured with `git worktree add` at `HEAD`, which genuinely lacks `OSIFOG/` and
the OpenRocket JAR:

| | Fresh clone | Dev machine |
|---|---|---|
| Before `tests/conftest.py` | `Interrupted: 13 errors during collection`, 0 tests run | 349 passed, 2 failed, 1 skipped |
| After | **308 passed, 31 skipped, 3 failed** | 349 passed, 2 failed, 1 skipped — *unchanged* |

`tests/conftest.py` is inert when the CSV is present: it ignores the modules
that read it at import time, and converts the runtime `FileNotFoundError` into a
skip with a reason. Detection is done at runtime deliberately — static analysis
was tried and over-skipped 154 tests, because `osifog_sweep` both defines
`parse_wind_csv` and calls it inside its own helpers.

Full suite re-run after the reorganization: `cargo test` 175 passed;
`pytest -m "not slow"` 349 passed, 2 failed, 1 skipped — identical to before.

**Not run:** OpenRocket authority validation end-to-end; the `slow`-marked JVM
tests; `cargo test --features viz`; any professional secret scanner.

## 5. Decisions made

- **License: GPL-3.0-or-later for the whole repository** (audit Option A). The
  engine contains acknowledged verbatim ports of OpenRocket tables and formulas
  (`l2_engine/src/barrowman.rs`, `l2_engine/src/sim_core/sim/adaptive.rs`).
  Permissive relicensing is not a unilateral option without a clean-room
  rewrite. Do not reopen this without reading the audit.
- **Motor curves ship with the repo** under OpenRocket's GPL §7 data-packaging
  permission, mapped in `DATA_LICENSES.md`.
- **Repository name is `l2-rocket-platform`**, not `L2-OSIFOG`. The competition
  acronym means nothing outside the competition. The local directory keeps its
  old name; only the GitHub remote uses the new one.
- **Publish private first, flip public after review.** Publication is not
  reversible — forks and caches outlive deletion.
- **The `.ork` design artifacts under `designs/` are published** as
  project-authored validation evidence, per `DATA_LICENSES.md`.

## 6. Known risks / open issues

1. **No professional secret scan has been run.** The audit's scan was regex-only
   and did not cover entropy, encoded secrets, or binary/archive contents. The
   `.ork` files are ZIP archives; their contents were never scanned. Run
   gitleaks or trufflehog across all refs — see §8 R1. The repo is now public, so
   anything found is a rotate-and-disclose problem, not a delete problem.
2. **Three tests still fail, two of them pre-existing.** In a fresh clone:
   `test_orhelper.py::test_simulation` needs the OpenRocket JAR, which is not
   distributed (README says to download it);
   `test_organic_evolution.py::test_run_rust_evaluator_batch_defaults_to_openrocket`
   passes in isolation but fails in a full-suite run — test-order pollution,
   reproduced on the pre-reorg baseline;
   `test_osifog_session_check.py::test_immutable_submission_manifest_...` fails
   on the dev machine too, also reproduced at baseline. None were introduced by
   the 2026-08-16 reorganization; all three predate it.
3. **The Rust proxy overestimates apogee by roughly 13.9%** relative to
   OpenRocket, measured 2026-07-04 against `karman_m6` elites. This is why
   OpenRocket is the authority and the Rust core is only a proxy — the design is
   sound, but the gap is unclosed and the calibration is stale.
4. **Java version is an undocumented trap.** `java` on the development machine's
   PATH is JRE 1.8, which cannot run OpenRocket 24.12. It works only because
   `JAVA_HOME` points at JDK 21 and JPype's `getDefaultJVMPath()` reads
   `JAVA_HOME` first. A cloner following the README will hit this.
5. **`l2_hyper/` has no unit tests** and the Rust↔Python handoff has no
   integration test. Long-standing, from `STATE.md`.

## 7. Do not repeat / do not do

- **Do not restart engine work to make the repository look better.** The
  publication task is documentation. The podset/parallel-staging upgrade in
  §8 is the next *product* increment, not a publication prerequisite.
- **Do not treat `docs/history/session-log-through-2026-07-26.md` as current.**
  It is an archive. Its "next steps" sections describe a competition that has
  already closed.
- **Do not look for `.planning/HANDOFF.json`.** It was the live source of truth
  until the squash removed `.planning/` from version control. It no longer
  exists; `.planning/` is gitignored local state. This file replaced it.
- **Do not assume a minimum static margin rule exists.** It does not — the
  organizers confirmed this on 2026-07-24. Any 1.5-cal threshold language in the
  archive describes a fixed internal bug.
- **Do not relicense to MIT/Apache.** See §5.
- **Do not push the pre-public bundles** (`L2-OSIFOG-pre-public-*.bundle`).
  They hold the pre-squash history and are the only copy of it. Do not delete
  them either.

## 8. Next actions

### Publication — DONE 2026-08-16

- [x] Publication hygiene committed — `d524060`.
- [x] `L2-ootm/l2-rocket-platform` created private, `origin` added, `main` pushed.
- [x] Root `LICENSE` replaced with verbatim GPL-3 text — `31f0cda`. GitHub had
      classified the repository as `Other`; it now reports `gpl-3.0`.
### Completed Publication Follow-ups (2026-08-16)

- [x] **R1 (Secret Scanning): COMPLETED.**
  - **Tool:** Gitleaks v8.30.1.
  - **Command:** `gitleaks detect --source . --log-opts "--all" --report-path gitleaks.json`
  - **ORK Scan:** All 65 `.ork` zip archives extracted and audited for secrets/keys (0 leaks found).
  - **Allowlist:** `.gitleaks.toml` created to allowlist internal concurrency process leases (`campaign.lease*.json`).
  - **Final Output:** `gitleaks: no leaks found`.

- [x] **R2 (Fresh Clone Clean Test Suite): COMPLETED.**
  - **Dev Machine:** `352 passed, 1 skipped, 0 failed in 58.52s`.
  - **Clean Fresh Worktree (no `OSIFOG/`, no JAR):** `310 passed, 33 skipped, 0 failed in 45.44s`.
  - **Fixes Applied:**
    - `test_orhelper.py`: skips cleanly with reason if `lib/OpenRocket-24.12.jar` is missing.
    - `test_organic_evolution.py:test_run_rust_evaluator_batch_defaults_to_openrocket`: isolated batch test from `_AST_EVAL_STREAMS` process caching.
    - `scripts/osifog_session_check.py`: handled candidates without optional `"robustness"` companion and added float rounding tolerance.
    - `tests/test_phase2f_gates.py`: guarded `_json_artifact` with `WRITE_PHASE2F_ARTIFACTS == "1"` preventing test-driven mutation of tracked `artifacts/phase2f/scenario-semantic-proof.json`.
    - `tools/checks/`: renamed manual check scripts away from `test_*` prefix to prevent unintended pytest collection.

- [x] **R3 (Module Migration into `src/`): COMPLETED.**
  - **Moved:** All 18 root Python modules migrated to `src/` (`campaign_infra.py`, `ckg_memory.py`, `mission_evolution.py`, `motor_data.py`, `organic_campaign.py`, `organic_loop.py`, `osifog_attitude_campaign.py`, `osifog_authority_worker.py`, `osifog_campaign_watchdog.py`, `osifog_engine_search.py`, `osifog_legal_stage_campaign.py`, `osifog_podset.py`, `osifog_precision.py`, `osifog_reversal_gate.py`, `osifog_sweep.py`, `physical_geometry.py`, `rocket_ast.py`, `rocket_forge.py`).
  - **Path Resolution:** Fixed `REPO_ROOT` vs `SRC_DIR` in `motor_data.py`, `organic_loop.py`, `osifog_*.py`, `conftest.py`, and `pytest.ini`.
  - **Scripts & Tools:** 57 standalone scripts updated to seamlessly resolve `src/` in `sys.path`.
  - **Authority Suite Verification:**
    - Before move: 352 passed, 1 skipped, 0 failed.
    - After move (Dev): 352 passed, 1 skipped, 0 failed; `cargo test`: 175 passed, 0 failed.
    - After move (Fresh Worktree): 310 passed, 33 skipped, 0 failed.

- [x] **Continuous Integration (CI): ACTIVE.**
  - Created `.github/workflows/ci.yml` running multi-platform CI on GitHub Actions:
    - **Security & Secret Scan:** Gitleaks action with `.gitleaks.toml`.
    - **Rust Engine Suite:** `cargo check` and `cargo test` on Ubuntu and Windows.
    - **Python Test Suite:** Python 3.11 with cached dependencies, release binary compilation (`ast_eval`, `divergence_fit`), and full `pytest` execution.

### Product work — only if the engine freeze lifts, in dependency order

**P1.** Close the OpenRocket proxy parity gap. Compare Rust trajectory curves
against OpenRocket `FlightData` for the worst residual case (seed `2026070408`)
using `or_curve_compare.py` and the Rust `ast_trace` binary **before** changing
any drag coefficient. Do not guess-tune from a single point.

**P2.** Podset / parallel-staging support: AST and geometry nodes, 3D CG
tracking with the parallel axis theorem, off-axis thrust and torque in the 6-DOF
dynamics, and parasitic-drag hooks. Full plan in
`docs/engine/podset_upgrade_plan.md`.

**P3.** Add `l2_hyper` unit tests and a Rust↔Python integration test.

**P4.** Defer GPU/WGSL work (`docs/l2_gpu_engine.md`) until CPU proxy parity is
stable across the five-seed suite.

## 9. Inspect first

Read in this order before doing anything:

| Path | Why |
|---|---|
| `README.md` | What the platform is and how to run it |
| `docs/maintenance/open-source-readiness.md` | Licensing constraints; read the resolution header first |
| `NOTICE`, `DATA_LICENSES.md` | What is derived from whom, and under what permission |
| `docs/architecture/platform.md` | Current engine/adapter boundaries |
| `docs/roadmap/platform.md` | Ordered future work |
| `STATE.md` | Operational status and the proxy-parity blocker |
| `docs/history/session-log-through-2026-07-26.md` | Only when you need the *why* behind a competition-era decision |

## 10. Acceptance criteria status

All publication acceptance criteria are fully met and verified with reproducible evidence:

- [x] **R1:** Gitleaks v8.30.1 executed across all git refs and 65 unzipped `.ork` archives; 0 secrets found; `.gitleaks.toml` configured.
- [x] **R2:** Fresh-clone `pytest` measured in a real `git worktree` without `OSIFOG/` and without JAR: **310 passed, 33 skipped, 0 failed**.
- [x] **R3:** All 18 modules moved into `src/`; OpenRocket authority suite verified: **352 passed, 1 skipped, 0 failed**; `cargo test`: **175 passed, 0 failed**.
- [x] **Artifact Churn:** `artifacts/phase2f/scenario-semantic-proof.json` isolated behind `WRITE_PHASE2F_ARTIFACTS` flag, eliminating test suite git mutations.
- [x] **CI Automation:** Complete GitHub Actions workflow at `.github/workflows/ci.yml`.
