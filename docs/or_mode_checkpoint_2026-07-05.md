# L2-OSIFOG OR-Mode Checkpoint - 2026-07-05

## Current State

We are stopping the OpenRocket-parity push here because the current results are good and the last source-backed experiment did not improve the system-level objective.

The retained state keeps the organic Rust bridge and the OpenRocket validation loop healthy:

- `organic_loop.py` can use Rust AST evaluation as the fast proxy.
- OpenRocket validation remains the authority for official claims.
- Sequential OpenRocket validation through `orhelper` works.
- The old 12-gene `l2_engine evolve` path remains separate.
- The best retained five-seed comparison is `designs/or_mode_sweep_after_launch_guide_wind_5seed/report.json`.

## Retained Improvement

### OpenRocket launch environment parsing for `.ork` simulation

Retained changes parse OpenRocket launch environment data from `.ork` files for `simulate_rocket` in `PhysicsMode::OpenRocketLegacy`:

- launch rod length
- launch rod angle
- launch rod direction
- launch-into-wind behavior
- wind speed and wind direction

The Rust sim-core now has launch-guide support:

- `Mission` carries an optional launch guide.
- 6DOF dynamics projects motion along the launch guide before clearance.
- The runner clamps pre-clear position, velocity, angular velocity, and attitude.
- Direct `build_mission` behavior remains unchanged unless `.ork` environment parsing applies it.

This was a small but real parity gain:

| Report | Mean abs apogee | Max abs apogee | Mean abs Mach | Max abs Mach |
| --- | ---: | ---: | ---: | ---: |
| Restored baseline | 1.8135% | 3.1381% | 0.01518 | 0.03490 |
| After launch guide / wind `.ork` path | 1.7778% | 3.1287% | 0.01528 | 0.03531 |

The gain is modest, but source-backed and green.

## Rejected Experiments

### AST default launch environment

Tried applying OpenRocket AST compiler defaults directly in Rust AST scoring:

- 2.0 m launch rod
- 2.0 m/s wind
- OpenRocket-like launch direction defaults

Result: severe regression.

| Report | Mean abs apogee | Max abs apogee | Mean abs Mach | Max abs Mach |
| --- | ---: | ---: | ---: | ---: |
| After AST launch env experiment | 26.9232% | 72.6979% | 0.79363 | 1.99391 |

Decision: reverted. Do not reapply naive AST wind/launch defaults. If this area is revisited, isolate guide-only, wind-only, and OpenRocket coordinate semantics in controlled tests first.

### OpenRocket linear kinematic viscosity in OR-legacy friction

Source finding:

- OpenRocket `AtmosphericConditions.getKinematicViscosity()` uses a linear dynamic-viscosity approximation divided by density.
- Rust sim-core atmosphere uses Sutherland's law for kinematic viscosity.
- OR-legacy friction recomputes Reynolds-dependent friction dynamically, so this was plausible.

Experiment:

- Added `openrocket_kinematic_viscosity` to `Atmo`.
- Used it only for `FrictionModel::OpenRocketLegacy`.
- Kept HyperReal on Sutherland.

Result: Rust and Python tests passed, but five-seed comparison did not improve apogee.

| Report | Mean abs apogee | Max abs apogee | Mean abs Mach | Max abs Mach |
| --- | ---: | ---: | ---: | ---: |
| After OR viscosity experiment | 1.8140% | 3.1779% | 0.01498 | 0.03449 |

Decision: reverted. It slightly helped Mach but worsened mean and max apogee, so it is not worth retaining.

## Verified Gates

Before stopping, the retained tree had passed:

```powershell
cd l2_engine
cargo test
```

Result:

- 80 lib tests passed
- 11 AST bridge tests passed
- 2 OR-mode tests passed before the viscosity experiment
- 1 OpenRocket validation test passed

Python bridge gates passed:

```powershell
python -m pytest test_organic_evolution.py test_or_mode_ast_sweep.py test_or_mode_calibrate.py -q
python -m py_compile ckg_memory.py organic_loop.py rocket_ast.py test_organic_evolution.py test_or_mode_ast_sweep.py test_or_mode_calibrate.py or_mode_ast_sweep.py or_mode_calibrate.py
```

Result:

- `20 passed`
- Python compile clean

After reverting the viscosity experiment, the gates were rerun and passed:

- `rg -n "openrocket_kinematic_viscosity|hyperreal_mode_ignores_openrocket_viscosity_value" l2_engine/src l2_engine/tests` returned no matches.
- `cd l2_engine && cargo test` passed.
- `python -m pytest test_organic_evolution.py test_or_mode_ast_sweep.py test_or_mode_calibrate.py -q` passed with `20 passed`.
- `python -m py_compile ckg_memory.py organic_loop.py rocket_ast.py test_organic_evolution.py test_or_mode_ast_sweep.py test_or_mode_calibrate.py or_mode_ast_sweep.py or_mode_calibrate.py` passed.

The workspace is still very dirty and includes many untracked generated artifacts, so rerun gates before any future commit.

## Known Dirty Workspace Context

The repository contains many unrelated dirty/generated files, including:

- generated `designs/or_mode_sweep_*` reports
- `.planning/or_mode_sweep_*` artifacts
- imported or deleted `l2_engine_base` content
- temporary OpenRocket `.ork` files
- project docs and engine additions from earlier work

Do not clean these blindly. Treat them as user/prior-session state unless explicitly asked.

Known `git diff --check` issue from before this checkpoint:

- `rocket_forge.py:248` trailing whitespace
- line-ending warnings

## Source Findings Worth Keeping

OpenRocket launch/source behavior observed:

- Liftoff is detected when relative position `z > 0.02`.
- Launch rod clear is detected when liftoff is true and relative position length exceeds raw launch rod length.
- While not launch-rod-cleared, acceleration is projected onto the launch rod direction and angular acceleration is zeroed.
- `launchIntoWind` uses wind direction as the launch rod direction.
- Effective launch rod length exists in OpenRocket, but the simulation loop clearance check uses raw launch rod length.

OpenRocket atmosphere findings:

- `ExtendedISAModel` uses geopotential altitude internally.
- Standard humidity defaults to zero.
- Mach speed uses `165.77 + 0.606 * temperature`.
- Kinematic viscosity uses `(3.7291e-06 + 4.9944e-08 * temperature) / density`.

The viscosity finding is documented but not retained because measured parity got worse.

## Current Best Metrics

Best retained five-seed report:

```text
designs/or_mode_sweep_after_launch_guide_wind_5seed/report.json
```

Summary:

- count: 5
- success_count: 5
- mean_abs_apogee_pct: 1.7778055286102425
- max_abs_apogee_pct: 3.1287043372126186
- mean_abs_mach: 0.015278286053107437
- max_abs_mach: 0.03531102948863252

Organic validation smoke that previously passed:

```powershell
python organic_loop.py --evaluator rust --population 12 --elite-count 4 --generations 2 --seed 20260703 --out designs/organic_launch_guide_smoke --ckg .planning/organic_launch_guide_smoke_ckg.json
python organic_loop.py --evaluator rust --population 12 --elite-count 4 --generations 2 --seed 20260703 --out designs/organic_launch_guide_or_validated --ckg .planning/organic_launch_guide_or_validated_ckg.json --validate-openrocket 1
```

Validated elite result:

- Rust apogee: `6387.611672824161`
- OpenRocket apogee: `6483.03888807816`
- Rust Mach: `1.5796240233259193`
- OpenRocket Mach: `1.5971256601717712`

## Recommended Next Session

Resume from this checkpoint only after rerunning gates.

Next source-backed candidates:

1. Compare OpenRocket `FlightData` curves against Rust trajectory for a worst residual case, especially seed `2026070408`.
2. Investigate geopotential altitude vs geometric altitude in atmosphere only if curve evidence suggests density/pressure drift.
3. Investigate OpenRocket event timing and apogee sampling bias only if curve traces show similar dynamics but different reported maxima.
4. Avoid broad coefficient tuning unless tied to a specific OpenRocket source behavior and measured against the five-seed suite.

The current result is good enough to pause. The BEAST is already alive; the next push should be measured surgery, not brute-force fiddling.
