"""Full motor database scanner — dumps all motors with classification."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import orhelper
from orhelper import OpenRocketInstance

with OpenRocketInstance('lib/OpenRocket-.jar') as inst:
    import jpype
    Application = jpype.JClass('net.sf.openrocket.startup.Application')
    db = Application.getMotorSetDatabase()

    all_motors = []
    for ms in db.getMotorSets():
        for m in ms.getMotors():
            imp  = float(m.getTotalImpulseEstimate())
            diam = float(m.getDiameter())
            mfr  = str(m.getManufacturer().getDisplayName())
            des  = str(m.getDesignation())
            dig  = str(m.getDigest())
            lm   = float(m.getLaunchMass())
            ln   = float(m.getLength())
            all_motors.append((imp, diam, mfr, des, dig, lm, ln))

    all_motors.sort(key=lambda x: x[0])

    def motor_class(imp):
        thresholds = [
            (0.3,'micro'),(1.25,'A'),(2.5,'B'),(5,'C'),(10,'D'),
            (20,'E'),(40,'F'),(80,'G'),(160,'H'),(320,'I'),(640,'J'),
            (1280,'K'),(2560,'L'),(5120,'M'),(10240,'N'),
        ]
        for thr, cls in thresholds:
            if imp < thr:
                return cls
        return 'O+'

    # Summary by class
    classes = {}
    for row in all_motors:
        cls = motor_class(row[0])
        classes.setdefault(cls, []).append(row)

    order = ['micro','A','B','C','D','E','F','G','H','I','J','K','L','M','N','O+']
    print('=== Motor Database Summary ===')
    for cls in order:
        motors = classes.get(cls, [])
        if motors:
            diams = sorted(set(int(d*1000) for _, d, *_ in motors))
            print(f'Class {cls:5s}: {len(motors):4d} motors | diams: {diams[:10]} mm')

    print()
    print('=== All motors <= 40 Ns (E class and below) ===')
    header = 'Impulse    Diam  LaunchMass  Length   Manufacturer              Designation'
    print(header)
    print('-' * len(header))
    for imp, diam, mfr, des, dig, lm, ln in all_motors:
        if imp <= 40:
            print(f'{imp:9.2f}Ns  {diam*1000:4.0f}mm  {lm:8.4f}kg  {ln:7.4f}m  {mfr[:25]:25s}  {des}')

    print()
    print('=== F class (40-80 Ns), diam <= 29mm ===')
    for imp, diam, mfr, des, dig, lm, ln in all_motors:
        if 40 < imp <= 80 and diam <= 0.029:
            print(f'{imp:9.2f}Ns  {diam*1000:4.0f}mm  {lm:8.4f}kg  {ln:7.4f}m  {mfr[:25]:25s}  {des}  dig={dig[:8]}')
