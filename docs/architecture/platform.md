# Platform architecture

## Decision

The platform uses a core-and-adapters architecture:

```text
                     local API / CLI
                           |
                           v
mission + catalogs -> evolution engine -> rocket AST
                           |
              +------------+------------+
              |                         |
              v                         v
       Rust proxy physics       authority adapters
                                 |             |
                                 v             v
                           OpenRocket      future KSP
```

## Core responsibilities

The core owns:

- serializable rocket topology and component parameters;
- mutation, crossover, repair, and selection;
- mission constraints and objective scoring;
- fast deterministic proxy evaluation;
- authority-neutral evaluation requests and results.

The core does not own:

- OpenRocket process lifecycle or file installation;
- KSP installation, saves, mods, CKAN, or game telemetry;
- browser rendering or user-interface state;
- downloaded catalogs, credentials, or generated campaign memory.

## Current contracts

- Python creates AST payloads in `rocket_ast.py`.
- Rust accepts batch JSON through `l2_engine/src/bin/ast_eval.rs`.
- The evaluator contract is documented in `docs/engine/ast-eval-contract.md`.
- `organic_loop.py` coordinates proxy evaluation and optional authority checks.
- OpenRocket remains the real-world authority for supported rocket models.

The first platform refactor should stabilize these existing payloads before
introducing a generic adapter trait or network protocol.

## Local state boundary

All mutable or machine-specific state belongs under ignored paths such as
`runs/` and `.local/`. A clean clone should contain source, small deterministic
fixtures, schemas, selected examples, and documentation—not build products,
simulator installations, optimizer populations, or learned CKG state.

## Performance budgets

Initial budgets:

- engine CLI startup: under 100 ms after compilation;
- idle local service: under 80 MiB;
- cached proxy request overhead: under 10 ms excluding simulation;
- browser interaction feedback: under 100 ms for non-simulation actions;
- source checkout: under 100 MiB excluding Git history and optional fixtures;
- no new runtime framework without a measured need and removal plan.
