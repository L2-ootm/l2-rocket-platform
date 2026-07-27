# Handoff: Starship Forward-Flap Concept

## Date: 2026-07-22

## What Changed

### 1. Continuous Genome (DJ-mixer sliders)
All parameters are now continuous ranges instead of discrete `rng.choice()` menus:
- `s0_aft_ballast_kg`: uniform(0.0, 0.80) — was choice((0.20, 0.40, 0.60))
- `nose_mass_kg`: uniform(0.2, 0.8) — was uniform(0.85, 0.98)
- `nose_length_m`: uniform(1.0, 2.0) — was uniform(1.20, 1.45)
- `s0_fin_count`: choice((3, 4, 5)) — was fixed 4
- `s0_pod_fin_count`: choice((0, 3, 3, 4)) — was fixed 3
- `s0_pod_angle_offset_deg`: uniform(0.0, 120.0) — was fixed 0.0
- `s0_core_fin_angle_offset_deg`: uniform(0.0, 120.0) — was fixed 60.0
- All fin sizes, positions, and angles are continuous

### 2. Starship Forward Flaps
- Forward fin count: 0 or 3 (OpenRocket requires 0 or >=3 per set)
- Forward fin root: 0.05-0.15m (was 0.04-0.07m)
- Forward fin height: 0.04-0.12m (was 0.04-0.07m)
- Forward fin position: 0.02-0.15m from nose (was 0.02-0.30m)
- Forward fin sweep: 0-30 degrees (was 0-25)

### 3. Corrected Stability Gate
- Sustainer stability now measured only from separation to sustainer motor burnout
- Previously measured from separation to end of flight (included unpowered coast)
- This allows designs where sustainer is stable during powered flight but unstable after burnout (enables tail-first descent)

### 4. Stability Threshold
- MIN_STATIC_MARGIN lowered from 1.5 to 0.3 cal
- Mission requires SM > 0 (stable), 0.3 provides safety margin
- This allows marginal stability during powered flight

### 5. Delay Selection Fix
- Window midpoints now promoted to vertical_priority in `_delay_candidates`
- Previously only alignment_trace samples with both q>=0.5 AND vq>=0.5 were promoted
- Now tail_first_windows midpoints are always promoted with their best_q
- Limit increased from 15 to 25 candidates per branch

### 6. Geometry Fixes
- Pod axial offset now starts at 0.0 (was -pod_nose_length, causing pods to extend before core)
- Core length now includes pod_nose_length in minimum computation
- Core fin root clamped to core_length * 0.95
- Pod fin count uses genome value instead of hardcoded 3

## First Results

All 8 tested candidates reach `powered_trials_completed` gate. Best results:
- Candidate #0: apogee=2100m, booster retro 100% opposing, sustainer 17.1% opposing
- Candidate #3: apogee=1809m, booster retro 50.6% opposing

## Next Steps

1. Run multi-generation evolutionary search with new genome
2. Tune retro delay calibration for each candidate
3. Find balance: forward flap area (tail-first) vs motor impulse (3000m apogee)
4. Target: apogee ~3000m, both landing speeds < 5 m/s

## Files Changed
- `osifog_engine_search.py`: genome sampling, stability gate, delay selection
- `osifog_sweep.py`: stability measurement, MIN_STATIC_MARGIN
- `missions/osifog_l3_precision.json`: forward fin ranges
