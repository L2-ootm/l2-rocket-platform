"""
L2 Systems - FORGE MEGA EVOLUTION v3.0
Testa MILHARES de designs. Zero erros. Produção pronta.
"""
import os
import sys
import math
import json
import random
import shutil
import time
import jpype
import orhelper
from orhelper import OpenRocketInstance
from rocket_forge import (
    RocketArchitect, MOTOR_DATABASE, MATERIALS, NOSE_SHAPES, FIN_CROSS_SECTIONS,
    random_rocket_params, mutate_params, crossover
)

# =========================================================================
# AVALIAÇÃO
# =========================================================================
def evaluate(orh, filepath, launch_angle_deg=0.0):
    """Avalia um foguete. Retorna dict de métricas ou None se falhar."""
    try:
        doc = orh.load_doc(filepath)
    except Exception as e:
        import traceback
        print(f"Error loading {filepath}: {e}")
        traceback.print_exc()
        return None
    try:
        sim = doc.getSimulations().get(0)
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
        max_vel = float(data.getMaximum(FDT.TYPE_VELOCITY_TOTAL))
        # Sanity check
        if apogee < 0 or apogee > 100000 or mach < 0 or mach > 30:
            return None
        return {
            "apogee": apogee, "mach": mach, "duration": duration,
            "max_accel": max_accel, "max_vel": max_vel,
        }
    except Exception as e:
        print(f"ERROR loading {filepath}: {e}")
        return None

# =========================================================================
# FITNESS FUNCTIONS
# =========================================================================
def fitness_altitude_mach(metrics):
    """Piso 2500m + maximizar Mach."""
    if not metrics: return -99999.0
    a, m = metrics["apogee"], metrics["mach"]
    if a < 2500:
        return a + (m * 200)
    return 100000 + a + (m * 10000)

def fitness_max_altitude(metrics):
    """Altitude pura. O mais alto possível."""
    if not metrics: return -99999.0
    return metrics["apogee"]

def fitness_max_mach(metrics):
    """Velocidade pura. O mais rápido possível."""
    if not metrics: return -99999.0
    a = metrics["apogee"]
    if a < 100: return -99999.0  # precisa pelo menos sair do chão
    return metrics["mach"] * 10000 + a

def fitness_target_altitude(target):
    """Fábrica: acertar uma altitude exata."""
    def fn(metrics):
        if not metrics: return -99999.0
        a = metrics["apogee"]
        error = abs(a - target)
        return 10000 - error  # quanto menor o erro, maior o fitness
    return fn

def fitness_duration(metrics):
    """Tempo de voo máximo."""
    if not metrics: return -99999.0
    return metrics["duration"] * 100 + metrics["apogee"]

def fitness_balanced(metrics):
    """Equilíbrio entre altitude, mach e duração."""
    if not metrics: return -99999.0
    a, m, d = metrics["apogee"], metrics["mach"], metrics["duration"]
    if a < 500: return a
    return a + (m * 3000) + (d * 50)

def fitness_weird_mission(metrics):
    """Acertar exatamente 15,000m e maximizar Mach."""
    if not metrics: return -99999.0
    a = metrics["apogee"]
    m = metrics["mach"]
    error = abs(a - 15000.0)
    
    # 1.0 = target exato, diminui linearmente (pode ir negativo)
    target_score = 1.0 - (error / 15000.0)
    mach_score = m / 10.0
    
    # Pesos: 10 para apogeu, 5 para mach (baseado no weird_speed_demon.json)
    score = (target_score * 10.0) + (mach_score * 5.0)
    
    # Penalizar severamente voos inválidos ou apogeu=0
    if a < 100.0:
        return -99999.0
        
    return score * 1000.0  # Scale up for readability

# =========================================================================
# ENGINE
# =========================================================================
def run_evolution(orh, config):
    """Motor genético principal."""
    name = config["name"]
    fitness_fn = config["fitness_fn"]
    launch_angle = config.get("launch_angle", 0.0)
    motor_min = config.get("motor_min", 0)
    motor_max = config.get("motor_max", len(MOTOR_DATABASE) - 1)
    generations = config.get("generations", 15)
    pop_size = config.get("pop_size", 20)
    elite_count = config.get("elite_count", 5)
    mutation_rate = config.get("mutation_rate", 0.25)
    
    print(f"\n{'='*65}")
    print(f"  L2 FORGE v3 - {name}")
    print(f"  Ângulo: {launch_angle}° | Gens: {generations} | Pop: {pop_size}")
    print(f"  Motores: idx {motor_min}-{motor_max} | Elite: {elite_count}")
    print(f"{'='*65}")
    
    gen_dir = f"designs/forge_v3/{name}"
    os.makedirs(gen_dir, exist_ok=True)
    os.makedirs("designs/optimized", exist_ok=True)
    
    architect = RocketArchitect()
    
    # População inicial
    population = []
    for _ in range(pop_size):
        p = random_rocket_params(motor_class_min=motor_min, motor_class_max=motor_max)
        p["launch_angle"] = launch_angle
        population.append(p)
    
    best_ever = {"fitness": -99999.0, "params": None, "metrics": None}
    gen_bests = []
    total_tested = 0
    total_failed = 0
    
    for gen in range(1, generations + 1):
        t0 = time.time()
        results = []
        
        for i, params in enumerate(population):
            filepath = f"{gen_dir}/G{gen:02d}_I{i:02d}.ork"
            try:
                architect.save(params, filepath)
            except Exception:
                results.append((params, None, -99999.0))
                total_failed += 1
                continue
            
            metrics = evaluate(orh, filepath, launch_angle)
            fitness = fitness_fn(metrics)
            results.append((params, metrics, fitness))
            total_tested += 1
            if metrics is None:
                total_failed += 1
        
        # Sort
        results.sort(key=lambda x: x[2], reverse=True)
        top = results[0]
        
        elapsed = time.time() - t0
        
        if top[1]:
            motor_name = MOTOR_DATABASE[top[0]["motor_index"]][1]
            print(f"  G{gen:02d} ({elapsed:.1f}s) | "
                  f"Best: {motor_name:>7s} | "
                  f"Alt:{top[1]['apogee']:>7.1f}m | "
                  f"Mach:{top[1]['mach']:.2f} | "
                  f"Dur:{top[1]['duration']:>6.1f}s | "
                  f"Fit:{top[2]:>11.1f} | "
                  f"Fails:{sum(1 for r in results if r[1] is None)}/{pop_size}")
            gen_bests.append({
                "gen": gen, "apogee": top[1]["apogee"],
                "mach": top[1]["mach"], "fitness": top[2],
                "motor": motor_name,
            })
        else:
            print(f"  G{gen:02d} ({elapsed:.1f}s) | ALL FAILED")
            gen_bests.append({"gen": gen, "apogee": 0, "mach": 0, "fitness": -99999})
        
        if top[2] > best_ever["fitness"]:
            best_ever = {"fitness": top[2], "params": dict(top[0]), "metrics": dict(top[1]) if top[1] else None}
        
        # Seleção + reprodução
        valid = [r for r in results if r[1] is not None]
        if len(valid) < 2:
            # Re-seed se tudo falhou
            population = [random_rocket_params(motor_min, motor_max) for _ in range(pop_size)]
            for p in population: p["launch_angle"] = launch_angle
            continue
        
        elites = [r[0] for r in valid[:elite_count]]
        new_pop = list(elites)
        
        while len(new_pop) < pop_size:
            r = random.random()
            if r < 0.5:
                # Crossover
                a, b = random.sample(elites, min(2, len(elites)))
                child = crossover(a, b)
                child = mutate_params(child, rate=mutation_rate)
            elif r < 0.85:
                # Mutação forte de um elite
                child = mutate_params(random.choice(elites), rate=mutation_rate * 1.5)
            else:
                # Injeção de sangue novo (diversidade)
                child = random_rocket_params(motor_min, motor_max)
            child["launch_angle"] = launch_angle
            new_pop.append(child)
        
        population = new_pop
    
    # Salva campeão
    if best_ever["params"]:
        output_ork = f"designs/optimized/{name}.ork"
        output_json = f"designs/optimized/{name}_params.json"
        
        architect.save(best_ever["params"], output_ork)
        
        with open(output_json, 'w') as f:
            json.dump({
                "params": best_ever["params"],
                "metrics": best_ever["metrics"],
                "fitness": best_ever["fitness"],
                "gen_history": gen_bests,
                "total_tested": total_tested,
                "total_failed": total_failed,
            }, f, indent=2)
        
        mi = best_ever["params"]["motor_index"]
        motor = MOTOR_DATABASE[mi]
        m = best_ever["metrics"]
        
        print(f"\n  {'-'*55}")
        print(f"  CAMPEÃO: {name}")
        print(f"  Motor: {motor[1]} ({motor[0]})")
        print(f"  Apogeu: {m['apogee']:.1f}m | Mach: {m['mach']:.2f}")
        print(f"  Duração: {m['duration']:.1f}s | Accel: {m['max_accel']:.0f} m/s2")
        print(f"  Testados: {total_tested} | Falhas: {total_failed}")
        print(f"  Arquivo: {output_ork}")
        print(f"  {'-'*55}")
    
    return best_ever


# =========================================================================
# VALIDAÇÃO FINAL
# =========================================================================
def validate_champion(orh, name, launch_angle=0.0):
    """Carrega e valida o campeão no OpenRocket."""
    path = f"designs/optimized/{name}.ork"
    if not os.path.exists(path):
        print(f"  [SKIP] {name} não encontrado")
        return False
    
    metrics = evaluate(orh, path, launch_angle)
    if metrics:
        print(f"  [OK] {name}: Alt={metrics['apogee']:.1f}m Mach={metrics['mach']:.2f} Dur={metrics['duration']:.1f}s")
        return True
    else:
        print(f"  [FAIL] {name}: Simulação falhou!")
        return False


# =========================================================================
# MAIN
# =========================================================================
if __name__ == "__main__":
    # Configurações dos lotes
    CAMPAIGNS = [
        {
            "name": "L2_Weird_Speed_Demon",
            "fitness_fn": fitness_weird_mission,
            "launch_angle": 0.0,
            "motor_min": 0,    # F
            "motor_max": 27,   # L
            "generations": 15, # Faster evolution for the test
            "pop_size": 25,
            "elite_count": 5,
            "mutation_rate": 0.30,
        }
    ]
    
    print("=" * 65)
    print("  L2 SYSTEMS - FORGE MEGA EVOLUTION v3.0")
    print(f"  Campanhas: {len(CAMPAIGNS)}")
    total_tests = sum(c['generations'] * c['pop_size'] for c in CAMPAIGNS)
    print(f"  Total de testes planejados: {total_tests}")
    print("=" * 65)
    
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        champions = {}
        for campaign in CAMPAIGNS:
            result = run_evolution(orh, campaign)
            champions[campaign["name"]] = result
        
        # Validação final
        print(f"\n{'='*65}")
        print("  VALIDAÇÃO FINAL DE TODOS OS CAMPEÕES")
        print(f"{'='*65}")
        
        for campaign in CAMPAIGNS:
            validate_champion(orh, campaign["name"], campaign.get("launch_angle", 0.0))
    
    # Resumo
    print(f"\n{'='*65}")
    print("  RESUMO FINAL - TODOS OS CAMPEÕES")
    print(f"{'='*65}")
    for name, result in champions.items():
        if result["metrics"]:
            m = result["metrics"]
            mi = result["params"]["motor_index"]
            motor = MOTOR_DATABASE[mi][1]
            print(f"  {name:>25s} | Motor:{motor:>7s} | "
                  f"Alt:{m['apogee']:>7.1f}m | Mach:{m['mach']:.2f} | "
                  f"Dur:{m['duration']:>6.1f}s")
        else:
            print(f"  {name:>25s} | FALHOU")
    print(f"{'='*65}")
