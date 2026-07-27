"""Quick verification of canonical motor_data module."""
import sys
sys.path.insert(0, ".")

import motor_data
from rocket_forge import MOTOR_DATABASE

results = []
errors = []
for idx in range(len(MOTOR_DATABASE)):
    try:
        m = motor_data.load_motor_by_index(idx)
        results.append(m)
    except Exception as e:
        errors.append((MOTOR_DATABASE[idx][1], str(e)))

key_motors = ["J510W", "K550W", "H180W", "J360_CTI", "F50T"]
header = f"{'Motor':<12} {'Diam':>6} {'Len':>6} {'Prop':>8} {'Loaded':>8} {'Dry':>8} {'Burn':>8} {'Impulse':>9}"
print(header)
print("-" * 80)
for r in results:
    if r.designation in key_motors:
        print(f"{r.designation:<12} {r.diameter_m*1000:>6.0f} {r.length_m*1000:>6.0f} {r.propellant_mass_kg:>8.4f} {r.loaded_mass_kg:>8.4f} {r.dry_mass_kg:>8.4f} {r.burn_duration_s:>8.3f} {r.total_impulse_ns:>9.1f}")

# Verify consistency
for r in results:
    assert abs(r.dry_mass_kg - (r.loaded_mass_kg - r.propellant_mass_kg)) < 1e-9, f"{r.designation}: dry_mass mismatch"
    assert r.burn_duration_s == r.time_points_s[-1] - r.time_points_s[0], f"{r.designation}: burn_duration mismatch"
    assert len(r.time_points_s) >= 2, f"{r.designation}: insufficient data points"

print(f"\nTotal: {len(results)} motors loaded")
print(f"Errors: {len(errors)}")
if errors:
    for name, err in errors:
        print(f"  {name}: {err}")
print("Dry mass = loaded - propellant: VERIFIED for all")
print("Burn duration from curve time domain: VERIFIED for all")
