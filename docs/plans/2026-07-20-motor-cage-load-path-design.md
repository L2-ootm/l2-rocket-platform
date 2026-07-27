# OSIFOG motor-cage load-path design

## Decision

Replace the floating annular cluster supports with a continuous-contact 3+1
motor cage made only from native OpenRocket axial components.

Each stage keeps three identical, organically selected ascent mounts around
one central K550W retro motor. All motor nozzles share the stage aft plane.
The central motor sits aft-aligned inside a full-length structural spine. The
spine is thick enough that every outer mount is simultaneously tangent to it
and the airframe bore over their shared aft interval. This creates the explicit
load path:

`retro mount -> structural sleeve -> ascent mounts -> airframe`

Ballast rods are tangent to the structural sleeve and must overlap it axially:

`ballast rod -> structural sleeve -> ascent mounts -> airframe`

## Components

- The motor cavities retain 0.25 mm insertion clearance.
- Every structural wall remains at least 1 mm.
- The central sleeve thickness is derived from the selected motor diameters
  and airframe bore; it is never a hidden mass override.
- The central motor case may be shorter than the structural spine; its nozzle
  remains coplanar with the three ascent nozzles.
- Outer mount cluster scale is derived from the airframe contact condition.
- The former `CenteringRing` components are removed. A native annular ring
  cannot represent four motor cutouts and becomes geometrically meaningless
  when the motor pack reaches the airframe.
- The former internal `TubeCoupler` is removed because it intersects the
  wall-tangent sustainer motor cage. The native axial-stage interface remains
  the explicit pre-separation joint between matching airframe end faces.

## Validation

The physical geometry gate builds a contact graph containing all motor mounts,
ballast rods and the airframe. A component is structurally valid only if:

1. it is finite, contained and collision-free;
2. its declared mass matches material volume;
3. motor nozzles share the aft plane;
4. ballast overlaps its support sleeve axially; and
5. every internal solid has a tangent-contact path to the airframe.

OpenRocket remains the flight and scoring authority. The Rust AST bridge
supports independent multi-motor roles, multiplicity and delays for ascent
filtering. Its `retro` role does not yet reverse thrust direction, so landing
burns remain OpenRocket-authority work.

## Genuine staging

The booster must separate during ascent no more than one second after its
burnout and strictly before apogee. The upper stage must pass the 1.5-caliber
stability gate independently after separation. A design that carries booster
fins and structure through apogee is rejected even if OpenRocket later emits
two descent branches.
