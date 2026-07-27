with open('l2_engine/src/geometry.rs', 'a') as f:
    f.write('\n#[derive(Debug, Clone)]\n')
    f.write('pub struct ParachuteGeometry {\n')
    f.write('    pub diameter: f64,\n')
    f.write('    pub cd: f64,\n')
    f.write('    pub deploy_delay: f64,\n')
    f.write('}\n')
