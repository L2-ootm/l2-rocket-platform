"""Pick retro delays that maximise cross-seed pass rate, not single-seed depth.

Candidate I and its lineage were tuned by driving one seed's touchdown speed to
a minimum.  That optimises the wrong thing: what decides whether a reviewer's
rerun stays legal is how many wind realisations land under 5 m/s, and the
deepest point of a basin is not usually its most seed-robust point.

The two descent branches are physically independent after separation -- verified
directly: the Sustainer lands at 1.745 m/s whether the Booster retro fires at
79.05 s, 80.0 s or never, and vice versa.  So a single sweep that varies both
delays together yields, per seed, an independent Sustainer curve and Booster
curve, and every (s0_delay, s1_delay) pair's joint pass rate can then be scored
exactly without simulating the pairs.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/delay_pass_rate_optimizer.py
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
OUT = "OSIFOG/experiments-2026-07-25/k_arch/delay_pass_rate.json"
SEEDS = [16000, 7, 12345, 101, 202, 303, 404, 505, 606, 707, 808, 909,
         1111, 2222, 3333, 4444]

S0_DELAYS = [round(49.2400 + 0.0025 * i, 5) for i in range(17)]   # 49.2400..49.2800
S1_DELAYS = [round(79.0000 + 0.0100 * i, 5) for i in range(17)]   # 79.00..79.16


def main():
    # The batch `_base` is only an override layer -- it omits driver defaults
    # such as main_cluster_count=3, without which the geometry gate rejects the
    # build.  Compose them the same way osifog_direct_driver does.
    from scripts.osifog_direct_driver import BASE as DRIVER_BASE
    base = copy.deepcopy(DRIVER_BASE)
    base.update(json.load(open(
        "OSIFOG/experiments-2026-07-25/k_arch/k14_seed_matrix.json",
        encoding="utf-8"))["_base"])
    init_or()
    n = max(len(S0_DELAYS), len(S1_DELAYS))
    # s0[i][seed] and s1[j][seed]; pairing index i with j in the same run is
    # safe precisely because the branches do not interact.
    s0 = [{} for _ in S0_DELAYS]
    s1 = [{} for _ in S1_DELAYS]
    total = n * len(SEEDS)
    done = 0
    for i in range(n):
        p = copy.deepcopy(base)
        p["s0_retro_delay"] = S0_DELAYS[min(i, len(S0_DELAYS) - 1)]
        p["s1_retro_delay"] = S1_DELAYS[min(i, len(S1_DELAYS) - 1)]
        try:
            xml = generate_ork(p)
        except Exception as exc:
            print(f"  gen fail at i={i}: {exc}")
            continue
        for seed in SEEDS:
            try:
                m = run_sim(xml, seed=seed)
                if i < len(S0_DELAYS):
                    s0[i][seed] = m["s0_landing_speed"]
                if i < len(S1_DELAYS):
                    s1[i][seed] = m["s1_landing_speed"]
            except Exception as exc:
                print(f"  sim fail i={i} seed={seed}: {str(exc)[:70]}")
            done += 1
        print("  %3d/%3d  s0[%.4f] %s | s1[%.4f] %s" % (
            done, total,
            S0_DELAYS[min(i, len(S0_DELAYS) - 1)],
            " ".join("%5.1f" % s0[min(i, len(S0_DELAYS) - 1)].get(s, -1)
                     for s in SEEDS[:6]),
            S1_DELAYS[min(i, len(S1_DELAYS) - 1)],
            " ".join("%5.1f" % s1[min(i, len(S1_DELAYS) - 1)].get(s, -1)
                     for s in SEEDS[:6])), flush=True)

    def rate(table, idx):
        vals = table[idx]
        return sum(1 for s in SEEDS if vals.get(s, 9e9) < LEGAL)

    print("\n--- Sustainer delay vs seeds passed ---")
    for i, d in enumerate(S0_DELAYS):
        print("  %.4f  %2d/%d" % (d, rate(s0, i), len(SEEDS)))
    print("--- Booster delay vs seeds passed ---")
    for j, d in enumerate(S1_DELAYS):
        print("  %.4f  %2d/%d" % (d, rate(s1, j), len(SEEDS)))

    best = None
    for i, d0 in enumerate(S0_DELAYS):
        for j, d1 in enumerate(S1_DELAYS):
            joint = sum(1 for s in SEEDS
                        if s0[i].get(s, 9e9) < LEGAL and s1[j].get(s, 9e9) < LEGAL)
            if best is None or joint > best[0]:
                best = (joint, d0, d1)
    print("\nbest joint pair: s0=%.4f s1=%.4f -> %d/%d seeds"
          % (best[1], best[2], best[0], len(SEEDS)))
    json.dump({"seeds": SEEDS, "s0_delays": S0_DELAYS, "s1_delays": S1_DELAYS,
               "s0": [{str(k): v for k, v in t.items()} for t in s0],
               "s1": [{str(k): v for k, v in t.items()} for t in s1],
               "best": {"joint": best[0], "s0_delay": best[1],
                        "s1_delay": best[2]}},
              open(OUT, "w"), indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
