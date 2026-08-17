"""Wide-seed re-optimisation of Candidate J's two retro ignition delays.

Same method as `delay_pass_rate_optimizer.py` -- pick delays that maximise the
measured fraction of wind/integrator seeds landing under 5 m/s, not the depth
of any single seed's basin -- but run against 40 seeds instead of 16 and over a
wider delay grid on both branches.

Candidate J's delays were selected from a 16-seed sample.  With ~2.5 seeds of
sampling noise per delay point, a 16-seed argmax can easily sit one or two grid
steps off the true pass-rate peak, and each step is worth several percent of
joint pass rate.  This re-runs the same estimator with enough seeds that the
ranking is stable.

The two descent branches do not interact after separation (verified in the
k24 independence batch), so one sweep that moves both delays together still
yields an independent Sustainer curve and Booster curve, and every
(s0_delay, s1_delay) pair's joint pass rate is then scored exactly without
simulating the pairs -- 21 runs per seed instead of 21*21.

Constraint: the winning s0 delay must also be legal at seed 16000, because the
delivered .ork carries exactly one simulation and that is the seed saved in it.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/delay_pass_rate_wide.py
"""
import copy
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or, run_sim, generate_ork

LEGAL = 5.0
SAVED_SEED = 16000
OUT = "OSIFOG/experiments-2026-07-25/l_twoburn/delay_pass_rate_wide.json"

SEEDS = [16000, 7, 12345, 101, 202, 303, 404, 505, 606, 707, 808, 909,
         1111, 2222, 3333, 4444, 5555, 6666, 7777, 8888, 9999, 1234,
         4321, 2468, 1357, 8642, 9753, 1470, 2581, 3692, 13, 27, 42,
         55, 68, 71, 84, 97, 110, 123]

S0_DELAYS = [round(49.2300 + 0.0025 * i, 5) for i in range(21)]
S1_DELAYS = [round(78.9500 + 0.0150 * i, 5) for i in range(21)]


def main():
    base = json.load(open("designs/osifog_submission/candidate_J.json",
                          encoding="utf-8"))
    from scripts.osifog_direct_driver import BASE as DRIVER_BASE
    p_base = copy.deepcopy(DRIVER_BASE)
    p_base.update(base)

    init_or()
    n = max(len(S0_DELAYS), len(S1_DELAYS))
    s0 = [{} for _ in S0_DELAYS]
    s1 = [{} for _ in S1_DELAYS]
    total = n * len(SEEDS)
    done = 0
    for i in range(n):
        p = copy.deepcopy(p_base)
        p["s0_retro_delay"] = S0_DELAYS[min(i, len(S0_DELAYS) - 1)]
        p["s1_retro_delay"] = S1_DELAYS[min(i, len(S1_DELAYS) - 1)]
        try:
            xml = generate_ork(p)
        except Exception as exc:
            print(f"  gen fail at i={i}: {exc}", flush=True)
            continue
        for seed in SEEDS:
            try:
                m = run_sim(xml, seed=seed)
                if i < len(S0_DELAYS):
                    s0[i][seed] = m["s0_landing_speed"]
                if i < len(S1_DELAYS):
                    s1[i][seed] = m["s1_landing_speed"]
            except Exception as exc:
                print(f"  sim fail i={i} seed={seed}: {str(exc)[:70]}", flush=True)
            done += 1
        i0 = min(i, len(S0_DELAYS) - 1)
        i1 = min(i, len(S1_DELAYS) - 1)
        print("  %4d/%4d  s0[%.4f] %2d/%d | s1[%.4f] %2d/%d" % (
            done, total,
            S0_DELAYS[i0], sum(1 for s in SEEDS if s0[i0].get(s, 9e9) < LEGAL),
            len(SEEDS),
            S1_DELAYS[i1], sum(1 for s in SEEDS if s1[i1].get(s, 9e9) < LEGAL),
            len(SEEDS)), flush=True)

    print("\n--- Sustainer delay vs seeds passed ---", flush=True)
    for i, d in enumerate(S0_DELAYS):
        ok16 = s0[i].get(SAVED_SEED, 9e9) < LEGAL
        print("  %.4f  %2d/%d  seed16000=%.2f%s" % (
            d, sum(1 for s in SEEDS if s0[i].get(s, 9e9) < LEGAL), len(SEEDS),
            s0[i].get(SAVED_SEED, -1), "" if ok16 else "   (ILLEGAL at saved seed)"))
    print("--- Booster delay vs seeds passed ---", flush=True)
    for j, d in enumerate(S1_DELAYS):
        ok16 = s1[j].get(SAVED_SEED, 9e9) < LEGAL
        print("  %.4f  %2d/%d  seed16000=%.2f%s" % (
            d, sum(1 for s in SEEDS if s1[j].get(s, 9e9) < LEGAL), len(SEEDS),
            s1[j].get(SAVED_SEED, -1), "" if ok16 else "   (ILLEGAL at saved seed)"))

    best = None
    for i, d0 in enumerate(S0_DELAYS):
        if s0[i].get(SAVED_SEED, 9e9) >= LEGAL:
            continue
        for j, d1 in enumerate(S1_DELAYS):
            if s1[j].get(SAVED_SEED, 9e9) >= LEGAL:
                continue
            joint = sum(1 for s in SEEDS
                        if s0[i].get(s, 9e9) < LEGAL and s1[j].get(s, 9e9) < LEGAL)
            if best is None or joint > best[0]:
                best = (joint, d0, d1)
    if best is None:
        print("\nno delay pair is legal at the saved seed", flush=True)
    else:
        print("\nbest joint pair legal at seed %d: s0=%.4f s1=%.4f -> %d/%d (%.0f%%)"
              % (SAVED_SEED, best[1], best[2], best[0], len(SEEDS),
                 100.0 * best[0] / len(SEEDS)), flush=True)
    json.dump({"seeds": SEEDS, "s0_delays": S0_DELAYS, "s1_delays": S1_DELAYS,
               "s0": [{str(k): v for k, v in t.items()} for t in s0],
               "s1": [{str(k): v for k, v in t.items()} for t in s1],
               "best": None if best is None else
                       {"joint": best[0], "s0_delay": best[1],
                        "s1_delay": best[2], "n_seeds": len(SEEDS)}},
              open(OUT, "w"), indent=1)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
