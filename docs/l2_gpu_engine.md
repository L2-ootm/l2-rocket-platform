# L2 GPU Engine Architecture (WGPU + WGSL)

To achieve a fully self-contained, vendor-agnostic GPU physics engine (AMD, NVIDIA, Intel, Apple) without external black-box dependencies like `rocket-sim`, we will use **Rust + `wgpu`**. 

By utilizing **WGSL** (WebGPU Shading Language) Compute Shaders, we can evaluate tens of thousands of rocket topologies in parallel on the GPU.

## Core Architecture

### 1. The Physics Shader (`physics.wgsl`)
Instead of relying on external crates, we will write our own RK4 (Runge-Kutta 4th Order) integrator and aerodynamics calculator strictly in WGSL. **Most importantly, this means directly porting your custom `barrowman.rs` and `openrocket_nose.rs` modules line-by-line into the GPU shader.** This guarantees that all the bespoke OpenRocket defects and aerodynamic quirks you meticulously modeled are perfectly preserved on the GPU.

**Shader Components:**
* **Standard Atmosphere Model:** Functions to calculate density ($\rho$), temperature, and speed of sound at any given altitude based on the International Standard Atmosphere (ISA) equations.
* **Mass & Thrust Interpolator:** Since we load motor files (e.g., `.rse` or `.eng`), we will bake the thrust curves into a 1D GPU texture/buffer. The shader will sample the exact thrust and motor mass decrement at $t$.
* **Barrowman Aerodynamics (WGSL Port):** A direct WGSL translation of `l2_engine/src/barrowman.rs` and `openrocket_nose.rs`. This will calculate the Center of Pressure (CP) and coefficient of drag ($C_d$) dynamically based on the rocket's current geometry and Mach number, exactly matching OpenRocket's behavior.
* **RK4 Integration Loop:** The core compute loop. For every rocket in the population, the GPU takes the state vector (altitude, velocity, mass), computes forces (Thrust - Drag - Gravity), and integrates forward by $\Delta t$ until apogee is reached.

### 2. Memory Layout (Flattening the AST)
GPUs do not understand Abstract Syntax Trees (ASTs) or complex Rust structs. They need contiguous arrays. We will flatten the genome population into heavily optimized `structs` aligned for GPU memory (`std430` layout).

```wgsl
struct RocketGenome {
    stages_count: u32,
    total_length: f32,
    max_radius: f32,
    empty_mass: f32,
    // Aerodynamic properties
    fin_count: u32,
    fin_root: f32,
    fin_span: f32,
    // ... motor references
};

struct FlightResult {
    apogee_m: f32,
    max_mach: f32,
    flight_time_s: f32,
    margin_min: f32,
    tumbled: u32,
};
```

### 3. Execution Flow
1. **Python (`organic_loop.py`)** generates the ASTs.
2. **Rust (`l2_engine_gpu`)** parses the ASTs and flattens them into an array of `RocketGenome`.
3. Rust uploads the array to a WGPU `Buffer`.
4. Rust dispatches a Compute Pipeline where `workgroups = population / 64`.
5. The GPU executes the flight physics for all rockets in a fraction of a second.
6. Rust maps the output buffer back to the CPU and returns the `FlightResult` array to Python.

## Roadmap & Implementation Steps
- [ ] **Step 1:** Initialize a new `wgpu` project inside the L2 ecosystem (e.g., `l2_engine_gpu`).
- [ ] **Step 2:** Write the WGSL Standard Atmosphere & Physics math.
- [ ] **Step 3:** Implement the WGPU Rust host code to manage buffers and dispatch the compute shader.
- [ ] **Step 4:** Translate AST topological nodes into flat `RocketGenome` floats.
- [ ] **Step 5:** Wire the new engine into `organic_loop.py` (`--evaluator wgpu`).
