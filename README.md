# L2 Rocket Platform

L2 Rocket Platform is a local-first, modular framework for generating,
evolving, simulating, and validating high-power rocket designs across arbitrary
topologies, motor combinations, and flight objectives.

The platform provides an organic topology AST generator, high-throughput Rust
proxy physics evaluator, and ground-truth simulation adapters (OpenRocket and
extensible external runtimes).

## Status

| Capability | Status |
|---|---|
| Rust batch physics proxy | Working |
| AST-based topology generation and evolution | Working |
| Headless OpenRocket authority validation | Working |
| Arbitrary mission objectives and scoring constraints | Working |
| Clean modular Python package layout (`src/`) | Complete |
| Automated CI test suite (Windows & Linux) | Active |
| Local API and browser UI | Planned |
| Kerbal Space Program and CKAN integration | Research/planning |

The software is experimental. Simulation output is not a substitute for
professional engineering review, certified flight software, range-safety
procedures, or applicable law.

## How it works

```text
mission + component catalogs
           |
           v
    src/rocket_ast.py
  generate / mutate / repair
           |
           v
 l2_engine ast_eval (Rust)
 high-throughput proxy simulation
           |
           v
   src/organic_loop.py
 select / crossover / evolve
           |
           v
 headless OpenRocket validation
 (external ground-truth authority)
```

The engine owns topology, proxy physics, constraints, scoring, and evolution.
OpenRocket is an external validation adapter. Future KSP support will use the
same boundary rather than embedding game-specific behavior into the engine.

## Repository map

| Path | Purpose |
|---|---|
| `src/` | Core platform modules: AST compiler, genetic evolution, motor models, geometry solvers |
| `src/rocket_ast.py` | Rocket AST generation, dynamic topology mutation, repair, and ORK compilation |
| `src/organic_loop.py` | Organic genetic evolution loop and population evaluator |
| `src/organic_campaign.py` | Resumable long-running multi-cycle campaign runner |
| `src/campaign_infra.py` | Campaign process leases, locking, and recovery |
| `src/ckg_memory.py` | Local continuous knowledge graph memory |
| `src/rocket_forge.py`, `motor_data.py`, `physical_geometry.py` | Component catalog, motor database, and physical geometry solvers |
| `l2_engine/` | Self-contained Rust physics core (`sim_core`), 6-DOF runner, Barrowman aerodynamics, `ast_eval` binary |
| `missions/` | Declarative mission definitions, flight objectives, and environment constraints |
| `integrations/openrocket/` | OpenRocket adapter contract and JVM bridge |
| `designs/` | Curated reference designs and validation evidence |
| `tools/` | Standalone developer tools and diagnostic checks (`tools/debug/`, `tools/reports/`, `tools/checks/`) |
| `tests/` | Complete Python integration and regression test suite |
| `docs/architecture/` | Platform architecture and adapter contracts |
| `docs/roadmap/` | Ordered future work, including KSP integration and WebUI |
| `docs/history/` | Historical logs and background references |

Generated builds, downloaded simulators, optimizer memory, game installations,
and campaign populations are local state and are excluded from version control.

## Requirements

- Python 3.11+
- Rust toolchain with Cargo
- A JDK 17 or newer for OpenRocket authority validation. JPype resolves the JVM
  through `JAVA_HOME` first, so point `JAVA_HOME` at that JDK.
- OpenRocket 24.12 JAR at `lib/OpenRocket-24.12.jar` (optional, for full JVM authority validation).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
cargo build --manifest-path l2_engine/Cargo.toml --release --bin ast_eval
```

## Run an organic campaign

```powershell
python -m organic_campaign `
  --mission missions/osifog_l3_precision.json `
  --out runs/campaign-example `
  --population 96 `
  --elite-count 12 `
  --generations-per-cycle 5 `
  --validate-openrocket 4
```

`runs/` contains disposable local campaign state. Keep selected `.ork` files and
reports by copying them into a named reference directory under `designs/`.

## Tests

```powershell
python -m pytest tests/test_organic_evolution.py tests/test_or_mode_ast_sweep.py tests/test_or_mode_calibrate.py -q
cargo test --manifest-path l2_engine/Cargo.toml
```

See [the platform architecture](docs/architecture/platform.md), the
[roadmap](docs/roadmap/platform.md), and [contribution guide](CONTRIBUTING.md)
before changing engine or adapter contracts.

## Repository hygiene

Audit generated state without deleting anything:

```powershell
python scripts/repo_hygiene.py audit --profile safe
```

Cleanup is manifest-driven, refuses tracked/protected paths, and uses a short
interactive confirmation (or `--yes` for trusted automation). See
[repository hygiene](docs/maintenance/repository-hygiene.md).
