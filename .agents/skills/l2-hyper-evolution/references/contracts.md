> **[RETIRADA 2026-07-04]** Referência histórica do pipeline paramétrico
> fixo. Seções 4 (regras duras do `.ork`) e 5 (semânticas rocket-sim/agora
> `sim_core`) continuam tecnicamente válidas e foram migradas para
> `l2-organic-evolution/references/contracts.md`. Seções 1-3 (schema de
> missão/genoma/elite.json paramétrico) descrevem código deletado
> (`genome.rs`, `l2_engine evolve`) — não implemente contra elas.

# Contratos e formatos

Todos os schemas que atravessam fronteiras do sistema. Mudou um lado,
atualize o outro E este arquivo.

## 1. Missão (`missions/*.json`)

```json
{
  "name": "Karman M6",
  "output": "designs/optimized/L2_Hyper_100K_M6.ork",
  "payload_kg": 0.3,
  "stack": [
    { "name": "Kick",      "body_radius": 0.040,
      "motor": { "manufacturer": "Cesaroni Technology Inc.", "designation": "9977M2245-P" } },
    { "name": "Sustainer", "body_radius": 0.0508,
      "motor": { "manufacturer": "Cesaroni Technology Inc.", "designation": "20146N5800-P" } },
    { "name": "Booster",   "body_radius": 0.0825,
      "motor": { "manufacturer": "Cesaroni Technology Inc.", "designation": "40960O8000-P" } }
  ],
  "objectives": [
    { "metric": "apogee", "kind": "atleast",  "value": 100000, "weight": 1.0 },
    { "metric": "mach",   "kind": "atleast",  "value": 6.0,    "weight": 1.0 },
    { "metric": "apogee", "kind": "maximize", "scale": 1000000, "weight": 1.0 }
  ],
  "constraints": { "min_static_margin": 1.5 },
  "stability":   { "phase_machs": [0.3, 2.0, 3.0] },
  "seeds": [ { "s0_nose_len": 0.45, "...": 0 } ]
}
```

Regras:
- `stack` é TOP primeiro (índice 0 = estágio de cima). N estágios livres.
- `objectives.kind`: `atleast` (satura em value), `atmost`, `target`
  (meta exata, tolerância default 2%), `maximize`/`minimize` (com `scale`,
  desempate aberto). Métricas: `apogee`, `mach`, `vmax`, `flight_time`.
- `min_static_margin` default 1.5 cal (absorve viés 23.09↔24.12). Exigido
  em TODAS as fases de voo por `targets_met`.
- `seeds` usam as chaves de genoma abaixo; genes faltantes viram mid-bound.

## 2. Genoma (chaves compartilhadas Rust↔Python)

Convenção Python: `s{i}_*` com i=0 no TOPO. Derivado do stack:
- por estágio: `s{i}_span`, `s{i}_root` (aletas; tip=0.35·root, sweep=0.70·root)
- estágio 0 (topo): + `s0_nose_len`, `s0_ballast`
- todo estágio exceto o de baixo: + `s{i}_delay` (ignição pós-burnout do
  estágio anterior, convenção OpenRocket)
- global: `sep_delay`
- Rust exporta extras ignorados pelo Python: `s1_body_len`, `payload`.

Bounds Python escalam com o raio r do estágio: span (1.3r, 3.2r),
root (3r, 7.5r), nose (8r, 16r), ballast (0, 40r). Rust: `genome.rs BOUNDS`
(mantenha compatível ao mudar).

## 3. elite.json (handoff Etapa 1 → Etapa 2)

```json
{
  "generated_by": "l2_engine evolve v1 (pop=300, gens=40, seed=42)",
  "fitness_def": "min(apogee/1e5,1)+min(mach/6,1)+apogee/1e6; ...",
  "elite": [
    { "genome": { "s0_nose_len": 0.43, "...": 0 },
      "rust_apogee_m": 280900.0, "rust_mach": 7.30,
      "rust_static_margin_min": 0.51, "rust_score": 2.28 }
  ]
}
```

- Elite passa por filtro de diversidade (distância normalizada > 0.15) —
  16 clones travam o polish.
- `run_mission --seed-file` aceita este formato OU uma lista simples de
  genomas; mescla com os seeds da missão e limita ao total de pop/2.

## 4. Regras duras do .ork / OpenRocket 23.09 (violou = load falha)

1. Todo `configid` (em `<motorconfiguration>` e `<motor>`) é **UUID válido**;
   o MESMO UUID aparece no `<conditions><configid>` de toda `<simulation>`.
   Violação = `IllegalArgumentException ... error id` oculta pelo JPype.
2. ZIP com entrada `rocket.ork`; XML declaration com `encoding="utf-8"`
   (com hífen).
3. Multi-estágio: `<separationevent>burnout</separationevent>` +
   `<separationdelay>` dentro de `<stage>` (todos exceto o topo). Valores de
   enum: nome Java lowercase sem underscore (`burnout`, `upperignition`...).
4. Ignição de estágio superior: `<ignitionevent>burnout</ignitionevent>` +
   `<ignitiondelay>` dentro de `<motormount>`.
5. InnerTube usa `<outerradius>` — um `<radius>` é IGNORADO em silêncio
   (warning "Unknown parameter type 'radius'"), massa do mount fica errada.
6. `<digest>` no `<motor>` pina a variante exata (sem ele: "Multiple motors
   ... one chosen arbitrarily"). Resolver via database em runtime, cache em
   `motors_cache.json`.
7. Estágio de topo precisa de dispositivo de recuperação (drogue,
   `<deployevent>apogee</deployevent>`) ou o sim marca crítico.
8. Warnings inevitáveis (não caçar): precisão supersônica do Barrowman;
   "open forward airframe" dos interstages; "-P-P" no nome de motor CTI
   plugged na GUI (designação oficial + sufixo de delay da GUI).

## 5. Semânticas rocket-sim (Etapa 1)

- `geometry.stages` em ordem de IGNIÇÃO (0 = booster). Python s0 = topo →
  builder.rs inverte.
- `ejection_charge_delay` → `separation_coast` (burnout → drop do estágio).
  `SeparationConfig.delay` NÃO é consumido pelo adapter.
- `ignition_delay` é medido da separação; genoma é medido do burnout →
  `ignition_delay = delay - sep_delay` (clamp em 0).
- Motor mass no PONTO MÉDIO do primeiro bodytube do estágio → tubo
  principal primeiro no vec.
- Sem paraquedas no proxy (abriria no burnout do último estágio); massa do
  drogue+aviônica vai em `ballast_mass` do nosecone.
- `compute_aero` soma `stage.axial_offset_m + component.axial_offset_m`
  (frame absoluto do nariz do stack); CG passado a ele deve estar no mesmo
  frame.
