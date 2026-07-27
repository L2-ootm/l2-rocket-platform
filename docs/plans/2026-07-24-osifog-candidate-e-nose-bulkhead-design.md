# Candidate E — Shell-Bonded Nose Ballast Design

## Problem

Candidate D's 50 g steel nose ballast is a 1 mm-thick bulkhead with a
44.884 mm radius. At its 90 mm axial station, the LV-Haack nose cone's
71.549 mm inner radius leaves approximately 26.7 mm of radial air. The existing
geometry gate proves containment and mass truth, but not a rigid attachment
path. This violates the rule that every component must be physically attached.
Candidate D remains immutable evidence and is not edited.

## Selected design

Candidate E replaces the floating steel slug with a full-radius aluminum
structural bulkhead at the same axial station. The compiler derives its radius
from the actual Haack profile minus the 2 mm nose shell thickness, then derives
thickness from requested mass and aluminum density:

`length = mass / (density × pi × radius²)`.

For Candidate E this produces a 50 g, 71.549 mm-radius, approximately
1.151 mm-thick disk. It stays above the 1 mm dimensional minimum, touches the
nose shell around its forward circumference, preserves requested inert mass,
and requires no mass or CG override. Aluminum is already an approved bulk
material at 2700 kg/m³.

The parameter contract is explicit:
`nose_ballast_attachment = "nose_shell_bonded"` and
`nose_ballast_material = "aluminum"`. Legacy free ballast remains readable for
archived candidates but fails the current submission checklist.

## Validation and acceptance

Static compilation must verify exact material-derived mass, containment across
the tapered profile, centered placement, and radial contact with the local
inner shell. The final packaged `.ork` must independently re-read the nose,
bulkhead, position, material density, radius, and mass geometry; validating only
pre-save XML is insufficient.

Candidate E is accepted only after OpenRocket 24.12 produces two legal
tail-first landings below 5 m/s, Mach below 1, four valid motor centering rings,
no forbidden recovery/override/ejection elements, deterministic packaging, and
a fresh terminal-delay calibration. Candidate D's files and hashes remain
unchanged.
