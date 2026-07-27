import glob
import re

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # .propellant_mass -> .propellant_mass() except where it's already a method call
    content = re.sub(r'\.propellant_mass(?!\()', '.propellant_mass()', content)
    
    # .isp -> .motors.first().map(|m| m.isp).unwrap_or(0.0)
    # wait, this is getting complex for inline replacements.
    # It's better to just write the replacement literally or add methods.
    
    # Let's replace stage.isp with stage.motors.first().map(|m| m.isp).unwrap_or(0.0)
    content = re.sub(r'stage\.isp\b', 'stage.motors.first().map(|m| m.isp).unwrap_or(0.0)', content)
    
    # .ignition_delay
    content = re.sub(r'(stage|first|s)\.ignition_delay\b', r'\1.motors.first().map(|m| m.ignition_delay).unwrap_or(0.0)', content)
    
    # .thrust_curve
    # .thrust_curve.is_empty() -> .motors.first().map_or(true, |m| m.thrust_curve.is_empty())
    content = re.sub(r'(stage|s)\.thrust_curve\.is_empty\(\)', r'\1.motors.first().map_or(true, |m| m.thrust_curve.is_empty())', content)
    
    # .thrust_curve.last() -> .motors.first().and_then(|m| m.thrust_curve.last())
    content = re.sub(r'(stage|s)\.thrust_curve\.last\(\)', r'\1.motors.first().and_then(|m| m.thrust_curve.last())', content)
    
    # &stage.thrust_curve -> &stage.motors.first().unwrap().thrust_curve
    content = re.sub(r'&(stage|s)\.thrust_curve\b', r'&\1.motors.first().unwrap().thrust_curve', content)

    # stage.thrust_curve -> stage.motors.first().unwrap().thrust_curve
    content = re.sub(r'(stage|s)\.thrust_curve\b', r'\1.motors.first().unwrap().thrust_curve', content)

    with open(filepath, 'w') as f:
        f.write(content)

for file in glob.glob('l2_engine/src/**/*.rs', recursive=True):
    if file.endswith('stage.rs'):
        continue
    fix_file(file)
