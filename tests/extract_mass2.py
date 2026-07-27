import xml.etree.ElementTree as ET

tree = ET.parse('l2_engine/tests/fixtures/ork_extracted/rocket.ork')
root = tree.getroot()

for databranch in root.iter('databranch'):
    print(f"Branch: {databranch.attrib.get('name')}")
    data = databranch.find('data')
    if data is not None and data.text:
        lines = data.text.strip().split('\n')
        if lines:
            row = lines[0].split(',')
            print(f"Row length: {len(row)}")
            if len(row) > 21:
                print(f"Massa (index 21): {row[21]} kg")
            if len(row) > 22:
                print(f"Prop Massa (index 22): {row[22]} kg")
