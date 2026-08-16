#!/usr/bin/env python3
"""Debug: check landing with 0.05kg nose mass."""
import sys, os, tempfile, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from osifog_sweep import parse_wind_csv, generate_ork, init_or, LAUNCH_ALT
from rocket_forge import MOTOR_DATABASE

wind = parse_wind_csv("OSIFOG/OpenWind_File.csv")
helper = init_or()

p = {
    "s0_main": 19, "s0_retro": 0,
    "s1_main": 24, "s1_retro": 0,
    "s0_body_len": 0.60, "s0_body_rad": 0.033,
    "s1_body_len": 0.749, "s1_body_rad": 0.046,
    "s0_retro_delay": 81.0,
    "s1_retro_delay": 114.0,
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

    import jpype
    fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
    TYPE_ALT = fdt.TYPE_ALTITUDE
    TYPE_PX = fdt.TYPE_POSITION_X
    TYPE_PY = fdt.TYPE_POSITION_Y
    TYPE_VZ = fdt.TYPE_VELOCITY_Z
    TYPE_VXY = fdt.TYPE_VELOCITY_XY

    n_branches = int(data.getBranchCount())
    print(f"Max alt: {data.getMaxAltitude():.1f}m  Mach: {data.getMaxMachNumber():.2f}  Flight time: {data.getFlightTime():.1f}s")
    print(f"Status: {sim.getStatus()}")
    print(f"Branches: {n_branches}")

    for bi in range(n_branches):
        br = data.getBranch(bi)
        n = br.getLength()
        events = br.getEvents()
        print(f"\n  Branch {bi}: {n} points, {len(events)} events")

        for e in events:
            etype = str(e.getType().name()) if hasattr(e.getType(), 'name') else str(e.getType())
            if etype in ("IGNITION", "BURNOUT", "APOGEE", "GROUND_HIT", "STAGE_SEPARATION", "TUMBLE", "SIM_ABORT", "SIM_WARN"):
                print(f"    {etype} at t={float(e.getTime()):.2f}s")

        if n < 2:
            continue

        alt_arr = br.get(TYPE_ALT)
        px_arr = br.get(TYPE_PX)
        py_arr = br.get(TYPE_PY)
        vz_arr = br.get(TYPE_VZ)
        vxy_arr = br.get(TYPE_VXY)

        final_alt = float(alt_arr[n - 1])
        final_vz = float(vz_arr[n - 1])
        final_vxy = float(vxy_arr[n - 1])
        final_px = float(px_arr[n - 1])
        final_py = float(py_arr[n - 1])
        h_dist = math.sqrt(final_px**2 + final_py**2)
        landing_speed = math.sqrt(final_vz**2 + final_vxy**2)

        print(f"    Final: alt={final_alt:.1f}m vz={final_vz:.1f}m/s vxy={final_vxy:.1f}m/s total={landing_speed:.1f}m/s dist={h_dist:.1f}m")

        # Show velocity at key altitudes
        for target_alt in [100, 50, 20, 10, 5, 2, 0]:
            for i in range(n):
                if abs(float(alt_arr[i]) - target_alt) < 2:
                    vz = float(vz_arr[i])
                    vxy = float(vxy_arr[i])
                    print(f"    At {target_alt}m: vz={vz:.1f}m/s vxy={vxy:.1f}m/s total={math.sqrt(vz**2+vxy**2):.1f}m/s")
                    break

except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    os.unlink(path)
