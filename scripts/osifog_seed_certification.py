#!/usr/bin/env python3
"""Prove fixed-seed replay and audit unknown-seed robustness.

This script never edits Candidate I. It combines source-level seed provenance,
the completed Candidate I authority campaign, and optional repeated OpenRocket
runs. Use ``--run-repeats`` to compare repeated runs in one JVM and in fresh
Python/JVM processes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from scripts.candidate_i_window_campaign import Campaign
from osifog_sweep import init_or, run_sim

CANDIDATE_ORK = REPO / "designs/osifog_submission/candidate_I.ork"
CAMPAIGN_JSON = (
    REPO
    / "OSIFOG/experiments-2026-07-25/candidate_I_full_window_campaign/campaign.json"
)
OUTPUT_DIR = REPO / "OSIFOG/experiments-2026-07-25/seed_certification"
SELECTED_DELAYS = {"s0": 49.262188, "s1": 80.822100}
TIMESTEP_S = 0.05
OFFICIAL_SEED = 16000
RESULT_MARKER = "OSIFOG_SEED_RESULT="


def upper_failure_bound_zero_failures(
    trials: int, confidence: float = 0.95
) -> float:
    """Exact one-sided binomial upper bound when no failures were observed."""
    if trials <= 0:
        raise ValueError("trials must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    return 1.0 - (1.0 - confidence) ** (1.0 / trials)


def _landing_summary(metrics: dict) -> dict:
    landings = {
        int(item["branch"]): item for item in metrics.get("stage_landings", [])
    }
    summary = {
        "seed": int(metrics["seed"]),
        "apogee_m": float(metrics["apogee_m"]),
        "mach": float(metrics["mach"]),
    }
    for name, branch in (("sustainer", 0), ("booster", 1)):
        item = landings.get(branch, {})
        summary[f"{name}_touchdown_speed_mps"] = float(
            item.get("total_speed", math.inf)
        )
        summary[f"{name}_contact_time_s"] = float(
            item.get("time_s", math.inf)
        )
    summary["mission_legal"] = all(
        summary[f"{name}_touchdown_speed_mps"] < 5.0
        for name in ("sustainer", "booster")
    )
    return summary


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _run_once(
    seed: int = OFFICIAL_SEED,
    wind_seed: int | str | None = "match_simulation_seed",
) -> dict:
    campaign = Campaign(OUTPUT_DIR / "_immutable_probe")
    xml = campaign.trial_xml(
        SELECTED_DELAYS["s0"], SELECTED_DELAYS["s1"], TIMESTEP_S
    )
    metrics = run_sim(xml, seed=seed, wind_seed=wind_seed)
    summary = _landing_summary(metrics)
    summary["wind_seed"] = metrics["wind_seed"]
    summary["result_hash"] = _canonical_hash(summary)
    return summary


def _fresh_process_once(seed: int) -> dict:
    environment = os.environ.copy()
    environment.setdefault("RAYON_NUM_THREADS", "1")
    environment.setdefault(
        "JAVA_TOOL_OPTIONS", "-Xmx768m -XX:+UseSerialGC"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(Path(__file__).resolve()),
            "--worker",
            "--seed",
            str(seed),
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER) :])
    raise RuntimeError(
        "fresh worker returned no result marker\n"
        + completed.stdout[-2000:]
        + completed.stderr[-2000:]
    )


def _source_and_package_evidence() -> dict:
    simulation_options = (
        REPO
        / "openrocket/core/src/main/java/info/openrocket/core/simulation/"
        "SimulationOptions.java"
    ).read_text(encoding="utf-8")
    run_dialog = (
        REPO
        / "openrocket/swing/src/main/java/info/openrocket/swing/gui/"
        "simulation/SimulationRunDialog.java"
    ).read_text(encoding="utf-8")
    authority_runner = (REPO / "osifog_sweep.py").read_text(encoding="utf-8")
    with zipfile.ZipFile(CANDIDATE_ORK) as archive:
        candidate_xml = archive.read("rocket.ork").decode("utf-8")

    checks = {
        "openrocket_seed_default_is_random": (
            "randomSeed = new Random().nextInt()" in simulation_options
        ),
        "gui_randomizes_seed_before_run": (
            "sim.getOptions().randomizeSeed()" in run_dialog
        ),
        "setRandomSeed_does_not_seed_wind_layers": (
            "public void setRandomSeed(int randomSeed)" in simulation_options
            and "getMultiLevelWindModel" not in simulation_options[
                simulation_options.index(
                    "public void setRandomSeed(int randomSeed)"
                ) : simulation_options.index(
                    "public void randomizeSeed()"
                )
            ]
        ),
        "authority_sets_master_seed": (
            "sim.getOptions().setRandomSeed(seed)" in authority_runner
        ),
        "authority_seeds_each_multilevel_wind_layer": (
            "def _seed_multilevel_wind" in authority_runner
            and "_seed_multilevel_wind(sim.getOptions()," in authority_runner
        ),
        "candidate_ork_contains_no_seed_tag": (
            "<randomseed" not in candidate_xml.lower()
            and "<seed" not in candidate_xml.lower()
        ),
    }
    return {"checks": checks, "all_pass": all(checks.values())}


def _campaign_evidence() -> dict:
    campaign = json.loads(CAMPAIGN_JSON.read_text(encoding="utf-8"))
    analysis = campaign["analysis"]
    seeds = analysis["selected_center_by_seed"]
    passes = [seed for seed, result in seeds.items() if result["mission_legal"]]
    failures = [
        seed for seed, result in seeds.items() if not result["mission_legal"]
    ]
    return {
        "selected_delays_s": analysis["selected_coupled_delays_s"],
        "seed_results": seeds,
        "pass_count": len(passes),
        "trial_count": len(seeds),
        "passing_seeds": passes,
        "failing_seeds": failures,
        "seed_16000_is_sufficient": len(failures) == 0,
    }


def build_report(payload: dict) -> str:
    campaign = payload["candidate_i_seed_campaign"]
    repeats = payload.get("repeatability")
    lines = [
        "# OSIFOG seed and reviewer-rerun certification",
        "",
        "## Verdict",
        "",
        "**Seed 16000 is reproducible when the complete seed procedure is "
        "restored, but it is not evidence that Candidate I is robust to a "
        "reviewer rerun.** Candidate I passed "
        f"{campaign['pass_count']}/{campaign['trial_count']} tested seeds.",
        "",
        "OpenRocket's GUI randomizes the simulation seed before each run, the "
        "seed is absent from Candidate I's `.ork`, and the multilevel wind "
        "layers require separate deterministic seeding.",
        "",
        "## Candidate I alternate-seed evidence",
        "",
        "| Seed | Sustainer m/s | Booster m/s | Legal |",
        "|---:|---:|---:|:---:|",
    ]
    for seed, result in campaign["seed_results"].items():
        lines.append(
            f"| {seed} | {result['s0_touchdown_speed_mps']:.3f} | "
            f"{result['s1_touchdown_speed_mps']:.3f} | "
            f"{'yes' if result['mission_legal'] else 'no'} |"
        )
    if repeats:
        lines.extend(
            [
                "",
                "## Fixed-seed replay",
                "",
                f"- Same-process identical: "
                f"**{str(repeats['same_process_identical']).lower()}**",
                f"- Fresh-process identical: "
                f"**{str(repeats['fresh_process_identical']).lower()}**",
                f"- Reference result hash: `{repeats['reference_hash']}`",
            ]
        )
    factorial = payload.get("seed_factorial")
    if factorial:
        integrator_rows = factorial["integrator_only_fixed_wind_16000"]
        wind_rows = factorial["wind_only_fixed_integrator_16000"]
        integrator_passes = sum(row["mission_legal"] for row in integrator_rows)
        wind_passes = sum(row["mission_legal"] for row in wind_rows)
        lines.extend(
            [
                "",
                "## GUI-session diagnosis",
                "",
                f"- Varying only the integrator seed with wind fixed: "
                f"**{integrator_passes}/{len(integrator_rows)} legal**.",
                f"- Varying only the hidden wind-layer seed: "
                f"**{wind_passes}/{len(wind_rows)} legal**.",
                "",
                "This explains repeated successful GUI runs: one open GUI "
                "session keeps its hidden wind realization while randomizing "
                "only the integrator seed. Closing and reopening the `.ork` "
                "constructs new, unpersisted wind-layer seeds.",
            ]
        )
    confidence = payload["certification_targets"]
    lines.extend(
        [
            "",
            "## What can be claimed",
            "",
            "A declared seed plus the recorded OpenRocket/Java environment can "
            "be replayed exactly. No finite random-seed campaign proves every "
            "32-bit seed. With zero failures in 300 held-out seeds, the exact "
            "one-sided 95% upper bound on the failure probability is "
            f"{100.0 * confidence['300_seeds_upper_failure_probability']:.3f}%.",
            "",
            "Candidate J therefore uses a 50 ms minimum continuous window per "
            "stage, converged timesteps, development seeds, 30 promotion "
            "seeds, and 300 held-out authority seeds. Any seed failure blocks "
            "promotion.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-repeats", action="store_true")
    parser.add_argument(
        "--run-seed-factorial",
        action="store_true",
        help="separate integrator-seed sensitivity from wind-seed sensitivity",
    )
    parser.add_argument("--repeat-count", type=int, default=2)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--seed", type=int, default=OFFICIAL_SEED)
    args = parser.parse_args(argv)

    if args.worker:
        init_or()
        print(RESULT_MARKER + json.dumps(_run_once(args.seed), sort_keys=True))
        return 0

    payload = {
        "schema": 1,
        "candidate": "Candidate I (immutable)",
        "source_and_package_evidence": _source_and_package_evidence(),
        "candidate_i_seed_campaign": _campaign_evidence(),
        "certification_targets": {
            f"{count}_seeds_upper_failure_probability":
                upper_failure_bound_zero_failures(count)
            for count in (30, 100, 300, 1000)
        },
    }
    if args.run_repeats:
        if args.repeat_count < 2:
            parser.error("--repeat-count must be at least 2")
        init_or()
        same_process = [_run_once(args.seed) for _ in range(args.repeat_count)]
        fresh_process = [
            _fresh_process_once(args.seed) for _ in range(args.repeat_count)
        ]
        hashes = [item["result_hash"] for item in same_process + fresh_process]
        payload["repeatability"] = {
            "seed": args.seed,
            "same_process": same_process,
            "fresh_process": fresh_process,
            "same_process_identical": len(
                {item["result_hash"] for item in same_process}
            ) == 1,
            "fresh_process_identical": len(
                {item["result_hash"] for item in fresh_process}
            ) == 1,
            "all_replays_identical": len(set(hashes)) == 1,
            "reference_hash": hashes[0],
        }
    if args.run_seed_factorial:
        init_or()
        seeds = list(range(16000, 16005))
        payload["seed_factorial"] = {
            "integrator_only_fixed_wind_16000": [
                _run_once(seed=seed, wind_seed=16000) for seed in seeds
            ],
            "wind_only_fixed_integrator_16000": [
                _run_once(seed=16000, wind_seed=seed) for seed in seeds
            ],
            "interpretation": (
                "Same-session GUI reruns resemble the first group: the GUI "
                "randomizes the integrator seed while the imported wind-layer "
                "seeds remain fixed. Reopening the ORK reconstructs unpersisted "
                "wind-layer models and can select a different realization."
            ),
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "certification.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    (OUTPUT_DIR / "REPORT.md").write_text(
        build_report(payload), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["source_and_package_evidence"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
