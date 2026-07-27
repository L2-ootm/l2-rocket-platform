"""Verify Candidate K visual variants against the immutable authority package."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import osifog_precision as precision
import osifog_sweep as sweep
from osifog_direct_driver import BASE


VARIANTS = (
    "candidate_K_living_terrain_institutional_v6",
    "candidate_K_celestial_datum_v7",
)
IDENTITY_KEYS = (
    "apogee_m",
    "mach",
    "s0_landing_speed",
    "s1_landing_speed",
    "min_static_margin",
)

def continuous_v3_seam_matches() -> bool:
    """The sustainer's aft contour anchors must equal the booster's fore anchors."""
    generated = ROOT / "assets/generated"
    sustainer = (
        generated / "l2-topographic-continuous-v3-sustainer.svg"
    ).read_text(encoding="utf-8")
    booster = (
        generated / "l2-topographic-continuous-v3-booster.svg"
    ).read_text(encoding="utf-8")

    def anchors(svg: str, endpoint: str) -> list[float]:
        values = []
        for path_data in re.findall(
            r'<path d="([^"]+)" fill="none" stroke=', svg
        ):
            if endpoint == "end":
                match = re.search(r"L2048(?:\.0+)? (-?\d+(?:\.\d+)?)$", path_data)
            else:
                match = re.match(r"M0(?:\.0+)? (-?\d+(?:\.\d+)?)", path_data)
            if match:
                values.append(float(match.group(1)))
        return values

    aft = anchors(sustainer, "end")
    fore = anchors(booster, "start")
    return len(aft) == len(fore) == 26 and aft == fore


def living_seam_matches(asset_stem: str) -> bool:
    """Every marching-squares crossing must continue across the stage plane."""
    generated = ROOT / "assets/generated"
    sustainer = (
        generated / f"{asset_stem}-sustainer.svg"
    ).read_text(encoding="utf-8")
    booster = (
        generated / f"{asset_stem}-booster.svg"
    ).read_text(encoding="utf-8")

    def crossings(svg: str, x_value: str) -> list[float]:
        values = []
        for path_data in re.findall(
            r'<path d="([^"]+)" fill="none" stroke=', svg
        ):
            values.extend(
                float(value)
                for value in re.findall(
                    rf"(?:M|L){x_value}(?:\.0+)? (-?\d+(?:\.\d+)?)",
                    path_data,
                )
            )
        return sorted(values)

    aft = crossings(sustainer, "2048")
    fore = crossings(booster, "0")
    return bool(aft) and aft == fore


def main() -> int:
    authority_report = json.loads(
        (REPO / "designs/osifog_submission/candidate_K_report.json").read_text(
            encoding="utf-8"
        )
    )
    authority_package = REPO / "designs/osifog_submission/candidate_K.ork"
    with zipfile.ZipFile(authority_package) as archive:
        authority_saved_xml = archive.read("rocket.ork").decode("utf-8")
    authority_certification = json.loads(
        (
            REPO
            / "OSIFOG/experiments-2026-07-25/l_twoburn/"
            "cert100_candidate_K.json"
        ).read_text(encoding="utf-8")
    )
    visual_certification = json.loads(
        (ROOT / "cert100_candidate_K_topographic.json").read_text(
            encoding="utf-8"
        )
    )
    certification_checks = {
        "seed_count_unchanged": (
            visual_certification["n"] == authority_certification["n"] == 100
        ),
        "sustainer_pass_unchanged": (
            visual_certification["sustainer_pass"]
            == authority_certification["sustainer_pass"]
            == 50
        ),
        "booster_pass_unchanged": (
            visual_certification["booster_pass"]
            == authority_certification["booster_pass"]
            == 96
        ),
        "joint_pass_unchanged": (
            visual_certification["joint_pass"]
            == authority_certification["joint_pass"]
            == 49
        ),
        "all_seed_rows_exactly_equal": (
            visual_certification["rows"] == authority_certification["rows"]
        ),
    }

    results = {}
    failed = not all(certification_checks.values())
    for variant in VARIANTS:
        delta = json.loads((ROOT / f"{variant}.json").read_text(encoding="utf-8"))
        params = copy.deepcopy(BASE)
        params.update(delta)
        generated_xml = sweep.generate_ork(params)
        report = json.loads(
            (ROOT / f"{variant}_report.json").read_text(encoding="utf-8")
        )

        with zipfile.ZipFile(ROOT / f"{variant}.ork") as archive:
            entries = archive.namelist()
            saved_xml = archive.read("rocket.ork").decode("utf-8")
            decal_names = [
                declaration["zip_name"]
                for declaration in params.get("livery_decals", [])
            ]
            decals_resolve = all(
                name in entries and len(archive.read(name)) > 0
                for name in decal_names
            )
            decal_bytes_match = all(
                archive.read(declaration["zip_name"])
                == (REPO / declaration["path"]).read_bytes()
                for declaration in params.get("livery_decals", [])
            )

        reopened = precision.inspect_saved_submission(
            ROOT / f"{variant}.ork", params
        )
        checks = {
            "generated_finish_normal_count_is_3": (
                generated_xml.count("<finish>normal</finish>") == 3
            ),
            "saved_finish_count_matches_candidate_K": (
                saved_xml.count("<finish>normal</finish>")
                == authority_saved_xml.count("<finish>normal</finish>")
            ),
            "rocket_xml_is_first_zip_entry": entries[0] == "rocket.ork",
            "declared_decals_resolve": decals_resolve,
            "packaged_decals_match_sources": decal_bytes_match,
            "exactly_one_simulation": saved_xml.count("<simulation ") == 1,
            "reopen_status_loaded_from_file": (
                reopened["status"] == "Loaded From File"
            ),
            "reopen_has_two_branches": reopened["branch_count"] == 2,
            "reopen_is_legal": bool(reopened["legal"]),
            "checklist_is_20_of_20": (
                len(report["checklist"]) == 20
                and all(item[0] for item in report["checklist"])
            ),
            "authority_score_unchanged": (
                report["score"]["raw_score"]
                == authority_report["score"]["raw_score"]
            ),
            "authority_metrics_unchanged": all(
                report["metrics"][key] == authority_report["metrics"][key]
                for key in IDENTITY_KEYS
            ),
        }
        if variant == "candidate_K_living_terrain_institutional_v6":
            checks["all_heightfield_crossings_match_at_stage_seam"] = (
                living_seam_matches("l2-living-terrain-institutional-v6")
            )
        if variant == "candidate_K_celestial_datum_v7":
            checks["all_heightfield_and_datum_crossings_match_at_stage_seam"] = (
                living_seam_matches("l2-celestial-datum-v7")
            )
        failed = failed or not all(checks.values())
        results[variant] = {
            "checks": checks,
            "zip_entries": entries,
            "appearance_count": saved_xml.count("<appearance>"),
            "generated_finish_normal_count": generated_xml.count(
                "<finish>normal</finish>"
            ),
            "saved_finish_normal_count": saved_xml.count(
                "<finish>normal</finish>"
            ),
            "authority_score": report["score"]["raw_score"],
            "reopen_status": reopened["status"],
            "reopen_branch_count": reopened["branch_count"],
        }

    output = {
        "base_candidate": "K",
        "authority_score": authority_report["score"]["raw_score"],
        "all_pass": not failed,
        "certification_100_seed": {
            "checks": certification_checks,
            "sustainer_pass": visual_certification["sustainer_pass"],
            "booster_pass": visual_certification["booster_pass"],
            "joint_pass": visual_certification["joint_pass"],
        },
        "variants": results,
    }
    verification_path = ROOT / "verification.json"
    verification_path.write_text(
        json.dumps(output, indent=2) + "\n",
        encoding="utf-8",
    )
    artifact_paths = [
        ROOT / "candidate_K_living_terrain_institutional_v6.json",
        ROOT / "candidate_K_living_terrain_institutional_v6.ork",
        ROOT / "candidate_K_living_terrain_institutional_v6_report.json",
        ROOT / "candidate_K_celestial_datum_v7.json",
        ROOT / "candidate_K_celestial_datum_v7.ork",
        ROOT / "candidate_K_celestial_datum_v7_report.json",
        ROOT / "cert100_candidate_K_topographic.json",
        verification_path,
        ROOT / "assets/generated/candidate_K_living_terrain_institutional_v6_preview.png",
        ROOT / "assets/generated/candidate_K_celestial_datum_v7_preview.png",
        ROOT / "assets/generated/l2-systems-institutional-lockup-v6.svg",
        ROOT / "assets/generated/l2-systems-institutional-lockup-v6.png",
        ROOT / "assets/generated/l2-living-terrain-institutional-v6-sustainer.svg",
        ROOT / "assets/generated/l2-living-terrain-institutional-v6-sustainer.png",
        ROOT / "assets/generated/l2-living-terrain-institutional-v6-booster.svg",
        ROOT / "assets/generated/l2-living-terrain-institutional-v6-booster.png",
        ROOT / "assets/generated/l2-systems-celestial-datum-v7.svg",
        ROOT / "assets/generated/l2-systems-celestial-datum-v7.png",
        ROOT / "assets/generated/l2-celestial-datum-v7-sustainer.svg",
        ROOT / "assets/generated/l2-celestial-datum-v7-sustainer.png",
        ROOT / "assets/generated/l2-celestial-datum-v7-booster.svg",
        ROOT / "assets/generated/l2-celestial-datum-v7-booster.png",
        ROOT / "concepts/celestial_datum_imagegen_v7.png",
        ROOT / "assets/source/L2_LOGO_DARK.png",
        ROOT / "assets/source/L2_LOGO_WHITE.png",
    ]
    manifest = {
        "authority_base": {
            "path": "designs/osifog_submission/candidate_K.ork",
            "sha256": hashlib.sha256(authority_package.read_bytes()).hexdigest(),
        },
        "artifacts": {
            str(path.relative_to(ROOT)).replace("\\", "/"): {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in artifact_paths
        },
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
