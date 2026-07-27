import jpype
import jpype.imports
import os
import sys

JAR_PATH = "../OpenRocket-23.09.jar"
ORK_FILE = "../designs/optimized/L2_Hyper_Parallel_15K.ork"

def main():
    print(f"Starting JPype JVM with {JAR_PATH}")
    if not jpype.isJVMStarted():
        jpype.startJVM("-Djava.awt.headless=true", classpath=[JAR_PATH])
    
    print(f"Loading {ORK_FILE}...")
    import java.io.File as File
    import net.sf.openrocket.file.GeneralRocketLoader as GeneralRocketLoader
    
    try:
        import net.sf.openrocket.startup.Application as Application
        import net.sf.openrocket.startup.GuiModule as GuiModule
        import com.google.inject.Guice as Guice
        import com.google.inject.Module as Module
        from com.google.inject.util import Modules
        from com.google.inject.multibindings import Multibinder
        from net.sf.openrocket.formatting import RocketSubstitutor
        
        from net.sf.openrocket.database.motor import MotorDatabase
        from net.sf.openrocket.database.motor import ThrustCurveMotorSetDatabase
        
        @jpype.JImplements(Module)
        class FixModule:
            @jpype.JOverride
            def configure(self, binder):
                Multibinder.newSetBinder(binder, RocketSubstitutor)
                binder.bind(MotorDatabase).to(ThrustCurveMotorSetDatabase)
                
        # Fix the GuiModule by overriding it
        ModuleArray = jpype.JArray(Module)
        overridden = Modules.override(ModuleArray([GuiModule()]))
        # In JPype, 'with' is a reserved keyword, so it is mapped to 'with_'
        injector = Guice.createInjector(overridden.with_(FixModule()))
        Application.setInjector(injector)
    except Exception as e:
        print("Initialization warnings (expected in headless):", e)
    
    loader = GeneralRocketLoader(File(ORK_FILE))
    try:
        doc = loader.load()
        rocket = doc.getRocket()
        print(f"Rocket successfully loaded: {rocket.getName()}")
        
        simulations = doc.getSimulations()
        if simulations.isEmpty():
            print("No simulations found in the .ork file.")
            return
            
        sim = simulations.get(0)
        print(f"Executing OpenRocket Simulation '{sim.getName()}'...")
        
        sim.simulate()
        data = sim.getSimulatedData()
        
        apogee = data.getMaxAltitude()
        mach = data.getMaxMachNumber()
        
        print("\n" + "="*50)
        print(f"  OPENROCKET JAVA VALIDATION ENGINE (ORK 23.09)")
        print("="*50)
        print(f"  Final Apogee: {apogee:.2f} meters")
        print(f"  Max Mach:   {mach:.2f}")
        print("="*50 + "\n")
        
        if abs(apogee - 83456.0) < 5000:
            print("PRECISION TARGET ACHIEVED. Dual Workflow is consistent!")
        else:
            print(f"Difference from target (83456m): {abs(apogee - 83456.0):.2f}m")
            
    except Exception as e:
        print("Failed to run Java ORK simulation:", e)
    finally:
        jpype.shutdownJVM()

if __name__ == '__main__':
    main()
