# Revised Urgent Path (v2)

## Evidence-Gated Implementation Order

### Phase 1: Correct Motor/Mass Data (dependency: none)

**Task 1.1**: Replace MOTOR_PROPELLANT_KG with actual .eng header values
- **Why**: J510W propellant is 0.6620 kg, not 0.310 kg. K550W is 0.9197 kg, not 0.620 kg. These errors affect the scoring fallback and propellant penalty calculation.
- **Files**: `osifog_sweep.py` lines 145-194
- **Proof**: Parse all .eng headers, verify values match, run test suite
- **Best case**: 30 minutes
- **Likely case**: 1 hour (including test fixes)
- **Failure condition**: Tests fail after change
- **Completion proof**: `python -m pytest tests/test_osifog_sweep.py -q` passes; all values match .eng headers

**Task 1.2**: Replace _motor_burn_time() with actual .eng curve integration
- **Why**: J510W burn time is 2.500 s, not 4.5 s. K550W is 3.356 s, not 3.2 s. Stale values affect delay calibration.
- **Files**: `osifog_sweep.py` lines 2077-2110
- **Proof**: New burn times match .eng curve last-time-point within 0.01 s
- **Best case**: 30 minutes
- **Likely case**: 1 hour
- **Failure condition**: Burn times don't match .eng data
- **Completion proof**: All 36 motors have burn times matching .eng last data point

**Task 1.3**: Remove 0.4 kg dry_mass offset
- **Why**: Systematic mass error for organic-evolution candidates
- **Files**: `l2_engine/src/mission_adapter.rs` line 374
- **Proof**: `cargo test` passes; one candidate mass matches expected value
- **Best case**: 15 minutes
- **Likely case**: 30 minutes
- **Failure condition**: Tests fail after removal
- **Completion proof**: `cargo test -q` passes

### Phase 2: Establish Parity (dependency: Phase 1)

**Task 2.1**: Create exposed-sustainer stability fixture
- **Why**: Rust/OpenRocket margin comparison never completed
- **Proof**: One candidate compared across both engines, margin agreement < 0.1 cal at all 5 phases
- **Best case**: 4 hours
- **Likely case**: 1 day
- **Failure condition**: Margins disagree by > 0.2 cal
- **Completion proof**: Test output shows all 5 phases within tolerance
- **Risk**: MEDIUM — may reveal systematic differences

**Task 2.2**: Establish wind parity
- **Why**: Rust and OpenRocket wind models may differ
- **Proof**: Wind vector agreement < 0.1 m/s at all 28 levels
- **Best case**: 2 hours
- **Likely case**: 4 hours
- **Failure condition**: Wind vectors disagree
- **Completion proof**: Test output shows all levels within tolerance

### Phase 3: Expand Search (dependency: Phase 2)

**Task 3.1**: Expand fin geometry search space
- **Why**: Current fin height range [0.33, 0.35] m is too narrow
- **Proposed ranges** (after physical containment check):
  - s1_fin_height_m: [0.25, 0.30, 0.35, 0.40, 0.45] m
  - nose_ballast_mass_kg: [0.5, 0.75, 1.0, 1.25, 1.5] kg
  - s1_fin_root_m: [0.18, 0.20, 0.22, 0.25] m
- **Proof**: At least one candidate passes 1.5-cal stability gate in Rust
- **Best case**: 2 hours
- **Likely case**: 4 hours
- **Failure condition**: No candidate passes 1.5-cal in Rust
- **Completion proof**: Test output shows margin >= 1.5 at all 5 phases

**Task 3.2**: Verify physical containment of expanded ranges
- **Why**: Wider ranges may produce physically impossible geometries
- **Proof**: `validate_candidate_geometry()` passes for all expanded-range candidates
- **Best case**: 1 hour
- **Likely case**: 2 hours
- **Failure condition**: Many candidates fail geometry validation
- **Completion proof**: Geometry validation passes for representative samples

### Phase 4: Targeted Stability Search (dependency: Phase 3)

**Task 4.1**: Run expanded stability search
- **Why**: Find at least one ascent-legal candidate
- **Proof**: One candidate passes all ascent gates in OpenRocket
- **Best case**: 4 hours (compute time)
- **Likely case**: 1 day
- **Failure condition**: No candidate passes in OpenRocket
- **Completion proof**: OpenRocket simulation shows stable ascent

### Phase 5: Powered Landing (dependency: Phase 4)

**Task 5.1**: Calibrate landing delays
- **Why**: Landing speed must be < 5 m/s
- **Proof**: Both stages land below 5 m/s in OpenRocket
- **Best case**: 4 hours
- **Likely case**: 1 day
- **Failure condition**: No delay achieves < 5 m/s for both stages
- **Completion proof**: OpenRocket simulation shows legal landings

**Task 5.2**: Run trajectory polish
- **Why**: Minimize touchdown displacement
- **Proof**: Score improvement from trajectory optimization
- **Best case**: 2 hours
- **Likely case**: 4 hours
- **Failure condition**: No trajectory improvement found
- **Completion proof**: Score improves by > 1000 points

### Phase 6: Authority Verification (dependency: Phase 5)

**Task 6.1**: Save/reopen verification
- **Why**: Submission requires saved/reopened artifact
- **Proof**: Saved .ork passes all gates on reopen
- **Best case**: 2 hours
- **Likely case**: 4 hours
- **Failure condition**: Saved artifact fails verification
- **Completion proof**: `inspect_saved_submission()` returns legal=True

## Total Estimated Timeline

| Phase | Best Case | Likely Case | Dependencies |
|-------|-----------|-------------|-------------|
| Phase 1 | 1.5 hours | 2.5 hours | None |
| Phase 2 | 6 hours | 1.5 days | Phase 1 |
| Phase 3 | 3 hours | 6 hours | Phase 2 |
| Phase 4 | 4 hours | 1 day | Phase 3 |
| Phase 5 | 6 hours | 1.5 days | Phase 4 |
| Phase 6 | 2 hours | 4 hours | Phase 5 |
| **Total** | **22.5 hours** | **~6 days** | |

## Failure Conditions

If Phase 3 produces no stable candidate:
- **Action**: Redesign motor cage topology (different motor pairing, different body diameter)
- **Risk**: May require fundamental architecture change

If Phase 5 cannot achieve < 5 m/s landings:
- **Action**: Try different retro motors (larger impulse, different thrust profile)
- **Risk**: May require motor pool expansion

If Phase 6 fails saved/reopen verification:
- **Action**: Debug serialization, check anti-tumble persistence
- **Risk**: May reveal OpenRocket compatibility issue
