import jpype
import jpype.imports
import os
import sys
import random
import zipfile
import xml.etree.ElementTree as ET
import shutil

JAR_PATH = "../OpenRocket-23.09.jar"
BASE_ORK = "../designs/optimized/L2_Hyper_Parallel_15K_Fixed.ork"
OUT_FOLDER = "../designs/generations/"

if not os.path.exists(OUT_FOLDER):
    os.makedirs(OUT_FOLDER)

def patch_ork_xml(base_ork, out_ork, nose_m, fin_m, body_m):
    with zipfile.ZipFile(base_ork, 'r') as zin:
        xml_data = zin.read('rocket.ork')
    root = ET.fromstring(xml_data)

    def multiply_tag(parent, tag_name, multiplier):
        elem = parent.find(tag_name)
        if elem is not None and elem.text:
            try:
                val = float(elem.text)
                elem.text = str(val * multiplier)
            except ValueError:
                pass

    for comp in root.iter('rocketcomponent'):
        ctype = comp.get('type')
        if ctype == 'NoseCone':
            multiply_tag(comp, 'length', nose_m)
        elif ctype == 'BodyTube':
            multiply_tag(comp, 'length', body_m)
        elif ctype in ('TrapezoidFinSet', 'EllipticalFinSet', 'FreeformFinSet'):
            multiply_tag(comp, 'rootchord', fin_m)
            multiply_tag(comp, 'tipchord', fin_m)
            multiply_tag(comp, 'sweeplength', fin_m)
            multiply_tag(comp, 'span', fin_m)

    new_xml_data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    with zipfile.ZipFile(out_ork, 'w', zipfile.ZIP_DEFLATED) as zout:
        zout.writestr('rocket.ork', new_xml_data)

def evaluate_ork_jpype(ork_file):
    import java.io.File as File
    import net.sf.openrocket.file.GeneralRocketLoader as GeneralRocketLoader
    
    loader = GeneralRocketLoader(File(ork_file))
    try:
        doc = loader.load()
        simulations = doc.getSimulations()
        if simulations.isEmpty():
            return 0.0, 0.0
        
        sim = simulations.get(0)
        sim.simulate()
        data = sim.getSimulatedData()
        
        return data.getMaxAltitude(), data.getMaxMachNumber()
    except Exception as e:
        # Fails silently to allow genetic loop to skip broken geometries
        return 0.0, 0.0

def main():
    print(f"Starting JPype JVM for Native Dual-Engine Pipeline (Target: 83456m)...")
    if not jpype.isJVMStarted():
        jpype.startJVM("-Djava.awt.headless=true", classpath=[JAR_PATH])
    
    try:
        import net.sf.openrocket.startup.Application as Application
        import net.sf.openrocket.startup.GuiModule as GuiModule
        import com.google.inject.Guice as Guice
        injector = Guice.createInjector(GuiModule())
        Application.setInjector(injector)
    except Exception as e:
        pass # Guice hack is fine for what we need

    target_apogee = 83456.0
    num_simulations = 100
    print(f"Running {num_simulations} simulated genetic mutations exactly inside OpenRocket Java Engine...")
    
    best_diff = float('inf')
    best_stats = None
    best_file = ""
    
    for i in range(num_simulations):
        nose_m = random.uniform(0.8, 1.5)
        fin_m = random.uniform(0.4, 1.2)
        body_m = random.uniform(0.6, 1.2)
        
        temp_file = os.path.join(OUT_FOLDER, f"temp_gen_{i}.ork")
        patch_ork_xml(BASE_ORK, temp_file, nose_m, fin_m, body_m)
        
        apogee, mach = evaluate_ork_jpype(temp_file)
        
        if apogee > 0.0:
            diff = abs(apogee - target_apogee)
            if diff < best_diff:
                best_diff = diff
                best_stats = (nose_m, fin_m, body_m, apogee, mach)
                # Save the new champion
                if best_file and os.path.exists(best_file):
                    os.remove(best_file)
                best_file = os.path.join(OUT_FOLDER, f"Champion_L2_83456m.ork")
                shutil.copy(temp_file, best_file)
                print(f"[{i+1}/{num_simulations}] New Champion! Apogee: {apogee:.1f}m (Err: {diff:.1f}m) | Mach: {mach:.2f} | Multipliers: N:{nose_m:.2f} F:{fin_m:.2f} B:{body_m:.2f}")
        
        # clean up temp
        if os.path.exists(temp_file):
            os.remove(temp_file)

    print("\n" + "="*50)
    print(" DUAL ENGINE OPTIMIZATION COMPLETE (100% ORK ALIGNED)")
    print("="*50)
    if best_stats:
        print(f" Winner Saved To: {best_file}")
        print(f" Apogee Reached:  {best_stats[3]:.1f} meters")
        print(f" Distance to tgt: {abs(best_stats[3] - target_apogee):.1f} meters")
        print(f" Max Mach:        {best_stats[4]:.2f}")
        print(f" Multipliers:     Nose x{best_stats[0]:.3f} | Fins x{best_stats[1]:.3f} | Body x{best_stats[2]:.3f}")
    
    jpype.shutdownJVM()

if __name__ == '__main__':
    main()
