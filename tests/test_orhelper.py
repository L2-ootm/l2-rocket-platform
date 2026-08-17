from pathlib import Path
import subprocess
import sys

import pytest

from organic_loop import write_ork_zip
from rocket_ast import ASTCompiler, ASTNode, MOTOR_DATABASE


def test_simulation(tmp_path):
    jar_path = Path("lib/OpenRocket-24.12.jar")
    if not jar_path.is_file():
        pytest.skip(
            "requires lib/OpenRocket-24.12.jar — download OpenRocket 24.12 jar to run full JVM simulation tests"
        )
    motor_index = next(
        index for index, motor in enumerate(MOTOR_DATABASE) if motor[1] == "K550W"
    )
    ast = [
        ASTNode("STAGE", name="OpenRocket Integration Test"),
        ASTNode("NOSE_CONE", shape="haack", length=0.4, material="fiberglass"),
        ASTNode(
            "BODY_TUBE",
            length=1.2,
            radius=0.05,
            thickness=0.002,
            material="fiberglass",
        ),
        ASTNode(
            "FIN_SET",
            count=4,
            sweep=30.0,
            root=0.16,
            height=0.08,
            material="fiberglass",
        ),
        ASTNode("MOTOR_MOUNT", motor_index=motor_index, ignition="automatic"),
        ASTNode("CLOSE_BODY"),
    ]
    ork_path = tmp_path / "orhelper_integration.ork"
    write_ork_zip(ork_path, ASTCompiler().compile(ast))

    script = """
from pathlib import Path
import orhelper
from orhelper import OpenRocketInstance
from organic_loop import validate_openrocket_ork

with OpenRocketInstance("lib/OpenRocket-24.12.jar") as instance:
    metrics = validate_openrocket_ork(
        Path(r"{ork_path}"), orhelper.Helper(instance), phase_machs=[0.3]
    )
assert metrics["status"] == "success", metrics
assert metrics["apogee_m"] > 0.0
assert metrics["mach"] >= 0.0
print("AUTHORITY_OK")
""".format(ork_path=ork_path)
    import os
    env = os.environ.copy()
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join([src_dir, env.get("PYTHONPATH", "")]) if env.get("PYTHONPATH") else src_dir
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "AUTHORITY_OK" in completed.stdout
