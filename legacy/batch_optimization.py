import os
import shutil
import math
import xml.etree.ElementTree as ET
import random
import jpype
import orhelper
from genetic_pipeline import RocketMutator, OpenRocketInstance

def evaluate_rocket(orh, rocket_file, launch_angle_deg):
    try:
        doc = orh.load_doc(rocket_file)
        sim = doc.getSimulations().get(0)
        
        # Seta o angulo de lancamento
        opts = sim.getOptions()
        opts.setLaunchRodAngle(math.radians(launch_angle_deg))
        
        orh.run_simulation(sim)
        sim_data = sim.getSimulatedData().getBranch(0)
        
        if sim_data is None:
            return -9999.0, 0.0, 0.0
            
        FDT = jpype.JClass("net.sf.openrocket.simulation.FlightDataType")
        apogee = sim_data.getMaximum(FDT.TYPE_ALTITUDE)
        mach = sim_data.getMaximum(FDT.TYPE_MACH_NUMBER)
        
        return apogee, mach
        
    except Exception as e:
        # Erro de simulacao
        return -9999.0, 0.0

def run_batch_ga(orh, seed_file, output_name, launch_angle_deg, generations=10, population_size=12):
    print(f"\n=======================================================")
    print(f"[*] INICIANDO LOTE: {output_name}")
    print(f"[*] Semente: {seed_file}")
    print(f"[*] Ângulo de Lançamento: {launch_angle_deg}°")
    print(f"=======================================================\n")
    
    best_overall_fitness = -99999.0
    best_overall_ork = seed_file
    best_apogee = 0
    best_mach = 0
    
    mutator = RocketMutator(seed_file)
    
    os.makedirs("designs/generations", exist_ok=True)
    os.makedirs("designs/optimized", exist_ok=True)
    
    for gen in range(1, generations + 1):
        print(f"\n[+] GERAÇÃO {gen}")
        population = []
        
        for i in range(population_size):
            out_name = f"designs/generations/Gen_{gen}_Ind_{i}.ork"
            mutator.mutate(out_name, mutation_rate=0.20) # 20% de mutacao
            population.append(out_name)
            
        gen_results = []
        for ind in population:
            apogee, mach = evaluate_rocket(orh, ind, launch_angle_deg)
            
            if apogee == -9999.0:
                gen_results.append((ind, -9999.0, 0, 0))
                continue
            
            # Funcao de Fitness
            if apogee < 2500:
                fitness = apogee + (mach * 100)
            else:
                fitness = 100000 + apogee + (mach * 5000)
                
            gen_results.append((ind, fitness, apogee, mach))
            print(f"    {ind} -> Apogeu: {apogee:.1f}m | Mach: {mach:.2f} | Fit: {fitness:.1f}")
            
        # Seleciona o melhor
        gen_results.sort(key=lambda x: x[1], reverse=True)
        best_gen = gen_results[0]
        print(f"  -> Melhor da Geração {gen}: Apogeu {best_gen[2]:.1f}m | Mach {best_gen[3]:.2f}")
        
        if best_gen[1] > best_overall_fitness:
            best_overall_fitness = best_gen[1]
            best_overall_ork = best_gen[0]
            best_apogee = best_gen[2]
            best_mach = best_gen[3]
            mutator = RocketMutator(best_overall_ork)
            
    print(f"\n[!] LOTE CONCLUÍDO: {output_name}")
    print(f"[!] Melhor Foguete -> Apogeu: {best_apogee:.1f}m | Mach: {best_mach:.2f}")
    
    final_path = f"designs/optimized/{output_name}"
    shutil.copy(best_overall_ork, final_path)
    print(f"[!] Salvo como: {final_path}")

if __name__ == "__main__":
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        # 1. Darkstar Vertical
        run_batch_ga(orh, "designs/seeds/Darkstar 2-6 est.ork", "Darkstar_Sniper_Vertical.ork", launch_angle_deg=0.0, generations=6, population_size=10)
        
        # 2. Apex Hybrid 45 graus
        run_batch_ga(orh, "designs/optimized/L2_Apex_Hybrid.ork", "Apex_Sniper_45Deg.ork", launch_angle_deg=45.0, generations=6, population_size=10)
