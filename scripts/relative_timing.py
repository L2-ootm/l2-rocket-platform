#!/usr/bin/env python3
"""Contact-relative landing-timing coordinate (mission section 3).

Absolute retro-ignition delay (`s1_retro_delay` / `s0_retro_delay`) is scheduled
from LAUNCH (t=0) in the OSIFOG XML (ignitionevent="launch" + ignitiondelay),
confirmed directly against the ORK XML in
artifacts/autoevo/phase5a/corrected-stage-event-map.json. That absolute value
is candidate-specific: a coupled vehicle's unpowered hang time shifts with
sustainer geometry/mass, so reusing a fixed absolute delay across mutated
candidates is wrong by construction (this is exactly how the literal 33.104s
historical delay failed against today's authority pipeline -- see
phase4c-current-rerun.json).

This module defines a contact-relative coordinate that survives across
mutated candidates, and translates it back to an absolute XML delay only at
serialization time (i.e. only when actually building the ORK for a trial).
"""
import math
import os
import sys
import tempfile

os.environ.setdefault("RAYON_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jpype
from osifog_sweep import (
    generate_ork, SIM_SEED, _seed_multilevel_wind, _load_ork_doc,
    _get_anti_tumble_listener,
)

BOOSTER_BRANCH = 1  # branch 1 = stage 1 = Booster (corrected mapping, unchanged)

# Reference basin point (29.864s delay, artifacts/autoevo/best-legal-booster-branch.ork),
# recomputed independently from phase4c-current-rerun.json / phase4c-delay-sweep-finest.json:
#   unpowered_contact_time_s = 30.3423148731   (retro disabled, delay=200.0)
#   ignition_time_s          = 29.864          (ignitionevent=launch + ignitiondelay)
#   powered_contact_time_s   = 30.7866981310   (ground_hit_time_s at delay=29.864)
#   burnout_time_s           = 31.177
REFERENCE_UNPOWERED_CONTACT_S = 30.3423148731
REFERENCE_IGNITION_S = 29.864
REFERENCE_POWERED_CONTACT_S = 30.786698131031873
REFERENCE_BURNOUT_S = 31.177
REFERENCE_TAU_FREE_CONTACT_S = REFERENCE_UNPOWERED_CONTACT_S - REFERENCE_IGNITION_S
REFERENCE_TAU_POWERED_CONTACT_S = REFERENCE_POWERED_CONTACT_S - REFERENCE_IGNITION_S
REFERENCE_BURN_REMAINING_AT_CONTACT_S = REFERENCE_BURNOUT_S - REFERENCE_POWERED_CONTACT_S


def _ground_contact_time(params, retro_delay_field="s1_retro_delay", branch=BOOSTER_BRANCH):
    """Run one candidate and return its ground-contact time on the given branch."""
    ork_xml = generate_ork(params)
    fd, path = tempfile.mkstemp(suffix=".ork")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(ork_xml)
        doc = _load_ork_doc(path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
        sim.simulate(_get_anti_tumble_listener())
        data = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")
        br = data.getBranch(branch)
        for ev in br.getEvents():
            if ev.getType() == FlightEvent.Type.GROUND_HIT:
                return float(ev.getTime())
        # fall back: last altitude<=0 crossing
        alt_arr = br.get(fdt.TYPE_ALTITUDE)
        t_arr = br.get(fdt.TYPE_TIME)
        n = int(br.getLength())
        for i in range(1, n):
            if float(alt_arr[i]) <= 0.0 and float(alt_arr[i - 1]) > 0.0:
                return float(t_arr[i])
        return float(t_arr[n - 1])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def unpowered_contact_time(params, retro_delay_field="s1_retro_delay", branch=BOOSTER_BRANCH):
    """t_unpowered_ground_contact for this candidate (retro disabled at 200s)."""
    p = dict(params)
    p[retro_delay_field] = 200.0
    return _ground_contact_time(p, retro_delay_field, branch)


def seed_absolute_delay(params, tau_free_contact_s=REFERENCE_TAU_FREE_CONTACT_S,
                          retro_delay_field="s1_retro_delay", branch=BOOSTER_BRANCH):
    """Translate a contact-relative tau_free_contact seed into an absolute XML delay
    for THIS candidate's own unpowered hang time.

    This is a SEED for a local delay sweep, not a final answer -- the basin is
    narrow (~2ms) and non-monotonic at millisecond scale (section 0.1/4), so a
    local fine sweep around the seed is still required.
    """
    t_unpowered = unpowered_contact_time(params, retro_delay_field, branch)
    return t_unpowered - tau_free_contact_s, t_unpowered


def relative_timing_descriptor(ignition_time_s, unpowered_contact_time_s,
                                 powered_contact_time_s, burnout_time_s):
    tau_free_contact = unpowered_contact_time_s - ignition_time_s
    tau_powered_contact = powered_contact_time_s - ignition_time_s
    burn_remaining_at_contact = burnout_time_s - powered_contact_time_s
    return {
        "ignition_time_s": ignition_time_s,
        "unpowered_contact_time_s": unpowered_contact_time_s,
        "powered_contact_time_s": powered_contact_time_s,
        "burnout_time_s": burnout_time_s,
        "tau_free_contact_s": tau_free_contact,
        "tau_powered_contact_s": tau_powered_contact,
        "burn_remaining_at_contact_s": burn_remaining_at_contact,
    }


if __name__ == "__main__":
    ref = relative_timing_descriptor(
        REFERENCE_IGNITION_S, REFERENCE_UNPOWERED_CONTACT_S,
        REFERENCE_POWERED_CONTACT_S, REFERENCE_BURNOUT_S,
    )
    import json
    print(json.dumps(ref, indent=2))
