"""Find exact apex and tail-first transition for booster free descent."""
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
    's1_grid_fin_count': 0, 's0_grid_fin_count': 0,
    's0_fin_thickness_m': 0.003, 's1_fin_thickness_m': 0.003,
    's0_grid_fin_thickness_m': 0.001, 's1_grid_fin_thickness_m': 0.001,
    's0_fin_material': 'fiberglass', 's1_fin_material': 'fiberglass',
    's0_grid_fin_material': 'fiberglass', 's1_grid_fin_material': 'fiberglass',
    's0_grid_fin_root': 0.06, 's0_grid_fin_height': 0.06, 's0_grid_fin_position_m': 0.03,
    's1_grid_fin_root': 0.06, 's1_grid_fin_height': 0.06, 's1_grid_fin_position_m': 0.03,
    'launch_azimuth': 34.0, 'launch_angle_deg': 3.85,
    'wind_levels': parse_wind_csv(WIND_CSV),
}

init_or()
fixture = dict(BEST)
fixture['s1_retro_delay'] = 200.0
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
br = data.getBranch(1)
n = int(br.getLength())
times = [float(br.get(fdt.TYPE_TIME)[i]) for i in range(n)]
vzs = [float(br.get(fdt.TYPE_VELOCITY_Z)[i]) for i in range(n)]
thetas = [float(br.get(fdt.TYPE_ORIENTATION_THETA)[i]) for i in range(n)]

print("Booster free-descent velocity profile (selected samples):")
print(f"{'time':>8s} {'vz':>10s} {'theta_deg':>10s} {'phase':>15s}")
for i in range(0, n, max(1, n // 30)):
    phase = 'ascending' if vzs[i] > 0.1 else ('descending' if vzs[i] < -0.1 else 'apex')
    print(f"{times[i]:8.3f} {vzs[i]:10.3f} {math.degrees(thetas[i]):10.1f} {phase:>15s}")

# Find exact apex (vz crosses zero)
for i in range(1, n):
    if vzs[i - 1] > 0 and vzs[i] <= 0:
        frac = vzs[i - 1] / (vzs[i - 1] - vzs[i])
        apex_t = times[i - 1] + frac * (times[i] - times[i - 1])
        apex_theta = math.degrees(thetas[i - 1] + frac * (thetas[i] - thetas[i - 1]))
        print(f"\nEXACT APEX: t={apex_t:.3f}s, theta={apex_theta:.1f}deg")
        print(f"After apex, vz becomes negative (descending)")
        break

# Find when theta first becomes positive during descent
for i in range(1, n):
    if vzs[i] < -0.1 and thetas[i] > 0:
        print(f"\nFIRST TAIL-FIRST during descent: t={times[i]:.3f}s, vz={vzs[i]:.3f}, theta={math.degrees(thetas[i]):.1f}deg")
        break

# Find when theta first becomes negative during descent (nose-first)
for i in range(1, n):
    if vzs[i] < -0.1 and thetas[i] < 0:
        print(f"FIRST NOSE-FIRST during descent: t={times[i]:.3f}s, vz={vzs[i]:.3f}, theta={math.degrees(thetas[i]):.1f}deg")
        break

os.unlink(path)
