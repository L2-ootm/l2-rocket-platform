# Decision Log: L2 Organic Evolution

Crônica de testes, bugs e descobertas da transição de Otimização Paramétrica para Evolução Orgânica Baseada em AST.

## O Bug Inicial de Convergência Paramétrica
**Problema:** O otimizador `Nelder-Mead` no Python não conseguia fazer um foguete de fibra de vidro de 3.0 metros atingir 15km, porque o foguete era simplesmente pesado demais. O assistente humano precisou fazer um "tape fix", hackeando manualmente o tamanho do foguete para 2.0 metros no script, permitindo o solver achar a solução de carga útil.
**Decisão:** Não fazemos "tape fixes" manuais. O sistema precisa ser totalmente desamarrado de matrizes rígidas de variáveis. Criar a classe `ASTNode` (`rocket_ast.py`) para permitir que o solver altere o esqueleto do foguete (mutação tipológica, ex: tamanho dinâmico, estágios dinâmicos) sozinho, de zero.

## O Teste de Multithreading: Rust vs Java (JPype)
**Contexto:** O solver genético requer centenas de milhares de simulações. A engine em Rust (`l2_engine`) processa rápido (via `rayon`), mas no Python o `JPype` parecia agir sequencialmente. O usuário questionou "we are running the rust engine in a single thread of my cpu only? why?".
**Experimento:** 
- Foi desenvolvido o `parallel_evaluator.py` que invocava 16 threads no `concurrent.futures.ThreadPoolExecutor` usando `jpype.attachThreadToJVM()` e injetava os workers em instâncias de `OpenRocketInstance`.
- A engine Rust foi invocada no terminal `cargo run --release --bin evolve -- --mission ../missions/15k_precision.json --pop 2000 --gens 40`.
**Resultado Rust:** Sucesso total. Saturou a CPU perfeitamente, avaliando as 80.000 chamadas em 14.6s (`~137 sims/segundo`). 
**Resultado Java/Python:** Falha crítica. Deadlock após compilar os presets iniciais devido à natureza Single Thread / Singleton estático de projetos originados como aplicativos Desktop GUI (Java OpenRocket). O processo travou e precisou ser desativado pelo `manage_task`.
**Decisão Definitiva:** Python + JPype + Multithreading = Erro Crítico Não Solucionável. A arquitetura Dual-Engine foi finalizada: Rust faz todo o pipeline de evolução de exploração profunda, Java/JPype valida as top-X escolhas (as "Elite").

## Continuous Knowledge Graph (CKG)
**Desafio:** O Genetic Algorithm, ao tentar construir um foguete AST do zero, criará inúmeras monstruosidades injogáveis (foguetes abrindo paraquedas no motor, sem nariz, de massa infinita). Simular fisicamente na engine Rust custa ciclos de CPU (ainda que curtos).
**Ideia Original (Discussão):** Criar uma base de memória estrutural que penaliza o fit de foguetes sem simular se eles baterem com sub-grafos reconhecidos por serem mortais. 
**Design do Sistema:** Utilizar a filosofia de UI "Dark Prism" e "Topographic", o webUI para o CKG pode renderizar um "Force-Directed Graph" 3D para o cientista assistir à IA ligando motores, estágios e eventos. As linhas vermelhas mostram dead-ends estruturais (onde o foguete colapsa fisicamente), e as azuis ciano/obsidiana marcam combinações eficientes.

## Sessão 2026-07-04: self-contained, retirada do pipeline paramétrico, motor pool dinâmico

**Gatilho:** pedido para tornar `l2_engine` self-contained (sem depender de
`l2_engine_base/rocket-sim`, um repo externo que não vai para o repo
publicado) e para corrigir bugs documentados. Depois, pergunta direta do
usuário: "builder.rs tem valores hardcoded? nossa evolução é realmente
orgânica, com peças/motores reais do OpenRocket, não mágicos?"

**1. Port nativo de `rocket-sim`.** `l2_engine_base/rocket-sim` (2.769
linhas, MIT, ZenAlexa/rocket-sim) foi portado fisicamente para
`l2_engine/src/sim_core/` (não um wrapper — código movido, namespaces
reescritos). `l2_engine_base/` foi deletado. Verificado por: build limpo,
80 testes verdes, `evolve`/`ast_eval` rodando fisicamente igual antes/depois.

**2. Bug do fitment motor/airframe** (relatado em
`docs/organic_loop_report.md` #3): nada verificava diâmetro do motor vs
diâmetro do tubo antes de simular. Corrigido centralizado em
`mission_adapter.rs::build_mission` (cobre AST e o pipeline paramétrico que
ainda existia na época). Regressão: teste que reproduz o exploit exato do
report (N5800 em tubo de 36mm).

**3. Motor/airframe fitment REVELOU um bug de calibração real**: ao ativar
a checagem, o pipeline paramétrico (então ainda vivo) colapsou 100% —
`S_RADIUS`/`B_RADIUS` estavam EXATAMENTE no limite de clearance (1mm) ou
com clearance ZERO. Não era coincidência: eram constantes hardcoded, não
derivadas do motor real. Fix imediato: derivar radius+wall do diâmetro real
do motor (`.eng`) via `derive_radius_and_wall`. Isso já apontava para o
problema maior (item 5).

**4. Pergunta direta do usuário sobre hardcoding — investigação, não
suposição.** Em vez de responder "acho que sim, tá tudo dinâmico", foi
auditado: `builder.rs` (pipeline paramétrico) tinha radius hardcoded
(confirmado, já parcialmente mitigado no item 3); o pipeline ORGÂNICO
(`rocket_ast.py`/`ast.rs`) parecia limpo (radius genuinamente mutado por
`_jitter`) — mas a auditoria mais funda achou um bug MUITO pior: `ast.rs`'s
`motor_designation()` só reconhecia 3 substrings hardcoded (O8000/N5800/
M2245); qualquer outro `motor_index` (32 dos 34 motores reais de
`MOTOR_DATABASE`) virava um placeholder tipo `"motor_index_5"` que NUNCA
batia com nenhuma curva carregada. **Na prática, o GA orgânico só
conseguia avaliar 1 motor real (N5800) desde sempre.**

**Decisão:** retirar o pipeline paramétrico fixo por completo (não só
consertar os raios) — `evolve.rs`, `genome.rs`, `build_geometry` deletados;
`builder.rs` reduzido à matemática de CG/margem compartilhada. AST vira o
único caminho. Fix do motor pool: `rocket_ast.py` passa a emitir
`motor_designation` (a designação real resolvida de `MOTOR_DATABASE`)
junto com `motor_index` sempre; `ast.rs` exige essa string diretamente, sem
fallback por índice nem substring matching.

**5. Fonte de dados de motor — nunca de mão.** Ao expandir o pool de
motores utilizável, extraiu-se os dados reais direto do database SQLite
que o próprio OpenRocket 23.09 embarca
(`openrocket/core/.../datafiles/thrustcurves/initial_motors.db`, via
`extract_motors.py` novo) em vez de confiar nos `.eng` vendorizados à mão.
Isso pegou um erro real já existente no repo: `O8000.eng` tinha diâmetro
161mm hardcoded; o motor real (mesmíssima massa/comprimento/impulso,
conferido campo a campo) é **150mm**. `rocket_forge.py`'s `MOTOR_DATABASE`
também tinha comprimentos errados em alguns motores (K510, K1050W) —
corrigidos contra a mesma fonte.

**6. Designação de motor — uma string, três lugares, tem que bater.**
Descoberto que o `.ork` compilado precisa da designação EXATA que o
próprio OpenRocket resolve (para os 3 Cesaroni "grandes", isso inclui
prefixo de catálogo + sufixo `-P`, ex. `"20146N5800-P"` — comprovado
funcionando via `l2_hyper/generator.py`/missions JSON já em produção).
Simplificar para "N5800" (mais legível) teria quebrado a compilação do
`.ork` silenciosamente. Resolvido mantendo o nome de ARQUIVO curto (`.eng`
como label humano) mas o DESIGNATION dentro do header do arquivo = a
mesma string usada em `MOTOR_DATABASE` e no JSON da AST.

**7. Segundo hardcoding de topologia, achado ao consertar o primeiro.**
`stack_wet_cg` (usado por ambos os pipelines) posicionava a massa molhada
do motor usando um array `MOTOR_LENGTHS` de 3 elementos fixo — errado em
silêncio para qualquer foguete orgânico usando motor diferente dos 3
antigos. Corrigido: `ThrustCurve` ganhou `length_m` (do próprio `.eng`),
`stack_wet_cg` usa o comprimento real do motor daquele estágio.

**8. Bug de serialização JSON, achado ao verificar o fix end-to-end.**
`ast.rs` usava `f64::NEG_INFINITY` para o `min_static_margin` de candidatos
que falham — `serde_json` emite isso como `null` (JSON não tem Infinity),
e o parser Python (`organic_loop.py`) quebrava com `TypeError` assim que
candidatos reais (não só sempre o mesmo tipo de falha) começaram a
aparecer. Fix: sentinela finito (`-1.0e9`) em vez de infinito.

**Verificação end-to-end:** `organic_loop.py --evaluator rust` produzindo
elites com motores genuinamente variados (N2000W, M650W, I218R, L1500T
observados em runs de teste — não mais só N5800), e uma elite validada com
sucesso no OpenRocket real (`--validate-openrocket 1`, `status: success`).

**Lição:** uma pergunta cética e direta ("isso é real ou é mágica?") sobre
um sistema que "parecia funcionar" (rodava, produzia elites, sem crash)
revelou que o componente central (escolha de motor) estava, na prática,
morto há quem sabe quanto tempo — o sistema "funcionava" só porque sempre
convergia pro único motor que de fato resolvia. Nenhum teste unitário
pegou isso porque os testes também só usavam N5800/O8000. Teste de
integração real (rodar o loop completo e inspecionar QUAIS motores saíram
na elite) é o que expôs o problema — não iria aparecer em testes que
mockam ou fixam a entrada.

## Sessão 2026-07-23: cluster octaweb 3-main+1-retro (motivada pelo 839k não ser legal)

**Gatilho:** usuário reportou que `osifog_physical_839k_falcon.ork` (o
"authority artifact" salvo em `.planning/HANDOFF.json` até então) **não é
legal, é fisicamente impossível** — 3+1 motores por estágio mal
posicionados, sem centering rings, e um exploit de staging (todos os
motores disparando no launch, separação no descent). Instrução: "do it all,
we cant afford tape fixes" — os 4 itens escopados (topology.stage_count,
ballast, octaweb no campaign live, staging) deviam ser implementados de
verdade, não só um "tape fix" pontual.

**Arquitetura do octaweb final** (`rocket_ast.py::octaweb_motor_mounts` +
`_octaweb_circumscribing_rings_xml` + `octaweb_ballast_rods`): 3 motores
principais em `clusterconfiguration=3-ring` nativo do OpenRocket (não
posicionamento manual por instância) + 1 motor retro central em tubo
separado, todos tangentes, presos por 2 centering rings circunscritas por
estágio (uma forward, uma aft — não uma por tubo, não uma ao redor do
retro). Cada bug abaixo só foi confirmado via `orhelper.getComponentLocations()`
contra a JVM real do OpenRocket, nunca só por "a simulação não abortou" (ver
`feedback_verify_geometry_empirically` na memória) — screenshots reais do
usuário pegaram cada regressão visual.

**Bugs reais encontrados e corrigidos, em ordem:**
1. `<radialposition>`/`<clusterscale>` aplicados em dobro no mesmo
   `innertube` (offset manual + clustering nativo) — motores saíam do tubo.
2. `CenteringRing` com `radialposition` não-zero é **ignorado silenciosamente**
   pelo OpenRocket (renderiza em 0,0,0 sempre) — descoberto via experimento
   JVM isolado (`test_nested_ring.py`). Redesenhado como 2 rings
   circunscrevendo o cluster inteiro, raio calculado do envelope do cluster.
3. Ballast mal posicionado, duas vezes: primeiro no mesmo raio dos motores
   principais (zero espaço matematicamente), depois fora do envelope do
   cluster (atravessando o material sólido das centering rings recém-
   redesenhadas). Fix final: tangente ao motor retro central, raio
   encolhido numericamente até não colidir com os motores principais.
4. **`<radialdirection>`/`<clusterrotation>` são GRAUS, não radianos** — o
   compilador convertia valores em graus para radianos antes de escrever,
   então todo ângulo não-zero renderizava a ~1/57 do valor pretendido.
   Confirmado empiricamente (`atan2` da posição real via JVM) e contra os
   valores do próprio 839k (`-90.0`, `30.0`, `150.0` — inequivocamente graus).
5. **Rotação nativa de +90° do `clusterconfiguration=3-ring`** quando
   `clusterrotation=0`: mesmo com o bug de graus/radianos corrigido, o
   ballast ainda ficava 30° fora do centro de cada gap. Confirmado via
   experimento isolado variando `clusterrotation` (0/-90/90/30) e observando
   os ângulos reais. Documentado em
   `.planning/ultra/ULTRAREVIEW-octaweb-ballast-radialdirection-units.md`.

**Depois, mesma sessão: liberdade de material por peça.** Mount do motor
principal estava fixo em "kraft", rings fixos em "fiberglass" — corrigido
para escolha independente por peça (main mount / retro mount / rings) de
`MOUNT_MATERIAL_CHOICES`, refletindo no `mount_material_density` que o Rust
usa para a massa do ponto inerte. `OCTAWEB_BODY_RADIUS_RANGE_M` alargado de
0.15m para 0.20m (o maior motor legal da missão, L1500T, precisava do
envelope exatamente no teto antigo, sem margem). Ao testar isso sob stress
(2500 foguetes aleatórios), achado um bug pré-existente separado: o
alargamento de `BODY_TUBE` por estágio (correção de descontinuidade de
diâmetro) não redimensionava as fins daquele estágio — 27/2500 violações do
invariante `root >= 1.2*radius`, corrigido com re-escala proporcional.

## Sessão 2026-07-23/24 (continuação): campanha de teste revela que octaweb NUNCA rodou de verdade, e um bug de staging muito mais grave

**Gatilho:** "we may be ready to a next complex campaign launch, lets test
it and see how the rockets will turn out."

**1. `octaweb_probability` nunca foi ligado à missão real.** A missão
`missions/osifog_l3_precision.json` já declara
`topology.main_cluster: {"configuration": "3-ring"}` como intenção de
design (mesmo padrão de `topology.stage_count`), mas nada em
`organic_loop.py::run_generation` lia isso — `octaweb_probability` ficava
sempre no default `0.0` do `create_random_ast`. **Confirmado ao vivo: a
campanha `osifog_campaign_v7`, rodando havia 5.5 horas / 2440 gerações,
nunca gerou um único candidato octaweb.** Corrigido com
`_resolve_octaweb_probability(mission_data)`, mesmo padrão de
`_resolve_stage_range`.

**2. `OCTAWEB_CONVERT` era uma mutação morta.** Estava listada em
`_structural_mutation`'s `choices` mas sem nenhum `elif` correspondente —
1/6 das mutações estruturais eram um no-op silencioso. Implementada
(reaproveitando `octaweb_motor_mounts`/`octaweb_ballast_rods`, alargando o
raio do estágio, deixando o fix de re-escala de fins já existente cuidar da
proporcionalidade).

**3. Bug de crash ao vivo: `_select_motor_index` nunca foi importado em
`organic_loop.py`** (só o `_motor_index`, uma função diferente, estava
importado). O ramo `RETRO_MOTOR` de `_structural_mutation` chamava
`_select_motor_index` sem guarda nenhuma no call site (`mutate_ast` dentro
do loop de reprodução de `run_generation` roda sem try/except) — um
`NameError` real toda vez que essa mutação disparava com `retro_motor_pool`
definido. Quase certamente a causa dos 2 restarts do watchdog da v7 naquela
noite. Corrigido com o import.

**Verificado com uma campanha real (não só scripts isolados):** 48
população x 6 gerações contra a missão real: 0% -> 100% dos elites
genuinamente octaweb (antes: 0% em 2440 gerações). Candidato compilado via
pipeline completo (`sanitize_ast_for_openrocket` -> `ASTCompiler` ->
`validate_compiled_geometry`): zero violações. Carregado na JVM real do
OpenRocket sem erros de carga.

**4. `osifog_campaign_v7` reiniciada** (mata a árvore de processos inteira
com `taskkill /PID <watchdog> /T /F` — Windows/uv spawna pares shim+
interpretador real, o PID visível nem sempre é o PID que
`watchdog.json`/`campaign.lease.json` rastreiam; sempre matar a partir do
topo da árvore) com os mesmos parâmetros de lançamento (population=96,
elite-count=12, generations-per-cycle=5, super-speed, seed=314159,
max-hours=48, capturados via `Get-CimInstance Win32_Process` antes de
matar). Estado anterior (não-octaweb, 2440 gerações) arquivado, não
apagado, em `designs/osifog_level3/osifog_campaign_v7_pre_octaweb_2026-07-23/`
— para o resume-from-checkpoint automático de `organic_campaign.py` não
semear a nova campanha com uma população inteira da arquitetura errada.

**5. Bug muito mais grave, achado ao investigar "staging sequencial" a
pedido do usuário: motores de estágios superiores nunca disparavam.**
`normalize_stage_ignition_events` (chamada em TODO candidato, TODA geração,
via `normalize_ast`) reescrevia `ignition` em QUALQUER `MOTOR_MOUNT`
(ignorando `role`) só pela posição do estágio, e apagava `ignition_delay`
incondicionalmente. Consequência 1: motores retro (pouso/frenagem) do
estágio mais baixo viravam `ignition="automatic"` — disparavam no launch,
junto do motor de ascensão, em vez de perto do touchdown — e perdiam sua
variável de busca de timing (`ignition_delay`) toda geração. Consequência 2
(a mais grave): `ignition="burnout"` era escrito para o motor principal de
QUALQUER estágio que não fosse o mais baixo — confirmado via
`l2_engine/src/bin/ast_trace.rs` (trace direto da simulação Rust, não
suposição) que esse motor **nunca dispara em momento nenhum do voo
simulado**: `mission_adapter.rs` resolve `ignition_delay` de "burnout" como
`primary_burn_duration` (dos motores não-retro DO PRÓPRIO estágio) +
`mount.ignition_delay` — para o motor único e principal de um estágio, isso
é auto-referente (o motor "espera ele mesmo queimar" antes de acender, o
que nunca acontece). Forçado `ignition="automatic"` no mesmo candidato e o
motor disparou corretamente, no instante exato da ativação do estágio.
`"automatic"` é também o evento nativo do OpenRocket, ciente da posição do
estágio (launch pro estágio mais baixo, separação-do-estágio-abaixo pros
demais) — a suposição de que só "automatic" no mais baixo bastava estava
simplesmente errada. **Toda campanha 2+ estágios já rodada por este
pipeline pontuou candidatos com o motor do estágio superior contribuindo
zero para a trajetória simulada.** Corrigido em 3 lugares
(`create_random_ast`, `octaweb_motor_mounts`, `normalize_stage_ignition_events`)
para `ignition="automatic"` universal em motores principais; retro
motors exemptos da reescrita de `normalize_stage_ignition_events`.
Verificado via `ast_trace` (thrust liga/desliga na sequência certa, ambos
os motores) e via JVM real do OpenRocket.

**6. Liberdade de material em fins nunca existia de verdade.**
`_sanitize_fin` validava qualquer material, mas nada em `create_random_ast`
nem em `ASTNode.mutate()` jamais setava um — toda fin ficava presa no
fallback "fiberglass" pra sempre, ao contrário de nose/body que já tinham
liberdade real. Corrigido: material + thickness agora são livres na
criação e na mutação, mesmo pool completo de `MATERIALS` que nose/body já
usavam. `l2_engine/src/ast.rs` já resolvia material de fin por nome
(`material_density_checked`), sem exigir wiring novo no Rust.

**7. `osifog_campaign_v7` reiniciada de novo** (mesmo procedimento do item
4) especificamente pelo bug de staging (item 5) — grave demais pra deixar
rodando quebrado. Estado anterior arquivado em
`designs/osifog_level3/osifog_campaign_v7_pre_ignition_fix_2026-07-23/`.
Fix de material de fin (item 6) chegou depois desse restart; não reiniciada
de novo só por ele.

**Lição, de novo:** o padrão se repete — "roda sem crashar, produz elites"
não é prova de que o mecanismo central está fazendo o que deveria. O bug de
staging (item 5) é o exemplo mais caro da sessão: o pipeline "funcionava"
(gerava, simulava, exportava elites) há quem sabe quantas campanhas, mas o
motor do estágio superior nunca contribuiu em NENHUMA pontuação de foguete
2+ estágios. Só apareceu ao traçar a simulação Rust ponto a ponto
(`ast_trace`) em vez de confiar no status/score agregado.

**8. URGENT, achado nos últimos minutos via screenshot do usuário do
melhor candidato salvo:** `_falcon_cluster_geometry` (de `osifog_sweep.py`,
usada por `octaweb_motor_mounts`) produz `retro_sleeve_outer_radius_m`
absurdamente grande para pelo menos um par de motores real (main=H238T,
retro=F50T, visto no próprio best-candidate.json da campanha ao vivo):
`retro_sleeve_outer_radius_m=0.2997m`, `radial_offset_m=0.3154m` — maiores
que o próprio teto de `OCTAWEB_BODY_RADIUS_RANGE_M` (0.20m). Os motores
principais ficam fisicamente FORA do tubo. Nada pega isso hoje (a guarda de
overlap de `octaweb_motor_mounts` só rejeita motor-vs-motor, não o cluster
inteiro vs `body_radius_m`). Não investigado a fundo por falta de tempo —
prioridade máxima da próxima sessão, ver `.planning/HANDOFF.json`
`known_limitations_still_open` pro detalhe completo e sugestão de por onde
começar.

**Estado ao desligar (2026-07-24, ~03:30 UTC):** `osifog_campaign_v7`
rodando havia ~30min com todos os fixes, ainda sem candidato `status:
success` (melhor `min_static_margin` observado ~1.37 de 1.5 exigido,
convergindo). Checkpoints são atômicos (`atomic_json`) e o campaign auto-
resume de `organic_elite.json` — seguro desligar o computador, é só rodar o
mesmo comando do watchdog de novo (documentado em `HANDOFF.json`) pra
continuar de onde parou.

## Sessão 2026-07-24 — OSIFOG ruling received, major constraint corrections, geometry/motor/doc fixes

A full-day session (in English for this entry, given the volume of technical
detail) found and fixed a long chain of real bugs, then received a formal
OSIFOG ruling email that corrected two fundamental assumptions this
pipeline had been operating under since early sessions. Full blow-by-blow
detail is in `.planning/HANDOFF.json` (schema_version 3 through 9+) --
this entry is the permanent narrative summary.

**Bugs found and fixed (fixes_round_2 through fixes_round_6 in HANDOFF.json):**
octaweb main-mount cage geometry frozen/stale across generations (never
re-tightened when body radius changed) -- fixed via an unconditional
repair pass in `sanitize_ast_for_openrocket`; duplicate overlapping fin
sets from a missing guard in `_structural_mutation`'s FIN_SET branch --
fixed guard + active de-duplication; a flat-zero-gradient bug in
`enforce_motor_mount_clearance`/`enforce_motor_adequacy` (no closeness
ratio embedded, so the GA had zero signal to climb once these became the
dominant blocking constraints) -- fixed via `violation()`; body-tube wall
thickness never wired to mutation (dead parameter since inception) --
fixed; 12 of 38 motors in `MOTOR_DATABASE` missing a real OpenRocket
digest (including L1000/L1150 whose designation strings don't match ANY
real catalog entry) -- fixed by pinning real digests found via direct JVM
query, confirmed digest-alone resolution works regardless of designation
text; a fin cross-section value ("double-wedge") that can never match any
real OpenRocket enum, producing a genuine load warning -- fixed by mapping
to "square" in the XML compiler only (Rust's own richer physics model
unaffected); L2200G's length was wrong in both `rocket_forge.py` and its
own `.eng` file (665mm vs the real 681mm, confirmed via matching mass
properties) -- fixed both; octaweb body radius drawn independently of the
chosen motor cage's actual size, leaving oversized bodies with visible
gaps -- fixed with a post-generation tightening step in both
`create_random_ast` and the `OCTAWEB_CONVERT` mutation.

**OSIFOG ruling email (2026-07-24), two corrections that override prior
assumptions everywhere in this repo:**
1. There is NO minimum static margin requirement. Confirmed by reading
   both organizer PDFs in full (`OSIFOG_Nivel3_ProjetoFalcon.pdf`,
   `OSIFOG_Missao_Secreta_2026.pdf`) -- neither specifies a numeric
   minimum. The only real rule is "manter apenas estabilidade estática"
   (maintain ONLY static, non-active stability). `missions/
   osifog_l3_precision.json`'s `min_static_margin: 1.5` had been a
   self-invented hard rejection gate blocking essentially every candidate
   this pipeline had ever scored -- corrected to 0.1.
2. "3+1" is this team's own design choice (confirmed with the user,
   inspired by the real Falcon 9's 3-engine boostback/1-engine landing
   burn phases), not a rule -- it appears nowhere in either PDF. No
   architecture change needed; octaweb remains the active pipeline.

**Geometry insight from user-provided real OpenRocket reference designs**
(`designs/osifog_level3/clusterexample.ork`, `octawebexample-with
parachute.ork`, `2stagehighpower.ork`): centering rings auto-size to the
body/center-tube boundary and are NOT individually tangent to each
clustered motor (visibly pass through the motor cluster in the reference).
This meant our exact-zero-margin tangent cage formula had no physical
justification and was the actual cause of a persistent
`motor_mount_collision` near-miss (every elite, hundreds of generations,
within 2-3% of the threshold) for large-main/small-retro motor pairs,
where "tangent to retro sleeve" and "clear of neighboring main motor"
nearly coincide at the boundary. Fixed by taking whichever constraint
needs more room plus a real 5mm safety margin.

**Interstage coupler**: confirmed real (never generated, `<tubecoupler>`
appears nowhere in `ASTCompiler`) but deliberately deferred, not fixed --
neither PDF requires one, it doesn't affect simulation correctness
(OpenRocket separates stages via `<separationevent>` independent of any
physical coupler geometry), and the octaweb retro-motor cage currently
occupies nearly the full axial length of each stage, leaving no
uncontested room for one without a larger, riskier redesign this deadline
doesn't have time for.

**Doc cleanup**: several docs describing superseded architectures/wrong
constraints (external PodSet, the old 1.5 margin, an old "Starship
forward-flap" description) were given explicit SUPERSEDED banners pointing
back to `.planning/HANDOFF.json` rather than rewritten in place --
`OSIFOG/OSIFOG_Level3_Brief.md`, `OSIFOG/MISSION_STATUS.md`,
`OSIFOG/OSIFOG_Level3_PodSet_Findings.md`,
`.planning/PODSET-EXTERNAL-3PLUS1-ARCHITECTURE.md`, root `handoff.md`.
