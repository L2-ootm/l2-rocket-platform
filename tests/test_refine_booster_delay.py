"""Refine booster retro ignition around the optimal delay."""
import sys, os, math, tempfile
sys.path.insert(0, '.')
os.environ.setdefault('RAYON_NUM_THREADS', '1')
import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, _get_anti_tumble_listener,
)

BEST = {
    's0_main': 14, 's1_main': 14, 's0_retro': 19, 's1_retro': 19,
    'main_cluster_count': 3, 's0_body_rad': 0.074, 's1_body_rad': 0.074,
    's0_body_len': 0.75, 's1_body_len': 0.80,
    's1_separation_delay': 0.0, 's0_retro_delay': 200.0, 's1_retro_delay': 200.0,
    'nose_mass_kg': 4.0, 'nose_ballast_pos_m': 0.45, 'nose_length_m': 0.50,
    's0_mid_ballast_kg': 0.0, 's1_mid_ballast_kg': 0.0,
    's0_aft_ballast_kg': 0.0, 's1_aft_ballast_kg': 0.5,
    's0_fin_count': 4, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's0_fin_sweep': 8.0,
    's1_fin_count': 4, 's1_fin_root': 0.22, 's1_fin_height': 0.38, 's1_fin_sweep': 5.0,
    's1_grid_fin_count': 0, 's1_grid_fin_root': 0.06, 's1_grid_fin_height': 0.06,
    's1_grid_fin_position_m': 0.03, 's0_grid_fin_count': 0, 's0_grid_fin_root': 0.06,
    's0_grid_fin_height': 0.06, 's0_grid_fin_position_m': 0.03,
    's0_fin_thickness_m': 0.003, 's1_fin_thickness_m': 0.003,
    's0_grid_fin_thickness_m': 0.001, 's1_grid_fin_thickness_m': 0.001,
    's0_fin_material': 'fiberglass', 's1_fin_material': 'fiberglass',
    's0_grid_fin_material': 'fiberglass', 's1_grid_fin_material': 'fiberglass',
    'launch_azimuth': 34.0, 'launch_angle_deg': 3.85,
    'wind_levels': parse_wind_csv(WIND_CSV),
}

init_or()
for delay in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]:
    fixture = dict(BEST)
    fixture['s1_retro_delay'] = delay
    fixture['s0_retro_delay'] = 200.0
    ork_xml = generate_ork(fixture)
    fd, path = tempfile.mkstemp(suffix='.ork')
    with os.fdopen(fd, 'w') as f:
        f.write(ork_xml)
    doc = _load_ork_doc(path)
    sim = doc.getSimulations().get(0)
    sim.getOptions().setRandomSeed(SIM_SEED)
    _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
    sim.simulate(_get_anti_tumble_listener())
    data = sim.getSimulatedData()
    fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
    FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
    br1 = data.getBranch(1)
    n1 = int(br1.getLength())
    times = br1.get(fdt.TYPE_TIME)
    vz_arr = br1.get(fdt.TYPE_VELOCITY_Z)
    vxy_arr = br1.get(fdt.TYPE_VELOCITY_XY)
    alt_arr = br1.get(fdt.TYPE_ALTITUDE)
    hit_time = None
    for ev in br1.getEvents():
        if ev.getType() == FlightEvent.Type.GROUND_HIT:
            hit_time = float(ev.getTime())
            break
    if hit_time is None:
        print(f"delay={delay:5.1f}s: no ground hit")
        os.unlink(path)
        continue
    idx = 1
    for i in range(1, n1):
        if float(times[i]) >= hit_time:
            idx = i
            break
    t1, t2 = float(times[idx-1]), float(times[idx])
    f_frac = (hit_time - t1) / (t2 - t1) if t2 > t1 else 1.0
    vz = float(vz_arr[idx-1]) + f_frac * (float(vz_arr[idx]) - float(vz_arr[idx-1]))
    vxy = float(vxy_arr[idx-1]) + f_frac * (float(vxy_arr[idx]) - float(vxy_arr[idx-1]))
    speed = math.sqrt(vz**2 + vxy**2)
    retro_ign_idx = min(range(n1), key=lambda i: abs(float(times[i]) - delay))
    alt_at_ign = float(alt_arr[retro_ign_idx])
    legal = speed < 5.0
    marker = "***" if legal else "   "
    status = "LEGAL" if legal else "ILLEGAL"
    print(f"{marker} delay={delay:5.1f}s: alt={alt_at_ign:7.1f}m  speed={speed:7.2f}m/s  [{status}]")
    os.unlink(path)
