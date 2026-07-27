import os

with open('rocket_forge.py', 'r', encoding='utf-8') as f:
    content = f.read()

# We'll locate the build method and replace it.
import re

new_build_code = '''
    def _build_stage(self, params, name="Sustainer", prefix=""):
        motor_idx = params[f"{prefix}motor_index"]
        motor = MOTOR_DATABASE[motor_idx]
        mfr, designation, motor_diam, motor_len, delay, digest = motor
        
        # Inner tube matches motor
        motor_mount_radius = motor_diam / 2.0 + 0.001
        motor_mount_length = motor_len + 0.02
        
        # Use sustainer body radius for all stages to maintain aerodynamics, unless specified
        # Always fallback to params["body_radius"] if prefix doesn't have it
        base_body_radius = params.get(f"{prefix}body_radius", params.get("body_radius", 0.02))
        base_body_thickness = params.get(f"{prefix}body_thickness", params.get("body_thickness", 0.002))
        
        body_radius = max(base_body_radius, motor_mount_radius + base_body_thickness + 0.002)
        
        # Geometria das aletas
        sweep_rad = math.radians(params.get(f"{prefix}fin_sweep_angle", params.get("fin_sweep_angle", 30)))
        fin_height = params.get(f"{prefix}fin_height", params.get("fin_height", 0.05))
        fin_root = params.get(f"{prefix}fin_root_chord", params.get("fin_root_chord", 0.1))
        fin_tip = params.get(f"{prefix}fin_tip_chord", params.get("fin_tip_chord", fin_root * 0.3))
        sweep_offset = fin_height * math.tan(sweep_rad)
        
        fin_points = [
            (0.0, 0.0),
            (sweep_offset, fin_height),
            (sweep_offset + fin_tip, fin_height),
            (fin_root, 0.0),
        ]
        fin_points_xml = "\\n".join([f'<point x="{p[0]:.6f}" y="{p[1]:.6f}"/>' for p in fin_points])
        
        # Componentes condicionais
        has_nose = (prefix == "")  # Only Sustainer gets a nosecone
        has_chute = (prefix == "") # Only Sustainer gets the main parachute for now
        
        xml = f"""
      <stage>
        <name>{name}</name>
        <subcomponents>"""
        
        if has_nose:
            nose_len = params["nose_length"]
            nose_thick = params.get("nose_thickness", 0.002)
            xml += f"""
          <nosecone>
            <name>Nose Cone</name>
            <finish>smooth</finish>
            {self._mat_xml(params.get("nose_material", "fiberglass"))}
            <length>{nose_len:.6f}</length>
            <thickness>{nose_thick:.6f}</thickness>
            <shape>{params.get("nose_shape", "ogive")}</shape>
            <shapeclipped>false</shapeclipped>
            <shapeparameter>0.0</shapeparameter>
            <aftradius>{body_radius:.6f}</aftradius>
            <aftshoulderlength>0.03</aftshoulderlength>
            <aftshoulderradius>{body_radius - base_body_thickness:.6f}</aftshoulderradius>
            <aftshoulderthickness>{nose_thick:.6f}</aftshoulderthickness>
            <aftshouldercapped>false</aftshouldercapped>
          </nosecone>"""

        body_len = params.get(f"{prefix}body_length", params.get("body_length", 0.5))
        xml += f"""
          <bodytube>
            <name>Airframe {name}</name>
            <finish>smooth</finish>
            {self._mat_xml(params.get(f"{prefix}body_material", params.get("body_material", "kraft")))}
            <length>{body_len:.6f}</length>
            <thickness>{base_body_thickness:.6f}</thickness>
            <radius>{body_radius:.6f}</radius>
            <subcomponents>
              <innertube>
                <name>Motor Mount</name>
                <position type="bottom">0.005</position>
                {self._mat_xml("kraft")}
                <length>{motor_mount_length:.6f}</length>
                <radialposition>0.0</radialposition>
                <radialdirection>0.0</radialdirection>
                <outerradius>{motor_mount_radius:.6f}</outerradius>
                <thickness>0.001</thickness>
                <clusterconfiguration>single</clusterconfiguration>
                <clusterscale>1.0</clusterscale>
                <clusterrotation>0.0</clusterrotation>
                <motormount>
                  <ignitionevent>{'automatic' if prefix == 'booster_' else params.get('sustainer_ignition_event', 'automatic')}</ignitionevent>
                  <ignitiondelay>{params.get('ignition_delay', 0.0) if prefix == '' else 0.0}</ignitiondelay>
                  <overhang>0.005</overhang>
                  <motor configid="{self.config_id}">
                    <manufacturer>{mfr}</manufacturer>
                    {f'<digest>{digest}</digest>' if digest else ''}
                    <designation>{designation}</designation>
                    <diameter>{motor_diam}</diameter>
                    <length>{motor_len}</length>
                    <delay>{delay}</delay>
                  </motor>
                </motormount>
                <subcomponents>
                  <centeringring>
                    <name>Aft Centering Ring</name>
                    <position type="bottom">-0.005</position>
                    {self._mat_xml("birch")}
                    <length>0.005</length>
                    <radialposition>0.0</radialposition>
                    <radialdirection>0.0</radialdirection>
                    <outerradius>{body_radius - base_body_thickness:.6f}</outerradius>
                    <innerradius>{motor_mount_radius:.6f}</innerradius>
                  </centeringring>
                  <centeringring>
                    <name>Forward Centering Ring</name>
                    <position type="top">0.005</position>
                    {self._mat_xml("birch")}
                    <length>0.005</length>
                    <radialposition>0.0</radialposition>
                    <radialdirection>0.0</radialdirection>
                    <outerradius>{body_radius - base_body_thickness:.6f}</outerradius>
                    <innerradius>{motor_mount_radius:.6f}</innerradius>
                  </centeringring>
                </subcomponents>
              </innertube>"""
              
        if has_chute:
            xml += f"""
              <parachute>
                <name>Recovery Chute</name>
                <position type="middle">0.0</position>
                <packedlength>0.06</packedlength>
                <packedradius>{body_radius * 0.6:.6f}</packedradius>
                <radialposition>0.0</radialposition>
                <radialdirection>0.0</radialdirection>
                <cd>auto</cd>
                <material type="surface" density="0.067">Ripstop nylon</material>
                <deployevent>ejection</deployevent>
                <deployaltitude>300.0</deployaltitude>
                <deploydelay>{params.get("chute_deploy_delay", 0.0):.1f}</deploydelay>
                <diameter>{params.get("chute_diameter", 0.5):.4f}</diameter>
                <linecount>6</linecount>
                <linelength>{params.get("chute_diameter", 0.5) * 1.1:.4f}</linelength>
                <linematerial type="line" density="0.001">Braided nylon (2 mm, 1/16 in)</linematerial>
              </parachute>"""

        xml += f"""
              <freeformfinset>
                <name>Fins</name>
                <position type="bottom">-0.005</position>
                <finish>smooth</finish>
                {self._mat_xml(params.get(f"{prefix}fin_material", params.get("fin_material", "fiberglass")))}
                <fincount>{params.get(f"{prefix}fin_count", params.get("fin_count", 4))}</fincount>
                <rotation>0.0</rotation>
                <thickness>{params.get(f"{prefix}fin_thickness", params.get("fin_thickness", 0.003)):.6f}</thickness>
                <crosssection>{params.get(f"{prefix}fin_cross_section", params.get("fin_cross_section", "airfoil"))}</crosssection>
                <cant>{params.get(f"{prefix}fin_cant", params.get("fin_cant", 0.0)):.2f}</cant>
                <filletradius>0.003</filletradius>
                <filletmaterial type="bulk" density="1250.0">Epoxy</filletmaterial>
                <finpoints>
                  {fin_points_xml}
                </finpoints>
              </freeformfinset>
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>"""
        return xml

    def build(self, params):
        designation_sus = MOTOR_DATABASE[params["motor_index"]][1]
        designation_boost = MOTOR_DATABASE[params["booster_motor_index"]][1] if "booster_motor_index" in params else None
        
        name_str = f"L2 Forge - {designation_sus}" + (f" + {designation_boost}" if designation_boost else "")
        team_str = "L2 Systems 1024" if params.get("is_final", False) else "L2 Systems AI"

        # Header
        xml = f"""<?xml version="1.0" ?>
<openrocket version="1.6" creator="L2-Systems-Forge-v3">
  <rocket>
    <name>{name_str}</name>
    <designer>{team_str}</designer>
    <motorconfiguration configid="{self.config_id}" default="true"/>
    <referencetype>maximum</referencetype>
    <subcomponents>"""
        
        # Sustainer (Top stage)
        # If it's multi-stage, sustainer shouldn't ignite automatically
        if "booster_motor_index" in params:
            params['sustainer_ignition_event'] = 'stageatburnout'  # Or 'ignitiondelay'
            
        xml += self._build_stage(params, name="Sustainer", prefix="")
        
        # Booster (Bottom stage)
        if "booster_motor_index" in params:
            xml += self._build_stage(params, name="Booster", prefix="booster_")

        # Footer
        xml += f"""
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="notsimulated">
      <name>L2 Forge Simulation</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{self.config_id}</configid>
        <launchrodlength>2.0</launchrodlength>
        <launchrodangle>{math.radians(params.get("launch_angle", 0.0)):.6f}</launchrodangle>
        <launchroddirection>90.0</launchroddirection>
        <windaverage>2.0</windaverage>
        <windturbulence>0.1</windturbulence>
        <launchaltitude>0.0</launchaltitude>
        <launchlatitude>-23.55</launchlatitude>
        <launchlongitude>-46.63</launchlongitude>
        <geodeticmethod>spherical</geodeticmethod>
        <atmosphere model="isa"/>
        <timestep>0.05</timestep>
      </conditions>
    </simulation>
  </simulations>
</openrocket>
"""
        return xml
'''

# Find the start of `def build(self, params):` and end of the function (before `def save(self, params, filepath):`)
start_idx = content.find('    def build(self, params):')
end_idx = content.find('    def save(self, params, filepath):')

if start_idx != -1 and end_idx != -1:
    new_content = content[:start_idx] + new_build_code + '\n' + content[end_idx:]
    with open('rocket_forge.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("rocket_forge.py updated successfully.")
else:
    print("Could not find start or end index.")
