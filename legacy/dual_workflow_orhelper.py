import os
import subprocess
import re
import zipfile
import xml.etree.ElementTree as ET
import orhelper
from orhelper import OpenRocketInstance
from pathlib import Path

def run_rust_optimization():
    print("[*] Running L2 Engine optimization in HyperReal mode...")
    cwd = os.path.join(os.getcwd(), "l2_engine")
    result = subprocess.run(
        ["cargo", "run", "--release", "--bin", "optimize"],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    if result.returncode != 0:
        print("Rust engine failed!")
        print(result.stderr)
        return None
        
    print(result.stdout)
    
    # parse the top 1: #1: Apogee 83.46 km (Err: 0.12m) | Mach 4.20 | Nose x1.2345 | Fins x0.9876 | Body x1.0123
    match = re.search(r'#1: Apogee.*?Nose x([0-9.]+)\s+\|\s+Fins x([0-9.]+)\s+\|\s+Body x([0-9.]+)', result.stdout)
    if match:
        nose_mult = float(match.group(1))
        fin_mult = float(match.group(2))
        body_mult = float(match.group(3))
        return (nose_mult, fin_mult, body_mult)
    else:
        print("Could not parse optimal multipliers from Rust output.")
        return None

def extract_xml(ork_path):
    # .ork files can be zip files containing either rocket.ork or rocket.xml
    try:
        with zipfile.ZipFile(ork_path, 'r') as z:
            first_file = z.namelist()[0]
            with z.open(first_file) as f:
                return f.read().decode('utf-8')
    except zipfile.BadZipFile:
        # If it's not a zip file, it's just raw XML
        with open(ork_path, 'r', encoding='utf-8') as f:
            return f.read()
    return None

def save_xml_to_ork(xml_string, out_ork_path):
    with zipfile.ZipFile(out_ork_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr("rocket.ork", xml_string)

def mutate_ork_file(seed_path, out_path, multipliers):
    xml_content = extract_xml(seed_path)
    if not xml_content:
        raise ValueError("No XML found in .ork file.")
        
    root = ET.fromstring(xml_content)
    nose_mult, fin_mult, body_mult = multipliers
    
    for body_tube in root.findall('.//bodytube'):
        length_elem = body_tube.find('length')
        if length_elem is not None:
            base_length = float(length_elem.text)
            length_elem.text = str(base_length * body_mult)
            
    for nose in root.findall('.//nosecone'):
        length_elem = nose.find('length')
        if length_elem is not None:
            base_length = float(length_elem.text)
            length_elem.text = str(base_length * nose_mult)
            
    for finset in root.findall('.//trapezoidfinset'):
        for tag in ['rootchord', 'tipchord', 'sweep', 'span']:
            elem = finset.find(tag)
            if elem is not None:
                base_val = float(elem.text)
                elem.text = str(base_val * fin_mult)
                
    xml_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    save_xml_to_ork(xml_bytes.decode('utf-8'), out_path)
    print(f"[*] Saved mutated rocket to {out_path}")

def run_openrocket_validation(ork_path):
    print("[*] Validating in OpenRocket via orhelper...")
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        doc = orh.load_doc(ork_path)
        sim = doc.getSimulations().get(0)
        print(f"[*] Executing Simulation: {sim.getName()}")
        orh.run_simulation(sim)
        data = sim.getSimulatedData()
        
        apogee = data.getMaxAltitude()
        mach = data.getMaxMachNumber()
        
        print(f"--- OPENROCKET OFFICIAL VALIDATION ---")
        print(f"Apogee: {apogee:.2f} m")
        print(f"Max Mach: {mach:.2f}")

if __name__ == "__main__":
    best_config = run_rust_optimization()
    if best_config:
        print(f"[*] Optimal Config Found: Nose x{best_config[0]:.4f}, Fins x{best_config[1]:.4f}, Body x{best_config[2]:.4f}")
        
        os.makedirs("designs/optimized", exist_ok=True)
        out_ork = "designs/optimized/L2_Perfect_83K.ork"
        
        mutate_ork_file("L2_Apex_Hybrid.ork", out_ork, best_config)
        run_openrocket_validation(out_ork)
