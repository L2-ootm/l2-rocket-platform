import io
from pathlib import Path

import pytest

from scripts.repo_hygiene import (
    Candidate,
    build_report,
    collect_candidates,
    confirm_cleanup,
    delete_candidate,
)


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "l2_engine").mkdir()
    (tmp_path / "l2_engine" / "Cargo.toml").write_text("[package]\nname='test'\n")
    return tmp_path


def test_audit_finds_cache_without_mutating_it(tmp_path):
    root = make_repo(tmp_path)
    cache = root / "package" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"x" * 32)

    candidates = collect_candidates(root, "safe", tracked_paths=set())

    assert any(item.path == "package/__pycache__" for item in candidates)
    assert cache.exists()
    assert build_report(root, "safe", candidates)["deletable_bytes"] == 32


def test_protected_finalization_is_never_scanned(tmp_path):
    root = make_repo(tmp_path)
    protected = root / "designs" / "osifog_finalization" / "__pycache__"
    protected.mkdir(parents=True)
    (protected / "do-not-delete.pyc").write_bytes(b"protected")

    candidates = collect_candidates(root, "deep", tracked_paths=set())

    assert not any("osifog_finalization" in item.path for item in candidates)
    assert protected.exists()


def test_tracked_content_blocks_directory_cleanup(tmp_path):
    root = make_repo(tmp_path)
    target = root / "l2_engine" / "target"
    target.mkdir()
    (target / "artifact.bin").write_bytes(b"build")

    candidates = collect_candidates(
        root,
        "safe",
        tracked_paths={"l2_engine/target/artifact.bin"},
    )

    candidate = next(item for item in candidates if item.path == "l2_engine/target")
    assert candidate.blocked == "contains tracked content"
    delete_candidate(root, candidate)
    assert target.exists()


def test_deep_profile_removes_only_manifested_untracked_state(tmp_path):
    root = make_repo(tmp_path)
    ckg = root / ".planning" / "campaign_ckg.json"
    ckg.parent.mkdir()
    ckg.write_text("{}")
    source = root / "rocket_ast.py"
    source.write_text("pass")

    candidates = collect_candidates(root, "deep", tracked_paths=set())
    candidate = next(item for item in candidates if item.path == ".planning/campaign_ckg.json")
    delete_candidate(root, candidate)

    assert not ckg.exists()
    assert source.exists()


def test_non_repository_root_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not an L2 Rocket Platform root"):
        collect_candidates(tmp_path, "safe", tracked_paths=set())


def test_delete_candidate_refuses_protected_path(tmp_path):
    root = make_repo(tmp_path)
    final = root / "designs" / "osifog_finalization" / "final.ork"
    final.parent.mkdir(parents=True)
    final.write_text("rocket")

    with pytest.raises(RuntimeError, match="became unsafe"):
        delete_candidate(
            root,
            Candidate(
                path="designs/osifog_finalization/final.ork",
                kind="file",
                bytes=6,
                reason="malicious test",
            ),
        )

    assert final.exists()


class InteractiveInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def cleanup_report(count: int = 1) -> dict:
    return {
        "profile": "safe",
        "deletable_count": count,
        "deletable_bytes": 1024,
    }


def test_interactive_cleanup_accepts_short_yes():
    output = io.StringIO()

    confirmed = confirm_cleanup(
        cleanup_report(),
        input_stream=InteractiveInput("y\n"),
        output_stream=output,
    )

    assert confirmed
    assert "[y/N]" in output.getvalue()


def test_interactive_cleanup_defaults_to_no():
    confirmed = confirm_cleanup(
        cleanup_report(),
        input_stream=InteractiveInput("\n"),
        output_stream=io.StringIO(),
    )

    assert not confirmed


def test_noninteractive_cleanup_requires_yes_flag():
    assert not confirm_cleanup(
        cleanup_report(),
        input_stream=io.StringIO("y\n"),
        output_stream=io.StringIO(),
    )
    assert confirm_cleanup(
        cleanup_report(),
        assume_yes=True,
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
    )


def test_empty_cleanup_needs_no_confirmation():
    assert confirm_cleanup(
        cleanup_report(count=0),
        input_stream=io.StringIO(),
        output_stream=io.StringIO(),
    )
