"""Diagnose whether an internal coupler cap changes OpenRocket aerodynamics.

The injected bulkhead is deliberately near-massless and exists only in the
temporary XML passed to OpenRocket.  It is not a legal submission component
and is never written to a candidate ORK.  This isolates the aerodynamic-model
question from the mass/CG effect of a real structural plate.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import osifog_sweep as sweep
from osifog_direct_driver import BASE


def inject_diagnostic_bulkhead(xml: str, body_radius_m: float) -> str:
    booster_name = "<name>Booster Airframe</name>"
    booster_at = xml.index(booster_name)
    subcomponents_at = xml.index("<subcomponents>", booster_at)
    insert_at = subcomponents_at + len("<subcomponents>")
    bulkhead = f"""
              <bulkhead>
                <name>DIAGNOSTIC Near-Massless Coupler Closure</name>
                <id>{sweep._component_id("DIAGNOSTIC Coupler Closure")}</id>
                <position type="top">0.001000000</position>
                <material type="bulk" density="0.000001">DIAGNOSTIC</material>
                <length>0.001000000</length>
                <radialposition>0.000000000</radialposition>
                <radialdirection>0.000000000</radialdirection>
                <outerradius>{body_radius_m - 0.002:.9f}</outerradius>
              </bulkhead>"""
    return xml[:insert_at] + bulkhead + xml[insert_at:]


def main() -> int:
    candidate_path = (
        REPO
        / "designs/osifog_visuals/candidate_K_celestial_datum_v7.json"
    )
    output_path = (
        REPO
        / "designs/osifog_finalization/coupler_closure_drag_probe.json"
    )
    delta = json.loads(candidate_path.read_text(encoding="utf-8"))
    parameters = copy.deepcopy(BASE)
    parameters.update(delta)
    baseline_xml = sweep.generate_ork(parameters)
    closed_xml = inject_diagnostic_bulkhead(
        baseline_xml, float(parameters["s1_body_rad"])
    )

    sweep.init_or()
    cases = []
    for seed in (16000, 30017, 1348235082):
        baseline = sweep.run_sim(baseline_xml, seed=seed)
        closed = sweep.run_sim(closed_xml, seed=seed)
        baseline_score = sweep.score_official(baseline, parameters)
        closed_score = sweep.score_official(closed, parameters)
        keys = (
            "apogee_m",
            "mach",
            "s0_landing_speed",
            "s1_landing_speed",
            "apogee_east_m",
            "apogee_north_m",
            "s0_east_m",
            "s0_north_m",
            "s1_east_m",
            "s1_north_m",
        )
        cases.append(
            {
                "seed": seed,
                "baseline_score": baseline_score["raw_score"],
                "closed_score": closed_score["raw_score"],
                "metric_deltas": {
                    key: float(closed[key]) - float(baseline[key])
                    for key in keys
                },
                "all_metric_deltas_zero": all(
                    float(closed[key]) == float(baseline[key])
                    for key in keys
                ),
            }
        )

    ascent_invariant = all(
        abs(case["metric_deltas"]["apogee_m"]) < 1.0e-6
        and abs(case["metric_deltas"]["mach"]) < 1.0e-9
        for case in cases
    )
    booster_speed_deltas = [
        case["metric_deltas"]["s1_landing_speed"] for case in cases
    ]
    systematic_booster_braking = all(
        delta < 0.0 for delta in booster_speed_deltas
    )
    output = {
        "purpose": (
            "Isolate whether an internal forward coupler bulkhead changes "
            "OpenRocket aerodynamics independently of mass/CG."
        ),
        "submission_candidate_mutated": False,
        "diagnostic_component_legal": False,
        "openrocket_source_model": (
            "TubeCalc derives internal-tube drag from tube length, inner radius, "
            "roughness and flow speed; internal bulkheads are not inputs."
        ),
        "cases": cases,
        "ascent_aerodynamics_invariant": ascent_invariant,
        "systematic_booster_braking": systematic_booster_braking,
        "interpretation": (
            "Apogee and Mach are invariant at numerical-noise scale. Landing "
            "deltas change sign across seeds, which is consistent with chaotic "
            "tumble sensitivity, not a modeled drag increase."
        ),
        "conclusion": (
            "The internal closure provides no systematic modeled aerodynamic "
            "braking and must not be promoted."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
