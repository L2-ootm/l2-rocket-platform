"""
L2 Systems — FORGE MULTI-STAGE EVOLUTION
Script evolutivo focado exclusivamente em foguetes bi-estágio.
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
    """Gera DNA de um foguete bi-estágio."""
    if sustainer_max is None: sustainer_max = len(MOTOR_DATABASE) - 1
    if booster_max is None: booster_max = len(MOTOR_DATABASE) - 1
    
    # Base sustainer DNA
    params = random_rocket_params(sustainer_min, sustainer_max)
    
    # Booster genes
    params["booster_motor_index"] = random.randint(booster_min, booster_max)
    params["booster_body_length"] = random.uniform(0.3, 0.8)
    params["booster_fin_count"] = random.choice([3, 4])
    params["booster_fin_height"] = random.uniform(0.06, 0.15)
    params["booster_fin_sweep_angle"] = random.uniform(10, 45)
    params["booster_fin_root_chord"] = random.uniform(0.12, 0.25)
    params["booster_fin_tip_chord"] = random.uniform(0.02, 0.1)
    params["booster_fin_thickness"] = random.uniform(0.003, 0.006)
    
    # Delay between booster burnout and sustainer ignition (coasting phase)
    params["ignition_delay"] = random.uniform(0.0, 4.0)
    
    return params

def mutate_multistage(params, rate=0.25):
    """Mutação adaptada para genes booster."""
    p = dict(params)
    
    # Mutate Sustainer
    for key in ["body_length", "nose_length", "fin_height", "fin_root_chord"]:
        if random.random() < rate:
            p[key] *= random.uniform(0.8, 1.2)
            
    # Mutate Booster
    for key in ["booster_body_length", "booster_fin_height", "booster_fin_root_chord", "ignition_delay"]:
        if random.random() < rate:
            p[key] *= random.uniform(0.8, 1.2)
            
    # Discrete mutations
    if random.random() < rate:
        p["booster_fin_count"] = random.choice([3, 4])
    if random.random() < rate:
        p["booster_motor_index"] = random.randint(18, len(MOTOR_DATABASE) - 1)
        
    return p

def crossover_multistage(p1, p2):
    """Cruza dois foguetes bi-estágio."""
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
        
        # Stability check - if it flips, apogee will be very low or negative
        if apogee < 100 or mach > 50:
            return None
            
        return {"apogee": apogee, "mach": mach, "max_vel": vel}
    except Exception:
        return None

def fitness_fn(metrics):
    if not metrics: return -99999.0
    return metrics["apogee"]

def run_multistage_evolution():
    name = "L2_Multistage_10K"
    generations = 15
    pop_size = 20
    elite_count = 5
    
    print("=" * 65)
    print("  L2 SYSTEMS — MULTI-STAGE EVOLUTION v4")
    print(f"  Gens: {generations} | Pop: {pop_size} | Alvo: Quebrar recordes")
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
                filepath = f"designs/optimized/temp_ms_{i}.ork"
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
                print(f"  G{gen:02d} ({elapsed:.1f}s) | Best: [{m_boo} -> {m_sus}] delay:{top[0]['ignition_delay']:.1f}s | Alt: {top[1]['apogee']:.1f}m | Mach: {top[1]['mach']:.2f}")
            else:
                print(f"  G{gen:02d} ({elapsed:.1f}s) | ALL FAILED (Instabilidade aerodinâmica)")
                
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
            
        # Salva o grande campeão
        if best_ever["params"]:
            best_ever["params"]["is_final"] = True
            architect.save(best_ever["params"], f"designs/optimized/{name}.ork")
            with open(f"designs/optimized/{name}_params.json", "w") as f:
                json.dump(best_ever, f, indent=2)
            
            m = best_ever["metrics"]
            m_sus = MOTOR_DATABASE[best_ever["params"]["motor_index"]][1]
            m_boo = MOTOR_DATABASE[best_ever["params"]["booster_motor_index"]][1]
            
            print(f"\n  {'='*55}")
            print(f"  CAMPEÃO ABSOLUTO: {name}")
            print(f"  Estágios: {m_boo} (Booster) --> {m_sus} (Sustainer)")
            print(f"  Apogeu Final: {m['apogee']:.1f}m")
            print(f"  Velocidade: Mach {m['mach']:.2f}")
            print(f"  {'='*55}")

if __name__ == "__main__":
    run_multistage_evolution()
