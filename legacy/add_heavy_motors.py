import os
import re

with open("rocket_forge.py", "r", encoding="utf-8") as f:
    content = f.read()

new_motors = """    ("Cesaroni Technology", "K510",  0.054, 0.488, 12.0, None),
    # Classe L (2560-5120 Ns)
    ("AeroTech", "L1000", 0.054, 0.635, 14.0, None),
    ("AeroTech", "L1150", 0.075, 0.531, 14.0, None),
    ("AeroTech", "L1500T", 0.098, 0.443, 14.0, None),
    ("AeroTech", "L2200G", 0.075, 0.665, 14.0, None),
    # Classe M (5120-10240 Ns)
    ("AeroTech", "M1939W", 0.098, 0.732, 14.0, None),
    ("AeroTech", "M2500T", 0.098, 0.751, 14.0, None),
    ("AeroTech", "M650W", 0.075, 0.801, 14.0, None),
    ("AeroTech", "M1297W", 0.075, 0.665, 14.0, None),
    # Classe N (10240-20480 Ns) - MONSTROS
    ("AeroTech", "N2000W", 0.098, 1.046, 14.0, None),
    ("AeroTech", "N4800T", 0.098, 1.194, 14.0, None),
"""

content = content.replace('    ("Cesaroni Technology", "K510",  0.054, 0.488, 12.0, None),', new_motors)

with open("rocket_forge.py", "w", encoding="utf-8") as f:
    f.write(content)
