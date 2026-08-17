"""pytest configuration.

`OSIFOG/` holds the 2026 competition materials and is intentionally not
distributed with this repository (see `.gitignore`). Much of the suite reaches
`OSIFOG/OpenWind_File.csv` — sometimes directly, more often several calls deep
through a helper such as `osifog_precision.falcon_submission_candidate()`. In a
fresh clone that file is absent, and the result was that `pytest` aborted during
collection and ran nothing at all.

Two mechanisms, because collection and execution fail differently:

1. **Collection.** A module that reads the CSV at import time raises
   `FileNotFoundError` while pytest is importing it, and a collection error
   aborts the whole run. Those modules must be ignored outright. They are found
   by parsing each test module and following its module-level imports of
   first-party modules — `test_flip_diagnosis_helpers` never mentions the CSV
   itself, it imports `scripts.flip_diagnosis`, which reads it on import.

2. **Execution.** Everything else fails at call time, at unpredictable depth.
   Rather than guess statically which tests those are — which either misses
   indirect paths or over-skips every module that merely touches
   `osifog_sweep` — the missing file is detected where it actually surfaces and
   the failure is converted into a skip with a clear reason.

Both are inert when the file is present: nothing is ignored, nothing is skipped.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
WIND_CSV = ROOT / "OSIFOG" / "OpenWind_File.csv"

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_TRIGGER = "parse_wind_csv"
_MISSING = WIND_CSV.name

SKIP_REASON = (
    f"requires OSIFOG/{_MISSING} — 2026 competition material, not distributed "
    "with this repository (see .gitignore)"
)


def _module_level_nodes(tree: ast.Module):
    """Yield every node reachable without entering a function or class body."""
    for node in tree.body:
        if isinstance(node, _SCOPES):
            continue
        yield from ast.walk(node)


def _is_trigger_call(node: ast.AST) -> bool:
    """True for `parse_wind_csv(...)` and `anything.parse_wind_csv(...)`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == _TRIGGER
    if isinstance(func, ast.Attribute):
        return func.attr == _TRIGGER
    return False


def _resolve(module: str) -> Path | None:
    """Map a dotted first-party module name to a file in this repository."""
    for base in (ROOT, ROOT / "src"):
        candidate = base.joinpath(*module.split("."))
        for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
            if path.is_file():
                return path
    return None


def _reads_wind_csv_at_import(path: Path, seen: set[Path] | None = None) -> bool:
    seen = seen if seen is not None else set()
    resolved = path.resolve()
    if resolved in seen:
        return False
    seen.add(resolved)

    try:
        tree = ast.parse(resolved.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, ValueError, OSError):
        return False

    imported: set[str] = set()
    for node in _module_level_nodes(tree):
        if _is_trigger_call(node):
            return True
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module)

    for module in imported:
        target = _resolve(module)
        if target is not None and _reads_wind_csv_at_import(target, seen):
            return True
    return False


collect_ignore: list[str] = []

if not WIND_CSV.exists():
    collect_ignore = sorted(
        p.name for p in TESTS_DIR.glob("test_*.py") if _reads_wind_csv_at_import(p)
    )


OR_JAR = ROOT / "lib" / "OpenRocket-24.12.jar"
OR_SKIP_REASON = (
    "requires lib/OpenRocket-24.12.jar — download OpenRocket 24.12 jar to run full JVM simulation tests"
)


def _is_missing_wind_csv(excinfo) -> bool:
    if excinfo is None:
        return False
    error = excinfo.value
    return isinstance(error, (FileNotFoundError, OSError)) and _MISSING in str(error)


def _is_missing_or_jar(excinfo) -> bool:
    if excinfo is None:
        return False
    error = excinfo.value
    return (
        isinstance(error, (FileNotFoundError, OSError))
        and ("OpenRocket" in str(error) or "openrocket" in str(error).lower())
        and "jar" in str(error).lower()
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Report a missing competition CSV or OpenRocket JAR as a skip rather than a failure."""
    outcome = yield
    report = outcome.get_result()
    if not report.failed or call.excinfo is None:
        return

    if not WIND_CSV.exists() and _is_missing_wind_csv(call.excinfo):
        report.outcome = "skipped"
        report.longrepr = (str(item.fspath), None, f"Skipped: {SKIP_REASON}")
    elif not OR_JAR.exists() and _is_missing_or_jar(call.excinfo):
        report.outcome = "skipped"
        report.longrepr = (str(item.fspath), None, f"Skipped: {OR_SKIP_REASON}")


def pytest_sessionfinish(session, exitstatus):
    """Cleanly exit when JPype JVM is started to avoid native thread teardown races on Linux."""
    import os
    import sys
    try:
        import jpype
        if jpype.isJVMStarted():
            sys.stdout.flush()
            sys.stderr.flush()
            # os._exit bypasses glibc static destruction races with running JVM daemon threads
            os._exit(exitstatus)
    except Exception:
        pass
