#!/usr/bin/env python3
"""Test bigger motors with 0.08kg nose mass for tail-first descent."""
import sys, os, tempfile, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from osifog_sweep import parse_wind_csv, generate_ork, init_or, LAUNCH_ALT
from rocket_forge import MOTOR_DATABASE

wind = parse_wind_csv("OSIFOG/OpenWind_File.csv")
helper = init_or()

# Test different sustainer motors with 0.08kg nose mass
tests = [
    # (s0_main, s1_main, nose_mass, label)
    (19, 24, 0.08, "J800T+L1150 nm=0.08"),
    (20, 24, 0.08, "K550W+L1150 nm=0.08"),
    (21, 24, 0.08, "K700W+L1150 nm=0.08"),
    (22, 24, 0.08, "K1050W+L1150 nm=0.08"),
    (20, 23, 0.08, "K550W+L1000 nm=0.08"),
    (21, 23, 0.08, "K700W+L1000 nm=0.08"),
]

for s0m, s1m, nm, label in tests:
    ml0 = MOTOR_DATABASE[s0m][3]
    ml1 = MOTOR_DATABASE[s1m][3]
    dm0 = MOTOR_DATABASE[s0m][2]
    dm1 = MOTOR_DATABASE[s1m][2]
    r0 = max(0.033, dm0/2 + 0.006)
    r1 = max(0.035, dm1/2 + 0.006)

    p = {
        "s0_main": s0m, "s0_retro": 0,
        "s1_main": s1m, "s1_retro": 0,
        "s0_body_len": max(0.60, ml0 + 0.098 + 0.12),
        "s0_body_rad": r0,
        "s1_body_len": max(0.70, ml1 + 0.098 + 0.12),
        "s1_body_rad": r1,
        "s0_retro_delay": 173.0,
        "s1_retro_delay": 114.0,
        "wind_levels": wind,
    }

    ork_xml = generate_ork(p)
    ork_xml = ork_xml.replace("<mass>0.050</mass>", f"<mass>{nm:.3f}</mass>")

    fd, path = tempfile.mkstemp(suffix=".ork")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ork_xml)

    try:
        doc = helper.load_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(16000)
        sim.simulate()
        data = sim.getSimulatedData()

        import jpype
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        TYPE_ALT = fdt.TYPE_ALTITUDE
        TYPE_VZ = fdt.TYPE_VELOCITY_Z
        TYPE_VXY = fdt.TYPE_VELOCITY_XY
        TYPE_PX = fdt.TYPE_POSITION_X
        TYPE_PY = fdt.TYPE_POSITION_Y

        n_branches = int(data.getBranchCount())
        apogee = data.getMaxAltitude()

        s0_v, s1_v, s0_d, s1_d = None, None, None, None
        for bi in range(n_branches):
            br = data.getBranch(bi)
            n = br.getLength()
            if n < 2:
                continue
            alt_arr = br.get(TYPE_ALT)
            vz_arr = br.get(TYPE_VZ)
            vxy_arr = br.get(TYPE_VXY)
            px_arr = br.get(TYPE_PX)
            py_arr = br.get(TYPE_PY)
            final_vz = float(vz_arr[n-1])
            final_vxy = float(vxy_arr[n-1])
            final_px = float(px_arr[n-1])
            final_py = float(py_arr[n-1])
            total_v = math.sqrt(final_vz**2 + final_vxy**2)
            dist = math.sqrt(final_px**2 + final_py**2)
            final_alt = float(alt_arr[n-1])
            if final_alt <= LAUNCH_ALT + 10:
                if bi == 0:
                    s0_v, s0_d = total_v, dist
                elif bi == 1:
                    s1_v, s1_d = total_v, dist

        s0_vs = f"{s0_v:.1f}" if s0_v is not None else "N/A"
        s1_vs = f"{s1_v:.1f}" if s1_v is not None else "N/A"
        s0_ds = f"{s0_d:.0f}" if s0_d is not None else "N/A"
        s1_ds = f"{s1_d:.0f}" if s1_d is not None else "N/A"

        err = abs(apogee - 3000)
        print(f"  {label:25s} apogee={apogee:7.1f}m  err={err:5.1f}m  "
              f"S0: v={s0_vs:>6} d={s0_ds:>5}m  S1: v={s1_vs:>6} d={s1_ds:>5}m")
    except Exception as e:
        print(f"  {label:25s} ERROR: {e}")
    finally:
        os.unlink(path)
