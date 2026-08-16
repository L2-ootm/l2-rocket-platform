# Dia D - Protocolo de Julho 19

O dia em que o Edital da Missão Secreta é revelado. Esta é a rotina de execução instantânea para o deploy do Motor L2.

- [ ] **08:00** - Ler a Missão Secreta.
- [ ] **08:15** - Extrair a **Equação de Pontuação Matemática**.
- [ ] **08:30** - Converter a equação da OSIFOG em código Python e injetá-la na rotina `evaluate_fitness` do arquivo `genetic_engine.py`.
- [ ] **08:45** - Extrair o Motor Estático Obrigatório (ex: C6-5) e Payload exigidos, e repassá-los para os parâmetros `--motor` e `--payload`.
- [ ] **08:50** - Extrair os parâmetros geográficos (rugosidade, hora solar) para o perfil de vento e configurar as variáveis globais da Espiral de Ekman no nosso simulador de vento customizado.
- [ ] **09:00** - Executar o Orquestrador L2:
  ```powershell
  .\venv\Scripts\python genetic_engine.py --apogee [META_ALVO] --motor [MOTOR_EDITAL] --payload [MASSA_EDITAL]
  ```
- [ ] **09:30** - Extrair o arquivo `.ork` do espécime perfeito gerado (Geração 100).
- [ ] **09:40** - Extrair o log de simulação `.csv` do melhor projeto.
- [ ] **09:50** - Submeter ao OpenEarth OSIFOG para renderização final e envio do relatório oficial da competição.
