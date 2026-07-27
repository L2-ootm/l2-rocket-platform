# Phase-Margin Parity: Gate 4 Result

## Finding

**Parity NOT ESTABLISHED.** The Rust and OpenRocket compute fundamentally different margin representations:

### Rust (builder.rs)
- `static_margins_with_mode_at_machs()` returns **one margin per stage**
- For single-stage: minimum across 5 phases via `exposed_stage_phase_margins()`
- For multi-stage: single wet-CG snapshot at a specified Mach
- Result for 2-stage fixture: 2 margins (one per stage)

### OpenRocket
- Reports **stability at each simulation sample** (hundreds of time points)
- Stability is extracted at 5 representative time points
- Result for same fixture: 5 stability values at different times

### Root Cause
The Rust "phase-aware margin" is a **prefilter metric** (minimum across phases), not a **per-phase diagnostic**. The OpenRocket stability is a **continuous time series**. These are different quantities:
- Rust: `min(phase_margins)` → single number per stage
- OpenRocket: `stability(t)` → time series

### Implications
1. Direct numerical comparison is not meaningful without mapping Rust phases to OpenRocket time points
2. The Rust prefilter is more conservative (takes minimum)
3. The OpenRocket time series includes phases the Rust model does not sample
4. Sign agreement may differ because Rust samples at different motor mass states

### Test Fixture
- Body: 74mm radius, 650mm length
- Motor: J510W (3+1 cluster, single stage)
- Fins: 4 fiberglass, root=120mm, height=100mm
- Result: Rust min=-0.181 cal, OpenRocket min=0.044 cal, delta=0.225 cal

### Recommendation
For parity, create a **Rust per-phase diagnostic mode** that returns margin at each of the 5 phases (not just the minimum), then compare with OpenRocket at matching time points. This requires a Rust code change, not just a test.

### Status
**PARITY NOT ESTABLISHED — architectural mismatch documented.**
