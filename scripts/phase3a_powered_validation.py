#!/usr/bin/env python3
"""Phase 3A — Powered validation of 8-fin configuration."""
import json, math, os, sys, tempfile
os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, parse_wind_csv, WIND_CSV,
)
from motor_data import load_motor

ARTIFACTS = "artifacts/phase3a"

def save(name, data):
    with open(os.path.join(ARTIFACTS, name), 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True, default=str)
    print(f"  wrote {name}")

BASE = {
    's0_main': 14, 's1_main': 14, 's0_retro': 19, 's1_retro': 19,
    'main_cluster_count': 3, 's0_body_rad': 0.074, 's1_body_rad': 0.074,
    's0_body_len': 0.75, 's1_body_len': 0.80,
    's1_separation_delay': 0.0, 's0_retro_delay': 200.0, 's1_retro_delay': 200.0,
    'nose_mass_kg': 4.0, 'nose_ballast_pos_m': 0.45, 'nose_length_m': 0.50,
    's0_mid_ballast_kg': 0.0, 's1_mid_ballast_kg': 0.0,
    's0_aft_ballast_kg': 0.0, 's1_aft_ballast_kg': 0.5,
    's0_fin_count': 4, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's0_fin_sweep': 8.0,
    's1_fin_count': 8, 's1_fin_root': 0.22, 's1_fin_height': 0.80, 's1_fin_sweep': 5.0,
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

LEGAL_S1_RETRO = {'H180W': 7, 'J350W': 14, 'J420R': 15}


def run_powered(params):
    """Run powered simulation and extract booster landing + descent alignment."""
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

        mach = float(data.getMaxMachNumber())
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        stab0 = br0.get(fdt.TYPE_STABILITY)
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        min_margin = float('inf')
        for i in range(n0):
            s = float(stab0[i])
            if 0 < float(alt0[i]) < float(data.getMaxAltitude()) * 0.95:
                if s < min_margin and s > 0:
                    min_margin = s

        # Branch events
        branch_events = []
        for bi in range(int(data.getBranchCount())):
            br = data.getBranch(bi)
            bev = {}
            for ev in br.getEvents():
                name = str(ev.getType().name())
                bev.setdefault(name, []).append(round(float(ev.getTime()), 4))
            branch_events.append(bev)

        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)
        thrust_arr = br.get(fdt.TYPE_THRUST_FORCE)
        mass_arr = br.get(fdt.TYPE_MASS)

        apex_idx = max(range(n), key=lambda i: float(alt_arr[i]))
        apex_t = float(t_arr[apex_idx])

        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        # Landing
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
                "vz_ms": round(final_vz, 3),
                "vxy_ms": round(final_vxy, 3),
                "total_speed": round(math.sqrt(final_vz**2 + final_vxy**2), 3),
            }

        # Check if retro fired during flight
        s1_ignitions = branch_events[1].get('IGNITION', [])
        retro_fired = any(t > 0.1 and hit_time and t < hit_time for t in s1_ignitions)

        # Descent alignment during burn
        burn_start = None
        burn_end = None
        for t in s1_ignitions:
            if t > 0.1 and hit_time and t < hit_time:
                burn_start = t
                break
        for t in branch_events[1].get('BURNOUT', []):
            if burn_start and t > burn_start:
                burn_end = t
                break

        # Compute q during burn
        burn_q_values = []
        for i in range(n):
            t = float(t_arr[i])
            if burn_start and burn_end and burn_start <= t <= burn_end:
                vz = float(vz_arr[i])
                vxy = float(vxy_arr[i])
                theta = float(theta_arr[i])
                phi = float(phi_arr[i])
                speed = math.sqrt(vz**2 + vxy**2)
                nose_z = math.sin(theta)
                if i > 0:
                    dt_prev = float(t_arr[i]) - float(t_arr[i-1])
                    if dt_prev > 0:
                        vx_a = (float(px_arr[i]) - float(px_arr[i-1])) / dt_prev
                        vy_a = (float(py_arr[i]) - float(py_arr[i-1])) / dt_prev
                    else:
                        vx_a, vy_a = 0, 0
                else:
                    vx_a, vy_a = 0, 0
                cos_theta = math.cos(theta)
                nose_x = cos_theta * math.sin(phi)
                nose_y = cos_theta * math.cos(phi)
                vel_dot = nose_x * vx_a + nose_y * vy_a + nose_z * vz
                q_total = -vel_dot / max(speed, 0.01)
                burn_q_values.append(round(q_total, 4))

        mean_burn_q = sum(burn_q_values) / max(1, len(burn_q_values)) if burn_q_values else 0

        return {
            "mach": round(mach, 4),
            "min_margin_cal": round(min_margin, 3) if min_margin != float('inf') else None,
            "s1_landing": s1_landing,
            "retro_fired": retro_fired,
            "s1_ignitions": [round(t, 4) for t in s1_ignitions],
            "booster_ground_hit_s": round(hit_time, 4) if hit_time else None,
            "burn_start_s": round(burn_start, 4) if burn_start else None,
            "burn_end_s": round(burn_end, 4) if burn_end else None,
            "mean_burn_q": round(mean_burn_q, 4),
            "burn_q_values": burn_q_values[:5],
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    results = []

    # Free-descent baseline for 8-fin
    print("=== 8-FIN FREE DESCENT BASELINE ===")
    p_free = dict(BASE)
    p_free['s1_retro_delay'] = 200.0
    r_free = run_powered(p_free)
    free_speed = r_free['s1_landing']['total_speed']
    print(f"  Free descent: {free_speed:.2f} m/s (vxy={r_free['s1_landing']['vxy_ms']:.2f}, vz={r_free['s1_landing']['vz_ms']:.2f})")

    # Also test 8f_h0.65 which has better q
    for config_label, fin_h in [("8f_h0.80", 0.80), ("8f_h0.65", 0.65), ("8f_h0.70", 0.70)]:
        print(f"\n=== POWERED SEARCH: {config_label} ===")

        for motor_name, motor_idx in LEGAL_S1_RETRO.items():
            motor = load_motor(motor_name)
            best_speed = float('inf')
            best_delay = None
            all_results = []

            for delay in [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 18.0, 20.0, 25.0, 30.0, 35.0, 40.0]:
                p = dict(BASE)
                p['s1_fin_height'] = fin_h
                p['s1_retro'] = motor_idx
                p['s1_retro_delay'] = delay
                try:
                    r = run_powered(p)
                    s1 = r['s1_landing']
                    if s1:
                        all_results.append({
                            "delay": delay, "speed": s1['total_speed'],
                            "vxy": s1['vxy_ms'], "vz": s1['vz_ms'],
                            "retro_fired": r['retro_fired'],
                            "mean_burn_q": r['mean_burn_q'],
                        })
                        if s1['total_speed'] < best_speed:
                            best_speed = s1['total_speed']
                            best_delay = delay
                except Exception:
                    pass

            if best_delay is not None:
                results.append({
                    "config": config_label,
                    "motor": motor_name,
                    "best_delay_s": best_delay,
                    "best_speed_ms": round(best_speed, 3),
                    "free_descent_ms": round(free_speed, 3),
                    "improvement_ms": round(free_speed - best_speed, 2),
                    "legal_branch": best_speed < 5.0,
                    "n_runs": len(all_results),
                })
                status = "LEGAL!" if best_speed < 5.0 else f"best={best_speed:.2f}"
                print(f"  {motor_name}: {status} at delay={best_delay}s (q={all_results[0]['mean_burn_q'] if all_results else '?'})")

    # Save results
    save("powered-topology-finalists.json", {
        "gate": 9,
        "free_descent_baseline_ms": round(free_speed, 3),
        "configs_tested": ["8f_h0.80", "8f_h0.65", "8f_h0.70"],
        "results": results,
        "legal_branch_found": any(r['legal_branch'] for r in results),
        "best_result": min(results, key=lambda r: r['best_speed_ms']) if results else None,
    })

    print(f"\n=== SUMMARY ===")
    print(f"  Free descent: {free_speed:.2f} m/s")
    if results:
        best = min(results, key=lambda r: r['best_speed_ms'])
        print(f"  Best powered: {best['best_speed_ms']:.2f} m/s ({best['config']}, {best['motor']})")
        print(f"  Legal branch: {best['legal_branch']}")


if __name__ == "__main__":
    main()
