# L2 OSIFOG - Relatório Técnico e Decisões de Engenharia

Este documento compila as decisões arquiteturais, limites físicos e métricas de execução do projeto **L2 OSIFOG**, onde automatizamos o design aeroespacial usando Inteligência Artificial Genética e o simulador OpenRocket.

## 1. Decisões Estratégicas e Arquiteturais
* **Automação por Algoritmos Genéticos (GA):** Em vez de simulações de força-bruta (testar todas as combinações possíveis, o que levaria anos) ou projeto manual iterativo, a L2 Systems optou por um Algoritmo Genético. O script muta diâmetros, comprimentos, envergadura de aletas e atraso de estágios (delay) simulando evolução natural.
* **Processamento Paralelo em JVMs Distribuídas:** Como o OpenRocket (`orhelper`) não é *thread-safe* por padrão no Python, foi implementada uma arquitetura com `ProcessPoolExecutor` onde cada *worker* levanta sua própria Máquina Virtual Java (JVM) isolada. Isso permitiu pular de 1 simulação por segundo para até 8-10 simulações simultâneas.
* **Trava de Segurança de Memória (OOM Prevention):** O processamento de simulações multi-stage com motores classe N exauriu a memória RAM do sistema devido à sobrecarga de múltiplas JVMs. A decisão foi fixar o limite em 4 *workers* simultâneos, garantindo 100% de estabilidade até a 50ª geração.

## 2. Decisões de Engenharia Física e Aerodinâmica
* **Sanity Checks Estruturais (Anti-Cheat):** O Algoritmo Genético tentou "trapacear" as leis da física reduzindo o tubo externo a 40cm para economizar peso, mesmo abrigando um motor de 1.2m, resultando num motor exposto. A engine `rocket_forge.py` foi reprogramada com *sanity limits*: o tubo externo agora possui uma amarra matemática para **nunca** ser menor que o motor interno.
* **Perfil Von Kármán:** O nariz foi travado matematicamente no formato **Von Kármán (Haack series)**, o formato otimizado cientificamente para gerar o menor arrasto de onda no regime supersônico.
* **Upgrade de Materiais (Carbon Fiber):** Para alcançar a meta de 40km+ sem quebrar as leis físicas, a fuselagem e aletas que antes eram de Fibra de Vidro (Fiberglass) foram transferidas obrigatoriamente para **Fibra de Carbono (Carbon Fiber)**, cortando a massa inercial drasticamente.
* **Aerofólio Forçado:** Todas as aletas do Booster e Sustainer foram configuradas para seções transversais do tipo **Airfoil**, eliminando o arrasto de quinas retas comum em simulações geradas aleatoriamente.
* **Polimento Extremo:** A variável `<finish>` foi definida globalmente como `polished`, baixando a altura da rugosidade ao nível microscópico e minimizando o *skin friction drag* em altas velocidades.
* **Estágios e Motores (Classe N):** Liberação do motor **Aerotech N4800T** e **N2000W** para os estágios pesados, e otimização milimétrica do atraso de separação (staging delay), encontrando o *sweet spot* de **2.5 segundos** antes da ignição do Sustainer.

## 3. Função de Fitness
A Função de Recompensa (Fitness Function) evoluiu para se tornar um multiplicador agressivo:
```python
score = apogee + (mach * 5000)
if apogee > 40000: score += 100000
if mach > 5.5: score += 100000
```
Isso forçou a IA a rejeitar foguetes lentos e focar inteiramente em veículos hipersônicos suborbitais.

## 4. Volume de Processamento Computacional
Desde o início da concepção da L2 OSIFOG, o algoritmo realizou um número massivo de testes e descartes de foguetes disfuncionais. Abaixo a estimativa histórica do processamento:

* **Fase 1 (Testes Unitários & Single-Stage base):** ~500 simulações
* **Fase 2 (Evolução Single-Stage "Mega"):** 100 gerações × 32 foguetes = 3.200 simulações
* **Fase 3 (Alpha Multi-Stage & Debugs):** ~800 simulações
* **Fase 4 (Multi-Stage Paralelo V1 - Bug de Física):** 30 gerações × 32 foguetes = 960 simulações
* **Fase 5 (Multi-Stage Paralelo V2 - Físicas Corrigidas):** 30 gerações × 32 foguetes = 960 simulações
* **Fase 6 (A Caçada por Mach 6 - Otimização Extrema):** 50 gerações × 32 foguetes = 1.600 simulações

**Total Geral Aproximado:** O Laboratório Virtual da L2 Systems gerou, compilou XML, enviou para o motor Java do OpenRocket, analisou a telemetria, calculou o *fitness* e matou / reproduziu **aproximadamente 8.020 foguetes distintos**.

*O Campeão atual (L2_Hyper_Parallel_15K) atingiu **70.127m** e **Mach 5.63**, colocando a equipe em um patamar projetual de nível agência espacial profissional.*
