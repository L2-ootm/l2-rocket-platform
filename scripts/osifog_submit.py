"""Build, verify and package an OSIFOG Level 3 submission candidate.

Runs the real OpenRocket oracle, checks the CONFIRMED OSIFOG rule set (playbook
sec 1 / sec 6), prints a per-item checklist verdict, and saves the executed .ork.

`osifog_sweep.score_official` now uses the confirmed metric/parameter gate.
This script adds the XML/package-only checks that cannot be inferred from
telemetry alone.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/osifog_submit.py <params.json> <out.ork> [seed]
"""
import sys, os, json, copy, math, zipfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import osifog_sweep as S
from osifog_direct_driver import BASE

MAX_LANDING_SPEED = 5.0
MAX_MACH_RULE = 1.0
MAX_HEIGHT = 4.0
MAX_ROD = 6.0
RHO_MIN, RHO_MAX = 170.0, 11340.0


def checklist(p, m, xml, cosmetic_check=None):
    """Return [(ok, label, detail)] against the confirmed rule set."""
    out = []
    lands = m.get("stage_landings", [])

    out.append((len(lands) >= 2, "multi-stage, all stages land",
                "%d landed branches" % len(lands)))
    for l in lands:
        out.append((l["total_speed"] < MAX_LANDING_SPEED,
                    "touchdown |v| < 5 m/s (%s)" % l["branch_name"],
                    "%.3f m/s at t=%.2f s" % (l["total_speed"], l["time_s"])))
        out.append((l["orientation_theta_deg"] > 45.0,
                    "tail-first attitude (%s)" % l["branch_name"],
                    "theta=%+.1f deg" % l["orientation_theta_deg"]))

    mach = m.get("mach", 0.0)
    out.append((mach < MAX_MACH_RULE, "subsonic throughout", "Mach_max=%.4f" % mach))

    nose = float(p.get("nose_length_m", 0.0))
    h = nose + float(p["s0_body_len"]) + float(p["s1_body_len"])
    out.append((h <= MAX_HEIGHT, "overall length <= 4 m", "%.3f m" % h))
    out.append((S.LAUNCH_ROD_M <= MAX_ROD, "launch rod <= 6 m",
                "%.2f m" % S.LAUNCH_ROD_M))

    out.append(("<recoverydevice" not in xml and "parachute" not in xml.lower()
                and "streamer" not in xml.lower(),
                "no recovery devices", "no parachute/streamer/recoverydevice tags"))
    out.append(("<overridemass>" not in xml and "<overridecg>" not in xml
                and "<overridesubcomponents>" not in xml
                and "<overridecd>" not in xml,
                "no mass/CG/CD overrides", "no override tags"))

    ring_violations = S.validate_compiled_centering_rings(xml)
    out.append((not ring_violations,
                "two valid airframe-spanning rings per stage",
                "; ".join(ring_violations) if ring_violations else
                "4 centered nondegenerate rings"))

    coupler_required = bool(p.get("interstage_coupler", False))
    coupler_violations = S.validate_compiled_interstage_coupler(
        xml, required=coupler_required
    )
    out.append((not coupler_violations,
                "booster-owned interstage coupler geometry",
                "; ".join(coupler_violations) if coupler_violations else
                (
                    "one coupler spanning both stage bores"
                    if coupler_required
                    else "coupler not requested"
                )))

    ignition_order_violations = S.validate_upper_stage_ignition_after_separation(xml)
    out.append((not ignition_order_violations,
                "all sustainer motors ignite after separation",
                "; ".join(ignition_order_violations)
                if ignition_order_violations else
                "separation precedes every sustainer ignition"))

    nose_violations = S.validate_compiled_nose_ballast_attachment(
        xml, p.get("nose_mass_kg")
    )
    out.append((not nose_violations,
                "nose ballast rigidly bonded to nose shell",
                "; ".join(nose_violations) if nose_violations else
                "centered full-radius structural bulkhead"))

    event_reference_violations = S.validate_serialized_flight_event_references(xml)
    out.append((not event_reference_violations,
                "saved flight-event references resolve",
                "; ".join(event_reference_violations)
                if event_reference_violations else
                "all warning/event IDs resolve within stored branches"))

    rho = [float(x) for x in __import__("re").findall(r'density="([0-9.]+)"', xml)]
    out.append((all(RHO_MIN - 1e-9 <= r <= RHO_MAX for r in rho),
                "material density in [170, 11340] kg/m3",
                "min=%.0f max=%.0f" % (min(rho), max(rho)) if rho else "none"))

    ej = m.get("event_times", {}).get("EJECTION_CHARGE", [])
    out.append((not ej, "no ejection charges (plugged motors)",
                "EJECTION_CHARGE at %s" % ej if ej else "none"))

    sims = xml.count("<simulation ")
    out.append((sims == 1, "exactly one simulation", "%d" % sims))

    st = str(m.get("status", ""))
    out.append(("ABORT" not in st.upper(), "simulation not aborted", st or "ok"))
    if cosmetic_check is not None:
        out.append(
            (
                bool(cosmetic_check["equal"]),
                "appearance is cosmetic only",
                cosmetic_check["detail"],
            )
        )
    return out


def package_livery_decals(out_path, declarations):
    """Append declared decal assets and restore deterministic ZIP metadata."""
    if not declarations:
        return
    with zipfile.ZipFile(out_path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        existing = set(archive.namelist())
        for declaration in declarations:
            source_path = declaration["path"]
            if not os.path.isabs(source_path):
                source_path = os.path.join(_REPO, source_path)
            source_path = os.path.abspath(source_path)
            zip_name = str(declaration["zip_name"]).replace("\\", "/").lstrip("/")
            if not zip_name.startswith("decals/"):
                raise ValueError("livery decal zip_name must live under decals/")
            if not os.path.isfile(source_path):
                raise FileNotFoundError(f"livery decal not found: {source_path}")
            if zip_name in existing:
                with open(source_path, "rb") as source:
                    source_bytes = source.read()
                if archive.read(zip_name) != source_bytes:
                    raise ValueError(
                        f"packaged livery decal differs from source {zip_name!r}"
                    )
            else:
                archive.write(source_path, zip_name)
                existing.add(zip_name)
    S._canonicalize_saved_ork(out_path)


def main():
    params_path, out_path = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 16000

    p = copy.deepcopy(BASE)
    p.update(json.load(open(params_path)))
    p.setdefault("plugged_motors", True)

    xml = S.generate_ork(p)
    S.init_or()
    m = S.run_sim(xml, seed=seed)
    cosmetic_check = None
    if p.get("livery"):
        plain_parameters = copy.deepcopy(p)
        plain_parameters.pop("livery", None)
        plain_parameters.pop("livery_decals", None)
        plain_metrics = S.run_sim(S.generate_ork(plain_parameters), seed=seed)
        equal = m == plain_metrics
        differing_keys = sorted(
            key
            for key in set(m) | set(plain_metrics)
            if m.get(key) != plain_metrics.get(key)
        )
        cosmetic_check = {
            "equal": equal,
            "detail": (
                "telemetry dict exactly equal"
                if equal
                else "different keys: " + ", ".join(differing_keys)
            ),
        }
    sc = S.score_official(m, p)
    decal_declarations = []
    for declaration in p.get("livery_decals", []):
        resolved = dict(declaration)
        if not os.path.isabs(resolved["path"]):
            resolved["path"] = os.path.join(_REPO, resolved["path"])
        decal_declarations.append(resolved)
    S.save_simulated_ork(
        xml,
        out_path,
        seed=seed,
        extra_entries=decal_declarations,
    )
    package_livery_decals(out_path, decal_declarations)
    with zipfile.ZipFile(out_path) as archive:
        saved_xml = archive.read("rocket.ork").decode("utf-8")

    print("\n=== TELEMETRY (seed %d, dt %.3f) ===" % (seed, p.get("timestep_s", 0.05)))
    print("apogee            %.2f m   (error %+.2f m)" % (m["apogee_m"], m["apogee_m"] - 3000.0))
    print("apogee drift      E %+.2f  N %+.2f  (|d| %.2f m)" % (
        m["apogee_east_m"], m["apogee_north_m"],
        math.hypot(m["apogee_east_m"], m["apogee_north_m"])))
    print("Mach max          %.4f" % m["mach"])
    print("propellant        %.3f kg" % m["m_prop_kg_actual"])
    for l in m.get("stage_landings", []):
        print("%-10s t=%7.2f s  v=%7.3f m/s  theta=%+6.1f  E=%+8.1f N=%+8.1f  m=%.3f kg" % (
            l["branch_name"], l["time_s"], l["total_speed"],
            l["orientation_theta_deg"], l["east_m"], l["north_m"], l["mass_kg"]))
    print("OFFICIAL SCORE    %.1f" % sc["raw_score"])

    print("\n=== RULE CHECKLIST ===")
    # Validate the actual packaged XML. OpenRocket normalizes some fields
    # while saving (for example a single-tube ring radius becomes ``auto``),
    # so checking only the generator input can miss packaging regressions.
    items = checklist(p, m, saved_xml, cosmetic_check=cosmetic_check)
    for ok, label, detail in items:
        print("  [%s] %-42s %s" % ("PASS" if ok else "FAIL", label, detail))
    n_fail = sum(1 for ok, _, _ in items if not ok)
    print("\n%d/%d pass, %d FAIL" % (len(items) - n_fail, len(items), n_fail))

    # Preserve the scorer's metric/parameter verdict, then make the complete
    # XML-aware checklist the packaged report authority.
    metric_is_legal = bool(sc.get("is_legal"))
    metric_violations = list(sc.get("violations", []))
    sc["metrics_gate"] = {
        "is_legal": metric_is_legal,
        "violations": metric_violations,
    }
    package_violations = [
        f"{label}: {detail}" for ok, label, detail in items if not ok
    ]
    sc["is_legal"] = metric_is_legal and n_fail == 0
    sc["violations"] = metric_violations + package_violations
    sc["score"] = sc["raw_score"] if sc["is_legal"] else -1_000_000.0

    print("saved %s" % out_path)
    json.dump({"params": p, "metrics": {k: v for k, v in m.items()
               if k not in ("descent_alignment_diagnostics", "retro_burn_diagnostics")},
               "score": sc, "seed": seed,
               "checklist": [[ok, lab, det] for ok, lab, det in items]},
              open(out_path.replace(".ork", "_report.json"), "w"), indent=1, default=str)
    print("saved %s" % out_path.replace(".ork", "_report.json"))
    return 0 if sc["is_legal"] else 1


if __name__ == "__main__":
    sys.exit(main())
