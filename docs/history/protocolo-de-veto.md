# Protocolo de Veto (The Sudden Death Criteria)

Este arquivo define as *Leis Imutáveis* da simulação. A Função Fitness do Algoritmo Genético deve aplicar estes filtros ABSOLUTOS ANTES de qualquer cálculo de otimização de apogeu ou pontuação.

## 1. Veto de Estabilidade (SM - Stability Margin)
Qualquer foguete cuja margem de estabilidade na decolagem ou durante a queima caia fora da janela segura é **descartado imediatamente**.
* **Morte por Instabilidade:** $SM < 1.5$ (Foguete gira incontrolavelmente, risco de trajetória balística não-previsível).
* **Morte por Excesso de Estabilidade:** $SM > 3.0$ (Foguete vira rapidamente contra o vento - *weathercocking* severo - destruindo o apogeu e aumentando o arrasto).

## 2. Veto da Haste de Lançamento
* **Morte Lenta:** Velocidade ao sair da haste (rod clearance velocity) $< 15$ m/s. 
Se a velocidade for menor que 15 m/s, as aletas não possuem fluxo de ar suficiente para gerar sustentação de correção. A simulação será vetada.

## 3. Veto da Missão Secreta
* Se o apogeu, massa ou tempo de voo estiverem fora das tolerâncias matemáticas da equação fornecida no dia 19 de Julho, o design é vetado (pontuação = 0).

---
*Nota L2 MIND: Falhas operacionais em simulação previnem falhas operacionais na vida real. Zero tolerância para designs que quebram as Leis Imutáveis.*
