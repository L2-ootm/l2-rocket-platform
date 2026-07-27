"""Calibrate saved organic AST elites against OpenRocket 24.12 authority."""

import argparse
import json
import tempfile
from pathlib import Path

from organic_loop import (
    ast_from_dicts,
    load_mission_data,
    validate_openrocket_ork,
    write_json_report,
    write_ork_zip,
)
from rocket_ast import ASTCompiler
from scripts.or_mode_ast_sweep import (
    DEFAULT_OPENROCKET_JAR,
    authority_metadata,
    max_or_zero,
    mean,
    pct_error,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_elites(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("elite"), list):
        raise ValueError(f"expected organic elite payload in {path}")
    return payload, payload["elite"]


def comparison_case(index, member, metrics):
    rust = {
        "apogee_m": float(member.get("rust_apogee_m", 0.0)),
        "mach": float(member.get("rust_mach", 0.0)),
        "min_static_margin": float(member.get("rust_min_static_margin", 0.0)),
        "score": float(member.get("score", 0.0)),
    }
    case = {
        "index": index,
        "status": metrics.get("status", "failed"),
        "reason": metrics.get("reason", "ok"),
        "rust": rust,
        "openrocket": metrics,
        "ork": member.get("ork"),
        "delta": {
            "apogee_m": None,
            "apogee_pct": None,
            "mach": None,
            "min_static_margin": None,
        },
    }
    if metrics.get("status") == "success":
        case["delta"] = {
            "apogee_m": rust["apogee_m"] - float(metrics["apogee_m"]),
            "apogee_pct": pct_error(rust["apogee_m"], float(metrics["apogee_m"])),
            "mach": rust["mach"] - float(metrics["mach"]),
            "min_static_margin": rust["min_static_margin"]
            - float(metrics["min_static_margin"]),
        }
    return case


def summarize(cases):
    successful = [case for case in cases if case["status"] == "success"]
    apogee = [abs(case["delta"]["apogee_pct"]) for case in successful]
    mach = [abs(case["delta"]["mach"]) for case in successful]
    margins = [abs(case["delta"]["min_static_margin"]) for case in successful]
    return {
        "count": len(cases),
        "success_count": len(successful),
        "failure_count": len(cases) - len(successful),
        "mean_abs_apogee_pct": mean(apogee),
        "max_abs_apogee_pct": max_or_zero(apogee),
        "mean_abs_mach": mean(mach),
        "max_abs_mach": max_or_zero(mach),
        "mean_abs_min_static_margin": mean(margins),
        "max_abs_min_static_margin": max_or_zero(margins),
    }


def resolve_or_compile_ork(member, index, scratch):
    stored = member.get("ork")
    if stored:
        path = Path(stored)
        if not path.is_absolute():
            path = REPO_ROOT / path
        if path.is_file():
            return path
    ast = member.get("ast")
    if not ast:
        raise ValueError("elite has neither an existing .ork path nor an AST")
    path = Path(scratch) / f"elite_{index:03d}.ork"
    xml = ASTCompiler().compile(ast_from_dicts(ast), name=f"Authority Elite {index}")
    write_ork_zip(path, xml)
    return path


def run_calibration(elite_path, mission_path, count, jar_path):
    import orhelper
    from orhelper import OpenRocketInstance

    elite_payload, members = load_elites(elite_path)
    selected = members[:count] if count else members
    mission = load_mission_data(mission_path) if mission_path else {}
    phase_machs = mission.get("stability", {}).get("phase_machs", [0.3, 2.0, 3.0])
    constraints = mission.get("constraints", {})
    jar_path = Path(jar_path)
    if not jar_path.is_file():
        raise FileNotFoundError(f"OpenRocket authority JAR not found: {jar_path}")

    cases = []
    with tempfile.TemporaryDirectory(prefix="l2-or-calibrate-") as scratch:
        instance = OpenRocketInstance(str(jar_path)).__enter__()
        helper = orhelper.Helper(instance)
        try:
            for index, member in enumerate(selected):
                try:
                    ork_path = resolve_or_compile_ork(member, index, scratch)
                    metrics = validate_openrocket_ork(ork_path, helper, phase_machs)
                except Exception as exc:
                    metrics = {"status": "failed", "reason": repr(exc)}
                case = comparison_case(index, member, metrics)
                case["authority_gate_reason"] = (
                    ""
                    if metrics.get("status") == "success"
                    else metrics.get("reason", "validation_failed")
                )
                cases.append(case)
        finally:
            instance.__exit__(None, None, None)

    return {
        "authority": authority_metadata(jar_path),
        "elite_file": str(elite_path),
        "elite_generated_by": elite_payload.get("generated_by"),
        "mission": str(mission_path) if mission_path else None,
        "phase_machs": phase_machs,
        "constraints": constraints,
        "summary": summarize(cases),
        "cases": cases,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare saved organic elites with deterministic OpenRocket 24.12 authority."
    )
    parser.add_argument("--elite", type=Path, required=True)
    parser.add_argument("--mission", type=Path)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--jar", type=Path, default=DEFAULT_OPENROCKET_JAR)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("designs/or_mode_calibration_24_12.json"),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    report = run_calibration(args.elite, args.mission, args.count, args.jar)
    write_json_report(args.out, report)
    summary = report["summary"]
    print(
        "validated {success}/{count} | mean abs apogee {apogee:.3f}% | "
        "mean abs Mach {mach:.4f} | failed {failed}".format(
            success=summary["success_count"],
            count=summary["count"],
            apogee=summary["mean_abs_apogee_pct"],
            mach=summary["mean_abs_mach"],
            failed=summary["failure_count"],
        )
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
