> **[RETIRADA 2026-07-04]** Este pipeline (genome.rs/builder.rs
> fixo/evolve.rs) foi deletado do código. Mantido como crônica histórica de
> raciocínio -- veja `l2-organic-evolution/references/decision-log.md` para
> as decisões do pipeline ativo, incluindo o porquê exato da retirada.

# Decision Log — como cada etapa foi executada e por quê

Crônica da sessão 2026-07-01 que construiu o pipeline. Serve de template de
raciocínio: cada seção mostra sintoma → hipótese → teste mínimo → decisão.
Leia quando for estender o sistema ou quando um problema "parecido" surgir.

## Fase 0 — Desbloqueio do load (.ork que não carregava)

**Sintoma**: `orhelper.load_doc()` lançava `RocketLoadException` com
`<exception str() failed>` em `GeneralRocketLoader.java:79` para arquivos
gerados em Python; arquivos salvos pela UI carregavam.

**Raciocínio**: a mensagem estava oculta pelo JPype → primeiro passo foi
extrair a exceção real (StringWriter + printStackTrace), NÃO especular sobre
ZIP/XML. Mensagem real: `IllegalArgumentException: Attempted to set the
configuration to an error id`.

**Teste mínimo (bisseção)**: mesmo arquivo, mesma embalagem ZIP, variando
UMA coisa por vez:
- v1: configid → UUID, resto igual → ainda falha.
- v2: UUID + `<simulations>` removido → carrega.
- v3: UUID + `<conditions><configid>` na simulação → carrega e simula.

**Decisão**: duas regras duras (UUID em configid E conditions/configid na
simulação). A correlação aparente "mutado em Python = quebra" era espúria —
todos os arquivos quebrados derivavam do mesmo template defeituoso.

**Lição**: quando A e B diferem em N dimensões, a explicação popular escolhe
a dimensão errada. Bisseque.

## Fase 1 — Design do zero para 100 km / Mach 6

**Escolhas de arquitetura, em ordem, com o porquê**:

1. *Gerar XML do zero em vez de construir via API Java*: o formato .ork já
   estava dominado (regras da Fase 0 + estrutura do arquivo da UI como
   referência); a API Java via JPype tem dezenas de chamadas incertas.
   Menor risco no caminho conhecido.
2. *Tags multi-estágio confirmadas na FONTE (release-23.09 no GitHub), não
   de memória*: `<separationevent>` = nome do enum lowercase sem underscore.
   Custo: 2 fetches. Benefício: zero tentativa-e-erro em formato.
3. *Um único JVM para todos os candidatos*: boot + motor DB ≈ 35 s; cada
   sim ≈ segundos. O loop inteiro vive dentro de um `OpenRocketInstance`.
4. *Motores máximos do database* (O8000 41 kNs + N5800): consultados em
   runtime com dump ordenado por impulso — não assumir o que existe.

**Iterações do foguete** (cada falha diagnosticada por EVENTOS de voo, não
por palpite):

| Iter | Resultado | Assinatura no log | Causa | Fix |
|---|---|---|---|---|
| v1 2-stage | 4.9 km M2.3 | vmax idêntico p/ todo delay; IGNITION após APOGEE | sustainer instável (CG traseiro, aletas pequenas) | lastro de nariz + aletas grandes como GENES |
| v2 2-stage | 125 km M5.55 | Mach satura ~5.5 | limite físico do par de motores | 3º estágio kick 75mm |
| v3 3-stage | 21 km M3.75 | branch do sustainer descartado chega MAIS ALTO que o kick com motor | kick instável na ignição + ignição perto do apogeu | estabilidade generosa + ignição 3-11s pós-separação |
| v3' | 236 km M6.9 | limpo | — | — |

**Lição-mestra da v3**: "estágio morto" (vmax preso no burnout do estágio
anterior para qualquer delay) significa que o empuxo do estágio de cima não
está sendo aproveitado — instabilidade na ignição ou ignição pós-apogeu.
A busca só fez sentido DEPOIS de garantir estabilidade; antes disso, a
varredura de delays media ruído.

## Fase 2 — Auditoria na GUI e contrato de estabilidade

**Sintoma**: usuário abriu o .ork na GUI: estabilidade -0.164 cal no
liftoff, apesar do sim "funcionar".

**Raciocínio**: TWR 13 + rail longo voam torto e mascaram margem negativa.
Logo margem estática por FASE DE VOO precisa ser métrica de primeira classe
na fitness, não inspeção manual posterior.

**Investigação do número da GUI**: nosso cálculo via API (mesmos
BarrowmanCalculator + MassCalculator.calculateLaunch da GUI) deu CP idêntico
(284 cm) e MASSA idêntica ao grama (64564 g), mas CG 277.9 vs 287. Única
variável restante: versão (GUI = 24.12, headless = 23.09). **Decisão**:
absorver o viés medido (~0.55 cal) com default de 1.5 cal em vez de 1.0 —
constante justificada por datapoint, não por gosto.

**Fitness**: penalidade GRADUADA (`0.05 + 0.95*ratio`) e não penhasco —
design a 1.3 cal precisa pontuar mais que um a -2.0 cal, senão o GA não tem
gradiente para subir de volta.

## Fase 3 — Modularização (l2_hyper)

**Por que missão declarativa**: o pedido "qualquer missão, por mais estranha
que seja" se traduz em: objectives compiláveis (`atleast/atmost/target/
maximize/minimize` sobre métricas), stack de motores como dados, genoma e
bounds DERIVADOS do stack (bounds escalam com o raio do estágio). Meta exata
de 83456 m = um objective `target`; zero código novo.

**Warnings da GUI tratados no gerador, não em pós-processamento**:
`<outerradius>` (não `<radius>`) em innertube; `<digest>` pinando a variante
do motor; drogue no estágio superior. "-P-P" foi investigado e classificado
como comportamento da GUI (sufixo de delay plugged), não bug — saber quando
NÃO consertar também é decisão.

## Fase 4 — Etapa 1 em Rust

**Estado encontrado**: esboços de genome.rs/builder.rs/evolve.rs criados em
paralelo (fora desta sessão). Decisão: ler tudo antes de sobrescrever,
aproveitar o esqueleto, corrigir os defeitos. Defeitos encontrados e por que
importavam:

1. `parse_eng("N5800-CS")` — designação não bate com o header do .eng →
   panic no boot. (Regra: o parser casa o 1º campo do header, verificado na
   fonte.)
2. `ejection_charge_delay = 0` em todos os estágios → `sep_delay` era gene
   morto (o adapter usa ejection_charge_delay como separation_coast, NÃO o
   SeparationConfig.delay — semântica documentada no próprio adapter).
3. Margem estática: só computava o kick e abandonava o stack — justamente a
   fase que quebrou na GUI. Reescrita com CG molhado por centroides e frame
   absoluto.
4. Offsets axiais de estágio zerados → CP do stack empilharia os 3 estágios
   em x=0. Fix acompanhado de correção no build_mission (CG stage-local →
   absoluto), desenhada como NO-OP para geometrias legadas (offset 0).
5. `rand_distr` usado sem estar no Cargo.toml → Box-Muller inline (menos
   uma dependência).
6. Chaves serde fora do contrato Python → `#[serde(rename = "s0_*")]` +
   clamp tolerante no Python (ignora chaves estrangeiras, preenche
   faltantes com mid-bound).

**Motores**: exportados do database do próprio OR (thrust points + massas)
para .eng — garante que proxy e verdade usam a MESMA curva.

**Semânticas do rocket-sim que custaram tempo** (agora em builder.rs):
delays do genoma são burnout-referenced (convenção OR), o rocket-sim é
separation-referenced → `ignition_delay = delay - sep_delay`; motor mass no
ponto médio do 1º bodytube (tubo principal primeiro no vec); sem paraquedas
no proxy (abriria no burnout e mataria o apogeu).

## Fase 5 — Calibração e o loop completo

**Protocolo**: mesmos genomas medidos nos dois motores → pares (rust, or)
por fase → fit linear → `REQ_MARGINS = [0.5, 3.2, 4.6]`.

**Falha honesta e resposta**: mesmo calibrado, o kick chegou a -1.4 cal no
OR (o fit de 2 pontos não segura uma região nova do espaço de design). Em
vez de perseguir o modelo indefinidamente, confiar no failsafe DESENHADO:
o polish OR mede margens reais e evolui a correção. Fix real (portar CNα do
OR) fica registrado como pendência, não esquecido.

**Última armadilha**: o 1º polish ficou PRESO (todos os 12 slots ocupados
por 16 clones da elite Rust, todos com kick instável; mutação pequena não
atravessa a distância até a região estável). Dois fixes de sistema:
filtro de diversidade no export da elite + `run_mission` mescla seed-file
com os seeds da missão e limita seeds a pop/2.

**Resultado final**: Rust (100 s, 12k sims) → elite → polish (56 sims) →
201.4 km / Mach 6.22 / margens [2.70, 2.80, 1.56] — o vencedor é um híbrido
real (genes de performance do Rust × genes de estabilidade do seed da
missão), que é exatamente o que o pipeline existe para produzir.
