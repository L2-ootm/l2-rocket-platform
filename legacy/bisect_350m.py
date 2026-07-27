"""
Precision bisection: find exact ballast that gives headless apogee = 351.0m
(which maps to exactly 350m in OpenRocket GUI 24.12 due to ~1m CG bias).

Uses the OR session directly — single JVM, multiple evaluations.
Champion genome from the last GA run is used as the base; only s0_ballast varies.
"""
import sys, json, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from l2_hyper.mission import load_mission
from l2_hyper.orkit import OpenRocketSession
from l2_hyper.generator import build_rocket_xml, save_ork

TARGET_APOGEE = 350.0  # headless target = GUI target (windturbulence=0.0 removes offset)
TOLERANCE     = 0.0001 # stop when within 0.1mm of target
MAX_ITERS     = 60

MISSION_PATH = "missions/precision_350m.json"
OUT_PATH     = "designs/optimized/precision_350m.ork"

# Champion genome — only ballast will change
BASE_GENOME = {
    "s0_span":     0.0688,
    "s0_root":     0.1640,
    "s0_nose_len": 0.2632,
    "sep_delay":   0.4377,
}

# 0.78115 kg → 351m headless, need slightly more ballast for 350m
LO_BALLAST = 0.781   # gives apogee > 350m
HI_BALLAST = 0.790   # gives apogee < 350m

mission = load_mission(MISSION_PATH)

def evaluate(session, motors, ballast):
    genome = {**BASE_GENOME, "s0_ballast": ballast}
    metrics = session.evaluate(mission, genome, motors)
    return float(metrics["apogee"]), genome, metrics

print(f"Target headless apogee: {TARGET_APOGEE:.7f} m  (→ ~350m in GUI 24.12)")
print(f"Bisection ballast range: [{LO_BALLAST}, {HI_BALLAST}] kg")
print(f"Tolerance: {TOLERANCE*100:.1f} cm")
print()

with OpenRocketSession() as session:
    motors = session.resolve_motors(mission["stack"])
    print(f"Motor: {motors[0]['designation']}  digest={motors[0]['digest'][:12]}")
    print()

    # Verify direction: lo_ballast should give apogee > TARGET, hi_ballast < TARGET
    lo_apogee, _, _ = evaluate(session, motors, LO_BALLAST)
    hi_apogee, _, _ = evaluate(session, motors, HI_BALLAST)
    print(f"Bound check: ballast={LO_BALLAST}kg → apogee={lo_apogee:.7f}m")
    print(f"Bound check: ballast={HI_BALLAST}kg → apogee={hi_apogee:.7f}m")

    if lo_apogee < TARGET_APOGEE:
        print(f"ERROR: lo_ballast={LO_BALLAST} already gives {lo_apogee:.3f}m < {TARGET_APOGEE}m")
        print("Widen the bisection range — decrease LO_BALLAST")
        sys.exit(1)
    if hi_apogee > TARGET_APOGEE:
        print(f"ERROR: hi_ballast={HI_BALLAST} gives {hi_apogee:.3f}m > {TARGET_APOGEE}m")
        print("Widen the bisection range — increase HI_BALLAST")
        sys.exit(1)

    print()
    print(f"{'Iter':>4}  {'Ballast (kg)':>14}  {'Apogee (m)':>14}  {'Error (m)':>10}")
    print("-" * 52)

    lo, hi = LO_BALLAST, HI_BALLAST
    best_genome, best_metrics, best_apogee = None, None, None

    for i in range(MAX_ITERS):
        mid = (lo + hi) / 2.0
        apogee, genome, metrics = evaluate(session, motors, mid)
        err = apogee - TARGET_APOGEE
        print(f"{i+1:>4}  {mid:>14.7f}  {apogee:>14.7f}  {err:>+10.7f}")

        # Track best
        if best_apogee is None or abs(apogee - TARGET_APOGEE) < abs(best_apogee - TARGET_APOGEE):
            best_apogee, best_genome, best_metrics = apogee, genome, metrics

        if abs(err) <= TOLERANCE:
            print(f"\n✓ Converged in {i+1} iterations!")
            break

        if err > 0:  # apogee too high → more ballast needed
            lo = mid
        else:        # apogee too low → less ballast needed
            hi = mid

    print()
    print("=" * 64)
    print(f"FINAL RESULT")
    print(f"  Ballast:   {best_genome['s0_ballast']:.7f} kg")
    print(f"  Headless apogee: {best_apogee:.7f} m  (target: {TARGET_APOGEE:.7f} m)")
    print(f"  Error from target: {best_apogee - TARGET_APOGEE:+.7f} m")
    print(f"  GUI 24.12 expected: ~{best_apogee - 1.0:.3f} m  (apply -1m correction)")
    print(f"  Mach: {best_metrics['mach']:.4f} | margin: {best_metrics['min_static_margin']:+.4f} cal")
    print(f"  Flight time: {best_metrics['flight_time']:.1f} s")
    print()

    # Save the final .ork
    import uuid
    fcid = str(uuid.uuid4())
    from l2_hyper.generator import save_ork
    xml = build_rocket_xml(mission, best_genome, motors, fcid)
    save_ork(xml, OUT_PATH)
    print(f"  Saved: {OUT_PATH}")

    # Print champion genome for mission seeds
    print()
    print("Champion genome (paste into mission seeds):")
    print(json.dumps(best_genome, indent=4))
