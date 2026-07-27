# Adversarial Review

## Unsupported Claims

1. **"Forward grid fins are the only path to tail-first descent"** — CONTRADICTED. The search space has only explored forward grid fins + aft main fins. Alternative topologies (distributed area, body transitions, asymmetric drag) have not been tested. The current search space constrains the solution to this one pattern.

2. **"The anti-tumble script does not affect pre-event trajectory"** — UNVERIFIED. No controlled experiment has compared trajectories with and without the listener. The claim is plausible (the script only intercepts TUMBLE events) but not empirically proven.

3. **"The Rust proxy is a faithful approximation of OpenRocket"** — UNVERIFIED. No parity fixture has been completed. The Mach proxy blocked the only attempted comparison. The Rust Barrowman model and OpenRocket's implementation may differ in transonic region.

4. **"The 839k score is achievable with genuine staging"** — INFERENCE. The quarantined artifact had separation after apogee. With genuine staging (separation before apogee), the apogee and landing characteristics would differ. The score may be lower or higher.

## Missed Branches

1. **Transition geometry**: The current 3+1 cage assumes constant body diameter. A transition section between stages could improve both ascent stability and descent behavior.

2. **Central landing motor torque**: The central retro motor creates asymmetric torque during descent. This has not been studied — the search assumes axisymmetric behavior.

3. **Ballast distribution during descent**: The nose ballast affects both ascent stability and descent inertia. The current search treats these as independent — they are coupled.

4. **Separation angular rate**: The vehicle's orientation at separation affects the initial conditions for descent. The current search does not control this variable.

## Incorrect Units or Frames

1. **Wind direction convention**: CSV uses degrees "from" direction. ORK XML stores radians. The conversion in `generate_ork()` uses `math.radians(d)` which is correct for the "from" convention.

2. **OpenRocket body-Z axis**: Positive Z points through the nose. Theta is elevation above horizontal. The `_retro_burn_diagnostic()` correctly handles this convention.

3. **East/North sign convention**: OpenRocket uses East/North from launch point. The score formula uses signed East/North. The extraction in `run_sim()` correctly reads TYPE_POSITION_X (East) and TYPE_POSITION_Y (North).

## Hidden Mission-Specific Hardcoding

1. **`MAX_MACH = 0.95`**: Conservative margin below Mach 1.0. This is appropriate but reduces search space.

2. **`MIN_STATIC_MARGIN = 1.5`**: Directly from mission rules. Correct.

3. **`LAUNCH_ROD_M = 6.0`**: Maximum allowed. Correct.

4. **Motor cage configuration**: The 3+1 topology is hardcoded throughout. This is by design for the Falcon vehicle but limits exploration.

5. **Body diameter 148mm**: Appears in the submission candidate but is not a mission constraint. The 4.0 m height limit is the real constraint.

## Stale Artifacts

1. **`osifog_sweep.py::BURN_TIMES`**: Hardcoded approximation table. Stale.
2. **`osifog_sweep.py::MOTOR_PROPELLANT_KG`**: Hand-estimated values. Stale.
3. **`osifog_sweep.py::RETRO_MOTORS`**: Only lists indices 0, 1, 2 (F-class). The actual retro motor pool is much larger.
4. **`osifog_sweep.py::SUSTAINER_MAINS` and `BOOSTER_MAINS`**: Hardcoded motor index lists. The mission manifest now defines the allowed pool.

## Overconfident Simulation Conclusions

1. **Booster landing at 3.5135 m/s**: This was achieved in OpenRocket with a specific motor (H180W) and delay (33.104s). It is a single data point, not a general result. The booster's descent behavior depends on its specific fin geometry, mass, and initial conditions.

2. **Sustainer q=0.9006**: This alignment metric was computed from a free-descent diagnostic, not a powered descent. The actual powered descent may have different alignment.

## Recommendations

### Immediate (Do Now)
1. Fix the 0.4 kg dry_mass offset
2. Replace stale burn-time and propellant-mass approximations
3. Create parity fixture for Rust/OpenRocket margin comparison
4. Expand fin geometry search space

### Short-Term (This Week)
1. Complete parity validation
2. Run targeted stability search with expanded space
3. Calibrate landing delays for best ascent candidate
4. Test anti-tumble pre-event invariance

### Medium-Term (After Competition)
1. Migrate to MAP-Elites architecture
2. Implement powered descent in Rust proxy
3. Add body transition support to AST
4. Migrate CKG to SQLite
