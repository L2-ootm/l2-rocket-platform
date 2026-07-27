# Physical geometry guard design

## Decision

Treat OpenRocket as the flight authority but compile every generated Falcon
through an independent finite-solid geometry gate before simulation.

## Model

Internal motors and ballast are finite axial cylinders with axial interval,
radius, radial center, material density and declared mass. Generation fails on
non-finite dimensions, body escape, mass-volume mismatch or interpenetration.
Only explicit bonded contacts between the motor cage and ballast are allowed
at zero clearance.

## Native OpenRocket mapping

- motor sleeves: `InnerTube` with 1 mm wall and 0.25 mm motor clearance;
- ballast rods: solid `InnerTube` (`thickness == outerradius`);
- nose ballast: steel `Bulkhead` inside the local Haack radius;
- cage supports: fiberglass `CenteringRing` with a 137.5 mm clear opening;
- stage joint: internal fiberglass `TubeCoupler`, 50 mm long.

## Fail-closed rules

No candidate reaches OpenRocket if geometry does not compile. No candidate is
promoted unless the saved ORK is reopened, contains one simulation and two
landed branches, retains the anti-tumble script, passes all mission gates and
matches the data-driven score table.
