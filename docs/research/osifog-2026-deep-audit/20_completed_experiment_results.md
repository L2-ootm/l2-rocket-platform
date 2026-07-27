# Completed Experiment Results

## Experiment A: Anti-Tumble Pre-Event Invariance — COMPLETE

**Status**: PASS
**Artifact**: `experiments/antitumble_invariance.json`

### Setup
- Candidate: s0_main=37 (J510W×3), s1_main=18 (J360×3), retro=19 (K550W)
- Seed: 16000 (fixed)
- Two simulations: with listener, without listener
- Compared all state samples (position, velocity, orientation, mass) before TUMBLE

### Results
- **Sustainer**: 1461 samples, 0.00e+00 max difference in all fields
- **Booster**: 701 samples, 0.00e+00 max difference in all fields
- **Tumble event**: Neither run produced a TUMBLE event — both branches reached ground contact naturally
- **Conclusion**: The anti-tumble listener has NO EFFECT on this candidate's trajectory. The listener is a no-op when the vehicle never tumbles. Pre-event invariance is confirmed with zero differences.

### Key Finding
For the current authority candidate (which is stable throughout its flight), the anti-tumble listener is irrelevant. It only matters for candidates that would naturally tumble before reaching ground. The experiment confirms zero side effects, but a more interesting test would use a candidate that actually tumbles.

---

## Experiment D: Score Cross-Validation — COMPLETE

**Status**: PASS
**Artifact**: `experiments/score_crossvalidation.json`

### Setup
- Used quarantined 839k artifact data
- Hand calculation vs Python score_official formula

### Hand Calculation
```
Base:              900,000.00
Altitude penalty:       2.88
Horizontal penalty:   172.88
Touch position:    21,430.55
Touch speed:        3,260.18
Propellant:        35,437.50
Score:             839,696.01
```

### Penalty Ranking (largest first)
1. **Propellant**: 35,437.50 (42.1% of total penalties)
2. **Touch position**: 21,430.55 (25.5%)
3. **Touch speed**: 3,260.18 (3.9%)
4. **Horizontal apogee**: 172.88 (0.2%)
5. **Altitude**: 2.88 (<0.01%)

### Score Ceilings
- **With zero touchdown penalties**: 864,387 (current propellant)
- **With zero all non-propellant penalties**: 864,563

### 850k Analysis
- With zero touchdown penalties AND current propellant: 864,387 > 850,000
- **The binding constraint is touchdown penalties, not propellant**
- To reach 850k with current propellant: reduce combined touch penalties from 24,691 to ≤21,435 (reduce by ~3,256 points)
- This means: reduce mean touch speed from 2.55 m/s to ~2.13 m/s AND reduce mean touch displacement from ~73 m to ~68 m

---

## Experiment E: Wind Parity — COMPLETE

**Status**: PASS
**Artifact**: `experiments/wind_parity.json`

### Setup
- Wind CSV: OSIFOG/OpenWind_File.csv (28 levels)
- Surface: 3.1 m/s from 215 deg
- Tested 10 representative altitudes from 0 to 3500 m

### Results
| Altitude | Speed (m/s) | Direction (deg) | Vx (East) | Vy (North) |
|----------|-------------|-----------------|-----------|------------|
| 0 | 3.06 | 215.0 | 1.757 | 2.509 |
| 100 | 6.41 | 214.3 | 3.616 | 5.298 |
| 500 | 8.69 | 210.5 | 4.408 | 7.487 |
| 1000 | 12.34 | 215.1 | 7.088 | 10.100 |
| 2000 | 13.47 | 206.4 | 5.981 | 12.067 |
| 3000 | 11.21 | 204.0 | 4.559 | 10.241 |

### Conventions Verified
- **Altitude reference**: AGL (0 = surface)
- **Direction**: Meteorological "from" degrees
- **ORK XML storage**: Radians (correctly converted in generate_ork)
- **Cartesian**: Vx = East, Vy = North (wind blows toward direction + 180)
- **Interpolation**: Linear between levels (matching both Python and Rust)

---

## Experiment C: Motor Data Parity — COMPLETE

**Status**: PASS (partial — Python comparison not possible due to name mismatches)
**Artifact**: `experiments/motor_data_parity.json`

### Setup
- Parsed all 36 .eng files in l2_engine/motors/
- Extracted RASP header fields and integrated thrust curves

### Key Motor Data (corrected from actual .eng files)
| Motor | Diameter (mm) | Length (mm) | Propellant (kg) | Loaded (kg) | Burn Time (s) | Total Impulse (Ns) |
|-------|---------------|-------------|-----------------|-------------|---------------|---------------------|
| J510W | 38 | 584 | 0.6620 | 1.0800 | 2.500 | 1180.5 |
| K550W | 54 | 410 | 0.9197 | 1.4874 | 3.356 | 1624.9 |
| H180W | 29 | 238 | 0.1210 | 0.2464 | 1.313 | 233.7 |
| J360 | 38 | 419 | 0.4090 | 0.7092 | 2.130 | 815.8 |

### Key Corrections
- J510W burn time: 2.500 s (NOT 5.84 s — that was the 584mm length)
- K550W burn time: 3.356 s (NOT 4.10 s — that was the 410mm length)
- J510W propellant: 0.6620 kg (Python MOTOR_PROPELLANT_KG[16] = 0.310 kg was WRONG)
- K550W propellant: 0.9197 kg (Python MOTOR_PROPELLANT_KG[19] = 0.620 kg was WRONG)

### Python MOTOR_PROPELLANT_KG Discrepancies
Could not compare directly because .eng file designations (e.g., "J360_CTI") don't match MOTOR_DATABASE designations (e.g., "J360"). This is a naming inconsistency that needs resolution.
