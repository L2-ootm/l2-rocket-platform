# Active source map

This file describes the post-competition platform boundary. Historical OSIFOG
investigation notes remain useful evidence, but they do not define the active
architecture.

## Runtime path

1. `rocket_ast.py` creates, mutates, repairs, and compiles rocket ASTs.
2. `organic_loop.py` manages population evolution and calls the Rust evaluator.
3. `organic_campaign.py` adds resumability and campaign lifecycle management.
4. `campaign_infra.py` provides process leases and recovery.
5. `l2_engine/src/bin/ast_eval.rs` accepts JSON and returns proxy simulation
   results.
6. `organic_loop.py` optionally validates elites through headless OpenRocket.

## Active supporting source

- `ckg_memory.py` — optional generated learning memory.
- `rocket_forge.py` — motor and material data still used by the active path.
- `physical_geometry.py` — shared physical-geometry rules.
- `motor_data.py` and `l2_engine/motors/` — motor catalogs and thrust curves.
- `missions/` — mission-specific constraints, objectives, and output settings.
- `scripts/` — validation, extraction, benchmarking, and maintenance tools.
- `tests/` and `l2_engine/tests/` — regression and authority fixtures.

## External authorities

- OpenRocket is not part of the core engine. The integration uses a locally
  downloaded JAR documented in `integrations/openrocket/`.
- Future KSP support will be implemented behind a separate adapter contract.

## Generated local state

The following are never source:

- `target/`, virtual environments, caches, and `node_modules/`
- `runs/` and campaign populations
- campaign or global CKG JSON
- downloaded OpenRocket binaries or upstream source checkouts
- KSP installations, saves, craft caches, CKAN caches, and credentials

## Historical code

`legacy/`, `l2_hyper/`, `l2_engine_base/`, and OSIFOG-specific root scripts are
reference material until their remaining imports are eliminated. New features
must use the organic AST → Rust proxy → external authority path.
