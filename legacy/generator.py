import zipfile
import xml.etree.ElementTree as ET
import shutil
import os

def generate_variant(template_path, output_path, new_mass, new_fin_span):
    """
    Reads an OpenRocket (.ork) file, modifies parameters in the XML,
    and saves it as a new variant.
    """
    temp_dir = "temp_ork_extraction"
    os.makedirs(temp_dir, exist_ok=True)
    
    # .ork files are just zipped XML files. Extract it.
    with zipfile.ZipFile(template_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)
    
    xml_file = os.path.join(temp_dir, 'rocket.ork')
    if not os.path.exists(xml_file):
        # Older versions or different configs might name it differently
        for file in os.listdir(temp_dir):
            if file.endswith('.xml') or file.endswith('.ork'):
                xml_file = os.path.join(temp_dir, file)
                break
                
    # Parse and modify XML
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    # Example Modification 1: Change a mass component
    # (Assuming there's a mass component; XPaths depend on actual OpenRocket structure)
    for mass_cmp in root.findall(".//masscomponent/mass"):
        # L2 MIND: Just a proof of concept modification
        mass_cmp.text = str(new_mass)
        
    # Example Modification 2: Change fin span
    for fin in root.findall(".//freeformfinset"):
        # modify geometric points or span tags here
        pass

    tree.write(xml_file)
    
    # Re-zip it
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zip_ref:
        for root_dir, _, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root_dir, file)
                zip_ref.write(file_path, arcname=file)
                
    # Clean up
    shutil.rmtree(temp_dir)
    print(f"[*] Generated variant: {output_path} (Mass: {new_mass})")

if __name__ == "__main__":
    print("L2-OSIFOG Procedural Generator")
    print("------------------------------")
    print("Waiting for template.ork to be created...")
    # Example usage:
    # generate_variant("template.ork", "variants/rocket_v1.ork", new_mass=0.5, new_fin_span=0.1)
