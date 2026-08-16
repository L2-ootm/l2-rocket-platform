# L2-OSIFOG Organic Evolution Architecture

**Date:** July 2026
**System:** L2-OSIFOG / L2 MIND

## 1. Overview: The Organic AST Pipeline (Option 2)
The legacy approach (`l2-hyper-evolution`) utilized a rigidly defined 3-stage genome. The new **Organic Evolution Pipeline** throws away structural hardcoding in favor of an **Abstract Syntax Tree (AST)** topology generator (`rocket_ast.py`). 

- Rockets are generated as dynamic graphs of nodes (Stages, Body Tubes, Nose Cones, Motors, Fins).
- A Genetic Algorithm (`organic_loop.py`) applies evolutionary mutations (e.g. stretching tubes, shrinking fins, adding stages) and passes them to the extremely fast Rust physics surrogate engine for broad-stroke fitness evaluations.
- This allows millions of structurally diverse rockets to be evaluated natively without hitting the massive I/O and memory overhead of booting the Java Virtual Machine for OpenRocket on every candidate.

## 2. High-Precision Native OR Polisher
To hit exact competition metrics (e.g. exactly 15,000.0m apogee) without getting stuck in optimization loops, the framework now features a natively integrated **High-Precision Polisher**.

- **Implementation:** Hooked natively inside `organic_loop.py` via the `--polish` flag.
- **Algorithm:** Uses `scipy.optimize.minimize_scalar` (Bounded method) to wrap directly around the OpenRocket JVM (via `orhelper`).
- **Mechanism:** It extracts the elite candidate's AST, isolates the payload mass, and iteratively commands the OpenRocket RK4 solver until it pinpoints the *exact mass required to hit the target down to 8 decimal places*. 
- **Result:** Complete eradication of "bouncing" or infinite loops in heuristics; it directly exploits OpenRocket's own math.

## 3. Topologic Confinement & Rendering Fixes
Prior versions of the evolution engine frequently exploited physics engine boundaries or failed to render visually in OpenRocket. These vulnerabilities have been successfully patched in the AST compiler core:

- **Strict Aerodynamic Encapsulation:** The genetic algorithm previously exploited OpenRocket by mutating ultra-thin body tubes (low drag) and shoving massive motors inside them, resulting in impossible geometry that broke drag calculations. The AST Compiler now performs a pre-pass scanning the payload and motors, calculating the absolute maximum bounding box, and forcing the main `BODY_TUBE` radius to be `max(raw_radius, motor_radius + 2mm)`. 
- **Eradication of "Auto" Parsers:** OpenRocket 1.5/23.09 GUI occasionally rejects `<aftradius>auto</aftradius>` tags, defaulting to zero-radius and creating catastrophic aerodynamic blocks. The compiler now explicitly calculates mathematical continuity across junctions (e.g., injecting the exact body tube radius into the nose cone parameters).
- **Physical Volume for Mass Components:** Payloads previously had mass but no physical geometry, causing the 3D renderer to crash or draw abstract artifacts. They now receive strictly calculated `<packedlength>` and `<packedradius>`.

## 4. Next Phase: Mirroring OR Metrics in the Surrogate Engine
To ensure the Rust Engine evaluates and ranks candidates using identical logic to OpenRocket, the framework will utilize the **Continuous Knowledge Graph (CKG)** for auto-calibration.

**How it will work:**
1. **Delta Feedback Loop:** Every $N$ generations, an elite candidate is passed to OpenRocket for a ground-truth simulation.
2. **Error Calculation:** The delta between the Rust Surrogate's projected Apogee/Mach and OpenRocket's true Apogee/Mach is calculated.
4. **Mirroring:** Over time, the Rust Engine natively aligns with OpenRocket's environmental physics, effectively making the 10,000x faster surrogate mathematically indistinguishable from OpenRocket for ranking purposes.

## 5. The Continuous Knowledge Graph (CKG) & Memory Architecture

The Continuous Knowledge Graph (`organic_ckg.json`) acts as the shared, global "brain" across all evolutionary missions. Instead of restarting its learning process for each mission, it persists topological performance data globally (currently operating as a massive ~1GB JSON).

### 5.1 Resolving the "Trauma" Death Spiral
In early test runs of 6-to-8 stage vehicles, the CKG entered an irreversible death spiral.
- **The Issue:** Generation 0 constructs rockets entirely at random. For complex multi-stage topologies, this results in a nearly 100% physics failure rate (aerodynamic breakup, instability). The CKG was aggressively penalizing failed nodes (`0.35` penalty per failure).
- **The Spiral:** By Generation 1, any new rocket reusing a nose cone or body tube from Generation 0 was mathematically rejected by the CKG's `ckg_prefilter` as "doomed to fail" *before it was even simulated*. This resulted in endless loops of 0.0 scores.
- **The Fix:** The penalty multiplier in `ckg_memory.py` was drastically reduced to `0.01`, forcing the CKG to be "optimistic" and requiring significant, repeated empirical failures across multiple contexts before permanently blacklisting a component combination.
- **Continuous I/O Fix:** `organic_loop.py` was patched to dump elites continually to disk after each generation, rather than hanging for weeks waiting for the 500,000 generation loop to terminate before saving.

### 5.2 Future Roadmap: Hebbian Learning & Contextual Pruning
To prevent the CKG from endlessly expanding and to increase its intelligence, the following neuro-inspired upgrades are planned:

1. **Hebbian Synaptic Wiring ("Neurons that fire together, wire together"):**
   Rather than global bans on parts, the CKG will adopt true contextual memory. If an elliptical fin set works flawlessly on Stage 1 but causes instability on Stage 3, the graph will track edge-specific success rates, thickening the "synapse" for successful topological couplings and weakening unsuccessful ones.
2. **Dynamic Pruning (Memory Decay):**
   A mechanism to purge statistically insignificant or outdated memories (e.g., combinations that haven't been generated in 100,000 generations) to compress the memory footprint from gigabytes down to a highly optimized `< 50MB` cache.
3. **Graph Neural Network (GNN) Migration:**
   Eventually replacing the flat JSON lookup with a lightweight GNN or a Graph Database (like Neo4j) that can predict aerodynamic flutter and stability *without* requiring an explicit Rust simulation, allowing the genetic generator to produce 100% physics-compliant ASTs straight out of Generation 0.
