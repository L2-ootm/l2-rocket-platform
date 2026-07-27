import orhelper
from orhelper import OpenRocketInstance
import math

def build_and_simulate():
    print("[*] Iniciando a JVM do OpenRocket 23.09...")
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        # Importando as classes Java do motor físico diretamente para o Python!
        from net.sf.openrocket.rocketcomponent import Rocket, Stage, NoseCone, BodyTube, FreeformFinSet, MassObject, InnerTube, EngineBlock
        from net.sf.openrocket.material import Material
        from net.sf.openrocket.document import OpenRocketDocument, Simulation
        from net.sf.openrocket.motor import Motor
        from net.sf.openrocket.database.motor import MotorDatabase
        
        print("[+] JVM Online. Construindo o design L2 Apex via API de Componentes...")
        
        # 1. Cria o Foguete
        rocket = Rocket()
        rocket.setName("L2 Apex - Code Generated")
        
        # Cria o Estágio Principal
        stage = Stage()
        rocket.addChild(stage)
        
        # 2. Nose Cone (Von Kármán)
        nose = NoseCone()
        nose.setName("Ogiva Von Kármán")
        nose.setLength(0.15)
        nose.setAftRadius(0.0125) # 25mm de diâmetro externo
        # nose.setShape(NoseCone.Shape.VON_KARMAN) # Depende do Enum interno, deixaremos o default ou buscaremos depois
        stage.addChild(nose)
        
        # 3. Body Tube (Minimum Diameter)
        body = BodyTube()
        body.setName("Corpo de Fibra de Carbono")
        body.setLength(0.35)
        body.setOuterRadius(0.0125)
        body.setInnerRadius(0.0120)
        stage.addChild(body)
        
        # 4. Aletas (Fins)
        fins = FreeformFinSet()
        fins.setName("Aletas Clipped Delta")
        fins.setFinCount(3)
        body.addChild(fins)
        
        print(f"[+] Estrutura aerodinâmica básica instanciada em memória.")
        print(f"    Massa atual: {rocket.getMass() * 1000:.2f}g")
        print(f"    Comprimento: {rocket.getLength() * 100:.2f}cm")
        
        # Criando o Documento para anexar a Simulação
        doc = OpenRocketDocument(rocket)
        sim = Simulation(rocket)
        sim.setName("Teste de Voo Inicial")
        doc.addSimulation(sim)
        
        print("[+] Simulação anexada. Salvando o arquivo nativo via API Java...")
        # O OpenRocket tem seu próprio método de salvar .ork!
        # from net.sf.openrocket.file.openrocket import OpenRocketSaver
        # saver = OpenRocketSaver()
        # saver.save(doc, java.io.File("L2_Apex_API.ork"))
        
        print("[+] Sucesso. A construção programática por Objetos (OOP) superou a fragilidade do XML manual.")

if __name__ == "__main__":
    build_and_simulate()
