"""Regenera os foguetes campeões com os digests corretos."""
import json
from rocket_forge import RocketArchitect, MOTOR_DATABASE

def find_motor_index(designation):
    for i, m in enumerate(MOTOR_DATABASE):
        if m[1] == designation:
            return i
    return None

architect = RocketArchitect()

# Vertical
with open('designs/optimized/L2_Forge_Vertical_2500_params.json') as f:
    data = json.load(f)
params = data['params']
# O foguete original usava K1050W
params['motor_index'] = find_motor_index('K1050W')
architect.save(params, 'designs/optimized/L2_Forge_Vertical_2500.ork')
motor = MOTOR_DATABASE[params['motor_index']]
print(f"[!] Vertical regenerado: {motor[1]} digest={motor[5]}")

# Atualiza o JSON também
data['params'] = params
with open('designs/optimized/L2_Forge_Vertical_2500_params.json', 'w') as f:
    json.dump(data, f, indent=2)

# 45 graus
try:
    with open('designs/optimized/L2_Forge_45Deg_2500_params.json') as f:
        data = json.load(f)
    params = data['params']
    # Busca pelo nome do motor que foi salvo nos métricas
    # O 45 graus usava K510
    params['motor_index'] = find_motor_index('K510')
    architect.save(params, 'designs/optimized/L2_Forge_45Deg_2500.ork')
    motor = MOTOR_DATABASE[params['motor_index']]
    print(f"[!] 45 graus regenerado: {motor[1]} digest={motor[5]}")
    data['params'] = params
    with open('designs/optimized/L2_Forge_45Deg_2500_params.json', 'w') as f:
        json.dump(data, f, indent=2)
except FileNotFoundError:
    print("[!] 45 graus não encontrado, será regenerado na próxima evolução")
