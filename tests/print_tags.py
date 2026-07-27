import xml.etree.ElementTree as ET

tree = ET.parse('l2_engine/tests/fixtures/ork_extracted/rocket.ork')
root = tree.getroot()

tags = set()
for elem in root.iter():
    tags.add(elem.tag)

print(sorted(list(tags)))
