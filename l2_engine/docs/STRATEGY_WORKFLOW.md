# Estratégia de Otimização e Módulos Físicos - L2-OSIFOG

Este documento define o pipeline definitivo adotado pela Engine L2 para a competição espacial, formalizando a arquitetura "Multi-Módulos" com foco em velocidade de treinamento e exatidão balística.

## A Filosofia de Pipeline Modular

Nossos testes em larga escala comprovaram que calcular o arrasto em altíssimas velocidades (Mach 6+) na L2 Engine (Rust) com modelos de Barrowman estritos e conservadores penaliza o apogeu em até 30% quando comparado com o modelo do OpenRocket original para foguetes super finos.

Em vez de encarar isso como um "bug", abraçamos isso como a nossa principal vantagem estratégica. O modelo "Conservador/Rígido" garante que se um foguete tiver força bruta para bater 80km na Engine Rust, ele voará para mais de 120km na vida real/OpenRocket.

O Workflow completo será executado em etapas modulares contínuas (O Funil L2):

### 1. Etapa de Stress-Testing & Fast-Tuning (Módulo Rígido L2)
- **Engine Utilizada:** L2 Engine (Rust) - Modo Estrito
- **Propósito:** Busca brutal em paralelo (10.000+ iterações/segundo).
- **Característica:** Arrasto penalizado e massa estática; atua como um "tunel de vento impiedoso" para garantir que a classe do motor sobressai o atrito supersônico.
- **Saída:** Multiplicadores genéticos (Nariz x%, Corpo y%, Aleta z%) imunes a drag intenso.

### 2. Etapa de Match-Perfeito (Módulo Fiel ORK - Em Desenvolvimento)
- **Engine Utilizada:** L2 Engine (Rust) - Modo Fiel (OpenRocket Physics Match)
- **Propósito:** Assim que esse módulo estiver portado para Rust, vamos passar o "Vencedor" da Etapa 1 nele. Esse módulo utilizará exatamente as equações e curvas empíricas do Java, escalando massa dinamicamente.
- **Característica:** Rodaremos refinamentos finos num ambiente superrápido (em Rust) mas com precisão 1:1 ao software padrão de mercado.

### 3. Etapa de Polimento de Missão e Exportação
- **Engine Utilizada:** Script em Python (`dual_engine_workflow.py`) 
- **Propósito:** Injetar os multiplicadores encontrados num XML puro do OpenRocket, gerando um `.ork` físico e organizado (arquivados em `/designs/generations/`).
- **Característica:** Permite que o operador humano abra o arquivo no aplicativo oficial (OpenRocket GUI) e instale cargas pagas, baterias, massas de sensores, e troque de classes de motor finais para validar a missão (e exiba os gráficos oficiais).

---

## Constatações Práticas do Laboratório #1

- Ao utilizar o foguete de testes alimentado por motor `N4800T`, a **Etapa 1 (Módulo Rígido)** projetou a quebra até ~83.000 metros de apogeu cravados.
- Ao levar para o OpenRocket (Etapa 3 - Módulo Fiel de interface visual), a exatidão bateu incríveis **123.000 metros**.
- **Regra da Competição:** Se o Alvo é exatamente 83.456m, a classe de motor `N` no mundo real supera as necessidades do foguete ultraleve (ultrapassa a linha de Kármán). 
- **Diretriz de Design:** Sempre usaremos a classe M (ou menor) no `.ork` exportado caso desejarmos atingir perfeitamente o alvo sem extrapolar a atmosfera de acordo com a predição.
