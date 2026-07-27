import xml.etree.ElementTree as ET
import zipfile
import os

class ORKBuilder:
    """
    Programmatic Rocket Builder for OpenRocket (.ork) files.
    Allows genetic algorithms to construct rockets from raw 0.
    """
    def __init__(self, name="AI Generated Rocket"):
        self.root = ET.Element("openrocket", version="1.4", creator="L2-OSIFOG Procedural API")
        self.rocket = ET.SubElement(self.root, "rocket")
        ET.SubElement(self.rocket, "name").text = name
        ET.SubElement(self.rocket, "motorconfiguration", configid="0", default="true")
        self.subcomponents = ET.SubElement(self.rocket, "subcomponents")
        self.stages = []
        self._add_simulation_stub()

    def _add_simulation_stub(self):
        sims = ET.SubElement(self.root, "simulations")
        sim = ET.SubElement(sims, "simulation", status="outdated")
        ET.SubElement(sim, "name").text = "Default Simulation"
        opts = ET.SubElement(sim, "options")
        ET.SubElement(opts, "windspeed").text = "2.0"

    def add_stage(self, name="Stage"):
        stage = StageBuilder(self.subcomponents, name)
        self.stages.append(stage)
        return stage

    def compile(self, filename="generated.ork"):
        tree = ET.ElementTree(self.root)
        try:
            ET.indent(tree, space="  ", level=0)
        except AttributeError:
            pass # Python < 3.9 compatibility for indent
            
        temp_xml = f"temp_{os.urandom(4).hex()}.xml"
        tree.write(temp_xml, encoding="utf-8", xml_declaration=True)
        
        with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(temp_xml, arcname="rocket.ork")
            
        os.remove(temp_xml)
        # print(f"[*] Generated new ORK model from scratch: {filename}")

class BaseComponent:
    def __init__(self, parent_element, tag, name):
        self.element = ET.SubElement(parent_element, tag)
        ET.SubElement(self.element, "name").text = name
        
    def add_property(self, key, value, **kwargs):
        el = ET.SubElement(self.element, key, **kwargs)
        if value is not None:
            el.text = str(value)
        return el

    def set_material(self, material="Cardboard", density=680.0, type="bulk"):
        self.add_property("material", material, type=type, density=str(density))

class StageBuilder(BaseComponent):
    def __init__(self, parent_element, name):
        super().__init__(parent_element, "stage", name)
        self.subcomponents = ET.SubElement(self.element, "subcomponents")

    def add_nosecone(self, name="Nose Cone", length=0.15, radius=0.012, thickness=0.002, shape="vonkarman"):
        nc = NoseConeBuilder(self.subcomponents, name, length, radius, thickness, shape)
        return nc

    def add_bodytube(self, name="Body Tube", length=0.40, radius=0.012, thickness=0.001):
        bt = BodyTubeBuilder(self.subcomponents, name, length, radius, thickness)
        return bt
        
    def add_transition(self, name="Transition", length=0.10, aftradius=0.02, foreradius=0.01, thickness=0.002, shape="conical"):
        tr = TransitionBuilder(self.subcomponents, name, length, aftradius, foreradius, thickness, shape)
        return tr

class NoseConeBuilder(BaseComponent):
    def __init__(self, parent, name, length, radius, thickness, shape):
        super().__init__(parent, "nosecone", name)
        self.add_property("length", length)
        self.add_property("thickness", thickness)
        self.add_property("shape", shape)
        self.add_property("shapeparameter", 0.0)
        self.add_property("aftradius", radius)
        self.add_property("aftshoulderradius", radius - thickness)
        self.add_property("aftshoulderlength", 0.015)
        self.add_property("aftshoulderthickness", thickness)
        self.set_material("PLA", 1240.0)

class TransitionBuilder(BaseComponent):
    def __init__(self, parent, name, length, aftradius, foreradius, thickness, shape):
        super().__init__(parent, "transition", name)
        self.add_property("length", length)
        self.add_property("thickness", thickness)
        self.add_property("shape", shape)
        self.add_property("foreradius", foreradius)
        self.add_property("aftradius", aftradius)
        self.add_property("foreshoulderradius", foreradius - thickness)
        self.add_property("foreshoulderlength", 0.015)
        self.add_property("foreshoulderthickness", thickness)
        self.add_property("aftshoulderradius", aftradius - thickness)
        self.add_property("aftshoulderlength", 0.015)
        self.add_property("aftshoulderthickness", thickness)
        self.set_material("PLA", 1240.0)

class BodyTubeBuilder(BaseComponent):
    def __init__(self, parent, name, length, radius, thickness):
        super().__init__(parent, "bodytube", name)
        self.add_property("length", length)
        self.add_property("radius", radius)
        self.add_property("thickness", thickness)
        self.set_material("Cardboard", 680.0)
        self.subcomponents = ET.SubElement(self.element, "subcomponents")

    def add_freeform_finset(self, name="Fins", fincount=3, thickness=0.003, points=None):
        fin = FinSetBuilder(self.subcomponents, name, fincount, thickness)
        if points:
            fin.set_points(points)
        return fin
        
    def add_inner_tube(self, name="Motor Mount", length=0.07, radius=0.009, thickness=0.001):
        it = InnerTubeBuilder(self.subcomponents, name, length, radius, thickness)
        return it
        
    def add_mass(self, name="Payload", mass=0.015, position=0.1):
        m = BaseComponent(self.subcomponents, "masscomponent", name)
        m.add_property("position", position, type="top")
        m.add_property("mass", mass)
        return m

class InnerTubeBuilder(BaseComponent):
    def __init__(self, parent, name, length, radius, thickness):
        super().__init__(parent, "innertube", name)
        self.add_property("position", -0.0, type="bottom")
        self.add_property("length", length)
        self.add_property("radius", radius)
        self.add_property("thickness", thickness)
        self.set_material("Cardboard", 680.0)
        
    def add_motor(self, configid="0", delay="5.0", motor_name="Estes:D12"):
        mm = ET.SubElement(self.element, "motormount")
        m = ET.SubElement(mm, "motor", delay=str(delay), configid=str(configid))
        m.text = motor_name

class FinSetBuilder(BaseComponent):
    def __init__(self, parent, name, fincount, thickness):
        super().__init__(parent, "freeformfinset", name)
        self.add_property("position", -0.0, type="bottom")
        self.add_property("fincount", fincount)
        self.add_property("rotation", 0.0)
        self.add_property("thickness", thickness)
        self.add_property("crosssection", "airfoil")
        self.set_material("PLA", 1240.0)
        
    def set_points(self, points):
        pts = ET.SubElement(self.element, "finpoints")
        for x, y in points:
            ET.SubElement(pts, "point", x=str(x), y=str(y))


if __name__ == "__main__":
    print("[*] Testing RocketBuilder API...")
    builder = ORKBuilder("L2 Raw 0 Rocket")
    
    stage = builder.add_stage("Main Stage")
    
    stage.add_nosecone("Aerodynamic Nose", length=0.18, radius=0.015, thickness=0.002, shape="vonkarman")
    
    bt = stage.add_bodytube("Main Fuselage", length=0.50, radius=0.015, thickness=0.001)
    
    # Payload
    bt.add_mass("Avionics", mass=0.02, position=0.05)
    
    # Fins
    fin_points = [
        (0.0, 0.0),
        (0.02, 0.05),
        (0.07, 0.05),
        (0.05, 0.0)
    ]
    bt.add_freeform_finset("Carbon Fins", fincount=4, thickness=0.003, points=fin_points)
    
    # Motor
    mount = bt.add_inner_tube("Motor Tube", length=0.08, radius=0.011, thickness=0.001)
    mount.add_motor(motor_name="Estes:D12")
    
    builder.compile("test_builder_raw0.ork")
    print("[*] Successfully compiled test_builder_raw0.ork from raw 0.")
