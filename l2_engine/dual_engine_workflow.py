import os
import zipfile
import re
import xml.etree.ElementTree as ET

# Workflow Script: L2 Engine (Rust) -> OpenRocket (.ork)
# 1. Read optimization result
# 2. Modify XML parameters
# 3. Zip back into .ork format

RESULT_FILE = 'optimize_goal_results.txt'
BASE_ORK = '../designs/optimized/L2_Hyper_Parallel_15K_Fixed.ork'
OUT_ORK = 'L2_Optimized_83456.ork'

def main():
    if not os.path.exists(RESULT_FILE):
        print(f"Error: {RESULT_FILE} not found.")
        return

    # Extract Top 1 Multipliers
    nose_m = 1.0
    fin_m = 1.0
    body_m = 1.0
    
    with open(RESULT_FILE, 'r') as f:
        content = f.read()
        # Find "#1: Apogee ... | Nose x1.23 | Fins x0.45 | Body x0.67"
        match = re.search(r'#1:.*?Nose x([\d\.]+).*?Fins x([\d\.]+).*?Body x([\d\.]+)', content)
        if match:
            nose_m = float(match.group(1))
            fin_m = float(match.group(2))
            body_m = float(match.group(3))
            print(f"Parsed Multipliers - Nose: {nose_m}, Fins: {fin_m}, Body: {body_m}")
        else:
            print("Could not find Top 1 in results. Using 1.0 defaults.")

    # Unzip ORK
    print("Extracting base .ork...")
    with zipfile.ZipFile(BASE_ORK, 'r') as zin:
        xml_data = zin.read('rocket.ork')

    # Modify XML
    print("Modifying rocket geometry...")
    root = ET.fromstring(xml_data)

    def multiply_tag(parent, tag_name, multiplier):
        elem = parent.find(tag_name)
        if elem is not None and elem.text:
            try:
                val = float(elem.text)
                elem.text = str(val * multiplier)
            except ValueError:
                pass

    for comp in root.iter('rocketcomponent'):
        ctype = comp.get('type')
        if ctype == 'NoseCone':
            multiply_tag(comp, 'length', nose_m)
        elif ctype == 'BodyTube':
            multiply_tag(comp, 'length', body_m)
        elif ctype in ('TrapezoidFinSet', 'EllipticalFinSet', 'FreeformFinSet'):
            multiply_tag(comp, 'rootchord', fin_m)
            multiply_tag(comp, 'tipchord', fin_m)
            multiply_tag(comp, 'sweeplength', fin_m)
            multiply_tag(comp, 'span', fin_m)

    new_xml_data = ET.tostring(root, encoding='utf-8', xml_declaration=True)

    # Zip back
    print(f"Saving to {OUT_ORK}...")
    with zipfile.ZipFile(OUT_ORK, 'w', zipfile.ZIP_DEFLATED) as zout:
        zout.writestr('rocket.ork', new_xml_data)

    print("Workflow step complete. .ork file generated successfully.")

if __name__ == '__main__':
    main()
