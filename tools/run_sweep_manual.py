import os
import json
import sys
from pathlib import Path

# Run from anywhere: put the repository root on sys.path so the flat
# top-level modules (osifog_sweep, rocket_forge, ...) resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from osifog_sweep import (
    parse_wind_csv, init_or, build_motor_grid, run_sweep, 
    print_top_results, save_results, build_fine_grid, build_precision_grid, save_ork, MOTOR_DATABASE, LAUNCH_ROD_M
)

def run():
    wind = parse_wind_csv("OSIFOG/OpenWind_File.csv")
    helper = init_or()
    
    print("PHASE 1: Coarse grid")
    grid1 = build_motor_grid(wind)
    res1 = run_sweep(grid1, helper, label="p1")
    save_results(res1, "phase1")
    
    top_p1 = res1[:3]
    all_res2 = []
    for rank, r in enumerate(top_p1, 1):
        print(f"PHASE 2: Fine grid for rank {rank}")
        grid2 = build_fine_grid(r["params"], wind)
        res2 = run_sweep(grid2, helper, label=f"p2_r{rank}")
        all_res2.extend(res2)
    
    all_res2.sort(key=lambda x: x["score"]["score"], reverse=True)
    save_results(all_res2, "phase2")
    
    top_p2 = all_res2[:3]
    all_res3 = []
    for rank, r in enumerate(top_p2, 1):
        print(f"PHASE 3: Precision grid for rank {rank}")
        grid3 = build_precision_grid(r["params"], wind)
        res3 = run_sweep(grid3, helper, label=f"p3_r{rank}")
        all_res3.extend(res3)
        
    all_res3.sort(key=lambda x: x["score"]["score"], reverse=True)
    save_results(all_res3, "phase3")
    
    winner = all_res3[0]
    best_p = winner["params"]
    
    save_ork(best_p, wind, "falcon_winner_final")
    print("DONE! Best score:", winner["score"]["score"])

if __name__ == "__main__":
    run()
