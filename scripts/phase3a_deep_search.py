#!/usr/bin/env python3
"""Phase 3A — Deep search on most promising direction: large fins + forward ballast."""
import json, math, os, sys, tempfile, hashlib
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

LEGAL_S1_RETRO = {'H180W': 7, 'J350W': 14, 'J420R': 15}


def run_free_descent(params):
    p = dict(params)
    p['s1_retro_delay'] = 200.0
    ork_xml = generate_ork(p)
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

        br = data.getBranch(1)
        n = int(br.getLength())
        t_arr = br.get(fdt.TYPE_TIME)
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        vz_arr = br.get(fdt.TYPE_VELOCITY_Z)
        vxy_arr = br.get(fdt.TYPE_VELOCITY_XY)
        theta_arr = br.get(fdt.TYPE_ORIENTATION_THETA)
        phi_arr = br.get(fdt.TYPE_ORIENTATION_PHI)
        mass_arr = br.get(fdt.TYPE_MASS)
        px_arr = br.get(fdt.TYPE_POSITION_X)
        py_arr = br.get(fdt.TYPE_POSITION_Y)

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

        # Key descent metrics
        desc_samples = []
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t - 0.01 or (hit_time and t > hit_time + 0.01):
                continue
            vz = float(vz_arr[i])
            vxy = float(vxy_arr[i])
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            speed = math.sqrt(vz**2 + vxy**2)
            nose_z = math.sin(theta)

            # q_total approximation
            if i > 0 and i < n-1:
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

            desc_samples.append({"t": round(t, 3), "q": round(q_total, 4), "speed": round(speed, 2), "vxy": round(vxy, 2), "theta": round(math.degrees(theta), 1)})

        # Alignment window
        aligned = [s for s in desc_samples if s['q'] > 0.3 and s['t'] > apex_t + 0.5]
        align_window = aligned[-1]['t'] - aligned[0]['t'] if len(aligned) > 1 else 0
        mean_q = sum(s['q'] for s in desc_samples if s['t'] > apex_t + 0.5) / max(1, len([s for s in desc_samples if s['t'] > apex_t + 0.5]))

        return {
            "mach": round(mach, 4),
            "min_margin_cal": round(min_margin, 3) if min_margin != float('inf') else None,
            "s1_landing": s1_landing,
            "apex_t": round(apex_t, 4),
            "hit_t": round(hit_time, 4) if hit_time else None,
            "mean_q": round(mean_q, 4),
            "align_window_s": round(align_window, 2),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    init_or()
    results = []

    # ═══════════════════════════════════════════════════════════════
    # Deep search: large fins (the dominant variable)
    # ═══════════════════════════════════════════════════════════════
    print("=== DEEP SEARCH: Large Fins ===")

    # Test even larger fins
    for fin_height in [0.50, 0.60, 0.70, 0.80, 0.90, 1.00]:
        p = dict(BASE)
        p['s1_fin_height'] = fin_height
        p['s1_aft_ballast_kg'] = 0.0  # Remove aft ballast (it hurts)
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            results.append({
                "label": f"fin_h{fin_height}",
                "s1_fin_height": fin_height,
                "s1_aft_ballast_kg": 0.0,
                "speed": s1['total_speed'],
                "vxy": s1['vxy_ms'],
                "vz": s1['vz_ms'],
                "mach": r['mach'],
                "margin": r['min_margin_cal'],
                "mean_q": r['mean_q'],
                "align_window": r['align_window_s'],
            })
            print(f"  fin_h={fin_height}m: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_margin_cal']:.2f}, q={r['mean_q']:.3f}")
        except Exception as exc:
            print(f"  fin_h={fin_height}m: ERROR {exc}")

    # Test more fins
    print("\n=== DEEP SEARCH: Fin Count ===")
    for fin_count in [4, 6, 8]:
        p = dict(BASE)
        p['s1_fin_count'] = fin_count
        p['s1_fin_height'] = 0.70
        p['s1_aft_ballast_kg'] = 0.0
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            results.append({
                "label": f"fins{fin_count}_h0.70",
                "s1_fin_count": fin_count,
                "s1_fin_height": 0.70,
                "speed": s1['total_speed'],
                "vxy": s1['vxy_ms'],
                "mach": r['mach'],
                "margin": r['min_margin_cal'],
                "mean_q": r['mean_q'],
            })
            print(f"  fins={fin_count}, h=0.70: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_margin_cal']:.2f}")
        except Exception as exc:
            print(f"  fins={fin_count}: ERROR {exc}")

    # Test forward ballast (shifts CG forward = more stable)
    print("\n=== DEEP SEARCH: Forward Ballast ===")
    for mid_ballast in [0.0, 0.5, 1.0, 1.5, 2.0]:
        p = dict(BASE)
        p['s1_mid_ballast_kg'] = mid_ballast
        p['s1_aft_ballast_kg'] = 0.0
        p['s1_fin_height'] = 0.70
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            results.append({
                "label": f"mid_bal{mid_ballast}_fin0.70",
                "s1_mid_ballast_kg": mid_ballast,
                "s1_fin_height": 0.70,
                "speed": s1['total_speed'],
                "vxy": s1['vxy_ms'],
                "mach": r['mach'],
                "margin": r['min_margin_cal'],
                "mean_q": r['mean_q'],
            })
            print(f"  mid_bal={mid_ballast}kg, fin_h=0.70: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_margin_cal']:.2f}")
        except Exception as exc:
            print(f"  mid_bal={mid_ballast}kg: ERROR {exc}")

    # Test combined best: large fins + forward ballast + separation delay
    print("\n=== DEEP SEARCH: Combined Best ===")
    combos = [
        {"label": "best_1", "s1_fin_height": 0.80, "s1_mid_ballast_kg": 1.0, "s1_aft_ballast_kg": 0.0, "s1_separation_delay": 0.2},
        {"label": "best_2", "s1_fin_height": 0.90, "s1_mid_ballast_kg": 1.5, "s1_aft_ballast_kg": 0.0, "s1_separation_delay": 0.2},
        {"label": "best_3", "s1_fin_height": 1.00, "s1_mid_ballast_kg": 1.0, "s1_aft_ballast_kg": 0.0, "s1_separation_delay": 0.2},
        {"label": "best_4", "s1_fin_height": 0.80, "s1_fin_count": 6, "s1_mid_ballast_kg": 1.0, "s1_aft_ballast_kg": 0.0},
        {"label": "best_5", "s1_fin_height": 0.90, "s1_fin_count": 6, "s1_mid_ballast_kg": 1.5, "s1_aft_ballast_kg": 0.0},
        {"label": "best_6", "s1_fin_height": 1.00, "s1_fin_count": 6, "s1_mid_ballast_kg": 1.0, "s1_aft_ballast_kg": 0.0},
        {"label": "best_7", "s1_fin_height": 0.80, "s1_fin_root": 0.25, "s1_mid_ballast_kg": 1.0, "s1_aft_ballast_kg": 0.0},
        {"label": "best_8", "s1_fin_height": 0.90, "s1_fin_root": 0.25, "s1_mid_ballast_kg": 1.5, "s1_aft_ballast_kg": 0.0},
    ]

    for combo in combos:
        p = dict(BASE)
        p.update({k: v for k, v in combo.items() if k != 'label'})
        try:
            r = run_free_descent(p)
            s1 = r['s1_landing']
            results.append({
                "label": combo['label'],
                "params": {k: v for k, v in combo.items() if k != 'label'},
                "speed": s1['total_speed'],
                "vxy": s1['vxy_ms'],
                "mach": r['mach'],
                "margin": r['min_margin_cal'],
                "mean_q": r['mean_q'],
                "align_window": r['align_window_s'],
            })
            print(f"  {combo['label']}: speed={s1['total_speed']:.2f} m/s, vxy={s1['vxy_ms']:.2f}, margin={r['min_margin_cal']:.2f}")
        except Exception as exc:
            print(f"  {combo['label']}: ERROR {exc}")

    # Sort and save
    results.sort(key=lambda r: r.get('speed', 999))
    save("deep-topology-search.json", {
        "results": results,
        "best": results[0] if results else None,
        "summary": {
            "total_tested": len(results),
            "best_speed": results[0]['speed'] if results else None,
            "baseline_speed": 21.699,
            "improvement": round(21.699 - (results[0]['speed'] if results else 21.699), 2),
        },
    })

    print(f"\n=== BEST RESULT ===")
    if results:
        print(f"  {results[0]['label']}: {results[0]['speed']:.2f} m/s")
        print(f"  vxy={results[0].get('vxy', '?')} m/s")
        print(f"  margin={results[0].get('margin', '?')} cal")
        print(f"  Improvement: {21.699 - results[0]['speed']:.2f} m/s")


if __name__ == "__main__":
    main()
