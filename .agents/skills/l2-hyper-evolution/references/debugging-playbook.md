> **[RETIRADA 2026-07-04]** Os snippets JPype/orhelper e as assinaturas de
> diagnóstico abaixo continuam válidos (migrados para
> `l2-organic-evolution/references/debugging-playbook.md`). O "Protocolo de
> recalibração" que referencia `cargo run --bin evolve -- --probe` está
> desatualizado -- esse binário foi deletado; o equivalente atual é
> `or_mode_calibrate.py` (compara `rust_*` metrics do elite.json orgânico
> contra OpenRocket real).

# Debugging Playbook — assinaturas, snippets e recalibração

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
cond = jpype.JClass("net.sf.openrocket.aerodynamics.FlightConditions")(config)
cond.setMach(0.3); cond.setAOA(0.0)
cp = calc.getCP(config, cond, WarningSet()).x
cg = jpype.JClass("net.sf.openrocket.masscalc.MassCalculator").calculateLaunch(config).getCM().x
margin_cal = (cp - cg) / float(cond.getRefLength())
# fases: config.setAllStages(); config._setStageActive(n-1-dropped, False)
```

Regras de ambiente:
- Um único `OpenRocketInstance` por processo (boot ~35 s); todos os
  candidatos dentro dele.
- 23.09 usa pacotes `net.sf.openrocket.*`; 24.x renomeou para
  `info.openrocket.*` (fetches no GitHub: 23.09 = `core/src/net/sf/...`).
- Console Windows: `sys.stdout.reconfigure(encoding='utf-8')` antes de
  imprimir saída do OR.
- Em strings Python com caminhos Windows, cuidado com `\a`/`\t` em
  concatenação de path (bug real desta sessão: `"\\apex"` virou `\x07pex`) —
  use forward slashes.

## Protocolo de recalibração proxy↔OR

Quando: elite Rust reprova margem no OR; stack/motores mudaram; barrowman.rs
ou builder.rs mudaram.

1. Escolha ≥2 genomas espalhados (um estável no OR, um da elite atual).
2. Rust: `cargo run --release --bin evolve -- --probe file.json`
   (formato elite.json; margens por fase + apogeu/Mach proxy).
3. OR: `python -m l2_hyper.run_mission <missão> --seed-file file.json
   --validate` (margens por fase + oficiais).
4. Monte a tabela (rust → or) por fase; ajuste fit/offset e atualize
   `REQ_MARGINS` em `evolve.rs` COM os datapoints no comentário.
5. Registre os pares novos no doc §9.1. Fit instável entre pontos = o
   modelo diverge estruturalmente nessa fase → não force constante; deixe o
   polish OR corrigir e priorize portar o CNα do OR.
6. Performance (apogeu/Mach) historicamente correlaciona bem (+12%/+5%);
   se descolar muito, o problema é outro (motor errado, geometria
   divergente entre builder.rs e generator.py).

## Verificação antes de declarar sucesso

1. `cargo test --release` verde (32 testes; inclui validação <2% vs OR do
   veículo de referência).
2. `--validate` do melhor genoma no OR: TARGETS MET + margens ≥ constraint
   em TODAS as fases + sem TUMBLE/LATE-IGN.
3. Warnings do sim: só os inevitáveis (supersônico, interstage aberto,
   deploy em velocidade moderada).
4. Abrir o .ork na GUI do usuário (24.12) continua sendo a auditoria final —
   lembrar do viés de CG entre versões.
5. Reportar números do OR, nunca do proxy, com os custos explícitos
   (paraquedas, estabilidade) e o caveat Barrowman >Mach 3-4.
