import re
import glob

def patch_stage_literals(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # We need to replace the fields in Stage literal with motors: vec![MotorBurn { ... }]
    # We have 5 fields to extract: propellant_mass, thrust, isp, thrust_curve, ignition_delay
    
    # We'll use a regex to find Stage { ... } block
    def replacer(m):
        block = m.group(0)
        
        # Extract fields
        pm = re.search(r'propellant_mass:\s*([^,]+),', block)
        th = re.search(r'thrust:\s*([^,]+),', block)
        isp = re.search(r'isp:\s*([^,]+),', block)
        tc = re.search(r'thrust_curve:\s*(vec!\[.*?\]),', block, flags=re.DOTALL)
        if not tc:
            tc = re.search(r'thrust_curve:\s*([^,]+),', block)
        ig = re.search(r'ignition_delay:\s*([^,]+),', block)
        
        if not pm or not th or not isp:
            return block
            
        motor_burn = f"""motors: vec![crate::sim_core::vehicle::MotorBurn {{
                    propellant_mass: {pm.group(1)},
                    thrust: {th.group(1)},
                    isp: {isp.group(1)},
                    thrust_curve: {tc.group(1) if tc else 'vec![]'},
                    ignition_delay: {ig.group(1) if ig else '0.0'},
                }}],"""
                
        # Remove original fields
        block = re.sub(r'propellant_mass:\s*[^,]+,\s*', '', block)
        block = re.sub(r'thrust:\s*[^,]+,\s*', '', block)
        block = re.sub(r'isp:\s*[^,]+,\s*', '', block)
        block = re.sub(r'thrust_curve:\s*vec!\[.*?\],\s*', '', block, flags=re.DOTALL)
        block = re.sub(r'thrust_curve:\s*[^,]+,\s*', '', block)
        block = re.sub(r'ignition_delay:\s*[^,]+,\s*', '', block)
        
        # Insert motor_burn right after dry_mass
        block = re.sub(r'(dry_mass:\s*[^,]+,)', r'\1\n                ' + motor_burn, block)
        
        return block

    # We find Stage { ... } or crate::sim_core::vehicle::Stage { ... }
    new_content = re.sub(r'Stage\s*\{.*?(?=\s*\}\s*])', replacer, content, flags=re.DOTALL)
    new_content = re.sub(r'Stage\s*\{.*?(?=\s*\}\s*\})', replacer, new_content, flags=re.DOTALL)

    # For runner.rs test 3
    new_content = new_content.replace('stage.propellant_mass() = 1.0;', 'stage.motors[0].propellant_mass = 1.0;')
    new_content = new_content.replace('stage.thrust = 10.0;', 'stage.motors[0].thrust = 10.0;')
    
    # sixdof.rs test 3
    new_content = new_content.replace('m.stages[0].ignition_delay = 2.5;', 'm.stages[0].motors[0].ignition_delay = 2.5;')

    if content != new_content:
        with open(filepath, 'w') as f:
            f.write(new_content)

for file in glob.glob('l2_engine/src/**/*.rs', recursive=True):
    patch_stage_literals(file)
