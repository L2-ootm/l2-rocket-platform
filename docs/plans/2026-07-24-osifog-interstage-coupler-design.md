# OSIFOG Candidate G interstage coupler design

## Decision

Preserve Candidate F byte-for-byte and create a successor with one native
OpenRocket `TubeCoupler` rigidly owned by the booster.

The coupler is:

- 50 mm long;
- 1 mm wall thickness;
- fiberglass, 1800 kg/m³;
- 80 mm outer radius and 79 mm inner radius;
- centered on the airframe axis;
- positioned from -25 mm to +25 mm relative to the booster's forward plane.

It therefore remains part of the booster after separation, while its forward
25 mm is inserted into the sustainer during coupled flight. This ownership is
required because a coupler retained by the sustainer would project behind the
sustainer K700W nozzle and enter its exhaust when the landing motor ignites.

## Motor and component clearance

The sustainer K700W motor mount is 700 mm long. OpenRocket places the 568 mm
motor at:

```text
forward end = 700 - 568 + 5 = 137 mm
aft end     = 137 + 568 = 705 mm
```

The coupler occupies the sustainer's final 25 mm, from 675 to 700 mm. These
axial envelopes overlap, but the solids do not: the K700W is centered inside
an approximately 28.25 mm motor sleeve, while the coupler bore is 79 mm.
There is more than 50 mm of radial clearance between them. The K700W cannot
ignite while coupled: separation is at 23.593 s and sustainer ignition is at
49.293 s.

Inside the booster, the coupler occupies only its first 25 mm. The three
K700W ascent mounts begin at 412 mm and their motors begin near 437 mm. The
central I211W motor begins near 765 mm. No booster motor or casing reaches the
coupler.

The current sustainer aft centering ring occupies 695-700 mm and would
intersect the coupler. With the coupler enabled, move that ring to 670-675 mm.
It remains bonded to the airframe and motor sleeve, ends exactly where the
coupler begins, and retains 670 mm spacing from the forward ring.

## Generator and validation contract

Historical inputs remain unchanged unless `interstage_coupler` is true.
Candidate G enables:

```json
"interstage_coupler": true,
"interstage_coupler_length_m": 0.05,
"interstage_coupler_wall_m": 0.001,
"interstage_coupler_sustainer_overlap_m": 0.025
```

Generation must reject:

- length or wall below the 1 mm mission minimum;
- overlap below 1 mm on either stage;
- coupler outer radius not touching both equal airframe bores;
- coupler bore intersecting any motor/mount envelope;
- an aft sustainer ring that overlaps the inserted portion;
- any sustainer motor ignition at or before stage separation.

The packaged `.ork` validator must repeat these checks after OpenRocket
save-normalization. Candidate acceptance requires one booster-owned coupler,
two rings per stage, one separation before all sustainer ignitions, and a
successful saved/reopened OpenRocket simulation.

## Alternatives rejected

An external collar avoids moving the aft ring but changes external drag and
creates a larger retuning problem. Three keyed pins could avoid a continuous
annulus but require modeled holes, guides, and finite attachment structures.
The internal booster-owned tube coupler is simpler, native to OpenRocket, and
creates the clearest inspectable load path.

## Optimization consequence

The coupler adds approximately 45 g near the stage interface and moves the
sustainer aft ring forward by 25 mm. Both retro ignition delays become stale.
After structural validation, rerun the authority simulation, restore apogee
if necessary, then retune sustainer and booster landing delays last.
