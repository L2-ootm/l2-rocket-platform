"""OpenRocket session: single-JVM evaluation, motor resolution, telemetry.

One OpenRocketSession per run — JVM boot plus motor database load costs ~35 s,
each candidate evaluation inside it costs seconds. Motor digests are resolved
against the live database (fixes "multiple motors ... chosen arbitrarily")
and cached to motors_cache.json across runs.
"""

import json
import os
import uuid

import orhelper
from orhelper import OpenRocketInstance

from .generator import build_rocket_xml, save_ork

JAR = "lib/OpenRocket-23.09.jar"
CACHE_PATH = "motors_cache.json"
SCRATCH = os.environ.get("L2_SCRATCH", "temp_ork")


class OpenRocketSession:
    def __init__(self, jar=JAR):
        self.jar = jar
        self._instance = None
        self.orh = None
        self._eval_count = 0

    def __enter__(self):
        self._instance = OpenRocketInstance(self.jar)
        self._instance.__enter__()
        self.orh = orhelper.Helper(self._instance)
        return self

    def __exit__(self, *exc):
        return self._instance.__exit__(*exc)

    # -- motors ------------------------------------------------------------

    def resolve_motors(self, stack):
        """Return one resolved motor dict per stack entry, digest included.

        Among database duplicates, deterministically picks the highest total
        impulse variant (tie-break on digest string).
        """
        cache = {}
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, encoding="utf-8") as f:
                cache = json.load(f)

        wanted = {(s["motor"]["manufacturer"], s["motor"]["designation"]) for s in stack}
        missing = [w for w in wanted if f"{w[0]}|{w[1]}" not in cache]
        if missing:
            import jpype
            Application = jpype.JClass("net.sf.openrocket.startup.Application")
            db = Application.getMotorSetDatabase()
            candidates = {w: [] for w in missing}
            for ms in db.getMotorSets():
                for m in ms.getMotors():
                    key = (str(m.getManufacturer().getDisplayName()), str(m.getDesignation()))
                    if key in candidates:
                        candidates[key].append(m)
            for key, ms_list in candidates.items():
                if not ms_list:
                    raise LookupError(f"motor not in OpenRocket database: {key}")
                best = max(ms_list, key=lambda m: (float(m.getTotalImpulseEstimate()), str(m.getDigest())))
                cache[f"{key[0]}|{key[1]}"] = dict(
                    manufacturer=key[0],
                    designation=key[1],
                    digest=str(best.getDigest()),
                    diameter=round(float(best.getDiameter()), 4),
                    length=round(float(best.getLength()), 4),
                    launch_mass=round(float(best.getLaunchMass()), 3),
                    impulse=round(float(best.getTotalImpulseEstimate()), 1),
                )
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)

        return [cache[f"{s['motor']['manufacturer']}|{s['motor']['designation']}"] for s in stack]

    # -- stability -----------------------------------------------------------

    def static_margins(self, doc, phase_machs):
        """Static margin [calibers] for each flight phase of the stack.

        Phase p = stages 0..n-1-p active (full stack, minus booster, ...,
        top stage alone), each judged at a representative Mach. Uses the same
        BarrowmanCalculator OpenRocket's GUI stability readout uses.
        """
        import jpype
        rocket = doc.getRocket()
        config = rocket.getSelectedConfiguration()
        calc = jpype.JClass("net.sf.openrocket.aerodynamics.BarrowmanCalculator")()
        FlightConditions = jpype.JClass("net.sf.openrocket.aerodynamics.FlightConditions")
        MassCalculator = jpype.JClass("net.sf.openrocket.masscalc.MassCalculator")
        try:
            WarningSet = jpype.JClass("net.sf.openrocket.logging.WarningSet")
        except Exception:
            WarningSet = jpype.JClass("net.sf.openrocket.aerodynamics.WarningSet")

        n = rocket.getStageCount()
        margins = {}
        for phase in range(n):
            config.setAllStages()
            for dropped in range(phase):
                config._setStageActive(n - 1 - dropped, False)
            conditions = FlightConditions(config)
            mach = phase_machs[min(phase, len(phase_machs) - 1)]
            conditions.setMach(mach)
            conditions.setAOA(0.0)
            cp = calc.getCP(config, conditions, WarningSet()).x
            cg = MassCalculator.calculateLaunch(config).getCenterOfMass().x
            margins[f"phase{phase}_M{mach}"] = (cp - cg) / float(conditions.getRefLength())
        config.setAllStages()
        return margins

    # -- evaluation ----------------------------------------------------------

    def evaluate(self, mission, genome, motors, keep_path=None):
        """Simulate one genome; returns a metrics dict for the fitness."""
        fcid = str(uuid.uuid4())
        os.makedirs(SCRATCH, exist_ok=True)
        path = keep_path or os.path.join(SCRATCH, f"cand_{self._eval_count}.ork")
        self._eval_count += 1
        save_ork(build_rocket_xml(mission, genome, motors, fcid), path)

        doc = self.orh.load_doc(path)
        margins = self.static_margins(doc, mission.get("stability", {}).get("phase_machs", [0.3, 2.0, 3.0]))
        sim = doc.getSimulations().get(0)
        self.orh.run_simulation(sim)
        data = sim.getSimulatedData()

        events = [(float(ev.getTime()), ev.getType().name())
                  for ev in data.getBranch(0).getEvents()]
        try:
            warnings = sorted({str(w) for w in data.getWarningSet()})
        except Exception:
            warnings = []
        ignitions = [t for t, n in events if n == "IGNITION"]
        apogee_t = next((t for t, n in events if n == "APOGEE"), None)
        return dict(
            apogee=float(data.getMaxAltitude()),
            mach=float(data.getMaxMachNumber()),
            vmax=float(data.getMaxVelocity()),
            flight_time=float(data.getFlightTime()),
            tumbled=any(n == "TUMBLE" for _, n in events),
            late_ignition=(apogee_t is not None and any(t > apogee_t for t in ignitions)),
            static_margins=margins,
            min_static_margin=min(margins.values()) if margins else 0.0,
            events=events,
            warnings=warnings,
        )
