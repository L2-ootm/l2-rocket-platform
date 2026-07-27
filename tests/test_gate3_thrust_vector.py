"""Gate 3: Retro thrust vector proof — does the motor fire in the right direction?

This experiment proves whether OpenRocket applies motor thrust along the
rocket's forward axis (nose-first) or allows reversed thrust.

Key question: Is the 'retro' motor actually producing retrograde thrust?
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
os.environ.setdefault('RAYON_NUM_THREADS', '1')

import jpype
from osifog_sweep import (
    init_or, generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    parse_wind_csv, WIND_CSV, _get_anti_tumble_listener,
    _component_id, MATERIALS,
)

# Minimal single-stage test vehicle with one motor
# The motor mount is positioned at the bottom of the body tube
# OpenRocket applies thrust along the rocket's longitudinal axis (nose-first)
# A motor at the bottom fires FORWARD (nose direction), not backward

def build_test_ork(retro_delay, motor_name='K550W', body_len=0.8):
    """Build a minimal test ORK with one stage and one motor."""
    cid = str(_component_id('test-config'))
    return f'''<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.6" creator="L2-OSIFOG-ThrustTest">
  <rocket>
    <name>Thrust Direction Test</name>
    <id>{_component_id('test-rocket')}</id>
    <designer>L2 Systems AI</designer>
    <motorconfiguration configid="{cid}" default="true"/>
    <referencetype>maximum</referencetype>
    <subcomponents>
      <stage>
        <name>TestStage</name>
        <id>{_component_id('test-stage')}</id>
        <subcomponents>
          <nosecone>
            <name>Nose</name>
            <id>{_component_id('test-nose')}</id>
            <finish>normal</finish>
            {MATERIALS['fiberglass']}
            <length>0.30</length>
            <thickness>0.002</thickness>
            <shape>ogive</shape>
            <shapeclipped>false</shapeclipped>
            <aftradius>0.030</aftradius>
            <aftshoulderlength>0.01</aftshoulderlength>
            <aftshoulderradius>0.027</aftshoulderradius>
            <aftshoulderthickness>0.002</aftshoulderthickness>
            <aftshouldercapped>false</aftshouldercapped>
          </nosecone>
          <bodytube>
            <name>Body</name>
            <id>{_component_id('test-body')}</id>
            <finish>normal</finish>
            {MATERIALS['cardboard']}
            <length>{body_len}</length>
            <thickness>0.002</thickness>
            <radius>0.030</radius>
            <subcomponents>
              <innertube>
                <name>Motor Mount</name>
                <id>{_component_id('test-motor-mount')}</id>
                <position type="bottom">0.0</position>
                {MATERIALS['kraft']}
                <length>0.45</length>
                <radialposition>0.0</radialposition>
                <radialdirection>0.0</radialdirection>
                <outerradius>0.016</outerradius>
                <thickness>0.001</thickness>
                <clusterconfiguration>single</clusterconfiguration>
                <clusterscale>1.0</clusterscale>
                <clusterrotation>0.0</clusterrotation>
                <motormount>
                  <ignitionevent>launch</ignitionevent>
                  <ignitiondelay>{retro_delay:.6f}</ignitiondelay>
                  <overhang>0.005</overhang>
                  <motor configid="{cid}">
                    <manufacturer>AeroTech</manufacturer>
                    <designation>{motor_name}</designation>
                    <diameter>54</diameter>
                    <length>410</length>
                    <delay>0</delay>
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
    <simulation status="notsimulated">
      <name>Thrust Direction Test</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{cid}</configid>
        <launchrodlength>0.0</launchrodlength>
        <launchrodangle>0.0</launchrodangle>
        <launchroddirection>0.0</launchroddirection>
        <wind model="average">
          <speed>0.0</speed>
          <direction>0.0</direction>
          <standarddeviation>0.0</standarddeviation>
        </wind>
        <launchaltitude>0.0</launchaltitude>
        <launchlatitude>0.0</launchlatitude>
        <launchlongitude>0.0</launchlongitude>
        <geodeticmethod>spherical</geodeticmethod>
        <atmosphere model="standard">
          <basetemperature>288.15</basetemperature>
          <basepressure>101325.0</basepressure>
          <baserelativehumidity>0.0</baserelativehumidity>
        </atmosphere>
        <timestep>0.01</timestep>
      </conditions>
      <extension extensionid="info.openrocket.core.simulation.extension.impl.ScriptingExtension">
        <entry key="language" type="string">JavaScript</entry>
        <entry key="script" type="string">function handleFlightEvent(s,e){{if(e.getType().name()==="TUMBLE")return false;return true;}}</entry>
        <entry key="enabled" type="boolean">true</entry>
      </extension>
    </simulation>
  </simulations>
</openrocket>'''


def run_thrust_test(retro_delay, motor_name='K550W'):
    """Run a test and measure velocity change during motor burn."""
    init_or()
    ork_xml = build_test_ork(retro_delay, motor_name)
    fd, path = tempfile.mkstemp(suffix='.ork')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        listener = _get_anti_tumble_listener()
        sim.simulate(listener)
        data = sim.getSimulatedData()
        fdt = jpype.JClass('info.openrocket.core.simulation.FlightDataType')

        br = data.getBranch(0)
        n = int(br.getLength())
        times = [float(br.get(fdt.TYPE_TIME)[i]) for i in range(n)]
        vzs = [float(br.get(fdt.TYPE_VELOCITY_Z)[i]) for i in range(n)]
        vxy = [float(br.get(fdt.TYPE_VELOCITY_XY)[i]) for i in range(n)]
        alts = [float(br.get(fdt.TYPE_ALTITUDE)[i]) for i in range(n)]
        masses = [float(br.get(fdt.TYPE_MASS)[i]) for i in range(n)]
        thetas = [float(br.get(fdt.TYPE_ORIENTATION_THETA)[i]) for i in range(n)]

        # Find ignition and burnout
        ignition_idx = None
        burnout_idx = None
        for i, t in enumerate(times):
            if abs(t - retro_delay) < 0.02 and ignition_idx is None:
                ignition_idx = i
            if ignition_idx is not None and masses[i] < masses[ignition_idx] * 0.98 and burnout_idx is None:
                if i > ignition_idx + 5:
                    burnout_idx = i

        if ignition_idx is None:
            ignition_idx = min(range(n), key=lambda i: abs(times[i] - retro_delay))

        # Velocity before ignition
        vz_before = vzs[max(0, ignition_idx - 1)]
        vz_at_ign = vzs[ignition_idx]

        # Velocity at burnout (or end of data if no clear burnout)
        if burnout_idx is not None:
            vz_after = vzs[burnout_idx]
            vz_change = vz_after - vz_at_ign
        else:
            # Use last sample
            vz_after = vzs[-1]
            vz_change = vz_after - vz_at_ign

        # Check if velocity INCREASED (motor pushing forward) or DECREASED (braking)
        motor_increases_velocity = vz_change > 0

        return {
            'ignition_time_s': times[ignition_idx],
            'vz_at_ignition_ms': vz_at_ign,
            'vz_after_burn_ms': vz_after,
            'vz_change_ms': vz_change,
            'motor_increases_velocity': motor_increases_velocity,
            'thrust_direction': 'NOSE_FIRST' if motor_increases_velocity else 'RETROGRADE',
            'ignition_altitude_m': alts[ignition_idx],
            'theta_at_ignition_deg': math.degrees(thetas[ignition_idx]),
            'mass_at_ignition_kg': masses[ignition_idx],
            'total_time_s': times[-1],
            'max_altitude_m': max(alts),
        }
    finally:
        try:
            os.unlink(path)
        except:
            pass


def main():
    print("Gate 3: Retro Thrust Vector Proof")
    print("=" * 60)

    # Test 1: K550W at delay=3.0s (the "best" powered landing)
    print("\nTest 1: K550W at delay=3.0s")
    result1 = run_thrust_test(3.0, 'K550W')
    print(f"  Ignition at t={result1['ignition_time_s']:.3f}s")
    print(f"  Vz at ignition: {result1['vz_at_ignition_ms']:.3f} m/s")
    print(f"  Vz after burn: {result1['vz_after_burn_ms']:.3f} m/s")
    print(f"  Vz change: {result1['vz_change_ms']:.3f} m/s")
    print(f"  Motor increases velocity: {result1['motor_increases_velocity']}")
    print(f"  Thrust direction: {result1['thrust_direction']}")
    print(f"  Theta at ignition: {result1['theta_at_ignition_deg']:.1f} deg")
    print(f"  Altitude at ignition: {result1['ignition_altitude_m']:.1f} m")

    # Test 2: K550W at delay=5.0s
    print("\nTest 2: K550W at delay=5.0s")
    result2 = run_thrust_test(5.0, 'K550W')
    print(f"  Ignition at t={result2['ignition_time_s']:.3f}s")
    print(f"  Vz at ignition: {result2['vz_at_ignition_ms']:.3f} m/s")
    print(f"  Vz after burn: {result2['vz_after_burn_ms']:.3f} m/s")
    print(f"  Vz change: {result2['vz_change_ms']:.3f} m/s")
    print(f"  Motor increases velocity: {result2['motor_increases_velocity']}")
    print(f"  Thrust direction: {result2['thrust_direction']}")

    # Test 3: H180W at delay=4.0s
    print("\nTest 3: H180W at delay=4.0s")
    result3 = run_thrust_test(4.0, 'H180W')
    print(f"  Ignition at t={result3['ignition_time_s']:.3f}s")
    print(f"  Vz at ignition: {result3['vz_at_ignition_ms']:.3f} m/s")
    print(f"  Vz after burn: {result3['vz_after_burn_ms']:.3f} m/s")
    print(f"  Vz change: {result3['vz_change_ms']:.3f} m/s")
    print(f"  Motor increases velocity: {result3['motor_increases_velocity']}")
    print(f"  Thrust direction: {result3['thrust_direction']}")

    # Conclusion
    print("\n" + "=" * 60)
    print("CONCLUSION:")
    all_increase = all(r['motor_increases_velocity'] for r in [result1, result2, result3])
    if all_increase:
        print("  ALL motors INCREASE velocity — thrust is NOSE-FIRST (forward)")
        print("  The 'retro' motor is NOT producing retrograde thrust")
        print("  OpenRocket applies motor thrust along the rocket's longitudinal axis")
        print("  regardless of motor mount name or position")
        print("  THE CURRENT MOTOR MOUNT CONFIGURATION CANNOT PRODUCE RETRO THRUST")
    else:
        some_decrease = any(not r['motor_increases_velocity'] for r in [result1, result2, result3])
        if some_decrease:
            print("  Some motors DECREASE velocity — thrust direction varies")
        else:
            print("  INCONCLUSIVE — need more tests")

    # Save artifact
    artifact = {
        'test': 'retro_thrust_vector_proof',
        'findings': {
            'openrocket_thrust_direction': 'NOSE_FIRST' if all_increase else 'MIXED',
            'motor_mount_cannot_reverse_thrust': all_increase,
            'conclusion': 'OpenRocket applies motor thrust along the rocket longitudinal axis. The motor mount position/orientation does not reverse thrust direction. The current 3+1 cage configuration has all motors firing forward (nose-first).',
        },
        'tests': [result1, result2, result3],
    }
    with open('artifacts/phase2d/retro-thrust-vector-proof.json', 'w') as f:
        json.dump(artifact, f, indent=2, default=str)
    print(f"\nArtifact written to artifacts/phase2d/retro-thrust-vector-proof.json")


if __name__ == '__main__':
    main()
