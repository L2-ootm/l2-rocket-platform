with open('l2_engine/src/xml_parser.rs', 'a') as f:
    f.write('\nfn parse_parachute(node: &Node) -> Result<ParachuteGeometry, L2EngineError> {\n')
    f.write('    Ok(ParachuteGeometry {\n')
    f.write('        diameter: child_f64(node, \"diameter\").unwrap_or(0.0),\n')
    f.write('        cd: child_f64(node, \"cd\").unwrap_or(0.75),\n')
    f.write('        deploy_delay: child_f64(node, \"deploydelay\").unwrap_or(0.0),\n')
    f.write('    })\n')
    f.write('}\n')
