# Mass, Motors, and Inertia

## Mass Calculation Audit

### Rust (mass_calculator.rs — 637 lines)
- **Body tubes**: Thin-wall cylinder: `density × π × (R² - (R-t)²) × L`
- **Nose cones**: 200-slice numerical integration of Haack-series profile
- **Fins**: Shoelace planform area × thickness × density × cross_section_factor (0.85 airfoil, 1.0 otherwise)
- **Motors**: From .eng curve header (loaded_mass_kg, propellant_mass_kg)
- **Motor mount tubes**: Computed in enrich_ast_motor_mounts: `density × π × (R_outer² - R_inner²) × L`
- **Point masses**: BALLAST/PAYLOAD nodes → PointMassGeometry

### Python (physical_geometry.py — 350 lines)
- **Ballast cylinders**: `density × π × r² × L` (AxialCylinder)
- **Ballast rods**: 3× steel rods in Falcon cage gaps
- **Motor mounts**: AxialCylinders with declared mass from density

### Known Issues

**BUG (MEDIUM)**: `mission_adapter.rs:374` adds hardcoded `+0.4 kg` dry_mass offset:
```rust
let dry_mass = mass + 0.4;
```
This is a residual calibration hack that adds systematic error to every organic-evolution candidate.

**BUG (HIGH)**: `osifog_sweep.py::MOTOR_PROPELLANT_KG` dictionary uses approximate values:
```python
MOTOR_PROPELLANT_KG = {
    0: 0.028,   # F50T
    19: 0.620,  # K550W
    ...
}
```
These are hand-estimated, not from actual .eng headers. The actual consumed propellant is computed via mass difference in `run_sim()`, but the fallback in `score_official()` uses these approximate values.

**BUG (HIGH)**: `osifog_sweep.py::_motor_burn_time()` uses hardcoded approximation table:
```python
BURN_TIMES = {
    0: 1.4,   # F50T
    16: 4.5,  # J510W
    19: 3.2,  # K550W
    ...
}
```
These are stale approximations. The actual burn times from .eng curves are:
- J510W: 5.84 s (from .eng header, length 0.584 m, but actual burn duration from curve data)
- K550W: 4.10 s (from .eng header)

The stale values affect delay calibration in `compute_retro_delay()`.

## Motor Curve Integration

### Rust (motor_db.rs)
- **Impulse-weighted mass loss**: `total_mass - propellant_mass × (cumulative_impulse(t) / total_impulse)`
- **Trapezoidal integration** for cumulative impulse
- **Multi-motor clusters**: Summed at unified time knots via `aggregate_motor_curves()`
- **Correct**: Avoids the naive time-linear formula (Pitfall 3)

### Python (osifog_engine_search.py)
- **`_load_motor_curve()`**: Reads .eng files directly
- **`_curve_impulse()`**: Piecewise-linear integration with irregular spacing
- **`_interpolate_curve()`**: Linear interpolation of thrust at arbitrary time
- **Correct**: Uses actual .eng data, not approximations

### Parity Status
**NOT COMPARED.** The Rust and Python motor integration paths have not been validated against each other on the same motor curve.

## Dynamic CG During Burn

### Rust (mass_calculator.rs::dynamic_cg_at)
- Propellant modeled as point mass at motor CG position
- As propellant depletes, CG shifts toward dry motor CG
- Standard engineering approximation, matches OpenRocket

### Python
- No dynamic CG model — static mass at generation time
- OpenRocket handles dynamic CG internally during simulation

## Inertia Tensor

### Rust (mass_calculator.rs::principal_inertia)
- **Full principal moments** via parallel-axis theorem:
  - Body tube: hollow cylinder I_xx, I_yy = I_zz
  - Nose cone: 200-slice numerical integration
  - Fins: approximate transverse unit inertia `(span² + 2×width²)/24 + (span/2 + body_radius)²/2`
  - Motor: point mass at CG
  - Motor mount tube: hollow cylinder
  - Point masses: m × r²
  - Parachute: point mass

### Python
- No inertia calculation — OpenRocket handles internally

## Propellant Consumption

### Score Formula
```
m_prop = initial_mass - sum(landed_final_masses)
```

### Implementation (osifog_sweep.py::run_sim)
```python
initial_mass_kg = float(br0.get(TYPE_MASS)[0])
# ... per branch ...
final_masses_kg += final_mass
m["m_prop_kg_actual"] = max(0.0, initial_mass_kg - final_masses_kg)
```

**Potential issue**: If a branch does not reach ground (no GROUND_HIT event), its final mass is not counted, making consumed propellant appear smaller.
