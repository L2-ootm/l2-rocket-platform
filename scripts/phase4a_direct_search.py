#!/usr/bin/env python3
"""Phase 4A — Direct Design-Discovery Campaign.

Extract teacher phenotype from illegal ORK, build legal families,
search for legal booster branch.
"""
import json, math, os, sys, tempfile, hashlib, time
os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener, parse_wind_csv, WIND_CSV, MOTOR_DATABASE,
    _falcon_cluster_geometry,
)
from motor_data import load_motor

ARTIFACTS = "artifacts/phase4a"
os.makedirs(ARTIFACTS, exist_ok=True)
EVAL_COUNT = {"structural": 0, "powered": 0}

def save(name, data):
    with open(os.path.join(ARTIFACTS, name), 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True, default=str)

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# ═══════════════════════════════════════════════════════════════
# TEACHER PHENOTYPE (from illegal 839k/850k ORK)
# ═══════════════════════════════════════════════════════════════
TEACHER = {
    "horizontal_speed_near_ignition_mps": 6.0,
    "q_total_near_ignition": 0.998,
    "angular_rate_near_ignition_rad_s": 0.0,
    "horizontal_speed_at_separation_mps": 6.0,
    "landing_ignition_altitude_booster_m": 34.0,
    "landing_ignition_altitude_sustainer_m": 80.0,
    "apogee_m": 3000.0,
    "max_mach": 0.85,
    "ascent_margin_cal": 2.0,
}

def behavior_distance(candidate, teacher=TEACHER):
    """Compute weighted distance from candidate to teacher phenotype."""
    w = {"vh": 1.0, "q": 2.0, "ar": 0.5, "wd": 0.3, "ri": 0.5}
    d_vh = abs(candidate.get("vh_separation", 20) - teacher["horizontal_speed_near_ignition_mps"]) / 20.0
    d_q = abs(candidate.get("q_mean", 0) - teacher["q_total_near_ignition"]) / 1.0
    d_ar = abs(candidate.get("ar_ignition", 5) - teacher["angular_rate_near_ignition_rad_s"]) / 5.0
    d_wd = max(0, (10 - candidate.get("window_s", 0))) / 10.0
    d_ri = max(0, (100 - candidate.get("opposing_impulse_pct", 0))) / 100.0
    return w["vh"]*d_vh + w["q"]*d_q + w["ar"]*d_ar + w["wd"]*d_wd + w["ri"]*d_ri


# ═══════════════════════════════════════════════════════════════
# SIMULATION INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════
def run_full(params, label="candidate"):
    """Run full OpenRocket simulation. Returns detailed metrics."""
    EVAL_COUNT["structural"] += 1
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
        apogee = float(data.getMaxAltitude())

        # Ascent stability
        br0 = data.getBranch(0)
        n0 = int(br0.getLength())
        stab0 = br0.get(fdt.TYPE_STABILITY)
        alt0 = br0.get(fdt.TYPE_ALTITUDE)
        min_margin = float('inf')
        for i in range(n0):
            s = float(stab0[i])
            if 0 < float(alt0[i]) < apogee * 0.95:
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

        # Booster branch
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
        mass_arr = br.get(fdt.TYPE_MASS)

        apex_idx = max(range(n), key=lambda i: float(alt_arr[i]))
        apex_t = float(t_arr[apex_idx])
        apex_alt = float(alt_arr[apex_idx])

        hit_time = None
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                hit_time = float(ev.getTime())
                break

        # Separation time
        sep_times = branch_events[1].get("STAGE_SEPARATION", [])
        sep_t = min(sep_times) if sep_times else None

        # Check staging legality
        apogee_times = branch_events[0].get("APOGEE", [])
        first_apogee = min(apogee_times) if apogee_times else float('inf')
        staging_legal = sep_t is not None and sep_t < first_apogee

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

        # Separation state
        sep_state = None
        if sep_t:
            idx = 0
            for i in range(1, n):
                if float(t_arr[i]) >= sep_t:
                    idx = i
                    break
            sep_state = {
                "time_s": round(sep_t, 4),
                "vxy_ms": round(float(vxy_arr[idx]), 3),
                "vz_ms": round(float(vz_arr[idx]), 3),
                "theta_deg": round(math.degrees(float(theta_arr[idx])), 2),
                "altitude_m": round(float(alt_arr[idx]), 2),
            }

        # Descent alignment (after apex)
        desc_q = []
        for i in range(n):
            t = float(t_arr[i])
            if t < apex_t + 0.5 or (hit_time and t > hit_time - 0.5):
                continue
            vz = float(vz_arr[i])
            vxy = float(vxy_arr[i])
            theta = float(theta_arr[i])
            phi = float(phi_arr[i])
            speed = math.sqrt(vz**2 + vxy**2)
            if speed < 0.5:
                continue

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
            nose_z = math.sin(theta)
            vel_dot = nose_x * vx_a + nose_y * vy_a + nose_z * vz
            q_total = -vel_dot / max(speed, 0.01)
            desc_q.append({"t": round(t, 3), "q": round(q_total, 4), "vxy": round(vxy, 2)})

        mean_q = sum(d["q"] for d in desc_q) / max(1, len(desc_q))
        min_q = min(d["q"] for d in desc_q) if desc_q else 0
        window_s = sum(1 for d in desc_q if d["q"] > 0.3) * 0.1  # approximate

        return {
            "label": label,
            "mach": round(mach, 4),
            "apogee_m": round(apogee, 2),
            "min_margin_cal": round(min_margin, 3) if min_margin != float('inf') else None,
            "staging_legal": staging_legal,
            "sep_time_s": round(sep_t, 4) if sep_t else None,
            "first_apogee_s": round(first_apogee, 4),
            "sep_state": sep_state,
            "s1_landing": s1_landing,
            "apex_t": round(apex_t, 4),
            "hit_t": round(hit_time, 4) if hit_time else None,
            "mean_q": round(mean_q, 4),
            "min_q": round(min_q, 4),
            "window_s": round(window_s, 1),
            "n_desc_samples": len(desc_q),
            "branch_events": branch_events,
            "ork_hash": sha256(ork_xml),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════
# FAMILY SEEDS
# ═══════════════════════════════════════════════════════════════
def make_base(s0_retro_delay=200.0, s1_retro_delay=200.0):
    return {
        's0_main': 14, 's1_main': 14, 's0_retro': 19, 's1_retro': 19,
        'main_cluster_count': 3, 's0_body_rad': 0.074, 's1_body_rad': 0.074,
        's0_body_len': 0.75, 's1_body_len': 0.80,
        's1_separation_delay': 0.0, 's0_retro_delay': s0_retro_delay, 's1_retro_delay': s1_retro_delay,
        'nose_mass_kg': 4.0, 'nose_ballast_pos_m': 0.45, 'nose_length_m': 0.50,
        's0_mid_ballast_kg': 0.0, 's1_mid_ballast_kg': 0.0,
        's0_aft_ballast_kg': 0.0, 's1_aft_ballast_kg': 0.0,
        's0_fin_count': 4, 's0_fin_root': 0.15, 's0_fin_height': 0.20, 's0_fin_sweep': 8.0,
        's1_fin_count': 8, 's1_fin_root': 0.22, 's1_fin_height': 0.70, 's1_fin_sweep': 5.0,
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


# ═══════════════════════════════════════════════════════════════
# STAGE 1: FAMILY EXPLORATION
# ═══════════════════════════════════════════════════════════════
def stage1_family_exploration():
    print("=== STAGE 1: Family Exploration ===")
    results = {}

    # Family H — Historical physical phenotype
    print("\n--- Family H: Historical Physical Phenotype ---")
    h_seeds = [
        {"label": "H1", "s1_fin_count": 4, "s1_fin_height": 0.38, "s1_fin_root": 0.22, "s0_fin_count": 4, "s0_fin_height": 0.20, "s1_aft_ballast_kg": 0.0},
        {"label": "H2", "s1_fin_count": 4, "s1_fin_height": 0.50, "s1_fin_root": 0.25, "s0_fin_count": 4, "s0_fin_height": 0.25, "s1_aft_ballast_kg": 0.0},
        {"label": "H3", "s1_fin_count": 4, "s1_fin_height": 0.60, "s1_fin_root": 0.25, "s0_fin_count": 4, "s0_fin_height": 0.30, "s1_aft_ballast_kg": 0.0},
        {"label": "H4", "s1_fin_count": 4, "s1_fin_height": 0.40, "s1_fin_root": 0.22, "s0_fin_count": 4, "s0_fin_height": 0.20, "s1_aft_ballast_kg": 0.0, "s1_body_len": 0.90},
        {"label": "H5", "s1_fin_count": 4, "s1_fin_height": 0.50, "s1_fin_root": 0.22, "s0_fin_count": 4, "s0_fin_height": 0.25, "s1_aft_ballast_kg": 0.0, "s1_body_len": 1.00},
    ]
    h_results = []
    for seed in h_seeds:
        p = make_base()
        p.update({k: v for k, v in seed.items() if k != 'label'})
        try:
            r = run_full(p, seed["label"])
            r["family"] = "H"
            r["bd"] = behavior_distance(r)
            h_results.append(r)
            print(f"  {seed['label']}: speed={r['s1_landing']['total_speed']:.2f} q={r['mean_q']:.3f} bd={r['bd']:.3f} sep_legal={r['staging_legal']}")
        except Exception as exc:
            print(f"  {seed['label']}: ERROR {exc}")
    results["H"] = sorted(h_results, key=lambda r: r.get("bd", 999))

    # Family E8 — Eight aft fins
    print("\n--- Family E8: Eight Aft Fins ---")
    e8_seeds = [
        {"label": "E8_1", "s1_fin_count": 8, "s1_fin_height": 0.60, "s1_fin_sweep": 5.0},
        {"label": "E8_2", "s1_fin_count": 8, "s1_fin_height": 0.65, "s1_fin_sweep": 5.0},
        {"label": "E8_3", "s1_fin_count": 8, "s1_fin_height": 0.70, "s1_fin_sweep": 5.0},
        {"label": "E8_4", "s1_fin_count": 8, "s1_fin_height": 0.75, "s1_fin_sweep": 3.0},
        {"label": "E8_5", "s1_fin_count": 8, "s1_fin_height": 0.80, "s1_fin_sweep": 5.0},
        {"label": "E8_6", "s1_fin_count": 8, "s1_fin_height": 0.65, "s1_fin_sweep": 0.0},
        {"label": "E8_7", "s1_fin_count": 8, "s1_fin_height": 0.70, "s1_fin_sweep": 0.0},
        {"label": "E8_8", "s1_fin_count": 8, "s1_fin_height": 0.70, "s1_fin_sweep": 5.0, "s1_body_len": 1.0},
    ]
    e8_results = []
    for seed in e8_seeds:
        p = make_base()
        p.update({k: v for k, v in seed.items() if k != 'label'})
        try:
            r = run_full(p, seed["label"])
            r["family"] = "E8"
            r["bd"] = behavior_distance(r)
            e8_results.append(r)
            print(f"  {seed['label']}: speed={r['s1_landing']['total_speed']:.2f} q={r['mean_q']:.3f} bd={r['bd']:.3f} sep_legal={r['staging_legal']}")
        except Exception as exc:
            print(f"  {seed['label']}: ERROR {exc}")
    results["E8"] = sorted(e8_results, key=lambda r: r.get("bd", 999))

    # Family D — Distributed aerodynamic area
    print("\n--- Family D: Distributed Aerodynamic Area ---")
    d_seeds = [
        {"label": "D1", "s1_fin_count": 6, "s1_fin_height": 0.50, "s0_fin_count": 6, "s0_fin_height": 0.30},
        {"label": "D2", "s1_fin_count": 6, "s1_fin_height": 0.60, "s0_fin_count": 6, "s0_fin_height": 0.35},
        {"label": "D3", "s1_fin_count": 8, "s1_fin_height": 0.50, "s0_fin_count": 4, "s0_fin_height": 0.35},
        {"label": "D4", "s1_fin_count": 8, "s1_fin_height": 0.60, "s0_fin_count": 4, "s0_fin_height": 0.40},
    ]
    d_results = []
    for seed in d_seeds:
        p = make_base()
        p.update({k: v for k, v in seed.items() if k != 'label'})
        try:
            r = run_full(p, seed["label"])
            r["family"] = "D"
            r["bd"] = behavior_distance(r)
            d_results.append(r)
            print(f"  {seed['label']}: speed={r['s1_landing']['total_speed']:.2f} q={r['mean_q']:.3f} bd={r['bd']:.3f} sep_legal={r['staging_legal']}")
        except Exception as exc:
            print(f"  {seed['label']}: ERROR {exc}")
    results["D"] = sorted(d_results, key=lambda r: r.get("bd", 999))

    # Family S — Separation-state design
    print("\n--- Family S: Separation-State Design ---")
    s_seeds = [
        {"label": "S1", "s1_separation_delay": 0.0, "s1_fin_height": 0.70, "s1_fin_count": 8},
        {"label": "S2", "s1_separation_delay": 0.2, "s1_fin_height": 0.70, "s1_fin_count": 8},
        {"label": "S3", "s1_separation_delay": 0.5, "s1_fin_height": 0.70, "s1_fin_count": 8},
        {"label": "S4", "s1_separation_delay": 0.0, "s1_fin_height": 0.65, "s1_fin_count": 8, "s1_body_len": 1.0},
        {"label": "S5", "s1_separation_delay": 0.0, "s1_fin_height": 0.65, "s1_fin_count": 8, "s1_body_len": 0.90},
    ]
    s_results = []
    for seed in s_seeds:
        p = make_base()
        p.update({k: v for k, v in seed.items() if k != 'label'})
        try:
            r = run_full(p, seed["label"])
            r["family"] = "S"
            r["bd"] = behavior_distance(r)
            s_results.append(r)
            print(f"  {seed['label']}: speed={r['s1_landing']['total_speed']:.2f} q={r['mean_q']:.3f} bd={r['bd']:.3f} sep_legal={r['staging_legal']}")
        except Exception as exc:
            print(f"  {seed['label']}: ERROR {exc}")
    results["S"] = sorted(s_results, key=lambda r: r.get("bd", 999))

    # Save progress
    best_per_family = {}
    for fam, res in results.items():
        legal = [r for r in res if r.get("staging_legal") and r.get("min_margin_cal", 0) >= 1.5 and r.get("mach", 2) < 1.0]
        best_per_family[fam] = legal[0] if legal else (res[0] if res else None)

    all_legal = []
    for fam, res in results.items():
        legal = [r for r in res if r.get("staging_legal") and r.get("min_margin_cal", 0) >= 1.5 and r.get("mach", 2) < 1.0]
        all_legal.extend(legal)
    all_legal.sort(key=lambda r: r.get("bd", 999))

    save("progress-stage-1.json", {
        "evaluations_used": EVAL_COUNT["structural"],
        "families": list(results.keys()),
        "best_candidate_per_family": {k: v["label"] if v else None for k, v in best_per_family.items()},
        "best_behavior_distance": all_legal[0]["bd"] if all_legal else None,
        "best_free_descent_speed": all_legal[0]["s1_landing"]["total_speed"] if all_legal else None,
        "best_horizontal_speed": all_legal[0]["s1_landing"]["vxy_ms"] if all_legal else None,
        "best_q_total_mean": all_legal[0]["mean_q"] if all_legal else None,
        "best_powered_speed": None,
        "dominant_failure": "stage_1_complete",
        "next_action": "stage_2_refinement",
    })

    return results, all_legal


# ═══════════════════════════════════════════════════════════════
# STAGE 2: CONTINUOUS REFINEMENT (best 2 families)
# ═══════════════════════════════════════════════════════════════
def stage2_refinement(stage1_results, stage1_legal):
    print("\n=== STAGE 2: Family Refinement ===")

    # Pick best 2 families
    family_scores = {}
    for fam, res in stage1_results.items():
        legal = [r for r in res if r.get("staging_legal") and r.get("min_margin_cal", 0) >= 1.5]
        if legal:
            family_scores[fam] = legal[0]["bd"]
    best_families = sorted(family_scores.keys(), key=lambda f: family_scores[f])[:2]
    print(f"  Refining families: {best_families}")

    refinement_results = []
    for fam in best_families:
        print(f"\n  --- Refining Family {fam} ---")
        # Get the best seed from stage 1
        best_seed = None
        for r in stage1_results[fam]:
            if r.get("staging_legal") and r.get("min_margin_cal", 0) >= 1.5:
                best_seed = r
                break
        if not best_seed:
            best_seed = stage1_results[fam][0]

        # Generate variations around the best seed
        variations = []
        base = make_base()
        # Apply the best seed's parameters
        for key in ["s1_fin_count", "s1_fin_height", "s1_fin_root", "s1_fin_sweep",
                     "s0_fin_count", "s0_fin_height", "s0_fin_root",
                     "s1_body_len", "s1_body_rad", "s0_body_len",
                     "s1_separation_delay", "launch_angle_deg"]:
            if key in best_seed:
                base[key] = best_seed[key]

        # Fin height variations
        for fh in [best_seed.get("s1_fin_height", 0.70) + d for d in [-0.05, -0.02, 0.0, 0.02, 0.05]]:
            if fh < 0.30 or fh > 1.00:
                continue
            p = dict(base)
            p["s1_fin_height"] = round(fh, 3)
            variations.append({"label": f"{fam}_fh{fh:.2f}", "params": p})

        # Fin sweep variations
        for sw in [0, 3, 5, 8]:
            p = dict(base)
            p["s1_fin_sweep"] = sw
            variations.append({"label": f"{fam}_sw{sw}", "params": p})

        # Body length variations
        for bl in [base.get("s1_body_len", 0.80) + d for d in [-0.10, 0.0, 0.10, 0.20]]:
            if bl < 0.50 or bl > 1.50:
                continue
            p = dict(base)
            p["s1_body_len"] = round(bl, 2)
            variations.append({"label": f"{fam}_bl{bl:.2f}", "params": p})

        # Fin count variations
        for fc in [4, 6, 8]:
            p = dict(base)
            p["s1_fin_count"] = fc
            variations.append({"label": f"{fam}_fc{fc}", "params": p})

        # Separation delay variations
        for sd in [0.0, 0.2, 0.5]:
            p = dict(base)
            p["s1_separation_delay"] = sd
            variations.append({"label": f"{fam}_sd{sd}", "params": p})

        # Run variations
        for var in variations:
            try:
                r = run_full(var["params"], var["label"])
                r["family"] = fam
                r["bd"] = behavior_distance(r)
                refinement_results.append(r)
                legal_marker = " *" if r["staging_legal"] and r.get("min_margin_cal", 0) >= 1.5 else ""
                print(f"    {var['label']}: speed={r['s1_landing']['total_speed']:.2f} q={r['mean_q']:.3f} bd={r['bd']:.3f}{legal_marker}")
            except Exception as exc:
                print(f"    {var['label']}: ERROR {exc}")

    refinement_results.sort(key=lambda r: r.get("bd", 999))
    all_refined = [r for r in refinement_results if r.get("staging_legal") and r.get("min_margin_cal", 0) >= 1.5 and r.get("mach", 2) < 1.0]
    all_refined.sort(key=lambda r: r.get("bd", 999))

    save("progress-stage-2.json", {
        "evaluations_used": EVAL_COUNT["structural"],
        "families_refined": best_families,
        "best_candidate": all_refined[0]["label"] if all_refined else None,
        "best_behavior_distance": all_refined[0]["bd"] if all_refined else None,
        "best_free_descent_speed": all_refined[0]["s1_landing"]["total_speed"] if all_refined else None,
        "best_horizontal_speed": all_refined[0]["s1_landing"]["vxy_ms"] if all_refined else None,
        "best_q_total_mean": all_refined[0]["mean_q"] if all_refined else None,
        "dominant_failure": "stage_2_complete",
        "next_action": "stage_3_powered",
    })

    return refinement_results, all_refined


# ═══════════════════════════════════════════════════════════════
# STAGE 3: POWERED VALIDATION
# ═══════════════════════════════════════════════════════════════
def stage3_powered(refined):
    print("\n=== STAGE 3: Powered Validation ===")

    # Top 12 candidates
    finalists = refined[:12]
    powered_results = []

    for fin in finalists:
        print(f"\n  Testing: {fin['label']} (free-descent: {fin['s1_landing']['total_speed']:.2f} m/s)")

        # Test with H180W and J350W
        for motor_name in ["H180W", "J350W"]:
            try:
                motor = load_motor(motor_name)
                motor_idx = None
                for i, m in enumerate(MOTOR_DATABASE):
                    if m[1] == motor_name:
                        motor_idx = i
                        break
                if motor_idx is None:
                    continue
            except Exception:
                continue

            best_speed = float('inf')
            best_delay = None
            best_burn_q = None

            for delay in [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 18.0, 20.0, 25.0, 30.0, 35.0, 40.0]:
                p = make_base()
                for k, v in fin.items():
                    if k in p and k not in ("label", "family", "bd", "mach", "apogee_m", "min_margin_cal",
                                              "staging_legal", "sep_time_s", "first_apogee_s", "sep_state",
                                              "s1_landing", "apex_t", "hit_t", "mean_q", "min_q", "window_s",
                                              "n_desc_samples", "branch_events", "ork_hash"):
                        p[k] = v
                p['s1_retro'] = motor_idx
                p['s1_retro_delay'] = delay
                try:
                    EVAL_COUNT["powered"] += 1
                    r = run_full(p, f"{fin['label']}_{motor_name}_d{delay}")
                    if r["s1_landing"] and r["s1_landing"]["total_speed"] < best_speed:
                        best_speed = r["s1_landing"]["total_speed"]
                        best_delay = delay
                        best_burn_q = r["mean_q"]
                except Exception:
                    pass

            if best_delay is not None:
                powered_results.append({
                    "candidate": fin["label"],
                    "motor": motor_name,
                    "best_delay_s": best_delay,
                    "best_speed_ms": round(best_speed, 3),
                    "free_descent_ms": round(fin["s1_landing"]["total_speed"], 3),
                    "improvement_ms": round(fin["s1_landing"]["total_speed"] - best_speed, 2),
                    "legal_branch": best_speed < 5.0,
                    "mean_burn_q": best_burn_q,
                })
                status = "LEGAL!" if best_speed < 5.0 else f"best={best_speed:.2f}"
                print(f"    {motor_name}: {status} at delay={best_delay}s")

    powered_results.sort(key=lambda r: r["best_speed_ms"])

    save("progress-stage-3.json", {
        "evaluations_used": EVAL_COUNT["structural"],
        "total_evaluations": EVAL_COUNT["structural"] + EVAL_COUNT["powered"],
        "powered_evaluations": EVAL_COUNT["powered"],
        "candidates_tested": len(finalists),
        "best_powered_speed": powered_results[0]["best_speed_ms"] if powered_results else None,
        "best_candidate": powered_results[0]["candidate"] if powered_results else None,
        "best_motor": powered_results[0]["motor"] if powered_results else None,
        "best_delay": powered_results[0]["best_delay_s"] if powered_results else None,
        "legal_branch_found": any(r["legal_branch"] for r in powered_results),
        "dominant_failure": "stage_3_complete",
        "next_action": "report" if not any(r["legal_branch"] for r in powered_results) else "legal_branch_found",
    })

    return powered_results


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    init_or()
    t0 = time.time()

    # Stage 1
    stage1_results, stage1_legal = stage1_family_exploration()

    # Stage 2
    refinement_results, refined = stage2_refinement(stage1_results, stage1_legal)

    # Stage 3
    powered_results = stage3_powered(refined)

    elapsed = time.time() - t0

    # Summary
    best_powered = powered_results[0] if powered_results else None
    legal_branch = any(r["legal_branch"] for r in powered_results) if powered_results else False

    summary = {
        "phase": "4a",
        "status": "LEGAL_BOOSTER_BRANCH" if legal_branch else "NO_LEGAL_BRANCH",
        "total_evaluations": EVAL_COUNT["structural"] + EVAL_COUNT["powered"],
        "structural_evaluations": EVAL_COUNT["structural"],
        "powered_evaluations": EVAL_COUNT["powered"],
        "elapsed_s": round(elapsed, 1),
        "legal_branch": legal_branch,
        "best_powered": best_powered,
        "teacher_distance": best_powered.get("free_descent_ms", 21.7) if best_powered else None,
    }
    save("phase4a-summary.json", summary)

    print(f"\n{'='*60}")
    print(f"STATUS: {'LEGAL BOOSTER BRANCH' if legal_branch else 'NO LEGAL BRANCH'}")
    print(f"TOTAL EVALUATIONS: {EVAL_COUNT['structural']} structural + {EVAL_COUNT['powered']} powered")
    print(f"ELAPSED: {elapsed:.1f}s")
    if best_powered:
        print(f"BEST: {best_powered['candidate']} {best_powered['motor']} → {best_powered['best_speed_ms']:.2f} m/s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
