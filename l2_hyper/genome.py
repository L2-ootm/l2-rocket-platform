"""Genome derived from the mission stack — bounds, random init, GA operators.

Genes per stage i (0 = top): fin span/root; stage 0 adds nose length and nose
ballast; every stage except the bottom adds an ignition delay. One global
separation delay. Bounds scale with each stage's body radius so the same code
serves 29 mm minimum-diameter and 161 mm heavies alike.

Operators (tournament k=3, BLX-alpha crossover, per-gene gaussian mutation)
are the reference semantics for the Rust `evolve` port — keep them in sync.
"""


def build_bounds(mission):
    bounds = {}
    stack = mission["stack"]
    for i, st in enumerate(stack):
        r = st["body_radius"]
        bounds[f"s{i}_span"] = (1.3 * r, 3.2 * r)
        bounds[f"s{i}_root"] = (3.0 * r, 7.5 * r)
        if i == 0:
            bounds["s0_nose_len"] = (8.0 * r, 16.0 * r)
            bounds["s0_ballast"] = (0.0, 40.0 * r)
        if i < len(stack) - 1:
            bounds[f"s{i}_delay"] = (1.0, 30.0)
    bounds["sep_delay"] = (0.3, 1.5)
    return bounds


def clamp(genome, bounds):
    """Clamp to bounds; ignore foreign keys (e.g. Rust-only genes in
    elite.json) and fill genes missing from the seed with the mid-bound."""
    return {k: min(max(genome.get(k, (lo + hi) / 2), lo), hi)
            for k, (lo, hi) in bounds.items()}


def random_genome(bounds, rng):
    return {k: rng.uniform(lo, hi) for k, (lo, hi) in bounds.items()}


def crossover(a, b, bounds, rng, alpha=0.3):
    """BLX-alpha: child gene sampled from the extended parent interval."""
    child = {}
    for k in bounds:
        lo, hi = min(a[k], b[k]), max(a[k], b[k])
        ext = alpha * (hi - lo)
        child[k] = rng.uniform(lo - ext, hi + ext)
    return clamp(child, bounds)


def mutate(genome, bounds, rng, rate=0.15, sigma_frac=0.08):
    child = dict(genome)
    for k, (lo, hi) in bounds.items():
        if rng.random() < rate:
            child[k] += rng.gauss(0.0, sigma_frac * (hi - lo))
    return clamp(child, bounds)


def tournament(scored, rng, k=3):
    """scored: list of (score, genome). Returns the winning genome."""
    picks = [scored[rng.randrange(len(scored))] for _ in range(k)]
    return max(picks, key=lambda sg: sg[0])[1]
