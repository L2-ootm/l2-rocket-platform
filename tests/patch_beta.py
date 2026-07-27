import re

with open('l2_engine/src/barrowman.rs', 'r', encoding='utf-8') as f:
    code = f.read()

# Fix fin wave drag
code = code.replace('let cd_wave_per_fin_area = 4.0 * t_over_c.powi(2) / beta;', 'let cd_wave_per_fin_area = 4.0 * t_over_c.powi(2);')

# Wait, nose wave drag was removed and replaced by nose_pressure_cd!
# In nose_pressure_cd, it uses VON_KARMAN_TABLE!
# VON_KARMAN_TABLE has values up to Mach 3.0.
# At Mach 3.0, value is 0.083. It is clamped there for all higher Machs.
# So nose wave drag is CONSTANT above Mach 3.0!
# It does NOT drop to zero!
