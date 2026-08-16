# L2 Rocket Platform

L2 Rocket Platform is a local-first, experimental toolkit for generating,
evolving, simulating, and validating rocket designs.

The project began as the engine behind an OSIFOG 2026 competition entry. That
entry is finished; the reusable product is the engine and its organic
topology-evolution workflow.

## Status

| Capability | Status |
|---|---|
| Rust batch physics proxy | Working |
| AST-based topology generation and evolution | Working |
| Headless OpenRocket authority validation | Working |
| Mission-specific scoring and constraints | Working |
| General-purpose CLI and stable public schema | In progress |
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
     rocket_ast.py
  generate / mutate / repair
           |
           v
 l2_engine ast_eval (Rust)
 high-throughput proxy simulation
           |
           v
     organic_loop.py
 select / crossover / evolve
           |
           v
 headless OpenRocket validation
 external authority model
```

The engine owns topology, proxy physics, constraints, scoring, and evolution.
OpenRocket is an external validation adapter. Future KSP support will use the
same boundary rather than embedding game-specific behavior into the engine.

## Repository map

| Path | Purpose |
|---|---|
| `l2_engine/` | Rust physics engine, AST evaluator, binaries, and authority fixtures |
| `rocket_ast.py` | Rocket AST generation, mutation, repair, and ORK compilation |
| `organic_loop.py` | Organic genetic evolution loop |
| `organic_campaign.py` | Resumable long-running campaign runner |
| `campaign_infra.py` | Campaign leases, locking, and recovery |
| `ckg_memory.py` | Optional local continuous knowledge graph |
| `rocket_forge.py`, `motor_data.py`, `physical_geometry.py` | Component, motor, and geometry data used by the generators |
| `osifog_*.py` | The OSIFOG 2026 competition layer built on the engine — retained as worked examples, not part of the reusable platform |
| `integrations/openrocket/` | OpenRocket adapter contract and dependency instructions |
| `missions/` | Serializable mission definitions and objectives |
| `designs/` | Curated design references plus ignored local run output |
| `tools/` | Standalone developer scripts: `debug/`, `reports/`, `checks/`. Nothing here is imported by the engine |
| `tests/` | Python integration and regression tests |
| `docs/architecture/` | Current platform boundaries and contracts |
| `docs/roadmap/` | Ordered future work, including KSP and WebUI |
| `docs/history/` | Dated session logs and superseded analyses. Kept for the reasoning they record — **not current** |

The top-level Python modules form one flat import namespace and several resolve
paths relative to their own location, so they are expected to sit at the
repository root. `tools/` scripts add the root to `sys.path` themselves and run
from anywhere.

Generated builds, downloaded simulators, optimizer memory, game installations,
and campaign populations are local state and are excluded from version control.

## Requirements

- Python 3.11+
- Rust toolchain with Cargo
- A JDK 17 or newer for OpenRocket authority validation. JPype resolves the JVM
  through `JAVA_HOME` first, so point `JAVA_HOME` at that JDK — an older `java`
  earlier on your `PATH` will not be used, but an older `JAVA_HOME` will be, and
  OpenRocket 24.12 will not start on it.
- OpenRocket 24.12 JAR at `lib/OpenRocket-24.12.jar`. It is not distributed with
  this repository; download it from the OpenRocket project.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
cargo build --manifest-path l2_engine/Cargo.toml --release --bin ast_eval
```

## Run an organic campaign

```powershell
python organic_campaign.py `
  --mission missions/osifog_l3_precision.json `
  --out runs/osifog-example `
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
