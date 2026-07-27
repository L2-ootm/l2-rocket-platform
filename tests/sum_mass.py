import xml.etree.ElementTree as ET

tree = ET.parse('l2_engine/tests/fixtures/ork_extracted/rocket.ork')
root = tree.getroot()

total_mass = 0.0
for elem in root.iter():
    if elem.tag == 'mass':
        total_mass += float(elem.text)

print(f'Total explicitly defined <mass> elements sum to: {total_mass} kg')
