"""Dump OpenRocket 24.12's *live* motor database with digests.

`rocket_forge.MOTOR_DATABASE` is a hand-curated 38-motor subset. The live
OpenRocket database that actually resolves `.ork` motor references holds ~1088
motor sets. Any motor in the live database can be referenced from a generated
`.ork` as long as we emit the correct
(manufacturer, designation, diameter, length, delay, digest) tuple -- which is
exactly the shape of a `MOTOR_DATABASE` row.

This script enumerates the live database and writes a JSON catalog with the
performance fields needed to size a retro motor (average thrust, burn time,
total impulse) plus the digest needed to reference it unambiguously.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/dump_or_motor_catalog.py
Output: OSIFOG/experiments-2026-07-25/or_motor_catalog.json
"""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_sweep import init_or

OUT = "OSIFOG/experiments-2026-07-25/or_motor_catalog.json"


def main():
    app = init_or()
    rows = []
    for mset in app.getMotorSetDatabase().getMotorSets():
        for motor in mset.getMotors():
            try:
                # Thrust motors expose burn time / impulse through the
                # ThrustCurveMotor API; delays live on the motor set.
                designation = str(motor.getDesignation())
                mfr = str(motor.getManufacturer().getDisplayName())
                rows.append({
                    "manufacturer": mfr,
                    "designation": designation,
                    "diameter_m": float(motor.getDiameter()),
                    "length_m": float(motor.getLength()),
                    "digest": str(motor.getDigest()),
                    "avg_thrust_n": float(motor.getAverageThrustEstimate()),
                    "max_thrust_n": float(motor.getMaxThrustEstimate()),
                    "burn_time_s": float(motor.getBurnTimeEstimate()),
                    "total_impulse_ns": float(motor.getTotalImpulseEstimate()),
                    "launch_mass_kg": float(motor.getLaunchMass()),
                    "burnout_mass_kg": float(motor.getBurnoutMass()),
                    "motor_type": str(motor.getMotorType()),
                    "delays": [float(d) for d in motor.getStandardDelays()],
                })
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"  [skip] {motor}: {exc}")
    rows.sort(key=lambda r: (r["avg_thrust_n"], r["designation"]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    print(f"wrote {len(rows)} motors -> {OUT}")


if __name__ == "__main__":
    main()
