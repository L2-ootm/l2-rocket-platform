import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from organic_loop import (
    _simulation_abort_reasons,
    _simulation_status_name,
    run_openrocket_simulation,
)
from scripts.or_mode_ast_sweep import DEFAULT_OPENROCKET_JAR, authority_metadata


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_elite_member(elite_path, index):
    payload = json.loads(Path(elite_path).read_text(encoding="utf-8"))
    member = payload["elite"][index]
    return {
        "id": f"elite-{index}",
        "ast": member["ast"],
        "signature": "",
    }, member


def run_rust_trace(candidate):
    from rocket_ast import OPENROCKET_SIMULATION_DEFAULTS

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump({**candidate, "environment": OPENROCKET_SIMULATION_DEFAULTS}, handle)
        batch_path = Path(handle.name)
    try:
        completed = subprocess.run(
            ["cargo", "run", "--quiet", "--release", "--bin", "ast_trace", "--", "--input", str(batch_path)],
            cwd=REPO_ROOT / "l2_engine",
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)
    finally:
        try:
            batch_path.unlink()
        except OSError:
            pass


def java_trace(jexc):
    import jpype

    sw = jpype.JPackage("java").io.StringWriter()
    pw = jpype.JPackage("java").io.PrintWriter(sw)
    jexc.printStackTrace(pw)
    pw.flush()
    return str(sw.toString())


def branch_series(branch, flight_data_type, limit=600):
    limit = int(os.environ.get("L2_OR_TRACE_LIMIT", limit))
    values = branch.get(flight_data_type)
    if values is None:
        return []
    length = int(values.size())
    stride = max(1, int((length + limit - 1) / limit))
    return [float(values.get(i)) for i in range(0, length, stride)]


def openrocket_trace(ork_path, jar):
    import jpype
    import orhelper
    from orhelper import OpenRocketInstance

    with OpenRocketInstance(jar) as instance:
        helper = orhelper.Helper(instance)
        doc = helper.load_doc(str(ork_path))
        rocket = doc.getRocket()
        configuration = rocket.getSelectedConfiguration()
        BarrowmanCalculator = jpype.JClass(
            "info.openrocket.core.aerodynamics.BarrowmanCalculator"
        )
        FlightConditions = jpype.JClass(
            "info.openrocket.core.aerodynamics.FlightConditions"
        )
        WarningSet = jpype.JClass("info.openrocket.core.logging.WarningSet")
        MassCalculator = jpype.JClass("info.openrocket.core.masscalc.MassCalculator")
        calculator = BarrowmanCalculator()
        launch_cg = float(MassCalculator.calculateLaunch(configuration).getCenterOfMass().x)
        reference_conditions = FlightConditions(configuration)
        reflected_damping_multiplier = None
        reflected_damping_multiplier_error = None
        try:
            calculator.getCP(configuration, reference_conditions, WarningSet())
            method = BarrowmanCalculator.class_.getDeclaredMethod(
                "getDampingMultiplier",
                jpype.JClass("info.openrocket.core.rocketcomponent.FlightConfiguration").class_,
                FlightConditions.class_,
                jpype.JClass("java.lang.Double").TYPE,
            )
            method.setAccessible(True)
            reflected_damping_multiplier = float(
                method.invoke(
                    calculator,
                    configuration,
                    reference_conditions,
                    launch_cg,
                )
            )
        except Exception as exc:
            reflected_damping_multiplier_error = str(exc)
        damping_components = []
        component_iterator = configuration.getActiveComponents().iterator()
        while component_iterator.hasNext():
            component = component_iterator.next()
            item = {
                "name": str(component.getName()),
                "type": str(component.getClass().getSimpleName()),
            }
            for key, accessor in (
                ("length_m", "getLength"),
                ("component_planform_area_m2", "getComponentPlanformArea"),
                ("fin_planform_area_m2", "getPlanformArea"),
                ("fin_count", "getFinCount"),
            ):
                if hasattr(component, accessor):
                    try:
                        item[key] = float(getattr(component, accessor)())
                    except Exception:
                        pass
            damping_components.append(item)
        static_analysis = {}
        for mach in (0.3, 0.6, 1.2, 1.5, 1.8, 2.0, 2.2):
            conditions = FlightConditions(configuration)
            conditions.setMach(mach)
            conditions.setAOA(0.0)
            total_cp = calculator.getCP(configuration, conditions, WarningSet())
            components = []
            analysis = calculator.getForceAnalysis(
                configuration, conditions, WarningSet()
            )
            iterator = analysis.entrySet().iterator()
            while iterator.hasNext():
                entry = iterator.next()
                component = entry.getKey()
                forces = entry.getValue()
                cp = forces.getCP() if forces is not None else None
                if component is None or cp is None:
                    continue
                components.append(
                    {
                        "name": str(component.getName()),
                        "type": str(component.getClass().getSimpleName()),
                        "cp_m": float(cp.x),
                        "cna": float(cp.weight),
                        "pressure_cd": float(forces.getPressureCD()),
                        "base_cd": float(forces.getBaseCD()),
                        "friction_cd": float(forces.getFrictionCD()),
                    }
                )
            static_analysis[f"M{mach:g}"] = {
                "cp_m": float(total_cp.x),
                "cna": float(total_cp.weight),
                "launch_cg_m": launch_cg,
                "reflected_multiplier": reflected_damping_multiplier,
                "reflection_error": reflected_damping_multiplier_error,
                "components": components,
            }
        sim = doc.getSimulations().get(0)
        options = sim.getOptions()
        run_openrocket_simulation(sim)
        data = sim.getSimulatedData()
        branch = data.getBranch(0)
        FlightDataType = jpype.JClass(
            "info.openrocket.core.simulation.FlightDataType"
        )

        def series(type_name):
            try:
                return branch_series(branch, getattr(FlightDataType, type_name))
            except Exception:
                return []

        events = [
            (float(ev.getTime()), str(ev.getType().name()))
            for ev in branch.getEvents()
        ]
        simulation_status = _simulation_status_name(sim)
        abort_reasons = _simulation_abort_reasons(data)
        return {
            "status": (
                "failed"
                if simulation_status == "ABORTED" or abort_reasons
                else "success"
            ),
            "simulation_status": simulation_status,
            "abort_reasons": abort_reasons,
            "environment": {
                "launch_rod_length_m": float(options.getLaunchRodLength()),
                "launch_rod_angle_rad": float(options.getLaunchRodAngle()),
                "launch_rod_direction_rad": float(options.getLaunchRodDirection()),
                "launch_into_wind": bool(options.getLaunchIntoWind()),
                "wind_speed_average_mps": float(options.getWindSpeedAverage()),
                "wind_direction_rad": float(options.getWindDirection()),
            },
            "static_analysis": static_analysis,
            "damping_geometry": {
                "reference_area_m2": float(reference_conditions.getRefArea()),
                "reference_length_m": float(reference_conditions.getRefLength()),
                "launch_cg_m": launch_cg,
                "components": damping_components,
            },
            "summary": {
                "apogee_m": float(data.getMaxAltitude()),
                "mach": float(data.getMaxMachNumber()),
                "flight_time_s": float(data.getFlightTime()),
            },
            "events": events,
            "series": {
                "time_s": series("TYPE_TIME"),
                "altitude_m": series("TYPE_ALTITUDE"),
                "velocity_mps": series("TYPE_VELOCITY_TOTAL"),
                "vertical_velocity_mps": series("TYPE_VELOCITY_Z"),
                "downrange_m": series("TYPE_POSITION_XY"),
                "mach": series("TYPE_MACH_NUMBER"),
                "acceleration_mps2": series("TYPE_ACCELERATION_TOTAL"),
                "mass_kg": series("TYPE_MASS"),
                "motor_mass_kg": series("TYPE_MOTOR_MASS"),
                "longitudinal_inertia_kg_m2": series("TYPE_LONGITUDINAL_INERTIA"),
                "rotational_inertia_kg_m2": series("TYPE_ROTATIONAL_INERTIA"),
                "thrust_n": series("TYPE_THRUST_FORCE"),
                "drag_n": series("TYPE_DRAG_FORCE"),
                "drag_cd": series("TYPE_DRAG_COEFF"),
                "axial_drag_cd": series("TYPE_AXIAL_DRAG_COEFF"),
                "friction_drag_cd": series("TYPE_FRICTION_DRAG_COEFF"),
                "pressure_drag_cd": series("TYPE_PRESSURE_DRAG_COEFF"),
                "base_drag_cd": series("TYPE_BASE_DRAG_COEFF"),
                "reference_area_m2": series("TYPE_REFERENCE_AREA"),
                "angle_of_attack_rad": series("TYPE_AOA"),
                "orientation_theta_rad": series("TYPE_ORIENTATION_THETA"),
                "wind_velocity_mps": series("TYPE_WIND_VELOCITY"),
                "wind_direction_rad": series("TYPE_WIND_DIRECTION"),
                "stability_calibers": series("TYPE_STABILITY"),
                "cp_location_m": series("TYPE_CP_LOCATION"),
                "cg_location_m": series("TYPE_CG_LOCATION"),
                "pitch_rate_rad_s": series("TYPE_PITCH_RATE"),
                "pitch_moment_coeff": series("TYPE_PITCH_MOMENT_COEFF"),
                "pitch_damping_moment_coeff": series("TYPE_PITCH_DAMPING_MOMENT_COEFF"),
            },
        }


def nearest_delta(rust_points, or_times, or_values, rust_key):
    if not rust_points or not or_times or not or_values:
        return None
    deltas = []
    cursor = 0
    for point in rust_points:
        t = point["time_s"]
        while cursor + 1 < len(or_times) and abs(or_times[cursor + 1] - t) < abs(or_times[cursor] - t):
            cursor += 1
        if cursor < len(or_values) and or_values[cursor] is not None:
            deltas.append(point[rust_key] - or_values[cursor])
    if not deltas:
        return None
    abs_deltas = [abs(value) for value in deltas]
    return {
        "mean_abs": sum(abs_deltas) / len(abs_deltas),
        "max_abs": max(abs_deltas),
        "samples": len(deltas),
    }


def compare(rust_trace, or_trace):
    or_series = or_trace["series"]
    rust_points = rust_trace["points"]
    return {
        "summary_delta": {
            "apogee_m": rust_trace["summary"]["apogee_m"] - or_trace["summary"]["apogee_m"],
            "mach": rust_trace["summary"]["max_mach"] - or_trace["summary"]["mach"],
        },
        "curve_delta": {
            "altitude_m": nearest_delta(rust_points, or_series["time_s"], or_series["altitude_m"], "altitude_m"),
            "mach": nearest_delta(rust_points, or_series["time_s"], or_series["mach"], "mach"),
            "speed_mps": nearest_delta(rust_points, or_series["time_s"], or_series["velocity_mps"], "speed_mps"),
        },
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Compare one organic elite's Rust trace against OpenRocket FlightData.")
    parser.add_argument("--elite", type=Path, required=True, help="Path to organic_elite.json")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--jar", type=Path, default=DEFAULT_OPENROCKET_JAR)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    candidate, member = load_elite_member(args.elite, args.index)
    rust_trace = run_rust_trace(candidate)
    ork_path = Path(member["ork"])
    if not ork_path.is_absolute():
        ork_path = REPO_ROOT / ork_path
    or_trace = openrocket_trace(ork_path, str(args.jar))
    report = {
        "authority": authority_metadata(args.jar),
        "elite": str(args.elite),
        "index": args.index,
        "ork": member["ork"],
        "rust": rust_trace,
        "openrocket": or_trace,
        "comparison": (
            compare(rust_trace, or_trace)
            if or_trace["status"] == "success"
            else None
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
