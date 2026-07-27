import re

with open('l2_engine/tests/validation.rs', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('let apogee_err', 'println!("Max Mach: {}", summary.max_mach);\n      let apogee_err')

with open('l2_engine/tests/validation.rs', 'w', encoding='utf-8') as f:
    f.write(code)

