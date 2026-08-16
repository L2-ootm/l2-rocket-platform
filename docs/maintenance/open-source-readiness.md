# F2 — Open-source readiness: licensing, provenance, and publication blockers

> **Status: RESOLVED 2026-08-16. This is a historical audit, retained as the
> provenance record for the licensing decisions the repository now ships.**
> The `BLOCK PUBLICATION` verdict below was the state on 2026-07-26 and is no
> longer current. Remediation taken, verifiable in this tree:
>
> - **Option A adopted.** Root `LICENSE` is GPL-3.0-or-later; `l2_engine/Cargo.toml`
>   declares the matching SPDX expression.
> - `NOTICE` records OpenRocket provenance (upstream commit
>   `6e18add7055a66dcae580f9d1e0dc1538c0454c7`, copyright, modification dates)
>   and points to the preserved rocket-sim MIT notice.
> - `licenses/OpenRocket-LICENSE.txt` carries the full GPL-3 text plus
>   OpenRocket's §7 data-packaging permission.
> - `DATA_LICENSES.md` maps the `l2_engine/motors/*.eng` curves to that permission.
> - History was squashed to a single audited commit. The 1,078,954,394-byte
>   `.planning/` blob, all `.planning/` state, the regulation PDFs, the
>   third-party Wildman Rocketry design, and the broken OpenRocket gitlinks are
>   absent from every ref. Largest reachable blob is now 10.7 MB.
> - Absolute local paths were stripped from tracked artifacts on 2026-08-16.
>   The `C:\Users\Davi\...` references remaining *in this file* are quoted
>   audit evidence, not live leaks.
>
> Not closed, and deliberately so: no professional secret scanner
> (gitleaks/trufflehog) has been run, and there is no CI dependency-license
> policy or release SBOM. Both are listed in the checklist at the end.

**Audit date:** 2026-07-26
**Scope:** repository and all reachable Git refs, with emphasis on `l2_engine/`,
OpenRocket/rocket-sim provenance, dependency licenses, secrets/PII, and
competition material.
**Method:** read-only inspection of the working tree and Git objects; `cargo
metadata --locked`; regex-based secret scan of every reachable revision;
upstream licenses checked against primary sources. No legal conclusion below
is a substitute for advice from qualified counsel.

## Verdict

**BLOCK PUBLICATION.** The repository does not yet grant any license to users,
while `l2_engine` contains an acknowledged MIT-derived rocket-sim port and
multiple functions/tables described in its own source and planning history as
verbatim ports from GPL-3.0-or-later OpenRocket. A permissive whole-project
license cannot safely be asserted without first replacing or separately
licensing the OpenRocket-derived expression. The simplest defensible path that
retains the current engine is to license the covered `l2_engine` work under
**GPL-3.0-or-later**, preserve the MIT rocket-sim notice, and add OpenRocket
copyright/modification notices. A permissive license is possible only after a
documented clean-room rewrite and a separate decision on OpenRocket-sourced
motor data.

Publication is independently blocked by a reachable **1,078,954,394-byte**
blob, extensive competition outputs/history, uncertain third-party document
and design provenance, and an incomplete professional secret scan.

## Findings

### P0 — No project license or package license

Evidence:

- The repository root contains no tracked `LICENSE`, `COPYING`, or `NOTICE`.
  `git ls-tree HEAD` finds none.
- `l2_engine/` contains only
  `l2_engine/THIRD_PARTY_NOTICES.md`; it has no license for original L2 code.
- `l2_engine/Cargo.toml:1-22` declares the package and dependencies but no
  `license`, `license-file`, `authors`, `description`, `repository`, or
  `readme`.
- None of the 49 tracked Rust source files under `l2_engine/src/` contains an
  SPDX identifier or copyright header.

Impact:

- Public visibility is not open source. Without an explicit grant, default
  copyright applies and recipients lack permission to copy, modify, or
  distribute original L2 code.
- The crate is not publish-ready. Cargo/crates.io uses an SPDX expression in
  the manifest `license` field (or a `license-file`); Cargo's primary
  documentation confirms this requirement:
  <https://doc.rust-lang.org/cargo/reference/manifest.html#the-license-and-license-file-fields>.

Required action:

1. Resolve the OpenRocket-derived-code question below.
2. Add a root license and component-level licensing where scopes differ.
3. Add matching Cargo metadata and SPDX headers (or a repository-wide
   `REUSE.toml`/clear file map).

### P0 — `l2_engine` contains clear rocket-sim-derived code; MIT compliance is mostly preserved

Evidence:

- `l2_engine/THIRD_PARTY_NOTICES.md:3-5` says `sim_core/` “originates from” the
  `ZenAlexa/rocket-sim` project and was physically merged.
- `l2_engine/THIRD_PARTY_NOTICES.md:7-29` reproduces the MIT license and
  copyright `Copyright (c) 2025 ZenAlexa`, satisfying MIT's core notice
  retention condition if this file is shipped.
- Git rename/blame evidence connects current files directly to the vendored
  originals. Examples:
  `l2_engine/src/sim_core/physics/gravity.rs:1-15`,
  `l2_engine/src/sim_core/orbital/elements.rs:1-15`, and
  `l2_engine/src/sim_core/gnc/pid.rs:1-15` blame to
  `l2_engine_base/rocket-sim/...` in commit
  `f1829a6a87bbbf28e85545ada9b8ad6c17cb661a`.
- That historical tree's `l2_engine_base/rocket-sim/LICENSE` is MIT,
  `Copyright (c) 2025 ZenAlexa`; its `Cargo.toml` also declares `license =
  "MIT"` and repository `https://github.com/ZenAlexa/rocket-sim`.

Conclusion:

- rocket-sim does **not** force copyleft. L2 may distribute its derivative
  under MIT, Apache-2.0, GPL-3.0-or-later, or another compatible license, but
  must retain the ZenAlexa copyright and MIT permission/warranty text.
- Keep `THIRD_PARTY_NOTICES.md` in source archives and binary distributions.
  Add exact upstream commit/release provenance; the current notice identifies
  only the repository, not the revision imported.

### P0 — OpenRocket-derived formulas/tables create a GPL constraint unless independently rewritten

Primary upstream license:

- OpenRocket commit `6e18add7055a66dcae580f9d1e0dc1538c0454c7`
  declares **GNU GPL version 3 or, at the recipient's option, any later
  version**:
  <https://raw.githubusercontent.com/openrocket/openrocket/6e18add7055a66dcae580f9d1e0dc1538c0454c7/LICENSE.TXT>.
- The same license defines a modified work as copying or adapting all or part
  of the program in a way requiring copyright permission and requires a
  conveyed modified source work, as a whole, to be licensed under GPL
  (§§0, 4, and 5). Its aggregate exception applies only to genuinely separate
  and independent works.

Repository evidence of copying/adaptation:

- `l2_engine/src/barrowman.rs:8-15` calls its fin CNa formula and interference
  factors “verbatim” and traces them to OpenRocket `FinSetCalc.java`.
- `l2_engine/src/barrowman.rs:231-242` reproduces the fin-count table
  (`0.948`, `0.913`, `0.854`, `0.81`, `0.75`) and labels it verbatim.
- `l2_engine/src/barrowman.rs:257-272` labels the CNa formula verbatim from
  `FinSetCalc.java`.
- `l2_engine/src/barrowman.rs:626-645` reproduces the ten-pair von Kármán drag
  table and labels it verbatim from the OpenRocket class initializer.
- `l2_engine/src/barrowman.rs:647-674` labels stagnation- and base-drag closed
  forms verbatim from OpenRocket bytecode.
- `l2_engine/src/sim_core/sim/adaptive.rs:53-82` implements the eight
  constraints of OpenRocket 24.12 `RK4SimulationStepper.step()`, including its
  constant structure and `1.5 * previous_dt` growth rule.
- `.planning/phases/01.1-barrowman-drag-table-fidelity-port-openrocket-s-real-finenes/01.1-RESEARCH.md:5-19`
  says constants were extracted directly from OpenRocket bytecode and
  recommends porting exact table-selection, blending, and closed-form logic.
- The same research document at lines 128-196 records direct bytecode offsets,
  literal tables, and exact runtime behavior. The Phase 1 research at
  `.planning/phases/01-rust-physics-engine-core/01-RESEARCH.md:293-299`,
  `348-413`, and `490-501` repeatedly directs implementers to port or reproduce
  exact OpenRocket implementation details.
- Commit messages reinforce provenance: `f6b2762` (“port vonKarman
  nose-pressure + stagnation drag functions”), `5f01c78`
  (“bytecode-verified ... formulas”), and `3ad09c4`
  (“complete ... drag-table port”).
- No Rust file contains the OpenRocket copyright, GPL notice, modification
  notice/date, or a link to the GPL text.

Assessment:

- Mathematical facts and scientific formulas themselves may not be
  copyrightable, but this record goes beyond an independent implementation of
  general equations: it expressly documents verbatim tables, selection logic,
  and bytecode-to-Rust ports. That is enough provenance risk that declaring the
  engine MIT/Apache-only would be imprudent without a line-by-line legal and
  technical clean-room review.
- Merely running an unmodified OpenRocket JAR as an external subprocess/JVM
  authority does not automatically impose GPL on independent L2 code. The
  problem is copied/adapted implementation inside `l2_engine`, not the external
  validation boundary described at `README.md:44-50`.

Required choice:

1. **Retain current engine (recommended minimum-change route):** license
   `l2_engine` under `GPL-3.0-or-later`; include the full GPL text; add
   OpenRocket copyright and prominent “modified/ported by L2” notices with
   dates; preserve rocket-sim's MIT notice. The remainder of the repository
   may be GPL too (simplest), or separately permissive if it is genuinely
   independent and the boundaries are documented.
2. **Permissive release:** remove and clean-room reimplement every
   OpenRocket-derived expression/table/algorithm using public standards,
   papers, or independently generated test observations. The clean-room
   implementer must not consult the copied Rust, disassembly notes, or
   OpenRocket source. Keep OpenRocket only as an external oracle. Publish a
   provenance matrix and either squash the public history or retain the old
   GPL-covered history with correct licensing.
3. **Alternative permission:** obtain explicit relicensing permission from
   all relevant OpenRocket copyright holders. A single maintainer's approval
   may not cover all contributed code.

Dual-licensing the current OpenRocket-derived engine as MIT/Apache is not an
available unilateral choice.

### P0 — OpenRocket-sourced motor curves need a data-rights decision

Evidence:

- `README.md` identifies `l2_engine/motors/*.eng` as real motor curves
  extracted from OpenRocket's bundled database.
- All 36 tracked `.eng` files say this on lines 1-2; for example,
  `l2_engine/motors/F50T.eng:1-2`.
- `scripts/extract_motors.py:90-112` reads exact time/force rows and writes
  them into distributable `.eng` files.
- OpenRocket's license adds GPL §7 permission to package OpenRocket or a
  covered work **along with** non-compilable data such as thrust curves and
  component databases. See upstream `LICENSE.TXT:14-18` at the primary link
  above. It does not unambiguously state that extracted subsets of third-party
  manufacturer data may be relicensed as MIT-only standalone data.

Impact and action:

- Do not place `l2_engine/motors/` under a blanket MIT/Apache declaration until
  data provenance and redistribution rights are confirmed.
- Lowest-risk options are: (a) distribute them under the same
  GPL-3.0-or-later covered-work package with source attribution and the
  upstream §7 permission intact; (b) fetch/generate them at install time from
  a user-supplied OpenRocket installation; or (c) replace them with curves
  obtained directly under explicit redistributable terms from the original
  source.
- Add a `DATA_LICENSES.md` mapping every curve to source, revision, original
  author/manufacturer, and terms.

### P1 — Dependency licenses are permissive, but the notice process is incomplete

Evidence:

- `l2_engine/Cargo.lock` is tracked and resolves the dependency graph.
- `cargo metadata --locked` reports direct dependencies as:
  `anyhow`, `rand`, `rand_distr`, `rayon`, `roxmltree`, `serde`,
  `serde_json`, and `thiserror` under `MIT OR Apache-2.0`; `nalgebra` under
  `Apache-2.0`; and `zip` under `MIT`.
- The full feature-expanded graph contains permissive expressions (MIT,
  Apache-2.0, BSD-2-Clause, 0BSD, Zlib, Unlicense, CC0, bzip2-1.0.6, and
  selectable alternatives). No dependency is GPL-only. `r-efi` offers
  `MIT OR Apache-2.0 OR LGPL-2.1-or-later`, so a permissive branch can be
  selected.

Impact and action:

- Rust dependencies do not dictate the project's license.
- Before binary release, generate and review a locked SBOM/license report for
  both default and `viz` features. Bundle applicable MIT/Apache/BSD notices and
  license texts. `THIRD_PARTY_NOTICES.md` currently covers only rocket-sim,
  not registry dependencies or OpenRocket.
- Add an automated policy (`cargo-deny`, `cargo-about`, or equivalent) in CI,
  with allowed SPDX expressions and source/advisory checks.

### P1 — Nested/history licensing is not publication-ready

Evidence:

- `openrocket/` and `openrocket_src/` are tracked gitlinks at the same exact
  OpenRocket commit `6e18add7055a66dcae580f9d1e0dc1538c0454c7`, but the
  repository has no `.gitmodules`. A fresh clone therefore cannot initialize
  them through normal submodule commands.
- The upstream checkout's `LICENSE.TXT` is GPL-3.0-or-later with the data
  packaging permission quoted above.
- Commit `f1829a6` added complete snapshots under
  `l2_engine_base/rocket-sim/` (MIT) and
  `l2_engine_base/OpenTsiolkovsky/` (MIT, copyright 2016 Interstellar
  Technologies Inc.). Both license files existed in that historical commit.
- Current code has strong rename/blame continuity to rocket-sim but no similar
  evidence found that current production code was copied from
  OpenTsiolkovsky. Its historical snapshot nevertheless remains distributed
  when full Git history is published.

Action:

- Remove the broken duplicate OpenRocket gitlinks from the publication tree or
  replace them with one valid pinned submodule plus `.gitmodules`; prefer a
  documented download step for the external runtime.
- Preserve historical MIT license files if retaining vendored history.
- If publishing a rewritten/squashed history, carry forward both rocket-sim
  and any other surviving third-party notices.

### P0 — Git history contains a hard hosting blocker and large generated state

Evidence:

- Reachable blob
  `.planning/.organic_ckg.json.2700.tmp` is **1,078,954,394 bytes**.
- Other large reachable generated files include
  `.planning/ultra/multifidelity_campaign_ckg.json` (17,244,806 bytes),
  `.planning/or_authority_phase_mach_ckg_v2.json` (12,342,751 bytes), and
  multiple 10+ MB extracted `.ork` XML files.
- GitHub's primary documentation states that normal Git pushes are blocked for
  files over 100 MiB:
  <https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github>.

Impact and action:

- A normal GitHub publication of all current refs will fail even if the file
  is deleted in the latest working tree.
- Rewrite all publication refs to remove generated state and large blobs, or
  build a clean public repository from an audited source snapshot. Use Git LFS
  only for intentional release artifacts, not transient optimizer state.
- Re-run `git rev-list --objects --all` plus `git cat-file --batch-check`
  after the rewrite and require every regular Git blob to be below the chosen
  cap.

### P0/P1 — Competition outputs and possible third-party artifacts remain in history

Evidence:

- `HEAD` tracks 3,709 paths under `designs/` and 98 under `outputs/`, mostly
  generated candidates, elites, calibration runs, authority reports, and
  `.ork` designs.
- Particularly publication-sensitive examples include
  `designs/multifidelity_campaign_20260719_final/precision_polished_elite.ork`,
  its 16 `authority_candidate_*.ork` files and report,
  `designs/osifog_level3/falcon_best.ork`,
  `designs/osifog_level3/falcon_best_config.json`, OSIFOG mission definitions,
  and multiple authority-model/CKG files under `.planning/`.
- `docs/history/CODEBASE_ANALYSIS_2026-07-04.md:25-27` explicitly says this is an
  optimizer for OSIFOG/BIRST 2026 and that teams submit `.ork` files scored
  against competition objectives.
- Four competition regulation PDFs are tracked. No redistribution permission
  or document license was found.
- `outputs/competitor_mini.xml` and `designs/seeds/Competitor Mini.ork`
  identify a third-party “Competitor Mini” design and list
  `Wildman Rocketry` as designer. No source URL or permission is recorded.

Assessment:

- L2-generated algorithms and designs are likely L2-owned, but publishing
  final candidates, fitness state, authority reports, scoring assumptions, and
  mission tactics may disclose valuable competition strategy or violate
  competition/confidentiality rules. This is an owner-policy review, not an
  open-source-license conclusion.
- Regulation PDFs and the Wildman design are third-party works. Public
  availability is not the same as redistribution permission.

Required action:

1. Obtain an owner sign-off for what competition strategy/results may be
   disclosed and when.
2. Exclude `designs/`, `outputs/`, `.planning/`, logs, scratch data, and final
   submission artifacts from public history by default; publish only small,
   intentionally selected examples.
3. Replace regulation PDFs with official links unless redistribution terms
   are verified.
4. Remove the Wildman design or document its authoritative source and license.
5. Review all `.ork` examples for embedded designer names, decals/images,
   motor data, and other third-party assets.

### P1 — No high-confidence secret hit, but the scan is not sufficient for release

Evidence:

- A regex scan across every reachable revision found no matches for common AWS,
  GitHub, OpenAI, Google, Slack token forms, PEM private keys, or quoted
  password/token assignments.
- No suspicious credential filenames such as `.env`, `id_rsa`, `.pem`,
  `.p12`, `.pfx`, or `credentials.json` were found in reachable history.
- `gitleaks`, `trufflehog`, and `detect-secrets` are not installed, so entropy,
  encoded secrets, provider-specific variants, binary content, and validity
  checks were not covered.
- Git history exposes the author identity/email
  `L2-ootm <l2works2@gmail.com>` in 54 commits.
- Tracked planning documents expose the local account/path
  `C:\Users\Davi\...`; examples include
  `.planning/phases/01.1-barrowman-drag-table-fidelity-port-openrocket-s-real-finenes/01.1-RESEARCH.md:9,75`
  and
  `.planning/phases/01.2-barrowman-friction-formula-fin-leading-edge-sweep-drag-fidel/01.2-CONTEXT.md:64`.

Action:

- Run a current professional scanner across **all refs and binary/archive
  contents**, then manually triage results before publication. Enable host-side
  secret scanning and push protection. GitHub documents supported patterns at
  <https://docs.github.com/en/code-security/reference/secret-security/supported-secret-scanning-patterns>.
- Confirm the commit email is intentionally public; otherwise use a clean
  public history or rewrite author metadata.
- Strip absolute local paths, machine/user names, crash dumps, prompts/session
  artifacts, and internal planning files from the public snapshot.
- If any real credential is found, revoke/rotate first, then rewrite all refs.
  GitHub's primary history-removal guidance is:
  <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository>.

## Recommended license structures

### Option A — Whole repository GPL-3.0-or-later (fastest defensible route)

- Root `LICENSE`: GPL-3.0-or-later.
- `l2_engine/Cargo.toml`: `license = "GPL-3.0-or-later"` plus description,
  repository, readme, and authors policy.
- Preserve rocket-sim MIT text in `THIRD_PARTY_NOTICES.md`.
- Add OpenRocket attribution, exact source revisions/tags, changed-file map,
  modification dates, warranty/legal notices, and full GPL text.
- Treat independent third-party data/documents separately; GPL does not cure
  missing rights to unrelated PDFs or third-party designs.

This minimizes relicensing uncertainty but applies copyleft to conveyed
derivative engine code and distributions that form one covered program.

### Option B — Split license (pragmatic if the Python platform should remain permissive)

- `l2_engine/`: GPL-3.0-or-later.
- Independently authored orchestration/adapters: MIT or
  `Apache-2.0 OR MIT`.
- Root documentation must clearly map scopes and explain that combining or
  distributing the components may trigger GPL obligations where they form one
  program.
- rocket-sim remains MIT-attributed inside the GPL engine.

Use only after reviewing how tightly the Python code and Rust executable form a
single combined work. Subprocess/JSON boundaries help but are not a guaranteed
legal safe harbor.

### Option C — Permissive whole repository after clean-room remediation

- Replace every OpenRocket-derived implementation and table; use only
  independently sourced equations/standards or black-box observations.
- Resolve motor-curve data separately.
- Preserve rocket-sim's MIT notice.
- Then choose `Apache-2.0 OR MIT` (recommended for Rust ecosystem familiarity
  and an Apache patent grant) or MIT alone.
- Publish a new audited history or retain old snapshots only with the GPL
  licensing they require.

## Publish checklist

### Legal and provenance

- [ ] Choose Option A, B, or C with the copyright owner and counsel.
- [ ] Add root and component license files; add Cargo SPDX metadata.
- [ ] Add OpenRocket copyright, source revisions, modification notices/dates,
  and GPL text if retaining derived code.
- [ ] Preserve the complete rocket-sim MIT notice and record its import commit.
- [ ] Create `THIRD_PARTY_NOTICES.md`/SBOM covering registry dependencies,
  OpenTsiolkovsky history if retained, data, examples, and assets.
- [ ] Create a file-level provenance matrix for every port/table/dataset.
- [ ] Resolve or exclude the 36 OpenRocket-sourced motor curves.
- [ ] Resolve or exclude regulation PDFs and the Wildman Rocketry design.
- [ ] Confirm contributor/copyright ownership; add DCO/CLA policy if desired.

### Repository and history

- [ ] Define the public source boundary; exclude optimizer state, internal
  planning, competition finals, outputs, logs, crash dumps, caches, and
  secrets.
- [ ] Rewrite all publication refs or create a clean audited public history.
- [ ] Verify the 1.08 GB blob and all unintended generated blobs are absent
  from every public ref.
- [ ] Remove duplicate/broken OpenRocket gitlinks or add one valid pinned
  submodule with `.gitmodules`.
- [ ] Re-scan blob sizes, object names, archives, and `.ork` contents.
- [ ] Run gitleaks/trufflehog (or equivalent) across all refs and binaries;
  manually review; rotate any real secret before rewriting.
- [ ] Decide whether the existing Gmail author identity and local username may
  be public.

### Release engineering

- [ ] Ensure a fresh clone builds without untracked local JARs/repos; document
  reproducible OpenRocket acquisition and checksum/version.
- [ ] Run `cargo test --locked` for default and `viz` features.
- [ ] Run dependency license/advisory policy in CI and generate release SBOM.
- [ ] Add security policy, contribution guide, code of conduct, support scope,
  and trademark/name guidance as appropriate.
- [ ] Produce source and binary artifacts from the audited tree and confirm all
  required license/notice texts are included.
- [ ] Have a final human review open the exact public archive—not merely the
  cleaned working directory—before pushing.
