"""Mission runner CLI.

    python -m l2_hyper.run_mission missions/karman_m6.json --validate
    python -m l2_hyper.run_mission missions/karman_m6.json --pop 16 --gens 4

Validate mode evaluates the first mission seed and saves the .ork. GA mode
evolves from the mission seeds (or random population) and saves the best.
"""

import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .mission import load_mission, compile_fitness, targets_met
from .genome import build_bounds, clamp
from .ga import evolve
from .orkit import OpenRocketSession


def _report(mission, genome, metrics, out_path):
    print("=" * 64)
    print(f"MISSION: {mission.get('name')}")
    print(f"  apogee {metrics['apogee']/1000:.2f} km | Mach {metrics['mach']:.2f} | "
          f"vmax {metrics['vmax']:.1f} m/s | flight {metrics['flight_time']:.0f} s")
    print(f"  tumbled={metrics['tumbled']} late_ignition={metrics['late_ignition']}")
    margins = " | ".join(f"{k}: {v:+.2f} cal" for k, v in metrics["static_margins"].items())
    print(f"  static margins: {margins}")
    if metrics.get("warnings"):
        print("  simulation warnings:")
        for w in metrics["warnings"]:
            print(f"    - {w}")
    print(f"  genome: {json.dumps({k: round(v, 4) for k, v in genome.items()})}")
    print(f"  saved: {out_path}")
    print(f"  TARGETS {'MET' if targets_met(mission, metrics) else 'NOT MET'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mission")
    ap.add_argument("--validate", action="store_true", help="evaluate first seed only")
    ap.add_argument("--pop", type=int, default=16)
    ap.add_argument("--gens", type=int, default=4)
    ap.add_argument("--seed-file", help="JSON list of genomes (e.g. Rust elite.json)")
    ap.add_argument("--out", help="output .ork path (default: mission['output'])")
    args = ap.parse_args()

    mission = load_mission(args.mission)
    bounds = build_bounds(mission)
    fitness = compile_fitness(mission)
    out_path = args.out or mission.get("output", "designs/optimized/mission_best.ork")

    seeds = list(mission.get("seeds", []))
    if args.seed_file:
        with open(args.seed_file, encoding="utf-8") as f:
            payload = json.load(f)
        file_seeds = [e["genome"] if isinstance(e, dict) and "genome" in e else e
                      for e in (payload.get("elite", payload) if isinstance(payload, dict) else payload)]
        # file elite first (exploration source), mission seeds kept as the
        # known-good stable attractor for the GA to cross against
        seeds = file_seeds + seeds
    seeds = [clamp(g, bounds) for g in seeds]
    # never let seeds fill the whole population — diversity slots are what
    # allow the GA to escape a homogeneous (or flawed) elite basin
    max_seeds = max(2, args.pop // 2)
    if len(seeds) > max_seeds:
        seeds = seeds[: max_seeds - 1] + [seeds[-1]]

    with OpenRocketSession() as session:
        motors = session.resolve_motors(mission["stack"])
        print("[*] motors resolved:")
        for m in motors:
            print(f"    {m['designation']} ({m['impulse']/1000:.1f} kNs) digest={m['digest']}")

        if args.validate:
            if not seeds:
                raise SystemExit("--validate needs mission['seeds'] or --seed-file")
            metrics = session.evaluate(mission, seeds[0], motors, keep_path=out_path)
            _report(mission, seeds[0], metrics, out_path)
            return

        def eval_fn(genome):
            # fault isolation: one broken candidate must never kill the run
            try:
                metrics = session.evaluate(mission, genome, motors)
            except Exception as e:
                print(f"  [eval-fail] {repr(e)[:120]}")
                return float("-inf"), dict(apogee=0, mach=0, vmax=0, flight_time=0,
                                           tumbled=True, late_ignition=True,
                                           static_margins={}, min_static_margin=-99,
                                           events=[], warnings=[])
            return fitness(metrics), metrics

        def on_candidate(gen, score, genome, metrics):
            flag = " <<< TARGET" if targets_met(mission, metrics) else ""
            notes = ("TUMBLE " if metrics["tumbled"] else "") + \
                    ("LATE-IGN " if metrics["late_ignition"] else "")
            print(f"  [g{gen}] score {score:6.3f} | apogee {metrics['apogee']/1000:8.2f} km | "
                  f"Mach {metrics['mach']:5.2f} | margin {metrics['min_static_margin']:+.2f} {notes}{flag}")

        print(f"[*] GA: pop {args.pop}, {args.gens} generations, {len(seeds)} seeds")
        _, best_genome, _, history = evolve(
            eval_fn, bounds, seeds=seeds, pop_size=args.pop,
            generations=args.gens, on_candidate=on_candidate)

        print("[*] final validation of best genome")
        metrics = session.evaluate(mission, best_genome, motors, keep_path=out_path)
        _report(mission, best_genome, metrics, out_path)
        for h in history:
            print(f"    gen {h['gen']}: best {h['best']:.3f} mean {h['mean']:.3f}")


if __name__ == "__main__":
    main()
