import json
import argparse
from scipy.optimize import minimize
from l2_hyper.mission import load_mission
from l2_hyper.orkit import OpenRocketSession
from l2_hyper.genome import clamp, build_bounds

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mission")
    ap.add_argument("seed_file")
    args = ap.parse_args()

    mission = load_mission(args.mission)
    bounds = build_bounds(mission)
    
    with open(args.seed_file, "r") as f:
        data = json.load(f)
    
    # Extract the best genome
    if isinstance(data, list) and len(data) > 0:
        base_genome = data[0]["genome"] if "genome" in data[0] else data[0]
    elif isinstance(data, dict) and "elite" in data:
        base_genome = data["elite"][0]["genome"] if "genome" in data["elite"][0] else data["elite"][0]
    else:
        base_genome = data

    print("[*] Base genome loaded.")

    # We will optimize a subset of continuous parameters to micro-tune the apogee.
    # The simplest way to control apogee precisely is adjusting the mass of the payload (if it were a parameter) 
    # or the length of a body tube (e.g., 'kick_length' or 'booster_length') which changes mass and drag.
    # Let's optimize 'sustainer_length' and 'kick_length'.
    
    keys_to_opt = ["sustainer_length", "kick_length"]
    initial_x = [base_genome[k] for k in keys_to_opt]

    with OpenRocketSession() as session:
        motors = session.resolve_motors(mission["stack"])
        
        def objective(x):
            genome = dict(base_genome)
            for k, val in zip(keys_to_opt, x):
                genome[k] = val
            
            # clamp to bounds
            genome = clamp(genome, bounds)
            
            metrics = session.evaluate(mission, genome, motors)
            apogee = metrics["apogee"]
            error = abs(apogee - 15000.0)
            
            # Add penalty for instability to keep the rocket viable
            penalty = 0.0
            if metrics["min_static_margin"] < 1.5:
                penalty += (1.5 - metrics["min_static_margin"]) * 100000.0
            if metrics["tumbled"]:
                penalty += 1000000.0
                
            return error + penalty

        print("[*] Starting Nelder-Mead micro-polish for 5-decimal precision...")
        res = minimize(
            objective, 
            initial_x, 
            method='Nelder-Mead',
            options={'xatol': 1e-8, 'fatol': 1e-6, 'maxiter': 200, 'disp': True}
        )
        
        print("\n[*] Optimization Finished!")
        print(f"Success: {res.success}")
        print(f"Message: {res.message}")
        print(f"Final error: {res.fun}")
        
        final_genome = dict(base_genome)
        for k, val in zip(keys_to_opt, res.x):
            final_genome[k] = val
        final_genome = clamp(final_genome, bounds)
        
        # Final evaluate to get exact apogee and save
        metrics = session.evaluate(mission, final_genome, motors, keep_path=mission.get("output", "designs/optimized/15k_precision.ork"))
        print("\n" + "="*50)
        print(f"FINAL MICRO-POLISHED APOGEE: {metrics['apogee']:.6f} m")
        print(f"TARGET APOGEE              : 15000.000000 m")
        print(f"MACH                       : {metrics['mach']:.3f}")
        print(f"STATIC MARGIN              : {metrics['min_static_margin']:.3f} cal")
        print(f"SAVED TO                   : {mission.get('output', 'designs/optimized/15k_precision.ork')}")
        print("="*50)

if __name__ == "__main__":
    main()
