#!/usr/bin/env python3
"""Analyze the illegal 850k ORK to extract its structure."""
import zipfile, xml.etree.ElementTree as ET, json, sys

path = 'designs/osifog_level3/osifog_850k_falcon.ork'
with zipfile.ZipFile(path) as z:
    xml_data = z.read('rocket.ork').decode('utf-8')
    root = ET.fromstring(xml_data)

def find_all(elem, tag):
    results = []
    if elem.tag == tag:
        results.append(elem)
    for child in elem:
        results.extend(find_all(child, tag))
    return results

# Extract key components
stages = root.findall('.//stage')
for stage in stages:
    name = stage.findtext('name')
    print(f'\n=== STAGE: {name} ===')

    # Body tube
    for bt in stage.findall('.//bodytube'):
        bt_name = bt.findtext('name', '')
        length = bt.findtext('length', '?')
        radius = bt.findtext('radius', '?')
        thickness = bt.findtext('thickness', '?')
        mat = bt.find('material')
        mat_text = mat.text if mat is not None else '?'
        print(f'  BodyTube "{bt_name}": length={length}, radius={radius}, thickness={thickness}, material={mat_text}')

    # Nose cone
    for nc in stage.findall('.//nosecone'):
        length = nc.findtext('length', '?')
        shape = nc.findtext('shape', '?')
        aftradius = nc.findtext('aftradius', '?')
        print(f'  NoseCone: length={length}, shape={shape}, aftradius={aftradius}')

    # Motor configuration
    for mc in find_all(stage, 'motorconfiguration'):
        motor_id = mc.findtext('motorid', '?')
        delay = mc.findtext('delay', '?')
        ignition = mc.findtext('ignitionevent', '?')
        configid = mc.get('configid', '?')
        print(f'  MotorConfig: motorid={motor_id}, delay={delay}, ignition={ignition}')

    # Fins
    for fin in stage.findall('.//fin'):
        fin_name = fin.findtext('name', '?')
        rootchord = fin.findtext('rootchord', '?')
        tipchord = fin.findtext('tipchord', '?')
        height = fin.findtext('height', '?')
        sweep = fin.findtext('sweeplength', '?')
        finscount = fin.findtext('fins', '?')
        pos = fin.find('position')
        pos_text = f'type={pos.get("type")}, value={pos.text}' if pos is not None else '?'
        mat = fin.find('material')
        mat_text = mat.text if mat is not None else '?'
        print(f'  Fin "{fin_name}": count={finscount}, root={rootchord}, tip={tipchord}, height={height}, sweep={sweep}, material={mat_text}')
        print(f'    Position: {pos_text}')

    # Inner tubes (motor mounts)
    for it in stage.findall('.//innertube'):
        it_name = it.findtext('name', '?')
        length = it.findtext('length', '?')
        radius = it.findtext('radius', '?')
        thickness = it.findtext('thickness', '?')
        pos = it.find('position')
        pos_text = f'type={pos.get("type")}, value={pos.text}' if pos is not None else '?'
        mat = it.find('material')
        mat_text = mat.text if mat is not None else '?'
        cluster = it.findtext('clusterconfiguration', '?')
        clusterscale = it.findtext('clusterscale', '?')
        clusterrotation = it.findtext('clusterrotation', '?')
        print(f'  InnerTube "{it_name}": length={length}, radius={radius}, thickness={thickness}, material={mat_text}')
        print(f'    Position: {pos_text}, cluster={cluster}, scale={clusterscale}, rotation={clusterrotation}')

    # Bulkheads
    for bh in stage.findall('.//bulkhead'):
        bh_name = bh.findtext('name', '?')
        length = bh.findtext('length', '?')
        outerradius = bh.findtext('outerradius', '?')
        pos = bh.find('position')
        pos_text = f'type={pos.get("type")}, value={pos.text}' if pos is not None else '?'
        mat = bh.find('material')
        mat_text = mat.text if mat is not None else '?'
        print(f'  Bulkhead "{bh_name}": length={length}, outerradius={outerradius}, material={mat_text}')
        print(f'    Position: {pos_text}')

    # Centering rings
    for cr in stage.findall('.//centeringring'):
        cr_name = cr.findtext('name', '?')
        pos = cr.find('position')
        pos_text = f'type={pos.get("type")}, value={pos.text}' if pos is not None else '?'
        length = cr.findtext('length', '?')
        outerradius = cr.findtext('outerradius', '?')
        innerradius = cr.findtext('innerradius', '?')
        mat = cr.find('material')
        mat_text = mat.text if mat is not None else '?'
        print(f'  CenteringRing "{cr_name}": length={length}, outer={outerradius}, inner={innerradius}, material={mat_text}')
        print(f'    Position: {pos_text}')

# Simulation conditions
sims = root.findall('.//simulation')
for sim in sims:
    name = sim.findtext('name', '?')
    print(f'\n=== SIMULATION: {name} ===')
    cond = sim.find('.//conditions')
    if cond is not None:
        for child in cond:
            if child.text and child.text.strip():
                print(f'  {child.tag}: {child.text.strip()[:80]}')

# Also print the full XML for reference (just structure, not content)
print('\n=== FULL XML STRUCTURE (tags only) ===')
def print_tag_tree(elem, indent=0, max_depth=5):
    if indent > max_depth:
        return
    tag = elem.tag
    text = elem.text.strip() if elem.text and elem.text.strip() else ''
    attribs = dict(elem.attrib)
    prefix = '  ' * indent
    info = ''
    if text:
        info = f': {text[:60]}'
    elif attribs:
        info = f' {json.dumps(attribs)[:80]}'
    print(f'{prefix}{tag}{info}')
    for child in elem:
        print_tag_tree(child, indent+1, max_depth)

print_tag_tree(root, 0, 6)
