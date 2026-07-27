# OSIFOG Level 3 engine integration

The mission contract is `missions/osifog_l3_precision.json`. Its score begins
at 900,000 and subtracts penalties for:

1. squared vertical apogee error from 3,000 m;
2. squared East/North apogee displacement;
3. squared signed-mean touchdown East/North position;
4. squared mean total touchdown speed; and
5. consumed propellant mass.

Hard gates require two landed stages, every touchdown below 5 m/s, Mach below
0.95, at least 1.5 calibers during initial ascent, no passive recovery and
complete finite telemetry.

## Authority boundary

Rust results are search guidance. A score is publishable only after the
generated `.ork` is simulated, saved, reopened and independently scored from
its persisted OpenRocket flight branches.

## Locked optimization workflow

Do not hand-tune Falcon parameters or run ad hoc candidate grids. All new
designs follow:

1. organic AST topology and material generation;
2. broad Rust population evaluation;
3. automated OpenRocket finalist validation and trajectory/ignition polish;
4. save, reopen and independently verify the single authority ORK.

If the engine cannot represent a required motor role, structure, material,
geometry variable or telemetry objective, implement it in the engine, add
tests and update this documentation before resuming optimization. Manual
probes are limited to diagnosing a demonstrably incorrect engine/authority
model.

The AST bridge supports multiple `MOTOR_MOUNT` nodes per stage. Each mount may
declare `role`, `multiplicity`, `ignition`, and `ignition_delay`; older
single-mount ASTs remain valid. Cluster thrust, propellant, dry mass, mount
mass and per-motor mass flow are aggregated in the 6DOF path.

The OSIFOG broad search uses Rust `simulation_phase=ascent`. Its three primary
motors burn normally; the central retro motor and sleeve remain installed as
wet/structural mass at their physical CG locations but do not block primary
burnout or separation. Rust stops at first apogee. This avoids the invalid
model in which unburned retro propellant kept the booster attached and also
avoids spending population budget on descent branches that OpenRocket will
recompute.

`retro` still does not reverse the Rust thrust vector. Powered-descent timing,
tail-first alignment, branch landings and every final score remain
OpenRocket-only.

The physical Python compiler separately requires every motor mount and ballast
solid to have a collision-free tangent-contact path to the airframe.

## Powered-descent evidence

OpenRocket reports the rocket nose/body axis with `theta=+90 deg` nose-up and
`theta=-90 deg` nose-down. The optimizer reconstructs horizontal velocity from
the East/North position history and computes:

`q = -u_body_z dot v_hat`

`q=1` is ideal braking and `q=-1` means the central motor accelerates the
impact. Every authority evaluation records natural tail-first windows, powered
descent direction cosine, vertical thrust power and the fraction of the burn
that opposes velocity. Delay-only tuning is forbidden when no physical
tail-first window exists.

The mission search space includes legal 1 mm fins, selected balsa at the rule
minimum density of 0.17 g/cm3, structural ballast rods, central H/I/J/K motor
choices and the counter-wind launch-angle band that produces repeatable
post-apogee tumble. These are optimizer variables, not hand-selected final
values.

After a physical topology change, run the engine search to regenerate
free-impact timing and calibrate both central motors. A score becomes valid
only after the winning ORK is saved and reopened:

```powershell
python osifog_engine_search.py --rust-budget 5000 --finalists 48 `
  --seed 16000 --output designs/osifog_engine_search
```

The search writes an atomic `checkpoint.json` after Rust screening and after
every OpenRocket finalist. A legal winner is automatically saved as
`best-authority.ork`, reopened, and written to `result.json` with persisted
authority telemetry.
# Autonomous authority loop

`osifog_engine_search.py` now supports closed-loop generations:

```powershell
python osifog_engine_search.py `
  --rust-budget 5000 --finalists 48 --cycles 4 `
  --calibrate-from designs/osifog_engine_search_v3/result.json `
  --target-score 850000 `
  --output designs/osifog_autopilot_v4 --no-resume
```

Rust evaluates an ascent-only projection of the mission scoring table. The
projection keeps apogee altitude, apogee displacement, and propellant terms;
landing terms stay with OpenRocket authority. After each OR batch, paired
Rust/OR telemetry produces robust median apogee and Mach multipliers. The next
cycle applies those multipliers before hard constraints and scoring.

The evaluator environment is mission-driven per candidate: full multilevel
wind, launch guide length/angle/azimuth, launch altitude, base temperature,
base pressure, and relative humidity are serialized through the AST batch
contract. Surface wind is no longer a substitute for the CSV profile.
