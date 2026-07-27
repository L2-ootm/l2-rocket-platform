"""Quick physics estimate: apogee by motor class, no ballast."""
import sys, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def estimate_apogee(imp_Ns, avg_thrust_N, launch_mass_motor_kg, motor_diam_m, payload_kg=0.3):
    burn_time = imp_Ns / avg_thrust_N
    prop_mass = imp_Ns / (200 * 9.81)
    dry_motor = launch_mass_motor_kg - prop_mass
    r = motor_diam_m / 2 + 0.001 + 0.0015
    body_len = max(burn_time * 0.5 + 0.10, 0.15)
    wall = 0.0015
    shell = 2 * math.pi * r * body_len * wall * 1780
    nose_mass = 2 * math.pi * r * (10 * r) * 0.0018 * 1780
    airframe_kg = shell + nose_mass + 0.15
    m1 = dry_motor + airframe_kg + payload_kg
    m0 = m1 + prop_mass
    dv = 200 * 9.81 * math.log(m0 / m1)
    dv_net = max(0, dv - 9.81 * burn_time * 0.5)
    apogee = dv_net**2 / (2 * 9.81)
    return apogee, m0, r * 2 * 1000

motors = [
    ('A', 'A8',           2.5,    8.0,  0.016, 0.018),
    ('B', 'B6',           5.0,    6.0,  0.024, 0.018),
    ('C', 'C6',          10.0,    6.0,  0.028, 0.018),
    ('D', 'D12',         17.0,   12.0,  0.040, 0.018),
    ('E', 'E30T-24mm',   33.7,   30.0,  0.047, 0.024),
    ('E', 'E18W-24mm',   38.2,   18.0,  0.057, 0.024),
    ('F', 'F39T-24mm',   49.7,   39.0,  0.059, 0.024),
    ('F', 'F27R-29mm',   49.5,   27.0,  0.080, 0.029),
    ('F', 'F52T-29mm',   73.0,   52.0,  0.121, 0.029),
    ('G', 'G71R-29mm',  120.0,   71.0,  0.121, 0.029),
    ('H', 'H133-29mm',  163.0,  133.0,  0.190, 0.029),
    ('H', 'H220T-29mm', 207.0,  220.0,  0.239, 0.029),
    ('I', 'I204-29mm',  348.0,  204.0,  0.349, 0.029),
    ('M', 'M2245-75mm', 9978.0, 2245.0, 8.182, 0.075),
    ('O', 'O8000-161mm',41125.0,8000.0,32.672, 0.161),
]

print('Class  Motor           Impulse    m0(launch)  OD       Apogee (no ballast)')
print('-' * 78)
for cls, name, imp, thr, lm, diam in motors:
    apogee, m0, od = estimate_apogee(imp, thr, lm, diam)
    print(f'{cls:5s}  {name:16s} {imp:8.0f}Ns  {m0:8.3f}kg  {od:5.1f}mm  {apogee:10.0f}m = {apogee/1000:7.3f}km')

print()
print('Ideal motor for 350m with NO ballast: look at D/E class.')
print('Ideal motor for 350m with small ballast: E/F class.')
print('H class (our current H133) needs ~785g ballast for 350m -- works but heavy.')
