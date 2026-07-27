import os
import zipfile
import xml.etree.ElementTree as ET
import random
import orhelper
from orhelper import OpenRocketInstance

class RocketMutator:
    def __init__(self, seed_ork_path, working_dir="."):
        self.seed_path = seed_ork_path
        self.working_dir = working_dir
        self.seed_xml = self._extract_xml(seed_ork_path)
        
    def _extract_xml(self, ork_path):
        with zipfile.ZipFile(ork_path, 'r') as z:
            return z.read('rocket.ork')
            
    def _save_xml_to_ork(self, xml_string, out_ork_path):
        with zipfile.ZipFile(out_ork_path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('rocket.ork', xml_string)
            
    def mutate(self, out_path, mutation_rate=0.1):
        """Mutate specific parameters of the rocket XML"""
        root = ET.fromstring(self.seed_xml)
        
        # 1. Mutar o Nose Cone (Comprimento e Formato)
        for nose in root.iter('nosecone'):
            length = nose.find('length')
            if length is not None:
                current_len = float(length.text)
                # Mutação de +/- 20%
                new_len = current_len * random.uniform(1 - mutation_rate, 1 + mutation_rate)
                length.text = f"{new_len:.4f}"
                
        # 2. Mutar Aletas (Fins)
        for fins in root.iter('freeformfinset'):
            points = fins.find('finpoints')
            if points is not None:
                for pt in points.findall('point'):
                    x = float(pt.get('x', 0))
                    y = float(pt.get('y', 0))
                    # Mutate x and y by +/- 10% se não forem zero
                    if x > 0: pt.set('x', f"{x * random.uniform(1 - mutation_rate, 1 + mutation_rate):.4f}")
                    if y > 0: pt.set('y', f"{y * random.uniform(1 - mutation_rate, 1 + mutation_rate):.4f}")

        # 3. Mutar Paraquedas (se o objetivo for planagem/tempo de voo)
        for parachute in root.iter('parachute'):
            diameter = parachute.find('diameter')
            if diameter is not None:
                current_dia = float(diameter.text)
                new_dia = current_dia * random.uniform(1 - mutation_rate, 1 + mutation_rate)
                # Limite entre 10cm e 2m
                new_dia = max(0.1, min(2.0, new_dia))
                diameter.text = f"{new_dia:.4f}"

        new_xml = ET.tostring(root, encoding='utf-8')
        self._save_xml_to_ork(new_xml, out_path)
        return out_path

def fitness_max_apogee(sim_data):
    """Objetivo: Chegar o mais alto possível"""
    import jpype
    FlightDataType = jpype.JClass('net.sf.openrocket.simulation.FlightDataType')
    return sim_data.getMaximum(FlightDataType.TYPE_ALTITUDE)

def fitness_max_duration(sim_data):
    """Objetivo: Ficar o máximo de tempo no ar (Glider / Heavy Chute)"""
    import jpype
    FlightDataType = jpype.JClass('net.sf.openrocket.simulation.FlightDataType')
    return sim_data.getMaximum(FlightDataType.TYPE_TIME)

def fitness_target_altitude(sim_data, target=1000.0):
    """Objetivo: Atingir exatamente 1000m (Penalidade por erro)"""
    import jpype
    FlightDataType = jpype.JClass('net.sf.openrocket.simulation.FlightDataType')
    apogee = sim_data.getMaximum(FlightDataType.TYPE_ALTITUDE)
    error = abs(target - apogee)
    return target - error # Quanto menor o erro, maior o fitness

def fitness_apogee_and_duration(sim_data):
    """Mix: Maximizar Apogeu e Tempo de Voo simultaneamente"""
    import jpype
    FlightDataType = jpype.JClass('net.sf.openrocket.simulation.FlightDataType')
    apogee = sim_data.getMaximum(FlightDataType.TYPE_ALTITUDE)
    duration = sim_data.getMaximum(FlightDataType.TYPE_TIME)
    if apogee <= 0 or duration <= 0: return -9999.0
    # Multiplicar ambos cria uma pressão evolutiva para subir alto e demorar a cair
    return apogee * duration

def fitness_target_1k_fast(sim_data, target=1000.0):
    """Mix: Atingir 1000m (precisão) com velocidade máxima (rápido)"""
    import jpype
    FlightDataType = jpype.JClass('net.sf.openrocket.simulation.FlightDataType')
    apogee = sim_data.getMaximum(FlightDataType.TYPE_ALTITUDE)
    max_vel = sim_data.getMaximum(FlightDataType.TYPE_VELOCITY_TOTAL)
    error = abs(target - apogee)
    # Recompensa velocidade, pune severamente o erro de altitude
    return max_vel - (error * 10.0)

def run_genetic_algorithm(seed_ork, generations=3, population_size=5, objective="apogee", output_filename="L2_Optimized.ork"):
    print(f"[*] Iniciando Algoritmo Genético L2 Systems")
    print(f"    Semente: {seed_ork}")
    print(f"    Objetivo: {objective.upper()}")
    print("-" * 50)
    
    mutator = RocketMutator(seed_ork)
    
    # Fitness map
    fitness_funcs = {
        "apogee": fitness_max_apogee,
        "duration": fitness_max_duration,
        "target_1k": fitness_target_altitude,
        "apogee_duration": fitness_apogee_and_duration,
        "target_1k_fast": fitness_target_1k_fast
    }
    fitness_func = fitness_funcs.get(objective, fitness_max_apogee)

    best_overall_fitness = -99999
    best_overall_ork = None
    
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        # Ensure generations folder exists
        os.makedirs("designs/generations", exist_ok=True)
        
        for gen in range(1, generations + 1):
            print(f"\n[+] GERAÇÃO {gen}")
            population = []
            
            # Gera a população
            for i in range(population_size):
                out_name = f"designs/generations/Gen_{gen}_Ind_{i}.ork"
                mutator.mutate(out_name, mutation_rate=0.15)
                population.append(out_name)
                
            # Avalia a população
            gen_results = []
            for ind in population:
                try:
                    doc = orh.load_doc(ind)
                    sims = doc.getSimulations()
                    
                    best_ind_fit = -9999.0
                    for sim in sims:
                        try:
                            orh.run_simulation(sim)
                            sim_data = sim.getSimulatedData().getBranch(0)
                            if sim_data is not None:
                                fit = fitness_func(sim_data)
                                if fit > best_ind_fit:
                                    best_ind_fit = fit
                        except Exception:
                            continue
                            
                    if best_ind_fit != -9999.0:
                        gen_results.append((ind, best_ind_fit))
                        print(f"    {ind} -> Fitness: {best_ind_fit:.2f}")
                    else:
                        print(f"    {ind} -> TODAS SIMULAÇÕES FALHARAM")
                        gen_results.append((ind, -9999.0))
                except Exception as e:
                    print(f"    {ind} -> ERRO DE CARREGAMENTO")
                    gen_results.append((ind, -9999.0))
            
            # Seleciona o melhor da geração
            gen_results.sort(key=lambda x: x[1], reverse=True)
            best_gen = gen_results[0]
            print(f"  -> Melhor da Geração {gen}: {best_gen[0]} (Fitness: {best_gen[1]:.2f})")
            
            # Se for o melhor global, salva e atualiza a semente
            if best_gen[1] > best_overall_fitness:
                best_overall_fitness = best_gen[1]
                best_overall_ork = best_gen[0]
                mutator = RocketMutator(best_overall_ork) # Próxima geração muta a partir do melhor!
                
    print(f"\n[!] ALGORITMO CONCLUÍDO")
    print(f"[!] O Foguete Perfeito ({objective}) foi gerado na geração {best_overall_ork} com Fitness {best_overall_fitness:.2f}")
    
    import shutil
    shutil.copy(best_overall_ork, f"designs/optimized/{output_filename}")
    print(f"[!] Salvo como: designs/optimized/{output_filename}")

if __name__ == "__main__":
    # Teste de um mix (Apogeu + Tempo no Ar)
    run_genetic_algorithm("designs/seeds/Extreme54.ork", generations=5, population_size=10, objective="apogee_duration", output_filename="L2_Apex_Hybrid.ork")
