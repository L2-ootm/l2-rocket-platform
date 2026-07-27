"""Gate 7 Round 3: Refine around best candidate region."""
import sys, os, json, math, tempfile, time
sys.path.insert(0, '.')
os.environ.setdefault('RAYON_NUM_THREADS', '1')

import jpype
import motor_data
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, validate_candidate_geometry,
    _get_anti_tumble_listener, MIN_STATIC_MARGIN, MAX_MACH
)

WIND = parse_wind_csv(WIND_CSV)
results = []

configs = [
    {'nose_mass_kg': 3.0, 's1_aft_ballast_kg': 0.5, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's1_fin_root': 0.22, 's1_fin_height': 0.38},
    {'nose_mass_kg': 3.5, 's1_aft_ballast_kg': 0.5, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's1_fin_root': 0.22, 's1_fin_height': 0.38},
    {'nose_mass_kg': 2.5, 's1_aft_ballast_kg': 0.5, 's0_fin_root': 0.18, 's0_fin_height': 0.25, 's1_fin_root': 0.25, 's1_fin_height': 0.45},
    {'nose_mass_kg': 2.5, 's1_aft_ballast_kg': 0.5, 's0_fin_root': 0.20, 's0_fin_height': 0.30, 's1_fin_root': 0.28, 's1_fin_height': 0.50},
    {'nose_mass_kg': 3.0, 's1_aft_ballast_kg': 0.5, 's0_fin_root': 0.18, 's0_fin_height': 0.25, 's1_fin_root': 0.25, 's1_fin_height': 0.45},
    {'nose_mass_kg': 3.5, 's1_aft_ballast_kg': 0.5, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's1_fin_root': 0.22, 's1_fin_height': 0.38, 's0_main': 14, 's1_main': 14},
    {'nose_mass_kg': 4.0, 's1_aft_ballast_kg': 0.5, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's1_fin_root': 0.22, 's1_fin_height': 0.38, 's0_main': 14, 's1_main': 14},
]

base = {
    's0_main': 15, 's1_main': 15, 's0_retro': 19, 's1_retro': 19,
    'main_cluster_count': 3, 's0_body_rad': 0.074, 's1_body_rad': 0.074,
    's0_body_len': 0.75, 's1_body_len': 0.80,
    's1_separation_delay': 0.0, 's0_retro_delay': 200.0, 's1_retro_delay': 200.0,
    'nose_ballast_pos_m': 0.45, 'nose_length_m': 0.50,
    's0_mid_ballast_kg': 0.0, 's0_aft_ballast_kg': 0.0,
    's0_fin_count': 4, 's0_fin_sweep': 8.0, 's1_fin_count': 4, 's1_fin_sweep': 5.0,
    's0_grid_fin_count': 0, 's1_grid_fin_count': 0,
    's0_fin_thickness_m': 0.003, 's1_fin_thickness_m': 0.003,
    's0_grid_fin_thickness_m': 0.001, 's1_grid_fin_thickness_m': 0.001,
    's0_grid_fin_root': 0.06, 's0_grid_fin_height': 0.06, 's0_grid_fin_position_m': 0.03,
    's0_grid_fin_sweep': 0.0, 's1_grid_fin_sweep': 0.0,
    's0_fin_material': 'fiberglass', 's1_fin_material': 'fiberglass',
    's0_grid_fin_material': 'fiberglass', 's1_grid_fin_material': 'fiberglass',
    'launch_azimuth': 34.0, 'launch_angle_deg': 3.85, 'wind_levels': WIND,
}

init_or()
for i, cfg in enumerate(configs):
    fixture = dict(base)
    fixture.update(cfg)
    motor_name = motor_data.load_motor_by_index(fixture['s0_main']).designation
    label = f"cfg{i}: {motor_name}x3 nose={fixture['nose_mass_kg']}kg fin_r={fixture['s0_fin_root']} fin_h={fixture['s0_fin_height']}"
    
    violations = validate_candidate_geometry(fixture)
    if violations:
        print(f"  {label}: GEOMETRY REJECTED - {violations[0][:60]}")
        continue
    
    try:
        ork_xml = generate_ork(fixture)
        fd, path = tempfile.mkstemp(suffix=".ork")
        with os.fdopen(fd, "w") as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        sim.simulate(_get_anti_tumble_listener())
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        vz0 = br0.get(fdt.TYPE_VELOCITY_Z)
        stab0 = br0.get(fdt.TYPE_STABILITY)
        times0 = br0.get(fdt.TYPE_TIME)
        apex_idx = max(range(n0), key=lambda i: float(alt0[i]))
        ascent_stab = [float(stab0[i]) for i in range(apex_idx + 1) if float(vz0[i]) > 0.01 and math.isfinite(float(stab0[i]))]
        mach = float(data.getMaxMachNumber())
        min_margin = min(ascent_stab) if ascent_stab else float("-inf")
        os.unlink(path)
        
        mach_ok = mach < MAX_MACH
        margin_ok = min_margin >= MIN_STATIC_MARGIN
        status = "STABLE" if (mach_ok and margin_ok) else ("SUPERSONIC" if not mach_ok else "UNSTABLE")
        marker = "***" if status == "STABLE" else "   "
        print(f"{marker} {label:<75} mach={mach:.3f} margin={min_margin:.3f} [{status}]")
        results.append({"label": label, "mach": mach, "margin": min_margin, "status": status})
    except Exception as e:
        print(f"  {label}: ERROR {str(e)[:80]}")

stable = [r for r in results if r["status"] == "STABLE"]
print(f"\nTested: {len(results)}, Stable subsonic: {len(stable)}")
if stable:
    for r in stable:
        print(f"  STABLE: {r['label']}: mach={r['mach']}, margin={r['margin']}")
