import zipfile
import os

def create_optimal_rocket():
    # Engenharia Aerodinâmica de Ponta (End-to-End)
    # 1. Nariz (Nose Cone): Série de Haack (Von Kármán) para arrasto mínimo
    # 2. Transição: Ausente (corpo contínuo minimiza perturbação de camada limite)
    # 3. Tubo (Body Tube): O mínimo possível para abraçar o motor D12 (24mm interno, 25mm externo)
    # 4. Aletas (Fins): Clipped Delta com perfil aerodinâmico (Airfoil)
    # 5. Base (Boat Tail): Redução do diâmetro na base para matar o arrasto de base (Base Drag)

    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.6" creator="OpenRocket 23.09">
  <rocket>
    <name>L2 Apex Orbital Design</name>
    <motorconfiguration configid="motor-config-1" default="true"/>
    <subcomponents>
      <stage>
        <name>Sustainer</name>
        <subcomponents>
          <nosecone>
            <name>Von Karman Nose</name>
            <!-- L/D Ratio of 5:1 for subsonic perfection -->
            <length>0.125</length>
            <thickness>0.001</thickness>
            <shape>vonkarman</shape>
            <shapeparameter>0.0</shapeparameter>
            <aftradius>0.0125</aftradius>
            <aftshoulderradius>0.0118</aftshoulderradius>
            <aftshoulderlength>0.015</aftshoulderlength>
            <aftshoulderthickness>0.001</aftshoulderthickness>
            <material type="bulk" density="1040.0">ABS</material>
          </nosecone>
          <bodytube>
            <name>Minimum Diameter Hull</name>
            <length>0.35</length>
            <thickness>0.0005</thickness>
            <radius>0.0125</radius>
            <material type="bulk" density="1800.0">Carbon fiber</material>
            <subcomponents>
              <freeformfinset>
                <name>Clipped Delta Fins</name>
                <position type="bottom">-0.015</position>
                <material type="bulk" density="1800.0">Carbon fiber</material>
                <fincount>3</fincount>
                <rotation>0.0</rotation>
                <thickness>0.002</thickness>
                <crosssection>airfoil</crosssection>
                <finpoints>
                  <point x="0.0" y="0.0"/>
                  <point x="0.035" y="0.045"/>
                  <point x="0.065" y="0.045"/>
                  <point x="0.075" y="0.0"/>
                </finpoints>
              </freeformfinset>
              <innertube>
                <name>Motor mount</name>
                <position type="bottom">0.0</position>
                <length>0.07</length>
                <thickness>0.0005</thickness>
                <radius>0.012</radius>
                <material type="bulk" density="680.0">Cardboard</material>
                <motormount>
                  <ignitionevent>automatic</ignitionevent>
                  <ignitiondelay>0.0</ignitiondelay>
                  <overhang>0.005</overhang>
                  <motor configid="motor-config-1">
                    <manufacturer>Estes</manufacturer>
                    <designation>D12</designation>
                    <diameter>0.024</diameter>
                    <length>0.07</length>
                    <delay>5.0</delay>
                  </motor>
                </motormount>
              </innertube>
              <masscomponent>
                <name>Avionics &amp; Recovery Payload</name>
                <position type="top">0.05</position>
                <mass>0.025</mass>
              </masscomponent>
            </subcomponents>
          </bodytube>
          <transition>
            <name>Boat Tail</name>
            <length>0.015</length>
            <thickness>0.0005</thickness>
            <foreclerance>0.0</foreclerance>
            <aftclerance>0.0</aftclerance>
            <foreradius>0.0125</foreradius>
            <aftradius>0.009</aftradius>
            <shape>conical</shape>
            <material type="bulk" density="1800.0">Carbon fiber</material>
          </transition>
        </subcomponents>
      </stage>
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="outdated">
      <name>Aerodynamic Assessment</name>
      <options>
        <windspeed>2.0</windspeed>
      </options>
    </simulation>
  </simulations>
</openrocket>
"""

    with open("apex.ork", "w", encoding="utf-8") as f:
        f.write(xml_content)

    with zipfile.ZipFile("L2_Apex.ork", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write("apex.ork", arcname="rocket.ork")

    os.remove("apex.ork")
    print("[+] Design Aerodinâmico L2 Apex compilado com sucesso: L2_Apex.ork")

if __name__ == "__main__":
    create_optimal_rocket()
