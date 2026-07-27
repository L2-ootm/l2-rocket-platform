import argparse
import json
from pathlib import Path

from l2_hyper.mission import load_mission


def pct_error(rust_value, or_value):
    if or_value == 0:
        return 0.0 if rust_value == 0 else float("inf")
    return (rust_value - or_value) / or_value * 100.0


def abs_pct_error(rust_value, or_value):
    return abs(pct_error(rust_value, or_value))


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def max_or_zero(values):
    values = list(values)
    return max(values) if values else 0.0


def load_elite_members(path):
    payload = json.loads(Path(path).read_text())
    if isinstance(payload, dict):
        members = payload.get("elite", [])
        return payload, members
    if isinstance(payload, list):
        return {"generated_by": None, "fitness_def": None, "elite": payload}, payload
    raise ValueError(f"unsupported elite payload in {path}")


def member_genome(member):
    if isinstance(member, dict) and "genome" in member:
        return member["genome"]
    if isinstance(member, dict):
        return member
    raise ValueError(f"unsupported elite member: {member!r}")


def rust_metrics(member):
    return {
        "apogee_m": float(member.get("rust_apogee_m", 0.0)),
        "mach": float(member.get("rust_mach", 0.0)),
        "min_static_margin": float(member.get("rust_static_margin_min", 0.0)),
        "score": float(member.get("rust_score", 0.0)),
    }


def compare_metrics(index, member, or_metrics):
    rust = rust_metrics(member)
    official = {
        "apogee_m": float(or_metrics.get("apogee", 0.0)),
        "mach": float(or_metrics.get("mach", 0.0)),
        "min_static_margin": float(or_metrics.get("min_static_margin", 0.0)),
        "flight_time_s": float(or_metrics.get("flight_time", 0.0)),
        "vmax_mps": float(or_metrics.get("vmax", 0.0)),
    }
    return {
        "index": index,
        "rust": rust,
        "openrocket": official,
        "delta": {
            "apogee_m": rust["apogee_m"] - official["apogee_m"],
            "apogee_pct": pct_error(rust["apogee_m"], official["apogee_m"]),
            "mach": rust["mach"] - official["mach"],
            "min_static_margin": rust["min_static_margin"] - official["min_static_margin"],
        },
        "status": {
            "tumbled": bool(or_metrics.get("tumbled", False)),
            "late_ignition": bool(or_metrics.get("late_ignition", False)),
            "warnings": list(or_metrics.get("warnings", [])),
        },
        "genome": member_genome(member),
    }


def failed_case(index, member, reason):
    rust = rust_metrics(member)
    return {
        "index": index,
        "rust": rust,
        "openrocket": None,
        "delta": {
            "apogee_m": None,
            "apogee_pct": None,
            "mach": None,
            "min_static_margin": None,
        },
        "status": {
            "failed": True,
            "reason": reason,
        },
        "genome": member_genome(member),
    }


def summarize(cases):
    successful = [case for case in cases if case.get("openrocket") is not None]
    failed = [case for case in cases if case.get("openrocket") is None]
    return {
        "count": len(cases),
        "success_count": len(successful),
        "failure_count": len(failed),
        "mean_abs_apogee_pct": mean(abs(case["delta"]["apogee_pct"]) for case in successful),
        "max_abs_apogee_pct": max_or_zero(abs(case["delta"]["apogee_pct"]) for case in successful),
        "mean_abs_mach": mean(abs(case["delta"]["mach"]) for case in successful),
        "max_abs_mach": max_or_zero(abs(case["delta"]["mach"]) for case in successful),
        "mean_abs_min_static_margin": mean(abs(case["delta"]["min_static_margin"]) for case in successful),
        "max_abs_min_static_margin": max_or_zero(abs(case["delta"]["min_static_margin"]) for case in successful),
    }


def run_calibration(mission_path, elite_path, count, keep_orks=None):
    from l2_hyper.orkit import OpenRocketSession

    mission = load_mission(mission_path)
    elite_payload, members = load_elite_members(elite_path)
    selected = members[:count] if count else members

    cases = []
    with OpenRocketSession() as session:
        motors = session.resolve_motors(mission["stack"])
        for idx, member in enumerate(selected):
            keep_path = None
            if keep_orks:
                keep_path = str(Path(keep_orks) / f"case_{idx:03d}.ork")
            try:
                or_metrics = session.evaluate(mission, member_genome(member), motors, keep_path=keep_path)
            except Exception as exc:
                cases.append(failed_case(idx, member, repr(exc)))
                continue
            cases.append(compare_metrics(idx, member, or_metrics))

    return {
        "mission": str(mission_path),
        "elite_file": str(elite_path),
        "elite_generated_by": elite_payload.get("generated_by"),
        "elite_fitness_def": elite_payload.get("fitness_def"),
        "summary": summarize(cases),
        "cases": cases,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Compare l2_engine OR-mode elite metrics against OpenRocket truth.")
    parser.add_argument("--mission", required=True)
    parser.add_argument("--elite", required=True)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("designs/organic/or_mode_calibration.json"))
    parser.add_argument("--keep-orks", type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    report = run_calibration(args.mission, args.elite, args.count, args.keep_orks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    summary = report["summary"]
    print(
        "calibrated {count} cases | mean abs apogee {apogee:.2f}% | "
        "mean abs Mach {mach:.3f} | mean abs margin {margin:.3f} cal".format(
            count=summary["count"],
            apogee=summary["mean_abs_apogee_pct"],
            mach=summary["mean_abs_mach"],
            margin=summary["mean_abs_min_static_margin"],
        )
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
