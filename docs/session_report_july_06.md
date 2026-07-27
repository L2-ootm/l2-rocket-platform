# Relatório de Evolução L2-OSIFOG (06 de Julho de 2026)

## 1. Problemas Identificados (Gargalos do Algoritmo)
Durante as execuções iniciais em background das missões `anomaly_200km` e `push_limits`, os processos rodaram por mais de 2 horas sem gerar nenhum output de elite. Após análise profunda do código, dois bugs críticos (Assassinos de IA) foram descobertos no núcleo do algoritmo genético:

1. **Gargalo de Memória (I/O Buffer):** 
   O script `organic_loop.py` possuía um erro de lógica de escopo. A função `export_elites()` estava fora do loop geracional. Isso obrigava o algoritmo a rodar e guardar na memória RAM todas as 500.000 gerações exigidas pelo usuário antes de salvar o arquivo `.json` no disco.
2. **Espiral de Trauma do CKG (Pessimismo Matemático):**
   A memória global (`.planning/organic_ckg.json`) estava configurada com uma taxa de penalidade extremamente agressiva (`0.35` por falha). Como foguetes aleatórios de 8 estágios gerados na Geração 0 falham as leis da física quase 100% das vezes, o CKG "aprendia" que tudo dava errado. Na Geração 1, o multiplicador de aceitação já estava abaixo de 0.10, fazendo o pre-filtro rejeitar matematicamente todos os novos designs antes mesmo deles serem enviados ao simulador Rust.

## 2. Correções Implementadas
* **I/O Contínuo:** A função de exportação foi identada para dentro do loop principal em `organic_loop.py`, salvando o melhor design a cada geração e permitindo inspeção em tempo real.
* **Otimismo Estrutural:** A penalidade em `ckg_memory.py` foi reduzida de `0.35` para `0.01` e a recompensa subiu para `0.15`. O algoritmo agora permite a exploração de peças "falhas" em novas combinações estruturais sem medo de testá-las no simulador.
* **Calibração de Missão:** Os pesos da missão `anomaly_200km` foram alterados para priorizar cegamente o apogeu (peso `5000.0`), evitando a geração de "halteres voadores" (foguetes muito lentos e pesados).

## 3. Resultados Finais da Sessão
Com o motor totalmente destravado, deixamos os modelos processarem. O motor de física Rust superou as expectativas avaliando milhares de designs. Estes números são resultados do proxy Rust, não validações de autoridade OpenRocket:
* **Anomaly 200km:** apogeu proxy `205.022 km` (`205022.528 m`), Mach proxy `7.14`, 4 estágios.
* **Push Limits:** apogeu proxy `522.432 km` (`522432.251 m`) no segundo elite salvo; o primeiro elite salvo atingiu `530.876 km` (`530875.561 m`). Mach proxy `10.06-10.15`, até 6 estágios.

Essas missões precisam de validação OpenRocket (`run_polisher.py` ou `organic_loop.py --validate-openrocket ... --polish`) antes de serem chamadas de resultados de autoridade. O proxy Rust é a ferramenta de exploração; OpenRocket é o juiz final.

### Rechecagem OpenRocket posterior

Uma rechecagem de autoridade com `run_polisher.py` e loops curtos seedados com `--calibrate-every 1 --validate-openrocket 6 --polish` mostrou que os elites extremos carregam e simulam no OpenRocket, mas não passam os gates finais:

* **Anomaly 200km:** OpenRocket mediu cerca de `6.7-8.1 km`, Mach `1.2-1.3`, margem Barrowman negativa (`-3.9` a `-5.4` calibers), zero warnings críticos.
* **Push Limits:** OpenRocket mediu cerca de `6.8-12.8 km`, Mach `1.1-4.1`, margem Barrowman negativa (`-3.8` a `-5.7` calibers), zero warnings críticos.

Conclusão: essas missões são evidência valiosa de mismatch proxy/autoridade em topologias extremas multiestágio. O próximo trabalho deve pressionar estabilidade OpenRocket por fase e fidelidade de staging/drag no proxy antes de declarar sucesso de altitude extrema.

### Rerun do zero com memória contextual de autoridade

Depois da rechecagem, a memória foi ajustada para registrar falhas de autoridade por contexto de estágio e pares de estágios, em vez de gravar punições genéricas como `STAGE`/`CLOSE_BODY`. Isso evita que uma falha de OpenRocket em uma arquitetura extrema ensine ao sistema que "todo estágio é ruim".

Reruns curtos do zero com `.planning/or_authority_zero_context_ckg.json`, `--validate-openrocket 8`, `--calibrate-every 1` e `--polish` confirmaram que o polidor está recusando corretamente:

* **Anomaly 200km:** elites Rust `41.8-101.2 km`; OpenRocket `71-3458 m`, Mach `0.11-1.05`, margem mínima `-2.15` a `-6.24`, alguns críticos nos piores candidatos. Nenhum elite foi `authority_viable`.
* **Push Limits:** elites Rust `31.4-42.7 km`; OpenRocket `626-4550 m`, Mach `0.29-1.05`, margem mínima `-0.76` a `-4.07`, zero críticos. Nenhum elite foi `authority_viable`.

Relatórios de autoridade:
* `designs/anomaly_200km_zero_context_polish_report/authority_polish_report.json`
* `designs/push_limits_zero_context_polish_report/authority_polish_report.json`

Também foi corrigida a persistência de JSON dos reports e do CKG com escrita temporária + replace atômico com retry, porque o Windows bloqueou momentaneamente arquivos logo depois das execuções OpenRocket.

### Causa raiz do distanciamento Rust OR-mode vs OpenRocket

O drift grande não veio do polidor. O polidor estava certo em recusar. A causa raiz estava no gate Rust de margem estática:

* O Rust calculava todas as margens usando Mach de referência baixo (`0.3`).
* O OpenRocket authority calculava as fases usando `stability.phase_machs` da missão (`[0.3, 2.0, 5.0]` para `anomaly_200km`, `[0.3, 3.0, 10.0]` para `push_limits`).
* Em fase 0 os números eram próximos, mas nas fases supersônicas o Rust superestimava a estabilidade e aceitava candidatos que o OpenRocket via com margem negativa.

Diagnóstico antes da correção:

* `anomaly_200km` candidato 0: Rust margins `[3.34, 6.09, 7.09, 8.94, 4.60, 2.14]`; OpenRocket margins `[3.38, 5.14, 1.58, 2.62, -1.03, -5.84]`.
* `push_limits` candidato 0: Rust margins `[9.92, 4.95, 3.70, 5.26]`; OpenRocket margins `[8.06, -0.01, -4.07, -1.31]`.

Depois da correção, o batch Rust recebe `phase_machs` e calcula cada fase no Mach correspondente. No replay dos top-8 extremos, `push_limits` passou a rejeitar todos os 8 por `constraint_violation:min_static_margin`; `anomaly_200km` rejeitou 7 de 8 diretamente pelo mesmo gate. Isso fecha a principal porta por onde o GA estava promovendo topologias que OR nunca aceitaria.

## 4. O Futuro: Evolução da Memória (Roadmap)
A memória do algoritmo já é global e transversal a todas as missões. O primeiro passo bio-inspirado já entrou: falhas de autoridade agora são contextuais por estágio. Para a próxima iteração do L2 MIND, a memória deve continuar transicionando de um JSON estático para um modelo mais seletivo:

1. **Aprendizado de Hebb (Sinapses Contextuais):**
   Parcialmente implementado para autoridade OpenRocket. Próximo passo: usar essa memória com pressão mais forte na seleção, não apenas como penalidade fraca.
2. **Esquecimento Dinâmico (Pruning):**
   Limpeza cíclica de metadados inúteis. Assinaturas estruturais que não aparecem há milhares de gerações perderão força até desaparecerem, comprimindo o arquivo de 1GB para menos de 50MB de conhecimento super-refinado.
3. **Graph Neural Networks (GNN):**
   Substituição do mapa de hashes JSON por um modelo abstrato capaz de *interpolar* valores. O modelo aprenderá as leis da aeroelasticidade e saberá que um tubo de 1.45m e 1.46m possuem arrasto quase idêntico, parando de gerar chaves combinatórias infinitas e prevendo falhas aerodinâmicas sem nem precisar abrir o simulador de física.
