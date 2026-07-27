# L2 Engine: Modular Design (OpenRocket vs Realistic Mode)

## Context
During the reverse engineering and validation of the OpenRocket simulation physics, we discovered a major discrepancy in apogee for high-performance multi-stage rockets. OpenRocket predicts an apogee of ~70km, while a true ballistic flight reaches ~154km.

The root cause: **Premature Parachute Deployment in OpenRocket.**
In OpenRocket, the motor ejection charge delay (e.g., 14 seconds) combined with parachute deployment delay (e.g., 2 seconds) causes the parachute to deploy at 	 = 42s. At this time, the rocket is still traveling at supersonic/hypersonic speeds. OpenRocket magically allows the parachute to survive, applying a massive drag penalty that acts like a brick wall, artificially capping the apogee at ~70km.

In real life, deploying a parachute at Mach 3+ would instantly shred the canopy and snap the shock cords, causing the rocket to continue its ballistic ascent to ~154km.

## The Solution: Modular Simulation Modes
To respect both the need for OpenRocket validation and the physical reality of the flight, the engine will be split into two modular modes:

### 1. OpenRocket Mode (SimMode::OpenRocket)
- **Behavior**: Faithfully reproduces OpenRocket's quirks.
- **Rules**:
  - Parachutes deploy exactly at urnout_time + motor_delay + chute_deploy_delay.
  - Parachutes are indestructible, regardless of dynamic pressure (q_dyn) or Mach number.
  - Generates the massive drag spike required to match OpenRocket's ~70km apogee prediction.
- **Use Case**: Validation against .ork files and CI test suites.

### 2. Realistic Mode (SimMode::Realistic)
- **Behavior**: Simulates true physical constraints.
- **Rules**:
  - Parachutes deployed at excessive dynamic pressure or supersonic speeds are destroyed.
  - If a parachute is destroyed, its drag contribution becomes 0, and the rocket continues its ballistic trajectory.
  - Represents the true physical apogee (~154km) if premature ejection occurs.
- **Use Case**: Actual flight prediction, structural limits testing, and L2 mission planning.

## Implementation Steps
1. **XML Parser**: Update xml_parser.rs to extract <delay> from motors and <parachute> parameters (diameter, CD).
2. **Flight State**: Add parachute_deployed: bool and track deployment events.
3. **SixDOF Physics**: In sixdof.rs, add parachute drag calculation.
4. **Configuration**: Introduce SimMode to toggle parachute destruction logic.
