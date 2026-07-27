import xml.etree.ElementTree as ET
import re

tree = ET.parse('l2_engine/tests/fixtures/ork_extracted/rocket.ork')
root = tree.getroot()

for data in root.iter('data'):
    lines = data.text.strip().split('\n')
    if lines:
        first_line = lines[0].split(',')
        print(f'Data columns: {len(first_line)}')
        # Let us print the first line to see the mass
        print(f'First row values: {first_line}')
        break
