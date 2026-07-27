import argparse
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB = REPO_ROOT / "openrocket/core/src/main/resources/datafiles/thrustcurves/initial_motors.db"
OUT = REPO_ROOT / "l2_engine/motors"

# designation -> output filename stem (matches existing convention: bare
# common name, e.g. N5800.eng not 20146N5800-P.eng)
TARGETS = {
    "9977M2245": "M2245",
    "20146N5800": "N5800",
    "40960O8000": "O8000",
    "F50T": "F50T", "F67W": "F67W",
    "G71R": "G71R", "G104T": "G104T", "G80T": "G80T",
    "H73J": "H73J", "H128W": "H128W", "H180W": "H180W", "H238T": "H238T",
    "I161W": "I161W", "I218R": "I218R", "I357T": "I357T", "I211W": "I211W", "I284W": "I284W",
    "J350W-OLD": "J350W", "J420R": "J420R", "J510W": "J510W", "J800T": "J800T", "J360": "J360_CTI",
    "K550W": "K550W", "K700W": "K700W", "K1050W-SU": "K1050W", "2486K510": "K510_CTI",
    "HP-L1000W": "L1000", "L1150R": "L1150", "L1500T": "L1500T", "L2200G": "L2200G",
    "M1939W": "M1939W", "M2500T": "M2500T", "M650W": "M650W", "M1297W": "M1297W",
    "N2000W": "N2000W", "N4800T": "N4800T",
}

# For these 3, OpenRocket's own motor-resolution needs the catalog-code-prefixed
# "-P" form (proven working: this is what l2_hyper/generator.py and the
# declarative mission JSONs already use to compile .ork files OpenRocket loads
# successfully), which differs from the bundled DB's own `designation` column
# (no "-P" suffix there) and from the short filename stem used elsewhere.
# The .eng file's HEADER designation is the one thing that must match this
# exactly, since it's the string Rust looks candidates up by; the filename
# itself is just a human label (see motor_db::parse_eng_file).
HEADER_DESIGNATION_OVERRIDE = {
    "9977M2245": "9977M2245-P",
    "20146N5800": "20146N5800-P",
    "40960O8000": "40960O8000-P",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract deterministic OpenRocket 24.12 motor curves for the Rust proxy."
    )
    parser.add_argument(
        "--designation",
        action="append",
        help="extract only this database designation (repeatable); default: all catalog motors",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    selected = set(args.designation or TARGETS)
    unknown = selected.difference(TARGETS)
    if unknown:
        raise SystemExit(f"unknown designation(s): {', '.join(sorted(unknown))}")
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    written = []
    missing = []
    for designation, stem in TARGETS.items():
        if designation not in selected:
            continue
        cur.execute(
            """SELECT m.id, mf.name, m.designation, m.diameter, m.length,
                      m.propellant_weight, m.total_weight, m.delays
               FROM motors m JOIN manufacturers mf ON m.manufacturer_id = mf.id
               WHERE m.designation = ?""",
            (designation,),
        )
        row = cur.fetchone()
        if not row:
            missing.append(designation)
            continue
        motor_id, mfr, real_designation, diameter_mm, length_mm, prop_g, total_g, delays = row
        # Match the deterministic OpenRocket resolver used by this project:
        # prefer the highest-impulse variant instead of whichever row happens
        # to be returned first.  Generated ORK files pin its digest separately.
        cur.execute(
            """SELECT id FROM thrust_curves WHERE motor_id = ?
               ORDER BY total_impulse DESC, id ASC LIMIT 1""",
            (motor_id,),
        )
        curve_row = cur.fetchone()
        if not curve_row:
            missing.append(designation + " (no thrust_curves row)")
            continue
        curve_id = curve_row[0]
        cur.execute(
            "SELECT time_seconds, force_newtons FROM thrust_data WHERE curve_id = ? ORDER BY time_seconds",
            (curve_id,),
        )
        points = cur.fetchall()
        if not points:
            missing.append(designation + " (no thrust_data rows)")
            continue

        prop_kg = prop_g / 1000.0
        total_kg = total_g / 1000.0
        mfr_short = "".join(w[0] for w in mfr.split())  # "AeroTech" -> "A", "Cesaroni Technology" -> "CT"
        header_designation = HEADER_DESIGNATION_OVERRIDE.get(designation, stem)

        lines = [
            f";{mfr} {real_designation} extracted from OpenRocket 24.12 bundled initial_motors.db",
            ";source: db table motors/thrust_curves/thrust_data, motor_id={0}, curve_id={1}".format(
                motor_id, curve_id
            ),
            f"; lookup key '{header_designation}' must exactly match MOTOR_DATABASE's designation string in rocket_forge.py",
            f"{header_designation} {diameter_mm:.0f} {length_mm:.0f} {delays} {prop_kg:.4f} {total_kg:.4f} {mfr_short}",
        ]
        for t, f in points:
            lines.append(f"{t:.3f} {f:.2f}")

        out_path = OUT / f"{stem}.eng"
        out_path.write_text("\n".join(lines) + "\n")
        written.append((stem, real_designation, diameter_mm, length_mm))

    print(f"written {len(written)} files, missing {len(missing)}")
    for w in written:
        print("  ", w)
    if missing:
        print("MISSING:", missing)


if __name__ == "__main__":
    main()
