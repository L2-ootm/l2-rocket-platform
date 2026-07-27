"""
L2 Systems - Forge Evolutionary Engine v2.0
Algoritmo Genético que constrói foguetes DO ZERO.
Muta tudo: motor, formato do nariz, aletas, materiais, tubos.
"""
import os
import sys
import math
import json
import random
import shutil
import jpype
import orhelper
from orhelper import OpenRocketInstance
from rocket_forge import (
    RocketArchitect, MOTOR_DATABASE, MATERIALS,
    random_rocket_params, mutate_params, crossover
)

def evaluate(orh, filepath, launch_angle_deg=0.0):
    """Avalia um foguete e retorna (apogee, mach, duration)."""
    try:
        doc = orh.load_doc(filepath)
        sim = doc.getSimulations().get(0)
        
        # Força o ângulo de lançamento
        opts = sim.getOptions()
        opts.setLaunchRodAngle(math.radians(launch_angle_deg))
        
        orh.run_simulation(sim)
        data = sim.getSimulatedData().getBranch(0)
        
        if data is None:
            return None
            
        FDT = jpype.JClass("net.sf.openrocket.simulation.FlightDataType")
        apogee = float(data.getMaximum(FDT.TYPE_ALTITUDE))
        mach = float(data.getMaximum(FDT.TYPE_MACH_NUMBER))
        duration = float(data.getMaximum(FDT.TYPE_TIME))
        max_accel = float(data.getMaximum(FDT.TYPE_ACCELERATION_TOTAL))
        
        return {
            "apogee": apogee,
            "mach": mach,
            "duration": duration,
            "max_accel": max_accel,
        }
    except Exception as e:
        return None


def fitness_2500m_mach(metrics):
    """Fitness: piso 2500m + maximizar Mach."""
    if metrics is None:
        return -99999.0
    apogee = metrics["apogee"]
    mach = metrics["mach"]
    
    if apogee < 0 or apogee > 50000:
        return -99999.0  # Simulação instável
    
    if apogee < 2500:
        # Prioridade: ganhar altitude
        return apogee + (mach * 200)
    else:
        # Já bateu 2500m, agora foca em Mach
        return 100000 + apogee + (mach * 10000)


def run_forge_evolution(
    orh,
    objective_fn,
    objective_name,
    output_name,
    launch_angle=0.0,
    motor_min=14,
    motor_max=None,
    generations=8,
    population_size=15,
    elite_count=3,
    mutation_rate=0.25,
):
    """Roda o algoritmo genético completo."""
    
    print(f"\n{'='*60}")
    print(f"  L2 SYSTEMS - FORGE EVOLUTION ENGINE v2.0")
    print(f"  Objetivo: {objective_name}")
    print(f"  Ângulo de Lançamento: {launch_angle}°")
    print(f"  Gerações: {generations} | Pop: {population_size} | Elite: {elite_count}")
    print(f"{'='*60}\n")
    
    os.makedirs("designs/forge_generations", exist_ok=True)
    os.makedirs("designs/optimized", exist_ok=True)
    
    architect = RocketArchitect()
    
    # Gera população inicial aleatória
    population = []
    for i in range(population_size):
        params = random_rocket_params(motor_class_min=motor_min, motor_class_max=motor_max)
        params["launch_angle"] = launch_angle
        population.append(params)
    
    best_ever_fitness = -99999.0
    best_ever_params = None
    best_ever_metrics = None
    
    if True:  # Flat block (orh is passed in)
        
        for gen in range(1, generations + 1):
            print(f"\n[+] GERAÇÃO {gen}")
            results = []
            
            for i, params in enumerate(population):
                filepath = f"designs/forge_generations/Forge_G{gen}_I{i}.ork"
                architect.save(params, filepath)
                
                metrics = evaluate(orh, filepath, launch_angle)
                fitness = objective_fn(metrics)
                
                if metrics:
                    motor_name = MOTOR_DATABASE[params["motor_index"]][1]
                    print(f"    [{i:02d}] Motor:{motor_name:>7s} | "
                          f"Apogeu:{metrics['apogee']:>7.1f}m | "
                          f"Mach:{metrics['mach']:.2f} | "
                          f"Fins:{params['fin_count']} | "
                          f"Nose:{params['nose_shape']:>9s} | "
                          f"Fit:{fitness:>10.1f}")
                else:
                    print(f"    [{i:02d}] FALHA NA SIMULAÇÃO")
                    
                results.append((params, metrics, fitness))
            
            # Ordena por fitness
            results.sort(key=lambda x: x[2], reverse=True)
            
            # Reporta o melhor
            best = results[0]
            best_params, best_metrics, best_fitness = best
            motor_name = MOTOR_DATABASE[best_params["motor_index"]][1]
            
            print(f"\n  >>> MELHOR G{gen}: Motor {motor_name} | "
                  f"Apogeu {best_metrics['apogee']:.1f}m | "
                  f"Mach {best_metrics['mach']:.2f} | "
                  f"Fitness {best_fitness:.1f}")
            
            if best_fitness > best_ever_fitness:
                best_ever_fitness = best_fitness
                best_ever_params = dict(best_params)
                best_ever_metrics = dict(best_metrics)
            
            # Seleciona elite
            elites = [r[0] for r in results[:elite_count]]
            
            # Gera nova população
            new_population = list(elites)  # Elite passa direto
            
            while len(new_population) < population_size:
                if random.random() < 0.7:
                    # Crossover entre dois pais da elite
                    parent_a = random.choice(elites)
                    parent_b = random.choice(elites)
                    child = crossover(parent_a, parent_b)
                    child = mutate_params(child, rate=mutation_rate)
                else:
                    # Mutação direta de um elite
                    parent = random.choice(elites)
                    child = mutate_params(parent, rate=mutation_rate * 1.5)
                
                child["launch_angle"] = launch_angle
                new_population.append(child)
            
            population = new_population
    
    # Salva o melhor de todos
    print(f"\n{'='*60}")
    print(f"  EVOLUÇÃO COMPLETA")
    print(f"  Melhor Foguete: Motor {MOTOR_DATABASE[best_ever_params['motor_index']][1]}")
    print(f"  Apogeu: {best_ever_metrics['apogee']:.1f}m")
    print(f"  Mach: {best_ever_metrics['mach']:.2f}")
    print(f"  Max Accel: {best_ever_metrics['max_accel']:.1f} m/s²")
    print(f"  Duração: {best_ever_metrics['duration']:.1f}s")
    print(f"{'='*60}")
    
    final_path = f"designs/optimized/{output_name}"
    architect.save(best_ever_params, final_path)
    print(f"[!] Salvo: {final_path}")
    
    # Salva os parâmetros para referência
    params_path = f"designs/optimized/{output_name.replace('.ork', '_params.json')}"
    with open(params_path, 'w') as f:
        json.dump({
            "params": best_ever_params,
            "metrics": best_ever_metrics,
            "fitness": best_ever_fitness,
        }, f, indent=2)
    print(f"[!] Parâmetros: {params_path}")
    
    return best_ever_params, best_ever_metrics


if __name__ == "__main__":
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        # ===================================================================
        # LOTE 1: Foguete vertical puro - Classe I/J/K
        # Meta: 2500m+ altitude, Mach 1.2+
        # ===================================================================
        run_forge_evolution(
            orh=orh,
            objective_fn=fitness_2500m_mach,
            objective_name="ALTITUDE 2500m+ & MACH 1.2+ (VERTICAL)",
            output_name="L2_Forge_Vertical_2500.ork",
            launch_angle=0.0,
            motor_min=14,
            motor_max=None,
            generations=8,
            population_size=15,
            elite_count=4,
            mutation_rate=0.25,
        )
        
        # ===================================================================
        # LOTE 2: Foguete 45° - Classe J/K
        # Meta: 2500m+ altitude inclinado, Mach 1.5+
        # ===================================================================
        run_forge_evolution(
            orh=orh,
            objective_fn=fitness_2500m_mach,
            objective_name="ALTITUDE 2500m+ & MACH 1.5+ (45 GRAUS)",
            output_name="L2_Forge_45Deg_2500.ork",
            launch_angle=45.0,
            motor_min=19,
            motor_max=None,
            generations=8,
            population_size=15,
            elite_count=4,
            mutation_rate=0.25,
        )
