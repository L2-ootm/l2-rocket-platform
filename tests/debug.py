import re

with open('l2_engine/src/mission_adapter.rs', 'r', encoding='utf-8') as f:
    code = f.read()

debug_print = '''        let aero = barrowman::compute_aero(&active_stages, static_cg, roughness_m)?;
        println!("Stage {} Drag CD at Mach 1.0: {:?}", stage.name, aero.cd_table.iter().find(|(m, _)| (*m - 1.0).abs() < 1e-3));
        println!("Stage {} ref_area: {}", stage.name, aero.reference_area);
'''
code = code.replace('let aero = barrowman::compute_aero(&active_stages, static_cg, roughness_m)?;\n', debug_print)

with open('l2_engine/src/mission_adapter.rs', 'w', encoding='utf-8') as f:
    f.write(code)

print("Debug added")
