#!/usr/bin/env python3
"""Phase 2F — Test LEGAL s1 retro motors for powered landing."""
import json, math, os, sys, tempfile
os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, parse_wind_csv, WIND_CSV, MOTOR_DATABASE,
)
from motor_data import load_motor

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

# Legal s1 retro motors from mission constraint
LEGAL_S1_RETRO = {
    'H180W': 7,    # 233.7 Ns, 0.121 kg prop, 1.313s burn
    'J350W': 14,   # 689.8 Ns, 0.376 kg prop, 1.981s burn
    'J420R': 15,   # ~850 Ns, ~0.45 kg prop
}

def run_and_extract(params):
    ork_xml = generate_ork(params)
    fd, path = tempfile.mkstemp(suffix='.ork')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        sim.simulate(_get_anti_tumble_listener())
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)

        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        s1_landing = None
        if hit_time:
            idx = 0
            for i in range(1, n):
                if float(t_arr[i]) >= hit_time:
                    idx = i
                    break
            t1, t2 = float(t_arr[idx-1]), float(t_arr[idx])
            dt = t2 - t1
            if dt > 0 and t2 >= hit_time >= t1:
                f = (hit_time - t1) / dt
                final_vz = float(vz_arr[idx-1]) + f * (float(vz_arr[idx]) - float(vz_arr[idx-1]))
                final_vxy = float(vxy_arr[idx-1]) + f * (float(vxy_arr[idx]) - float(vxy_arr[idx-1]))
            else:
                final_vz = float(vz_arr[idx])
                final_vxy = float(vxy_arr[idx])
            s1_landing = {
                'vz_ms': round(final_vz, 3),
                'vxy_ms': round(final_vxy, 3),
                'total_speed': round(math.sqrt(final_vz**2 + final_vxy**2), 3),
            }

        s1_events = {}
        for ev in br.getEvents():
            name = str(ev.getType().name())
            s1_events.setdefault(name, []).append(round(float(ev.getTime()), 4))

        s1_ignitions = s1_events.get('IGNITION', [])
        retro_fired = any(t > 0.1 and hit_time and t < hit_time for t in s1_ignitions)

        return {
            's1_landing': s1_landing,
            'retro_fired_in_flight': retro_fired,
            's1_ignitions': [round(t, 4) for t in s1_ignitions],
            'booster_ground_hit_s': round(hit_time, 4) if hit_time else None,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()

    # Print motor data
    print("=== LEGAL S1 RETRO MOTORS ===")
    for name, idx in LEGAL_S1_RETRO.items():
        motor = load_motor(name)
        print(f"  {name}: impulse={motor.total_impulse_ns:.1f} Ns, "
              f"burn={motor.burn_duration_s:.3f}s, prop={motor.propellant_mass_kg:.4f} kg, "
              f"loaded={motor.loaded_mass_kg:.4f} kg")

    # Free descent baseline
    print("\n=== FREE DESCENT BASELINE ===")
    p_free = dict(BEST)
    p_free['s1_retro_delay'] = 200.0
    r_free = run_and_extract(p_free)
    free_speed = r_free['s1_landing']['total_speed']
    print(f"  Free descent: {free_speed:.3f} m/s (vz={r_free['s1_landing']['vz_ms']}, vxy={r_free['s1_landing']['vxy_ms']})")

    # Test each legal motor
    for motor_name, motor_idx in LEGAL_S1_RETRO.items():
        print(f"\n=== {motor_name} (index {motor_idx}) ===")
        motor = load_motor(motor_name)

        # Search delays from apex (~9s) to near ground hit
        delays = list(range(9, 40, 2))
        results = []

        for delay in delays:
            p = dict(BEST)
            p['s1_retro'] = motor_idx
            p['s1_retro_delay'] = float(delay)
            try:
                r = run_and_extract(p)
                spd = r['s1_landing']['total_speed'] if r['s1_landing'] else 0
                status = "FIRED" if r['retro_fired_in_flight'] else "NO FIRE"
                results.append({
                    'delay_s': delay,
                    'speed_ms': spd,
                    'vz_ms': r['s1_landing']['vz_ms'] if r['s1_landing'] else None,
                    'vxy_ms': r['s1_landing']['vxy_ms'] if r['s1_landing'] else None,
                    'retro_fired': r['retro_fired_in_flight'],
                })
                marker = " <-- BEST" if spd == min(x['speed_ms'] for x in results if x['speed_ms']) else ""
                print(f"  delay={delay:2d}s  speed={spd:6.2f} m/s  [{status}]{marker}")
            except Exception as exc:
                print(f"  delay={delay:2d}s  ERROR: {exc}")

        valid = [r for r in results if r['speed_ms'] is not None]
        if valid:
            best = min(valid, key=lambda r: r['speed_ms'])
            print(f"  BEST: delay={best['delay_s']}s, speed={best['speed_ms']:.3f} m/s")

            # Refinement
            if best['speed_ms'] < 50:
                print(f"  --- Refinement around {best['delay_s']}s ---")
                for offset in [-0.5, -0.2, -0.1, 0.1, 0.2, 0.5]:
                    ref_delay = best['delay_s'] + offset
                    if ref_delay < 9 or ref_delay > 43:
                        continue
                    p = dict(BEST)
                    p['s1_retro'] = motor_idx
                    p['s1_retro_delay'] = ref_delay
                    try:
                        r = run_and_extract(p)
                        spd = r['s1_landing']['total_speed'] if r['s1_landing'] else 0
                        status = "FIRED" if r['retro_fired_in_flight'] else "NO FIRE"
                        print(f"    delay={ref_delay:.1f}s  speed={spd:6.2f} m/s  [{status}]")
                        results.append({'delay_s': ref_delay, 'speed_ms': spd,
                                       'vz_ms': r['s1_landing']['vz_ms'] if r['s1_landing'] else None,
                                       'vxy_ms': r['s1_landing']['vxy_ms'] if r['s1_landing'] else None,
                                       'retro_fired': r['retro_fired_in_flight']})
                    except Exception as exc:
                        print(f"    delay={ref_delay:.1f}s  ERROR: {exc}")

    print("\n=== CONCLUSION ===")
    print(f"Free descent speed: {free_speed:.3f} m/s")
    print(f"Target: < 5.0 m/s total speed for BOTH stages")


if __name__ == '__main__':
    main()
