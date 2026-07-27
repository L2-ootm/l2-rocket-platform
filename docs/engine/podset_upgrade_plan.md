# Rust Engine Upgrade Plan: 3+1 PodSet Topology

## 1. The Core Problem (Why the upgrade is necessary)
The current OSIFOG L3 mission requires a propulsive landing (no parachutes) with a touchdown speed of `< 5 m/s`. Because solid retro motors have a fixed total impulse and cannot be throttled, the rocket must perform a perfect "hoverslam". The timing window for this ignition is on the order of milliseconds. 
While tweaking the passive aerodynamic drag (e.g., deploying large grid fins) can slow the terminal descent velocity and artificially widen this window to ~0.5 seconds, the sheer size of the coupled search space (Mass + Fin Area + Motor Impulse + Exact Ignition Delay) is too massive to solve at Python/JVM speeds (1 generation per 20 seconds).
We must shift the heavy lifting to the `l2_engine` (Rust) which can evaluate 100+ simulations per second, allowing us to brute-force the ignition delay landscape instantly.

## 2. Current Engine Limitations
The current `l2_engine` and `l2-organic-evolution` framework strictly assume **inline (series) staging**.
Analysis of `l2_engine/src/ast.rs` and `l2_engine/src/sim_core/vehicle/stage.rs` reveals the following structural blockers:

1. **Missing AST Nodes for Parallel Staging:** `rocket_ast.py` and `ast.rs` only support `STAGE`, `BODY_TUBE`, `NOSE_CONE`, `FIN_SET`, etc. There is no `POD` or `STRAP_ON` AST node.
2. **Missing Off-Axis Mass Physics:** The current physics core assumes the Center of Gravity (CG) is perfectly aligned on the centerline (`y = 0`, `z = 0`). It only tracks axial CG (`x`). Parallel pods shift the radial CG and significantly alter the moments of inertia ($I_{xx}$, $I_{yy}$, $I_{zz}$).
3. **Missing Off-Axis Thrust Torque:** `sixdof.rs` currently only calculates torque from TVC angles. It does not support multiple simultaneous thrust vectors offset from the centerline. If three pods fire, they must be mathematically combined; if one burns out early, it induces massive torque.
4. **Aerodynamic Interference:** Barrowman equations in Rust do not natively handle parasitic drag or CP shifts caused by laterally mounted tubes.

## 3. Required Implementation & Testing
To unleash the Rust engine for the PodSet architecture, the following must be built and tested:

### Phase 1: AST & Geometry Extension
* Update `rocket_ast.py` to support `POD` nodes, allowing arrays of tubes to be attached radially at specific angular offsets.
* Update `l2_engine/src/ast.rs` to parse the new `POD` nodes and translate them into a `RocketGeometry` structure.

### Phase 2: Mass & Inertia Core
* Refactor `l2_engine/src/builder.rs` and `mass_calculator.rs` to compute 3D Center of Gravity (`cg_x, cg_y, cg_z`).
* Implement parallel axis theorem to accurately model the expanded moment of inertia.

### Phase 3: 6-DOF Dynamics Upgrades
* Refactor `Stage` in `l2_engine/src/sim_core/vehicle/stage.rs` to hold a `Vec<MotorMount>` rather than a single axial motor offset.
* Update `sixdof.rs` thrust equations: Iterate through all active motors, calculate their individual thrust vectors, apply their radial offset arms, and sum the resultant linear forces and rotational torques around the 3D CG.
* Implement asymmetric flame-out detection (e.g., if one pod motor burns faster than the others, correctly model the induced spin).

### Phase 4: Aerodynamics Proxy
* Implement a simplified drag-multiplier heuristic in `aerodynamics.rs` for lateral pods, or allow the Python orchestrator to query OpenRocket *once* for the base aerodynamic coefficients of the PodSet, and inject those static aero curves into the Rust AST JSON payload (`aero_stability_table`).

## 4. Verification Protocol
* **Thrust Torque Test:** Construct an asymmetric 1-pod rocket in Rust, verify it tumbles violently upon ignition.
* **Hoverslam Test:** Run a batch of 1,000 different ignition delays through the Rust engine in under 1 second; verify the optimal delay yields a touchdown speed matching OpenRocket within ±2 m/s.
