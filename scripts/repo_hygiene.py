"""Security-gated cleanup for generated repository state.

The cleaner is intentionally manifest-driven. It never accepts arbitrary delete
paths, never follows directory symlinks, and never deletes tracked files.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import stat
import subprocess
import sys
from typing import TextIO
from dataclasses import asdict, dataclass
from pathlib import Path


PROTECTED_PREFIXES = (
    ".git",
    ".agents",
    "designs/osifog_finalization",
    "designs/osifog_submission",
    "l2_engine/src",
    "l2_engine/tests/fixtures",
    "licenses",
    "tests",
)

SAFE_DIRECTORY_NAMES = {"__pycache__", ".pytest_cache", "node_modules", "target"}
SAFE_ROOT_DIRECTORIES = {".venv", "venv", ".playwright-cli"}
SAFE_FILE_PATTERNS = ("*.pyc", "hs_err_pid*.log", "replay_pid*.log")

DEEP_ROOT_DIRECTORIES = {
    ".ork_extracted",
    ".ork_extracted2",
    "logs",
    "output",
    "outputs",
    "runs",
    "temp_ork",
    "tmp",
}
DEEP_FILE_PATTERNS = (
    ".planning/*ckg*.json",
    ".planning/**/*ckg*.json",
    "designs/**/campaign_ckg.json",
    "designs/**/.campaign_ckg*.tmp",
)


@dataclass(frozen=True)
class Candidate:
    path: str
    kind: str
    bytes: int
    reason: str
    blocked: str | None = None


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_protected(relative: str) -> bool:
    return any(
        relative == prefix or relative.startswith(f"{prefix}/")
        for prefix in PROTECTED_PREFIXES
    )


def has_reparse_ancestor(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        try:
            info = current.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode):
            return True
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
        current = current.parent
    return False


def path_size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        return path.lstat().st_size
    total = 0
    for current, directories, files in os.walk(path, followlinks=False):
        base = Path(current)
        directories[:] = [
            name for name in directories if not (base / name).is_symlink()
        ]
        for name in files:
            item = base / name
            try:
                total += item.lstat().st_size
            except FileNotFoundError:
                continue
    return total


def git_tracked_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return set()
    return {
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    }


def tracked_block(relative: str, tracked: set[str]) -> str | None:
    prefix = f"{relative.rstrip('/')}/"
    if relative in tracked or any(item.startswith(prefix) for item in tracked):
        return "contains tracked content"
    return None


def matches_deep_file(relative: str) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in DEEP_FILE_PATTERNS)


def collect_candidates(
    root: Path,
    profile: str,
    tracked_paths: set[str] | None = None,
) -> list[Candidate]:
    root = root.resolve()
    if not (root / "l2_engine" / "Cargo.toml").is_file():
        raise ValueError(f"not an L2 Rocket Platform root: {root}")
    tracked = git_tracked_paths(root) if tracked_paths is None else tracked_paths
    candidates: dict[str, Candidate] = {}

    def add(path: Path, reason: str) -> None:
        relative = normalize_relative(path, root)
        blocked = None
        if is_protected(relative):
            blocked = "protected path"
        elif has_reparse_ancestor(path, root):
            blocked = "symlink or reparse point"
        else:
            blocked = tracked_block(relative, tracked)
        candidates[relative] = Candidate(
            path=relative,
            kind="directory" if path.is_dir() and not path.is_symlink() else "file",
            bytes=path_size(path),
            reason=reason,
            blocked=blocked,
        )

    for name in sorted(SAFE_ROOT_DIRECTORIES):
        path = root / name
        if path.exists() or path.is_symlink():
            add(path, "recreatable local dependency/cache")

    if profile == "deep":
        for name in sorted(DEEP_ROOT_DIRECTORIES):
            path = root / name
            if path.exists() or path.is_symlink():
                add(path, "generated run or temporary state")

    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        relative_base = normalize_relative(base, root) if base != root else ""
        if relative_base and is_protected(relative_base):
            directories[:] = []
            continue
        directories[:] = [
            name
            for name in directories
            if not is_protected(
                f"{relative_base}/{name}".strip("/")
            )
            and name != ".git"
        ]

        for name in list(directories):
            path = base / name
            relative = normalize_relative(path, root)
            if name in SAFE_DIRECTORY_NAMES:
                add(path, f"recreatable {name} directory")
                directories.remove(name)
            elif profile == "deep" and relative in DEEP_ROOT_DIRECTORIES:
                directories.remove(name)

        for name in files:
            path = base / name
            relative = normalize_relative(path, root)
            if any(fnmatch.fnmatch(name, pattern) for pattern in SAFE_FILE_PATTERNS):
                add(path, "generated cache or crash log")
            elif profile == "deep" and matches_deep_file(relative):
                add(path, "generated optimizer memory")

    return sorted(candidates.values(), key=lambda item: (-item.bytes, item.path))


def delete_candidate(root: Path, candidate: Candidate) -> None:
    if candidate.blocked:
        return
    root = root.resolve()
    path = root / candidate.path
    resolved_parent = path.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise RuntimeError(f"candidate escaped repository: {candidate.path}")
    if is_protected(candidate.path) or has_reparse_ancestor(path, root):
        raise RuntimeError(f"candidate became unsafe: {candidate.path}")
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def build_report(root: Path, profile: str, candidates: list[Candidate]) -> dict:
    deletable = [item for item in candidates if not item.blocked]
    return {
        "schema": "l2.repository-hygiene/v1",
        "root": str(root.resolve()),
        "profile": profile,
        "candidate_count": len(candidates),
        "deletable_count": len(deletable),
        "deletable_bytes": sum(item.bytes for item in deletable),
        "blocked_count": len(candidates) - len(deletable),
        "candidates": [asdict(item) for item in candidates],
    }


def confirm_cleanup(
    report: dict,
    *,
    assume_yes: bool = False,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> bool:
    """Authorize a manifested cleanup without weakening path-level safeguards."""
    if report["deletable_count"] == 0 or assume_yes:
        return True

    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stderr if output_stream is None else output_stream
    if not input_stream.isatty():
        return False

    size_mib = report["deletable_bytes"] / (1024 * 1024)
    prompt = (
        f"Delete {report['deletable_count']} manifested {report['profile']} "
        f"item(s), reclaiming {size_mib:.2f} MiB? [y/N] "
    )
    print(prompt, end="", file=output_stream, flush=True)
    return input_stream.readline().strip().lower() in {"y", "yes"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "clean"))
    parser.add_argument("--profile", choices=("safe", "deep"), default="safe")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="apply the manifested cleanup without an interactive prompt",
    )
    parser.add_argument("--root", type=Path, default=repository_root())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        candidates = collect_candidates(args.root, args.profile)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 2

    report = build_report(args.root, args.profile, candidates)
    if args.command == "audit":
        report["status"] = "audit"
        print(json.dumps(report, indent=2))
        return 0

    if not confirm_cleanup(report, assume_yes=args.yes):
        report["status"] = "refused"
        report["reason"] = (
            "cleanup was not confirmed; use an interactive terminal or pass --yes"
        )
        print(json.dumps(report, indent=2))
        return 2

    for candidate in candidates:
        delete_candidate(args.root, candidate)
    report["status"] = "cleaned"
    report["deleted_count"] = report["deletable_count"]
    report["deleted_bytes"] = report["deletable_bytes"]
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
