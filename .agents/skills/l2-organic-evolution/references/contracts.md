# Contracts & Schemas

## O Genoma AST (Abstract Syntax Tree)
Diferente do genoma clássico (`s0_m_para`, `s0_m_x34`, etc), a evolução orgânica serializa o foguete em formato de nós. Cada `ASTNode` engatilha uma injeção de bloco na string XML que envia ao OpenRocket, ou no builder da l2_engine. 

```json
[
  {
    "type": "STAGE",
    "params": {
      "name": "Evolved Sustainer"
    }
  },
  {
    "type": "NOSE_CONE",
    "params": {
      "shape": "conical",
      "length": 0.473,
      "material": "pla"
    }
  },
  {
    "type": "BODY_TUBE",
    "params": {
      "length": 1.27,
      "radius": 0.076,
      "material": "abs"
    }
  },
  {
    "type": "MOTOR_MOUNT",
    "params": {
      "motor_index": 22,
      "motor_designation": "L1500T",
      "ignition": "automatic"
    }
  },
  {
    "type": "CLOSE_BODY",
    "params": {}
  }
]
```

## Protocolos Essenciais
1. Todo AST precisa obrigatoriamente abrir com um node tipo `STAGE`.
2. Um `BODY_TUBE` no compilador AST injeta todos os blocos abaixos como sub-componentes em árvore (porém escritos sequencialmente). O nó deve obrigatoriamente ser fechado por `CLOSE_BODY`.
3. `motor_index` aponta para `MOTOR_DATABASE` (`rocket_forge.py`) e é o que `rocket_ast.py`'s `ASTCompiler` usa para escrever `<designation>`/`<diameter>`/`<manufacturer>` no `.ork` (sempre via lookup na tabela, nunca hardcoded). `motor_designation` é OBRIGATÓRIO no JSON que chega no Rust (`l2_engine/src/ast.rs::motor_designation()` retorna erro de parse se faltar) -- não existe fallback por índice no lado Rust, porque o Rust não tem acesso a `MOTOR_DATABASE`. `rocket_ast.py` preenche os dois campos juntos sempre que `motor_index` é definido/mutado (`ASTNode.mutate`, `create_random_ast`), então nunca escreva um `MOTOR_MOUNT` só com `motor_index` manualmente.

## Convenção de designação de motor (fonte única de verdade)

Uma única string de designação por motor circula em três lugares, e DEVE ser
idêntica nos três -- não um alias Rust-side e um nome OpenRocket-side:

1. `MOTOR_DATABASE[idx][1]` em `rocket_forge.py` (usada para compilar `.ork`
   -- é a string que o próprio OpenRocket resolve; para os 3 motores
   Cesaroni "grandes" isso é a forma com prefixo de catálogo + sufixo `-P`,
   ex. `"20146N5800-P"`, comprovada funcionando contra o OpenRocket real via
   `l2_hyper/generator.py`/missions JSON -- NÃO simplifique para "N5800" só
   porque é mais legível, isso quebra a resolução do motor no `.ork`).
2. O header do `.eng` correspondente em `l2_engine/motors/*.eng` (primeiro
   campo da linha de header -- `motor_db::parse_eng_file` lê esse campo como
   a chave de lookup, o nome do ARQUIVO é só um label humano, pode divergir).
3. `motor_designation` no JSON do `MOTOR_MOUNT` (ver acima).

Todos os `.eng` em `l2_engine/motors/` foram extraídos direto do database
SQLite que o OpenRocket 23.09 embarca
(`openrocket/core/src/main/resources/datafiles/thrustcurves/initial_motors.db`)
via `extract_motors.py` -- não de transcrição manual. Ao adicionar um motor
novo: adicione a entrada em `MOTOR_DATABASE` com a designação real (consulte
o `.db` ou o database em runtime, nunca invente), rode `extract_motors.py`
(ou adicione manualmente ao dict `TARGETS`/`HEADER_DESIGNATION_OVERRIDE` se
precisar de uma designação diferente da bundled), confira que os três locais
batem.

## Regras duras do `.ork` / OpenRocket 23.09 (violou = load falha)

Migrado de `l2-hyper-evolution` -- aplica-se igualmente ao `.ork` compilado
por `rocket_ast.ASTCompiler`, já que é o mesmo formato de arquivo:

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
5. InnerTube usa `<outerradius>` -- um `<radius>` é IGNORADO em silêncio
   (warning "Unknown parameter type 'radius'"), massa do mount fica errada.
6. `<digest>` no `<motor>` pina a variante exata (sem ele: "Multiple motors
   ... one chosen arbitrarily"). Resolver via database em runtime quando
   possível.
7. Estágio de topo precisa de dispositivo de recuperação (drogue,
   `<deployevent>apogee</deployevent>`) ou o sim marca crítico.
8. Warnings inevitáveis (não caçar): precisão supersônica do Barrowman;
   "open forward airframe" dos interstages; "-P-P" no nome de motor CTI
   plugged na GUI (designação oficial + sufixo de delay da GUI).

## Semânticas `sim_core` (física portada de rocket-sim, agora nativa)

Migrado de `l2-hyper-evolution` -- `l2_engine/src/sim_core/` é hoje um port
nativo (não mais um crate externo), mas as semânticas do modelo físico não
mudaram:

- `geometry.stages` em ordem de IGNIÇÃO (0 = booster/último a ser
  adicionado). `ast_to_geometry` em `ast.rs` já monta nessa ordem.
- `ejection_charge_delay` → `separation_coast` (burnout → drop do estágio).
  `SeparationConfig.delay` NÃO é consumido pelo adapter.
- `ignition_delay` é medido da separação, não do burnout do estágio
  anterior direto -- confira a convenção ao portar genomas/parâmetros.
- Motor mass no PONTO MÉDIO do primeiro bodytube do estágio → tubo
  principal primeiro no vec.
- Sem paraquedas real no proxy (abriria no burnout do último estágio); massa
  do drogue+aviônica deve ir em `ballast_mass` do nosecone.
- `compute_aero` soma `stage.axial_offset_m + component.axial_offset_m`
  (frame absoluto do nariz do stack); CG passado a ele deve estar no mesmo
  frame.
- Motor/airframe fitment é validado ANTES de montar a `Mission`
  (`mission_adapter.rs::build_mission`): diâmetro do motor + 1mm de
  clearance radial não pode exceder o diâmetro interno (raio - thickness)
  do tubo mais estreito do estágio. Genoma que viola isso retorna erro de
  parse, nunca roda a física (ver `docs/organic_loop_report.md` #3 para o
  exploit histórico que motivou essa checagem).
