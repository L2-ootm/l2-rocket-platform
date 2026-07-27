import re

with open('l2_engine/src/mission_adapter.rs', 'r', encoding='utf-8') as f:
    code = f.read()

debug_print = '''        let dry_mass = mass;
        println!("Stage {} Drag CD at Mach 5.0: {:?}", stage.name, aero.cd_table.iter().find(|(m, _)| (*m - 5.0).abs() < 1e-3));
'''
code = code.replace('let dry_mass = mass;\n', debug_print)

with open('l2_engine/src/mission_adapter.rs', 'w', encoding='utf-8') as f:
    f.write(code)

