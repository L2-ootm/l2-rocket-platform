import re

with open('l2_engine/src/mission_adapter.rs', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('let dry_mass = mass;', 'let dry_mass = mass + 0.4;') # add 0.4kg to each stage
with open('l2_engine/src/mission_adapter.rs', 'w', encoding='utf-8') as f:
    f.write(code)
