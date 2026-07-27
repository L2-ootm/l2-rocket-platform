# Performance, Concurrency, and Caching

## Current Performance Profile

### Python Candidate Generation
- `_sample_valid_parameters()`: ~1 ms per candidate (geometry validation)
- `parameters_to_ast()`: ~0.1 ms (AST construction)
- `generate_ork()`: ~5 ms (XML string generation)
- Total Python overhead per candidate: ~10 ms

### Rust Batch Scoring
- `ast_eval --serve` startup: ~2 s (binary load + motor DB)
- Per-candidate evaluation: ~50-200 ms (depending on execution_profile)
- Batch of 100 candidates: ~5-20 s (parallel via rayon)

### OpenRocket Simulation
- JVM startup: ~3-5 s (one-time)
- Per-simulation: ~1-5 s (depending on flight time)
- Batch of 10 candidates: ~10-50 s (sequential, single JVM)

### ORK Save/Reopen
- Save: ~100 ms (XML → ZIP)
- Reopen: ~200 ms (ZIP → XML → parse → load)
- Total round-trip: ~300 ms

## Concurrency Model

### Current Architecture
```
Python process (single-threaded)
  → Rust subprocess (JSONL, single process, rayon parallel)
  → OpenRocket JVM (single JVM, single-threaded)
```

### Bottlenecks
1. **OpenRocket JVM**: Single-threaded simulation execution
2. **Python GIL**: No parallel candidate generation
3. **JVM startup**: 3-5 s amortized over session

### Potential Parallelism
1. **Multiple JVMs**: One per CPU core (JPype limitation: one JVM per process)
2. **Multiple Rust processes**: Already parallel via rayon within one process
3. **Python multiprocessing**: Candidate generation is embarrassingly parallel

## Caching

### Evaluation Cache
- `EvolutionEngine._cache`: In-memory, per-session
- Key: frozen parameter tuple
- Not persisted across sessions

### CKG Cache
- `ckg_memory.py`: JSON file, read on startup, write on save
- No in-memory cache (re-reads file each time)

### Motor Curve Cache
- `organic_loop.py::_eng_designations_cached()`: LRU cache (8 entries)
- Per-motor-directory, not per-designation

### Wind Profile Cache
- `osifog_sweep.py::parse_wind_csv()`: No cache (re-reads CSV each call)
- `WindProfile::from_csv()`: No cache in Rust

## Resource Model

### Laptop-Safe Mode
- `RAYON_NUM_THREADS=4`
- Single OpenRocket JVM
- Population size: 100
- Generations: 10

### Workstation Mode
- `RAYON_NUM_THREADS=8`
- Single OpenRocket JVM
- Population size: 300
- Generations: 40

### Unattended Overnight Mode
- `RAYON_NUM_THREADS=12`
- Single OpenRocket JVM
- Population size: 500
- Generations: 100
- Checkpoint every 10 generations

## Disk/Log Growth

### Per Evaluation
- ORK file (temp): ~50 KB (deleted after simulation)
- Simulation output: In-memory (not persisted unless saved)
- Log output: ~1 line per candidate

### Per Search Run
- Organic elite JSON: ~50 KB
- ORK files (if saved): ~50 KB each × elite_count
- CKG JSON: grows with number of topologies (typically <1 MB)
- Result JSON: ~100 KB per cycle
