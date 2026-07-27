> **[RETIRADA 2026-07-04]** O pipeline paramétrico de 3 estágios fixos
> descrito neste documento (`genome.rs`, `l2_engine/src/bin/evolve.rs`,
> template fixo em `builder.rs`) foi deletado do código e substituído pelo
> pipeline orgânico baseado em AST (`.agents/skills/l2-organic-evolution/`),
> que resolve a mesma classe de missão com topologia livre e motores/
> materiais reais do database do próprio OpenRocket, sem template fixo para
> divergir. Documento mantido como registro histórico do resultado e da
> engenharia original; não implemente contra os comandos/binários citados
> abaixo (não existem mais).

# Hyper Evolution Pipeline — Rust (rápido) → OpenRocket (oficial)

Documento de engenharia da missão 100 km / Mach 6 (2026-07-01) e plano de
implementação do pipeline evolutivo em duas etapas no framework L2.

Resultado alcançado: **apogeu 236.43 km, Mach 6.72** (validação oficial
OpenRocket 23.09), design 3 estágios criado do zero, salvo em
`designs/optimized/L2_Hyper_100K_M6.ork`. Gerador e otimizador em
`hyper_100k_pipeline.py`.

---

## 1. Como foi feito (registro da sessão)

### 1.1 Desbloqueio do load (pré-requisito)

O `orhelper.load_doc()` falhava com `IllegalArgumentException` oculta em
`GeneralRocketLoader.java:79`. Causa raiz (duas condições, ambas obrigatórias):

1. Todo `configid` (em `<motorconfiguration>` e `<motor>`) deve ser **UUID
   válido**. String arbitrária vira `ERROR_FCID` interno.
2. Toda `<simulation>` deve ter `<conditions><configid>MESMO-UUID</configid>`.
   Simulação sem configid também vincula ao error id e derruba o load inteiro.

Empacotamento ZIP (`rocket.ork` interno) e round-trip do ElementTree nunca
foram o problema. Regra: **um UUID compartilhado atravessa motorconfiguration,
motor e conditions**.

### 1.2 Iterações de arquitetura do foguete

| Iteração | Stack | Resultado | Lição |
|---|---|---|---|
| v1 | O8000 → N5800, aletas pequenas | 4.9 km, Mach 2.3 | Sustainer instável = "estágio morto": vmax travado no burnout do estágio anterior |
| v2 | + lastro de nariz + aletas grandes | 125.6 km, Mach 5.55 | 2 estágios saturam em Mach ~5.5; apogeu ok, Mach não |
| v3 | + kick M2245 75mm (3 estágios) | 21 km (falha) → **236 km, Mach 6.72** | Kick precisa ser generosamente estável E acender 3-11s após separação |

### 1.3 Assinaturas de diagnóstico (usar sempre)

- **Estágio morto**: vmax idêntico ao burnout do estágio anterior em todas as
  variações de delay → o motor de cima não está contribuindo (instabilidade
  na ignição, ignição pós-apogeu, ou motor não montado).
- **Ignição pós-apogeu**: evento IGNITION com t > t(APOGEE) no branch 0.
- **Tumble**: evento TUMBLE logo após IGNITION.
- Ler eventos com `ev.getType().name()` (enum name, ex.: `IGNITION`), nunca
  `str()` — o toString é localizado (pt-BR) e quebra matching de string.
- Exceções Java via JPype: extrair com `StringWriter` + `printStackTrace`;
  o `str()` da exceção falha e esconde a mensagem real.

---

## 2. Estratégias e princípios (o que generaliza)

1. **Proxy barato, ground truth caro.** A engine Rust (HyperReal, ~100+
   sims/s com rayon) explora; o OpenRocket (~2-6 s/sim + ~35 s de boot de JVM)
   arbitra. Nunca inverter os papéis.
2. **Um único JVM para todos os candidatos.** Boot do OpenRocket + motor DB
   custa ~35 s; cada avaliação dentro da mesma `OpenRocketInstance` custa
   segundos. O loop inteiro roda dentro de um `with OpenRocketInstance(...)`.
3. **Genoma único compartilhado.** O design é um dicionário plano de ~13
   parâmetros contínuos (ver §3.2). Os dois mundos (Rust e Python) constroem o
   foguete deterministicamente a partir do mesmo genoma — é isso que permite
   transferir elites entre etapas.
4. **Estabilidade é constraint de primeira classe, não pós-filtro.** A
   fitness penaliza tumble/ignição-tardia ×0.05. Sem isso o otimizador
   converge para foguetes de mínimo arrasto que tombam (aconteceu na v1 e v3).
5. **Física orienta a busca.** As alavancas dominantes, em ordem: (a) delays
   de ignição dos estágios superiores (trocam Mach por apogeu), (b) massa
   morta do estágio superior (cada kg ≈ ±200 m/s de Δv), (c) geometria de
   aletas (estabilidade vs arrasto). Varra as alavancas dominantes primeiro.
6. **Fitness da missão** (idêntica nas duas etapas):

   ```
   score = min(apogee/100_000, 1) + min(mach/6, 1) + apogee/1_000_000
   se tumble ou ignição_pós_apogeu: score *= 0.05
   ```

   Os dois primeiros termos saturam nos alvos (100 km, Mach 6); o terceiro
   desempata acima do alvo sem dominar.

---

## 3. Arquitetura em etapas para o framework

```
                ┌────────────────────────────┐      ┌─────────────────────────────┐
 genome bounds  │ ETAPA 1 — l2_engine (Rust) │      │ ETAPA 2 — OpenRocket (Java) │
 (JSON)  ─────► │ GA: pop 300, 30-50 gerações│ ───► │ GA: pop 16-24, 3-5 gerações │ ──► .ork final
                │ ~10k-15k avaliações, seg.  │elite │ seeded pela elite Rust      │     + relatório
                │ fitness proxy + margem est.│.json │ mesma fitness, física ofic. │
                └────────────────────────────┘      └─────────────────────────────┘
```

Alinhamento com L2 MIND: o espaço agora é contínuo de 13 dimensões (≫100k
combinações), o que ativa a decisão travada de migrar de brute force para GA.

### 3.1 Contrato entre etapas: `elite.json`

```json
{
  "generated_by": "l2_engine evolve v1",
  "fitness_def": "min(apogee/1e5,1)+min(mach/6,1)+apogee/1e6",
  "elite": [
    {
      "genome": { "k_nose_len": 0.45, "k_ballast": 1.1, "...": 0 },
      "rust_apogee_m": 231000.0,
      "rust_mach": 6.8,
      "rust_static_margin_min": 1.9
    }
  ]
}
```

### 3.2 Genoma (13 genes contínuos + bounds)

| Gene | Bounds | Descrição |
|---|---|---|
| `k_nose_len` | 0.35–0.85 | comprimento do nariz von Kármán [m] |
| `k_ballast` | 0.0–2.0 | lastro de nariz do kick [kg] |
| `k_span` / `k_root` | 0.05–0.13 / 0.12–0.24 | aletas do kick [m] |
| `k_delay` | 1–14 | ignição do kick após burnout do sustainer [s] |
| `s_body_len` | 1.28–1.60 | corpo do sustainer [m] |
| `s_span` / `s_root` | 0.08–0.14 / 0.17–0.32 | aletas do sustainer [m] |
| `s_delay` | 8–28 | ignição do sustainer após burnout do booster [s] |
| `b_span` / `b_root` | 0.09–0.15 / 0.20–0.32 | aletas do booster [m] |
| `sep_delay` | 0.3–1.5 | separação após burnout [s] |
| `payload` | fixo 0.3 | aviônica [kg] |

Motores fixos nesta missão (genes categóricos ficam para v2): O8000 → N5800 →
M2245. Derivados (tip = 0.35·root, sweep = 0.70·root) ficam no gerador, não no
genoma.

---

## 4. Etapa 1 — implementação no `l2_engine`

Estado atual: `src/bin/optimize.rs` faz random search (500 amostras) com
multiplicadores nose/fin/body sobre um .ork template single-stage, motor
N4800T hardcoded. Mudanças, em ordem:

1. **`DesignGenome` (serde)** em `src/genome.rs`: os 13 campos de §3.2 +
   bounds + `fn clamp(&mut self)`. Serializável para `elite.json`.
2. **Construção paramétrica 3-stage**: `fn build_geometry(genome) ->
   RocketGeometry` montando as `StageGeometry` diretamente (nose, tubos,
   finsets por pontos, innertubes, massas), sem parsear .ork template.
   `geometry.rs` já tem `SeparationConfig` — usar burnout+delay por estágio.
3. **Motores**: baixar as curvas RASP `.eng` do thrustcurve.org (Cesaroni
   O8000, N5800, M2245) para `l2_engine/motors/` e carregar com o
   `motor_db::parse_eng` existente. Verificar massa/dimensões contra o
   database do OR (valores em §1 do histórico: 32.67 / 14.83 / 8.18 kg).
4. **Staging na `mission_adapter`**: eventos de separação (burnout+`sep_delay`)
   e ignição (burnout do estágio anterior + `s_delay`/`k_delay`). Se o
   rocket-sim não suportar multi-burn nativo, encadear três simulações com
   handoff de estado (posição/velocidade/massa) — aceitável para proxy.
5. **Margem estática como proxy de tumble** (crítico): o rocket-sim não
   simula tumble; sem isso a elite Rust degenera em foguetes instáveis que o
   OpenRocket reprova (modo de falha observado na v1/v3). Usar o
   `barrowman.rs` para computar CP/CG **por fase de voo** (stack completo,
   sustainer+kick, kick isolado) e aplicar na fitness:
   `se margem_min < 1.5 cal → score *= 0.05`.
6. **GA real** em `src/bin/evolve.rs` (substitui o random search):
   - população 300, avaliação paralela com rayon (o `into_par_iter` atual já
     é o padrão);
   - seleção por torneio k=3; elitismo 5%;
   - crossover BLX-α (α=0.3) gene a gene; mutação gaussiana por gene
     (σ = 8% do range, taxa 0.15), clamp nos bounds;
   - 30-50 gerações com early-stop se o melhor score estagnar 8 gerações;
   - saída: top-16 → `elite.json` (formato §3.1).
   - CLI: `cargo run --release --bin evolve -- --pop 300 --gens 40 --out elite.json`

Critério de aceite da etapa: `elite.json` com ≥16 genomas, todos com
`rust_static_margin_min ≥ 1.5` e apogeu proxy > 100 km.

## 5. Etapa 2 — polish no OpenRocket (mesmo GA)

Refatorar `hyper_100k_pipeline.py` (o gerador `build_rocket_xml` e o
`evaluate` já estão isolados; trocar as fases fixas por um modo GA):

1. **Operadores idênticos aos do Rust** (torneio k=3, BLX-α 0.3, mutação
   gaussiana σ=8%/taxa 0.15, elitismo) — portar as mesmas fórmulas para que a
   dinâmica evolutiva seja a mesma e resultados sejam comparáveis.
2. **População inicial = elite do Rust**: 16 genomas de `elite.json` +
   4-8 mutantes deles (diversidade local). Nada de população aleatória — o
   Rust já pagou esse custo.
3. **Orçamento pequeno**: população 16-24, 3-5 gerações ≈ 60-110 sims ≈
   5-12 min dentro de um único JVM. O OR só refina delays/aletas ao redor do
   ótimo do proxy.
4. **Fitness idêntica** (§2.6) com os detectores reais de tumble/late-ignition
   por eventos de voo (branch 0, `getType().name()`).
5. **Saída**: melhor .ork salvo + revalidação final + tabela das gerações.
   - CLI alvo: `python hyper_100k_pipeline.py --seed elite.json --pop 20 --gens 4`

### 5.1 Calibração do proxy (fecha o loop)

A cada rodada, salvar os pares (apogeu_rust, apogeu_or) e (mach_rust, mach_or)
dos genomas da elite. Se o viés sistemático passar de ~15%, ajustar o
HyperReal (é a continuação natural do trabalho de residual drag gap da fase
1.2). Proxy não precisa ser exato — precisa **ordenar** candidatos na mesma
ordem que o OR.

## 6. Ordem de implementação sugerida

| # | Tarefa | Onde | Esforço |
|---|---|---|---|
| 1 | `DesignGenome` + bounds + serde | `l2_engine/src/genome.rs` | S |
| 2 | Curvas .eng O8000/N5800/M2245 | `l2_engine/motors/` | S |
| 3 | `build_geometry(genome)` 3-stage | `geometry.rs`/novo | M |
| 4 | Staging (separação/ignição) na mission | `mission_adapter.rs` | M-L |
| 5 | Margem estática por fase na fitness | `barrowman.rs` + evolve | M |
| 6 | `bin/evolve.rs` (GA + rayon + elite.json) | `l2_engine/src/bin/` | M |
| 7 | Modo GA + `--seed elite.json` no Python | `hyper_100k_pipeline.py` | S-M |
| 8 | Log de calibração proxy↔OR | ambos | S |

Riscos conhecidos: (4) é o maior — se o rocket-sim resistir a multi-burn,
usar o encadeamento de 3 simulações; (5) é obrigatório antes de (6) — GA sem
constraint de estabilidade produz elite inútil (evidência empírica nesta
sessão, não hipótese).

---

## 7. Sistema modular implementado (`l2_hyper/`) — Etapa 2 pronta

A Etapa 2 (OpenRocket + GA) já está implementada como pacote modular
orientado a missão. Qualquer missão nova = um arquivo JSON, zero código.

```
l2_hyper/
  mission.py     # MissionSpec: carrega JSON, compila fitness dos objectives
  genome.py      # genoma dinâmico derivado do stack + operadores GA (referência p/ port Rust)
  generator.py   # gerador .ork N-estágios from scratch (regras 23.09 embutidas)
  orkit.py       # sessão single-JVM, resolução de motores c/ digest, avaliação c/ telemetria
  ga.py          # loop GA mission-agnostic (torneio k=3, BLX-0.3, mutação gaussiana)
  run_mission.py # CLI
missions/
  karman_m6.json # a missão 100km/M6 como spec declarativa (com seed vencedor)
```

Uso:

```bash
python -m l2_hyper.run_mission missions/karman_m6.json --validate      # valida seed, salva .ork
python -m l2_hyper.run_mission missions/karman_m6.json --pop 16 --gens 4
python -m l2_hyper.run_mission missions/X.json --seed-file elite.json  # consome elite do Rust
```

Pontos de modularidade:
- **Objectives declarativos** (`atleast`/`atmost`/`target`/`maximize`/`minimize`
  sobre apogee/mach/vmax/flight_time) — a fitness é compilada da missão;
  missões "estranhas" (ex.: apogeu EXATO 83456 m = `target`) não exigem código.
- **Stack declarativo**: N estágios, cada um com motor (resolvido no database
  do OR com digest, cache em `motors_cache.json`) e raio de corpo. O genoma e
  seus bounds são derivados do stack (bounds escalam com o raio).
- **Warnings corrigidos no gerador** (v. sessão 2026-07-01): `<outerradius>`
  em innertube (não `<radius>`), `<digest>` no motor (elimina "chosen
  arbitrarily"), drogue no estágio superior com deploy no apogeu (elimina o
  crítico "No recovery device"). Restam apenas os avisos inerentes:
  precisão supersônica do Barrowman e "open forward airframe" dos interstages
  (informacional, esperado em multi-estágio com transição exposta).
- **`--seed-file elite.json`** já aceita o contrato §3.1 — quando o bin
  `evolve` do Rust existir, o handoff é plug-and-play.

Verificação (2026-07-01): `--validate` = 211.7 km / Mach 6.37 TARGETS MET
(paraquedas custou ~25 km vs versão sem recovery); GA smoke pop 8 × 2 gerações
evoluiu para 213.6 km / Mach 6.40, mutantes instáveis penalizados
corretamente. `hyper_100k_pipeline.py` fica como artefato legado da sessão;
novos trabalhos usam `l2_hyper`.

---

## 8. Contrato de estabilidade estática (auditoria GUI 2026-07-01)

A auditoria do design na GUI expôs margem estática **negativa no liftoff**
(-0.164 cal) que a simulação mascarava (TWR 13 + rail de 15 m voam torto e
"dão certo"). Correções no sistema:

1. **Margem estática por fase de voo é métrica de primeira classe** em
   `orkit.static_margins()`: para N estágios, fase p = estágios 0..N-1-p
   ativos (stack completo → ... → estágio de topo sozinho), cada fase avaliada
   em Mach representativo (`mission.stability.phase_machs`, default
   `[0.3, 2.0, 3.0]`), via os mesmos `BarrowmanCalculator` +
   `MassCalculator.calculateLaunch` da GUI.
2. **Constraint declarativo**: `constraints.min_static_margin` (default 1.5
   cal) com penalidade graduada na fitness — designs levemente abaixo mantêm
   gradiente para o GA voltar à estabilidade, em vez de um penhasco de score.
   `targets_met` também exige a margem: sem estabilidade não há missão
   cumprida.
3. **Viés entre versões (medido)**: no MESMO design, massa idêntica ao grama
   (64564 g) e CP idêntico (284 cm), mas o CG carregado difere ~9 cm entre o
   23.09 headless (CG 277.9 → +0.39 cal) e a GUI 24.12 do usuário (CG 287 →
   -0.16 cal). Por isso o default é 1.5 cal, absorvendo o viés de ~0.55 cal
   com folga. Se a engine headless migrar para o jar 24.12 (pacotes renomeados
   `info.openrocket.*`; orhelper precisa de adaptação), recalibrar.
4. **Fault isolation no GA**: exceção em um candidato vira score `-inf` e o
   loop continua (idempotente: mesma missão + mesma seed RNG = mesma
   evolução; única fonte de variação restante é a turbulência estocástica do
   próprio OR, ~±1% no apogeu).

Nota sobre nomes de motor na GUI: "9977M2245-P-P" não é defeito — "9977" é o
impulso total em N·s e o primeiro "-P" faz parte da designação oficial CTI no
database; o segundo "-P" é o sufixo de delay (plugged) que a GUI anexa a
qualquer motor sem carga de ejeção. O `<digest>` gravado pelo gerador garante
que a variante exata do database é a carregada.

## 9. Etapa 1 implementada — `l2_engine` evolve (2026-07-01)

A engine Rust rígida está operacional. **12.000 avaliações em ~100 s
(~110 sims/s)** — três ordens de magnitude acima do OpenRocket.

```
cargo run --release --bin evolve -- --pop 300 --gens 40 --out elite.json
cargo run --release --bin evolve -- --probe elite.json   # margens por fase p/ calibração
python -m l2_hyper.run_mission missions/karman_m6.json --seed-file elite.json --pop 12 --gens 3
```

Componentes:
- `src/genome.rs` — `DesignGenome` com serde renames para o contrato Python
  (`s0_*` = kick/topo); operadores GA (BLX-0.3, mutação gaussiana Box-Muller,
  clamp) espelhando `l2_hyper/genome.py`; determinístico via `StdRng` seeded.
- `src/builder.rs` — `build_geometry(genome)` 3 estágios paramétrico
  espelhando o gerador Python (mesmos raios, interstages, aletas, paredes);
  offsets axiais de estágio corretos p/ o frame absoluto do barrowman;
  `stack_wet_cg` + `static_margins` públicos.
- `motors/*.eng` — curvas O8000/N5800/M2245 exportadas do database do
  próprio OpenRocket 23.09 (consistência total entre etapas).
- `src/bin/evolve.rs` — GA (torneio k=3, elitismo 5%, early-stop 8 gens),
  avaliação paralela rayon, exporta `elite.json` (contrato §3.1).
- Fix em `mission_adapter::build_mission`: CG convertido para o frame
  absoluto antes do `compute_aero` (no-op para geometrias legadas).

Semânticas críticas descobertas na integração (gravadas em `builder.rs`):
- `ejection_charge_delay` é o que vira `separation_coast` no rocket-sim; os
  delays do genoma são referenciados ao burnout (convenção OR), o rocket-sim
  referencia à separação → `ignition_delay = delay - sep_delay`.
- O adapter coloca o motor no PONTO MÉDIO do primeiro bodytube — o tubo
  principal deve vir primeiro no vec; para CG de margem, o motor é posto na
  posição real (ré do tubo).
- Sem paraquedas no proxy (o rocket-sim abriria logo após o burnout do
  último estágio e mataria o apogeu); massa do drogue vai no ballast.

### 9.1 Calibração proxy↔OR (medida, 2026-07-01)

Desempenho correlaciona excelentemente: apogeu Rust +12%, Mach +5% vs OR no
mesmo genoma. Margens estáticas divergem por fase (rust → or):

| Fase | Ponto A | Ponto B | Conclusão |
|---|---|---|---|
| liftoff | 1.31 → 2.47 | 1.29 → 2.24 | Rust pessimista ~1 cal |
| mid | 2.96 → 1.36 | 5.07 → 2.63 | Rust otimista ~2x |
| kick | 2.25 → **-2.19** | 4.60 → 1.60 | Rust MUITO otimista; fit instável |

Mitigação atual: `REQ_MARGINS = [0.5, 3.2, 4.6]` por fase no `evolve.rs`
(fit linear de 2 pontos para OR ≥ 1.5 cal). **Limite conhecido**: o modelo do
kick isolado (nariz+tubo+aletas pequenas) diverge estruturalmente — elites
Rust podem chegar com kick instável no OR (-1.4 cal observado). O failsafe é
por design: a Etapa 2 mede margens reais e evolui a correção. Próximo passo
de engenharia: portar o CNα de aletas/corpo do OR para a fase bare-kick do
barrowman.rs, e alimentar o fit com os pares (rust, or) de cada polish
(log de calibração §5.1) até o fit estabilizar.

### 9.2 Loop completo verificado (2026-07-01)

Rust evolve (300×40, 100 s) → `elite.json` (16 genomas com filtro de
diversidade) → polish OR (pop 14 × 4 gerações, seeds = elite Rust + seed
estável da missão, cap de seeds em pop/2) → **TARGETS MET: 201.4 km,
Mach 6.22, margens [+2.70, +2.80, +1.56] cal**, sem tumble, salvo em
`designs/optimized/L2_Hyper_100K_M6.ork`.

Duas lições de antifragilidade que viraram código nesta verificação:
1. **Elite homogênea é veneno para o polish**: 16 clones da mesma bacia +
   pop pequena = seeds ocupam todos os slots e o GA não escapa (1º polish
   ficou preso em score 0.039). Correções: filtro de diversidade por
   distância normalizada no export do Rust, e `run_mission` mescla
   seed-file + seeds da missão limitando a pop/2 — os slots restantes são
   mutantes fortes e aleatórios.
2. O melhor design final é um HÍBRIDO real: delays e aletas de
   booster/sustainer vindos da elite Rust, kick estabilizado (root 0.26,
   ballast 0.92) vindo do atrator da missão via crossover.

Suíte de testes do crate: 32 passing (inclui o teste de validação <2% vs
OpenRocket do veículo de referência; corrigidos drifts de assinatura
pré-existentes em validation.rs, barrowman.rs e optimize.rs).

## 10. Estado-alvo do sistema (visão)

O objetivo final permanece: **engine Rust rígida** para criação e evolução
exponencial de foguetes a partir de metas específicas (milhares de
avaliações/s, GA agressivo, constraint de margem estática nativo) → exportar
elite → **desligar o Rust** → `l2_hyper` faz o polish fino dos goals exatos
dentro do OpenRocket, à prova de falhas (fault isolation por candidato),
idempotente (seeds determinísticas, cache de motores) e antifrágil
(estabilidade como constraint, buffer para viés entre versões, penalidades
graduadas). A Etapa 2 está operacional; a Etapa 1 segue o plano §4.
