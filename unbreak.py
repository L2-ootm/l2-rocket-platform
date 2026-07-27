import glob
import re

def fix_errors(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Revert incorrect _kg replacements
    content = content.replace('.propellant_mass()_kg', '.propellant_mass_kg')
    
    # Fix stage.thrust_curve usages over multiple lines, e.g.
    #    .thrust_curve
    # This might be on StageBuilder or on Stage. If it's on StageBuilder, it's valid.
    # Oh wait, the error is `&Stage`.
    # Let's fix specific files individually to be safe.

    with open(filepath, 'w') as f:
        f.write(content)

for file in glob.glob('l2_engine/src/**/*.rs', recursive=True):
    fix_errors(file)
