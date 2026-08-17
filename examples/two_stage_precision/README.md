# Two-Stage Precision Rocket Example

This example demonstrates how to use the L2 Rocket Platform to programmatically
construct a multi-stage rocket Abstract Syntax Tree (AST), compile it to
OpenRocket `.ork` format, and evaluate its flight dynamics using the high-throughput
native Rust proxy engine.

## Overview

The workflow follows three steps:

1. **AST Definition**: Programmatically declare rocket stages, aerodynamic surfaces
   (nose cones, body tubes, fin sets), recovery systems (parachutes), and propulsion
   (motors from the catalog).
2. **Compilation**: `ASTCompiler` serializes the tree into a standard `.ork` archive
   compatible with OpenRocket.
3. **High-Throughput Simulation**: `run_rust_evaluator` feeds the design into the
   native 6-DOF simulation proxy (`l2_engine`) for instantaneous apogee, Mach, and
   static margin telemetry.

## Running the Example

```powershell
python examples/two_stage_precision/run_mission.py
```

Expected Output:

```text
[*] Generating 2-stage rocket AST...
    AST nodes: 12
[OK] Compiled .ork rocket design: runs/example_two_stage.ork
[*] Running high-throughput Rust proxy simulation...
[OK] Proxy Simulation Results:
     Status:          success
     Apogee:          1494.37 m
     Max Mach:        1.22
     Static Margin:   -0.41 cal
     Details:         ok
```
