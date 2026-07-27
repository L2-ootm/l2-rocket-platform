import os
import glob
import time
import jpype
import concurrent.futures

def evaluate_single(filepath):
    """
    Worker function for parallel OpenRocket evaluation.
    """
    import java
    # We must attach the thread to the JVM before interacting with jpype.
    if hasattr(java.lang.Thread, "attach"):
        if not java.lang.Thread.isAttached():
            java.lang.Thread.attach()
            
    # Use the globally initialized `orh` from the main block
    global orh
    
    try:
        doc = orh.load_doc(filepath)
        sim = doc.getSimulations().get(0)
        orh.run_simulation(sim)
        stats = sim.getSimulatedData()
        
        apogee = stats.getMaxAltitude()
        max_mach = stats.getMaxMachNumber()
        
        return filepath, {"apogee": apogee, "mach": max_mach}
    except Exception as e:
        return filepath, None

def evaluate_batch_parallel(filepaths, max_workers=None):
    """
    Evaluates a list of .ork files in parallel using all available CPU cores.
    """
    if max_workers is None:
        max_workers = os.cpu_count() or 4
        
    print(f"[*] Starting parallel evaluation of {len(filepaths)} rockets across {max_workers} threads...")
    start_time = time.time()
    
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_path = {executor.submit(evaluate_single, path): path for path in filepaths}
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_path):
            path, metrics = future.result()
            results[path] = metrics
            
    elapsed = time.time() - start_time
    sims_per_sec = len(filepaths) / elapsed if elapsed > 0 else 0
    print(f"[*] Parallel batch finished in {elapsed:.2f}s ({sims_per_sec:.1f} sims/sec)")
    
    return results

if __name__ == "__main__":
    import orhelper
    # Ensure JVM is started and kept alive
    with orhelper.OpenRocketInstance('lib/OpenRocket-.jar') as orh:
        files = glob.glob("temp_ork/*.ork")
        if files:
            test_files = files * 10
            print(f"Testing parallel load with {len(test_files)} tasks...")
            results = evaluate_batch_parallel(test_files)
