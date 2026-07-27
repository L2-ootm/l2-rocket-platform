import json
import os
import math
from scipy.optimize import minimize
import orhelper
from orhelper import OpenRocketInstance
from rocket_forge import RocketArchitect
from forge_mega import evaluate

def main():
    with open("designs/optimized/L2_Weird_Speed_Demon_params.json", "r") as f:
        data = json.load(f)
    
    base_params = data["params"]
    
    # The rocket with birch wood was too heavy and draggy.
    # We will change the material to fiberglass. A fiberglass rocket of this size
    # with an N4800T motor will easily reach 25,000m - 30,000m.
    # Then we use continuous payload_mass to perfectly lower it to 15,000m!
    
    base_params["motor_index"] = 33 # Cesaroni N5800
    base_params["body_material"] = "fiberglass"
    base_params["body_radius"] = 0.054 
    base_params["body_length"] = 2.0 # Shorter body so it easily clears 15k, leaving room for payload
    base_params["payload_mass"] = 5.0 # Start with 5kg payload
    
    initial_val = base_params["payload_mass"]
    architect = RocketArchitect()

    print(f"[*] Starting micro-polish on Weird Speed Demon.")
    print(f"[*] Initial payload_mass: {initial_val:.6f}kg")

    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        def objective(x):
            new_val = x[0]
            if new_val < 0.0:
                return 999999.0
                
            params = dict(base_params)
            params["payload_mass"] = new_val
            
            filepath = "temp_ork/polish_test.ork"
            try:
                architect.save(params, filepath)
                metrics = evaluate(orh, filepath, 0.0)
            except Exception:
                return 999999.0
                
            if metrics is None or metrics["apogee"] < 100:
                return 999999.0
                
            apogee = metrics["apogee"]
            error = abs(apogee - 15000.0)
            return error

        print("[*] Running Nelder-Mead to reach 15,000.00000m...")
        res = minimize(
            objective, 
            [initial_val], 
            method='Nelder-Mead',
            options={'xatol': 1e-8, 'fatol': 1e-6, 'maxiter': 200, 'disp': True}
        )
        
        print("\n[*] Optimization Finished!")
        print(f"Final payload_mass: {res.x[0]:.6f}kg")
        print(f"Final error (meters): {res.fun:.6f}m")
        
        final_params = dict(base_params)
        final_params["payload_mass"] = res.x[0]
        final_path = "designs/optimized/15k_precision_polished.ork"
        architect.save(final_params, final_path)
        
        final_metrics = evaluate(orh, final_path, 0.0)
        
        print("\n" + "="*50)
        print(f"FINAL MICRO-POLISHED APOGEE: {final_metrics['apogee']:.5f} m")
        print(f"TARGET APOGEE              : 15000.00000 m")
        print(f"MACH                       : {final_metrics['mach']:.3f}")
        print(f"SAVED TO                   : {final_path}")
        print("="*50)

if __name__ == "__main__":
    os.makedirs("temp_ork", exist_ok=True)
    main()
