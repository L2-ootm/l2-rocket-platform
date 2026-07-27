# Contributing

## Principles

- Keep the engine independent from OpenRocket, KSP, and UI runtimes.
- Add mission-specific behavior through serializable mission data.
- Fix demonstrated physics or topology limitations before hand-tuning outputs.
- Do not commit generated campaigns, CKG memory, builds, downloaded simulators,
  game files, saves, credentials, or mod caches.
- Prefer small contracts and measured dependencies over framework scaffolding.

## Before a change

1. Identify whether the change belongs to the engine, orchestration, an adapter,
   or documentation.
2. Add or update a regression test.
3. Record any new dependency and why local code is insufficient.
4. Preserve JSON-in/JSON-out compatibility or version the contract.

## Verification

```powershell
python -m pytest tests/test_organic_evolution.py tests/test_or_mode_ast_sweep.py tests/test_or_mode_calibrate.py -q
cargo test --manifest-path l2_engine/Cargo.toml
```

OpenRocket-affecting changes also require a saved/reopened `.ork` validation
against the supported authority version.

## Pull requests

Keep pull requests focused. Include:

- the user-visible or physics problem;
- evidence for the change;
- tests and authority checks performed;
- performance impact where relevant;
- generated files intentionally excluded.
