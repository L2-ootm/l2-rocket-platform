# Debugging Playbook — assinaturas, snippets e recalibração

Migrado e atualizado de `l2-hyper-evolution/references/debugging-playbook.md`
(2026-07-04) — os snippets JPype/orhelper e as assinaturas de diagnóstico
continuam válidos para o pipeline orgânico ativo; o protocolo de
recalibração foi atualizado para as ferramentas reais (`or_mode_calibrate.py`,
não o `evolve.rs --probe` deletado).

## Assinaturas de diagnóstico (reconheça antes de investigar)

| Assinatura | Significado | Ação |
|---|---|---|
| vmax/Mach idênticos para qualquer delay de ignição | "estágio morto": o motor de cima não contribui | Checar: motor montado? ignição pós-apogeu? tumble na ignição (instável)? |
| Branch do estágio descartado apogia MAIS ALTO que o estágio com motor | estágio superior voando de lado/de ré | Estabilidade do estágio isolado (margens por fase) |
| `IGNITION` com t > t(`APOGEE`) no branch 0 | delay longo demais para a energia do stack | Reduzir delay / mais impulso abaixo |
| `TUMBLE` logo após `IGNITION` | instável na ignição (AOA alto, aletas pequenas, CG traseiro) | Aletas maiores/lastro; acender mais cedo (mais pressão dinâmica alinha) |
| "Grande ângulo de ataque (17x°)" no OR | voando quase de ré | idem acima |
| Apogeu do sim ok mas GUI mostra margem negativa | TWR alto mascarando instabilidade | Contrato de margens por fase (nunca confiar só no sim) |
| GA todo preso no mesmo score baixo por gerações | população homogênea num vale | Diversidade: seeds ≤ pop/2, mutantes fortes, aleatórios |
| Elite Rust reprova no OR | gap de calibração do proxy | Protocolo de recalibração abaixo |
| Todo genoma falha com `motor_oversized` | radius do stage não acomoda o motor + 1mm clearance | Ver `mission_adapter.rs::build_mission`; conferir se o motor mudou e a AST não ajustou o `radius` do `BODY_TUBE` |
| `missing_motor_curve:<designacao>` em massa | designação da AST não bate com nenhum `.eng` carregado | Confira a convenção de designação única (contracts.md); rode `extract_motors.py` se o motor não tem `.eng` ainda |
| `JVM cannot be restarted` no 2º+ candidato validado | `OpenRocketInstance` sendo aberto por candidato em vez de uma vez por run | `organic_loop.py::export_elites` deve abrir 1 `OpenRocketInstance` e reusar — não reintroduza o bug (regressão real, já corrigida 2026-07-04) |
| `TypeError: float() ... NoneType` ao ler resultado do `ast_eval` | `f64::NEG_INFINITY`/`INFINITY` do Rust virou `null` no JSON | Rust deve usar sentinela finito (ex. `-1.0e9`), nunca infinito, em qualquer campo numérico serializado (regressão real, já corrigida) |
| Mesma massa alterna entre dois apogeus | IDs aleatórios de componente/configuração, seed randomizada ou digest ausente | Verificar `<id>`, todos os `configid`, `<digest>` e usar `run_openrocket_simulation` com seed `16000` |
| Polidor para antes do alvo apesar de mais lastro | massa foi truncada pelo sanitizador ou topologia mudou no baseline | Confirmar payload compilado no XML e preservar o componente de payload existente |

## JPype / orhelper — extração forçada de informação

```python
# 1. Exceção Java real (str() falha em RocketLoadException aninhada)
def java_trace(jexc):
    import jpype
    sw = jpype.JPackage("java").io.StringWriter()
    pw = jpype.JPackage("java").io.PrintWriter(sw)
    jexc.printStackTrace(pw); pw.flush()
    return str(sw.toString())

# 2. Eventos de voo: SEMPRE .name() — str() vem localizado (pt-BR) e quebra matching
events = [(float(ev.getTime()), ev.getType().name())
          for ev in data.getBranch(0).getEvents()]

# 3. Consultar motores do database (nunca de memória)
Application = jpype.JClass("net.sf.openrocket.startup.Application")
for ms in Application.getMotorSetDatabase().getMotorSets():
    for m in ms.getMotors():
        m.getManufacturer().getDisplayName(), m.getDesignation(), m.getDigest(),
        m.getTotalImpulseEstimate(), m.getDiameter(), m.getLength(), m.getLaunchMass()

# 4. Margem estática como a GUI calcula (23.09)
calc = jpype.JClass("net.sf.openrocket.aerodynamics.BarrowmanCalculator")()
WarningSet = jpype.JClass("net.sf.openrocket.logging.WarningSet")
cond = jpype.JClass("net.sf.openrocket.aerodynamics.FlightConditions")(config)
cond.setMach(0.3); cond.setAOA(0.0)
cp = calc.getCP(config, cond, WarningSet()).x
cg = jpype.JClass("net.sf.openrocket.masscalc.MassCalculator").calculateLaunch(config).getCM().x
margin_cal = (cp - cg) / float(cond.getRefLength())
# fases: config.setAllStages(); config._setStageActive(n-1-dropped, False)
```

Regras de ambiente:
- Um único `OpenRocketInstance` por processo (boot ~35 s); todos os
  candidatos dentro dele — `organic_loop.py::export_elites` já segue isso.
- 23.09 usa pacotes `net.sf.openrocket.*`; 24.x renomeou para
  `info.openrocket.*` (fetches no GitHub: 23.09 = `core/src/net/sf/...`).
- Console Windows: `sys.stdout.reconfigure(encoding='utf-8')` antes de
  imprimir saída do OR.
- Em strings Python com caminhos Windows, cuidado com `\a`/`\t` em
  concatenação de path — use forward slashes.

## Protocolo de recalibração proxy↔OR

Quando: elite Rust reprova margem/apogeu/Mach no OR; motores mudaram;
`barrowman.rs`/`ast.rs`/`sim_core` mudaram.

1. Rode `organic_loop.py --evaluator rust ...` normalmente, gerando um
   `designs/organic/organic_elite.json` (ou use um `elite.json` existente).
2. Compare contra o OpenRocket real:
   `python or_mode_calibrate.py --mission <missão> --elite <elite.json>
   --count N --out designs/organic/or_mode_calibration.json`
   — isso abre UMA JVM, roda os N primeiros candidatos no OpenRocket real, e
   reporta `mean_abs_apogee_pct`, `mean_abs_mach`, `mean_abs_min_static_margin`.
3. Leia `summary.success_count` primeiro — se `0`, todos os candidatos
   falharam antes de chegar no OpenRocket (geometria inválida do lado
   Rust/AST), não é um problema de calibração ainda.
4. Datapoint real medido 2026-07-04 (pós-fix de clearance/radius):
   apogeu proxy superestimado em ~13.9%, Mach em ~0.35, consistente em
   direção/magnitude — indica o modelo de arrasto supersônico/transônico do
   `barrowman.rs` sendo otimista perto de Mach 6. Não tunar a partir de 3
   pontos; rode contra um dataset mais largo (várias missões, vários Mach)
   antes de mexer nas tabelas de CD.
5. Performance (apogeu/Mach) historicamente correlaciona bem; se descolar
   muito mais que o de costume, o problema é outro (motor errado, geometria
   divergente entre o `.ork` compilado e o que o Rust simulou).

## Verificação antes de declarar sucesso

1. `cargo test` verde em `l2_engine` (80 testes na sessão 2026-07-04).
2. `organic_loop.py --evaluator rust --validate-openrocket N` no melhor
   elite: `status: success` no `or_metrics`, sem exceção de resolução de
   motor, margens plausíveis.
3. Warnings do sim: zero em todos os níveis para uma missão que exige saída limpa.
4. Reportar números do OR, nunca do proxy, com os custos explícitos e o
   caveat Barrowman >Mach 3-4.
5. Reabrir o `.ork` final pelo menos cinco vezes com seed fixa; apogeu, Mach
   e margens devem se repetir exatamente.
