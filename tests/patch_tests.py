import re

# Fix barrowman.rs
with open('l2_engine/src/barrowman.rs', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('compute_cd_table(&stage, 1e-6)', 'compute_cd_table_from_stages(&[&stage], 1e-6)')
code = code.replace('compute_aero(&sustainer, sustainer_cg, 1e-6)', 'compute_aero(&[&sustainer], sustainer_cg, 1e-6)')
code = code.replace('compute_aero(&booster, booster_cg, 1e-6)', 'compute_aero(&[&booster], booster_cg, 1e-6)')

with open('l2_engine/src/barrowman.rs', 'w', encoding='utf-8') as f:
    f.write(code)

# Fix main.rs
with open('l2_engine/src/main.rs', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace(', l2_engine::PhysicsMode::OpenRocketLegacy', '')
with open('l2_engine/src/main.rs', 'w', encoding='utf-8') as f:
    f.write(code)

# Fix validation.rs
with open('l2_engine/tests/validation.rs', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace(', l2_engine::PhysicsMode::OpenRocketLegacy', '')
with open('l2_engine/tests/validation.rs', 'w', encoding='utf-8') as f:
    f.write(code)

print("Tests patched")
