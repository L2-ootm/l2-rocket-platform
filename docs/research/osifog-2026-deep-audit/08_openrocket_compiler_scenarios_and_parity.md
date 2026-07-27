# OpenRocket Compiler, Scenarios, and Parity

## OpenRocket Initialization

### JVM Lifecycle
- `init_or()` in osifog_sweep.py starts JVM once per process
- Uses JPype (not orhelper) for OR 24.12 compatibility
- Headless mode (`-Djava.awt.headless=true`)
- Log level set to ERROR (suppresses INFO/DEBUG)
- Motor database loaded before Application initialization
- **Thread safety**: NOT thread-safe — one JVM, one process

### ORK Generation Pipeline
```
parameters dict
  → compile_falcon_physical_geometry() [collision check]
  → _falcon_cluster_geometry() [cage geometry]
  → generate_ork() [XML string]
  → tempfile.mkstemp() [write to disk]
  → _load_ork_doc() [GeneralRocketLoader]
  → simulation options (seed, wind, atmosphere)
  → _seed_multilevel_wind() [deterministic pink noise]
  → _get_anti_tumble_listener() [fresh per simulation]
  → sim.simulate(listener) [execution]
  → extract metrics from FlightData
```

## Scenario Types

### OFFICIAL_FULL_MISSION
- All motors active (main + retro)
- Full mission environment
- Authority scoring eligible
- Used for: final submission, trajectory polish

### EXPOSED_SUSTAINER_ASCENT
- Retro motors disabled (inert wet mass only)
- Measures exposed-sustainer stability
- Used for: stability screening
- **Not yet implemented as runtime fixture**

### STAGE_FREE_DESCENT_DIAGNOSTIC
- Retro motors disabled
- Free-fall trajectory only
- Measures alignment windows
- Used for: landing opportunity screening
- **Runtime fixture complete**

### POWERED_STAGE_LANDING_VALIDATION
- Retro motors active with specific delays
- Full powered descent simulation
- Used for: landing validation
- **Not yet implemented as runtime fixture**

### DELAY_ROBUSTNESS
- Multiple delay perturbations around a candidate
- Tests sensitivity to timing errors
- Used for: robustness assessment
- **Not yet implemented as runtime fixture**

### DEBUG_ONLY
- Diagnostic configuration
- Cannot be scored as authority
- Used for: development testing

## Scenario Parity Status

| Scenario | Python Runtime | Rust Proxy | Parity |
|----------|---------------|------------|--------|
| OFFICIAL_FULL_MISSION | COMPLETE | N/A | N/A |
| EXPOSED_SUSTAINER_ASCENT | COMPLETE | COMPLETE | NOT COMPARED |
| STAGE_FREE_DESCENT_DIAGNOSTIC | COMPLETE | N/A | N/A |
| POWERED_STAGE_LANDING_VALIDATION | INCOMPLETE | N/A | N/A |
| DELAY_ROBUSTNESS | INCOMPLETE | N/A | N/A |

## Anti-Tumble Extension

### Serialization
- Single canonical JavaScript function
- Normalized whitespace
- SHA-256 digest: `1c4aecd0044eb6f143aba8038ecb197250eee49508375dc56703507f37775e0d`
- Serialized as `<extension extensionid="info.openrocket.core.simulation.extension.impl.ScriptingExtension">`

### Validation
- Exactly one extension per simulation
- Extension ID must end with "ScriptingExtension"
- Script must match normalized canonical form
- Extension must be enabled
- Fail-closed on any violation

### Known Behavior
- Suppresses TUMBLE events (returns false)
- Allows simulation to continue past natural tumble
- Enables reaching ground contact for scoring
- **Pre-event invariance**: NOT empirically proven

## OpenRocket Source Inspection Points

For the next parity audit, inspect:
1. `BarrowmanCalculator.java` — CP/CNa computation
2. `PinkNoiseWindModel.java` — Wind turbulence model
3. `RK4Simulator.java` — Integration method
4. `FlightEvent.java` — Event types and ordering
5. `StageSeparation` — Separation mechanics
6. `ScriptingSimulationListener` — Extension API
