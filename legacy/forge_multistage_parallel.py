"""
L2 Systems — FORGE MULTI-STAGE PARALLEL EVOLUTION (L, M, N Class)
Multithreading/Multiprocessing para esmagar a competição.
"""
import os
import time
import json
import math
import random
import concurrent.futures
import jpype
import orhelper
from orhelper import OpenRocketInstance
from rocket_forge import RocketArchitect, MOTOR_DATABASE, random_rocket_params

# Global vars for worker processes
_worker_instance = None
_worker_orh = None

def worker_init():
    global _worker_instance, _worker_orh
    _worker_instance = OpenRocketInstance("lib/OpenRocket-23.09.jar")
    _worker_instance.__enter__()
    _worker_orh = orhelper.Helper(_worker_instance)
    jpype.attachThreadToJVM()

def evaluate_worker(task):
    filepath, params = task
    try:
        # Create architect instance in the worker to build XML
        architect = RocketArchitect()
        architect.save(params, filepath)
        
        doc = _worker_orh.load_doc(filepath)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setLaunchRodAngle(0.0)
        _worker_orh.run_simulation(sim)
        
        data = sim.getSimulatedData().getBranch(0)
        if data is None:
            return None
            
        FDT = jpype.JClass("net.sf.openrocket.simulation.FlightDataType")
        apogee = float(data.getMaximum(FDT.TYPE_ALTITUDE))
        mach = float(data.getMaximum(FDT.TYPE_MACH_NUMBER))
        vel = float(data.getMaximum(FDT.TYPE_VELOCITY_TOTAL))
        
        if apogee < 100 or mach > 50:
            return None
            
        return {"apogee": apogee, "mach": mach, "max_vel": vel}
    except Exception as e:
        return None

def random_multistage_params():
    # Sustainer now can use L, M motors (indices ~ 20 to 30)
    max_idx = len(MOTOR_DATABASE) - 1
    params = random_rocket_params(18, max_idx) 
    
    # HEAVY ENGINEERING - UPGRADE PARA CARBONO (Menos massa, mais rigidez)
    params["body_material"] = "carbon"
    params["fin_material"] = "carbon"
    params["nose_material"] = "carbon"
    params["body_thickness"] = random.uniform(0.0010, 0.0020) # Carbono pode ser mais fino
    params["nose_shape"] = "vonkarman"
    params["fin_cross_section"] = "airfoil" # Aerodinâmica máxima
    
    # Booster (Força bruta absoluta M/N)
    params["booster_motor_index"] = random.randint(25, max_idx)
    params["booster_body_length"] = random.uniform(0.4, 0.8)
    params["booster_body_thickness"] = random.uniform(0.0015, 0.0025)
    params["booster_body_material"] = "carbon"
    params["booster_fin_material"] = "carbon"
    params["booster_fin_cross_section"] = "airfoil"
    params["booster_fin_count"] = random.choice([3, 4])
    params["booster_fin_height"] = random.uniform(0.10, 0.16)
    params["booster_fin_sweep_angle"] = random.uniform(30, 60)
    params["booster_fin_root_chord"] = random.uniform(0.2, 0.3)
    params["booster_fin_tip_chord"] = random.uniform(0.01, 0.05)
    params["booster_fin_thickness"] = random.uniform(0.004, 0.006)
    
    params["ignition_delay"] = random.uniform(0.0, 4.0)
    
    return params

def mutate_multistage(params, rate=0.25):
    p = dict(params)
    max_idx = len(MOTOR_DATABASE) - 1
    
    for key in ["body_length", "nose_length", "body_thickness", "fin_height", "fin_root_chord",
                "booster_body_length", "booster_body_thickness", "booster_fin_height", "ignition_delay"]:
        if random.random() < rate:
            p[key] *= random.uniform(0.85, 1.15)
            
    if random.random() < rate:
        p["booster_fin_count"] = random.choice([3, 4])
    if random.random() < rate:
        p["motor_index"] = random.randint(18, max_idx)
    if random.random() < rate:
        p["booster_motor_index"] = random.randint(22, max_idx)
        
    return p

def crossover_multistage(p1, p2):
    child = {}
    for k in p1.keys():
        if isinstance(p1[k], float):
            child[k] = (p1[k] + p2[k]) / 2.0
        else:
            child[k] = random.choice([p1[k], p2[k]])
    return child

def fitness_fn(metrics):
    if not metrics: return -99999.0
    a = metrics["apogee"]
    m = metrics["mach"]
    score = a + (m * 5000)
    if a > 40000: score += 100000
    if m > 5.5: score += 100000
    return score

def run_parallel_evolution():
    name = "L2_Hyper_Parallel_15K"
    generations = 50
    pop_size = 32 # Multiple of 4/8 cores
    elite_count = 6
    
    print("=" * 65)
    print("  L2 SYSTEMS — HYPER PARALLEL EVOLUTION (CLASS M/N)")
    print(f"  Gens: {generations} | Pop: {pop_size} | Foco: >40km & >Mach 5.5")
    print("=" * 65)
    
    os.makedirs("designs/optimized", exist_ok=True)
    population = [random_multistage_params() for _ in range(pop_size)]
    best_ever = {"fitness": -99999.0, "params": None, "metrics": None}
    
    # Set up process pool with reduced workers to prevent JVM OOM
    with concurrent.futures.ProcessPoolExecutor(max_workers=4, initializer=worker_init) as executor:
        for gen in range(1, generations + 1):
            t0 = time.time()
            
            # Prepare tasks
            tasks = []
            for i, params in enumerate(population):
                filepath = f"designs/optimized/temp_para_{i}.ork"
                tasks.append((filepath, params))
            
            # Execute in parallel
            metrics_list = list(executor.map(evaluate_worker, tasks))
            
            # Combine results
            results = []
            for i in range(pop_size):
                metrics = metrics_list[i]
                fitness = fitness_fn(metrics)
                results.append((population[i], metrics, fitness))
                
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
            
    # Salvar campeão absoluto
    if best_ever["params"]:
        best_ever["params"]["is_final"] = True
        architect = RocketArchitect()
        architect.save(best_ever["params"], f"designs/optimized/{name}.ork")
        with open(f"designs/optimized/{name}_params.json", "w") as f:
            json.dump(best_ever, f, indent=2)
        
        m = best_ever["metrics"]
        m_sus = MOTOR_DATABASE[best_ever["params"]["motor_index"]][1]
        m_boo = MOTOR_DATABASE[best_ever["params"]["booster_motor_index"]][1]
        
        print(f"\n  {'='*55}")
        print(f"  CAMPEÃO DESTRUIDOR: {name}")
        print(f"  Estágios: {m_boo} (Booster) --> {m_sus} (Sustainer)")
        print(f"  Apogeu Final: {m['apogee']:.1f}m")
        print(f"  Velocidade: Mach {m['mach']:.2f}")
        print(f"  {'='*55}")

if __name__ == "__main__":
    run_parallel_evolution()
