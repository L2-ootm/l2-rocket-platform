# Self-Contained Physics Engine + GPU Port — Design

## Goal
Remove `l2_engine`'s hard dependency on the external `l2_engine_base/rocket-sim`
crate (path dependency, won't ship in the published repo), fold its physics
natively into `l2_engine`, fix three documented bugs, then build the GPU
(WGSL/wgpu) engine on top of the now-native Rust physics as the ground truth
for the shader port.

## Sequencing
1. **Port** — move rocket-sim's source into `l2_engine` as native modules.
2. **Bug fixes** — fix the 3 documented defects on the now-native code.
3. **GPU engine** — implement `docs/l2_gpu_engine.md`'s roadmap using the
   native port as the line-by-line WGSL reference.

## Phase 1 — Native Port

### Scope
Port **all** of `l2_engine_base/rocket-sim/src/*` (full port, not just the
modules `l2_engine` currently calls) into `l2_engine/src/sim_core/`:

- `vehicle/{mission,stage}.rs` → `sim_core/vehicle/`
- `dynamics/{sixdof,state}.rs` → `sim_core/dynamics/`
- `physics/{aerodynamics,atmosphere,gravity}.rs` → `sim_core/physics/`
- `sim/{event,integrator,runner}.rs` → `sim_core/sim/`
- `gnc_mod/{controller,guidance,pid,tvc}.rs` → `sim_core/gnc/`
- `orbital/{elements,maneuvers,propagator}.rs` → `sim_core/orbital/`
- `io/{csv,json}.rs` → `sim_core/io/`
- `bin/viz.rs` → `l2_engine/src/bin/viz.rs`, kept behind an optional `viz`
  Cargo feature gating `eframe`/`egui_plot` (mirrors rocket-sim's own
  optionality) so default builds stay headless/dependency-light, consistent
  with the locked "headless CLI only" decision.

### Mechanics
- Namespace becomes `crate::sim_core::*` instead of `rocket_sim::*`; update
  all call sites in `barrowman.rs`, `ast.rs`, `builder.rs`, `mission_adapter.rs`,
  `bin/optimize.rs`.
- Drop the `rocket-sim = { path = ... }` line from `l2_engine/Cargo.toml`;
  absorb its non-optional deps (`nalgebra`, already shared) directly.
- Keep the MIT `LICENSE`/attribution notice from rocket-sim inside
  `l2_engine/` (e.g. `THIRD_PARTY_NOTICES.md`) since the ported code
  originates from ZenAlexa/rocket-sim (MIT).
- `l2_engine_base/` is deleted from the tree once the port is verified.

### Verification (regression gate before deleting l2_engine_base)
- `cargo build` + `cargo test` green in `l2_engine` with zero `rocket_sim::`
  references remaining.
- Re-run existing missions (`missions/karman_m6.json`, etc.) through
  `l2_engine evolve`/`optimize` pre- and post-port; apogee/Mach/margin outputs
  must match bit-for-bit (same integrator, same inputs) — this is a pure move,
  not a rewrite, so any numeric drift indicates a mistranslation, not an
  intended fix.

## Phase 2 — Documented Bug Fixes
Applied on the native `sim_core` code:

1. **Motor/tube fitment not validated** (`organic_loop_report.md #3`) — AST→
   `StageBuilder` assembly in `mission_adapter.rs`/`builder.rs` must reject
   (or heavily penalize) genomes where motor diameter exceeds parent body
   tube inner diameter, before simulation runs, instead of relying on
   OpenRocket to catch it post-hoc.
2. **JPype JVM-restart crash** (`organic_loop_report.md #5`) — the JVM must
   be initialized once globally in the Python validation loop
   (`organic_loop.py`/`l2_hyper` polisher), not per-elite.
3. **Friction/stability calibration drift** (`STATE.md`) — tune the
   `FrictionModel`/margin proxy in `sim_core` against current OpenRocket
   ground truth so Rust-proxy apogee/margin predictions track OpenRocket's
   within the existing calibration tolerance.

## Phase 3 — GPU Engine
Executes `docs/l2_gpu_engine.md`'s existing roadmap (Steps 1–5), using
`sim_core`'s now-native `physics`/`dynamics`/`vehicle` modules as the
line-by-line source of truth for the WGSL port, since there's no longer an
external crate boundary to diverge from.

## Risks
- Full port (including orbital/gnc extras never called by `l2_engine`) is
  larger than a minimal port — more surface to keep bit-identical, but
  requested explicitly for future-proofing (orbital insertion missions, GUI
  plotting).
- `sim_core` and the future WGSL shader will need to be kept in sync
  manually — no shared source generation; drift risk noted for later
  calibration work.
