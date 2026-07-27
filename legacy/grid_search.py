import json
import os
import orhelper
from orhelper import OpenRocketInstance
from rocket_forge import RocketArchitect
from forge_mega import evaluate

def main():
    with open("designs/optimized/L2_Weird_Speed_Demon_params.json", "r") as f:
        data = json.load(f)
    
    base_params = data["params"]
    base_params["motor_index"] = 32 # N4800T
    base_params["body_material"] = "fiberglass"
    base_params["body_radius"] = 0.054 
    
    architect = RocketArchitect()

    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        for m in range(0, 25, 2):
            params = dict(base_params)
            params["payload_mass"] = float(m)
            filepath = "temp_ork/grid_test.ork"
            architect.save(params, filepath)
            metrics = evaluate(orh, filepath, 0.0)
            
            if metrics:
                print(f"Mass: {m}kg -> Apogee: {metrics['apogee']:.1f}m | Mach: {metrics['mach']:.2f}")
            else:
                print(f"Mass: {m}kg -> FAILED")

if __name__ == "__main__":
    os.makedirs("temp_ork", exist_ok=True)
    main()
