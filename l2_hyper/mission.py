"""Mission spec loading and fitness compilation.

A mission JSON declares WHAT to achieve (objectives over flight metrics) and
WITH WHAT (motor stack, payload, launch conditions). Everything else — genome
bounds, rocket geometry, fitness — is derived from it, so arbitrary missions
run without code changes.
"""

import json


SIM_DEFAULTS = dict(
    launchrodlength=15.0,
    launchrodangle=0.0,
    windaverage=1.0,
    windturbulence=0.05,
    launchaltitude=0.0,
    launchlatitude=0.0,
    launchlongitude=0.0,
    geodeticmethod="flat",
    timestep=0.05,
)


def load_mission(path):
    with open(path, encoding="utf-8") as f:
        mission = json.load(f)
    if not mission.get("stack"):
        raise ValueError("mission needs a non-empty 'stack' (top stage first)")
    if not mission.get("objectives"):
        raise ValueError("mission needs 'objectives'")
    mission["sim"] = {**SIM_DEFAULTS, **mission.get("sim", {})}
    mission.setdefault("payload_kg", 0.3)
    mission.setdefault("stability_penalty", 0.05)
    # antifragile by default: every flight phase must be statically stable.
    # 1.5 cal (not 1.0) absorbs the ~0.55 cal CG bias observed between the
    # 23.09 headless engine and the 24.12 GUI on the same design.
    mission.setdefault("constraints", {})
    mission["constraints"].setdefault("min_static_margin", 1.5)
    return mission


def compile_fitness(mission):
    """Return score(metrics) built from the mission objectives.

    Objective kinds:
      atleast  : saturating reward, min(value_measured/target, 1) * weight
      atmost   : saturating reward, min(target/value_measured, 1) * weight
      target   : weight * (1 - min(|x - target| / target, 1))
      maximize : x / scale * weight   (open-ended tiebreaker)
      minimize : -x / scale * weight
    """
    objectives = mission["objectives"]
    penalty = mission["stability_penalty"]
    min_margin = mission["constraints"]["min_static_margin"]

    def score(metrics):
        total = 0.0
        for o in objectives:
            x = metrics[o["metric"]]
            w = o.get("weight", 1.0)
            kind = o["kind"]
            if kind == "atleast":
                total += w * min(x / o["value"], 1.0) if o["value"] > 0 else w
            elif kind == "atmost":
                total += w * min(o["value"] / x, 1.0) if x > 0 else w
            elif kind == "target":
                total += w * (1.0 - abs(x - o["value"]) / o["value"]) if o["value"] > 0 else w * (1.0 - abs(x))
            elif kind == "maximize":
                total += w * x / o.get("scale", 1.0)
            elif kind == "minimize":
                total -= w * x / o.get("scale", 1.0)
            else:
                raise ValueError(f"unknown objective kind: {kind}")
        
        # Penalties must make scores WORSE. If score is negative, multiplying by 
        # a fraction makes it closer to 0 (a reward). We divide instead for negative scores.
        if metrics.get("tumbled") or metrics.get("late_ignition"):
            if total > 0:
                total *= penalty
            else:
                total *= 1.0 / max(penalty, 0.01)
                
        margin = metrics.get("min_static_margin")
        if margin is not None and margin < min_margin:
            # graded penalty: gently under-margin designs keep some signal so
            # the GA can climb back toward stability instead of a score cliff
            ratio = penalty + (1.0 - penalty) * max(0.0, margin / min_min_margin) if 'min_min_margin' in locals() else penalty + (1.0 - penalty) * max(0.0, margin / min_margin)
            if total > 0:
                total *= ratio
            else:
                total *= 1.0 / max(ratio, 0.01)
        return total

    return score


def targets_met(mission, metrics):
    """True when every atleast/atmost/target objective is satisfied."""
    for o in mission["objectives"]:
        x = metrics[o["metric"]]
        if o["kind"] == "atleast" and x < o["value"]:
            return False
        if o["kind"] == "atmost" and x > o["value"]:
            return False
        if o["kind"] == "target" and abs(x - o["value"]) > o.get("tolerance", 0.02 * o["value"]):
            return False
    if metrics.get("min_static_margin", 99.0) < mission["constraints"]["min_static_margin"]:
        return False
    return metrics.get("tumbled") is not True and metrics.get("late_ignition") is not True
