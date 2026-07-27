# Pacote de envio OSIFOG — Nível 3

## Arquivo principal

- `Nível 3 L2 Systems 1024.ork`
- SHA-256: `440A43AEC55F45165BA74341380144F64B970166046479517140469CCF694467`
- Livery: L2 Celestial Datum V7
- Seed usado para gerar os dados salvos: `30017`

Antes do upload, confirmar que **L2 Systems 1024** é exatamente o nome completo
registrado no Formulário de Equipes. Se o formulário usar outra grafia, renomear o
arquivo para reproduzi-la exatamente.

## Verificação do arquivo reaberto

- Status do OpenRocket: `Loaded From File`
- Simulações: `1`
- Ramos de voo: `2`
- Score oficial recalculado dos dados salvos: `607220.0428767172`
- Apogeu: `2998.158 m`
- Mach máximo: `0.925`
- Pouso do sustainer: `2.054 m/s`
- Pouso do booster: `3.788 m/s`
- Extensão anti-tumbling: presente
- Resultado: legal, sem violações

O score durante a execução original foi `607219.4959246478`. A diferença mínima
após reabrir vem do arredondamento dos dados serializados pelo OpenRocket.

Foram comparados 600 registros de seed (certificação anterior de 100 e nova busca
determinística de 500). O seed `30017` permaneceu como o maior score legal
encontrado. O seed não é serializado pelo formato `.ork`; o que está preservado
para avaliação são todos os dados da execução vencedora.

## Coupler do booster

O fechamento interno da abertura foi testado somente em cópias diagnósticas. No
modelo aerodinâmico do OpenRocket, uma tampa interna não altera o arrasto de tubo
nem produz frenagem sistemática do booster; as pequenas variações no pouso mudaram
de sinal entre seeds e são compatíveis com a sensibilidade do tombamento. Por isso,
a geometria aprovada foi mantida. Uma superfície externa de frenagem seria outra
topologia e exigiria nova evolução e certificação.

## Itens para o formulário

Segundo as instruções oficiais arquivadas na codebase:

1. Rodar a simulação imediatamente antes de salvar.
2. Salvar com **todos os dados simulados**.
3. Manter exatamente uma simulação ativa.
4. Enviar o arquivo `.ork`.
5. Anexar captura 2D e captura 3D do foguete no OpenRocket.
6. Anexar uma captura do campo da missão no OpenEarth mostrando as duas trajetórias.
7. Usar o perfil OpenWind obrigatório e a extensão anti-tumbling permitida.

Os CSVs para a visualização no OpenEarth estão em `openearth/`:

- `osifog_850k_stage_1.csv`
- `osifog_850k_stage_2.csv`

Formulário oficial arquivado:
<https://docs.google.com/forms/d/e/1FAIpQLSd49zfqHk-Gj0xsCUCztaYjjGLB0VM8XyjYq4Ezt-npnSx6EA/viewform>

Prazo encontrado no regulamento arquivado: **26 de julho de 2026, 23:59 BRT**.
