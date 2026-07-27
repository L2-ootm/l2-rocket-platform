# AST Geometry and Constructability

## AST Schema for OSIFOG

The OSIFOG Falcon uses a custom parameter-driven topology (not the organic AST). The Falcon parameters are projected into AST nodes by `osifog_engine_search.py::parameters_to_ast()`:

```
parameters dict → list[ASTNode]:
  STAGE(name="Sustainer")
  NOSE_CONE(shape="haack", length, material="fiberglass")
  BODY_TUBE(length, radius=0.074, thickness=0.002, material="cardboard")
  MOTOR_MOUNT(designation, role="main", multiplicity=3, ignition="automatic")
  MOTOR_MOUNT(designation, role="retro", multiplicity=1, ignition="launch", delay)
  FIN_SET(count, sweep, root, height, thickness, material, cross_section="airfoil")
  [FIN_SET(grid_fin params)]  # optional forward grid fins
  BALLAST(mass, position, material="lead")
  CLOSE_BODY
  STAGE(name="Booster")
  BODY_TUBE(length, radius=0.074, thickness=0.002, material="cardboard")
  MOTOR_MOUNT(designation, role="main", multiplicity=3, ignition="launch")
  MOTOR_MOUNT(designation, role="retro", multiplicity=1, ignition="launch", delay)
  FIN_SET(...)
  [FIN_SET(grid_fin params)]
  BALLAST(mass, position, material="lead")
  CLOSE_BODY
```

## Physical Constructability Analysis

### 3+1 Motor Cage (per stage)
- 3× ascent motors arranged at 120° intervals on a circle of radius `center_distance`
- 1× central retro motor at center
- Centering rings connect to airframe
- Structural sleeve (retro mount) bonds to airframe

### Verified Dimensions (current authority candidate)
- Body radius: 74 mm (both stages)
- Body diameter: 148 mm
- Total length: ~2.19 m (nose + s0 body + s1 body)
- Main motor: J510W (38mm diameter, 584mm length)
- Retro motor: K550W (54mm diameter, 410mm length)

### Collision Detection
`physical_geometry.py` validates:
1. All cylinders fit within body bore (radial containment)
2. No axial overlap between components
3. Assembly clearance of 1 mm between non-bonded parts
4. Tangent contact for bonded parts (motor mount ↔ motor mount, motor mount ↔ ballast)
5. Attachment path from every internal solid to the airframe wall

### Missing Geometry Checks
1. **Fins** — not checked for collision with motor mounts or ballast
2. **Transitions** — not supported in AST
3. **Interstage couplers** — represented as TubeCoupler in ORK XML but not in AST
4. **Exhaust swept volume** — not checked for obstruction by ballast rods
5. **Stage separation plane** — no explicit geometric representation

## AST Mutation Capabilities

The organic AST (`rocket_ast.py`) supports:
- Motor index mutation (within allowed pool)
- Body tube length/radius jitter
- Fin count, sweep, root, height mutation
- Nose cone shape/length mutation
- Ballast mass mutation
- Structural mutations (add PAYLOAD, PARACHUTE, FIN_SET)

**Limitations for OSIFOG**:
- Cannot mutate motor cage configuration (3+1 is fixed)
- Cannot add/remove forward grid fins via mutation
- Cannot change ballast rod radius or attachment type
- Cannot modify launch angle or azimuth through AST mutation (these are separate parameters)
