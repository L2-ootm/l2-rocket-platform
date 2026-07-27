"""Step 4+7: Reinterpret existing telemetry and identify real landing bottleneck.

Extract signed velocity/acceleration from K550W and H180W powered runs.
Prove whether motor braking is occurring.
Identify the actual limiting mechanism.
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
os.environ.setdefault('RAYON_NUM_THREADS', '1')

import jpype
import motor_data
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, _get_anti_tumble_listener,
    _descent_alignment_diagnostic,
)

CANDIDATE = {
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


def run_and_extract(fixture, label):
    """Run simulation and extract detailed telemetry for booster branch."""
    init_or()
    ork_xml = generate_ork(fixture)
    fd, path = tempfile.mkstemp(suffix='.ork')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()
        fdt = jpype.JClass('info.openrocket.core.simulation.FlightDataType')
        FlightEvent = jpype.JClass('info.openrocket.core.simulation.FlightEvent')

        # Get booster branch (branch 1)
        br = data.getBranch(1)
        n = int(br.getLength())
        times = [float(br.get(fdt.TYPE_TIME)[i]) for i in range(n)]
        vzs = [float(br.get(fdt.TYPE_VELOCITY_Z)[i]) for i in range(n)]
        vxy = [float(br.get(fdt.TYPE_VELOCITY_XY)[i]) for i in range(n)]
        alts = [float(br.get(fdt.TYPE_ALTITUDE)[i]) for i in range(n)]
        thetas = [float(br.get(fdt.TYPE_ORIENTATION_THETA)[i]) for i in range(n)]
        masses = [float(br.get(fdt.TYPE_MASS)[i]) for i in range(n)]

        # Extract events
        events = {}
        for ev in br.getEvents():
            ename = str(ev.getType().name())
            events.setdefault(ename, []).append(float(ev.getTime()))

        sep_time = events.get('STAGE_SEPARATION', [None])[0]
        ign_times = events.get('IGNITION', [])
        burn_times = events.get('BURNOUT', [])
        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())

        # Find key moments
        # 1. At separation
        sep_idx = min(range(n), key=lambda i: abs(times[i] - sep_time)) if sep_time else 0
        # 2. At ignition (retro motor)
        retro_ign = [t for t in ign_times if t > sep_time + 0.1]  # Post-separation ignition
        ign_time = retro_ign[0] if retro_ign else None
        ign_idx = min(range(n), key=lambda i: abs(times[i] - ign_time)) if ign_time else None
        # 3. At burnout
        retro_burn = [t for t in burn_times if t > (ign_time or 0) + 0.1]
        burn_time = retro_burn[0] if retro_burn else None
        burn_idx = min(range(n), key=lambda i: abs(times[i] - burn_time)) if burn_time else None
        # 4. At impact
        hit_idx = min(range(n), key=lambda i: abs(times[i] - hit_time)) if hit_time else None

        # Compute alignment at key moments
        def get_state(idx):
            if idx is None or idx >= n:
                return None
            vx = 0  # Simplified — using vxy for horizontal
            vz = vzs[idx]
            speed = math.sqrt(vx**2 + vz**2) if abs(vz) > 0.01 else 1.0
            theta = thetas[idx]
            # Body nose axis vertical component
            nose_z = math.sin(theta)
            # alignment_q = -(nose_axis · velocity) / speed
            # For 1D vertical: axis_velocity_cosine = nose_z * vz / speed
            axis_cos = nose_z * vz / speed if speed > 1.0e-9 else 0
            q = -axis_cos
            return {
                'time_s': times[idx],
                'vz_ms': vz,
                'vxy_ms': vxy[idx],
                'speed_ms': math.sqrt(vz**2 + vxy[idx]**2),
                'theta_deg': math.degrees(theta),
                'nose_z': nose_z,
                'alignment_q': q,
                'mass_kg': masses[idx],
                'altitude_m': alts[idx],
            }

        sep_state = get_state(sep_idx)
        ign_state = get_state(ign_idx) if ign_idx else None
        burn_state = get_state(burn_idx) if burn_idx else None
        hit_state = get_state(hit_idx) if hit_idx else None

        # Compute velocity change during burn
        if ign_state and burn_state:
            dvz = burn_state['vz_ms'] - ign_state['vz_ms']
            speed_before = ign_state['speed_ms']
            speed_after = burn_state['speed_ms']
            speed_change = speed_after - speed_before
        else:
            dvz = speed_change = 0

        # Key insight: During tail-first descent, vz < 0 (downward)
        # If motor braking works, |vz| should DECREASE (become less negative)
        # So dvz should be POSITIVE (vz goes from e.g. -20 to -10)
        # A positive dvz during descent IS braking

        return {
            'label': label,
            'separation': sep_state,
            'ignition': ign_state,
            'burnout': burn_state,
            'impact': hit_state,
            'velocity_change_during_burn': {
                'dvz_ms': dvz,
                'speed_before_ms': speed_before if ign_state else None,
                'speed_after_ms': speed_after if burn_state else None,
                'speed_change_ms': speed_change,
            },
            'events': events,
            'total_branch_samples': n,
        }
    finally:
        try:
            os.unlink(path)
        except:
            pass


def main():
    print("Phase 2e: Vector Reanalysis and Bottleneck Identification")
    print("=" * 70)

    # Test 1: Free descent (no retro)
    print("\n1. FREE DESCENT (retro disabled)")
    fd = dict(CANDIDATE)
    fd['s0_retro_delay'] = 200.0
    fd['s1_retro_delay'] = 200.0
    fd_result = run_and_extract(fd, 'free_descent')

    b = fd_result
    print(f"  Separation: t={b['separation']['time_s']:.3f}s, vz={b['separation']['vz_ms']:.2f}m/s, q={b['separation']['alignment_q']:.4f}")
    if b['impact']:
        print(f"  Impact: t={b['impact']['time_s']:.3f}s, vz={b['impact']['vz_ms']:.2f}m/s, speed={b['impact']['speed_ms']:.2f}m/s")
    print(f"  Free-descent vz at apex: check booster apogee time")

    # Test 2: K550W at delay=3.0s (the "best" powered result)
    print("\n2. K550W at delay=3.0s")
    k550w = dict(CANDIDATE)
    k550w['s1_retro_delay'] = 3.0
    k550w['s0_retro_delay'] = 200.0
    k550w_result = run_and_extract(k550w, 'K550W_delay3')

    b = k550w_result
    print(f"  Separation: t={b['separation']['time_s']:.3f}s, vz={b['separation']['vz_ms']:.2f}m/s, q={b['separation']['alignment_q']:.4f}")
    if b['ignition']:
        print(f"  Ignition: t={b['ignition']['time_s']:.3f}s, vz={b['ignition']['vz_ms']:.2f}m/s, q={b['ignition']['alignment_q']:.4f}")
    if b['burnout']:
        print(f"  Burnout: t={b['burnout']['time_s']:.3f}s, vz={b['burnout']['vz_ms']:.2f}m/s, q={b['burnout']['alignment_q']:.4f}")
    if b['impact']:
        print(f"  Impact: t={b['impact']['time_s']:.3f}s, vz={b['impact']['vz_ms']:.2f}m/s, speed={b['impact']['speed_ms']:.2f}m/s")
    vc = b['velocity_change_during_burn']
    if vc['speed_before_ms'] is not None:
        print(f"  Speed before burn: {vc['speed_before_ms']:.2f}m/s")
        print(f"  Speed after burn: {vc['speed_after_ms']:.2f}m/s")
        print(f"  Speed change: {vc['speed_change_ms']:.2f}m/s")
        print(f"  dzv (vertical): {vc['dvz_ms']:.2f}m/s")
        if vc['speed_change_ms'] < 0:
            print(f"  MOTOR IS BRAKING: speed decreased by {abs(vc['speed_change_ms']):.2f}m/s")
        else:
            print(f"  MOTOR IS ACCELERATING: speed increased by {vc['speed_change_ms']:.2f}m/s")

    # Test 3: H180W at delay=4.0s
    print("\n3. H180W at delay=4.0s")
    h180w = dict(CANDIDATE)
    h180w['s1_retro'] = 7  # H180W
    h180w['s1_retro_delay'] = 4.0
    h180w['s0_retro_delay'] = 200.0
    h180w_result = run_and_extract(h180w, 'H180W_delay4')

    b = h180w_result
    print(f"  Separation: t={b['separation']['time_s']:.3f}s, vz={b['separation']['vz_ms']:.2f}m/s, q={b['separation']['alignment_q']:.4f}")
    if b['ignition']:
        print(f"  Ignition: t={b['ignition']['time_s']:.3f}s, vz={b['ignition']['vz_ms']:.2f}m/s, q={b['ignition']['alignment_q']:.4f}")
    if b['burnout']:
        print(f"  Burnout: t={b['burnout']['time_s']:.3f}s, vz={b['burnout']['vz_ms']:.2f}m/s, q={b['burnout']['alignment_q']:.4f}")
    if b['impact']:
        print(f"  Impact: t={b['impact']['time_s']:.3f}s, vz={b['impact']['vz_ms']:.2f}m/s, speed={b['impact']['speed_ms']:.2f}m/s")
    vc = b['velocity_change_during_burn']
    if vc['speed_before_ms'] is not None:
        print(f"  Speed before burn: {vc['speed_before_ms']:.2f}m/s")
        print(f"  Speed after burn: {vc['speed_after_ms']:.2f}m/s")
        print(f"  Speed change: {vc['speed_change_ms']:.2f}m/s")
        if vc['speed_change_ms'] < 0:
            print(f"  MOTOR IS BRAKING: speed decreased by {abs(vc['speed_change_ms']):.2f}m/s")
        else:
            print(f"  MOTOR IS ACCELERATING: speed increased by {vc['speed_change_ms']:.2f}m/s")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY:")
    print(f"  Free descent: impact speed = {fd_result['impact']['speed_ms']:.2f}m/s" if fd_result['impact'] else "")
    print(f"  K550W: impact speed = {k550w_result['impact']['speed_ms']:.2f}m/s" if k550w_result['impact'] else "")
    print(f"  H180W: impact speed = {h180w_result['impact']['speed_ms']:.2f}m/s" if h180w_result['impact'] else "")

    # Save artifact
    artifact = {
        'test': 'vector_reanalysis',
        'frame_contract': {
            'vz_positive': 'UPWARD (OpenRocket convention)',
            'alignment_q_plus_one': 'Tail-first (nose opposite velocity)',
            'motor_thrust': 'Nose-directed (forward along rocket axis)',
            'braking_condition': 'thrust_dot_velocity < 0 (thrust opposes velocity)',
        },
        'results': [fd_result, k550w_result, h180w_result],
    }
    with open('artifacts/phase2e/existing-run-vector-reanalysis.json', 'w') as f:
        json.dump(artifact, f, indent=2, default=str)
    print(f"\nArtifact written to artifacts/phase2e/existing-run-vector-reanalysis.json")


if __name__ == '__main__':
    main()
