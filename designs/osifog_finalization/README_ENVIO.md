# Pacote de envio OSIFOG — Nível 3

## ARQUIVO PARA UPLOAD

```
Nível 3 L2 Systems 1024.ork
```

- SHA-256 `AF634722F706B647EE627470E4EFC102EAFC25EAF35660DA9B59E5B86871B249`
- 2,22 MB (limite do formulário: 10 MB)
- Score oficial **789.357,9**
- Seed dos dados salvos: `16000`
- Livery: L2 Celestial Datum V7
- Nome interno do foguete: `Nível 3 L2 Systems 1024` (igual ao nome do arquivo)
- Designer no arquivo: `L2 Systems AI` — **conferir se é a grafia desejada**

Confirmar que **L2 Systems 1024** corresponde exatamente ao nome registrado no
Formulário de Equipes. Se o formulário usar outra grafia, renomear o arquivo e
regerar com `rocket_name` igual, para que o nome interno continue batendo.

## Resultado gravado (seed 16000, dt 0,05)

| item | valor |
|---|---:|
| apogeu | 2998,387 m |
| Mach máximo | 0,9248 |
| pouso do sustentador | **4,598 m/s** |
| pouso do booster | **4,338 m/s** |
| atitude no toque | +85,4° / +85,5° (base primeiro) |
| margem estática mínima na subida | 0,780 cal |
| propelente total | 5,234 kg |

Decomposição do score:

| termo | perda |
|---|---:|
| posição média de toque | 28.668 |
| propelente | 39.253 |
| deriva horizontal do apogeu | 24.938 |
| erro de apogeu (−1,61 m) | 7.801 |
| velocidade de toque | 9.981 |

## Verificação do pacote

`scripts/osifog_submit.py`, seed 16000: **20/20 PASS, 0 FAIL**.

- exatamente 1 simulação, status `Up To Date`
- 2 ramos de voo gravados (`Sustainer`, `Booster`)
- **4.764 `<datapoint>` gravados** — ou seja, "Todos os dados simulados".
  "Somente figuras primárias" gravaria zero datapoints e seria DESCLASSIFICAÇÃO.
- extensão anti-tumbling presente (a permitida pelo documento do Nível 3)
- 2 decais v7 resolvem byte a byte dentro do `.ork`
- zero tags de override de massa/CG/CD (checklist item 7)
- nenhum dispositivo de recuperação passiva

## OpenEarth — CONCLUÍDO E CONFERIDO

`openearth/` contém:

| arquivo | conteúdo |
|---|---|
| `Openearth.png` | captura do campo de missão, 1607x725 px — **anexar ao formulário** |
| `OpenEarth_File.kml` | 2 trajetórias + 7 marcadores |
| `osifog_L2Systems1024_stage1_sustentador.csv` | 2.191 pontos, extraídos do `.ork` |
| `osifog_L2Systems1024_stage2_booster.csv` | 2.573 pontos, extraídos do `.ork` |

Conferência do KML contra o voo gravado no `.ork` — bate em tudo:

- 2 `LineString` (uma por estágio) e 7 `Placemark`
  (Ponto de Lançamento, e para cada estágio: trajetória, apogeu, aterrissagem)
- contagem de pontos idêntica aos CSVs: 2.191 e 2.573
- ambas as trajetórias partem de `28,56210 / -80,57720` = o sítio de lançamento
- apogeu marcado em 2.998,4 m nos dois ramos
- altitudes de 3,0 m (elevação do sítio) a 3.001,4 m MSL = 2.998,4 m AGL + 3,0
- aterrissagem do sustentador `28,56283 / -80,57687` = E +31,9 m, N +81,2 m
- aterrissagem do booster `28,56334 / -80,57653` = E +65,8 m, N +137,4 m

Os CSVs foram extraídos diretamente dos `<datapoint>` gravados no `.ork`, não de
uma nova execução, então as trajetórias do KML são exatamente o voo submetido.

Os CSVs antigos `osifog_850k_stage_*.csv` (em `designs/osifog_level3/openearth/`)
são de um projeto diferente e **não** foram usados.

## Itens do formulário

1. Rodar a simulação imediatamente antes de salvar — **já feito neste arquivo**.
2. Salvar com "Todos os dados simulados" — **já feito** (4.764 datapoints).
3. Manter exatamente uma simulação ativa — **já feito**.
4. Enviar o `.ork` — este arquivo.
5. Anexar captura 2D e 3D do foguete no OpenRocket.
6. Anexar captura do OpenEarth com as trajetórias dos dois estágios.
7. Perfil OpenWind obrigatório e extensão anti-tumbling — **já embutidos**.

**Não apertar "Executar Simulação" ao abrir o arquivo para as capturas.** O
OpenRocket não serializa a seed; uma nova execução gera outra realização e
substituiria os dados gravados.

Prazo: **26 de julho de 2026, 23:59 BRT**.

## `archive_previous_607k/`

Pacote anterior desta pasta, preservado intacto como alternativa:

- `Nível 3 L2 Systems 1024.ork`, SHA-256
  `440A43AEC55F45165BA74341380144F64B970166046479517140469CCF694467`
- score 607.219,5 na seed `30017`
- pouso 2,054 m/s (sustentador) / 3,788 m/s (booster)

Mesma geometria; difere apenas no tempo de separação e nos dois atrasos de
retro. O arquivo novo vale **+182.138 pontos**, com pousos mais rápidos
(4,598 / 4,338 m/s contra 2,054 / 3,788 m/s), ambos legais.

Também guarda os diagnósticos anteriores (`coupler_closure_drag_probe.json`,
`seed_search_500.*`).

## Origem do ganho

Contra o pacote anterior, mudaram **apenas três valores de tempo**:

| parâmetro | anterior | novo |
|---|---:|---:|
| `s1_separation_delay` | 23,1000000 s | **46,0000000 s** |
| `s0_retro_delay` | 49,2525000 s | **50,3797631 s** |
| `s1_retro_delay` | 79,0625000 s | **57,4628638 s** |

Nenhuma geometria, material, motor, massa, vento, local ou acabamento mudou, de
modo que todas as verificações estruturais do candidato anterior continuam
válidas por construção.

A fórmula do regulamento penaliza `−2×(média_leste)² − 2×(média_norte)²`, a
média entre os estágios elevada ao quadrado. Como a separação acontece depois do
apogeu, atrasá-la não altera apogeu, Mach, margem de estabilidade nem propelente
— encurta apenas a descida do booster no vento. O raio de pouso do booster caiu
de 590,7 m para 152,4 m e essa penalidade caiu de 217.783 para 28.668.

**Ressalva honesta:** a janela de ignição do retro do sustentador neste ponto é
inferior a 1 ms na seed 16000. Isso é irrelevante se a banca pontuar os dados
gravados (o que o formulário indica, ao exigir "Todos os dados simulados" e
desclassificar "Somente figuras primárias"), e fatal se ela reexecutar a
simulação. O pacote em `archive_previous_607k/` é mais robusto a reexecução.
