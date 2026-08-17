import argparse
import json
from pathlib import Path

import orhelper
from orhelper import OpenRocketInstance

import sys
from pathlib import Path

# Run from anywhere: put the repository root on sys.path so the flat
# top-level modules (osifog_sweep, rocket_forge, ...) resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from organic_loop import (
    OrganicCandidate,
    ast_to_dicts,
    load_mission_data,
    load_mission_target_apogee,
    openrocket_metrics_are_viable,
    polish_ranked_elites,
    validate_openrocket_ork,
    write_ork_zip,
)
from rocket_ast import ASTCompiler, ASTNode


def candidate_from_json(item):
    valid_keys = {
        "ast",
        "score",
        "raw_score",
        "status",
        "reason",
        "rust_apogee_m",
        "rust_mach",
        "rust_min_static_margin",
        "rust_margins",
        "or_metrics",
    }
    filtered = {key: value for key, value in item.items() if key in valid_keys}
    filtered["ast"] = [ASTNode.from_dict(node) for node in filtered.get("ast", [])]
    return OrganicCandidate(**filtered)


def load_elites(path):
    payload = json.loads(Path(path).read_text())
    return [candidate_from_json(item) for item in payload.get("elite", [])]


def load_authority_config(mission_path, target_apogee):
    constraints = {"min_static_margin": 1.5}
    phase_machs = [0.3, 2.0, 3.0]
    if mission_path:
        mission = load_mission_data(mission_path)
        constraints = mission.get("constraints", constraints)
        phase_machs = mission.get("stability", {}).get("phase_machs", phase_machs)
        target_apogee = target_apogee or load_mission_target_apogee(mission_path)
        for key in ("target_apogee_m", "target_apogee"):
            if key in mission:
                target_apogee = float(mission[key])
    if target_apogee is None:
        raise SystemExit("--target-apogee is required when --mission is omitted")
    return float(target_apogee), constraints, phase_machs


def validate_ranked_elites(elites, helper, output_dir, constraints, phase_machs, count):
    results = []
    for index, candidate in enumerate(elites[:count]):
        record = {
            "index": index,
            "rust_status": candidate.status,
            "rust_reason": candidate.reason,
            "rust_apogee_m": candidate.rust_apogee_m,
            "rust_mach": candidate.rust_mach,
        }
        if candidate.status != "success":
            record["or_metrics"] = None
            record["authority_viable"] = False
            results.append(record)
            continue

        candidate_path = output_dir / f"authority_candidate_{index:03d}.ork"
        write_ork_zip(
            candidate_path,
            ASTCompiler().compile(candidate.ast, name=f"Authority Candidate {index}"),
        )
        metrics = validate_openrocket_ork(candidate_path, helper, phase_machs)
        candidate.or_metrics = metrics
        record["ork"] = str(candidate_path)
        record["or_metrics"] = metrics
        record["authority_viable"] = openrocket_metrics_are_viable(metrics, constraints)
        results.append(record)
    return results


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate and precision-polish saved organic elites against OpenRocket."
    )
    parser.add_argument("--elite", type=Path, required=True, help="Path to organic_elite.json")
    parser.add_argument("--mission", type=Path, help="Mission JSON for constraints and target")
    parser.add_argument("--target-apogee", type=float, help="Override target apogee in meters")
    parser.add_argument("--out", type=Path, help="Output directory for authority ORKs and report")
    parser.add_argument("--jar", default="lib/OpenRocket-24.12.jar")
    parser.add_argument("--validate-count", type=int, default=8)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--skip-polish", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = args.out or args.elite.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    target_apogee, constraints, phase_machs = load_authority_config(
        args.mission, args.target_apogee
    )
    elites = load_elites(args.elite)

    with OpenRocketInstance(args.jar) as instance:
        helper = orhelper.Helper(instance)
        validation = validate_ranked_elites(
            elites,
            helper,
            output_dir,
            constraints,
            phase_machs,
            min(args.validate_count, len(elites)),
        )

        polished_ast = None
        polish_index = None
        if not args.skip_polish:
            polished_ast, polish_index = polish_ranked_elites(
                elites,
                target_apogee,
                helper,
                output_dir,
                constraints,
                tolerance_m=args.tolerance,
                phase_machs=phase_machs,
            )

    report = {
        "elite": str(args.elite),
        "mission": str(args.mission) if args.mission else None,
        "target_apogee_m": target_apogee,
        "constraints": constraints,
        "phase_machs": phase_machs,
        "validation": validation,
        "polish": {
            "accepted_index": polish_index,
            "wrote_precision_polished_elite": polished_ast is not None,
            "ast": ast_to_dicts(polished_ast) if polished_ast is not None else None,
        },
    }
    report_path = output_dir / "authority_polish_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Wrote authority report to {report_path}")
    if polish_index is None and not args.skip_polish:
        print("No ranked elite satisfied every OpenRocket authority gate for polishing.")
    elif polish_index is not None:
        print(f"Accepted ranked elite index {polish_index} for polishing.")


if __name__ == "__main__":
    main()
