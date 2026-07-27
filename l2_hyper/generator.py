"""From-scratch N-stage .ork generator driven by mission stack + genome.

Encodes the OpenRocket 23.09 hard rules discovered in this project:
  - one shared UUID across motorconfiguration / motor / simulation conditions;
  - <separationevent>/<separationdelay> on every stage except the top one;
  - upper-stage ignition via <ignitionevent>burnout</ignitionevent> + delay;
  - <digest> pins the exact motor variant (silences "chosen arbitrarily");
  - enum values are the Java name, lowercase, no underscores.

Realism rules (v4 hardening, from the external engineering assessment):
  - minimum-diameter construction: the motor mounts directly in the airframe
    (no inner tube), and the generator REFUSES to build a stage whose airframe
    inner radius leaves less than 1 mm of radial clearance around the motor;
  - nose and interstage transitions carry real shoulders sized to slip-fit the
    receiving airframe ID (closes the "open forward airframe" warnings);
  - every stage gets its own recovery train (apogee drogue + altitude main)
    sized from an estimated recovered mass, plus recovery avionics mass;
  - separation hardware and motor retention are modelled as mass components;
  - fins get structural fillets instead of zero-radius placeholders;
  - launch longitude and geodetic model come from the mission sim block.
"""

import math
import os
import zipfile

CF = '<material type="bulk" density="1780.0">Carbon fiber</material>'

# fit contract: airframe ID must clear the motor case by at least this much
MIN_MOTOR_CLEARANCE = 0.001   # radial, metres
SHOULDER_SLIP_FIT = 0.0003    # radial gap of a shoulder inside its airframe
MAIN_DEPLOY_ALT = 500.0       # metres AGL
RHO_SL, G = 1.225, 9.81
CHUTE_CD = 0.8


def _fin_points(root, span):
    sweep, tip = root * 0.70, root * 0.35
    return (f'<point x="0.0" y="0.0"/>'
            f'<point x="{sweep}" y="{span}"/>'
            f'<point x="{sweep + tip}" y="{span}"/>'
            f'<point x="{root}" y="0.0"/>')


def _finset(name, root, span, thickness):
    return f"""<freeformfinset>
        <name>{name}</name>
        <position type="bottom">0.0</position>
        {CF}
        <fincount>3</fincount>
        <rotation>0.0</rotation>
        <thickness>{thickness:.4f}</thickness>
        <crosssection>airfoil</crosssection>
        <filletradius>{thickness:.4f}</filletradius>
        <filletmaterial type="bulk" density="1780.0">Carbon fiber</filletmaterial>
        <finpoints>{_fin_points(root, span)}</finpoints>
      </freeformfinset>"""


def _motormount(motor, fcid, ignition, delay):
    return f"""<motormount>
          <ignitionevent>{ignition}</ignitionevent>
          <ignitiondelay>{delay}</ignitiondelay>
          <overhang>0.02</overhang>
          <motor configid="{fcid}">
            <manufacturer>{motor['manufacturer']}</manufacturer>
            <digest>{motor['digest']}</digest>
            <designation>{motor['designation']}</designation>
            <diameter>{motor['diameter']}</diameter>
            <length>{motor['length']}</length>
            <delay>none</delay>
          </motor>
        </motormount>"""


def _chute_diameter(mass, v_target):
    """Flat-circular diameter giving v_target at sea level for `mass`."""
    area = 2.0 * mass * G / (RHO_SL * CHUTE_CD * v_target ** 2)
    return math.sqrt(4.0 * area / math.pi)


def _recovery(stage_name, recovered_mass):
    """Apogee drogue + low-altitude main, both sized from recovered mass."""
    drogue_d = max(0.4, _chute_diameter(recovered_mass, 28.0))
    main_d = _chute_diameter(recovered_mass, 6.5)
    return f"""<parachute>
        <name>{stage_name} Drogue</name>
        <position type="top">0.02</position>
        <cd>{CHUTE_CD}</cd>
        <material type="surface" density="0.067">Ripstop nylon</material>
        <deployevent>apogee</deployevent>
        <deploydelay>0.0</deploydelay>
        <diameter>{drogue_d:.3f}</diameter>
        <linecount>6</linecount>
        <linelength>{max(0.6, drogue_d):.3f}</linelength>
      </parachute>
      <parachute>
        <name>{stage_name} Main</name>
        <position type="top">0.05</position>
        <cd>{CHUTE_CD}</cd>
        <material type="surface" density="0.067">Ripstop nylon</material>
        <deployevent>altitude</deployevent>
        <deployaltitude>{MAIN_DEPLOY_ALT}</deployaltitude>
        <deploydelay>0.0</deploydelay>
        <diameter>{main_d:.3f}</diameter>
        <linecount>8</linecount>
        <linelength>{max(1.0, main_d):.3f}</linelength>
      </parachute>"""


def _mass(name, mass, pos=0.01):
    return f"""<masscomponent>
        <name>{name}</name>
        <position type="top">{pos}</position>
        <mass>{mass:.3f}</mass>
      </masscomponent>"""


def _recovered_mass_estimate(mission, genome, motor, r, body_len, wall, i):
    """Rough post-burnout stage mass for parachute sizing (not for CG)."""
    prop = motor["impulse"] / 1962.0                    # Isp ~200 s class
    empty_motor = max(motor["launch_mass"] - prop, 0.3 * motor["launch_mass"])
    shell = 2.0 * math.pi * r * body_len * wall * 1780.0
    m = empty_motor + 1.6 * shell + 1.0                 # fins/nose/hardware slop
    if i == 0:
        m += genome["s0_ballast"] + mission["payload_kg"]
    return m


def _stage(mission, genome, motors, fcid, i):
    stack = mission["stack"]
    st, motor = stack[i], motors[i]
    r = st["body_radius"]
    n = len(stack)
    body_len = st.get("body_len", motor["length"] + 0.08)
    wall = 0.0015 if r <= 0.06 else 0.002
    fin_thk = max(0.0035, 0.07 * r)
    ignition, delay = ("burnout", genome[f"s{i}_delay"]) if i < n - 1 else ("automatic", 0.0)
    name = st.get("name", f"Stage {i}")

    inner_r = r - wall
    min_r = motor["diameter"] / 2 + MIN_MOTOR_CLEARANCE
    if inner_r < min_r:
        raise ValueError(
            f"stage '{name}': airframe ID {2*inner_r*1000:.1f} mm cannot fit "
            f"motor {motor['designation']} ({motor['diameter']*1000:.0f} mm) "
            f"with {MIN_MOTOR_CLEARANCE*1000:.1f} mm radial clearance — "
            f"raise body_radius to at least {(min_r + wall):.4f} m")

    parts = []
    if i == 0:
        parts.append(f"""<nosecone>
        <name>Nose</name>
        <length>{genome['s0_nose_len']:.4f}</length>
        <thickness>0.0018</thickness>
        <shape>haack</shape>
        <shapeparameter>0.0</shapeparameter>
        <aftradius>{r}</aftradius>
        <aftshoulderradius>{inner_r - SHOULDER_SLIP_FIT:.4f}</aftshoulderradius>
        <aftshoulderlength>{r * 1.5:.4f}</aftshoulderlength>
        <aftshoulderthickness>0.0018</aftshoulderthickness>
        <aftshouldercapped>true</aftshouldercapped>
        {CF}
        <subcomponents>
          <masscomponent>
            <name>Nose Ballast</name>
            <position type="top">0.04</position>
            <mass>{genome['s0_ballast']:.4f}</mass>
          </masscomponent>
        </subcomponents>
      </nosecone>""")
    else:
        fore_r = stack[i - 1]["body_radius"]
        fore_wall = 0.0015 if fore_r <= 0.06 else 0.002
        tr_len = max(0.10, 4.0 * (r - fore_r) + 0.06)
        parts.append(f"""<transition>
        <name>Interstage</name>
        <length>{tr_len:.4f}</length>
        <thickness>{wall}</thickness>
        <shape>conical</shape>
        <foreradius>{fore_r}</foreradius>
        <aftradius>{r}</aftradius>
        <foreshoulderradius>{fore_r - fore_wall - SHOULDER_SLIP_FIT:.4f}</foreshoulderradius>
        <foreshoulderlength>{fore_r * 2.0:.4f}</foreshoulderlength>
        <foreshoulderthickness>{wall}</foreshoulderthickness>
        <foreshouldercapped>true</foreshouldercapped>
        <aftshoulderradius>0.0</aftshoulderradius>
        {CF}
      </transition>""")

    recovered = _recovered_mass_estimate(mission, genome, motor, r, body_len, wall, i)

    inner = []
    if i == 0:
        inner.append(_mass("Avionics", mission["payload_kg"], 0.01))
    else:
        inner.append(_mass("Recovery Avionics", 0.15, 0.01))
        inner.append(_mass("Separation System", 0.15 + 2.5 * r, 0.03))
    inner.append(_recovery(name, recovered))
    inner.append(_mass("Motor Retention", 2.5 * motor["diameter"], body_len - 0.05))
    inner.append(_finset(f"Stage{i} Fins", genome[f"s{i}_root"], genome[f"s{i}_span"], fin_thk))

    parts.append(f"""<bodytube>
        <name>{name} Airframe</name>
        <length>{body_len:.4f}</length>
        <thickness>{wall}</thickness>
        <radius>{r}</radius>
        {CF}
        {_motormount(motor, fcid, ignition, delay)}
        <subcomponents>
          {''.join(inner)}
        </subcomponents>
      </bodytube>""")

    separation = ""
    if i > 0:
        separation = (f"<separationevent>burnout</separationevent>"
                      f"<separationdelay>{genome['sep_delay']:.2f}</separationdelay>")
    return f"""<stage>
        <name>{name}</name>
        {separation}
        <subcomponents>
          {''.join(parts)}
        </subcomponents>
      </stage>"""


def build_rocket_xml(mission, genome, motors, fcid):
    """motors: list of resolved motor dicts (digest included), same order as stack."""
    stack = mission["stack"]
    stages_active = "".join(f'<stage number="{i}" active="true"/>' for i in range(len(stack)))
    stages_xml = "".join(_stage(mission, genome, motors, fcid, i) for i in range(len(stack)))
    sim = mission["sim"]
    return f"""<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.6" creator="L2 HyperPipeline">
  <rocket>
    <name>{mission.get('name', 'L2 Mission')}</name>
    <motorconfiguration configid="{fcid}" default="true">
      {stages_active}
    </motorconfiguration>
    <subcomponents>
      {stages_xml}
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="uptodate">
      <name>{mission.get('name', 'Mission')} Ascent</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{fcid}</configid>
        <launchrodlength>{sim['launchrodlength']}</launchrodlength>
        <launchrodangle>{sim['launchrodangle']}</launchrodangle>
        <windaverage>{sim['windaverage']}</windaverage>
        <windturbulence>{sim['windturbulence']}</windturbulence>
        <launchaltitude>{sim['launchaltitude']}</launchaltitude>
        <launchlatitude>{sim['launchlatitude']}</launchlatitude>
        <launchlongitude>{sim['launchlongitude']}</launchlongitude>
        <geodeticmethod>{sim['geodeticmethod']}</geodeticmethod>
        <atmosphere model="isa"/>
        <timestep>{sim['timestep']}</timestep>
      </conditions>
    </simulation>
  </simulations>
</openrocket>
"""


def save_ork(xml, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("rocket.ork", xml)
