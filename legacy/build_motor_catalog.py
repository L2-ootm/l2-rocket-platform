"""Fetch best motor candidates from each class, with digests. Outputs a curated catalog."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import orhelper
from orhelper import OpenRocketInstance

with OpenRocketInstance('lib/OpenRocket-.jar') as inst:
    import jpype
    Application = jpype.JClass('net.sf.openrocket.startup.Application')
    db = Application.getMotorSetDatabase()

    rows = []
    for ms in db.getMotorSets():
        for m in ms.getMotors():
            imp  = float(m.getTotalImpulseEstimate())
            diam = float(m.getDiameter())
            mfr  = str(m.getManufacturer().getDisplayName())
            des  = str(m.getDesignation())
            dig  = str(m.getDigest())
            lm   = float(m.getLaunchMass())
            ln   = float(m.getLength())
            rows.append(dict(imp=imp, diam=diam, mfr=mfr, des=des, dig=dig, lm=lm, ln=ln))

    rows.sort(key=lambda r: r['imp'])

    # Curated candidates: best (highest impulse) motor per class per diameter
    # Focus on: 18mm, 24mm, 29mm, 38mm tubes — standard rocket sizes
    targets = [
        # (label, imp_min, imp_max, diam_max, note)
        ('E-tiny (18mm)',    20,  40, 0.019, '18mm tube — minimal rocket'),
        ('E-std  (24mm)',    20,  40, 0.025, '24mm tube — sport standard'),
        ('F-tiny (24mm)',    40,  80, 0.025, '24mm tube — powerful sport'),
        ('F-std  (29mm)',    40,  80, 0.030, '29mm tube — HPR minimum'),
        ('G-std  (29mm)',    80, 160, 0.030, '29mm tube — HPR standard'),
        ('G-38mm (38mm)',    80, 160, 0.039, '38mm tube — mid-power'),
        ('H-29mm (29mm)',   160, 320, 0.030, '29mm tube — HPR high'),
        ('H-38mm (38mm)',   160, 320, 0.039, '38mm tube — HPR high'),
        ('I-29mm (29mm)',   320, 640, 0.030, '29mm tube — competition HPR'),
        ('I-38mm (38mm)',   320, 640, 0.039, '38mm tube — mid HPR'),
        ('J-38mm (38mm)',   640, 1280, 0.039, '38mm tube — high HPR'),
        ('K-54mm (54mm)',  1280, 2560, 0.055, '54mm tube — EX territory'),
        ('L-75mm (75mm)',  2560, 5120, 0.076, '75mm tube — large EX'),
    ]

    catalog = {}
    for label, lo, hi, dmax, note in targets:
        candidates = [r for r in rows if lo <= r['imp'] < hi and r['diam'] <= dmax]
        if not candidates:
            continue
        # pick highest impulse representative, break ties by manufacturer priority
        mfr_prio = lambda r: (0 if 'Cesaroni' in r['mfr'] else 1 if 'AeroTech' in r['mfr'] else 2)
        best = max(candidates, key=lambda r: (r['imp'], -mfr_prio(r)))
        catalog[label] = dict(note=note, **best)

    print('=== CURATED MOTOR CATALOG — Best candidate per class/tube ===')
    print()
    for label, info in catalog.items():
        note = info['note']; mfr = info['mfr']; des = info['des']
        imp = info['imp']; diam_mm = info['diam']*1000; lm = info['lm']; ln = info['ln']; dig = info['dig']
        print(f'[{label}]  {note}')
        print(f'  Motor : {mfr} / {des}')
        print(f'  Specs : {imp:.1f} Ns | diam={diam_mm:.0f}mm | launch_mass={lm:.3f}kg | length={ln:.3f}m')
        print(f'  Digest: {dig}')
        print()

    # Also write motors_catalog.json for use in missions
    with open('motors_catalog.json', 'w', encoding='utf-8') as f:
        json.dump(catalog, f, indent=2)
    print('Saved: motors_catalog.json')
