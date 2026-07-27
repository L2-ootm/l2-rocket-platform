# Platform roadmap

## Phase 0 — Open-source baseline

- Remove generated and machine-local state.
- Establish licensing and third-party provenance.
- Ensure every active pipeline file is tracked.
- Provide a reproducible OpenRocket dependency setup.
- Make the CLI workflow pass from a clean clone.

## Phase 1 — Stable engine contract

- Version the rocket AST and evaluation result schemas.
- Separate mission objectives from OSIFOG scoring.
- Add generic example missions and small deterministic fixtures.
- Add performance and cross-authority regression baselines.

## Phase 2 — Local service and WebUI foundation

- Wrap stable engine operations in a small local API.
- Stream campaign progress and evaluation telemetry.
- Render the rocket AST independently of OpenRocket.
- Keep simulation and mutation logic outside the browser.

## Phase 3 — OpenRocket WebUI

- Browse and edit supported OpenRocket components.
- Render stages, motors, fins, recovery, materials, and appearance.
- Run headless authority simulations and inspect plots/events.
- Export and reopen `.ork` files with round-trip verification.

## Phase 4 — KSP technical spikes

- Target KSP 1.12.5 stock first.
- Prove craft-file parsing and generation for KSP1.
- Prove stock-part catalog ingestion.
- Prove generated-craft launch and telemetry through kRPC.
- Define save/craft safety and version compatibility gates.

## Phase 5 — KSP and CKAN adapter

- Treat CKAN as package/provenance metadata, not as authoritative part physics.
- Ingest installed CKAN metadata and a resolved in-game modded-part catalog.
- Map rocket AST components to stock or modded KSP parts.
- Load generated craft through the verified game-side mechanism.
- Stream real flight data back into an authority result.

## Phase 6 — Integrated KSP experience

- Web-based craft rendering and supported appearance controls.
- Compatibility diagnostics for missing or incompatible mods.
- Closed-loop generation, in-game evaluation, and evolution.
- Explicit opt-in boundaries for save changes and live game control.

The detailed KSP plan is maintained as an UltraPlan artifact under
[`ksp-integration.md`](ksp-integration.md). KSP observations remain
separate from OpenRocket authority results, and active-flight craft replacement
is explicitly outside the supported product path.
