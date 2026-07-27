import zipfile
import os
import sys
import xml.etree.ElementTree as ET

def patch_ork(input_zip, output_zip, wind_csv):
    import csv
    wind_levels = []
    with open(wind_csv, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith('#') or row[0].startswith('alt'):
                continue
            wind_levels.append((float(row[0]), float(row[1]), float(row[2]), float(row[3])))

    # Read the XML from the input ZIP
    with zipfile.ZipFile(input_zip, 'r') as zf:
        xml_content = zf.read('rocket.ork').decode('utf-8')

    root = ET.fromstring(xml_content)
    
    # Find the simulation conditions
    simulations = root.find('simulations')
    if simulations is None:
        simulations = ET.SubElement(root, 'simulations')
    
    for sim in simulations.findall('simulation'):
        conditions = sim.find('conditions')
        if conditions is None:
            conditions = ET.SubElement(sim, 'conditions')
            
        # Remove old wind elements
        for el in list(conditions):
            if el.tag in ['windaverage', 'winddirection', 'windturbulence']:
                conditions.remove(el)
            
        # Add OpenRocket 24.12 wind model
        wind_avg = ET.SubElement(conditions, 'wind', {'model': 'average'})
        ET.SubElement(wind_avg, 'speed').text = str(wind_levels[0][1])
        ET.SubElement(wind_avg, 'direction').text = str(wind_levels[0][2])
        ET.SubElement(wind_avg, 'standarddeviation').text = str(wind_levels[0][3])
        
        wind_ml = ET.SubElement(conditions, 'wind', {'model': 'multilevel', 'altituderef': 'agl'})
        for alt, speed, dir, std in wind_levels:
            ET.SubElement(wind_ml, 'windlevel', {'altitude': str(alt), 'speed': str(speed), 'direction': str(dir), 'standarddeviation': str(std)})
            
        ET.SubElement(conditions, 'windmodeltype').text = 'multilevel'
        
        # Modify atmosphere
        atm = conditions.find('atmosphere')
        if atm is not None:
            atm.set('model', 'extendedisa')
            for el in list(atm):
                if el.tag in ['basetemperature', 'basepressure', 'baserelativehumidity']:
                    atm.remove(el)
            # Add OSIFOG atmosphere parameters
            ET.SubElement(atm, 'basetemperature').text = "303.25"
            ET.SubElement(atm, 'basepressure').text = "100000.0"
            ET.SubElement(atm, 'baserelativehumidity').text = "0.82"

    new_xml = ET.tostring(root, encoding='unicode')
    new_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + new_xml

    os.makedirs(os.path.dirname(output_zip), exist_ok=True)
    with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('rocket.ork', new_xml.encode('utf-8'))
    print(f"Patched ORK saved to {output_zip}")

if __name__ == "__main__":
    patch_ork("designs/organic/precision_polished_elite.ork", "designs/osifog_level3/falcon_winner.ork", "OSIFOG/OpenWind_File.csv")
