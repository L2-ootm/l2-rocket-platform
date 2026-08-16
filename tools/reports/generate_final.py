import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from osifog_sweep import generate_ork, parse_wind_csv, MOTOR_DATABASE, init_or, BEST_KNOWN_PARAMS, _load_ork_doc, run_sim, score_flight

def main():
    print("Loading wind profile...")
    wind = parse_wind_csv("OSIFOG/OpenWind_File.csv")
    
    p = BEST_KNOWN_PARAMS.copy()
    p["wind_levels"] = wind
    
    print("Generating ORK with BEST_KNOWN_PARAMS...")
    xml = generate_ork(p)
    
    output_path = "designs/osifog_level3/falcon_winner.ork"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml)
        
    print(f"File saved to {output_path}")
    
    print("Initializing OpenRocket to verify scoring...")
    init_or()
    
    metrics = run_sim(xml, anti_tumble=True)
    score = score_flight(metrics, p)
    
    print(f"\nSimulation Status: {metrics['status']}")
    print(f"Apogee: {metrics['apogee_m']:.2f} m")
    print(f"Score: {score:.2f} / 900000")

if __name__ == "__main__":
    main()
