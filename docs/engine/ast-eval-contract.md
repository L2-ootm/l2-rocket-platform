# AST evaluator contract

## Input

An evaluation batch contains:

- `target_apogee_m`;
- optional `physics_mode`, `execution_profile`, `objectives`, `constraints`,
  `phase_machs`, calibrations and divergence model;
- `candidates`, each containing `id`, `ast`, and optional signature/environment.

The optional candidate `environment` carries launch rod length, launch angle,
launch azimuth, surface wind direction/speed and relative humidity.
`evaluate_rust_population` preserves one environment per AST candidate so
trajectory variables remain heritable instead of being replaced by one
batch-wide random value. For OSIFOG, `constraints.wind_csv_path` additionally
points Rust at the same multilevel launch-site profile used by OpenRocket.

Each AST node is `{ "type": "...", "params": { ... } }`. Supported physical
nodes include stages, nose cones, body tubes, motor mounts, fins, parachutes,
payload and ballast.

Motor designations must match the header designation of a local `.eng` curve.
No filename alias or motor-index fallback is accepted.

Multiple `MOTOR_MOUNT` nodes may appear in one stage. Optional parameters:

- `role`: `main` by default; `central`, `retro`, or `core` marks the central
  cluster member for packing;
- `multiplicity`: positive integer, default 1;
- `ignition`: `automatic`, `launch`, `stage_activation`, `burnout`, or
  `primary_burnout`;
- `ignition_delay`: seconds relative to the selected ignition event.

`constraints.simulation_phase = "ascent"` activates the population-screen
projection for rockets that carry dedicated descent motors. Mounts whose role
is `retro`, `landing`, or `descent` are removed from powered ascent, but each
installed motor remains as wet point mass at its real axial center and its
mount tube remains as structural mass at the tube center. Primary cluster
burnout can therefore trigger genuine staging without consuming a delayed
landing motor. The 6DOF run stops at the first apogee and does not propagate
dropped branches to touchdown. This mode is a shortlist proxy only; full
multi-branch ignition, descent and score remain OpenRocket authority.

## Output

The top-level object contains `results`. Each result reports status, score,
apogee and horizontal position, Mach, static margins, total propellant and a
`stage_landings` array. Each landing includes stage index, touchdown time,
East/North position, vertical/horizontal velocity and total speed.

Malformed batches produce protocol errors. Candidate physics or constraint
failures remain normal result records with `status: "failed"` and a reason.

## Protocols

- One-shot: `ast_eval --input batch.json`, or JSON on stdin.
- Persistent JSONL: `ast_eval --serve`.
- Discovery: `ast_eval --capabilities`.
# Mission environment

Each candidate may include an `environment` object with:

- `launch_rod_length_m`, `launch_rod_angle_rad`,
  `launch_rod_direction_rad`;
- `base_temperature_k`, `base_pressure_pa`, `launch_altitude_m`,
  `relative_humidity`;
- `wind_speed_mps`, `wind_direction_rad` as the constant-wind fallback;
- `wind_levels`, an array of `{altitude_m, speed_ms, direction_deg,
  std_dev_ms}` objects.

When `wind_levels` is non-empty, Rust interpolates that profile by AGL
altitude. Launch-site atmosphere values anchor the extended ISA proxy. These
inputs are candidate/mission data and do not require recompiling the engine.
