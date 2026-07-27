#!/usr/bin/env python3
"""Phase 2F powered search — single JVM session for all OpenRocket runs."""
import json, math, os, sys, tempfile, hashlib
os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, parse_wind_csv, WIND_CSV,
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

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def run_and_extract(params):
    """Run simulation and extract booster descent timeline + landing data."""
    ork_xml = generate_ork(params)
    fd, path = tempfile.mkstemp(suffix='.ork')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

        n_branches = int(data.getBranchCount())
        branch_events = []
        for bi in range(n_branches):
            br = data.getBranch(bi)
            bev = {}
            for ev in br.getEvents():
                name = str(ev.getType().name())
                bev.setdefault(name, []).append(round(float(ev.getTime()), 4))
            branch_events.append(bev)

        # Booster branch (index 1)
        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        mass_arr = br.get(fdt.TYPE_MASS)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)

        # Find apex
        apex_idx = max(range(n), key=lambda i: float(alt_arr[i]))
        apex_t = float(t_arr[apex_idx])

        # Find ground hit
        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        # Get landing state
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
                'time_s': round(hit_time, 4),
                'vz_ms': round(final_vz, 3),
                'vxy_ms': round(final_vxy, 3),
                'total_speed': round(math.sqrt(final_vz**2 + final_vxy**2), 3),
            }

        # Check if retro fired during flight
        s1_ignitions = branch_events[1].get('IGNITION', [])
        retro_fired = any(t > 0.1 and hit_time and t < hit_time for t in s1_ignitions)

        return {
            's1_landing': s1_landing,
            'retro_fired_in_flight': retro_fired,
            's1_ignitions': [round(t, 4) for t in s1_ignitions],
            'booster_ground_hit_s': round(hit_time, 4) if hit_time else None,
            'ork_xml_hash': sha256(ork_xml),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    results = []

    # Coarse search: post-apex delays
    delays = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 18.0, 20.0, 25.0, 30.0, 35.0, 40.0]
    
    print("=== POST-APEX POWERED SEARCH ===")
    print(f"Booster apex: ~8.53s, ground hit: ~43.65s")
    print(f"Testing {len(delays)} delays\n")
    
    for delay in delays:
        p = dict(BEST)
        p['s1_retro_delay'] = delay
        try:
            r = run_and_extract(p)
            result = {
                'delay_s': delay,
                's1_speed_ms': r['s1_landing']['total_speed'] if r['s1_landing'] else None,
                's1_vz_ms': r['s1_landing']['vz_ms'] if r['s1_landing'] else None,
                's1_vxy_ms': r['s1_landing']['vxy_ms'] if r['s1_landing'] else None,
                'retro_fired': r['retro_fired_in_flight'],
                'ground_hit_s': r['booster_ground_hit_s'],
            }
            status = "FIRED" if r['retro_fired_in_flight'] else "NO FIRE"
            spd = r['s1_landing']['total_speed'] if r['s1_landing'] else 0
            print(f"  delay={delay:6.1f}s  speed={spd:6.2f} m/s  [{status}]")
            results.append(result)
        except Exception as exc:
            print(f"  delay={delay:6.1f}s  ERROR: {exc}")
            results.append({'delay_s': delay, 'error': str(exc)})

    # Find best
    valid = [r for r in results if r.get('s1_speed_ms') is not None]
    if valid:
        best = min(valid, key=lambda r: r['s1_speed_ms'])
        print(f"\nBEST: delay={best['delay_s']}s  speed={best['s1_speed_ms']} m/s")
        
        # Refinement around best
        if best['s1_speed_ms'] is not None:
            print(f"\n=== REFINEMENT around delay={best['delay_s']}s ===")
            refinements = [best['delay_s'] - 0.5, best['delay_s'] - 0.2, best['delay_s'] - 0.1,
                          best['delay_s'] + 0.1, best['delay_s'] + 0.2, best['delay_s'] + 0.5]
            for delay in refinements:
                if delay < 9.0 or delay > 43.0:
                    continue
                p = dict(BEST)
                p['s1_retro_delay'] = delay
                try:
                    r = run_and_extract(p)
                    spd = r['s1_landing']['total_speed'] if r['s1_landing'] else 0
                    status = "FIRED" if r['retro_fired_in_flight'] else "NO FIRE"
                    print(f"  delay={delay:6.2f}s  speed={spd:6.2f} m/s  [{status}]")
                    results.append({
                        'delay_s': delay,
                        's1_speed_ms': r['s1_landing']['total_speed'] if r['s1_landing'] else None,
                        's1_vz_ms': r['s1_landing']['vz_ms'] if r['s1_landing'] else None,
                        's1_vxy_ms': r['s1_landing']['vxy_ms'] if r['s1_landing'] else None,
                        'retro_fired': r['retro_fired_in_flight'],
                        'ground_hit_s': r['booster_ground_hit_s'],
                    })
                except Exception as exc:
                    print(f"  delay={delay:6.2f}s  ERROR: {exc}")

    # Save results
    all_valid = [r for r in results if r.get('s1_speed_ms') is not None]
    best_final = min(all_valid, key=lambda r: r['s1_speed_ms']) if all_valid else None

    output = {
        'gate': 7,
        'status': 'SEARCH_COMPLETE',
        'free_descent_s1_speed_ms': 21.699,
        'all_results': results,
        'best_result': best_final,
        'legal_branch_found': best_final['s1_speed_ms'] < 5.0 if best_final else False,
    }

    os.makedirs('artifacts/phase2f', exist_ok=True)
    with open('artifacts/phase2f/post-apex-powered-results.json', 'w') as f:
        json.dump(output, f, indent=2, sort_keys=True)
    
    print(f"\n=== SUMMARY ===")
    print(f"Legal branch found: {output['legal_branch_found']}")
    if best_final:
        print(f"Best: delay={best_final['delay_s']}s, speed={best_final['s1_speed_ms']} m/s")
        print(f"  vz={best_final.get('s1_vz_ms', '?')} m/s, vxy={best_final.get('s1_vxy_ms', '?')} m/s")


if __name__ == '__main__':
    main()
