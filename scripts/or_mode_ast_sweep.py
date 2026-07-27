import argparse
import hashlib
import json
from pathlib import Path

from organic_loop import (
    OrganicLoopConfig,
    load_mission_data,
    load_mission_target_apogee,
    run_generation,
    validate_openrocket_ork,
    write_json_report,
)


DEFAULT_OPENROCKET_JAR = Path("lib/OpenRocket-24.12.jar")


def authority_metadata(jar_path):
    path = Path(jar_path)
    digest = None
    if path.is_file():
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return {
        "product": "OpenRocket",
        "jar": str(path),
        "jar_sha256": digest,
        "deterministic_seed": 16000,
    }


def pct_error(rust_value, official_value):
    if official_value == 0:
        return 0.0 if rust_value == 0 else float("inf")
    return (rust_value - official_value) / official_value * 100.0


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def max_or_zero(values):
    values = list(values)
    return max(values) if values else 0.0


def parse_seeds(raw):
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def load_elites(path):
    payload = json.loads(Path(path).read_text())
    return payload, payload.get("elite", [])


def calibration_case(seed, index, elite, or_metrics):
    rust = {
        "apogee_m": float(elite.get("rust_apogee_m", 0.0)),
        "mach": float(elite.get("rust_mach", 0.0)),
        "min_static_margin": float(elite.get("rust_min_static_margin", 0.0)),
        "score": float(elite.get("score", 0.0)),
    }
    if or_metrics.get("status") != "success":
        return {
            "seed": seed,
            "index": index,
            "status": "failed",
            "reason": or_metrics.get("reason", "openrocket_failed"),
            "rust": rust,
            "openrocket": None,
            "delta": {"apogee_pct": None, "mach": None},
            "ork": elite.get("ork"),
        }

    official = {
        "apogee_m": float(or_metrics.get("apogee_m", 0.0)),
        "mach": float(or_metrics.get("mach", 0.0)),
        "flight_time_s": float(or_metrics.get("flight_time_s", 0.0)),
    }
    return {
        "seed": seed,
        "index": index,
        "status": "success",
        "rust": rust,
        "openrocket": official,
        "delta": {
            "apogee_m": rust["apogee_m"] - official["apogee_m"],
            "apogee_pct": pct_error(rust["apogee_m"], official["apogee_m"]),
            "mach": rust["mach"] - official["mach"],
        },
        "ork": elite.get("ork"),
    }


def summarize(cases):
    successful = [case for case in cases if case["status"] == "success"]
    failed = [case for case in cases if case["status"] != "success"]
    apogee_errors = [abs(case["delta"]["apogee_pct"]) for case in successful]
    mach_errors = [abs(case["delta"]["mach"]) for case in successful]
    return {
        "count": len(cases),
        "success_count": len(successful),
        "failure_count": len(failed),
        "mean_abs_apogee_pct": mean(apogee_errors),
        "max_abs_apogee_pct": max_or_zero(apogee_errors),
        "mean_abs_mach": mean(mach_errors),
        "max_abs_mach": max_or_zero(mach_errors),
    }


def run_seed(seed, args):
    seed_dir = args.out / f"seed_{seed}"
    mission_path = getattr(args, "mission", None)
    target_apogee = args.target_apogee
    objectives = None
    constraints = None
    phase_machs = [0.3, 2.0, 3.0]
    if mission_path:
        mission = load_mission_data(mission_path)
        target_apogee = load_mission_target_apogee(mission_path)
        objectives = mission.get("objectives")
        constraints = mission.get("constraints", {})
        phase_machs = mission.get("stability", {}).get("phase_machs", phase_machs)
        constraints = {**constraints, "phase_machs": phase_machs}
    config = OrganicLoopConfig(
        population=args.population,
        elite_count=args.elite_count,
        generations=args.generations,
        seed=seed,
        target_apogee_m=target_apogee,
        mission_path=mission_path,
        output_dir=seed_dir,
        ckg_path=args.ckg_dir / f"seed_{seed}.json",
        evaluator="rust",
        physics_mode=args.physics,
        validate_openrocket=0,
        objectives=objectives,
        constraints=constraints,
        phase_machs=phase_machs,
    )
    run_generation(config)
    payload, elites = load_elites(seed_dir / "organic_elite.json")
    return {
        "seed": seed,
        "output_dir": str(seed_dir),
        "payload": payload,
        "elites": elites,
        "target_apogee_m": target_apogee,
        "phase_machs": phase_machs,
    }


def run_sweep(args):
    args.out.mkdir(parents=True, exist_ok=True)
    args.ckg_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    seed_runs = [run_seed(seed, args) for seed in parse_seeds(args.seeds)]
    cases = []

    if args.validate_count > 0:
        import orhelper
        from orhelper import OpenRocketInstance

        jar = Path(getattr(args, "jar", DEFAULT_OPENROCKET_JAR))
        if not jar.is_file():
            raise FileNotFoundError(f"OpenRocket authority JAR not found: {jar}")
        or_instance = OpenRocketInstance(str(jar)).__enter__()
        helper = orhelper.Helper(or_instance)
        try:
            for run in seed_runs:
                for index, elite in enumerate(run["elites"][: args.validate_count]):
                    ork = elite.get("ork")
                    if not ork:
                        cases.append(
                            calibration_case(
                                run["seed"],
                                index,
                                elite,
                                {"status": "failed", "reason": "missing_ork"},
                            )
                        )
                        continue
                    or_metrics = validate_openrocket_ork(
                        Path(ork), helper, run["phase_machs"]
                    )
                    cases.append(calibration_case(run["seed"], index, elite, or_metrics))
        finally:
            or_instance.__exit__(None, None, None)

    report = {
        "physics_mode": args.physics,
        "authority": authority_metadata(getattr(args, "jar", DEFAULT_OPENROCKET_JAR)),
        "mission": str(getattr(args, "mission", None)) if getattr(args, "mission", None) else None,
        "target_apogee_m": (
            seed_runs[0].get("target_apogee_m", args.target_apogee)
            if seed_runs
            else args.target_apogee
        ),
        "population": args.population,
        "generations": args.generations,
        "elite_count": args.elite_count,
        "validate_count": args.validate_count,
        "seeds": [run["seed"] for run in seed_runs],
        "runs": [{"seed": run["seed"], "output_dir": run["output_dir"]} for run in seed_runs],
        "summary": summarize(cases),
        "cases": cases,
    }
    write_json_report(args.report, report)
    return report


def parse_args():
    parser = argparse.ArgumentParser(
        description="Measure the organic Rust OR-mode proxy against OpenRocket 24.12 authority."
    )
    parser.add_argument(
        "--seeds",
        default="2026070406,2026070407,2026070408,2026070409,2026070410",
    )
    parser.add_argument("--population", type=int, default=12)
    parser.add_argument("--elite-count", type=int, default=4)
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--target-apogee", type=float, default=15000.0)
    parser.add_argument("--mission", type=Path)
    parser.add_argument("--physics", choices=["openrocket"], default="openrocket")
    parser.add_argument("--jar", type=Path, default=DEFAULT_OPENROCKET_JAR)
    parser.add_argument("--validate-count", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("designs/or_mode_sweep"))
    parser.add_argument("--ckg-dir", type=Path, default=Path(".planning/or_mode_sweep"))
    parser.add_argument("--report", type=Path, default=Path("designs/or_mode_sweep/report.json"))
    return parser.parse_args()


def main():
    args = parse_args()
    report = run_sweep(args)
    summary = report["summary"]
    print(
        "validated {success}/{count} cases | mean abs apogee {apogee:.2f}% | "
        "mean abs Mach {mach:.3f}".format(
            success=summary["success_count"],
            count=summary["count"],
            apogee=summary["mean_abs_apogee_pct"],
            mach=summary["mean_abs_mach"],
        )
    )
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
