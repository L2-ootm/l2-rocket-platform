# Engine testing and troubleshooting

## Verification

```powershell
Set-Location l2_engine
cargo test --test ast_bridge
cargo test scoring_table
cargo test motor_adequacy
cargo test
Set-Location ..

python -m pytest tests/test_physical_geometry.py `
  tests/test_osifog_falcon_contract.py `
  tests/test_osifog_precision.py -q
```

## Common failures

- `missing_motor_curve`: designation does not match any `.eng` header.
- motor fit failure: motor diameter plus clearance exceeds the airframe bore.
- hard constraint failure: Mach, stability, landing or mission gate rejected.
- incomplete landings: score fails closed rather than averaging missing stages.
- Rust/OpenRocket disagreement: compare identical motor roles, phase Machs,
  environment, seed and component identities before tuning calibration.

Never parallelize multiple OpenRocket JPype instances. Use Rust for parallel
batch work and one reused JVM for sequential authority validation.
# Evaluator binary freshness

`organic_loop.run_rust_evaluator` compares the release `ast_eval` timestamp
against `Cargo.toml`, `Cargo.lock`, and every Rust source file. It rebuilds the
release binary and closes any cached JSONL process when an input is newer.
Do not invoke an old `target/release/ast_eval` directly after changing engine
physics; use the organic evaluator or rebuild it explicitly.

