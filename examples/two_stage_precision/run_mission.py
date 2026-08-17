"""Example: programmatic 2-stage rocket generation and proxy evaluation.

Demonstrates creating a dynamic 2-stage rocket topology using rocket_ast,
compiling it to OpenRocket XML (.ork), and running high-throughput proxy simulation.
"""

from pathlib import Path
import sys

# Ensure src/ is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from rocket_ast import ASTNode, ASTCompiler, MOTOR_DATABASE
from organic_loop import run_rust_evaluator, write_ork_zip


def build_two_stage_rocket() -> list[ASTNode]:
    """Construct an AST definition for a high-performance 2-stage rocket."""
    # Find suitable motors from the local catalog
    sustainer_idx = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "K550W")
    booster_idx = next(i for i, m in enumerate(MOTOR_DATABASE) if m[1] == "L1150")

    return [
        # Stage 1: Sustainer (Upper Stage)
        ASTNode("STAGE", name="Sustainer Stage"),
        ASTNode("NOSE_CONE", shape="haack", length=0.45, material="fiberglass"),
        ASTNode("BODY_TUBE", length=0.95, radius=0.045, thickness=0.002, material="fiberglass"),
        ASTNode("PARACHUTE", deploy="apogee", diameter=0.6),
        ASTNode("FIN_SET", count=3, sweep=35.0, root=0.14, height=0.07, thickness=0.003, material="carbon_fiber"),
        ASTNode("MOTOR_MOUNT", motor_designation="K550W", motor_index=sustainer_idx, ignition="stage_activation", delay_s=0.5),
        ASTNode("CLOSE_BODY"),

        # Stage 2: Booster (Lower Stage)
        ASTNode("STAGE", name="Booster Stage"),
        ASTNode("BODY_TUBE", length=1.10, radius=0.045, thickness=0.002, material="fiberglass"),
        ASTNode("FIN_SET", count=4, sweep=40.0, root=0.18, height=0.09, thickness=0.003, material="carbon_fiber"),
        ASTNode("MOTOR_MOUNT", motor_designation="L1150", motor_index=booster_idx, ignition="automatic"),
        ASTNode("CLOSE_BODY"),
    ]


def main():
    print("[*] Generating 2-stage rocket AST...")
    ast = build_two_stage_rocket()
    print(f"    AST nodes: {len(ast)}")

    # Compile AST to OpenRocket XML
    compiler = ASTCompiler()
    ork_xml = compiler.compile(ast)
    out_ork = REPO_ROOT / "runs" / "example_two_stage.ork"
    out_ork.parent.mkdir(parents=True, exist_ok=True)
    write_ork_zip(out_ork, ork_xml)
    print(f"[OK] Compiled .ork rocket design: {out_ork}")

    # Evaluate proxy physics using the native Rust engine
    print("[*] Running high-throughput Rust proxy simulation...")
    candidates = [{"id": "stage2_demo", "ast": [node.to_dict() for node in ast]}]
    results = run_rust_evaluator(candidates, target_apogee_m=3000.0)
    if results:
        res = results[0]
        print(f"[OK] Proxy Simulation Results:")
        print(f"     Status:          {res.status}")
        print(f"     Apogee:          {res.apogee_m:.2f} m")
        print(f"     Max Mach:        {res.mach:.2f}")
        print(f"     Static Margin:   {res.min_static_margin:.2f} cal")
        if res.reason:
            print(f"     Details:         {res.reason}")


if __name__ == "__main__":
    main()
