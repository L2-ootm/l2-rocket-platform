"""Read-only startup audit for immutable OSIFOG submission candidates.

This script does not start OpenRocket, rerun simulations, or write files.  It
verifies the exact candidate bytes and their saved-data/report provenance.

Usage:
  py -X utf8 scripts/osifog_session_check.py
  py -X utf8 scripts/osifog_session_check.py --candidate candidate_G
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


REPO = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO / "designs" / "osifog_submission" / "manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def count_robust_cells(table: dict) -> int:
    count = 0
    for cell in table.values():
        if "error" in cell:
            continue
        sustainer = cell.get("sustainer_v")
        booster = cell.get("booster_v")
        if (
            sustainer is not None
            and booster is not None
            and float(sustainer) < 5.0
            and float(booster) < 5.0
        ):
            count += 1
    return count


def inspect_saved_ork(path: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            corrupt = archive.testzip()
            if corrupt:
                errors.append(f"ZIP CRC failure in {corrupt}")
            if "rocket.ork" not in archive.namelist():
                return {}, errors + ["ZIP has no rocket.ork member"]
            xml_bytes = archive.read("rocket.ork")
    except (OSError, zipfile.BadZipFile) as exc:
        return {}, [f"invalid ORK ZIP: {exc}"]

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return {}, errors + [f"invalid rocket.ork XML: {exc}"]

    simulations = root.findall(".//simulation")
    branches = root.findall(".//databranch")
    extensions = root.findall(".//extension")
    seed_tags = [
        element.tag
        for element in root.iter()
        if "seed" in str(element.tag).lower()
    ]
    scripting_extensions = [
        element
        for element in extensions
        if "ScriptingExtension" in element.attrib.get("extensionid", "")
    ]

    if len(simulations) != 1:
        errors.append(f"expected 1 simulation, found {len(simulations)}")
    elif simulations[0].attrib.get("status", "").lower() != "uptodate":
        errors.append(
            "saved simulation status is "
            + repr(simulations[0].attrib.get("status"))
        )
    if len(branches) != 2:
        errors.append(f"expected 2 saved flight branches, found {len(branches)}")
    if len(scripting_extensions) != 1:
        errors.append(
            "expected 1 anti-tumble scripting extension, found "
            f"{len(scripting_extensions)}"
        )

    return {
        "simulation_count": len(simulations),
        "branch_count": len(branches),
        "branch_names": [branch.attrib.get("name", "") for branch in branches],
        "extension_count": len(extensions),
        "seed_tags": seed_tags,
    }, errors


def audit_candidate(
    submission_dir: Path,
    name: str,
    spec: dict,
    authority: dict,
) -> tuple[dict, list[str]]:
    errors: list[str] = []
    paths = {
        key: submission_dir / spec[key]
        for key in ("params", "ork", "report", "robustness")
    }
    for label, path in paths.items():
        if not path.is_file():
            errors.append(f"missing {label}: {path}")
    if errors:
        return {}, errors

    actual_hash = sha256_file(paths["ork"])
    if actual_hash != str(spec["sha256"]).upper():
        errors.append(
            f"SHA-256 mismatch: expected {spec['sha256']}, got {actual_hash}"
        )

    try:
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
        robustness = json.loads(paths["robustness"].read_text(encoding="utf-8"))
        json.loads(paths["params"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, errors + [f"invalid JSON companion: {exc}"]

    expected_seed = int(authority["simulation_seed"])
    report_seed = int(report.get("seed", -1))
    if report_seed != expected_seed:
        errors.append(
            f"report seed mismatch: expected {expected_seed}, got {report_seed}"
        )

    report_score = float(report.get("score", {}).get("raw_score", float("nan")))
    expected_score = float(spec["authority_score"])
    if abs(report_score - expected_score) > 1.0e-6:
        errors.append(
            f"report score mismatch: expected {expected_score}, got {report_score}"
        )

    checklist = report.get("checklist", [])
    passes = sum(1 for item in checklist if item and bool(item[0]))
    expected_passes = int(spec["checklist_passes"])
    if passes != expected_passes or len(checklist) != expected_passes:
        errors.append(
            f"checklist mismatch: {passes}/{len(checklist)}, "
            f"expected {expected_passes}/{expected_passes}"
        )

    robust_cells = count_robust_cells(robustness)
    expected_robust = int(spec["robust_cells"])
    if robust_cells != expected_robust:
        errors.append(
            f"robustness mismatch: expected {expected_robust}, got {robust_cells}"
        )

    ork_facts, ork_errors = inspect_saved_ork(paths["ork"])
    errors.extend(ork_errors)
    seed_persisted = bool(ork_facts.get("seed_tags"))
    if seed_persisted != bool(authority["seed_persisted_in_ork"]):
        errors.append(
            "serialized-seed expectation mismatch: "
            f"manifest={authority['seed_persisted_in_ork']} "
            f"ORK={seed_persisted}"
        )

    return {
        "name": name,
        "role": spec["role"],
        "sha256": actual_hash,
        "score": report_score,
        "checklist": f"{passes}/{len(checklist)}",
        "robust_cells": f"{robust_cells}/{len(robustness)}",
        **ork_facts,
    }, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="candidate manifest (default: designs/osifog_submission/manifest.json)",
    )
    parser.add_argument(
        "--candidate",
        help="audit one manifest candidate instead of all candidates",
    )
    args = parser.parse_args(argv)

    manifest_path = args.manifest.resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL manifest: {exc}")
        return 1

    candidates = manifest.get("candidates", {})
    if args.candidate:
        if args.candidate not in candidates:
            print(f"FAIL unknown candidate {args.candidate!r}")
            return 1
        selected = {args.candidate: candidates[args.candidate]}
    else:
        selected = candidates

    submission_dir = manifest_path.parent
    failures = 0
    for name, spec in selected.items():
        facts, errors = audit_candidate(
            submission_dir, name, spec, manifest["authority"]
        )
        if errors:
            failures += 1
            print(f"FAIL {name} ({spec.get('role', 'unknown role')})")
            for error in errors:
                print(f"  - {error}")
            continue
        print(
            f"PASS {name}: score={facts['score']:.1f}, "
            f"checks={facts['checklist']}, robust={facts['robust_cells']}, "
            f"branches={facts['branch_count']}, status=uptodate"
        )
        print(f"  SHA-256 {facts['sha256']}")

    authority = manifest["authority"]
    print(
        "\nSeed provenance: stored flight data was generated with seed "
        f"{authority['simulation_seed']}; the ORK does not serialize that seed."
    )
    print(
        "Inspecting the saved data preserves the authority result. Pressing Run "
        "is a new realization unless the authority code restores the seed and "
        "seeds every multilevel wind model."
    )
    if failures:
        print(f"\nBLOCKED: {failures} candidate audit(s) failed.")
        return 1
    print(
        f"\nREADY: active={manifest['active_candidate']}, "
        f"fallback={manifest['fallback_candidate']}; no files were changed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
