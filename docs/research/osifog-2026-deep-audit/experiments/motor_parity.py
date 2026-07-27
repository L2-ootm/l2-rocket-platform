"""Motor data parity experiment — compare Python, Rust, and .eng source data."""
import json
import math
import os
from pathlib import Path

MOTORS_DIR = Path("l2_engine/motors")


def parse_eng_file(path):
    """Parse a RASP .eng file and return header + data points."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = None
    data = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        fields = line.split()
        if header is None:
            header = fields
            continue
        if len(fields) == 2:
            data.append((float(fields[0]), float(fields[1])))
    return header, data


def integrate_trapezoidal(points):
    """Trapezoidal integration of thrust curve."""
    total = 0.0
    for i in range(1, len(points)):
        dt = points[i][0] - points[i-1][0]
        avg_thrust = (points[i][1] + points[i-1][1]) / 2.0
        total += avg_thrust * dt
    return total


def run_experiment():
    results = []
    for eng_file in sorted(MOTORS_DIR.glob("*.eng")):
        header, data = parse_eng_file(eng_file)
        if not header or len(data) < 2:
            continue
        
        designation = header[0]
        diameter_mm = float(header[1])
        length_mm = float(header[2])
        propellant_kg = float(header[4])
        loaded_kg = float(header[5])
        dry_kg = loaded_kg - propellant_kg
        burn_time_s = data[-1][0]
        total_impulse = integrate_trapezoidal(data)
        
        # Read Python MOTOR_PROPELLANT_KG if available
        python_propellant = None
        try:
            from osifog_sweep import MOTOR_PROPELLANT_KG
            from rocket_forge import MOTOR_DATABASE
            for idx, motor in enumerate(MOTOR_DATABASE):
                if motor[1] == designation:
                    python_propellant = MOTOR_PROPELLANT_KG.get(idx)
                    break
        except Exception:
            pass
        
        results.append({
            "designation": designation,
            "diameter_mm": diameter_mm,
            "length_mm": length_mm,
            "propellant_kg_eng": propellant_kg,
            "loaded_kg_eng": loaded_kg,
            "dry_kg_eng": dry_kg,
            "burn_time_s": burn_time_s,
            "total_impulse_ns": round(total_impulse, 2),
            "point_count": len(data),
            "python_propellant_kg": python_propellant,
            "propellant_error_pct": (
                round(abs(python_propellant - propellant_kg) / propellant_kg * 100, 1)
                if python_propellant is not None else None
            ),
        })
    
    return results


if __name__ == "__main__":
    results = run_experiment()
    
    # Summary
    print(f"{'Motor':<12} {'Diam':>5} {'Len':>5} {'Prop(kg)':>9} {'Loaded':>7} {'Burn(s)':>8} {'Impulse':>9} {'PyProp':>8} {'Err%':>6}")
    print("-" * 95)
    for r in results:
        py_prop = f"{r['python_propellant_kg']:.4f}" if r['python_propellant_kg'] is not None else "N/A"
        err = f"{r['propellant_error_pct']:.1f}" if r['propellant_error_pct'] is not None else "N/A"
        print(f"{r['designation']:<12} {r['diameter_mm']:>5.0f} {r['length_mm']:>5.0f} {r['propellant_kg_eng']:>9.4f} {r['loaded_kg_eng']:>7.4f} {r['burn_time_s']:>8.3f} {r['total_impulse_ns']:>9.1f} {py_prop:>8} {err:>6}")
    
    # Save full results
    output = Path("docs/research/osifog-2026-deep-audit/experiments")
    output.mkdir(parents=True, exist_ok=True)
    (output / "motor_data_parity.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(f"\nResults saved to {output / 'motor_data_parity.json'}")
    print(f"Total motors: {len(results)}")
    
    # Find discrepancies
    errors = [r for r in results if r['propellant_error_pct'] is not None and r['propellant_error_pct'] > 1.0]
    if errors:
        print(f"\nMotor propellant discrepancies > 1%:")
        for r in errors:
            print(f"  {r['designation']}: Python={r['python_propellant_kg']:.4f} kg, .eng={r['propellant_kg_eng']:.4f} kg ({r['propellant_error_pct']:.1f}% error)")
    else:
        print("\nNo propellant discrepancies > 1%")
