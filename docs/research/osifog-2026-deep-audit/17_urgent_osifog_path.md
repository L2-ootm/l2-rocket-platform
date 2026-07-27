# Urgent OSIFOG Path to Legal Vehicle

## Phase 1: Fix Known Bugs (Day 1)

### Task 1.1: Remove hardcoded 0.4 kg dry_mass offset
- **File**: `l2_engine/src/mission_adapter.rs:374`
- **Why**: Systematic mass error affects all organic-evolution candidates
- **Proof**: `cargo test -q` passes; re-run parity fixture
- **Risk**: LOW — removing a hardcoded hack

### Task 1.2: Replace _motor_burn_time() with actual .eng integration
- **File**: `osifog_sweep.py:2077-2110`
- **Why**: Stale approximations affect delay calibration
- **Proof**: Compare computed burn times with .eng curve data
- **Risk**: LOW — data-driven replacement

### Task 1.3: Replace MOTOR_PROPELLANT_KG with actual .eng headers
- **File**: `osifog_sweep.py:145-194`
- **Why**: Approximate propellant masses affect scoring fallback
- **Proof**: Parse all .eng headers, verify values match
- **Risk**: LOW — data-driven replacement

## Phase 2: Establish Parity (Days 1-2)

### Task 2.1: Create exposed-sustainer parity fixture
- **Goal**: Compare Rust and OpenRocket on one stable candidate
- **Method**: Generate ORK, run OR simulation, extract Barrowman margins; run Rust evaluator, extract phase margins; compare sign, phase ordering, and values
- **Proof**: Parity within 0.1 cal for all 5 phases
- **Risk**: MEDIUM — may reveal systematic differences

### Task 2.2: Run anti-tumble pre-event equivalence
- **Goal**: Verify no state mutation before TUMBLE event
- **Method**: Same candidate, deterministic seed, with/without listener; compare position/velocity/orientation at each sample before TUMBLE
- **Proof**: All pre-TUMBLE samples match to machine precision
- **Risk**: LOW — controlled experiment

## Phase 3: Expand Search Space (Days 2-3)

### Task 3.1: Widen fin geometry ranges in mission manifest
- **Current**: s1_fin_height_m: [0.33, 0.34, 0.35]
- **Proposed**: s1_fin_height_m: [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
- **Why**: Current range is too narrow for stable exposed sustainer
- **Risk**: LOW — wider search space

### Task 3.2: Add nose ballast mass range
- **Current**: nose_ballast_mass_kg: [0.20, 0.30, 0.40, 0.60, 0.80, 1.00]
- **Proposed**: nose_ballast_mass_kg: [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
- **Why**: More ballast moves CG forward, improving stability
- **Risk**: LOW — heavier nose may reduce apogee

### Task 3.3: Add alternative fin configurations
- **Current**: Only forward grid + aft main
- **Proposed**: Also try aft-only large fins (no forward fins)
- **Why**: Forward fins may hurt tail-first descent
- **Risk**: LOW — exploring alternatives

## Phase 4: Targeted Search (Days 3-5)

### Task 4.1: Run expanded search with relaxed touchdown constraint
- Accept >5 m/s landings initially to find ascent-legal candidates
- Then narrow to <5 m/s with powered landing calibration
- **Proof**: At least one candidate passes all ascent gates

### Task 4.2: Calibrate powered landing delays
- For each ascent-legal candidate, run `adaptive_delay_search()` for both stages
- **Proof**: Both stages land below 5 m/s

### Task 4.3: Run trajectory polish
- Optimize launch azimuth and angle to minimize touchdown displacement
- **Proof**: Both stages land within 100 m of launch

## Phase 5: Authority Verification (Days 5-7)

### Task 5.1: Save verified submission
- Use `save_verified_submission()` to save, reopen, re-extract
- **Proof**: Saved file scores match live simulation

### Task 5.2: Export OpenEarth CSVs
- Generate one CSV per branch for submission
- **Proof**: CSVs contain valid trajectory data

## Decision Points

| Decision | Criteria | Action if NO |
|----------|----------|-------------|
| Expand fin range? | Current 32 candidates all fail stability | YES — widen to [0.20, 0.50] m |
| Add more ballast? | CG too far aft after separation | YES — increase nose ballast range |
| Try aft-only fins? | Forward fins hurt descent | YES — add aft-only configuration |
| Relax touchdown? | No candidates reach powered landing | YES — accept >5 m/s initially |
| Change motor? | Current motors too heavy/light | YES — expand motor pool |
