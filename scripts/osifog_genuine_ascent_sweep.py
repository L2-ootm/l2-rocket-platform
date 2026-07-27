#!/usr/bin/env python3
"""OpenRocket-authority motor sweep for genuine OSIFOG ascent staging."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import osifog_precision as precision
import osifog_sweep as sweep


MAIN_MOTOR_INDICES = tuple(range(5, 17)) + (18, 36, 37)


def event_before(metrics, first: str, second: str) -> bool:
    events = metrics.get("event_times", {})
    return bool(events.get(first)) and bool(events.get(second)) and (
        min(events[first]) < min(events[second])
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="output/osifog/genuine_ascent_sweep.json",
    )
    args = parser.parse_args()

    sweep.init_or()
    base = precision.falcon_submission_candidate()
    base.update(
        s1_separation_delay=0.0,
        s0_retro_delay=200.0,
        s1_retro_delay=200.0,
        s0_fin_count=4,
        s0_fin_root=0.20,
        s0_fin_height=0.25,
        s0_fin_sweep=10.0,
    )
    results = []
    total = len(MAIN_MOTOR_INDICES) ** 2
    completed = 0
    for sustainer_main in MAIN_MOTOR_INDICES:
        for booster_main in MAIN_MOTOR_INDICES:
            completed += 1
            candidate = dict(
                base,
                s0_main=sustainer_main,
                s1_main=booster_main,
            )
            try:
                metrics = sweep.run_sim(sweep.generate_ork(candidate))
                genuine = event_before(
                    metrics, "STAGE_SEPARATION", "APOGEE"
                )
                ascent_legal = (
                    genuine
                    and float(metrics.get("mach", 99.0)) < sweep.MAX_MACH
                    and float(metrics.get("min_static_margin", -99.0))
                    >= sweep.MIN_STATIC_MARGIN
                )
                result = {
                    "s0_main": sustainer_main,
                    "s0_designation": sweep.MOTOR_DATABASE[sustainer_main][1],
                    "s1_main": booster_main,
                    "s1_designation": sweep.MOTOR_DATABASE[booster_main][1],
                    "apogee_m": metrics.get("apogee_m"),
                    "mach": metrics.get("mach"),
                    "min_static_margin": metrics.get("min_static_margin"),
                    "event_times": metrics.get("event_times", {}),
                    "ascent_legal": ascent_legal,
                }
            except Exception as exc:
                result = {
                    "s0_main": sustainer_main,
                    "s1_main": booster_main,
                    "ascent_legal": False,
                    "error": str(exc),
                }
            results.append(result)
            print(
                f"[{completed}/{total}] "
                f"{result.get('s0_designation', sustainer_main)}/"
                f"{result.get('s1_designation', booster_main)} "
                f"h={result.get('apogee_m')} M={result.get('mach')} "
                f"SM={result.get('min_static_margin')} "
                f"legal={result['ascent_legal']}",
                flush=True,
            )

    results.sort(
        key=lambda item: (
            not item["ascent_legal"],
            abs(float(item.get("apogee_m", 0.0)) - sweep.TARGET_APOGEE),
            max(0.0, float(item.get("mach", 99.0)) - sweep.MAX_MACH),
            -float(item.get("min_static_margin", -99.0)),
        )
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"saved {output}", flush=True)
    print(json.dumps(results[:10], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
