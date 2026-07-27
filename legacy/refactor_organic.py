import re

with open("organic_loop.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add calibrate_every and or_helper to OrganicLoopConfig
content = re.sub(
    r'validate_openrocket: int = 0',
    'validate_openrocket: int = 0\n    calibrate_every: int = 0\n    or_helper: object = None',
    content
)

# 2. Add --calibrate-every to parse_args
content = re.sub(
    r'parser\.add_argument\("--validate-openrocket", type=int, default=0\)',
    'parser.add_argument("--validate-openrocket", type=int, default=0)\n    parser.add_argument("--calibrate-every", type=int, default=0)',
    content
)

# 3. Add extract_topological_signature function
sig_func = """
def extract_topological_signature(ast_nodes):
    parts = []
    for node in ast_nodes:
        if node.node_type == "NOSE_CONE":
            parts.append(node.params.get("shape", "ogive"))
        elif node.node_type == "FIN_SET":
            parts.append(f"{node.params.get('count', 4)}fins")
        elif node.node_type == "MOTOR_MOUNT":
            parts.append(node.params.get("motor_designation", "motor"))
    return "_".join(parts)
"""
content = content.replace("def _structural_mutation(ast_nodes):", sig_func + "\n\ndef _structural_mutation(ast_nodes):")

# 4. Modify main()
main_original = """def main():
    args = parse_args()
    
    target_apogee_m = args.target_apogee or 15000.0"""

main_new = """def main():
    args = parse_args()
    
    or_instance = None
    helper = None
    if args.validate_openrocket or args.calibrate_every > 0:
        import orhelper
        from orhelper import OpenRocketInstance
        or_instance = OpenRocketInstance("lib/OpenRocket-23.09.jar").__enter__()
        helper = orhelper.Helper(or_instance)
        
    try:
        target_apogee_m = args.target_apogee or 15000.0"""

content = content.replace(main_original, main_new)

# Add calibrate_every and or_helper to Config init
config_init = """        validate_openrocket=args.validate_openrocket,
        objectives=objectives,
        constraints=constraints,
    )
    result = run_generation(config)
    best = result.elites[0]
    print(f"best score={best.score:.3f} status={best.status} reason={best.reason}")
    print(f"wrote {len(result.elites)} elites to {config.output_dir}")"""

config_init_new = """        validate_openrocket=args.validate_openrocket,
        calibrate_every=args.calibrate_every,
        or_helper=helper,
        objectives=objectives,
        constraints=constraints,
    )
    result = run_generation(config)
    best = result.elites[0]
    print(f"best score={best.score:.3f} status={best.status} reason={best.reason}")
    print(f"wrote {len(result.elites)} elites to {config.output_dir}")
    finally:
        if or_instance is not None:
            or_instance.__exit__(None, None, None)"""

content = content.replace(config_init, config_init_new)

# 5. Modify run_generation
run_gen = """        evaluated.sort(key=lambda candidate: candidate.score, reverse=True)

        for candidate in evaluated:
            ckg.record(candidate.ast, candidate.score, candidate.status, candidate.reason)"""

run_gen_new = """        evaluated.sort(key=lambda candidate: candidate.score, reverse=True)
        
        # Calibration hook
        if config.calibrate_every > 0 and (generation + 1) % config.calibrate_every == 0 and config.or_helper and evaluated[0].status == "success":
            best = evaluated[0]
            compiler = ASTCompiler()
            xml = compiler.compile(best.ast, name="calibrate")
            ork_path = config.output_dir / "calibrate.ork"
            write_ork_zip(ork_path, xml)
            or_metrics = validate_openrocket_ork(ork_path, config.or_helper)
            if or_metrics["status"] == "success":
                delta = or_metrics["apogee_m"] / max(best.rust_apogee_m, 1.0)
                sig = extract_topological_signature(best.ast)
                ckg.record_calibration(sig, delta)

        for candidate in evaluated:
            ckg.record(candidate.ast, candidate.score, candidate.status, candidate.reason)"""

content = content.replace(run_gen, run_gen_new)

# 6. Modify export_elites to use config.or_helper and not initialize OR
export_original = """    helper = None
    or_instance = None
    if config.validate_openrocket:
        import orhelper
        from orhelper import OpenRocketInstance

        or_instance = OpenRocketInstance("lib/OpenRocket-23.09.jar").__enter__()
        helper = orhelper.Helper(or_instance)

    try:
        for idx, candidate in enumerate(elites):"""

export_new = """    helper = config.or_helper
    try:
        for idx, candidate in enumerate(elites):"""

content = content.replace(export_original, export_new)

export_end_original = """            payload["elite"].append(
                {
                    "score": candidate.score,
                    "raw_score": candidate.raw_score,
                    "status": candidate.status,
                    "reason": candidate.reason,
                    "rust_apogee_m": candidate.rust_apogee_m,
                    "rust_mach": candidate.rust_mach,
                    "rust_min_static_margin": candidate.rust_min_static_margin,
                    "rust_margins": candidate.rust_margins or [],
                    "or_metrics": candidate.or_metrics,
                    "ork": str(ork_path),
                    "ast": ast_to_dicts(candidate.ast),
                }
            )
    finally:
        if or_instance is not None:
            or_instance.__exit__(None, None, None)

    (config.output_dir / "organic_elite.json").write_text(json.dumps(payload, indent=2))"""

export_end_new = """            payload["elite"].append(
                {
                    "score": candidate.score,
                    "raw_score": candidate.raw_score,
                    "status": candidate.status,
                    "reason": candidate.reason,
                    "rust_apogee_m": candidate.rust_apogee_m,
                    "rust_mach": candidate.rust_mach,
                    "rust_min_static_margin": candidate.rust_min_static_margin,
                    "rust_margins": candidate.rust_margins or [],
                    "or_metrics": candidate.or_metrics,
                    "ork": str(ork_path),
                    "ast": ast_to_dicts(candidate.ast),
                }
            )
    finally:
        pass

    (config.output_dir / "organic_elite.json").write_text(json.dumps(payload, indent=2))"""

content = content.replace(export_end_original, export_end_new)


with open("organic_loop.py", "w", encoding="utf-8") as f:
    f.write(content)

print("organic_loop.py updated!")
