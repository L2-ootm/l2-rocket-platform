#!/usr/bin/env python3
"""Debug: extract warnings from a failing .ork simulation."""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from osifog_sweep import parse_wind_csv, generate_ork, init_or
from rocket_forge import MOTOR_DATABASE

wind = parse_wind_csv("OSIFOG/OpenWind_File.csv")
helper = init_or()

p = {
    "s0_main": 2, "s0_retro": 0,
    "s1_main": 7, "s1_retro": 0,
    "s0_body_len": 0.35, "s0_body_rad": 0.025,
    "s1_body_len": 0.40, "s1_body_rad": 0.025,
    "s0_retro_delay": 14.0, "s1_retro_delay": 10.0,
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

    print(f"Status: {sim.getStatus()}")
    print(f"Max alt: {data.getMaxAltitude()}m")
    print(f"Max Mach: {data.getMaxMachNumber()}")
    print(f"Flight time: {data.getFlightTime()}s")

    # Extract warnings from the simulation data
    try:
        wlist = data.getWarnings()
        if wlist:
            print(f"\nData warnings ({len(wlist)}):")
            for w in wlist:
                print(f"  {w}")
    except Exception as e:
        print(f"  Could not get data warnings: {e}")

    # Try to get abort reasons
    branch_count = data.getBranchCount()
    print(f"\nBranches: {branch_count}")
    for bi in range(branch_count):
        branch = data.getBranch(bi)
        events = branch.getEvents()
        print(f"  Branch {bi}: {branch.getLength()} points")
        for e in events:
            etype = str(e.getType().name()) if hasattr(e.getType(), 'name') else str(e.getType())
            print(f"    Event: {etype} at t={float(e.getTime()):.4f}s")
            if etype == "SIM_ABORT":
                print(f"    ABORT DATA: {e.getData()}")

    # Show first and last few data points
    for bi in range(branch_count):
        branch = data.getBranch(bi)
        n = branch.getLength()
        pos = branch.getPosition()
        vel = branch.getVelocity()
        t = branch.getTime()
        print(f"\n  Branch {bi} trajectory (first 3 + last 3):")
        for i in range(min(3, n)):
            print(f"    t={float(t.get(i)):.3f}s alt={float(pos.get(i).z):.1f}m vz={float(vel.get(i).z):.2f}m/s")
        if n > 6:
            print(f"    ...")
            for i in range(max(3, n-3), n):
                print(f"    t={float(t.get(i)):.3f}s alt={float(pos.get(i).z):.1f}m vz={float(vel.get(i).z):.2f}m/s")

except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    os.unlink(path)
