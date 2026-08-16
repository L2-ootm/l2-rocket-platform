#!/usr/bin/env python3
"""Debug: check all events in both branches."""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from osifog_sweep import parse_wind_csv, generate_ork, init_or
from rocket_forge import MOTOR_DATABASE

wind = parse_wind_csv("OSIFOG/OpenWind_File.csv")
helper = init_or()

p = {
    "s0_main": 19, "s0_retro": 0,
    "s1_main": 24, "s1_retro": 0,
    "s0_body_len": 0.60, "s0_body_rad": 0.033,
    "s1_body_len": 0.749, "s1_body_rad": 0.046,
    "s0_retro_delay": 18.0, "s1_retro_delay": 10.0,
    "wind_levels": wind,
}

ork = generate_ork(p)
fd, path = tempfile.mkstemp(suffix=".ork")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(ork)

try:
    doc = helper.load_doc(path)
    sim = doc.getSimulations().get(0)
    sim.getOptions().setRandomSeed(16000)
    sim.simulate()
    data = sim.getSimulatedData()

    n_branches = int(data.getBranchCount())
    print(f"Branches: {n_branches}")

    for bi in range(n_branches):
        br = data.getBranch(bi)
        n = br.getLength()
        events = br.getEvents()
        print(f"\n  Branch {bi}: {n} points, {len(events)} events")
        for e in events:
            etype = str(e.getType().name()) if hasattr(e.getType(), 'name') else str(e.getType())
            print(f"    {etype} at t={float(e.getTime()):.4f}s")

    # Also check the simulation events (top level)
    print(f"\n  Simulation events:")
    sim_events = sim.getSimulatedData().getBranch(0).getEvents()
    for e in sim_events:
        etype = str(e.getType().name()) if hasattr(e.getType(), 'name') else str(e.getType())
        print(f"    {etype} at t={float(e.getTime()):.4f}s")

except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    os.unlink(path)
