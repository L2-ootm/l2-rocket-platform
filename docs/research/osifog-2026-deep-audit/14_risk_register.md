# Risk Register

| ID | Risk | Severity | Likelihood | Impact | Mitigation | Status |
|----|------|----------|------------|--------|------------|--------|
| RISK-001 | No stable exposed-sustainer configuration exists | BLOCKER | MEDIUM | Complete failure | Expand search space; try aft-only fins; add more ballast | OPEN |
| RISK-002 | Rust/OpenRocket parity gap invalidates proxy screening | BLOCKER | LOW | All proxy results unreliable | Create parity fixture; calibrate correction factors | OPEN |
| RISK-003 | Anti-tumble script has undocumented side effects | HIGH | LOW | Simulation results invalid | Run pre-event invariance experiment | OPEN |
| RISK-004 | Landing delays cannot achieve <5 m/s for both stages | HIGH | MEDIUM | No legal vehicle | Try different retro motors; expand delay search | OPEN |
| RISK-005 | 0.4 kg dry_mass offset causes systematic error | HIGH | CONFIRMED | All organic-evolution candidates biased | Remove offset, rebuild, retest | OPEN |
| RISK-006 | Stale burn-time approximations cause wrong delays | HIGH | CONFIRMED | Landing calibration fails | Replace with .eng curve integration | OPEN |
| RISK-007 | Scoring formula divergence between Python and Rust | MEDIUM | LOW | Score mismatch | Unify formula source (mission JSON) | OPEN |
| RISK-008 | OpenRocket JVM stability during long runs | MEDIUM | MEDIUM | Crash mid-optimization | Periodic JVM restart; memory monitoring | OPEN |
| RISK-009 | CKG stale data causes negative transfer | MEDIUM | MEDIUM | Valid topologies penalized | Add temporal decay; context-specific keys | OPEN |
| RISK-010 | MAP-Elites archive too sparse to be useful | LOW | MEDIUM | No diversity improvement | Tune descriptor bins; start with small archive | OPEN |
| RISK-011 | Saved/reopened artifact fails verification | HIGH | LOW | No submission | Debug serialization; add more save/reopen tests | OPEN |
| RISK-012 | Touchdown displacement too large for 850k target | MEDIUM | MEDIUM | Score ceiling ~878k | Optimize launch angle; consider motor timing bias | OPEN |
