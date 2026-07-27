"""Experiment D: Score cross-validation.

Compare Python, Rust, and hand-calculated score for the quarantined 839k artifact.
"""
import json
import math
from pathlib import Path

# Official formula: S = 900000 - 3000*(h-3000)^2 - 16*(E_ap^2+N_ap^2) - 2*mean(E_touch^2+N_touch^2) - 500*mean(V_touch^2) - 7500*m_prop

# Quarantined artifact data
DATA = {
    "apogee_m": 3000.031,
    "apogee_east_m": -2.483,
    "apogee_north_m": 2.154,
    "stage_landings": [
        {"east_m": 58.164, "north_m": 109.440, "total_speed": 2.648},
        {"east_m": 70.282, "north_m": 52.926, "total_speed": 2.459},
    ],
    "m_prop_kg_actual": 4.725,
}


def hand_calculate(data):
    """Hand calculation of the score formula."""
    base = 900000.0
    
    # Apogee altitude penalty
    h = data["apogee_m"]
    alt_pen = 3000.0 * (h - 3000.0) ** 2
    
    # Apogee horizontal penalty
    E_ap = data["apogee_east_m"]
    N_ap = data["apogee_north_m"]
    horiz_pen = 16.0 * (E_ap ** 2 + N_ap ** 2)
    
    # Touchdown position penalty (mean over stages)
    stages = data["stage_landings"]
    mean_E = sum(s["east_m"] for s in stages) / len(stages)
    mean_N = sum(s["north_m"] for s in stages) / len(stages)
    pos_pen = 2.0 * (mean_E ** 2 + mean_N ** 2)
    
    # Touchdown speed penalty (mean over stages)
    mean_V = sum(s["total_speed"] for s in stages) / len(stages)
    vel_pen = 500.0 * mean_V ** 2
    
    # Propellant penalty
    m_prop = data["m_prop_kg_actual"]
    prop_pen = 7500.0 * m_prop
    
    score = base - alt_pen - horiz_pen - pos_pen - vel_pen - prop_pen
    
    return {
        "base": base,
        "alt_pen": alt_pen,
        "horiz_pen": horiz_pen,
        "mean_E": mean_E,
        "mean_N": mean_N,
        "pos_pen": pos_pen,
        "mean_V": mean_V,
        "vel_pen": vel_pen,
        "m_prop": m_prop,
        "prop_pen": prop_pen,
        "score": score,
    }


def python_calculate(data):
    """Python score_official calculation."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))
    from osifog_sweep import score_official
    
    metrics = {
        "apogee_m": data["apogee_m"],
        "apogee_east_m": data["apogee_east_m"],
        "apogee_north_m": data["apogee_north_m"],
        "stage_landings": [
            {
                "branch": i,
                "east_m": s["east_m"],
                "north_m": s["north_m"],
                "total_speed": s["total_speed"],
                "time_s": 0.0,
                "dist_m": 0.0,
                "vz_ms": 0.0,
                "vxy_ms": 0.0,
            }
            for i, s in enumerate(data["stage_landings"])
        ],
        "m_prop_kg_actual": data["m_prop_kg_actual"],
        "status": "SIMULATED",
        "mach": 0.943,
        "min_static_margin": 1.502,
    }
    
    # Use dummy params that pass validation
    params = {
        "s0_body_rad": 0.074, "s1_body_rad": 0.074,
        "s0_body_len": 0.70, "s1_body_len": 0.75,
        "s0_main": 37, "s1_main": 18, "s0_retro": 19, "s1_retro": 19,
        "main_cluster_count": 3,
        "nose_mass_kg": 1.72, "nose_length_m": 0.50,
        "s1_separation_delay": 0.0,
    }
    
    result = score_official(metrics, params)
    return result


def main():
    print("Experiment D: Score Cross-Validation")
    print("=" * 60)
    
    # Hand calculation
    hand = hand_calculate(DATA)
    print("\nHand Calculation:")
    print(f"  Base:              {hand['base']:>12.2f}")
    print(f"  Altitude penalty:  {hand['alt_pen']:>12.4f}")
    print(f"  Horizontal penalty:{hand['horiz_pen']:>12.4f}")
    print(f"  Touch position:    {hand['pos_pen']:>12.2f}")
    print(f"  Touch speed:       {hand['vel_pen']:>12.2f}")
    print(f"  Propellant:        {hand['prop_pen']:>12.2f}")
    print(f"  Score:             {hand['score']:>12.2f}")
    
    # Verify arithmetic
    expected = 900000 - hand['alt_pen'] - hand['horiz_pen'] - hand['pos_pen'] - hand['vel_pen'] - hand['prop_pen']
    print(f"\n  Verification: {hand['score']:.2f} == {expected:.2f} -> {'PASS' if abs(hand['score'] - expected) < 0.01 else 'FAIL'}")
    
    # Penalty ranking
    penalties = [
        ("Propellant", hand['prop_pen']),
        ("Touch position", hand['pos_pen']),
        ("Touch speed", hand['vel_pen']),
        ("Horizontal apogee", hand['horiz_pen']),
        ("Altitude", hand['alt_pen']),
    ]
    penalties.sort(key=lambda x: x[1], reverse=True)
    print("\nPenalty ranking (largest first):")
    for name, pen in penalties:
        print(f"  {name:<20} {pen:>12.2f}")
    
    # Score ceiling analysis
    print("\nScore ceiling analysis:")
    ceiling_no_touch = 900000 - hand['alt_pen'] - hand['horiz_pen'] - hand['prop_pen']
    print(f"  With zero touch penalties:     {ceiling_no_touch:>12.2f}")
    ceiling_zero_all = 900000 - hand['prop_pen']
    print(f"  With zero all non-prop penalties: {ceiling_zero_all:>12.2f}")
    
    # What propellant reduction is needed for 850k?
    target = 850000
    needed = 900000 - target - hand['alt_pen'] - hand['horiz_pen']
    max_prop_penalty = needed
    max_prop_kg = max_prop_penalty / 7500.0
    print(f"\n  To reach {target} with zero touch penalties:")
    print(f"    Max propellant penalty: {max_prop_penalty:.2f}")
    print(f"    Max propellant mass:    {max_prop_kg:.4f} kg")
    print(f"    Current propellant:     {hand['m_prop']:.4f} kg")
    print(f"    Reduction needed:       {hand['m_prop'] - max_prop_kg:.4f} kg")
    
    # Save results
    output = Path("docs/research/osifog-2026-deep-audit/experiments")
    output.mkdir(parents=True, exist_ok=True)
    artifact = {
        "experiment": "D_score_crossvalidation",
        "input_data": DATA,
        "hand_calculation": hand,
        "penalty_ranking": penalties,
        "score_ceiling_no_touch": ceiling_no_touch,
        "score_ceiling_zero_all": ceiling_zero_all,
        "target_850k_max_prop_kg": max_prop_kg,
    }
    (output / "score_crossvalidation.json").write_text(
        json.dumps(artifact, indent=2), encoding="utf-8"
    )
    print(f"\nResults saved to {output / 'score_crossvalidation.json'}")


if __name__ == "__main__":
    main()
