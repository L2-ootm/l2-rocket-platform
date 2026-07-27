#!/usr/bin/env python3
"""Phase 3A — Gate 0: Classification correction and braking_alignment_angle definition.

Produces: artifacts/phase3a/classification-correction.json
"""
import json, math, os

ARTIFACTS = "artifacts/phase3a"

def save(name, data):
    os.makedirs(ARTIFACTS, exist_ok=True)
    with open(os.path.join(ARTIFACTS, name), 'w') as f:
        json.dump(data, f, indent=2, sort_keys=True, default=str)
    print(f"  wrote {name}")


def main():
    print("=== Gate 0: Classification Correction ===")

    # Corrected classification
    classification = {
        "gate": 0,
        "old_classification": "AERODYNAMIC_TOPOLOGY_INFEASIBLE",
        "new_classification": "CURRENT_BOOSTER_CONFIGURATION_3D_INFEASIBLE",
        "correction_rationale": (
            "The evidence only supports that the tested booster geometry, separation state, "
            "ballast distribution, motor set, and ignition windows are 3D-landing-infeasible. "
            "It does NOT prove that every 3+1 vehicle, every fixed aerodynamic topology, "
            "or every legal separation state is infeasible."
        ),
        "braking_alignment_angle_definition": {
            "formula": "braking_alignment_angle = acos(clamp(-u_T . v_hat, -1, 1))",
            "q_total_formula": "q_total = -u_T . v_hat",
            "where": {
                "u_T": "Unit vector along the motor thrust axis (nose direction)",
                "v_hat": "Unit vector along the total velocity (normalized)",
            },
            "interpretation": {
                "0_deg_q_equals_1": "Thrust directly opposes total velocity — maximum braking",
                "90_deg_q_equals_0": "Thrust is perpendicular to velocity — no first-order braking",
                "180_deg_q_equals_minus_1": "Thrust supports velocity — accelerates the stage",
            },
            "projections": {
                "q_vertical": "Projection of thrust axis onto vertical velocity component",
                "q_horizontal": "Projection of thrust axis onto horizontal velocity component",
                "braking_angle_vertical": "acos(clamp(-u_T_z * sign(vz), -1, 1))",
                "braking_angle_horizontal": "acos(clamp(-u_T_h . v_h_hat, -1, 1))",
            },
        },
        "key_metrics_from_current_failure": {
            "q_total_at_apex": 0.882,
            "q_total_at_15s": 0.115,
            "q_total_at_impact": 0.012,
            "braking_angle_at_apex_deg": 28.1,
            "braking_angle_at_15s_deg": 83.4,
            "braking_angle_at_impact_deg": 89.3,
            "interpretation": (
                "At apex, thrust axis is 28° from opposing velocity (good). "
                "By 15s, thrust axis is 83° from opposing velocity (nearly perpendicular). "
                "At impact, thrust axis is 89° from opposing velocity (useless for braking)."
            ),
        },
    }

    save("classification-correction.json", classification)
    print(f"\nOld: {classification['old_classification']}")
    print(f"New: {classification['new_classification']}")


if __name__ == "__main__":
    main()
