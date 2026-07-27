import json
from pathlib import Path

from scripts.osifog_session_check import (
    audit_candidate,
    count_robust_cells,
)


REPO = Path(__file__).resolve().parents[1]
SUBMISSION = REPO / "designs" / "osifog_submission"


def test_count_robust_cells_requires_both_stages_below_limit():
    table = {
        "pass": {"sustainer_v": 4.9, "booster_v": 4.9},
        "sustainer_fail": {"sustainer_v": 5.0, "booster_v": 1.0},
        "booster_fail": {"sustainer_v": 1.0, "booster_v": 5.0},
        "error": {"error": "simulation failed"},
        "missing": {"sustainer_v": 1.0, "booster_v": None},
    }
    assert count_robust_cells(table) == 1


def test_immutable_submission_manifest_matches_candidate_bytes_and_reports():
    manifest = json.loads((SUBMISSION / "manifest.json").read_text(encoding="utf-8"))

    for name, spec in manifest["candidates"].items():
        facts, errors = audit_candidate(
            SUBMISSION, name, spec, manifest["authority"]
        )
        assert errors == []
        assert facts["simulation_count"] == 1
        assert facts["branch_names"] == ["Sustainer", "Booster"]
        assert facts["extension_count"] == 1
        assert facts["seed_tags"] == []
