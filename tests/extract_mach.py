import xml.etree.ElementTree as ET

tree = ET.parse('l2_engine/tests/fixtures/ork_extracted/rocket.ork')
root = tree.getroot()

for databranch in root.iter('databranch'):
    if databranch.attrib.get('name') == 'Sustainer':
        data = databranch.find('data')
        if data is not None and data.text:
            lines = data.text.strip().split('\n')
            max_mach = 0.0
            for line in lines:
                row = line.split(',')
                if len(row) > 52: # Mach is at index 52 based on types string
                    try:
                        mach = float(row[52])
                        if mach > max_mach:
                            max_mach = mach
                    except:
                        pass
            print(f'Max Mach: {max_mach}')
        else:
            print('No data text found')
