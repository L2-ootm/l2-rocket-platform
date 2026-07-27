# Repository hygiene

`scripts/repo_hygiene.py` audits and removes known generated state without
accepting arbitrary paths.

## Safety model

- Audit is the default non-mutating workflow.
- Cleanup paths come from a source-controlled manifest.
- Canonical paths must remain inside the repository.
- Symlinks and Windows reparse points are blocked.
- Git-tracked content is never deleted.
- Engine source, tests/fixtures, licenses, agent contracts, and final OSIFOG
  packages are protected.
- Cleanup emits a JSON report containing every candidate, block reason, and
  byte count.

## Safe cleanup

The safe profile covers build output, dependency directories, Python caches,
and crash logs:

```powershell
python scripts/repo_hygiene.py audit --profile safe
python scripts/repo_hygiene.py clean --profile safe
```

The clean command asks for a short `y/N` confirmation. For trusted automation,
use `--yes`:

```powershell
python scripts/repo_hygiene.py clean --profile safe --yes
```

## Deep cleanup

The deep profile additionally covers local run directories, output folders,
temporary ORK extraction, and generated CKG memory:

```powershell
python scripts/repo_hygiene.py audit --profile deep
python scripts/repo_hygiene.py clean --profile deep
```

For unattended maintenance:

```powershell
python scripts/repo_hygiene.py clean --profile deep --yes
```

Deep cleanup is intended for completed campaigns. Copy selected designs and
reports into a curated reference directory before running it.

## Entropy budget

- Clean source checkout target: under 100 MiB excluding Git history and
  optional authority fixtures.
- No regular Git blob over 25 MiB without an explicit release-artifact review.
- `runs/`, `.local/`, CKG memory, simulator downloads, game data, and build
  products remain ignored.
- Review direct dependencies and generated-state growth at release boundaries.
- Run safe cleanup before switching long-lived branches or creating a release.
