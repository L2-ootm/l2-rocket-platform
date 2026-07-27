"""Find the Sustainer/Booster mass split that maximises the worst-stage window.

Candidate I carries 14.25 kg of dry stage mass, split 10.65 / 3.60.  That split
is what makes the Sustainer unlandable: required retro impulse grows as m^1.5,
and at 10.65 kg no motor that fits can arrest it, so its legal ignition window
is ~11 ms and it passes only 4 of 12 seeds.  The Booster at 3.60 kg is well
matched by I49N and passes 11 of 12.

Total dry mass is what sets ascent performance, apogee and peak Mach.  Moving
ballast *between* stages therefore leaves the ascent solution intact while
changing both landing problems.  This script sweeps the split, picks the best
plugged motor for each stage at that split, and reports the worst of the two
windows -- the quantity that actually governs cross-seed pass rate.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/mass_split_optimizer.py
"""
import json
import math
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CATALOG = "OSIFOG/experiments-2026-07-25/or_motor_catalog.json"
G = 9.80665
V_LEGAL = 5.0

TOTAL_DRY_KG = 14.25          # 10.65 Sustainer + 3.60 Booster, Candidate I
K_SUSTAINER = 179.1 / math.sqrt(10.65)   # measured freefall drag constants
K_BOOSTER = 52.8 / math.sqrt(3.60)

# The measured Sustainer landing needed ~15% more impulse than the ideal 1-D
# model (2645L265-P matched to +0.2% on paper but still left 11.3 m/s), while
# the Booster matched well.  Require a margin so a candidate is not selected on
# a knife-edge paper match.
IMPULSE_MARGIN = 1.15

FIT = {  # stage -> (max diameter m, max length m)
    # The Sustainer's central retro sits alone in an 82 mm body, so it can go
    # up to 75 mm.  The Booster's central retro is boxed in by the three 54 mm
    # K700W mains of the octaweb: anything above 38 mm leaves no room for a
    # legal central support sleeve and the generator rejects the build.
    "sustainer": (0.075, 0.66),
    "booster": (0.038, 0.95),
}


def best_motor(motors, mass, k, dmax, lmax):
    w = mass * G
    v_t = k * math.sqrt(mass)
    best = None
    for mo in motors:
        if mo["diameter_m"] > dmax + 1e-6 or mo["length_m"] > lmax + 1e-6:
            continue
        if not mo["delays"] or not all(math.isinf(d) for d in mo["delays"]):
            continue
        r = mo["avg_thrust_n"] / w
        if r <= 1.05:
            continue
        s = math.sqrt(r - 1.0)
        td = (v_t / G) * math.atan(1.0 / s) / s
        i_req = mo["avg_thrust_n"] * td * IMPULSE_MARGIN
        if mo["total_impulse_ns"] < i_req:
            continue                      # cannot arrest with margin
        if mo["burn_time_s"] < 0.75 * td:
            continue                      # burns out mid-arrest
        # A matched retro arrests the stage at ground level.  Delaying ignition
        # by dt makes it arrest dt*v_t lower, i.e. it is still descending when
        # it reaches the ground, arriving at sqrt(2*a_net*dh).  Requiring that
        # to stay under V_LEGAL gives the window below, which reproduces the
        # measured I49N Booster result (0.061 s predicted vs 0.100 s measured)
        # far better than the naive V_LEGAL/a_net (1.30 s).
        window = V_LEGAL ** 2 / (2.0 * (r - 1.0) * G * v_t)
        if best is None or window > best[0]:
            best = (window, mo, r, v_t)
    return best


def main():
    motors = json.load(open(CATALOG, encoding="utf-8"))
    print(f"total dry mass held at {TOTAL_DRY_KG} kg (ascent solution preserved)")
    print("%8s %8s | %-20s %6s %7s | %-20s %6s %7s | %8s" % (
        "sust_kg", "boost_kg", "sustainer motor", "T/W", "window",
        "booster motor", "T/W", "window", "WORST"))
    rows = []
    m_s = 4.0
    while m_s <= 10.7:
        m_b = TOTAL_DRY_KG - m_s
        bs = best_motor(motors, m_s, K_SUSTAINER, *FIT["sustainer"])
        bb = best_motor(motors, m_b, K_BOOSTER, *FIT["booster"])
        if bs and bb:
            worst = min(bs[0], bb[0])
            rows.append((worst, m_s, m_b, bs, bb))
        m_s += 0.25
    for worst, m_s, m_b, bs, bb in sorted(rows, key=lambda r: -r[0])[:18]:
        print("%8.2f %8.2f | %-20s %6.2f %6.2fs | %-20s %6.2f %6.2fs | %7.2fs" % (
            m_s, m_b, bs[1]["designation"][:20], bs[2], bs[0],
            bb[1]["designation"][:20], bb[2], bb[0], worst))
    if not rows:
        print("  (no split admits a landable motor on both stages)")


if __name__ == "__main__":
    main()
