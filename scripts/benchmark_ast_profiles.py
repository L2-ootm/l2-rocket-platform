#!/usr/bin/env python3
"""Reproducible throughput/fidelity benchmark for AST execution profiles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from organic_loop import run_rust_evaluator  # noqa: E402


CORPUS_SOURCES = (
    ROOT / "designs/precision_16k_campaign/organic_elite.json",
    ROOT / "designs/anomaly_200km/organic_elite.json",
    ROOT / "designs/push_limits/organic_elite.json",
    ROOT / "designs/engine05_validation/organic_elite.json",
)


def load_corpus(size: int) -> tuple[list[dict], str, dict[str, int]]:
    groups: list[tuple[str, list[list[dict]]]] = []
    for source in CORPUS_SOURCES:
        payload = json.loads(source.read_text(encoding="utf-8"))
        asts = [item["ast"] for item in payload.get("elite", []) if item.get("ast")]
        if not asts:
            raise RuntimeError(f"no AST elites in {source}")
        groups.append((source.parent.name, asts))

    candidates = []
    histogram: dict[str, int] = {}
    for index in range(size):
        group_name, asts = groups[index % len(groups)]
        ast = asts[(index // len(groups)) % len(asts)]
        histogram[group_name] = histogram.get(group_name, 0) + 1
        candidates.append(
            {
                "id": f"{group_name}-{index:04d}",
                "ast": ast,
                "signature": "",
            }
        )
    encoded = json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
    return candidates, hashlib.sha256(encoded).hexdigest(), histogram


def checksum(results) -> str:
    rows = [
        (
            item.id,
            item.status,
            round(item.score, 10),
            round(item.apogee_m, 10),
            round(item.mach, 10),
            item.reason,
        )
        for item in results
    ]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def rank_map(rows: list) -> dict[str, int]:
    return {
        row.id: rank
        for rank, row in enumerate(sorted(rows, key=lambda item: item.score, reverse=True))
    }


def spearman(left: list, right: list) -> float:
    left_ranks = rank_map(left)
    right_ranks = rank_map(right)
    ids = sorted(set(left_ranks) & set(right_ranks))
    if len(ids) < 2:
        return float("nan")
    count = len(ids)
    squared = sum((left_ranks[item] - right_ranks[item]) ** 2 for item in ids)
    return 1.0 - 6.0 * squared / (count * (count * count - 1))


def comparison(candidate_results: list, reference_results: list) -> dict:
    candidate_by_id = {item.id: item for item in candidate_results}
    reference_by_id = {item.id: item for item in reference_results}
    common = [
        reference_by_id[item]
        for item in sorted(set(candidate_by_id) & set(reference_by_id))
        if candidate_by_id[item].status == "success"
        and reference_by_id[item].status == "success"
        and reference_by_id[item].apogee_m > 0.0
    ]
    candidate_common = [candidate_by_id[item.id] for item in common]
    apogee_errors = [
        abs(candidate.apogee_m - reference.apogee_m) / reference.apogee_m
        for candidate, reference in zip(candidate_common, common)
    ]
    mach_errors = [
        abs(candidate.mach - reference.mach)
        for candidate, reference in zip(candidate_common, common)
    ]
    top_count = min(10, len(common))
    candidate_top = {
        item.id for item in sorted(candidate_common, key=lambda row: row.score, reverse=True)[:top_count]
    }
    reference_top = {
        item.id for item in sorted(common, key=lambda row: row.score, reverse=True)[:top_count]
    }
    return {
        "common_successes": len(common),
        "spearman_score_rank": spearman(candidate_common, common),
        "top_10_recall": len(candidate_top & reference_top) / top_count if top_count else 0.0,
        "median_apogee_relative_error": statistics.median(apogee_errors)
        if apogee_errors
        else float("nan"),
        "p95_apogee_relative_error": percentile(apogee_errors, 0.95),
        "median_mach_absolute_error": statistics.median(mach_errors)
        if mach_errors
        else float("nan"),
        "p95_mach_absolute_error": percentile(mach_errors, 0.95),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["super-speed"],
        choices=["super-speed", "balanced", "authority-heavy"],
    )
    args = parser.parse_args()
    os.environ["RAYON_NUM_THREADS"] = str(args.threads)

    mission = json.loads(
        (ROOT / "missions/precision_16k_m3_organic.json").read_text(encoding="utf-8")
    )
    candidates, corpus_sha256, groups = load_corpus(args.size)
    report = {
        "corpus_sha256": corpus_sha256,
        "size": args.size,
        "groups": groups,
        "threads": args.threads,
        "profiles": {},
        "comparisons": {},
    }
    final_results = {}

    for profile in args.profiles:
        samples = []
        result_checksum = None
        status_histogram = None
        for _ in range(args.repetitions):
            started = time.perf_counter()
            results = run_rust_evaluator(
                candidates,
                16_000.0,
                "openrocket",
                mission["objectives"],
                mission["constraints"],
                {},
                execution_profile=profile,
            )
            elapsed = time.perf_counter() - started
            current_checksum = checksum(results)
            if result_checksum is not None and current_checksum != result_checksum:
                raise RuntimeError(f"non-deterministic results for {profile}")
            result_checksum = current_checksum
            status_histogram = {}
            for result in results:
                status_histogram[result.status] = status_histogram.get(result.status, 0) + 1
            samples.append(
                {"seconds": elapsed, "simulations_per_second": len(results) / elapsed}
            )
            final_results[profile] = results
        throughputs = [sample["simulations_per_second"] for sample in samples]
        report["profiles"][profile] = {
            "samples": samples,
            "median_simulations_per_second": statistics.median(throughputs),
            "minimum_simulations_per_second": min(throughputs),
            "result_checksum": result_checksum,
            "status_histogram": status_histogram,
        }

    if "super-speed" in final_results and "balanced" in final_results:
        report["comparisons"]["super-speed_vs_balanced"] = comparison(
            final_results["super-speed"], final_results["balanced"]
        )
    if "authority-heavy" in final_results:
        for profile, results in final_results.items():
            if profile == "authority-heavy":
                continue
            report["comparisons"][f"{profile}_vs_authority-heavy"] = comparison(
                results, final_results["authority-heavy"]
            )

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
