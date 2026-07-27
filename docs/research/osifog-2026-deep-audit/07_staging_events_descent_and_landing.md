# Staging, Events, Descent, and Landing

## Event Sequence for Current 3+1 Topology

Each stage has:
- 3× main motors (ascent cluster, "3-ring" configuration)
- 1× central retro motor (structural sleeve + delayed ignition)

### Booster (Stage 1) Event Timeline
1. `t=0`: Launch — booster main motors ignite (ignitionevent="launch")
2. `t=burnout_booster`: Booster main motors burn out
3. `t=burnout_booster + separation_delay`: STAGE_SEPARATION event fires
4. Booster becomes a dropped stage, continues ballistic descent
5. `t=s1_retro_delay`: Retro motor ignites (launch_delay from XML)
6. `t=s1_retro_delay + burn_time`: Retro motor burns out
7. `t=ground_hit`: Booster touches ground

### Sustainer (Stage 0) Event Timeline
1. `t=0`: Sustainer main motors ignite (ignitionevent="burnout" = booster burnout)
2. `t=burnout_sustainer`: Sustainer main motors burn out
3. `t=s0_retro_delay`: Retro motor ignites (launch_delay from XML)
4. `t=s0_retro_delay + burn_time`: Retro motor burns out
5. `t=ground_hit`: Sustainer touches ground

### Anti-Tumble Listener
- Suppresses TUMBLE events (returns false)
- Allows simulation to continue past natural tumble
- Enables reaching ground contact for scoring

## Staging Failure Analysis

The quarantined 839k artifact had:
- Booster separation at 39.000 s
- First apogee at 23.250 s
- **Genuine staging: FAIL** (separation after apogee)

**Root cause**: The booster main motors burned for longer than expected because the retro motor's mass kept the combined vehicle's TWR high enough to continue ascending past the booster burnout, and the separation delay was too long.

## Branch Identity

Branch-to-stage mapping uses string matching:
```python
if "sustainer" in normalized_branch_name: stage_key = "s0"
elif "booster" in normalized_branch_name: stage_key = "s1"
```

**Risk**: If OpenRocket creates additional branches (e.g., from ejection charges, ground-hit events, or simulation artifacts), the mapping could produce wrong stage identities.

## Landing Interpolation

Both Python and Rust use linear interpolation to the GROUND_HIT event time:
```python
f = (hit_time - t1) / (t2 - t1)
final_value = values[idx-1] + f * (values[idx] - values[idx-1])
```

This is correct for the time resolution of OpenRocket's output (0.05s default timestep). Finer interpolation is not needed for the 5 m/s threshold.

## Landing Opportunity Metric (osifog_engine_search.py)

The `_landing_opportunity()` function:
1. Loads actual .eng motor curve
2. Integrates thrust using Simpson's rule over irregular time samples
3. Checks tail-first alignment q at each time sample
4. Computes impulse-weighted vertical opposition
5. Validates available delta-v exceeds required speed reduction

**Key thresholds**:
- `usable_duration + 0.05 >= burn_duration`: tail-first window must cover motor burn
- `burnout <= impact + 0.30`: motor must burn out near ground
- `available_dv >= required_dv * 1.05`: 5% delta-v margin
- `fraction_opposing >= 0.70`: 70% of burn-weighted impulse must oppose velocity
- `fraction_vertical >= 0.70`: 70% of burn-weighted impulse must be vertical

**Gap**: The alignment trace comes from the free-descent diagnostic, not a powered-descent simulation. Actual orientation during powered burn may differ due to thrust-vector-induced aerodynamic torque.
