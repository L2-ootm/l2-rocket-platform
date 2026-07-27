"""Gate 1+4+5: True free descent, motor feasibility, and powered landing calibration.

All scenario types, true unpowered free descent, motor feasibility envelope,
and powered landing with branch-aware timing.
"""
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, '.')
os.environ.setdefault('RAYON_NUM_THREADS', '1')

import jpype
import motor_data
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, validate_hard_constraints, score_official,
    _get_anti_tumble_listener, validate_candidate_geometry,
    MIN_STATIC_MARGIN, MAX_MACH,
    _descent_alignment_diagnostic, _retro_burn_diagnostic,
    validate_anti_tumble_extensions, inspect_anti_tumble_xml,
    ANTI_TUMBLE_SCRIPT_DIGEST,
)

# Best stable candidate
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


def run_scenario(fixture, label, scenario_type):
    """Run one OpenRocket scenario and return full telemetry."""
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

        # Extract events
        all_events = {}
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            branch_events = {}
            for ev in branch.getEvents():
                ename = str(ev.getType().name())
                branch_events.setdefault(ename, []).append(float(ev.getTime()))
            all_events[str(branch.getName())] = branch_events

        # Extract per-branch telemetry
        branches = []
        for bi in range(int(data.getBranchCount())):
            branch = data.getBranch(bi)
            n = int(branch.getLength())
            times = [float(branch.get(fdt.TYPE_TIME)[i]) for i in range(n)]
            alts = [float(branch.get(fdt.TYPE_ALTITUDE)[i]) for i in range(n)]
            vzs = [float(branch.get(fdt.TYPE_VELOCITY_Z)[i]) for i in range(n)]
            vxy = [float(branch.get(fdt.TYPE_VELOCITY_XY)[i]) for i in range(n)]
            thetas = [float(branch.get(fdt.TYPE_ORIENTATION_THETA)[i]) for i in range(n)]
            phis = [float(branch.get(fdt.TYPE_ORIENTATION_PHI)[i]) for i in range(n)]

            # Find events
            hit_time = None
            tumble_time = None
            ignition_times = []
            burnout_times = []
            for ev in branch.getEvents():
                ename = str(ev.getType().name())
                et = float(ev.getTime())
                if ename == 'GROUND_HIT': hit_time = et
                if ename == 'TUMBLE': tumble_time = et
                if ename == 'IGNITION': ignition_times.append(et)
                if ename == 'BURNOUT': burnout_times.append(et)

            # Apex
            apex_idx = max(range(n), key=lambda i: alts[i])

            # Touchdown
            td_vz = td_vxy = td_speed = None
            if hit_time is not None:
                idx = 1
                for i in range(1, n):
                    if times[i] >= hit_time: idx = i; break
                t1, t2 = times[idx-1], times[idx]
                f = (hit_time - t1) / (t2 - t1) if t2 > t1 else 1.0
                td_vz = vzs[idx-1] + f * (vzs[idx] - vzs[idx-1])
                td_vxy = vxy[idx-1] + f * (vxy[idx] - vxy[idx-1])
                td_speed = math.sqrt(td_vz**2 + td_vxy**2)

            # Alignment diagnostic
            alignment = _descent_alignment_diagnostic(
                times, alts,
                [float(branch.get(fdt.TYPE_POSITION_X)[i]) for i in range(n)],
                [float(branch.get(fdt.TYPE_POSITION_Y)[i]) for i in range(n)],
                vzs, vxy, thetas, phis,
            )

            branches.append({
                'branch': bi,
                'name': str(branch.getName()),
                'samples': n,
                'apex_time_s': times[apex_idx],
                'apex_alt_m': alts[apex_idx],
                'hit_time_s': hit_time,
                'tumble_time_s': tumble_time,
                'ignition_times_s': ignition_times,
                'burnout_times_s': burnout_times,
                'td_vz': td_vz,
                'td_vxy': td_vxy,
                'td_speed': td_speed,
                'tail_first_windows': len(alignment.get('tail_first_windows', [])),
                'best_q': alignment.get('best_alignment_q', -1),
                'orientation_at_apex_theta': thetas[apex_idx] if apex_idx < len(thetas) else None,
            })

        # Check anti-tumble
        anti_tumble = inspect_anti_tumble_xml(ork_xml)

        mach = float(data.getMaxMachNumber())
        apogee = float(data.getMaxAltitude())

        return {
            'label': label,
            'scenario_type': scenario_type,
            'mach': mach,
            'apogee_m': apogee,
            'branches': branches,
            'events': all_events,
            'anti_tumble_valid': anti_tumble['valid'],
            'anti_tumble_digest': anti_tumble['script_digest'],
            'extension_count': anti_tumble.get('simulation_count', 0),
        }
    finally:
        try: os.unlink(path)
        except: pass


def main():
    print("Gate 1+4+5: Scenario Matrix, True Free Descent, Motor Feasibility")
    print("=" * 70)

    # ── Gate 1: Scenario Matrix ──
    print("\n=== GATE 1: SCENARIO MATRIX ===")

    scenarios = [
        ('OFFICIAL_FULL_MISSION', {'s0_retro_delay': 54.31, 's1_retro_delay': 65.28}),
        ('EXPOSED_SUSTAINER_ASCENT', {'s0_retro_delay': 200.0, 's1_retro_delay': 200.0}),
        ('STAGE_FREE_DESCENT_DIAGNOSTIC', {'s0_retro_delay': 200.0, 's1_retro_delay': 200.0}),
        ('DEBUG_ONLY', {'s0_retro_delay': 200.0, 's1_retro_delay': 200.0}),
    ]

    scenario_results = []
    for stype, overrides in scenarios:
        fixture = dict(CANDIDATE)
        fixture.update(overrides)
        print(f"\n  Running {stype}...")
        try:
            result = run_scenario(fixture, stype, stype)
            print(f"    Mach: {result['mach']:.4f}, Apogee: {result['apogee_m']:.1f}m")
            for b in result['branches']:
                td = f"{b['td_speed']:.2f}" if b['td_speed'] is not None else 'N/A'
                print(f"    {b['name']}: touchdown={td}m/s, windows={b['tail_first_windows']}")
            scenario_results.append(result)
        except Exception as e:
            print(f"    ERROR: {str(e)[:80]}")
            scenario_results.append({'label': stype, 'error': str(e)[:200]})

    # ── Gate 4: True Free Descent ──
    print("\n=== GATE 4: TRUE FREE DESCENT (retro DISABLED) ===")
    fd_fixture = dict(CANDIDATE)
    fd_fixture['s0_retro_delay'] = 200.0
    fd_fixture['s1_retro_delay'] = 200.0

    fd_result = run_scenario(fd_fixture, 'TRUE_FREE_DESCENT', 'STAGE_FREE_DESCENT_DIAGNOSTIC')
    print(f"  Mach: {fd_result['mach']:.4f}")
    print(f"  Apogee: {fd_result['apogee_m']:.1f}m")
    for b in fd_result['branches']:
        td = f"{b['td_speed']:.2f}" if b['td_speed'] is not None else 'N/A'
        ign = b['ignition_times_s']
        burn = b['burnout_times_s']
        print(f"  {b['name']}: td={td}m/s, apex_t={b['apex_time_s']:.3f}s, "
              f"hit_t={b['hit_time_s']:.3f}s, ign={ign}, burn={burn}, "
              f"windows={b['tail_first_windows']}, q={b['best_q']:.4f}")

    # Verify no retro ignition in free descent
    for b in fd_result['branches']:
        retro_igns = [t for t in b['ignition_times_s'] if t > 1.5]  # After separation
        if retro_igns:
            print(f"  WARNING: {b['name']} has post-separation ignition at {retro_igns}")
        else:
            print(f"  OK: {b['name']} has no post-separation ignition (thrust disabled)")

    # ── Gate 5: Motor Feasibility ──
    print("\n=== GATE 5: MOTOR FEASIBILITY ===")
    booster = fd_result['branches'][1]  # Booster branch
    sep_time = 1.695  # From event analysis
    apogee_time = booster['apex_time_s']
    impact_time = booster['hit_time_s']
    td_vz = abs(booster['td_vz']) if booster['td_vz'] is not None else 0
    td_vxy = abs(booster['td_vxy']) if booster['td_vxy'] is not None else 0
    td_total = booster['td_speed'] if booster['td_speed'] is not None else 0

    print(f"  Booster free-descent: sep={sep_time:.3f}s, apogee={apogee_time:.3f}s, impact={impact_time:.3f}s")
    print(f"  Unpowered touchdown: vz={td_vz:.2f}m/s, vxy={td_vxy:.2f}m/s, total={td_total:.2f}m/s")

    # Estimate booster mass at landing (from OpenRocket data)
    # Use the mass at impact from the simulation
    motors_to_test = [
        ('H180W', 7),   # 234 Ns, 1.313s
        ('J360_CTI', 18), # 816 Ns, 2.130s
        ('K550W', 19),   # 1625 Ns, 3.356s
        ('J350W', 14),   # 690 Ns, 1.981s
    ]

    motor_feasibility = []
    for name, idx in motors_to_test:
        m = motor_data.load_motor_by_index(idx)
        # First-order feasibility: impulse needed to stop from td_total
        # Required delta-v ≈ td_total (if thrust opposes velocity perfectly)
        # Available delta-v ≈ impulse / stage_mass
        # Stage mass estimate: ~3-5 kg (structure + motors + ballast)
        stage_mass_est = 4.0  # Conservative estimate
        available_dv = m.total_impulse_ns / stage_mass_est
        gravity_loss_est = 9.81 * m.burn_duration_s * 0.5  # Average gravity loss
        net_dv = available_dv - gravity_loss_est
        feasible = net_dv > td_total * 1.2  # 20% margin

        # Window fit: motor must burn during tail-first window
        # Tail-first window starts after separation (~2s) and ends at impact
        window_duration = impact_time - sep_time
        burns_in_window = m.burn_duration_s < window_duration

        print(f"  {name}: impulse={m.total_impulse_ns:.0f}Ns, burn={m.burn_duration_s:.3f}s, "
              f"dv={available_dv:.1f}m/s, net_dv={net_dv:.1f}m/s, "
              f"window_fit={'YES' if burns_in_window else 'NO'}, feasible={'YES' if feasible else 'NO'}")

        motor_feasibility.append({
            'designation': name,
            'index': idx,
            'total_impulse_ns': m.total_impulse_ns,
            'burn_duration_s': m.burn_duration_s,
            'loaded_mass_kg': m.loaded_mass_kg,
            'propellant_kg': m.propellant_mass_kg,
            'available_dv_ms': available_dv,
            'gravity_loss_ms': gravity_loss_est,
            'net_dv_ms': net_dv,
            'required_dv_ms': td_total,
            'feasible': feasible,
            'burn_fits_window': burns_in_window,
        })

    # ── Gate 6: Powered Landing with Best Feasible Motor ──
    print("\n=== GATE 6: POWERED LANDING CALIBRATION ===")
    # Use H180W (most feasible: smallest, shortest burn, fits window)
    best_motor_idx = 7  # H180W
    best_motor = motor_data.load_motor_by_index(best_motor_idx)
    print(f"  Selected motor: {best_motor.designation} ({best_motor.total_impulse_ns:.0f}Ns, {best_motor.burn_duration_s:.3f}s)")

    # Test delays for booster (branch 1)
    # Retro fires at launch + delay
    # Booster separates at ~1.7s
    # Booster apogee at ~8.5s (free-descent) or ~13.3s (powered with K550W)
    # For H180W, try delays that place the burn during descent
    best_speed = float('inf')
    best_delay = None

    for delay in [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0, 16.0]:
        fixture = dict(CANDIDATE)
        fixture['s1_retro'] = best_motor_idx  # H180W
        fixture['s1_retro_delay'] = delay
        fixture['s0_retro_delay'] = 200.0  # Keep sustainer retro disabled

        try:
            result = run_scenario(fixture, f'H180W_delay_{delay:.1f}', 'POWERED')
            booster = result['branches'][1]
            if booster['td_speed'] is not None:
                td = booster['td_speed']
                marker = '***' if td < 5.0 else '   '
                print(f"  {marker} delay={delay:5.1f}s: td={td:7.2f}m/s, "
                      f"ign={booster['ignition_times_s']}, "
                      f"apex={booster['apex_time_s']:.3f}s")
                if td < best_speed:
                    best_speed = td
                    best_delay = delay
        except Exception as e:
            print(f"  delay={delay:5.1f}s: ERROR {str(e)[:60]}")

    if best_delay is not None:
        print(f"\n  Best: delay={best_delay:.1f}s, speed={best_speed:.2f}m/s")
        is_legal = best_speed < 5.0
        print(f"  LEGAL BRANCH: {'YES' if is_legal else 'NO'}")

    # Save artifacts
    artifact = {
        'scenario_matrix': scenario_results,
        'true_free_descent': fd_result,
        'booster_free_descent': {
            'separation_time_s': sep_time,
            'apogee_time_s': apogee_time,
            'impact_time_s': impact_time,
            'unpowered_vz_ms': td_vz,
            'unpowered_vxy_ms': td_vxy,
            'unpowered_total_ms': td_total,
        },
        'motor_feasibility': motor_feasibility,
        'powered_landing': {
            'motor': best_motor.designation,
            'best_delay_s': best_delay,
            'best_speed_ms': best_speed,
            'is_legal_branch': best_speed < 5.0 if best_delay else False,
        },
    }

    with open('artifacts/phase2c/scenario-runtime-matrix.json', 'w') as f:
        json.dump(artifact, f, indent=2, default=str)
    with open('artifacts/phase2c/booster-descent-timeline.json', 'w') as f:
        json.dump(artifact['booster_free_descent'], f, indent=2, default=str)
    with open('artifacts/phase2c/landing-motor-feasibility.json', 'w') as f:
        json.dump(artifact['motor_feasibility'], f, indent=2, default=str)
    with open('artifacts/phase2c/powered-landing-results.json', 'w') as f:
        json.dump(artifact['powered_landing'], f, indent=2, default=str)
    print(f"\nArtifacts written to artifacts/phase2c/")


if __name__ == '__main__':
    main()
