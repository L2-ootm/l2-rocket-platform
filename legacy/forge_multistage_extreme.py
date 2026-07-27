"""
L2 Systems — FORGE MULTI-STAGE EXTREME EVOLUTION
Engenharia Pesada para bater 10km+ e Mach 3.4+ com redução de massa.
"""
import os
import sys
import math
import json
import random
import time
import jpype
import orhelper
from orhelper import OpenRocketInstance
from rocket_forge import (
    RocketArchitect, MOTOR_DATABASE, MATERIALS, NOSE_SHAPES, FIN_CROSS_SECTIONS,
    random_rocket_params
)

def random_multistage_params(sustainer_min=14, sustainer_max=None, booster_min=18, booster_max=None):
    if sustainer_max is None: sustainer_max = len(MOTOR_DATABASE) - 1
    if booster_max is None: booster_max = len(MOTOR_DATABASE) - 1
    
    params = random_rocket_params(sustainer_min, sustainer_max)
    
    # HEAVY ENGINEERING: Redução de entropia (massa)
    # Forçar materiais ultraleves e resistentes do OpenRocket
    params["body_material"] = "fiberglass"  # Rígido para aguentar Mach 3
    params["fin_material"] = "fiberglass"
    params["nose_material"] = "fiberglass"
    params["body_thickness"] = random.uniform(0.001, 0.002) # Paredes mais finas possíveis (1mm a 2mm)
    params["nose_shape"] = "vonkarman" # Otimizado para Mach > 1
    
    # Booster genes (Força bruta para quebrar a inércia)
    params["booster_motor_index"] = random.randint(booster_min, booster_max)
    params["booster_body_length"] = random.uniform(0.3, 0.6) # Curto para economizar massa
    params["booster_body_thickness"] = random.uniform(0.0015, 0.0025)
    params["booster_body_material"] = "fiberglass"
    params["booster_fin_material"] = "fiberglass"
    params["booster_fin_count"] = random.choice([3, 4])
    params["booster_fin_height"] = random.uniform(0.08, 0.12)
    params["booster_fin_sweep_angle"] = random.uniform(25, 55) # Altamente varrida para Mach alto
    params["booster_fin_root_chord"] = random.uniform(0.15, 0.25)
    params["booster_fin_tip_chord"] = random.uniform(0.01, 0.05)
    params["booster_fin_thickness"] = random.uniform(0.003, 0.005)
    
    # Delay otimizado (Air-start timing)
    params["ignition_delay"] = random.uniform(0.0, 3.0)
    
    return params

def mutate_multistage(params, rate=0.25):
    p = dict(params)
    
    # Micro-otimizações para redução de massa
    for key in ["body_length", "nose_length", "body_thickness", "fin_height", "fin_root_chord"]:
        if random.random() < rate:
            # Viés para diminuir medidas (reduzir peso e arrasto), a não ser que gere instabilidade
            p[key] *= random.uniform(0.85, 1.15)
            
    for key in ["booster_body_length", "booster_body_thickness", "booster_fin_height", "ignition_delay"]:
        if random.random() < rate:
            p[key] *= random.uniform(0.85, 1.15)
            
    if random.random() < rate:
        p["booster_fin_count"] = random.choice([3, 4])
    if random.random() < rate:
        # Tenta trocar motor do sustainer para K ou J
        p["motor_index"] = random.randint(14, len(MOTOR_DATABASE) - 1)
    if random.random() < rate:
        # Booster sempre K forte
        p["booster_motor_index"] = random.randint(18, len(MOTOR_DATABASE) - 1)
        
    return p

def crossover_multistage(p1, p2):
    child = {}
    for k in p1.keys():
        if isinstance(p1[k], float):
            child[k] = (p1[k] + p2[k]) / 2.0
        else:
            child[k] = random.choice([p1[k], p2[k]])
    return child

def evaluate(orh, filepath):
    try:
        doc = orh.load_doc(filepath)
        sim = doc.getSimulations().get(0)
        opts = sim.getOptions()
        opts.setLaunchRodAngle(0.0)
        orh.run_simulation(sim)
        data = sim.getSimulatedData().getBranch(0)
        if data is None:
            return None
            
        FDT = jpype.JClass("net.sf.openrocket.simulation.FlightDataType")
        apogee = float(data.getMaximum(FDT.TYPE_ALTITUDE))
        mach = float(data.getMaximum(FDT.TYPE_MACH_NUMBER))
        vel = float(data.getMaximum(FDT.TYPE_VELOCITY_TOTAL))
        
        # Estabilidade super restrita para high-mach
        if apogee < 100 or mach > 50:
            return None
            
        return {"apogee": apogee, "mach": mach, "max_vel": vel}
    except Exception:
        return None

def fitness_fn(metrics):
    if not metrics: return -99999.0
    # Queremos passar de 10km E Mach 3.4
    a = metrics["apogee"]
    m = metrics["mach"]
    
    score = a + (m * 2000)
    
    # Bônus massivo para quebrar os dois recordes
    if a > 10000:
        score += 50000
    if m > 3.4:
        score += 50000
        
    return score

def run_extreme_evolution():
    name = "L2_Hyper_Multistage_10K"
    generations = 25
    pop_size = 20
    elite_count = 5
    
    print("=" * 65)
    print("  L2 SYSTEMS — HYPER MULTI-STAGE EVOLUTION")
    print(f"  Gens: {generations} | Pop: {pop_size} | Foco: Baixa Massa, High Mach")
    print("=" * 65)
    
    os.makedirs("designs/optimized", exist_ok=True)
    architect = RocketArchitect()
    
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        population = [random_multistage_params() for _ in range(pop_size)]
        best_ever = {"fitness": -99999.0, "params": None, "metrics": None}
        
        for gen in range(1, generations + 1):
            t0 = time.time()
            results = []
            
            for i, params in enumerate(population):
                filepath = f"designs/optimized/temp_hyper_ms_{i}.ork"
                architect.save(params, filepath)
                metrics = evaluate(orh, filepath)
                fitness = fitness_fn(metrics)
                results.append((params, metrics, fitness))
                
            results.sort(key=lambda x: x[2], reverse=True)
            top = results[0]
            elapsed = time.time() - t0
            
            if top[1]:
                m_sus = MOTOR_DATABASE[top[0]["motor_index"]][1]
                m_boo = MOTOR_DATABASE[top[0]["booster_motor_index"]][1]
                print(f"  G{gen:02d} ({elapsed:.1f}s) | Best: [{m_boo} -> {m_sus}] delay:{top[0]['ignition_delay']:.1f}s | Alt: {top[1]['apogee']:.1f}m | Mach: {top[1]['mach']:.2f} | Fit: {top[2]:.1f}")
            else:
                print(f"  G{gen:02d} ({elapsed:.1f}s) | ALL FAILED (Instabilidade aerodinâmica extrema)")
                
            if top[2] > best_ever["fitness"]:
                best_ever = {"fitness": top[2], "params": dict(top[0]), "metrics": dict(top[1])}
                
            valid = [r for r in results if r[1] is not None]
            if not valid:
                population = [random_multistage_params() for _ in range(pop_size)]
                continue
                
            elites = [r[0] for r in valid[:elite_count]]
            new_pop = list(elites)
            
            while len(new_pop) < pop_size:
                if random.random() < 0.6:
                    a, b = random.sample(elites, min(2, len(elites)))
                    child = mutate_multistage(crossover_multistage(a, b))
                else:
                    child = random_multistage_params()
                new_pop.append(child)
                
            population = new_pop
            
        if best_ever["params"]:
            best_ever["params"]["is_final"] = True
            architect.save(best_ever["params"], f"designs/optimized/{name}.ork")
            with open(f"designs/optimized/{name}_params.json", "w") as f:
                json.dump(best_ever, f, indent=2)
            
            m = best_ever["metrics"]
            m_sus = MOTOR_DATABASE[best_ever["params"]["motor_index"]][1]
            m_boo = MOTOR_DATABASE[best_ever["params"]["booster_motor_index"]][1]
            
            print(f"\n  {'='*55}")
            print(f"  CAMPEÃO HYPER ABSOLUTO: {name}")
            print(f"  Estágios: {m_boo} (Booster) --> {m_sus} (Sustainer)")
            print(f"  Apogeu Final: {m['apogee']:.1f}m")
            print(f"  Velocidade: Mach {m['mach']:.2f}")
            print(f"  {'='*55}")

if __name__ == "__main__":
    run_extreme_evolution()
