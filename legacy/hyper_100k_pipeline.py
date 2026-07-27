"""
L2 Hyper 100K pipeline — from-scratch three-stage design targeting >100 km apogee and Mach 6.

Architecture:
  1. build_rocket_xml(params) generates a complete .ork XML from scratch (no template),
     with UUID flight configuration ids and a proper <conditions><configid> simulation
     block (both required by OpenRocket 23.09, see GeneralRocketLoader findings).
  2. All candidates are evaluated inside ONE OpenRocketInstance (single JVM, single
     motor-database load) — this is the optimized pipeline vs. one JVM per candidate.
  3. Coarse-to-fine search over stage ignition delays, kick-stage stability (ballast,
     fin span) and airframe geometry. Candidates that tumble or ignite a stage after
     apogee are heavily penalized.

Stack:
  Stage 3 (booster):   Cesaroni O8000  (41.1 kNs, 161 mm)
  Stage 2 (sustainer): Cesaroni N5800  (20.4 kNs,  98 mm) minimum-diameter carbon
  Stage 1 (kick):      Cesaroni M2245  (10.0 kNs,  75 mm) lit high for Mach 6+
"""

import os
import sys
import uuid
import zipfile
import itertools

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JAR = "lib/OpenRocket-23.09.jar"
SCRATCH = os.environ.get("L2_SCRATCH", "temp_ork")
BEST_OUT = "designs/optimized/L2_Hyper_100K_M6.ork"

DEFAULTS = dict(
    # kick stage (75 mm, top) — must be generously stable: it ignites supersonic
    k_nose_len=0.45,      # von Karman nose length [m]
    k_ballast=1.1,        # nose ballast for static stability [kg]
    k_span=0.09,          # kick fin span [m]
    k_root=0.18,          # kick fin root chord [m]
    k_delay=4.0,          # kick ignition delay after sustainer burnout [s]
    payload=0.3,          # avionics mass [kg]
    # sustainer stage (98 mm, middle) — geometry from two-stage optimization
    s_body_len=1.32,
    s_span=0.11,
    s_root=0.28,
    s_delay=16.0,         # sustainer ignition delay after booster burnout [s]
    # booster stage (161 mm, bottom)
    b_span=0.115,
    b_root=0.26,
    sep_delay=0.5,        # stage separation delay after burnout [s]
)


def fin_points(root, span):
    sweep = root * 0.70
    tip = root * 0.35
    return (f'<point x="0.0" y="0.0"/>\n                  '
            f'<point x="{sweep}" y="{span}"/>\n                  '
            f'<point x="{sweep + tip}" y="{span}"/>\n                  '
            f'<point x="{root}" y="0.0"/>')


def build_rocket_xml(p, fcid):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.6" creator="L2 HyperPipeline">
  <rocket>
    <name>L2 Hyper 100K M6</name>
    <motorconfiguration configid="{fcid}" default="true">
      <stage number="0" active="true"/>
      <stage number="1" active="true"/>
      <stage number="2" active="true"/>
    </motorconfiguration>
    <subcomponents>
      <stage>
        <name>Kick</name>
        <subcomponents>
          <nosecone>
            <name>Apex Nose</name>
            <length>{p['k_nose_len']}</length>
            <thickness>0.0018</thickness>
            <shape>haack</shape>
            <shapeparameter>0.0</shapeparameter>
            <aftradius>0.040</aftradius>
            <aftshoulderradius>0.039</aftshoulderradius>
            <aftshoulderlength>0.06</aftshoulderlength>
            <aftshoulderthickness>0.0018</aftshoulderthickness>
            <material type="bulk" density="1780.0">Carbon fiber</material>
            <subcomponents>
              <masscomponent>
                <name>Nose Ballast</name>
                <position type="top">0.04</position>
                <mass>{p['k_ballast']}</mass>
              </masscomponent>
            </subcomponents>
          </nosecone>
          <bodytube>
            <name>Kick Airframe</name>
            <length>1.10</length>
            <thickness>0.0013</thickness>
            <radius>0.040</radius>
            <material type="bulk" density="1780.0">Carbon fiber</material>
            <subcomponents>
              <masscomponent>
                <name>Avionics</name>
                <position type="top">0.01</position>
                <mass>{p['payload']}</mass>
              </masscomponent>
              <freeformfinset>
                <name>Kick Fins</name>
                <position type="bottom">0.0</position>
                <material type="bulk" density="1780.0">Carbon fiber</material>
                <fincount>3</fincount>
                <rotation>0.0</rotation>
                <thickness>0.0035</thickness>
                <crosssection>airfoil</crosssection>
                <finpoints>
                  {fin_points(p['k_root'], p['k_span'])}
                </finpoints>
              </freeformfinset>
              <innertube>
                <name>M2245 Mount</name>
                <position type="bottom">0.0</position>
                <length>1.04</length>
                <thickness>0.001</thickness>
                <radius>0.0385</radius>
                <material type="bulk" density="1780.0">Carbon fiber</material>
                <motormount>
                  <ignitionevent>burnout</ignitionevent>
                  <ignitiondelay>{p['k_delay']}</ignitiondelay>
                  <overhang>0.02</overhang>
                  <motor configid="{fcid}">
                    <manufacturer>Cesaroni Technology Inc.</manufacturer>
                    <designation>9977M2245-P</designation>
                    <diameter>0.075</diameter>
                    <length>1.025</length>
                    <delay>none</delay>
                  </motor>
                </motormount>
              </innertube>
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>
      <stage>
        <name>Sustainer</name>
        <separationevent>burnout</separationevent>
        <separationdelay>{p['sep_delay']}</separationdelay>
        <subcomponents>
          <transition>
            <name>Kick Interstage</name>
            <length>0.12</length>
            <thickness>0.0018</thickness>
            <shape>conical</shape>
            <foreradius>0.040</foreradius>
            <aftradius>0.0508</aftradius>
            <foreshoulderradius>0.0</foreshoulderradius>
            <aftshoulderradius>0.0</aftshoulderradius>
            <material type="bulk" density="1780.0">Carbon fiber</material>
          </transition>
          <bodytube>
            <name>Sustainer Airframe</name>
            <length>{p['s_body_len']}</length>
            <thickness>0.0015</thickness>
            <radius>0.0508</radius>
            <material type="bulk" density="1780.0">Carbon fiber</material>
            <subcomponents>
              <freeformfinset>
                <name>Sustainer Fins</name>
                <position type="bottom">0.0</position>
                <material type="bulk" density="1780.0">Carbon fiber</material>
                <fincount>3</fincount>
                <rotation>0.0</rotation>
                <thickness>0.004</thickness>
                <crosssection>airfoil</crosssection>
                <finpoints>
                  {fin_points(p['s_root'], p['s_span'])}
                </finpoints>
              </freeformfinset>
              <innertube>
                <name>N5800 Mount</name>
                <position type="bottom">0.0</position>
                <length>1.25</length>
                <thickness>0.001</thickness>
                <radius>0.0495</radius>
                <material type="bulk" density="1780.0">Carbon fiber</material>
                <motormount>
                  <ignitionevent>burnout</ignitionevent>
                  <ignitiondelay>{p['s_delay']}</ignitiondelay>
                  <overhang>0.02</overhang>
                  <motor configid="{fcid}">
                    <manufacturer>Cesaroni Technology Inc.</manufacturer>
                    <designation>20146N5800-P</designation>
                    <diameter>0.098</diameter>
                    <length>1.239</length>
                    <delay>none</delay>
                  </motor>
                </motormount>
              </innertube>
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>
      <stage>
        <name>Booster</name>
        <separationevent>burnout</separationevent>
        <separationdelay>{p['sep_delay']}</separationdelay>
        <subcomponents>
          <transition>
            <name>Booster Interstage</name>
            <length>0.20</length>
            <thickness>0.002</thickness>
            <shape>conical</shape>
            <foreradius>0.0508</foreradius>
            <aftradius>0.0825</aftradius>
            <foreshoulderradius>0.0</foreshoulderradius>
            <aftshoulderradius>0.0</aftshoulderradius>
            <material type="bulk" density="1780.0">Carbon fiber</material>
          </transition>
          <bodytube>
            <name>Booster Airframe</name>
            <length>1.05</length>
            <thickness>0.002</thickness>
            <radius>0.0825</radius>
            <material type="bulk" density="1780.0">Carbon fiber</material>
            <subcomponents>
              <freeformfinset>
                <name>Booster Fins</name>
                <position type="bottom">0.0</position>
                <material type="bulk" density="1780.0">Carbon fiber</material>
                <fincount>3</fincount>
                <rotation>0.0</rotation>
                <thickness>0.006</thickness>
                <crosssection>airfoil</crosssection>
                <finpoints>
                  {fin_points(p['b_root'], p['b_span'])}
                </finpoints>
              </freeformfinset>
              <innertube>
                <name>O8000 Mount</name>
                <position type="bottom">0.0</position>
                <length>0.96</length>
                <thickness>0.0015</thickness>
                <radius>0.0810</radius>
                <material type="bulk" density="1780.0">Carbon fiber</material>
                <motormount>
                  <ignitionevent>automatic</ignitionevent>
                  <ignitiondelay>0.0</ignitiondelay>
                  <overhang>0.02</overhang>
                  <motor configid="{fcid}">
                    <manufacturer>Cesaroni Technology Inc.</manufacturer>
                    <designation>40960O8000-P</designation>
                    <diameter>0.161</diameter>
                    <length>0.957</length>
                    <delay>none</delay>
                  </motor>
                </motormount>
              </innertube>
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="uptodate">
      <name>Hyper Ascent</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{fcid}</configid>
        <launchrodlength>15.0</launchrodlength>
        <launchrodangle>0.0</launchrodangle>
        <windaverage>1.0</windaverage>
        <windturbulence>0.05</windturbulence>
        <launchaltitude>0.0</launchaltitude>
        <launchlatitude>0.0</launchlatitude>
        <launchlongitude>0.0</launchlongitude>
        <geodeticmethod>flat</geodeticmethod>
        <atmosphere model="isa"/>
        <timestep>0.05</timestep>
      </conditions>
    </simulation>
  </simulations>
</openrocket>
"""


def save_ork(xml, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("rocket.ork", xml)


def evaluate(orh, params, idx):
    fcid = str(uuid.uuid4())
    path = os.path.join(SCRATCH, f"cand_{idx}.ork")
    save_ork(build_rocket_xml(params, fcid), path)
    doc = orh.load_doc(path)
    sim = doc.getSimulations().get(0)
    orh.run_simulation(sim)
    data = sim.getSimulatedData()
    events = [(float(ev.getTime()), str(ev.getType())) for ev in data.getBranch(0).getEvents()]
    tumbled = any("TUMBLE" in t.upper() for _, t in events)
    ignitions = [t for t, ty in events if "IGNITION" in ty.upper()]
    apogee_t = next((t for t, ty in events if "APOGEE" in ty.upper()), None)
    late_ignition = (apogee_t is not None and any(t > apogee_t for t in ignitions))
    return (float(data.getMaxAltitude()), float(data.getMaxMachNumber()),
            float(data.getMaxVelocity()), tumbled, late_ignition)


def main():
    os.makedirs(SCRATCH, exist_ok=True)
    import orhelper
    from orhelper import OpenRocketInstance

    results = []
    with OpenRocketInstance(JAR) as instance:
        orh = orhelper.Helper(instance)

        def run(tag, **overrides):
            p = dict(DEFAULTS)
            p.update(overrides)
            idx = len(results)
            try:
                apogee, mach, vmax, tumbled, late = evaluate(orh, p, idx)
            except Exception as e:
                print(f"  [{idx:03d}] {tag}: FAIL {repr(e)[:140]}")
                return None
            # mission: >100 km AND Mach >6 — reward both, penalize instability
            score = min(apogee / 100_000.0, 1.0) + min(mach / 6.0, 1.0) + apogee / 1_000_000.0
            if tumbled or late:
                score *= 0.05
            results.append((score, apogee, mach, vmax, p, tag))
            notes = ("TUMBLE " if tumbled else "") + ("LATE-IGN " if late else "")
            flag = " <<< TARGET" if apogee > 100_000 and mach > 6 else ""
            print(f"  [{idx:03d}] {tag}: apogee {apogee/1000:8.2f} km | Mach {mach:5.2f} | "
                  f"vmax {vmax:6.1f} m/s {notes}{flag}")
            return apogee, mach

        print("[*] Phase 1 — kick stage mass: can less ballast keep it stable?")
        for kb, kr in itertools.product([0.9, 1.1, 1.3], [0.16, 0.18]):
            run(f"k_ballast={kb} k_root={kr}", k_ballast=kb, k_root=kr)

        best_p = max(results)[4]
        print("[*] Phase 2 — staging timeline: sustainer delay x kick delay")
        for sd, kd in itertools.product([14.0, 18.0, 22.0], [3.0, 6.0, 9.0]):
            run(f"s_delay={sd:>4} k_delay={kd:>4}",
                **{**best_p, "s_delay": sd, "k_delay": kd})

        best_p = max(results)[4]
        print("[*] Phase 3 — refinement around best timeline")
        sd0, kd0 = best_p["s_delay"], best_p["k_delay"]
        for sd, kd in [(sd0 + 3, kd0), (sd0 + 6, kd0), (sd0, kd0 + 2), (sd0 + 3, kd0 + 2)]:
            run(f"s_delay={sd:>4} k_delay={kd:>4}",
                **{**best_p, "s_delay": sd, "k_delay": kd})

        _, apogee, mach, vmax, p, tag = max(results)
        print("[*] Phase 4 — final validation of best candidate")
        fcid = str(uuid.uuid4())
        save_ork(build_rocket_xml(p, fcid), BEST_OUT)
        doc = orh.load_doc(BEST_OUT)
        sim = doc.getSimulations().get(0)
        orh.run_simulation(sim)
        data = sim.getSimulatedData()

        print("=" * 64)
        print("BEST DESIGN:", tag)
        print(f"  params: { {k: v for k, v in p.items()} }")
        print(f"  OFFICIAL OpenRocket: apogee {float(data.getMaxAltitude())/1000:.2f} km | "
              f"Mach {float(data.getMaxMachNumber()):.2f} | vmax {float(data.getMaxVelocity()):.1f} m/s | "
              f"flight {float(data.getFlightTime()):.0f} s")
        print(f"  saved: {BEST_OUT}")
        ok = float(data.getMaxAltitude()) > 100_000 and float(data.getMaxMachNumber()) > 6
        print(f"  MISSION {'ACHIEVED' if ok else 'NOT MET'} (target: >100 km, Mach >6)")


if __name__ == "__main__":
    main()
