import re

with open('l2_engine/src/mission_adapter.rs', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('dry_mass.mass_kg', 'dry_mass')

with open('l2_engine/src/mission_adapter.rs', 'w', encoding='utf-8') as f:
    f.write(code)

