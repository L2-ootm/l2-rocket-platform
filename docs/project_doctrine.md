# L2-OSIFOG Project Doctrine

## Authority Ladder

L2-OSIFOG has four evidence levels. Never collapse them into one claim.

1. **Rust proxy**: `organic_loop.py --evaluator rust` or `ast_eval` scores many ASTs quickly. These numbers are exploration signals.
2. **Calibrated Rust proxy**: `--calibrate-every N` records OpenRocket/Rust deltas in the CKG and improves ranking pressure. These numbers are still proxy results.
3. **OpenRocket validation**: `--validate-openrocket N` or `run_polisher.py --skip-polish` loads ranked elites in OpenRocket, runs deterministic simulations, and computes Barrowman margins by phase.
4. **OpenRocket polished authority**: `--polish` or `tools/run_polisher.py` validates ranked elites, preserves the accepted topology, adds forward payload ballast, and writes `precision_polished_elite.ork`.

Only levels 3 and 4 are authority results. Reports must label proxy-only missions as proxy-only.

## Standard Mission Loop

Use this for normal organic exploration:

```powershell
$env:RAYON_NUM_THREADS = "8"
python organic_loop.py `
  --evaluator rust `
  --physics openrocket `
  --mission missions/precision_16k_m3_organic.json `
  --population 300 `
  --generations 40 `
  --elite-count 8 `
  --validate-openrocket 8 `
  --calibrate-every 5 `
  --polish `
  --out designs/organic_16k_m3
```

Use this for authority testing of an already saved elite file:

```powershell
python tools/run_polisher.py `
  --elite designs/organic_16k_m3_longburn/organic_elite.json `
  --mission missions/precision_16k_m3_organic.json `
  --out designs/organic_16k_m3_longburn
```

For high-altitude stress missions, start by validating and reporting existing elites before claiming the structure works in OpenRocket:

```powershell
python tools/run_polisher.py `
  --elite designs/anomaly_200km/organic_elite.json `
  --mission missions/anomaly_200km.json `
  --out designs/anomaly_200km_or_polish
```

## Reporting Rules

- Report Rust apogee/Mach as `rust_apogee_m` and `rust_mach`.
- Report OpenRocket apogee/Mach as `or_metrics.apogee_m` and `or_metrics.mach`.
- Report `critical_warning_count` separately from normal and informational warnings.
- Non-critical OpenRocket warnings are acceptable only when explained in the mission report.
- Static margin authority comes from OpenRocket Barrowman phase margins, not Rust proxy margins.
- If `or_metrics` is missing, the mission has not been authority-validated.
- Rust OR-mode static-margin gates must consume `stability.phase_machs` from the mission. A low-Mach-only margin can falsely accept high-speed staged vehicles that OpenRocket rejects later.

## Calibration Rules

Calibration is guidance, not permission.

- `--calibrate-every N` stores topology-specific apogee and Mach deltas in `.planning/organic_ckg.json`.
- OpenRocket authority feedback is stored as contextual stage and stage-pair memory, not generic `STAGE`/`CLOSE_BODY` penalties. A failure must teach "this stage context failed", not "all stages are cursed".
- Calibration may influence the Rust proxy score.
- Calibration never relaxes `constraints.max_mach`, `constraints.min_static_margin`, motor fitment, or OpenRocket authority gates.
- After changing motor data, `rocket_ast.py`, `l2_engine/src/ast.rs`, `l2_engine/src/sim_core/`, or mission constraints, run a fresh validation/polish pass.

## Polishing Rules

The polisher is an authority refinement step, not a topology designer.

- It checks ranked elites in score order.
- It rejects elites that fail OpenRocket load/sim, critical warnings, target bracket, Mach constraints, or Barrowman margin constraints.
- It preserves topology and changes only forward payload ballast.
- It writes `authority_polish_report.json` for validation attempts and `precision_polished_elite.ork` only when polishing succeeds.

## Current Stress-Test Finding

The July 2026 `anomaly_200km` and `push_limits` saved elites were rechecked through OpenRocket authority using both `tools/run_polisher.py` and short seeded `organic_loop.py --calibrate-every 1 --validate-openrocket 6 --polish` runs.

- `anomaly_200km`: Rust proxy elites near `201-205 km` validated in OpenRocket around `6.7-8.1 km`, with Mach around `1.2-1.3` and negative OpenRocket Barrowman margins.
- `push_limits`: Rust proxy elites near `438-522 km` validated in OpenRocket around `6.8-12.8 km`, with Mach around `1.1-4.1` and negative OpenRocket Barrowman margins.
- Both campaigns loaded and simulated in OpenRocket with zero critical warnings, but no ranked elite satisfied the authority gates for polishing.

Fresh from-zero reruns using clean contextual authority memory (`.planning/or_authority_zero_context_ckg.json`) confirmed the same structural issue without relying on polluted generic CKG memory:

- `anomaly_200km_zero_context_or`: top Rust elites scored `41.8-101.2 km`, but OpenRocket measured only `71-3458 m`; every top-8 authority candidate had negative phase margin (`-2.15` to `-6.24` calibers), and no candidate was polishable.
- `push_limits_zero_context_or_r4`: top Rust elites scored `31.4-42.7 km`, but OpenRocket measured only `626-4550 m`; every top-8 authority candidate had negative phase margin (`-0.76` to `-4.07` calibers), and no candidate was polishable.
- Authority reports live at `designs/anomaly_200km_zero_context_polish_report/authority_polish_report.json` and `designs/push_limits_zero_context_polish_report/authority_polish_report.json`.

Do not report these missions as authority successes yet. Treat them as proxy/authority mismatch evidence and use them to improve extreme multi-stage proxy fidelity, phase-margin pressure, and OR-gated selection.

### Root Cause Found For The July 2026 OR Drift

The old close-match memory came from the retired `legacy_hyperreal_apogee_stays_within_calibrated_error_bound` sentinel, which checks a fixed-template `PhysicsMode::HyperReal` reference vehicle. It did not prove that organic AST `PhysicsMode::OpenRocketLegacy` was enforcing high-Mach phase stability.

The actual drift was in the Rust AST scorer: it computed every static margin at a hardcoded low-speed reference Mach `0.3`, while OpenRocket authority validated later phases at mission values such as Mach `2`, `5`, and `10`. This let Rust accept candidates with positive low-speed margins that OpenRocket correctly rejected at supersonic/hypersonic phase conditions. The Rust batch contract now carries `phase_machs`, and the Rust margin gate evaluates each phase at the corresponding mission Mach.

## Safety And Scope

L2-OSIFOG output is simulation evidence, not launch authorization. Any real hardware path still requires structural, thermal, recovery, range-safety, regulatory, and motor-certification review.
