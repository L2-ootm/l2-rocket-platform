# Análise Arquitetural do `l2_engine` (Rust)

> Projeto: L2-OSIFOG — Simulador de foguetes de sondagem multiestágio  
> Motor: `l2_engine` (crate Rust)  
> Data da análise: Julho 2026  
> Total de módulos Rust analisados: 48 arquivos `.rs`

---

## Índice

1. [Estrutura de Módulos](#1-estrutura-de-módulos)
2. [AST (ast.rs)](#2-ast-astrs)
3. [sim_core — Subsistemas](#3-sim_core--subsistemas)
   - [dynamics/ (6DOF)](#31-dynamics-6dof)
   - [gnc/ (PID, Guidance, TVC)](#32-gnc-pid-guidance-tvc)
   - [physics/ (Aerodinâmica, Atmosfera, Gravidade)](#33-physics-aerodinâmica-atmosfera-gravidade)
   - [sim/ (Run loop, Adaptativo, Axial, Integrador)](#34-sim-run-loop-adaptativo-axial-integrador)
   - [io/ (CSV + JSON)](#35-io-csv--json)
   - [orbital/ (Elementos, Manobras, Propagador)](#36-orbital-elementos-manobras-propagador)
   - [vehicle/ (Stage, Mission, Builders)](#37-vehicle-stage-mission-builders)
4. [PhysicsMode — OpenRocketLegacy vs HyperReal](#4-physicsmode--openrocketlegacy-vs-hyperreal)
5. [Divergence — Ridge Regression](#5-divergence--ridge-regression)
6. [Barrowman — CP, CNa, Margem de Estabilidade](#6-barrowman--cp-cna-margem-de-estabilidade)
7. [geometry.rs — Tipos Compartilhados](#7-geometryrs--tipos-compartilhados)
8. [mission_adapter.rs — Pipeline .ork → Simulação](#8-mission_adapterrs--pipeline-ork--simulação)
9. [Bin Tools (main.rs + bins)](#9-bin-tools-mainrs--bins)
10. [Interface Python — dual_engine_*.py](#10-interface-python--dual_engine_py)
11. [Padrões de Design](#11-padrões-de-design)
12. [Fluxo de Dados End-to-End](#12-fluxo-de-dados-end-to-end)

---

## 1. Estrutura de Módulos

```
l2_engine/src/
│
├── lib.rs                  # Raiz da library crate
│   └── pub mods: ast, barrowman, builder, divergence, errors,
│                 geometry, mass_calculator, mission_adapter,
│                 motor_db, openrocket_nose, sim_core, xml_parser
│   └── pub enum PhysicsMode { OpenRocketLegacy, HyperReal }
│   └── pub use simulate_rocket (re-export convenience)
│
├── main.rs                 # Binário padrão "l2_engine"
│   └── Simula o veículo de referência L2_Hyper_Parallel_15K.ork
│
├── ast.rs                  # Sistema de AST para evolução orgânica
│   ├── AstNode, AstEvalBatch, AstCandidate, AstEvalResult
│   ├── AstObjective, ExecutionProfile (SuperSpeed/Balanced/AuthorityHeavy)
│   ├── ast_to_geometry()   # Compilador AST → RocketGeometry
│   ├── evaluate_ast()      # Entry point de scoring do GA
│   └── evaluate_ast_inner() # Pipeline completa (geom→margins→mission→sim→score)
│
├── barrowman.rs            # Aerodinâmica Barrowman (CP, CNa, drag tables)
│   └── AerodynamicCoefficients, FinGeometryDerived
│
├── builder.rs              # CG e margem de estabilidade multi-estágio
│   ├── stack_wet_cg()
│   └── static_margins_with_mode_at_machs()
│
├── divergence.rs           # Modelo de correção OR↔Rust via Ridge Regression
│   ├── DivergenceModel, DivergenceFeatures, CalibrationSample
│   └── RidgeConfig
│
├── errors.rs               # L2EngineError (ParseError, Io, Zip)
│
├── geometry.rs             # Contratos de geometria compartilhados
│   ├── RocketGeometry → StageGeometry[]
│   ├── NoseconeGeometry, BodyTubeGeometry, FinsetGeometry
│   ├── MotorMountGeometry, ParachuteGeometry, PointMassGeometry
│   └── NoseShape enum, SurfaceFinish enum
│
├── mass_calculator.rs      # Cálculo de massa e CG
│   ├── bodytube_mass, nosecone_mass, fin_mass
│   ├── total_mass, static_cg_from_nose, dynamic_cg_at
│   └── principal_inertia
│
├── mission_adapter.rs      # Adaptador .ork → Mission (Pipeline principal)
│   ├── OrkSimulationEnvironment
│   ├── NoOpController, build_mission(), simulate_rocket()
│   └── apply_openrocket_environment()
│
├── motor_db.rs             # Parser RASP .eng + ThrustCurve
│   ├── ThrustCurve (time_s, thrust_n, massa, diâmetro, comprimento)
│   ├── parse_eng(), parse_eng_file()
│   └── thrust_at(), mass_at(), total_impulse()
│
├── openrocket_nose.rs      # Tabelas de Cd de pressão de nariz (OpenRocket)
│   └── calculate_nose_pressure_cd(), calculate_stagnation_cd()
│
├── xml_parser.rs           # Parser .ork (zip + XML → RocketGeometry)
│   ├── extract_ork_xml(), parse_rocket_geometry()
│   └── parse_stage(), parse_nosecone(), parse_bodytube(), etc.
│
├── sim_core/
│   ├── mod.rs              # pub mods: dynamics, gnc, io, orbital, physics, sim, vehicle
│   │
│   ├── dynamics/
│   │   ├── mod.rs → pub use derivatives
│   │   ├── state.rs       # State (6DOF), Deriv, GncCommand, SimConfig, constantes
│   │   └── sixdof.rs      # derivatives(): equações de movimento 6DOF completas
│   │
│   ├── gnc/
│   │   ├── mod.rs → Controller trait, Pid, TvcController, guidance_pitch
│   │   ├── controller.rs  # Trait Controller
│   │   ├── pid.rs         # PID de eixo único
│   │   ├── tvc.rs         # TvcController (PID duplo + guidance)
│   │   └── guidance.rs    # guidance_pitch(): programa de pitch (vertical→pitchover→gravity turn)
│   │
│   ├── physics/
│   │   ├── mod.rs → atmo, gravity, aero
│   │   ├── atmosphere.rs  # ISA 1976 + umidade (7 camadas até 86 km)
│   │   ├── gravity.rs     # Gravidade ENU + J2 ECI
│   │   └── aerodynamics.rs# Força de arrasto, momento restaurador, damping
│   │
│   ├── sim/
│   │   ├── mod.rs → pub use simulate, simulate_with, simulate_summary_with_mode, rk4_step, simulate_axial
│   │   ├── runner.rs      # Loop principal de simulação (check_staging, simulate_summary_with_mode)
│   │   ├── adaptive.rs    # Seleção adaptativa de passo (8 restrições OpenRocket)
│   │   ├── axial.rs       # Simulação 1D axial (scoring rápido sem alocação)
│   │   ├── integrator.rs  # RK4 step
│   │   └── event.rs       # Detectores de evento (apogeu, altitude)
│   │
│   ├── io/
│   │   ├── mod.rs → csv, json
│   │   ├── csv.rs         # Escrita de trajetória CSV
│   │   └── json.rs        # FlightSummary + escrita JSON
│   │
│   ├── orbital/
│   │   ├── mod.rs → KeplerianElements, HohmannTransfer, OrbitalState
│   │   ├── elements.rs    # Elementos Keplerianos (↔ECI)
│   │   ├── maneuvers.rs   # Transferência de Hohmann
│   │   └── propagator.rs  # Propagador orbital RK4 (com/sem J2)
│   │
│   └── vehicle/
│       ├── mod.rs → Mission, Stage, builders
│       ├── mission.rs     # Mission + MissionBuilder + presets
│       └── stage.rs       # Stage + StageBuilder + FrictionModel + CD dinâmico
│
└── bin/                    # Binários auxiliares
    ├── ast_eval.rs         # Avaliador de ASTs em lote (JSON/JSONL)
    ├── ast_trace.rs        # Trace detalhado de 1 AST
    ├── divergence_fit.rs   # Treinamento do modelo de divergência
    ├── evolve.rs           # GA explorer (3-stage fixo O8000/N5800/M2245)
    ├── optimize.rs         # Otimização paramétrica Monte Carlo (N4800T)
    └── viz.rs              # Visualizador GUI (eframe/egui, feature "viz")
```

---

## 2. AST (ast.rs)

### 2.1 Sistema de Nós

O AST (Abstract Syntax Tree) é uma representação serializável (JSON) de um foguete, usada pelo motor de evolução orgânica (Python GA). Cada nó tem `type` + `params`.

**Tipos de nós:**

| Nó | Função |
|---|---|
| `STAGE` | Delimita um estágio. Exige `name`. |
| `NOSE_CONE` | Nariz. Parâmetros: `length`, `material`, `shape`, `thickness`. |
| `BODY_TUBE` | Tubo de corpo. Parâmetros: `radius`, `length`, `material`, `thickness`. |
| `CLOSE_BODY` | Fecha o body tube atual (obrigatório). |
| `MOTOR_MOUNT` | Montagem do motor. Exige `motor_designation`. Opções: `ignition`, `ignition_delay`, `overhang`, `delay`. |
| `FIN_SET` | Aleta. Parâmetros: `root`, `height`, `sweep`, `tip`, `count`, `thickness`, `cross_section`, `material`. |
| `PARACHUTE` | Paraquedas. Parâmetros: `diameter`, `cd`, `delay`. |
| `PAYLOAD` | Massa pontual de carga útil. Parâmetro: `mass`. |

### 2.2 Compilador: `ast_to_geometry()`

Usa um **padrão Interpreter**: percorre a lista sequencial de `AstNode` e constrói um `RocketGeometry` via `PendingStage` (builder interno). Regras:

- Primeiro nó deve ser `STAGE`
- `BODY_TUBE` deve ser fechado com `CLOSE_BODY`
- Cada estágio finalizado vira `StageGeometry`
- Ao final, estágios são revertidos (para ordem de ignição: primeiro a ignite é o último na árvore)

### 2.3 Pipeline `evaluate_ast_inner()`

O pipeline completo de avaliação (chamado por `evaluate_ast` e `evaluate_ast_with_profile`):

```
AstNode[] → ast_to_geometry() → RocketGeometry
  → lookup ThrustCurve (por motor_designation)
  → enrich_ast_motor_mounts()
  → builder::static_margins_with_mode_at_machs() (margens de estabilidade)
  → build_mission() → Mission
  → apply_openrocket_environment() (se OR Legacy)
  → enforce_motor_adequacy()
  → SimConfig (dt variável por ExecutionProfile)
  → simulate_summary_with_mode() OU simulate_axial() (6DOF ou 1D)
  → Aplica calibração (ApogeeDelta/MachDelta ou DivergenceModel)
  → enforce_hard_constraints()
  → score_summary()
  → extract_features() → DivergenceFeatures
```

### 2.4 Execution Profiles

| Profile | dt | Physics Mode | Simulação |
|---|---|---|---|
| **SuperSpeed** | 0.05s | HyperReal (forçado) | `simulate_axial()` — 1D alocação zero |
| **Balanced** | 0.02s | HyperReal (forçado) | 6DOF `simulate_summary_with_mode()` |
| **AuthorityHeavy** | 0.005s | Modo herdado do batch | 6DOF completa |

### 2.5 Scoring (`score_summary()`)

Suporta objetivos nomeados: `apogee`, `mach`, `burn_time`, `flight_time`, `accel`, `mass`. Tipos: `atleast`, `atmost`, `target`, `maximize`, `minimize`. Aplica penalidade por margem estática abaixo do requisito.

---

## 3. sim_core — Subsistemas

### 3.1 dynamics/ (6DOF)

#### `state.rs` — Tipos Fundamentais

```rust
struct State {
    time: f64,
    pos: Vector3<f64>,        // ENU inertial
    vel: Vector3<f64>,
    quat: UnitQuaternion<f64>,  // body→inertial
    omega: Vector3<f64>,        // rad/s body frame
    mass: f64,
    stage_idx: usize,
    stage_activated_at: f64,
    stage_depleted_at: Option<f64>,
    parachute_deployed: bool,
}

struct Deriv {
    dpos, dvel, dquat, domega, dmass
}

struct GncCommand {
    gimbal_y: f64,  // pitch
    gimbal_z: f64,  // yaw
}

struct SimConfig {
    dt: f64,        // default 0.005 (200 Hz)
    max_time: f64,  // default 600s
}

// Constantes:
const G0: f64 = 9.80665;
const EARTH_RADIUS: f64 = 6_371_000.0;
const PROPELLANT_EPSILON_KG: f64 = 1.0e-9;
```

Métodos de `State`: `apply(&self, d: &Deriv, dt)` (Euler), `body_z()`, `pitch()`, `alpha()`.

#### `sixdof.rs` — Equações de Movimento

Função única `derivatives()` que computa **todas as forças e torques**:

**Forças (soma vetorial em `f_total`):**
1. **Gravidade** — inverso quadrado: `g = G0 * (R_earth / (R_earth + alt))²`
2. **Empuxo** — com TVC: deflete o vetor de empuxo por `gimbal_y`/`gimbal_z`, depois rotaciona do corpo → inercial. Skip de rotação quando `burning==false` (bugfix 01-08: evita NaN).
3. **Arrasto aerodinâmico** — `q_dyn = 0.5 * ρ * v²`, `CD` via `stage.cd_at_conditions()`. Paraquedas adiciona área quando deployado.
4. **Força normal aerodinâmica** — baseada em `cn_alpha` e ângulo de ataque. Estol limita a 20°. Inclui body lift Galejs.
5. **Força no rail de lançamento** — projeta movimento ao longo do guia.

**Torques (soma em `torque_body`):**
1. **TVC torque** — braço de momento do nozzle × força de empuxo desviada
2. **Momento restaurador aerodinâmico** — CP offset × força normal (mesma força que produz sustentação lateral)
3. **Amortecimento de pitch/yaw** — não-linear, limitado ao momento restaurador

**Equação de Euler:** `I·dω = τ - ω × (I·ω)`

**Quaternion kinematics:** `dq/dt = 0.5 · q · ω_quat`

**Mass flow:** `dm/dt = -thrust(t) / (isp · g₀)` (seguindo a curva real, não o pico constante — bugfix crítico 01-08 que corrigiu perda de ~45% do impulso).

**Launch guide constraint:** Enquanto o foguete está no rail de lançamento, posição/velocidade/atitude são projetadas na direção do guia.

### 3.2 gnc/ (PID, Guidance, TVC)

#### `controller.rs` — Trait `Controller`

```rust
trait Controller {
    fn control(&mut self, state: &State, mission: &Mission, dt: f64) -> GncCommand;
    fn reset(&mut self) {}
    fn name(&self) -> &str;
}
```

#### `pid.rs` — PID de eixo único

```rust
struct Pid { kp, ki, kd, integral, prev_error }
fn update(error, dt) → output
// Anti-windup: integral clampada em [-1, 1]
```

#### `guidance.rs` — Programa de Pitch

3 fases:
1. **Ascensão vertical** (`t < 2s`): pitch = 90°
2. **Pitchover linear** (`2s ≤ t < 15s`): interpola de 90° → 45°
3. **Gravity turn** (`t ≥ 15s`): pitch = flight path angle (`asin(v_z / |v|)`)

#### `tvc.rs` — TvcController

Combina 2 PIDs (pitch + yaw) com o guidance. Pitch: erro = desired_pitch - current_pitch. Yaw: erro = -body_z.x / |body_z.yz| (mantém zero). Gimbal max = `tvc_max` do estágio (default 0.1 rad).

**Implementa a trait `Controller`** — usado pelo loop de simulação.

Também exporta `GncSystem` como type alias para `TvcController`.

#### `NoOpController` (em mission_adapter.rs)

Retorna `GncCommand::default()` (gimbal zero). Usado para voos balísticos sem controle ativo.

### 3.3 physics/ (Aerodinâmica, Atmosfera, Gravidade)

#### `atmosphere.rs` — ISA 1976 + Umidade

Modelo padrão ISA 1976 com **7 camadas** de 0 a 86 km:

| Camada | Altitude (km) | Gradiente |
|---|---|---|
| Troposfera | 0–11 | -6.5 K/km |
| Tropopausa | 11–20 | Isotérmica 216.65 K |
| Estratosfera I | 20–32 | +1.0 K/km |
| Estratosfera II | 32–47 | +2.8 K/km |
| Mesosfera I | 47–51 | Isotérmica 270.65 K |
| Mesosfera II | 51–71 | -2.8 K/km |
| Mesosfera III | 71–86 | -2.0 K/km |

Acima de 86 km: decaimento exponencial.

**Correção de umidade**: calcula pressão de saturação (fórmula OpenRocket), corrige a constante do gás `R`, afeta densidade.

**Velocidade do som**: `165.77 + 0.606 * T` (aproximação linear OpenRocket, não a fórmula `sqrt(γRT)`).

**Viscosidade cinemática**: Lei de Sutherland.

#### `gravity.rs` — Gravidade

- **ENU**: `g = G0 * (R_earth / (R_earth + alt))²`
- **ECI** (para propagador orbital): `gravity_pointmass_eci()` e `gravity_j2_eci()` com J2
- Constantes: `MU_EARTH = 3.986004418e14`, `R_EARTH_ECI = 6_378_137.0`, `J2 = 1.08263e-3`

#### `aerodynamics.rs` — Forças Aerodinâmicas

Funções puras:
- `drag_force()`: `F_drag = -q·S·Cd · v̂`
- `restoring_moment()`: torque de restauração do CP offset
- `damping_moment()`: amortecimento proporcional à velocidade angular

> **Nota:** O `sixdof.rs` NÃO usa estas funções — implementa a aerodinâmica inline dentro de `derivatives()` com lógica mais sofisticada (stall angle, Galejs body lift, tabela de estabilidade Mach/AoA). As funções em `aerodynamics.rs` parecem ser utilitárias/legado.

### 3.4 sim/ (Run loop, Adaptativo, Axial, Integrador)

#### `runner.rs` — Loop Principal de Simulação

**`simulate_summary_with_mode()`** — caminho de produção (O(1) memória):
1. Inicializa `State` no pad (pos=0, vel=0, quat=I, massa=total)
2. Loop `while t < max_time`:
   - `select_runner_step()` → dt adaptativo ou fixo
   - `controller.control()` → GncCommand
   - `rk4_step()` → novo estado
   - `apply_launch_guide_constraint()`
   - `check_staging()` — separação de estágios
   - Verifica NaN → `Err("simulation_diverged")`
   - Atualiza apogeu, max_speed, max_mach, max_accel
   - Verifica liftoff (altitude > 1m)
   - Verifica deadline de no-liftoff
   - Se `stop_at_apogee=true` e último estágio depletado e `v_z ≤ 0`: break
   - Se altitude ≤ 0: impacto, break
3. Retorna `FlightSummary`

**`simulate_with()` / `simulate_loop()`** — versão com alocação de trajetória completa (usada por `ast_trace` e testes).

**`check_staging()`** — lógica de separação:
1. Verifica se propellente restante ≤ tolerância depleção
2. Marca `stage_depleted_at` na primeira vez
3. Após `separation_coast` segundos: remove massa seca do estágio, avança `stage_idx`, reseta `stage_activated_at`
4. Se último estágio e tem paraquedas: deploy após `separation_coast + parachute_delay`

**`select_runner_step()`** — modo-dependente:
- **HyperReal**: retorna `dt = config.dt` fixo
- **OpenRocketLegacy**: delega ao `select_time_step()` adaptativo

#### `adaptive.rs` — Passo Adaptativo (OpenRocket)

Implementa 8 restrições do `RK4SimulationStepper.step()` do OpenRocket 24.12:

| Restrição | Descrição |
|---|---|
| User | `dt` do usuário (baseline) |
| Event | Distância até próximo evento (ignição, burnout, separação) |
| PitchAngle | `max_angle_step / pitch_rate` |
| RollAngle | `MAX_ROLL_STEP / roll_rate` |
| RollRateChange | `MAX_ROLL_RATE_CHANGE / roll_accel` |
| PitchYawRateChange | `MAX_PITCH_YAW_CHANGE / lateral_angular_accel` |
| LaunchRodDistance | `(rod_length/10) / speed` (só no rail) |
| Growth | `1.5 × dt_anterior` |

O menor dt entre todas as restrições vence.

#### `axial.rs` — Simulação 1D Axial

**Caminho SuperSpeed.** Sem alocação de trajetória, sem attitude, sem AoA. Usa RK2 (midpoint) com clipping de eventos. Penas 2 avaliações de força por passo.

Forças: empuxo (`active_thrust`) + arrasto (`cd_at_conditions`) + gravidade. Vento apenas componente Z.

Propellente: consumo por `mass_flow = thrust / (isp * g₀)`. Curvas reais: usa `thrust_at()`. Motor constante: `stage.thrust`.

Apogeu detectado por interpolação linear da velocidade vertical (elimina quantização do dt).

#### `integrator.rs` — RK4 Step

```rust
fn rk4_step(state, mission, cmd, dt) -> State {
    k1 = derivatives(state)
    k2 = derivatives(state.apply(k1, dt/2))
    k3 = derivatives(state.apply(k2, dt/2))
    k4 = derivatives(state.apply(k3, dt))
    // Média ponderada + normalização do quaternion
}
```

#### `event.rs` — Detectores de Evento

- `distance_to_next_scheduled_event()`: distância em segundos até ignição, burnout, separação, deploy de paraquedas
- `distance_to_predicted_apogee()`: `v_z / -a_z` (predição linear)
- `ApogeeDetector`: detecta cruzamento `v_z > 0 → v_z ≤ 0`
- `AltitudeDetector`: detecta cruzamento de altitude configurável

### 3.5 io/ (CSV + JSON)

#### `json.rs` — FlightSummary

```rust
struct FlightSummary {
    apogee_m, apogee_time, max_speed, max_mach,
    max_accel, max_accel_g, flight_time, impact_speed,
}
```

Métodos: `from_trajectory()`, `from_trajectory_with_wind()`. Escrita: `write_summary()`, `write_summary_file()`.

#### `csv.rs` — Trajetória CSV

Colunas: time, pos_x/y/z, vel_x/y/z, quat_w/x/y/z, omega_x/y/z, mass, stage_idx, pitch_deg, alpha_deg.

### 3.6 orbital/ (Elementos, Manobras, Propagador)

Módulo para simulação orbital (pós-lançamento). Não usado no pipeline principal de foguete de sondagem.

#### `elements.rs` — KeplerianElements

```rust
struct KeplerianElements { sma, ecc, inc, raan, argp, true_anom }
```

Conversões: `to_state_vector()` (PQW → ECI), `from_state_vector()` (ECI → Kepler), `period()`, `circular()`.

#### `maneuvers.rs` — Hohmann

```rust
struct HohmannTransfer { dv1, dv2, total_dv, transfer_time, r1, r2 }
fn hohmann(r1, r2) → HohmannTransfer
```

#### `propagator.rs` — Propagador RK4

```rust
struct OrbitalState { time, pos (ECI), vel (ECI) }
fn propagate_orbit(initial, dt, duration, use_j2) → Vec<OrbitalState>
```

### 3.7 vehicle/ (Stage, Mission, Builders)

#### `mission.rs` — Mission + MissionBuilder

```rust
struct Mission {
    name, stages: Vec<Stage>,
    wind_velocity_mps: Vector3<f64>,
    launch_guide: Option<LaunchGuide>,
    relative_humidity: f64,
}
```

Métodos: `total_mass()`, `total_delta_v()`, `active_stage(idx)`.

**MissionBuilder**: fluent API (`new()`, `.stage()`, `.wind_velocity_mps()`, `.launch_guide()`, `.build()`).

**Presets**: `presets::pathfinder()` — 2-stage de referência.

#### `stage.rs` — Stage + StageBuilder

```rust
struct Stage {
    name, dry_mass, propellant_mass, thrust, isp, cd, area,
    inertia: Vector3<f64>, nozzle_offset, cp_offset,
    dry_cg_from_nose, motor_axial_offset_m,
    rotational_fixed_mass_kg, rotational_fixed_cg_from_nose,
    tvc_max, thrust_curve: Vec<(f64, f64)>,
    cn_alpha: Option<f64>,
    aero_stability_table: Vec<(f64, f64, f64, f64, f64)>,  // Mach, AoA, cp, CNa, damping
    pitch_damping_multiplier,
    cd_table: Vec<(f64, f64)>,         // Mach, CD total (sea-level)
    cd_nonfric_table: Vec<(f64, f64)>, // pressão + base + onda
    friction_params: Option<FrictionParams>,
    ignition_delay, separation_coast,
    parachute_delay: Option<f64>,
    parachute_cd_area: Option<f64>,
}
```

**CD dinâmico** — `cd_at_conditions(mach, speed, kinematic_viscosity)`:
1. CD não-friccional: lookup em `cd_nonfric_table` (pressão + base + onda)
2. CD friccional: Reynolds → `Cf` segundo modelo (HyperReal ou OpenRocketLegacy)
3. Soma: `CD_total = CD_friction + CD_nonfriction`

**Dois modelos de atrito:**

| Modelo | Fórmula |
|---|---|
| **HyperReal** | `Cf = 0.455 / (log10(Re_eff))^2.58 / (1 + 0.144·M²)^0.65`, com `Re_crit = 51·(roughness/L)^(-1.039)` |
| **OpenRocketLegacy** | `Cf = 1/(1.5·ln(Re) - 5.6)²`, com correção de compressibilidade e rugosidade |

**Tabela de estabilidade** — `stability_at(mach, aoa)`:
Interpolação bilinear (Mach × AoA) na tabela pré-computada durante `build_mission()`.

**CG dinâmico** — `cg_from_nose_at_propellant()`:
`CG = (fixed_mass * fixed_cg + propellant * motor_offset) / total`

**StageBuilder**: fluent API com 25+ métodos encadeados.

---

## 4. PhysicsMode — OpenRocketLegacy vs HyperReal

### Declaração (lib.rs)
```rust
enum PhysicsMode { OpenRocketLegacy, HyperReal }
```

### Diferenças por Subsistema

| Subsistema | OpenRocketLegacy | HyperReal |
|---|---|---|
| **Passo de integração** | Adaptativo (`adaptive.rs` — 8 restrições) | Fixo (config.dt) |
| **Massa do motor mount** | Incluída (`motor_mount_tube_mass`) | Excluída (zerada em `build_mission`) |
| **Fricção CD** | `openrocket_skin_friction_cf` + rugosidade limitada | `hyperreal_skin_friction_cf` (mais precisa, transição laminar→turbulento) |
| **Correção de corpo (friction)** | `1 + 1/(2·fineness_ratio)` | Sem correção |
| **Ambiente OR** | Aplica `apply_openrocket_environment()` (vento, guia, umidade) | Não aplica |
| **Tolerância depleção** | `PROPELLANT_EPSILON_KG = 1e-9` | `0.01` (mais tolerante) |
| **Paraquedas** | Ignorado (não configurado no builder) | Configurado no builder via `parachute_delay`/`parachute_cd_area` |
| **ExecutionProfile SuperSpeed** | Não usado (força HyperReal) | Usa `simulate_axial()` |
| **Uso principal** | Validação contra OpenRocket ground truth | Scoring de produção do GA |

### Na prática:
- **`main.rs`**: usa HyperReal
- **`ast_eval.rs`**: configurável por parâmetro JSON (`physics_mode`)
- **`evolve.rs`**: configurável via CLI (`--physics hyperreal|openrocket`)
- **`build_mission()`**: zeroza o motor mount tube se HyperReal, inclui se OpenRocketLegacy

---

## 5. Divergence — Ridge Regression

### Problema
O motor Rust produz apogeu e Mach que divergem do OpenRocket Java (ground truth). O `DivergenceModel` aprende um mapeamento de correção.

### Features (25 dimensões fixas)

| Índice | Feature |
|---|---|
| 0 | Impulso total (N·s) |
| 1 | Empuxo de pico (N) |
| 2 | Tempo de queima total (s) |
| 3 | Diâmetro do motor (m) |
| 4 | Massa total do motor (kg) |
| 5 | Comprimento total do foguete (m) |
| 6 | Raio médio do corpo (m) |
| 7 | Comprimento do nariz (m) |
| 8 | Código da forma do nariz (numérico) |
| 9 | Contagem total de aletas |
| 10 | Corda média da raiz (m) |
| 11 | Altura média da aleta (m) |
| 12 | Sweep médio da aleta (m) |
| 13 | Código da seção transversal da aleta |
| 14 | Massa molhada total (kg) |
| 15 | Massa de payload (kg) |
| 16 | Fração de propellente |
| 17 | Fração de estrutura |
| 18 | Número de estágios |
| 19 | Razão de impulso (max/min) |
| 20 | Apogeu previsto (m) |
| 21 | Mach máximo previsto |
| 22 | Aceleração máxima (g) |
| 23 | Velocidade máxima (m/s) |
| 24 | Tempo de voo (s) |

### Modelo: Ridge Regression

```rust
struct DivergenceModel {
    config: RidgeConfig,
    mean: [f64; 25], scale: [f64; 25],
    apogee_coefficients: Vec<f64>,  // 26 = intercept + 25 features
    mach_coefficients: Vec<f64>,
    samples: Vec<CalibrationSample>,
    trained: bool,
}
```

**Normalização:** cada feature é normalizada para média 0, variância 1 no fit.

**Matriz de design:** `n_samples × 26` (coluna 0 = 1.0 para intercepto). Ridge penalty apenas nos slopes (diagonal `[1..26] += lambda`).

**Solução:** SVD da matriz normal (`XᵀX + λI`), resolve para apogeu e Mach separadamente.

**Confiança:** combina confiança amostral (`n / (n + half_samples)`) e confiança de extrapolação (`exp(-rms_distance / distance_scale)`).

### Uso no Pipeline

Em `evaluate_ast_inner()`:
1. Simula com Rust (sem correção)
2. Se `divergence_model` presente: `features = extract_features()`, `prediction = model.predict(features)`
3. Aplica: `summary.apogee_m += prediction.apogee_correction_m * confidence`
4. Idem para Mach

### CLI: `divergence_fit`

Lê `FitRequest` (JSON) com `samples[]` + `model` opcional + `config` opcional. Chama `model.update()`. Retorna `FitResponse { sample_count, model }`.

---

## 6. Barrowman — CP, CNa, Margem de Estabilidade

### Funções Exportadas

#### `nosecone_cp_and_cna(nc) → (cp_offset, 2.0)`

- CP do nariz: fórmula clássica Barrowman `X_cp = L - V/A_base`
- Volume integrado numericamente (200 slices) do perfil Haack
- CNa = 2.0 (constante para narizes pontudos, sub/trans/supersônico)

#### `fin_geometry(fs) → FinGeometryDerived`

Deriva propriedades geométricas de aleta freeform:
- `root_chord`, `tip_chord`, `span`, `mid_chord_sweep_distance`
- `exposed_area` (integração por 40 slices spanwise)
- `mac_length`, `mac_lead` (Mean Aerodynamic Chord)
- `aspect_ratio`, `cos_gamma_lead`

#### `fin_cna_at_aoa(fin, mach, aoa, fin_count, body_radius) → CNa`

- **Subsônico** (`mach ≤ 0.9`): fórmula `2π·span² / (1 + sqrt(1 + (1-M²)·β)) / A_ref`
- **Transônico** (`0.9 < mach < 1.5`): blend linear entre valor subsônico em M=0.9 e supersônico em M=1.5
- **Supersônico** (`mach ≥ 1.5`): `A_fin · (K1 + K2·α + K3·α²) / A_ref` com K1/K2/K3 de Busemann
- Fatores de interferência: body-fin `(1 + τ)` e fin-count (tabela: 4→1.0, 5→0.948, 6→0.913, 7→0.854, 8→0.81, >8→0.75)

#### `fin_cp_from_root_le(fin, mach) → CP offset`

- Subsônico (M ≤ 0.5): 25% da MAC
- Supersônico (M ≥ 2.0): `(AR·β - 0.67) / (2·AR·β - 1)`
- Transônico: interpolação polinomial de 5ª ordem exata do OpenRocket FinSetCalc

#### `compute_aero()` → `AerodynamicCoefficients`

Combina nariz + aletas + body lift Galejs. Produz:
- `cp_offset_from_cg`: CP combinado relativo ao CG
- `cn_alpha`: CNa total (nariz + fins × interferência + body lift)
- `cd_table`: tabela Mach→CD (fricção + pressão + base + wave)
- `cd_nonfric_table`: apenas pressão+base+wave
- `friction_params`: parâmetros para CD dinâmico em altitude
- `damping_moment_sum_m2`, `pitch_damping_multiplier`

#### `compute_aero_at_mach_and_aoa()` → Variação Mach/AoA

Usada para pré-computar a `aero_stability_table` (100 linhas = 20 Mach × 5 AoA) em `build_mission()`.

#### Margem de Estabilidade (em `builder.rs`)

```rust
fn static_margins_with_mode_at_machs() → Vec<f64>
```

Para cada fase de voo (stack completo, sem booster, só topo):
1. `stack_wet_cg()` — CG molhado de todos os estágios ativos
2. `compute_aero_at_mach()` — CP para aquele Mach
3. Margem = `(CP - CG) / (2 · r_ref)` em calibres (diâmetros de corpo)

---

## 7. geometry.rs — Tipos Compartilhados

### Hierarquia

```
RocketGeometry
  └── stages: Vec<StageGeometry>
        ├── name: String
        ├── nosecone: Option<NoseconeGeometry>
        │     ├── shape: NoseShape { VonKarmanHaack, Ogive, Conical, Ellipsoid, PowerSeries, Parabolic }
        │     ├── shape_parameter, length, aft_radius
        │     ├── thickness, material_density, finish, axial_offset_m, ballast_mass
        ├── bodytubes: Vec<BodyTubeGeometry>
        │     ├── length, radius, thickness, material_density, finish, axial_offset_m
        ├── finsets: Vec<FinsetGeometry>
        │     ├── fin_count, points: Vec<(f64,f64)>, thickness, cross_section
        │     ├── material_density, finish, cant_rad, axial_offset_m
        ├── point_masses: Vec<PointMassGeometry>
        │     ├── mass_kg, axial_offset_m
        ├── motor_mount: MotorMountGeometry
        │     ├── ignition_event, ignition_delay, motor_designation, motor_overhang_m
        │     ├── mount_length/radius/thickness/density/offset
        │     └── ejection_charge_delay
        ├── separation: Option<SeparationConfig>
        │     ├── event, delay, altitude
        ├── parachute: Option<ParachuteGeometry>
        │     ├── diameter, cd, deploy_delay, packed_mass_kg, axial_offset_m
        └── axial_offset_m: f64

SurfaceFinish { Polished, Smooth, Unfinished, Rough }
  └── roughness_m() → 1e-6, 2e-6, 6e-6, 2e-5
```

### Quem produz / consome

| Produtor | Consumidores |
|---|---|
| `xml_parser::parse_rocket_geometry()` | `mission_adapter::build_mission()`, `builder::stack_wet_cg()` |
| `ast::ast_to_geometry()` | `ast::evaluate_ast_inner()` |
| `geometry` (tipos) | `mass_calculator`, `barrowman`, `builder`, `divergence`, `mission_adapter` |

---

## 8. mission_adapter.rs — Pipeline .ork → Simulação

### Função Principal: `simulate_rocket()`

```rust
pub fn simulate_rocket(
    ork_path: &Path,
    eng_text: &str,
    motor_designation: &str,
    physics_mode: PhysicsMode,
) -> Result<FlightSummary, L2EngineError>
```

Pipeline:
1. `xml_parser::extract_ork_xml()` → extrai `rocket.ork` do zip
2. `xml_parser::parse_rocket_geometry()` → `RocketGeometry`
3. `motor_db::parse_eng()` → `ThrustCurve`
4. Duplica curva para todos os estágios (compatibilidade)
5. `build_mission()` → `Mission`
6. Se OR Legacy: `parse_ork_simulation_environment()` + `apply_openrocket_environment()`
7. `SimConfig { dt: 0.005, max_time: 600 }`
8. `NoOpController`
9. `simulate_summary_with_mode(mission, config, &mut controller, physics_mode, false)`

### `build_mission()` — O Coração do Adaptador

Recebe `RocketGeometry` + `[ThrustCurve]` + `PhysicsMode` → `Mission`.

**Validações:**
- Número de estágios = número de curvas
- Motor cabe dentro do body tube (folga radial de 1mm)
- Propellente > 0

**Para cada estágio (i):**
1. Calcula `dry_motor_mass`, `isp = total_impulse / (propellant_mass · g₀)`
2. Calcula motor axial offset (do fundo do body tube, com overhang)
3. **Mass stage**: se HyperReal, zera motor mount tube; se OR Legacy, mantém
4. Computa `total_mass`, `static_cg_from_nose`
5. **Rotational fixed mass**: massa seca + propellente de todos os estágios superiores (para inércia do stack)
6. **Aerodynamics**: `barrowman::compute_aero()` com 20 pontos de Mach × 5 AoA → `aero_stability_table`
7. **Inércia do stack**: `principal_inertia()` de cada estágio + Steiner
8. **StageBuilder**: popula todos os campos (dry_mass, propellant, thrust_curve, cd_table, aero_stability_table, etc.)
9. **Se HyperReal**: configura paraquedas (delay + cd_area)
10. **MissionBuilder**: adiciona estágio

### `NoOpController`

```rust
impl Controller for NoOpController {
    fn control(&self, _state, _mission, _dt) -> GncCommand { GncCommand::default() }
}
```

Usado em todos os pipelines de scoring (sem guidança ativa).

### `OrkSimulationEnvironment`

Extraído do XML de simulação do .ork:
- `launch_rod_length_m`, `launch_rod_angle_rad`, `launch_rod_direction_rad`
- `wind_speed_mps`, `wind_direction_rad`, `relative_humidity`

---

## 9. Bin Tools (main.rs + bins)

### `main.rs` — `l2_engine` (binário padrão)

Caminho fixo: `L2_Hyper_Parallel_15K.ork` com motor `N4800T`. PhysicsMode: HyperReal. Imprime apogeu (km) e Mach.

### `ast_eval` — Avaliador de AST em Lote

**Protocolo JSON/JSONL.** Lê um `AstEvalBatch` (lista de `AstCandidate`). Avalia cada candidato em paralelo (`rayon::par_iter()`). Retorna `AstEvalBatchOutput { results: Vec<AstEvalResult> }`.

Modos:
- `--input <file>`: lê de arquivo
- `--serve`: modo JSONL (stdin/stdout linha por linha)
- `--capabilities`: retorna JSON de capacidades do protocolo

Carrega todos os motores de `l2_engine/motors/*.eng` dinamicamente.

Usa `evaluate_ast_with_profile()` com suporte a `ExecutionProfile`, `DivergenceModel`, `calibrations`.

### `ast_trace` — Trace Detalhado

Lê 1 `AstCandidate`. Simula com `OpenRocketLegacy` (6DOF completa). Retorna `TraceOutput` com:
- `summary`: apogeu, Mach, etc.
- `points`: até 600 pontos de trajetória com 35 campos cada (altitude, Mach, CD, thrust, drag, CP, CG, AoA, pitch rate, inércia, etc.)

Usa `simulate_with_mode()` em vez de `simulate_summary_with_mode()` para ter acesso à trajetória completa.

Variáveis de ambiente:
- `L2_TRACE_DT`: dt da simulação (default 0.005)
- `L2_TRACE_DENSE`: se definida, amostragem densa nos primeiros 15s

### `divergence_fit` — Treinamento de Divergência

Lê `FitRequest` (JSON) com `samples: Vec<CalibrationSample>`. Chama `DivergenceModel::update()`. Retorna modelo treinado.

### `optimize` — Otimização Paramétrica Monte Carlo

Carga o foguete base `.ork`. Aplica mutações aleatórias (0.5–2.0× nose, 0.5–1.5× fin, 0.7–1.3× body). 500 simulações paralelas com `rayon`. Score = `-|apogee - 83456| + mach·10`. Exibe top 10.

**Não usa o sistema AST** — muta diretamente os campos do `RocketGeometry` parseado de `.ork`.

### `evolve` — GA Explorer (3-stage fixo)

GA de população completa com os motores O8000/N5800/M2245. Usa `DesignGenome` (genoma paramétrico fixo, não AST — legado anterior ao AST engine).

- População: 300 indivíduos
- Gerações: 40 (early stop após 8 sem melhora)
- Seleção: torneio de 3
- Crossover + mutação (15%)
- Elitismo: top 5%
- Saída: `elite.json`

Importa `builder::build_geometry()` (converte `DesignGenome` → `RocketGeometry`) e `genome` module (não mais presente no código — módulo removido/renomeado; o `evolve.rs` referencia `l2_engine::genome` que não existe mais na versão atual).

### `viz` — Visualizador GUI

Usa `eframe`/`egui_plot` (feature `viz`). Simula a missão `pathfinder()` (2-stage). Mostra 4 gráficos: Altitude×Tempo, Velocidade×Tempo, Pitch×Tempo, Perfil Trajetória.

---

## 10. Interface Python — dual_engine_*.py

### `dual_engine_python_loop.py`

**Pipeline "Dual Engine" original (legado, anterior ao AST):**

1. Gera parâmetros aleatórios (nose_m, fin_m, body_m)
2. Aplica mutações no XML do `.ork` via `ElementTree`
3. Salva `.ork` modificado
4. Invoca **OpenRocket Java** via JPype (`GeneralRocketLoader` + `simulate()`)
5. Coleta apogeu e Mach
6. Loop genético

**Comunicação:** FFI indireta — Python modifica XML, empacota em .ork, carrega no JVM do OpenRocket via JPype. **Rust não está envolvido neste script** — é um predecessor histórico.

### `dual_engine_workflow.py`

**Pipeline pós-Rust (workflow de otimização):**

1. Lê resultados do `optimize` bin (Rust)
2. Aplica multiplicadores da topologia vencedora no XML do `.ork`
3. Re-empacota em `.ork`

**Comunicação:** Python lê arquivo de texto (`optimize_goal_results.txt`) gerado pelo binário `optimize` (Rust). Depois manipula XML. **Sem RPC, sem FFI direta.**

### Como o GA Python chama o Rust hoje

O pipeline de evolução orgânica atual funciona assim:

1. **Python (organic_loop.py)** gera candidatos como `AstNode[]` (JSON)
2. **Python invoca `ast_eval --serve`** (Rust) como subprocesso persistente
3. Comunicação via **JSONL sobre stdin/stdout**:
   - Python escreve uma linha JSON (`AstEvalBatch`)
   - Rust responde com uma linha JSON (`AstEvalBatchOutput`)
4. Rust retorna `score`, `apogee_m`, `mach`, `min_static_margin`, `features`

**Não há FFI (Python C extension, PyO3, etc.)** — a comunicação é exclusivamente via subprocesso com protocolo JSONL. Isso permite:
- Isolamento completo de memória
- Paralelismo natural (múltiplos processos Rust)
- Sem dependências de linking Python↔Rust

---

## 11. Padrões de Design

| Padrão | Onde | Como |
|---|---|---|
| **Builder** | `StageBuilder`, `MissionBuilder` | Fluent API encadeada (`new().dry_mass().propellant_mass().build()`) |
| **Adapter** | `mission_adapter.rs` | Traduz `RocketGeometry` + `ThrustCurve` → `Mission` (tipo do `rocket-sim`) |
| **Interpreter** | `ast.rs` `ast_to_geometry()` | Percorre AST → constrói `RocketGeometry` |
| **Strategy** | `ExecutionProfile` | Algoritmo de simulação diferente (axial vs 6DOF, dt diferente) |
| **Strategy** | `PhysicsMode` | Comportamento físico diferente (HyperReal vs ORLegacy) |
| **Strategy** | `FrictionModel` | Cálculo de atrito (HyperReal vs OpenRocketLegacy) |
| **Template Method** | `evaluate_ast_inner()` | Pipeline fixo com pontos de variação (physics_mode, execution_profile, divergence_model) |
| **Controller (MVC)** | `Controller` trait | Separa GNC (controle ativo) da dinâmica |
| **Observer** | `EventDetector` trait | Detectores passivos de apogeu/altitude |
| **DTO** | `AstEvalBatch`, `AstEvalResult`, etc. | Objetos serializáveis para fronteira Python↔Rust |
| **Null Object** | `NoOpController` | Controlador que não faz nada (voos balísticos) |
| **Command** | `GncCommand` | Encapsula comando de gimbal como objeto |
| **State** | `State` struct | Estado explícito 6DOF transportado entre steps |

---

## 12. Fluxo de Dados End-to-End

### Pipeline de Evolução (Python GA → Rust → Python)

```
┌─────────────────────────────────────────────────────────────┐
│  Python (organic_loop.py)                                   │
│  │                                                          │
│  ├─ Gera AstCandidate[] (AST JSON)                          │
│  │                                                          │
│  ├─ Escreve AstEvalBatch como JSONL linha → stdin           │
│  │                                                          │
│  │    ┌──────────────────────────────────────────────┐       │
│  │    │  Rust (ast_eval --serve)                     │       │
│  │    │                                              │       │
│  │    │  1. ast_to_geometry() → RocketGeometry        │       │
│  │    │  2. lookup ThrustCurve do motor              │       │
│  │    │  3. builder::static_margins_with_mode()       │       │
│  │    │  4. build_mission() → Mission                │       │
│  │    │     ├─ mass_calculator                       │       │
│  │    │     ├─ barrowman::compute_aero()             │       │
│  │    │     └─ aero_stability_table pré-computada    │       │
│  │    │                                              │       │
│  │    │  5. simulate_summary_with_mode()             │       │
│  │    │     ├─ runner loop (check_staging, RK4)      │       │
│  │    │     ├─ sixdof::derivatives (forces + torques)│       │
│  │    │     ├─ atmosphere::isa_with_humidity          │       │
│  │    │     └─ stage::cd_at_conditions (friction)    │       │
│  │    │                                              │       │
│  │    │  6. Aplica divergência/calibração            │       │
│  │    │  7. score_summary()                          │       │
│  │    │  8. extract_features()                       │       │
│  │    │                                              │       │
│  │    │  ──→ AstEvalBatchOutput como JSONL (stdout)  │       │
│  │    └──────────────────────────────────────────────┘       │
│  │                                                          │
│  ├─ Lê AstEvalResult[]                                     │
│  ├─ Calcula fitness, seleção, crossover, mutação            │
│  └─ Loop                                                   │
└─────────────────────────────────────────────────────────────┘
```

### Pipeline de Validação (main.rs)

```
.ork (zip) → xml_parser::extract_ork_xml()
  → parse_rocket_geometry() → RocketGeometry
  → motor_db::parse_eng(N4800T) → ThrustCurve
  → mission_adapter::build_mission() → Mission
    ├─ mass_calculator::total_mass(), static_cg_from_nose()
    ├─ barrowman::compute_aero() (20 Mach × 5 AoA)
    └─ principal_inertia() (stack completo)
  → simulate_summary_with_mode(HyperReal)
    ├─ sixdof::derivatives() em loop RK4
    ├─ check_staging() a cada passo
    └─ atmosphere::isa_with_humidity()
  → FlightSummary { apogee_m, max_mach, ... }
```

---

## Mapa de Dependências entre Módulos

```
lib.rs
  ├── ast.rs ──────────── builder.rs, mission_adapter.rs, motor_db.rs,
  │                       geometry.rs, divergence.rs, sim_core::sim,
  │                       sim_core::io::json, sim_core::dynamics::state
  │
  ├── barrowman.rs ────── geometry.rs, sim_core::vehicle (FrictionParams)
  │
  ├── builder.rs ──────── geometry.rs, motor_db.rs, barrowman.rs, mass_calculator.rs
  │
  ├── divergence.rs ───── geometry.rs, motor_db.rs, sim_core::io::json
  │                       (nalgebra para SVD)
  │
  ├── mass_calculator.rs─ geometry.rs, motor_db.rs (nalgebra para inércia)
  │
  ├── mission_adapter.rs─ xml_parser.rs, geometry.rs, motor_db.rs,
  │                       mass_calculator.rs, barrowman.rs,
  │                       sim_core::vehicle, sim_core::sim,
  │                       sim_core::dynamics::state, sim_core::gnc,
  │                       sim_core::io::json
  │
  ├── motor_db.rs ─────── errors.rs
  │
  ├── openrocket_nose.rs─ geometry.rs (NoseShape)
  │
  ├── xml_parser.rs ───── errors.rs, geometry.rs (roxmltree, zip)
  │
  └── sim_core/
      ├── dynamics/ ───── physics/, vehicle/, gnc/ (derivatives)
      ├── gnc/ ────────── dynamics/state, vehicle/
      ├── physics/ ────── apenas constantes / funções puras
      ├── sim/ ────────── dynamics/, gnc/, io/, physics/, vehicle/
      ├── io/ ─────────── dynamics/state, physics/, vehicle/
      ├── orbital/ ────── physics/gravity
      └── vehicle/ ────── dynamics/state

Binários:
  ├── main.rs ─────────── l2_engine::mission_adapter
  ├── ast_eval.rs ─────── l2_engine::ast, motor_db, rayon
  ├── ast_trace.rs ────── l2_engine::ast, motor_db, sim_core::sim
  ├── divergence_fit.rs ─ l2_engine::divergence
  ├── optimize.rs ─────── l2_engine::xml_parser, motor_db, mission_adapter, sim_core
  ├── evolve.rs ───────── l2_engine::builder, mission_adapter, motor_db (rayon, rand)
  └── viz.rs ──────────── l2_engine::sim_core (eframe, egui_plot)
```

---

## Resumo de Métricas

| Métrica | Valor |
|---|---|
| Total arquivos `.rs` | 48 |
| Linhas de código Rust | ~18.500 |
| Módulos na library | 12 (ast, barrowman, builder, divergence, errors, geometry, mass_calculator, mission_adapter, motor_db, openrocket_nose, sim_core, xml_parser) |
| Subsistemas sim_core | 7 (dynamics, gnc, io, orbital, physics, sim, vehicle) |
| Sub-módulos sim_core | 18 (6 + 4 + 2 + 3 + 3 + 5 + 2) |
| Binários | 6 (l2_engine, ast_eval, ast_trace, divergence_fit, optimize, viz) |
| Dependências externas | 11 (roxmltree, serde, serde_json, thiserror, anyhow, zip, nalgebra, rayon, rand, rand_distr, eframe/egui_plot) |
| Feature flags | 1 (`viz` = eframe + egui_plot) |
| Protocolo Python↔Rust | Subprocesso JSONL (stdin/stdout) |
| Modelos físicos | 2 (HyperReal, OpenRocketLegacy) |
| Integrador | RK4 clássico |
| Passo | Fixo (0.005/0.02/0.05s) ou adaptativo (8 restrições OR) |
| Dimensão simulação | 1D axial (SuperSpeed) ou 6DOF completa |
