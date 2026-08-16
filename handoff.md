# HANDOFF — L2 Rocket Platform

**Updated:** 2026-08-16
**Scope:** whole repository — publication readiness, licensing posture, and the
frozen state of engine development
**Status:** ready-for-review (publication in progress; engine work frozen)

---

## 1. Executive state

The platform is **feature-frozen and being published**, not actively developed.

Two things finished and one is in flight:

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
- **Publication is in flight.** History was squashed to a single audited commit
  and the repository is being pushed to GitHub as `L2-ootm/l2-rocket-platform`,
  private first, then flipped public after a rendered review.

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

Deliberately untouched: `l2_engine/`, `rocket_ast.py`, `organic_loop.py`,
`organic_campaign.py`, `missions/`, `designs/`, and every test. No engine
behavior changed this session.

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

**Not run:** OpenRocket authority validation end-to-end; the full
`tests/` suite beyond the three README-documented files;
`cargo test --features viz`; any professional secret scanner.

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
   gitleaks or trufflehog across all refs before the public flip if you want
   this closed properly.
2. **`docs/` contains substantial stale internal analysis** — `PROJECT_STATUS.md`,
   `CODEBASE_ANALYSIS_2026-07-04.md`, `analise_arquitetura_rust.md` (45 KB,
   Portuguese), `session_report_july_06.md`, and others. None of it is wrong as
   history; all of it is confusing as documentation. Same for the root-level
   Portuguese docs (`protocolo_julho_19.md`, `veto_protocol.md`) and ~45 loose
   Python scripts at the repository root.
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

Publication, in order:

1. Commit the working tree described in §3.
2. Create `L2-ootm/l2-rocket-platform` **private**, add the remote, push `main`.
3. Review the rendered README on github.com — not locally — and confirm the
   `designs/` artifacts and license files display correctly.
4. Set the repository description and topics.
5. Flip to public.

Product work, once publication is done and if the freeze lifts, in dependency
order:

6. Close the OpenRocket proxy parity gap. Compare Rust trajectory curves against
   OpenRocket `FlightData` for the worst residual case (seed `2026070408`) using
   `or_curve_compare.py` and the Rust `ast_trace` binary **before** changing any
   drag coefficient. Do not guess-tune from a single point.
7. Podset / parallel-staging support: AST and geometry nodes, 3D CG tracking
   with the parallel axis theorem, off-axis thrust and torque in the 6-DOF
   dynamics, and parasitic-drag hooks. Full plan in
   `docs/engine/podset_upgrade_plan.md`.
8. Add `l2_hyper` unit tests and a Rust↔Python integration test.
9. Defer GPU/WGSL work (`docs/l2_gpu_engine.md`) until CPU proxy parity is
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

## 10. Acceptance criteria for next handoff

The next handoff supersedes this one when it can state, with evidence:

- [ ] The GitHub remote exists, `main` is pushed, and the commit SHA is recorded.
- [ ] The rendered README was reviewed on github.com and the repository
      description and topics are set.
- [ ] The public/private decision is recorded with its date.
- [ ] Any secret scanner that was run is named, with its result.
- [ ] If engine work resumed: which of §8's items was started, and the test
      counts from `cargo test` and `pytest` after the change.
