---
name: l2-hyper-evolution
description: "[RETIRADA 2026-07-04] Pipeline paramétrica de 3 estágios fixos (Kick/Sustainer/Booster, DesignGenome, l2_engine evolve). Substituída pela l2-organic-evolution (AST, topologia livre, motores/materiais reais do OpenRocket). Mantida apenas como referência histórica — o binário `l2_engine evolve`, `genome.rs` e o template fixo em `builder.rs` foram DELETADOS do código. Não use esta skill para trabalho novo; use l2-organic-evolution."
---

# L2 Hyper Evolution — Framework Operacional [RETIRADA]

> **RETIRADA em 2026-07-04.** Este pipeline não existe mais no código:
> `l2_engine/src/bin/evolve.rs`, `l2_engine/src/genome.rs` e o template fixo
> de `builder.rs` (`build_geometry`, `DesignGenome`, raios hardcoded
> K/S/B_RADIUS) foram deletados. Motivo: o template fixo acumulou dados
> hardcoded/hand-transcribed (diâmetro errado do O8000, comprimentos de
> motor errados) e o pipeline organicamente evoluído (`l2-organic-evolution`)
> resolve a mesma missão com topologia livre, motores/materiais reais
> puxados diretamente do database do próprio OpenRocket, sem template fixo
> para divergir. **Use [l2-organic-evolution](../l2-organic-evolution/SKILL.md)
> para qualquer trabalho novo.**
>
> O conteúdo abaixo (princípios de decisão, playbook de debugging, regras
> duras do .ork, semânticas do rocket-sim) continua sendo lido como
> referência histórica — grande parte da engenharia (formato .ork, gotchas
> do JPype, calibração proxy↔OR) foi migrada para
> `l2-organic-evolution/references/contracts.md` e
> `l2-organic-evolution/references/debugging-playbook.md`, já que se aplica
> igualmente ao pipeline atual (o mesmo `sim_core` portado, o mesmo
> compilador de `.ork`). Os comandos abaixo (`l2_engine evolve`,
> `genome.rs`) **não funcionam mais** — não copie/cole.

# L2 Hyper Evolution — Framework Operacional (histórico)

Pipeline de duas etapas para design evolutivo de foguetes. A missão é um JSON
declarativo; nenhuma missão nova deve exigir código.

```
missions/X.json ─► ETAPA 1: Rust explorer          ─► elite.json ─► ETAPA 2: OpenRocket polisher ─► .ork final
                   l2_engine evolve (~110 sims/s)                    l2_hyper (física oficial)       + relatório
                   GA pop 300 × 40 gens                              GA pop 12-20 × 3-5 gens
```

**Lei fundamental: o OpenRocket é a autoridade; o Rust é proxy.** O proxy
explora barato (performance correlaciona: apogeu +12%, Mach +5%); a verdade
(margens de estabilidade, warnings, números oficiais) vem sempre do OR.
Nunca inverta os papéis, nunca reporte números do proxy como resultado final.

## Comandos padrão

```bash
# Etapa 1 — exploração (determinístico por seed, lê fitness dinamicamente da missão)
cd l2_engine && cargo run --release --bin evolve -- --mission ../missions/karman_m6.json --pop 300 --gens 40 --out ../elite.json

# Sonda de calibração (margens por fase de cada genoma da elite)
cargo run --release --bin evolve -- --mission ../missions/karman_m6.json --probe ../elite.json

# Etapa 2 — validar 1 genoma / polir com GA (na raiz do repo)
python -m l2_hyper.run_mission missions/karman_m6.json --validate
python -m l2_hyper.run_mission missions/karman_m6.json --seed-file elite.json --pop 14 --gens 4
```

## Workflow padrão para uma missão nova

1. **Escreva a missão** em `missions/<nome>.json`: stack de motores (top
   primeiro, com manufacturer/designation do database do OR), `objectives`
   declarativos, `constraints.min_static_margin` (default 1.5 cal),
   `seeds` se houver design conhecido. Schema completo em
   [references/contracts.md](references/contracts.md).
2. **Escolha motores** consultando o database do OR em runtime (nunca de
   memória — designações e digests variam). Snippet no playbook.
3. **Adapte a Etapa 1 se o stack mudou**: `l2_engine/motors/*.eng`
   (exportar do database do OR, nunca de outra fonte), raios/comprimentos em
   `builder.rs`, bounds em `genome.rs`. Stack igual = pular.
4. **Rode o evolve** e inspecione: a elite deve cavalgar as fronteiras de
   `REQ_MARGINS` (se está longe, os bounds estão apertados; se todos clones,
   verifique o filtro de diversidade).
5. **Valide 1 genoma no OR** (`--validate --seed-file elite.json`) ANTES do
   polish. Compare margens Rust×OR — se o gap mudou vs a tabela de
   calibração, recalibre (protocolo no playbook).
6. **Polish GA** com `--seed-file`. Seeds são mesclados com os da missão e
   limitados a pop/2 — não desligue isso (ver princípio 6).
7. **Relate honestamente**: números oficiais do OR, warnings restantes,
   caveat de que Barrowman extrapola acima de ~Mach 3-4.

## Princípios de decisão (o porquê de cada escolha)

1. **Causa raiz antes de fix, por bisseção mínima.** Nunca proponha correção
   sem reproduzir e isolar. Toda descoberta estrutural deste projeto veio de
   variar UMA coisa por vez (ex.: UUID-only vs UUID+conditions no .ork).
2. **Proxy barato / verdade cara.** 3 ordens de magnitude de diferença de
   custo (110 sims/s vs ~0.2 sims/s) compram exploração, não confiança.
3. **Constraint é fitness de primeira classe, com penalidade graduada.**
   Estabilidade não é pós-filtro: sem penalidade o GA converge para foguetes
   de mínimo arrasto que tombam (aconteceu 2×). Penalidade graduada
   (`score *= 0.05 + 0.95*ratio`), nunca penhasco — o GA precisa de
   gradiente para voltar à região viável.
4. **Simulação mascara instabilidade; margem estática não.** Um foguete com
   TWR alto "voa" no simulador mesmo instável no liftoff (-0.164 cal na GUI
   com sim "ok"). Sempre verifique margens por fase de voo, não só o voo.
5. **Calibre por medição, nunca por suposição.** O gap proxy↔OR e o viés
   entre versões do OR (23.09 vs 24.12: mesmo arquivo, CG ±9 cm) foram
   MEDIDOS com pares (rust, or) e absorvidos com buffer. Toda constante de
   calibração no código referencia os datapoints que a geraram.
6. **Diversidade é insumo do GA, não luxo.** Elite homogênea (16 clones) +
   população pequena = polish preso em vale (score 0.039 por 3 gerações).
   Filtro de distância no export + cap de seeds em pop/2 + slots aleatórios.
7. **Idempotência e isolamento de falha por default.** RNG seeded nos dois
   lados, cache de motores, candidato que explode vira score -inf e o run
   continua. Mesma missão + mesma seed = mesma evolução.
8. **Física orienta a ordem da busca.** Alavancas em ordem de impacto:
   (a) delays de ignição dos estágios superiores (trocam Mach por apogeu),
   (b) massa morta do estágio superior (~200 m/s de Δv por kg),
   (c) aletas (estabilidade vs arrasto). Varra (a) só depois de garantir
   estabilidade, senão a varredura mede ruído (aconteceu: 6 delays idênticos
   = assinatura de estágio morto).
9. **Erro oculto exige extração forçada.** JPype esconde exceções Java
   (`<exception str() failed>`); strings de eventos vêm localizadas em
   pt-BR. Sempre `printStackTrace` via StringWriter e `getType().name()`.
10. **Relate o que é, não o que gostaria.** Custos aparecem no relatório
    (paraquedas: -25 km; estabilidade: -130 km de apogeu proxy). "TARGETS
    NOT MET" é um resultado válido e reportável.

## Mapa de arquivos

| Caminho | Papel |
|---|---|
| `missions/*.json` | Missões declarativas (stack + objectives + seeds) |
| `l2_hyper/` | Etapa 2: mission/genome/generator/orkit/ga/run_mission |
| `l2_engine/src/genome.rs` | Genoma serde (contrato `s0_*`), operadores GA |
| `l2_engine/src/builder.rs` | Geometria 3-stage paramétrica + `static_margins` |
| `l2_engine/src/bin/evolve.rs` | GA Etapa 1 + `--probe`; parser dinâmico do mission JSON; `REQ_MARGINS` |
| `l2_engine/motors/*.eng` | Curvas exportadas do database do OR |
| `elite.json` | Contrato de handoff Etapa 1 → Etapa 2 |
| `l2_engine/docs/HYPER_EVOLUTION_PIPELINE.md` | Doc de engenharia (histórico + calibração) |

## Referências desta skill

- [references/decision-log.md](references/decision-log.md) — crônica de como
  cada etapa foi executada, cada bug encontrado e o raciocínio de cada fix.
- [references/contracts.md](references/contracts.md) — schemas (missão,
  genoma, elite.json) e regras duras do formato .ork / OpenRocket 23.09.
- [references/debugging-playbook.md](references/debugging-playbook.md) —
  assinaturas de diagnóstico, snippets JPype, protocolo de recalibração.

## Limites conhecidos (não redescobrir)

- Modelo de margem do kick isolado no Rust é estruturalmente otimista
  (+2.25 onde OR mede -2.19). Mitigado por `REQ_MARGINS` por fase; fix real
  pendente: portar CNα de aletas/corpo do OR ao barrowman.rs.
- Barrowman (ambos os lados) extrapola acima de ~Mach 3-4: números são
  "oficiais de simulador", não predição de voo real.
- OR 23.09 headless × 24.12 GUI: viés de CG ~0.55 cal no mesmo arquivo —
  por isso o default de margem é 1.5 e não 1.0.
- rocket-sim: sem multi-burn nativo real além de `ignition_delay`/
  `separation_coast`; paraquedas abriria no burnout do último estágio
  (por isso o proxy voa sem chute e a massa vai no ballast).
