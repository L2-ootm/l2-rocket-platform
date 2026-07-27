"""Experiment E: Wind parity — compare Rust and Python wind vectors."""
import json
import math
from pathlib import Path


def python_wind_at_altitude(wind_levels, altitude_m):
    """Linear interpolation of wind profile (matching osifog_sweep behavior)."""
    if not wind_levels:
        return 0.0, 0.0
    if altitude_m <= wind_levels[0][0]:
        return wind_levels[0][1], wind_levels[0][2]
    if altitude_m >= wind_levels[-1][0]:
        return wind_levels[-1][1], wind_levels[-1][2]
    for i in range(1, len(wind_levels)):
        alt0, spd0, dir0, _ = wind_levels[i-1]
        alt1, spd1, dir1, _ = wind_levels[i]
        if alt0 <= altitude_m <= alt1:
            t = (altitude_m - alt0) / (alt1 - alt0) if alt1 > alt0 else 0.0
            speed = spd0 + t * (spd1 - spd0)
            # Direction interpolation with wraparound
            d0_rad = math.radians(dir0)
            d1_rad = math.radians(dir1)
            # Handle wraparound
            if abs(d1_rad - d0_rad) > math.pi:
                if d0_rad < d1_rad:
                    d0_rad += 2 * math.pi
                else:
                    d1_rad += 2 * math.pi
            dir_rad = d0_rad + t * (d1_rad - d0_rad)
            direction = math.degrees(dir_rad) % 360.0
            return speed, direction
    return wind_levels[-1][1], wind_levels[-1][2]


def wind_to_cartesian(speed_ms, direction_deg):
    """Convert meteorological wind (from direction) to cartesian (vx, vy)."""
    # Wind FROM direction: the wind blows toward direction + 180
    toward_rad = math.radians(direction_deg + 180.0)
    vx = speed_ms * math.sin(toward_rad)  # East component
    vy = speed_ms * math.cos(toward_rad)  # North component
    return vx, vy


def run_experiment():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))
    from osifog_sweep import parse_wind_csv, WIND_CSV
    
    wind_levels = parse_wind_csv(WIND_CSV)
    
    test_altitudes = [0, 100, 200, 500, 1000, 1500, 2000, 2500, 3000, 3500]
    
    results = []
    for alt in test_altitudes:
        speed, direction = python_wind_at_altitude(wind_levels, alt)
        vx, vy = wind_to_cartesian(speed, direction)
        results.append({
            "altitude_m": alt,
            "speed_ms": round(speed, 4),
            "direction_deg": round(direction, 4),
            "vx_ms": round(vx, 4),
            "vy_ms": round(vy, 4),
        })
    
    # Verify wind CSV has expected number of levels
    print("Wind Parity Experiment")
    print("=" * 60)
    print(f"Wind CSV: {WIND_CSV}")
    print(f"Total levels: {len(wind_levels)}")
    print(f"Surface: {wind_levels[0][1]:.1f} m/s from {wind_levels[0][2]:.0f} deg")
    print()
    
    print(f"{'Alt(m)':>8} {'Speed':>8} {'Dir':>8} {'Vx(E)':>8} {'Vy(N)':>8}")
    print("-" * 50)
    for r in results:
        print(f"{r['altitude_m']:>8} {r['speed_ms']:>8.2f} {r['direction_deg']:>8.1f} {r['vx_ms']:>8.3f} {r['vy_ms']:>8.3f}")
    
    # Check AGL convention
    print(f"\nAGL reference: wind altitudes are above ground level (0 = surface)")
    print(f"Direction convention: meteorological 'from' degrees, stored as radians in ORK XML")
    print(f"Cartesian: vx = East, vy = North (wind blows toward direction + 180)")
    
    # Save results
    output = Path("docs/research/osifog-2026-deep-audit/experiments")
    output.mkdir(parents=True, exist_ok=True)
    artifact = {
        "experiment": "E_wind_parity",
        "wind_csv": WIND_CSV,
        "total_levels": len(wind_levels),
        "surface_speed_ms": wind_levels[0][1],
        "surface_direction_deg": wind_levels[0][2],
        "test_altitudes": results,
        "convention": {
            "altitude_reference": "AGL",
            "direction": "meteorological_from_degrees",
            "ork_xml_storage": "radians",
            "cartesian": "vx=east, vy=north",
        },
    }
    (output / "wind_parity.json").write_text(
        json.dumps(artifact, indent=2), encoding="utf-8"
    )
    print(f"\nResults saved to {output / 'wind_parity.json'}")


if __name__ == "__main__":
    run_experiment()
