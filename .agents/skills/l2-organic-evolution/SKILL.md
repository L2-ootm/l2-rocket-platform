---
name: l2-organic-evolution
description: Framework padrão do L2-OSIFOG para Geração de Topologia via Abstract Syntax Trees (AST) e intuição guiada por Continuous Knowledge Graph (CKG). Use para evoluir sistemas a partir do zero sem hardcoding estrutural, resolvendo gargalos de física dinamicamente através de mutações genéticas, em vez de tweaks numéricos em templates fixos.
---

# L2 Organic Evolution — Framework Operacional

Pipeline genético que constrói e avalia topologias físicas do absoluto zero (ex: foguetes) sem definir número de estágios, materiais, motores ou geometria antecipadamente. Baseado em genes instrucionais (AST).

```
[AST Generator] ─► [Rust Physics Evaluator] ─► [Continuous Knowledge Graph] ─► [Java Validation Elite]
rocket_ast.py      l2_engine/bin/ast_eval     Armazena hashes de falhas        l2_hyper (OpenRocket)
(Estrutura Zero)   (JSON batch via Rayon)     (CKG - Sub-graph penalization)   (Seqüencial, não-JPype-threads)
```

`organic_loop.py` é o orquestrador: gera/mutação população em Python, chama
`ast_eval` (subprocess, um batch JSON por geração) para o scoring físico
rápido, aplica o CKG, e opcionalmente valida a elite no OpenRocket real
(`--validate-openrocket N`). **`l2_engine` é self-contained** desde
2026-07-04 — `rocket-sim` foi portado nativamente para
`l2_engine/src/sim_core/`, sem dependência de repo externo.

**[2026-07-04] Esta skill é agora o único caminho ativo.** O pipeline
paramétrico de 3 estágios fixos (`l2-hyper-evolution`, `l2_engine evolve`,
`DesignGenome`) foi retirado — ver
[l2-hyper-evolution/SKILL.md](../l2-hyper-evolution/SKILL.md) para o porquê.
`builder.rs` hoje contém só a matemática de CG/margem estática compartilhada
(`stack_wet_cg`, `static_margins`), sem template fixo nenhum.

**Lei fundamental: O modelo não faz "tape fixes".** Se a física bater no limite (ex: foguete pesado demais para chegar a 15k com fiberglass e 3m), o algoritmo não pode ter seu template adulterado por humanos para 2m. A topologia deve evoluir organicamente a solução (ex: inserindo um novo Node de motor ou mutando o material da AST).

## Comandos padrão

```bash
# Geração inicial e teste de compilação da AST (sanity check isolado)
python rocket_ast.py

# Loop orgânico completo: gera população, avalia via ast_eval (Rust), aplica CKG, exporta elite
python organic_loop.py --evaluator rust --population 300 --generations 40 --target-apogee 100000 --seed 42

# Loop + validação da elite no OpenRocket real (JVM única, reutilizada por todos os candidatos)
python organic_loop.py --evaluator rust --population 300 --generations 40 --target-apogee 100000 --validate-openrocket 6

# Missão orgânica de precisão 16 km, limitada a Mach 3, com calibração no loop
python organic_loop.py --evaluator rust --physics openrocket --mission missions/precision_16k_m3_organic.json --population 300 --generations 40 --elite-count 8 --validate-openrocket 8 --calibrate-every 5 --polish --out designs/organic_16k_m3

# Validar/polir um arquivo de elites já salvo contra OpenRocket autoridade
python tools/run_polisher.py --elite designs/organic_16k_m3_longburn/organic_elite.json --mission missions/precision_16k_m3_organic.json --out designs/organic_16k_m3_longburn

# Avaliação em batch direta (o que organic_loop.py chama internamente por geração)
cd l2_engine && cargo run --release --bin ast_eval -- --input batch.json   # JSON in, JSON out

# Regenerar/estender o pool de motores reais direto do database do OpenRocket
python extract_motors.py   # lê openrocket/.../initial_motors.db, escreve l2_engine/motors/*.eng

# (NOTA: OpenRocket/JPype Multithreading é DEPRECATED devido a Java Singletons deadlocks)
```

## Workflow padrão para Evolução Orgânica

1. **Geração do Genoma AST**: Instancie a semente zero usando `rocket_ast.py`. A semente contém uma cadeia linear de blocos `ASTNode` (`STAGE`, `NOSE_CONE`, `BODY_TUBE`, `MOTOR_MOUNT`).
2. **Mutação Estrutural vs Paramétrica**:
   - *Estrutural*: O algoritmo injeta ou remove nós (ex: adicionar `PAYLOAD`, remover um `STAGE`).
   - *Paramétrica*: O algoritmo altera propriedades de um nó (ex: `sweep` de 45 para 30).
3. **Avaliação Inicial Rápida (Rust)**: O genoma AST é serializado como JSON (`AstEvalBatch`) e avaliado em lote pelo binário `ast_eval` via `rayon` (capping de threads recomendado para não freezar a CPU host). Contrato JSON-in/JSON-out por subprocess — mesma forma que um futuro avaliador GPU (`docs/l2_gpu_engine.md`) precisaria ter para ser um drop-in.
4. **Hashing no CKG**: Quando um genoma é avaliado (sucesso ou falha crítica), seus sub-grafos interativos (ex: `[Motor:N5800] -> [Delay:0] -> [Deploy:Ejection]`) são cacheados e penalizados se falharem (evasão orgânica de falhas conhecidas).
5. **Validação Elite**: As elites são exportadas para `.ork` (`rocket_ast.ASTCompiler`) e, com `--validate-openrocket N`, rodam no OpenRocket Java real (uma única JVM reutilizada por todos os N candidatos — nunca uma por candidato, ver princípio 6) para extrair apogeu/Mach reais e estabilidade Barrowman.
6. **Calibração durante evolução**: `--calibrate-every N` mede deltas de apogeu/Mach OR↔Rust do líder e os grava por assinatura topológica no CKG. Use em missões de precisão; calibração orienta o proxy, mas não relaxa nenhum gate do OpenRocket.
7. **Margem por fase no mesmo Mach da missão**: `stability.phase_machs` deve viajar até o batch Rust (`phase_machs`) e até o validador OpenRocket. O Rust não pode aceitar margem estática calculada só em Mach `0.3` quando a missão valida fases em Mach `2`, `5` ou `10`.
8. **Memória contextual de autoridade**: falhas OpenRocket devem ser gravadas como contexto de estágio/pair de estágios (`record_authority`), não como punição genérica a `STAGE` ou `CLOSE_BODY`. O CKG deve aprender que uma arquitetura específica falhou, não bloquear a gramática inteira.
9. **Polimento ranqueado**: `--polish` ou `tools/run_polisher.py` percorre elites em ordem de score. Antes de ajustar lastro, exige bracket acima do alvo, zero warnings críticos, Mach válido e margem Barrowman válida em todas as fases. Warnings normais/informativos devem ser reportados e explicados. O polidor preserva a topologia e altera massa de payload frontal existente; ele não resgata foguete abaixo do alvo nem corrige topologia instável.

## Princípios de decisão (o porquê de cada escolha)

1. **Zero Hardcoding**: Templates rígidos não descobrem o impossível. Se limitarmos o rocket a 1 body tube, ele nunca descobrirá que um foguete de 3 estágios atinge 100km mais fácil. A estrutura DEVE ser em árvore (AST). Isso vale para o motor também: nada de assumir "só existem 3 motores" — o pool é dinâmico (item 7).
2. **Intuição sobre Força Bruta (CKG)**: Testar `Ejection` a Mach 3 repetidas vezes é desperdício de CPU. Sub-grafos falhos são penalizados massivamente no Continuous Knowledge Graph; gerações futuras rejeitam a mutação ANTES da simulação.
3. **Java não é Thread-Safe para JPype Bulk**: Experimentos reais provaram que `OpenRocketInstance` e classes GUI internas como `startup.Application` geram deadlock ao serem acessadas por 16 threads simultâneas em Python (`parallel_evaluator.py`). Rust faz o peso, Java faz o polimento sequencial — e mesmo sequencial, a JVM só pode ser iniciada UMA VEZ por processo: `organic_loop.py::export_elites` abre um único `OpenRocketInstance` e reutiliza para todas as N validações (regressão real: abrir um por elite crasha com "JVM cannot be restarted" no 2º candidato).
4. **Sem "Tape Fixes" Humanos**: O programador/AI assistente não pode alterar o código-fonte do builder para consertar problemas aerodinâmicos do domínio. O solver genético deve sofrer a pressão evolutiva para se consertar sozinho. Isso inclui não hardcodear geometria/raio: `mission_adapter.rs::build_mission` valida diâmetro do motor vs diâmetro interno da airframe (1mm de clearance, igual ao OpenRocket) ANTES de simular — genomas fisicamente impossíveis são rejeitados, nunca "consertados" manualmente.
5. **Capping de CPU no Rust**: Mesmo a engine Rust sendo isolada, rodar genomas infinitos com `rayon` usa 100% da máquina e trava o Desktop do host. Deve-se configurar `RAYON_NUM_THREADS` ou o builder para deixar headroom (max 70% logical cores).
6. **Dados de motor/material vêm do OpenRocket real, nunca de mão**: `MOTOR_DATABASE` (`rocket_forge.py`) e os `.eng` em `l2_engine/motors/` são extraídos diretamente do database SQLite que o próprio OpenRocket 24.12 embarca (`openrocket/core/.../datafiles/thrustcurves/initial_motors.db`) via `extract_motors.py` — não de transcrição manual. Essa disciplina já pegou um erro real (O8000 tinha 161mm hardcoded; o motor real é 150mm) que só apareceu quando o pool deixou de ser 3 motores fixos.
7. **Pool de motores é dinâmico, não uma lista hardcoded**: `ast_eval.rs` escaneia `l2_engine/motors/*.eng` inteiro (não uma lista de 3 nomes); a designação usada pela AST (`motor_designation`, resolvida por `rocket_ast.py` a partir de `MOTOR_DATABASE[idx][1]`) é a MESMA string que o header do `.eng` usa E que o OpenRocket resolve no `.ork` — uma única fonte de verdade por motor, não um alias Rust-side. Adicionar um motor = rodar `extract_motors.py` de novo, zero mudança de código Rust.
8. **Precisão exige identidade determinística**: cada componente e a configuração de voo no `.ork` devem ter IDs estáveis, cada motor deve carregar o digest da curva, e a validação usa seed fixa `16000`. `orhelper.Helper.run_simulation` randomiza a seed por design e não pode ser usado no caminho de autoridade.
9. **Margem OpenRocket é gate, não telemetria**: a margem Rust filtra a população, mas a aceitação final exige Barrowman OpenRocket `>= constraints.min_static_margin` em cada fase configurada por `stability.phase_machs`.

## Mapa de arquivos

| Caminho | Papel |
|---|---|
| `rocket_ast.py` | Engine de geração, mutação e compilação de Árvores de Sintaxe Abstrata (AST) para ORK. |
| `rocket_forge.py` | `MOTOR_DATABASE` (34+ motores reais, dados verificados contra o database do OR) e `MATERIALS`. |
| `organic_loop.py` | Orquestrador do GA orgânico: população, mutação, chamada ao `ast_eval`, CKG, export de elite, validação OpenRocket. |
| `missions/precision_16k_m3_organic.json` | Missão orgânica padrão para alvo 16000.000000 m, `max_mach <= 3.0`, e margem estática mínima sem selecionar motor/topologia manualmente. |
| `extract_motors.py` | Regenera `l2_engine/motors/*.eng` direto do `initial_motors.db` bundled do OpenRocket. |
| `l2_engine/src/ast.rs` | Parser AST → `RocketGeometry`, avaliação física, checagem de fitment motor/airframe. |
| `l2_engine/src/bin/ast_eval.rs` | Scorer em batch (JSON in/out); escaneia `l2_engine/motors/*.eng` dinamicamente. |
| `l2_engine/src/builder.rs` | SÓ matemática de CG/margem compartilhada (`stack_wet_cg`, `static_margins`) — sem template fixo. |
| `l2_engine/src/sim_core/` | Física portada nativamente de `rocket-sim` (self-contained, sem dependência externa). |
| `l2_engine/motors/*.eng` | Curvas reais extraídas do database do OpenRocket (ver `extract_motors.py`). |
| `.agents/skills/l2-organic-evolution/` | Diretório de guidelines, contendo esta skill e os logs da arquitetura orgânica. |
| `parallel_evaluator.py` | [DEPRECATED/TEST ONLY] Script que provou o limite de singletons do JPype. |
| `docs/project_doctrine.md` | Doutrina operacional: níveis de autoridade, comandos de calibração/validação/polimento e regras de reporte. |

## Referências desta skill

- [references/decision-log.md](references/decision-log.md) — crônica dos testes de multithreading, descobrimento do deadlock do Java, a criação do compilador AST, e a retirada do pipeline paramétrico fixo (2026-07-04).
- [references/contracts.md](references/contracts.md) — formato de nó JSON da AST, limites do compilador XML, regras duras do `.ork`/OpenRocket, e a convenção de designação de motor único (MOTOR_DATABASE ↔ `.eng` ↔ `.ork`).
- [references/debugging-playbook.md](references/debugging-playbook.md) —
  assinaturas de diagnóstico, snippets JPype, protocolo de recalibração
  (`or_mode_calibrate.py`), migrado e atualizado de `l2-hyper-evolution`.

## Limites conhecidos (não redescobrir)

- OpenRocket via JPype **vai** gerar deadlock se você colocar múltiplos `OpenRocketInstance` dentro de um `ThreadPoolExecutor` para processamento paralelo.
- Resultado sem `or_metrics` é proxy Rust, não autoridade OpenRocket. Documento de missão deve dizer isso explicitamente.
- `tools/run_polisher.py` recusar todos os elites significa que nenhum baseline passou os gates de autoridade. Nas missões extremas `anomaly_200km` e `push_limits`, a causa observada foi mismatch Rust/OR: apogeu OpenRocket muito abaixo do alvo e margem Barrowman negativa, não bug do polidor.
- Se Rust OR-mode parecer distante do OpenRocket em missões extremas, primeiro verifique se o batch tem `phase_machs` e se as margens Rust estão sendo comparadas com as margens OR no mesmo Mach por fase.
- Nelder-Mead no Python é inútil para exploração estrutural profunda pois não processa arrays de tamanho variável (mutações topológicas). O solver DEVE ser um algoritmo genético (GA).
- `constraints.max_mach` e `constraints.min_static_margin` são rejeições duras no Rust (`l2_engine/src/ast.rs`), não apenas preferência de score. Candidatos reprovados ficam auditáveis no JSON, mas não devem gerar `.ork` viável.
- O polimento de precisão só é numericamente válido quando IDs de componentes/configuração, digest do motor e seed são determinísticos. Se a mesma massa produzir dois apogeus, audite esses três itens antes de trocar solver.
