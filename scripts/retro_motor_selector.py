"""Select retro motors by impulse-matching, over OpenRocket's full live catalog.

Physics (1-D, drag included, verified against measured OSIFOG freefall probes):

A stage of mass ``m`` falling at its terminal speed ``v_t`` has drag
``D(v) = m*g*(v/v_t)^2``.  Firing a retro of thrust ``T`` gives thrust ratio
``r = T/(m*g)`` and deceleration ``a(v) = g*[r - 1 + (v/v_t)^2]``, so the time
to arrest the descent is

    t_decel = (v_t/g) * arctan(1/sqrt(r-1)) / sqrt(r-1)

The motor must burn for *exactly* that long.  Burning short leaves the stage
still moving; burning long re-accelerates it upward, after which it coasts,
burns out at altitude and free-falls again -- this is precisely how I59WN
bottomed out at 24.8 m/s on the 3.6 kg Booster.  So the motor's own total
impulse must match

    I_required = T * t_decel

Once matched, the surviving sensitivity is how fast touchdown speed slews with
ignition time, which near v=0 is just the residual acceleration ``(r-1)*g``:

    window = V_legal / ((r-1)*g)

Low ``r`` widens the window but demands a longer burn and more impulse; the
catalog decides which combinations actually exist.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/retro_motor_selector.py
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

# Measured by scripts/osifog_direct_driver.py freefall probes at seed 16000
# (OSIFOG/experiments-2026-07-25/k_arch/k01_freefall_probe_out.json).
STAGES = {
    # name:        (mass_kg, v_terminal_ms, max_diameter_m, max_length_m)
    "sustainer": (10.65, 176.0, 0.075, 0.65),
    "booster": (3.60, 52.8, 0.075, 0.95),
}


def t_decel(v_t, r):
    """Time to arrest a terminal-velocity descent under thrust ratio r."""
    if r <= 1.0:
        return math.inf
    s = math.sqrt(r - 1.0)
    return (v_t / G) * math.atan(1.0 / s) / s


def evaluate(motor, mass_kg, v_t):
    w = mass_kg * G
    r = motor["avg_thrust_n"] / w
    if r <= 1.02:
        return None  # cannot arrest the descent at all
    td = t_decel(v_t, r)
    i_req = motor["avg_thrust_n"] * td
    i_act = motor["total_impulse_ns"]
    return {
        "r": r,
        "t_decel_s": td,
        "i_required_ns": i_req,
        "i_actual_ns": i_act,
        "impulse_error": (i_act - i_req) / i_req,
        "burn_error": (motor["burn_time_s"] - td) / td,
        "window_s": V_LEGAL / ((r - 1.0) * G),
    }


def main():
    motors = json.load(open(CATALOG, encoding="utf-8"))
    for stage, (mass, v_t, dmax, lmax) in STAGES.items():
        print(f"\n{'='*100}")
        print(f"{stage.upper()}  m={mass} kg  v_terminal={v_t} m/s  "
              f"weight={mass*G:.1f} N   (fits: <={dmax*1000:.0f} mm, <={lmax*1000:.0f} mm)")
        print("=" * 100)
        cands = []
        for mo in motors:
            if mo["diameter_m"] > dmax + 1e-6 or mo["length_m"] > lmax + 1e-6:
                continue
            if not mo["delays"] or not all(math.isinf(d) for d in mo["delays"]):
                continue  # plugged-only motors: no ejection charge on a retro
            ev = evaluate(mo, mass, v_t)
            if ev is None:
                continue
            # Both the total impulse and the burn duration must land near the
            # requirement; either one alone is not sufficient.
            ev["score"] = abs(ev["impulse_error"]) + abs(ev["burn_error"])
            cands.append((ev, mo))
        cands.sort(key=lambda c: c[0]["score"])
        print("%-22s %-13s %5s %6s %6s %7s %7s %8s %8s %8s" % (
            "designation", "mfr", "dia", "T/W", "t_dec", "t_burn", "I_req",
            "I_have", "err", "window"))
        for ev, mo in cands[:14]:
            print("%-22s %-13s %5.0f %6.2f %6.2f %7.2f %7.0f %8.0f %+7.0f%% %7.2fs" % (
                mo["designation"], mo["manufacturer"][:13], mo["diameter_m"] * 1000,
                ev["r"], ev["t_decel_s"], mo["burn_time_s"], ev["i_required_ns"],
                ev["i_actual_ns"], ev["impulse_error"] * 100, ev["window_s"]))
        if not cands:
            print("  (no plugged motor in the catalog can arrest this stage)")


if __name__ == "__main__":
    main()
