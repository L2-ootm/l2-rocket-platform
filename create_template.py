import zipfile
import os

xml_content = """<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.4" creator="OpenRocket 23.09">
  <rocket>
    <name>L2 Test Rocket</name>
    <subcomponents>
      <stage>
        <name>Stage</name>
        <subcomponents>
          <nosecone>
            <name>Nose cone</name>
            <length>0.15</length>
            <thickness>0.002</thickness>
            <shape>vonkarman</shape>
            <shapeparameter>0.0</shapeparameter>
            <aftradius>0.012</aftradius>
            <aftshoulderradius>0.011</aftshoulderradius>
            <aftshoulderlength>0.015</aftshoulderlength>
            <aftshoulderthickness>0.002</aftshoulderthickness>
            <material type="bulk" density="1040.0">ABS</material>
          </nosecone>
          <bodytube>
            <name>Body tube</name>
            <length>0.40</length>
            <thickness>0.001</thickness>
            <radius>0.012</radius>
            <material type="bulk" density="680.0">Cardboard</material>
            <subcomponents>
              <freeformfinset>
                <name>Fins</name>
                <position type="bottom">-0.0</position>
                <material type="bulk" density="1240.0">PLA</material>
                <fincount>3</fincount>
                <rotation>0.0</rotation>
                <thickness>0.003</thickness>
                <crosssection>airfoil</crosssection>
                <finpoints>
                  <point x="0.0" y="0.0"/>
                  <point x="0.02" y="0.04"/>
                  <point x="0.06" y="0.04"/>
                  <point x="0.04" y="0.0"/>
                </finpoints>
              </freeformfinset>
              <innertube>
                <name>Motor mount</name>
                <position type="bottom">-0.0</position>
                <length>0.07</length>
                <thickness>0.001</thickness>
                <radius>0.009</radius>
                <material type="bulk" density="680.0">Cardboard</material>
                <motormount>
                  <motor delay="5.0" configid="0">Estes:D12</motor>
                </motormount>
              </innertube>
              <masscomponent>
                <name>Recovery System</name>
                <position type="top">0.1</position>
                <mass>0.015</mass>
              </masscomponent>
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="outdated">
      <name>Simulacao de Voo</name>
      <options>
        <windspeed>2.0</windspeed>
      </options>
    </simulation>
  </simulations>
</openrocket>
"""

with open("rocket.ork", "w", encoding="utf-8") as f:
    f.write(xml_content)

with zipfile.ZipFile("template.ork", "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write("rocket.ork")

os.remove("rocket.ork")
print("Template 'template.ork' gerado com sucesso!")
