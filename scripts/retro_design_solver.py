"""Solve for the stage mass that a given retro motor can actually land.

`retro_motor_selector.py` answers "which motor suits this stage?".  This answers
the more useful question: "what does the stage have to weigh for this motor to
land it, and how wide is the resulting ignition window?"

Terminal speed for a slender tail-first stage scales as sqrt(mass) at fixed
drag area, so from one measured freefall probe we get the airframe constant

    k = v_terminal / sqrt(mass)          [measured, not assumed]

and then, for trial mass m:

    v_t = k*sqrt(m)                 r = T/(m*g)
    t_decel = (v_t/g) * arctan(1/sqrt(r-1)) / sqrt(r-1)
    I_required = T * t_decel         window = V_LEGAL / ((r-1)*g)

The required impulse rises as roughly m^1.5 while the motor's impulse is fixed,
so for each motor there is a single mass at which they balance.  Landing that
mass is then feasible; heavier is not, at any ignition time.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/retro_design_solver.py
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

# Airframe drag constants, measured from the seed-16000 freefall probes in
# OSIFOG/experiments-2026-07-25/k_arch/ (k01, k06/k07 ff_* rows):
#   Sustainer: 179.1 m/s at 10.65 kg  -> k = 54.9
#   Booster  :  52.8 m/s at  3.60 kg  -> k = 27.8
K_SUSTAINER = 179.1 / math.sqrt(10.65)
K_BOOSTER = 52.8 / math.sqrt(3.60)

STAGES = {
    "sustainer": (K_SUSTAINER, 0.075, 0.66),   # (k, max_dia_m, max_len_m)
    "booster": (K_BOOSTER, 0.075, 0.95),
}


def solve_mass(motor, k, lo=0.5, hi=40.0):
    """Bisect for the mass where required impulse == the motor's impulse."""
    thrust = motor["avg_thrust_n"]
    impulse = motor["total_impulse_ns"]

    def deficit(m):
        w = m * G
        r = thrust / w
        if r <= 1.02:
            return -1e9  # cannot arrest at all -> treat as hopelessly heavy
        v_t = k * math.sqrt(m)
        s = math.sqrt(r - 1.0)
        td = (v_t / G) * math.atan(1.0 / s) / s
        return impulse - thrust * td

    if deficit(lo) < 0:
        return None  # cannot even land the lightest plausible stage
    if deficit(hi) > 0:
        return None  # implausibly capable; out of our design range
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if deficit(mid) > 0:
            lo = mid
        else:
            hi = mid
    m = 0.5 * (lo + hi)
    r = thrust / (m * G)
    return {
        "mass_kg": m,
        "r": r,
        "v_t": k * math.sqrt(m),
        "window_s": V_LEGAL / ((r - 1.0) * G),
    }


def main():
    motors = json.load(open(CATALOG, encoding="utf-8"))
    for stage, (k, dmax, lmax) in STAGES.items():
        print(f"\n{'='*94}")
        print(f"{stage.upper()}   drag constant k={k:.1f} (v_t = k*sqrt(m))"
              f"   fits <={dmax*1000:.0f} mm, <={lmax*1000:.0f} mm")
        print("=" * 94)
        out = []
        for mo in motors:
            if mo["diameter_m"] > dmax + 1e-6 or mo["length_m"] > lmax + 1e-6:
                continue
            if not mo["delays"] or not all(math.isinf(d) for d in mo["delays"]):
                continue
            sol = solve_mass(mo, k)
            if sol is None or sol["window_s"] < 0.10:
                continue
            out.append((sol, mo))
        out.sort(key=lambda c: -c[0]["window_s"])
        print("%-22s %-13s %5s %7s %7s %6s %7s %8s %8s" % (
            "designation", "mfr", "dia", "landable", "v_term", "T/W",
            "t_burn", "I_have", "window"))
        for sol, mo in out[:16]:
            print("%-22s %-13s %5.0f %6.2fkg %6.1f %6.2f %7.2f %8.0f %7.2fs" % (
                mo["designation"], mo["manufacturer"][:13], mo["diameter_m"] * 1000,
                sol["mass_kg"], sol["v_t"], sol["r"], mo["burn_time_s"],
                mo["total_impulse_ns"], sol["window_s"]))
        if not out:
            print("  (nothing in the catalog gives a >=0.10 s window here)")


if __name__ == "__main__":
    main()
