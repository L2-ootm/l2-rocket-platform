"""Long-running, resumable, crash-hardened OSIFOG GA campaign wrapping
`organic_loop.run_generation`.

Idempotency/antifragility design (see `.claude/skills/l2-idempotency-
antifragility-review`):

- Canonical identity: one campaign = one `--out` directory. A
  `campaign.lease.json` (crash-recoverable, PID-checked) makes concurrent
  double-launch fail fast instead of corrupting shared state
  (`campaign_infra.campaign_lease`).
- Resumability: `organic_loop.export_elites` already writes
  `organic_elite.json` in the exact shape `OrganicLoopConfig.seed_from`
  reads back (`{"elite": [...]}"`), after *every* generation. On (re)start,
  this campaign auto-detects that checkpoint and resumes from it instead of
  reseeding from scratch -- a kill -9 mid-generation loses at most the
  generation in flight, never the whole campaign. Cumulative progress
  (generations run, cycles completed, wall-clock spent) is tracked
  separately in `campaign-progress.json` since each `run_generation` call's
  internal generation counter restarts at 0.
- Partial-failure containment: each cycle (a bounded batch of generations)
  runs inside a try/except. A transient failure (Rust eval crash, OpenRocket
  JVM hiccup during calibration) is retried with a fresh seed, not treated
  as fatal; `--max-consecutive-failures` (default 3) escalates to a
  terminal "blocked" state with a recovery artifact (`alert.json`) rather
  than looping forever or silently going quiet.
- Recovery artifacts: `alert.json` (latest issue), `events.jsonl`
  (append-only history of every cycle/failure/promotion), `health.json`
  (liveness heartbeat consumed by `osifog_campaign_watchdog.py`),
  `campaign-state.json` (`status` in {"running","degraded","goal_reached",
  "budget_exhausted","blocked"} -- the watchdog's TERMINAL_STATES contract).
- Rich, self-updating monitoring: `best-candidate.json` after every single
  generation (not just every cycle) with the current best candidate's full
  metrics AND a term-by-term official-formula breakdown
  (`organic_loop.official_score_breakdown`), plus population-level stats
  (legality rate, score distribution) for that generation -- not just a
  bare score number.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from campaign_infra import (
    append_event,
    atomic_json,
    campaign_lease,
    canonical_digest,
    now_iso,
    read_json,
    sha256_file,
    write_health,
)
from organic_loop import (
    OrganicLoopConfig,
    load_mission_data,
    load_mission_target_apogee,
    official_score_breakdown,
    run_generation,
)

TERMINAL_STATES = {"goal_reached", "budget_exhausted", "blocked"}

SOURCE_FILES = [
    "organic_loop.py",
    "organic_campaign.py",
    "campaign_infra.py",
    "rocket_ast.py",
    "ckg_memory.py",
    "l2_engine/target/release/ast_eval.exe",
]


def _build_manifest(args) -> dict:
    sources = {
        path: sha256_file(Path(path))
        for path in SOURCE_FILES
        if Path(path).exists() and Path(path).is_file()
    }
    sources[str(args.mission)] = sha256_file(Path(args.mission))
    body = {
        "schema": 1,
        "mission": str(args.mission),
        "population": args.population,
        "elite_count": args.elite_count,
        "generations_per_cycle": args.generations_per_cycle,
        "execution_profile": args.execution_profile,
        "target_score": args.target_score,
        "seed": args.seed,
        "sources": sources,
    }
    return {**body, "campaign_id": canonical_digest(body)}


def _candidate_summary(candidate, mission_scoring):
    breakdown = official_score_breakdown(candidate, mission_scoring)
    return {
        "status": candidate.status,
        "reason": candidate.reason,
        "selection_score": candidate.score,
        "raw_score": candidate.raw_score,
        "apogee_m": candidate.rust_apogee_m,
        "apogee_east_m": candidate.rust_apogee_east_m,
        "apogee_north_m": candidate.rust_apogee_north_m,
        "mach": candidate.rust_mach,
        "min_static_margin": candidate.rust_min_static_margin,
        "total_prop_mass_kg": candidate.rust_total_prop_mass_kg,
        "stage_landings": candidate.rust_stage_landings or [],
        "official_score_breakdown": breakdown,
        "ast": [n.to_dict() for n in candidate.ast],
    }


def _population_stats(evaluated):
    scores = [c.score for c in evaluated]
    n_success = sum(1 for c in evaluated if c.status == "success")
    n_landed = sum(1 for c in evaluated if c.rust_stage_landings)
    reasons = {}
    for c in evaluated:
        key = c.reason.split(" ")[0] if c.reason else "unknown"
        reasons[key] = reasons.get(key, 0) + 1
    return {
        "population_size": len(evaluated),
        "n_success": n_success,
        "n_reached_landing_phase": n_landed,
        "legality_rate": n_success / len(evaluated) if evaluated else 0.0,
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_median": statistics.median(scores) if scores else None,
        "failure_reason_histogram": dict(
            sorted(reasons.items(), key=lambda kv: -kv[1])[:10]
        ),
    }


def _make_progress_callback(root, mission_scoring, progress, target_score):
    """Returns a callback invoked by `run_generation` after every single
    generation (not just every cycle) -- this is what gives the monitoring
    files per-generation granularity instead of per-cycle."""

    def _callback(generation_index, evaluated):
        progress["cumulative_generations"] += 1
        best = evaluated[0] if evaluated else None
        best_summary = _candidate_summary(best, mission_scoring) if best else None
        pop_stats = _population_stats(evaluated)

        goal_reached = bool(
            best_summary
            and best_summary["status"] == "success"
            and best_summary["official_score_breakdown"]["complete"]
            and best_summary["official_score_breakdown"]["computed_score"] >= target_score
        )

        # Tracked so run_campaign can detect a stagnant population (median
        # score approx. equal to max score, nothing legal) at cycle
        # boundaries and force a cold restart -- see stagnation handling
        # below and .planning/ultra/ULTRAREVIEW-osifog-campaign-height-
        # stall.md. Only the last generation's numbers matter for that
        # decision, so plain overwrite (not accumulation) is correct here.
        progress["last_legality_rate"] = pop_stats["legality_rate"]
        progress["last_score_ratio"] = (
            (pop_stats["score_median"] / pop_stats["score_max"])
            if pop_stats["score_max"]
            else None
        )

        atomic_json(
            root / "best-candidate.json",
            {
                "updated_at": now_iso(),
                "cumulative_generations": progress["cumulative_generations"],
                "cycle": progress["cycles_completed"] + 1,
                "generation_in_cycle": generation_index,
                "goal_reached": goal_reached,
                "target_score": target_score,
                "best": best_summary,
                "population_stats": pop_stats,
            },
        )
        write_health(
            root,
            "running",
            "evaluating",
            cumulative_generations=progress["cumulative_generations"],
            cycle=progress["cycles_completed"] + 1,
            best_status=best_summary["status"] if best_summary else None,
            best_official_score=(
                best_summary["official_score_breakdown"]["computed_score"]
                if best_summary and best_summary["official_score_breakdown"]["complete"]
                else None
            ),
            best_official_score_display=(
                best_summary["official_score_breakdown"]["computed_score_display"]
                if best_summary and best_summary["official_score_breakdown"]["complete"]
                else None
            ),
            legality_rate=pop_stats["legality_rate"],
        )
        if goal_reached:
            progress["goal_reached"] = True

    return _callback


def _apply_stagnation_guard(progress, args) -> bool:
    """Updates `progress["stagnant_cycles"]` in place from the just-completed
    cycle's `last_legality_rate`/`last_score_ratio` (set by the progress
    callback above) and returns True exactly when the caller should drop the
    checkpoint and cold-restart from pure random for the next cycle.

    The checkpoint's top `elite_count` slots are unconditionally re-seeded
    into every future cycle (`organic_loop.run_generation`), so once a
    population collapses onto one boundary-hugging genome (median score
    approx. equal to max score, nothing legal), that genome's lineage wins
    every generation forever and the campaign can never recover on its own
    -- confirmed for real in
    .planning/ultra/ULTRAREVIEW-osifog-campaign-height-stall.md (0%
    legality across 253 cycles / 1265 generations in that exact campaign).
    After `stagnation_cycles` consecutive stagnant cycles, dropping the
    checkpoint for exactly one cycle gives the next population 100% fresh
    random genomes with no incumbent to lose to; organic_elite.json is
    overwritten with that cycle's real results either way, so this can
    never lose a genuinely legal candidate (`selection_rank` always ranks
    legal above illegal regardless of score)."""
    is_stagnant = (
        progress.get("last_legality_rate") == 0.0
        and progress.get("last_score_ratio") is not None
        and progress["last_score_ratio"] >= args.stagnation_score_ratio
    )
    progress["stagnant_cycles"] = (
        progress.get("stagnant_cycles", 0) + 1 if is_stagnant else 0
    )
    if args.stagnation_cycles > 0 and progress["stagnant_cycles"] >= args.stagnation_cycles:
        progress["stagnant_cycles"] = 0
        return True
    return False


def run_campaign(args) -> int:
    root = Path(args.out)
    with campaign_lease(root):
        manifest = _build_manifest(args)
        atomic_json(root / "campaign-manifest.json", manifest)
        append_event(root, "campaign_started", campaign_id=manifest["campaign_id"], pid=None)

        progress = read_json(root / "campaign-progress.json")
        progress.setdefault("cumulative_generations", 0)
        progress.setdefault("cycles_completed", 0)
        progress.setdefault("goal_reached", False)
        progress.setdefault("started_at", now_iso())
        progress.setdefault("stagnant_cycles", 0)

        mission_data = load_mission_data(args.mission)
        mission_scoring = mission_data.get("scoring", {})
        target_apogee_m = load_mission_target_apogee(args.mission)

        elite_checkpoint = root / "organic_elite.json"
        seed_from = elite_checkpoint if elite_checkpoint.exists() else args.seed_from
        if elite_checkpoint.exists():
            append_event(root, "resumed_from_checkpoint", path=str(elite_checkpoint))

        consecutive_failures = 0
        start_time = time.time()
        atomic_json(root / "campaign-state.json", {"status": "running", "updated_at": now_iso()})
        write_health(root, "running", "starting", cumulative_generations=progress["cumulative_generations"])

        try:
            while True:
                if progress["goal_reached"]:
                    _terminal(root, progress, "goal_reached", "target score reached")
                    return 0
                if args.max_cycles and progress["cycles_completed"] >= args.max_cycles:
                    _terminal(root, progress, "budget_exhausted", "max_cycles reached")
                    return 0
                elapsed_hours = (time.time() - start_time) / 3600.0
                if args.max_hours and elapsed_hours >= args.max_hours:
                    _terminal(root, progress, "budget_exhausted", "max_hours reached")
                    return 0

                cycle = progress["cycles_completed"] + 1
                attempt = 0
                while True:
                    attempt += 1
                    write_health(
                        root, "running", "cycle_start", cycle=cycle, attempt=attempt,
                        consecutive_failures=consecutive_failures,
                        cumulative_generations=progress["cumulative_generations"],
                    )
                    try:
                        config = OrganicLoopConfig(
                            population=args.population,
                            elite_count=args.elite_count,
                            generations=args.generations_per_cycle,
                            seed=args.seed + cycle + attempt * 1_000_003,
                            target_apogee_m=target_apogee_m,
                            mission_path=Path(args.mission),
                            output_dir=root,
                            ckg_path=Path(args.ckg),
                            evaluator="rust",
                            physics_mode="openrocket",
                            constraints=mission_data.get("constraints", {}),
                            objectives=mission_data.get("objectives", []),
                            execution_profile=args.execution_profile,
                            calibrate_every=args.calibrate_every,
                            validate_openrocket=args.validate_openrocket,
                            seed_from=seed_from,
                            progress_callback=_make_progress_callback(
                                root, mission_scoring, progress, args.target_score
                            ),
                        )
                        run_generation(config)
                        consecutive_failures = 0
                        break
                    except Exception as exc:  # noqa: BLE001 - contain, don't crash the campaign
                        consecutive_failures += 1
                        alert = {
                            "status": "retrying" if consecutive_failures < args.max_consecutive_failures else "blocked",
                            "updated_at": now_iso(),
                            "cycle": cycle,
                            "attempt": attempt,
                            "consecutive_failures": consecutive_failures,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                        atomic_json(root / "alert.json", alert)
                        append_event(root, "cycle_failed", **alert)
                        write_health(root, "degraded", "retrying", alert=alert)
                        if consecutive_failures >= args.max_consecutive_failures:
                            _terminal(
                                root, progress, "blocked",
                                f"exceeded {args.max_consecutive_failures} consecutive failures; see alert.json",
                            )
                            return 2
                        time.sleep(min(30.0, 2.0 ** attempt))

                progress["cycles_completed"] = cycle

                cold_restart = _apply_stagnation_guard(progress, args)
                if cold_restart:
                    append_event(
                        root, "stagnation_cold_restart", cycle=cycle,
                        stagnant_cycles=args.stagnation_cycles,
                    )
                    seed_from = None
                else:
                    seed_from = elite_checkpoint

                atomic_json(root / "campaign-progress.json", progress)
                append_event(
                    root, "cycle_completed", cycle=cycle,
                    cumulative_generations=progress["cumulative_generations"],
                )
                atomic_json(root / "alert.json", {
                    "status": "clear", "updated_at": now_iso(), "cycle": cycle,
                    "message": "latest cycle completed successfully",
                })
        finally:
            atomic_json(root / "campaign-progress.json", progress)


def _terminal(root, progress, status, message):
    atomic_json(root / "campaign-progress.json", progress)
    atomic_json(root / "campaign-state.json", {"status": status, "updated_at": now_iso(), "message": message})
    write_health(root, status, "finished", cumulative_generations=progress["cumulative_generations"])
    append_event(root, "campaign_terminal", status=status, message=message)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--population", type=int, default=64)
    parser.add_argument("--elite-count", type=int, default=8)
    parser.add_argument("--generations-per-cycle", type=int, default=5)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 = unbounded")
    parser.add_argument("--max-hours", type=float, default=0.0, help="0 = unbounded")
    parser.add_argument("--target-score", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--execution-profile", choices=["super-speed", "balanced", "authority-heavy"], default="balanced")
    parser.add_argument("--calibrate-every", type=int, default=10)
    parser.add_argument("--validate-openrocket", type=int, default=4)
    parser.add_argument("--ckg", type=Path, default=None, help="default: <out>/campaign_ckg.json (never the shared global default)")
    parser.add_argument("--seed-from", type=Path, default=None)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument(
        "--stagnation-cycles", type=int, default=20,
        help="consecutive stagnant cycles (0%% legality, median approx. max score) "
        "before dropping the checkpoint for one cycle to force fresh random "
        "exploration; 0 disables the guard",
    )
    parser.add_argument(
        "--stagnation-score-ratio", type=float, default=0.9,
        help="population is considered collapsed when score_median/score_max "
        "is at or above this ratio",
    )
    args = parser.parse_args(argv)
    if args.ckg is None:
        args.ckg = args.out / "campaign_ckg.json"
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    return run_campaign(args)


if __name__ == "__main__":
    raise SystemExit(main())
