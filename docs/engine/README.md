# L2 Rust engine instructions

The Rust engine is the fast local physics and scoring proxy. OpenRocket 24.12
is the final authority for OSIFOG legality, flight telemetry and score.

## Safe commands

```powershell
$env:RAYON_NUM_THREADS = "8"
Set-Location l2_engine
cargo test
cargo run --release --bin ast_eval -- --capabilities
cargo run --release --bin ast_eval -- --input batch.json
Set-Location ..
```

`ast_eval --serve` accepts one JSON batch per line and returns one JSON result
per line. `organic_loop.py` uses this persistent protocol when available.

## Documents

- [AST evaluator contract](ast-eval-contract.md)
- [OSIFOG Level 3 integration](osifog-level3.md)
- [Motor data](motor-data.md)
- [Testing and troubleshooting](testing-and-troubleshooting.md)

The unattended OSIFOG entry point is `osifog_engine_search.py`. It projects
3+1 Falcon candidates into the organic AST contract, screens the population
through Rust's genuine-staging ascent-only phase (retro motor/mount retained
as inert installed mass), derives central-motor ignition candidates from
OpenRocket tail-first windows, and persists only saved/reopened legal
authority results.

Keep Rust CPU use below roughly 70% of logical cores. The binary enforces a
default cap, but an explicit `RAYON_NUM_THREADS` value is easier to audit.
