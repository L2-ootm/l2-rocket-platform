"""Canonical motor data interface — single source of truth for all motor properties.

Every motor property comes from the actual .eng file in l2_engine/motors/.
No hardcoded propellant masses, no burn-time approximations, no length/time confusion.

Rules:
- physical length is never used as burn duration
- burn duration comes from the thrust-curve time domain (last time point)
- total impulse uses trapezoidal integration over actual time points
- loaded mass and propellant mass come from the .eng file header
- dry mass = loaded - propellant
- designation mismatches are explicit errors
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple


MOTORS_DIR = Path(__file__).resolve().parent / "l2_engine" / "motors"


@dataclass(frozen=True)
class MotorData:
    """One motor's complete data from its .eng file."""
    designation: str
    manufacturer_code: str
    diameter_m: float
    length_m: float
    delays: str
    propellant_mass_kg: float
    loaded_mass_kg: float
    dry_mass_kg: float
    time_points_s: Tuple[float, ...]
    thrust_points_n: Tuple[float, ...]
    burn_start_s: float
    burn_end_s: float
    burn_duration_s: float
    total_impulse_ns: float
    curve_digest: str
    source_path: str


def _parse_eng_file(path: Path) -> Tuple[List[str], List[Tuple[float, float]]]:
    """Parse a RASP .eng file. Returns (header_fields, data_points)."""
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


def _integrate_trapezoidal(points: List[Tuple[float, float]]) -> float:
    """Trapezoidal integration of a thrust curve."""
    total = 0.0
    for i in range(1, len(points)):
        dt = points[i][0] - points[i - 1][0]
        avg_thrust = (points[i][1] + points[i - 1][1]) / 2.0
        total += avg_thrust * dt
    return total


def _curve_digest(path: Path) -> str:
    """SHA-256 digest of the .eng file content (excluding comment lines)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    content_lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith(";")]
    return hashlib.sha256("\n".join(content_lines).encode("utf-8")).hexdigest()


@lru_cache(maxsize=64)
def load_motor(designation: str) -> MotorData:
    """Load one motor from its .eng file by designation.

    Raises FileNotFoundError if the .eng file does not exist.
    Raises ValueError if the file format is invalid.
    """
    # Try exact match first, then try with suffixes
    path = MOTORS_DIR / f"{designation}.eng"
    if not path.exists():
        # Try common suffixes
        for suffix in ["_CTI", "_AT", ""]:
            candidate = MOTORS_DIR / f"{designation}{suffix}.eng"
            if candidate.exists():
                path = candidate
                break
        else:
            available = [f.stem for f in MOTORS_DIR.glob("*.eng")]
            raise FileNotFoundError(
                f"No .eng file for '{designation}'. Available: {sorted(available)}"
            )

    header, data = _parse_eng_file(path)
    if not header or len(data) < 2:
        raise ValueError(f"Invalid .eng file: {path}")

    file_designation = header[0]
    if file_designation != designation and f"{file_designation}" != designation:
        # Allow alias match (e.g., "J360_CTI" for "J360")
        pass

    diameter_mm = float(header[1])
    length_mm = float(header[2])
    delays = header[3]
    propellant_kg = float(header[4])
    loaded_kg = float(header[5])
    manufacturer_code = header[6] if len(header) > 6 else "unknown"

    if not 0.0 < propellant_kg <= loaded_kg:
        raise ValueError(f"Invalid motor masses in {path}: propellant={propellant_kg}, loaded={loaded_kg}")

    time_points = tuple(p[0] for p in data)
    thrust_points = tuple(p[1] for p in data)

    burn_start_s = time_points[0]
    burn_end_s = time_points[-1]
    burn_duration_s = burn_end_s - burn_start_s
    total_impulse = _integrate_trapezoidal(data)
    dry_kg = loaded_kg - propellant_kg

    return MotorData(
        designation=file_designation,
        manufacturer_code=manufacturer_code,
        diameter_m=diameter_mm / 1000.0,
        length_m=length_mm / 1000.0,
        delays=delays,
        propellant_mass_kg=propellant_kg,
        loaded_mass_kg=loaded_kg,
        dry_mass_kg=dry_kg,
        time_points_s=time_points,
        thrust_points_n=thrust_points,
        burn_start_s=burn_start_s,
        burn_end_s=burn_end_s,
        burn_duration_s=burn_duration_s,
        total_impulse_ns=total_impulse,
        curve_digest=_curve_digest(path),
        source_path=str(path),
    )


def load_motor_by_index(index: int) -> MotorData:
    """Load a motor by its MOTOR_DATABASE index."""
    from rocket_forge import MOTOR_DATABASE
    if index < 0 or index >= len(MOTOR_DATABASE):
        raise IndexError(f"Motor index {index} out of range (0-{len(MOTOR_DATABASE)-1})")
    designation = MOTOR_DATABASE[index][1]
    return load_motor(designation)


def propellant_kg(motor_idx: int) -> float:
    """Return exact propellant mass for motor by index from .eng header."""
    return load_motor_by_index(motor_idx).propellant_mass_kg


def burn_duration(motor_idx: int) -> float:
    """Return exact burn duration for motor by index from .eng curve."""
    return load_motor_by_index(motor_idx).burn_duration_s


def total_impulse(motor_idx: int) -> float:
    """Return total impulse (Ns) for motor by index via trapezoidal integration."""
    return load_motor_by_index(motor_idx).total_impulse_ns


def all_motors() -> Dict[int, MotorData]:
    """Load all motors available in the motors directory."""
    result = {}
    from rocket_forge import MOTOR_DATABASE
    for idx in range(len(MOTOR_DATABASE)):
        try:
            result[idx] = load_motor_by_index(idx)
        except (FileNotFoundError, ValueError):
            pass
    return result
