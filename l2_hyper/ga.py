"""Mission-agnostic genetic algorithm loop.

Same operator semantics as the (planned) Rust `evolve` binary: tournament
selection k=3, BLX-alpha crossover, per-gene gaussian mutation, elitism.
Seeds (e.g. the Rust elite, or a known-good design) join generation 0.
"""

import random

from .genome import random_genome, crossover, mutate, tournament, clamp


def evolve(eval_fn, bounds, seeds=None, pop_size=16, generations=4,
           elitism=2, seed=42, on_candidate=None):
    """eval_fn(genome) -> (score, metrics). Returns (best_score, best_genome,
    best_metrics, history)."""
    rng = random.Random(seed)
    population = [clamp(dict(g), bounds) for g in (seeds or [])][:pop_size]
    while len(population) < pop_size:
        if seeds and rng.random() < 0.5:
            population.append(mutate(rng.choice(seeds), bounds, rng, rate=0.4))
        else:
            population.append(random_genome(bounds, rng))

    best = (float("-inf"), None, None)
    history = []
    for gen in range(generations):
        scored = []
        for genome in population:
            score, metrics = eval_fn(genome)
            scored.append((score, genome))
            if on_candidate:
                on_candidate(gen, score, genome, metrics)
            if score > best[0]:
                best = (score, genome, metrics)
        scored.sort(key=lambda sg: sg[0], reverse=True)
        history.append(dict(gen=gen, best=scored[0][0],
                            mean=sum(s for s, _ in scored) / len(scored)))

        elite = [g for _, g in scored[:elitism]]
        children = list(elite)
        while len(children) < pop_size:
            a = tournament(scored, rng)
            b = tournament(scored, rng)
            children.append(mutate(crossover(a, b, bounds, rng), bounds, rng))
        population = children

    return best[0], best[1], best[2], history
