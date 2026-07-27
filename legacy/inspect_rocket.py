import orhelper
from orhelper import OpenRocketInstance

def inspect():
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        doc = orh.load_doc("testraw.ork")
        rocket = doc.getRocket()
        
        print("Nome do Foguete:", rocket.getName())
        print("Massa (kg):", rocket.getMass())
        print("CG (m):", rocket.getCG())

        
        # Iterar sobre componentes
        def print_components(comp, level=0):
            print("  " * level + f"- {comp.getName()} ({comp.getClass().getSimpleName()})")
            for child in comp.getChildren():
                print_components(child, level + 1)
        
        print("\nComponentes:")
        for stage in rocket.getChildren():
            print_components(stage)
            
        print("\nMotores configurados:")
        for config_id in rocket.getMotorConfigurationIDs():
            print(f"Config ID: {config_id}")
            for comp in rocket.getMotorMounts():
                motor = comp.getMotor(config_id)
                if motor:
                    print(f"  Motor {motor.getDesignation()} no componente {comp.getName()}")

if __name__ == "__main__":
    inspect()
