"""Deterministically search OpenRocket random seeds for a saved OSIFOG score.

This does not mutate geometry or the source candidate.  It runs the OpenRocket
authority model against one generated XML document, records every realization,
and checkpoints after each seed so an interrupted search can resume safely.
The winning seed still has to be packaged and independently reopened by the
normal submission pipeline.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import osifog_sweep as sweep
from osifog_direct_driver import BASE


def load_parameters(path: Path) -> dict:
    delta = json.loads(path.read_text(encoding="utf-8"))
    parameters = copy.deepcopy(BASE)
    parameters.update(delta)
    return parameters


def compact_row(seed: int, metrics: dict, official: dict, elapsed_s: float) -> dict:
    return {
        "seed": int(seed),
        "legal": bool(official.get("is_legal", False)),
        "score": float(official.get("raw_score", float("-inf"))),
        "apogee_m": float(metrics.get("apogee_m", float("nan"))),
        "mach": float(metrics.get("mach", float("nan"))),
        "s0_landing_speed": float(
            metrics.get("s0_landing_speed", float("nan"))
        ),
        "s1_landing_speed": float(
            metrics.get("s1_landing_speed", float("nan"))
        ),
        "elapsed_s": float(elapsed_s),
        "error": None,
    }


def save_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--rng-seed", type=int, default=20260726)
    parser.add_argument("--min-seed", type=int, default=1)
    parser.add_argument("--max-seed", type=int, default=2_147_483_647)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    if args.count < 1:
        raise ValueError("--count must be positive")
    if args.min_seed > args.max_seed:
        raise ValueError("--min-seed must not exceed --max-seed")

    candidate_path = args.candidate.resolve()
    checkpoint_path = args.checkpoint.resolve()
    parameters = load_parameters(candidate_path)
    xml = sweep.generate_ork(parameters)

    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        rows = [
            row for row in state.get("rows", [])
            if not row.get("error")
        ]
    else:
        rows = []
    completed = {int(row["seed"]) for row in rows}

    rng = random.Random(args.rng_seed)
    requested = []
    while len(requested) < args.count:
        seed = rng.randint(args.min_seed, args.max_seed)
        if seed not in requested:
            requested.append(seed)

    sweep.init_or()
    started = time.perf_counter()
    for index, seed in enumerate(requested, start=1):
        if seed in completed:
            continue
        run_started = time.perf_counter()
        try:
            metrics = sweep.run_sim(xml, seed=seed)
            official = sweep.score_official(metrics, parameters)
            row = compact_row(
                seed,
                metrics,
                official,
                time.perf_counter() - run_started,
            )
        except Exception as exc:  # Keep the search resumable and auditable.
            row = {
                "seed": int(seed),
                "legal": False,
                "score": float("-inf"),
                "apogee_m": float("nan"),
                "mach": float("nan"),
                "s0_landing_speed": float("nan"),
                "s1_landing_speed": float("nan"),
                "elapsed_s": time.perf_counter() - run_started,
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(row)
        completed.add(seed)
        legal_rows = [item for item in rows if item.get("legal")]
        best = max(legal_rows, key=lambda item: item["score"], default=None)
        state = {
            "candidate": str(candidate_path),
            "rng_seed": args.rng_seed,
            "requested_count": args.count,
            "completed_count": len(completed.intersection(requested)),
            "legal_count": len(legal_rows),
            "best": best,
            "rows": rows,
        }
        save_checkpoint(checkpoint_path, state)
        if index % args.progress_every == 0 or index == len(requested):
            best_label = (
                f"seed={best['seed']} score={best['score']:.3f}"
                if best else "none"
            )
            print(
                f"[{index}/{len(requested)}] legal={len(legal_rows)} "
                f"best={best_label}",
                flush=True,
            )

    elapsed = time.perf_counter() - started
    print(f"completed in {elapsed:.1f}s")
    if state["best"] is None:
        print("no legal seed found")
        return 1
    print(json.dumps(state["best"], indent=2, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
