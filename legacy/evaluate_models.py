import os
import glob
import orhelper
from orhelper import OpenRocketInstance

def evaluate_models():
    ork_files = glob.glob("*.ork")
    
    # Ignorar arquivos de teste quebrados gerados anteriormente
    ignore_list = ["template.ork", "L2_Apex.ork", "testraw.ork"]
    models = [f for f in ork_files if f not in ignore_list]
    
    if not models:
        print("Nenhum modelo válido encontrado para teste.")
        return

    print(f"[*] Iniciando bateria de testes em {len(models)} modelos...")
    print("=" * 60)
    
    results = []

    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        for model in models:
            print(f"\n[+] Analisando: {model}")
            try:
                doc = orh.load_doc(model)
                simulations = doc.getSimulations()
                
                if not simulations:
                    print("  -> Nenhuma simulação configurada neste arquivo.")
                    continue
                    
                for sim in simulations:
                    sim_name = sim.getName()
                    try:
                        orh.run_simulation(sim)
                        
                        import jpype
                        data = sim.getSimulatedData().getBranch(0)
                        FlightDataType = jpype.JClass('net.sf.openrocket.simulation.FlightDataType')
                        apogee = data.getMaximum(FlightDataType.TYPE_ALTITUDE)
                        max_vel = data.getMaximum(FlightDataType.TYPE_VELOCITY_TOTAL)
                        max_mach = data.getMaximum(FlightDataType.TYPE_MACH_NUMBER)
                        
                        print(f"  -> Simulação '{sim_name}': Apogeu: {apogee:.1f}m | Vel: {max_vel:.1f}m/s (Mach {max_mach:.2f})")
                        
                        results.append({
                            "model": model,
                            "sim": sim_name,
                            "apogee": apogee,
                            "velocity": max_vel,
                            "mach": max_mach
                        })
                        
                    except Exception as e:
                        print(f"  -> Simulação '{sim_name}' FALHOU: {str(e).splitlines()[0][:80]}")
                        
            except Exception as e:
                print(f"  -> Erro crítico ao carregar {model}: {str(e).splitlines()[0][:80]}")

    print("\n" + "=" * 60)
    print("RANKING DE APOGEU (RESULTADOS CONCRETOS)")
    print("=" * 60)
    
    # Ordenar por apogeu descendente
    results.sort(key=lambda x: x["apogee"], reverse=True)
    
    for i, res in enumerate(results, 1):
        print(f"{i}. {res['model']} ({res['sim']}) -> {res['apogee']:.1f} metros (Mach {res['mach']:.2f})")

if __name__ == "__main__":
    evaluate_models()
