# OSIFOG Level 3 — Engine Integration Spec
## Wiring Real Wind, Atmosphere & Corrected Scoring into l2_engine

**Status**: Active implementation — 2026-07-19  
**Authority**: This doc supersedes any inline comments in osifog_sweep.py and generate_winner.py.

---

## 1. The Problem We Are Solving

The pure-OpenRocket pipeline (`osifog_sweep.py`) keeps hitting OR geometry validators that abort 
simulations before physics even runs. These same constraints were already handled and solved in 
our Rust engine (`l2_engine`) months ago. The correct architecture is:

```
┌──────────────────────────────────────────────────────────────────────┐
│  LAYER 1: Rust Engine (l2_engine/src/bin/ast_eval)                   │
│  • Fast: 300–1000 candidates/second via Rayon                        │
│  • Handles geometry fitment, stage continuity, motor diameter checks │
│  • OSIFOG scoring formula (corrected) baked in                       │
│  • Real wind profile from OpenWind_File.csv                          │
│  • Real atmosphere from competition conditions                        │
│  ► 300 pop × 40 gen = 12,000 sims in ~60s                           │
├──────────────────────────────────────────────────────────────────────┤
│  LAYER 2: CKG (Continuous Knowledge Graph)                           │
│  • Sub-graph penalization of known-bad topologies                    │
│  • Caches: retro-abort failures, stage-diameter mismatches, tumble   │
│  • Guides future mutations away from death zones                     │
├──────────────────────────────────────────────────────────────────────┤
│  LAYER 3: OpenRocket Validation (--validate-openrocket N)            │
│  • Ground-truth Barrowman stability + real OR physics                │
│  • Sequential (single JVM), elite only (top 6–10 candidates)        │
│  • Calibrates Rust proxy bias per topological signature              │
├──────────────────────────────────────────────────────────────────────┤
│  LAYER 4: Polisher (run_polisher.py)                                 │
│  • Fine-tunes ballast mass (nose/aft) around OR-validated elite      │
│  • Targets: apogee err < 0.5m, touchdown speed < 1.0 m/s            │
│  • Writes final .ork for submission                                  │
└──────────────────────────────────────────────────────────────────────┘
```

**The old `osifog_sweep.py` tried to be all 4 layers at once via OR — brittle and slow.**

---

## 2. What Data We Have (Already Collected)

### 2a. Wind Profile — `OSIFOG/OpenWind_File.csv`

- Altitude (m AGL), Speed (m/s), Direction (deg from North), Std_Dev (m/s)
- Date: 2022-12-22 (closest available to specified 2015 date)
- Key: WNW (~288 deg) wind throughout the column.
- Optimal launch azimuth INTO wind = ~108 deg heading.

### 2b. Atmosphere — Competition Spec

```
Temperature:  290.55 K  (17.4°C)
Pressure:     100,000 Pa
Humidity:     0.91 (91%)
Launch site:  28.5621°N, 80.5772°W, 3m MSL
Model:        Extended ISA
```

### 2c. OSIFOG Scoring Formula (CORRECTED)

```
Score = 900,000
      - 3000 * (h_ap - 3000)^2             # Apogee altitude penalty
      - 16 * (E_ap^2 + N_ap^2)             # Apogee horizontal drift (×16 weight!)
      - 2 * (mean(E_land)^2 + mean(N_land)^2)  # Touchdown position (signed mean)
      - 500 * mean(V_total)^2              # Touchdown total speed = sqrt(vz^2+vxy^2)
      - 7500 * m_prop_kg                   # Propellant mass penalty
```

Max = 900,000. Penalties stack without floor (negative scores possible).

### 2d. Hard Constraints (DISQUALIFY if violated)

- Max Mach <= 0.95 during powered flight
- All stage touchdown speeds <= 5.0 m/s (TOTAL speed, not just vertical)
- No simulation aborts
- No recovery devices (parachutes forbidden)
- Motor delay = "none" (plugged, no ejection charge)
- Launch rod <= 6.0m (we use 6.0m for max stability off the rail)

---

## 3. Integration Plan — What to Add to l2_engine

### 3a. New Mission File: `missions/osifog_l3_precision.json`

```json
{
  "name": "OSIFOG Level 3 Precision",
  "target_apogee": 3000.0,
  "apogee_tolerance_m": 0.5,
  "max_mach": 0.95,
  "scoring": {
    "base_score": 900000.0,
    "terms": [
      {"name": "apogee_altitude", "metrics": ["apogee_m"], "reference": [3000.0], "power": 2, "coefficient": -3000.0},
      {"name": "apogee_horizontal", "metrics": ["apogee_east_m", "apogee_north_m"], "reference": [0.0, 0.0], "power": 2, "coefficient": -16.0},
      {"name": "touchdown_position", "metrics": ["stage_landing_east_m", "stage_landing_north_m"], "reference": [0.0, 0.0], "power": 2, "coefficient": -2.0, "aggregate": "mean_over_stages"},
      {"name": "touchdown_speed", "metrics": ["stage_landing_total_speed_ms"], "reference": [0.0], "power": 2, "coefficient": -500.0, "aggregate": "mean_over_stages"},
      {"name": "propellant_used", "metrics": ["total_prop_mass_kg"], "reference": [0.0], "power": 1, "coefficient": -7500.0}
    ]
  },
  "atmosphere": {
    "model": "extended_isa",
    "base_temperature_k": 290.55,
    "base_pressure_pa": 100000.0,
    "humidity": 0.91,
    "launch_altitude_m": 3.0,
    "launch_lat": 28.5621,
    "launch_lon": -80.5772
  },
  "wind": {
    "source": "csv",
    "path": "../OSIFOG/OpenWind_File.csv",
    "altitude_ref": "agl",
    "column_order": ["altitude_m", "speed_ms", "direction_deg", "std_dev_ms"]
  },
  "launch": {
    "rod_length_m": 6.0,
    "azimuth_deg": 108.0,
    "angle_from_vertical_deg": 0.0,
    "seed": 16000
  },
  "constraints": {
    "min_static_margin": 1.5,
    "max_mach": 0.95,
    "max_touchdown_speed_ms": 5.0,
    "require_all_stages_land": true,
    "no_recovery_devices": true
  },
  "topology": {
    "stage_count": 2,
    "retro_motors": true,
    "ballast_points": ["nose", "mid", "aft"],
    "separation_event": "burnout"
  },
  "evolution": {
    "population": 400,
    "generations": 60,
    "elite_count": 8,
    "validate_openrocket": 6,
    "calibrate_every": 5,
    "polish": true
  }
}
```

### 3b. Changes to `l2_engine/src/ast.rs` — OSIFOG Scorer

**Superseded 2026-07-19** — this section originally proposed a hardcoded
`score_osifog_2026()` function gated by `mission.scoring.formula ==
"osifog_2026"`. That was implemented first, then replaced same-day by a
generic, data-driven `ScoringTable`/`ScoringTerm` evaluator (see
`docs/plans/2026-07-19-rust-scoring-and-ga-design.md`) so a new competition's
formula never requires a Rust code change, only a new mission JSON `scoring`
block (as shown in section 3a above). `score_osifog_2026` no longer exists in
`ast.rs` — do not re-add it.

### 3c. New SimResult fields needed in `sim_core/`

The current SimResult struct needs these additions:

```rust
pub struct SimResult {
    // Existing:
    pub apogee_m: f64,
    pub max_mach: f64,
    pub flight_time_s: f64,
    pub total_prop_mass_kg: f64,

    // NEW — needed for OSIFOG scoring:
    pub apogee_east_m: f64,       // East displacement at apogee
    pub apogee_north_m: f64,      // North displacement at apogee
    pub stage_landings: Vec<StageLanding>,
}

pub struct StageLanding {
    pub stage_idx: usize,
    pub east_m: f64,
    pub north_m: f64,
    pub vz_ms: f64,               // Vertical velocity at touchdown (m/s)
    pub vxy_ms: f64,              // Horizontal velocity at touchdown (m/s)
    pub total_speed_ms: f64,      // sqrt(vz^2 + vxy^2)
}
```

### 3d. Wind Model (`sim_core/wind.rs`)

```rust
pub struct WindLevel {
    pub altitude_m: f64,
    pub speed_ms: f64,
    pub direction_deg: f64,  // From North, clockwise
    pub std_dev_ms: f64,
}

pub struct WindProfile {
    levels: Vec<WindLevel>,
}

impl WindProfile {
    /// Load from the OSIFOG CSV (altitude, speed, direction, std_dev)
    pub fn from_csv(path: &str) -> Result<Self, Box<dyn Error>> { ... }

    /// Linear interpolation between levels; returns (east_force, north_force) components
    /// Wind FROM direction=288 means wind blows TOWARDS 108 deg.
    pub fn wind_vector_at(&self, alt_m: f64) -> (f64, f64) {
        let level = self.interpolate(alt_m);
        let from_rad = level.direction_deg.to_radians();
        // Wind blows FROM direction_deg — convert to velocity vector
        let east = -level.speed_ms * from_rad.sin();
        let north = -level.speed_ms * from_rad.cos();
        (east, north)
    }
}
```

### 3e. Atmosphere Model (`sim_core/atmosphere.rs`)

```rust
pub struct AtmosphereConditions {
    pub base_temp_k: f64,       // 290.55
    pub base_pressure_pa: f64,  // 100000
    pub humidity: f64,          // 0.91
    pub launch_alt_m: f64,      // 3.0
}

impl AtmosphereConditions {
    /// Air density at altitude (Extended ISA with humidity correction)
    pub fn density_kg_m3(&self, alt_m: f64) -> f64 { ... }

    /// Speed of sound at altitude
    pub fn speed_of_sound_ms(&self, alt_m: f64) -> f64 { ... }

    /// Mach number for given velocity at altitude
    pub fn mach(&self, velocity_ms: f64, alt_m: f64) -> f64 {
        velocity_ms / self.speed_of_sound_ms(alt_m)
    }
}
```

---

## 4. organic_loop.py Changes

Add `--mission` flag that reads the JSON and:
1. Passes `wind.path` to ast_eval as a field in the batch JSON
2. Uses the mission's `scoring.terms` table instead of generic apogee-proximity scoring
3. Embeds the wind profile in the `.ork` when exporting for OR validation
4. Enforces `constraints.max_touchdown_speed_ms` and `require_all_stages_land`

### Invocation after integration:

```bash
# Full run — ~3–6 hours, best results
python organic_loop.py \
  --evaluator rust \
  --mission missions/osifog_l3_precision.json \
  --population 400 \
  --generations 60 \
  --elite-count 8 \
  --validate-openrocket 6 \
  --calibrate-every 5 \
  --polish \
  --out designs/osifog_level3

# Quick sanity check (< 5 min)
python organic_loop.py \
  --evaluator rust \
  --mission missions/osifog_l3_precision.json \
  --population 20 \
  --generations 5 \
  --out designs/osifog_test
```

---

## 5. Comparison: Old vs New Pipeline

| Aspect | osifog_sweep.py (old) | organic_loop.py + l2_engine (new) |
|---|---|---|
| Speed per candidate | ~30s (JVM cold start) | ~2ms (Rust) |
| Candidates per run | ~100 total | 12,000–24,000 per run |
| Geometry validation | OR crashes → abort | Rust rejects before sim |
| Diameter continuity | Fails at sim start | Explicit check in ast.rs |
| Motor fitment | OR rejects mid-sim | 1mm clearance check in Rust |
| Topology | Fixed 2-stage template | Variable 1–4 stages |
| Wind | OR XML embed | Real CSV interpolated per timestep |
| Atmosphere | Hardcoded OR XML | Struct loaded from mission JSON |
| Scoring | Corrected formula (new) | Corrected formula (to wire) |
| OR role | Primary (broken) | Ground-truth polisher only |

---

## 6. Implementation Order (Execute Today)

### Step 1 — Write mission JSON (5 min)
```bash
# Just create the file at missions/osifog_l3_precision.json
```

### Step 2 — Audit current Rust scorer (10 min)
```bash
grep -n "score\|apogee\|touchdown\|penalty" l2_engine/src/ast.rs
```

### Step 3 — Add SimResult fields (30 min)
- Add `apogee_east_m`, `apogee_north_m` to SimResult
- Add `StageLanding` struct with `vz_ms`, `vxy_ms`
- Track position + velocity at touchdown in sim_core integration loop

### Step 4 — Add wind CSV reader to sim_core (45 min)
- `sim_core/wind.rs` — CSV loader + linear interpolator
- Wire into integration step force calculation

### Step 5 — Add OSIFOG scorer (DONE, superseded)
- Generic `ScoringTable`/`ScoringTerm` evaluator in ast.rs, driven entirely by
  the mission JSON's `scoring.terms` — no formula-name gate, no per-competition
  Rust function. See `docs/plans/2026-07-19-rust-scoring-and-ga-design.md`.

### Step 6 — Test quick run (10 min)
```bash
python organic_loop.py --evaluator rust \
  --mission missions/osifog_l3_precision.json \
  --population 20 --generations 5
```

### Step 7 — Full sweep overnight
```bash
python organic_loop.py --evaluator rust \
  --mission missions/osifog_l3_precision.json \
  --population 400 --generations 60 \
  --validate-openrocket 6 --polish \
  --out designs/osifog_level3
```

---

## 7. OR Abort Post-Mortem (Why We Pivot)

The OR abort at 38–57m altitude is caused by `BasicEventSimulationEngine` detecting 
geometry inconsistencies that cause immediate simulation termination:

- **Root cause observed**: "Descontinuidade no diâmetro do corpo" — body diameter 
  discontinuity between sustainer and booster, even after matching radii to 44mm.
- **Likely deeper cause**: The `_motor_mount_xml()` function in `generate_ork()` 
  may be generating motor mounts with incorrect XML schema for OR 24.12 (motor mount 
  radius/length constraints differ from OR 15.03 where the template was derived from).
- **Why Rust doesn't have this**: `ast.rs::build_mission` checks motor OD < airframe ID 
  with explicit 1mm clearance BEFORE building the geometry, never reaching the simulator 
  with an invalid configuration.

The correct fix is NOT to patch `generate_ork()` again — it is to route optimization 
through the Rust engine and only pass OR-clean, Rust-validated elite configurations.

---

*Spec: 2026-07-19 | L2 Systems AI*
